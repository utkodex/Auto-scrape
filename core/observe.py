"""Trimmed page observation for the LLM.

Returns the ≤ cap_tokens view described in .claude/specs/02.architecture.md §7.4:
labelled fields with kinds and option previews, visible buttons, a network summary,
the page kind, and a `suggested_next` hint. Never raw HTML.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from pydantic import Field as PField

from core.browser import APPLY_RE, SUBMIT_RE, Browser, PageKind

FieldKind = Literal["text", "textarea", "select", "combobox", "file", "radio", "checkbox", "date", "button"]

NEXT_BUTTON_RE = re.compile(r"^\s*(next|continue|save and continue|save & continue|proceed)\s*$", re.I)
CODE_LABEL_RE = re.compile(r"\b(country\s*code|dial(ing)?\s*code|isd|code)\b", re.I)
NUMBER_LABEL_RE = re.compile(r"\b(phone|mobile|number|contact)\b", re.I)
OPTIONS_PREVIEW = 8
MAX_FIELDS = 40
MAX_NETWORK = 10


class Field(BaseModel):
    label: str
    kind: FieldKind
    required: bool = False
    value: str = ""
    options: list[str] | None = None


class NetworkCall(BaseModel):
    m: str
    url: str
    st: int | None = None


class SuggestedNext(BaseModel):
    action: Literal["click", "fill", "fill_group", "stop", "ask_human", "wait"]
    targets: list[str] = PField(default_factory=list)


class ObserveResult(BaseModel):
    page_kind: str
    url: str
    title: str = ""
    section: str = ""
    fields: list[Field] = PField(default_factory=list)
    buttons: list[str] = PField(default_factory=list)
    network: list[NetworkCall] = PField(default_factory=list)
    suggested_next: SuggestedNext
    snapshot: str = ""
    truncated: bool = False

    def to_json(self) -> str:
        return json.dumps(self.model_dump(exclude_none=True), separators=(",", ":"), ensure_ascii=False)

    def field(self, label: str) -> Field | None:
        for f in self.fields:
            if f.label == label:
                return f
        return None


def approx_tokens(obj: Any) -> int:
    """Cheap token estimate: characters / 4 (the spec's convention)."""
    if isinstance(obj, ObserveResult):
        text = obj.to_json()
    elif isinstance(obj, str):
        text = obj
    else:
        text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str)
    return len(text) // 4


OBSERVE_JS = r"""
() => {
  const visible = (el) => {
    if (!el || !(el instanceof Element)) return false;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    if (el.closest('[hidden],[aria-hidden="true"]')) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const clean = (s) => (s || '').replace(/\s+/g, ' ').replace(/[\*:]+\s*$/, '').trim();
  const labelFor = (el) => {
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) return clean(l.innerText);
    }
    const al = el.getAttribute('aria-label'); if (al) return clean(al);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(/\s+/).map(id => document.getElementById(id)).filter(Boolean).map(e => e.innerText).join(' '); if (t) return clean(t); }
    const wrap = el.closest('label');
    if (wrap) { const c = wrap.cloneNode(true); c.querySelectorAll('input,select,textarea').forEach(n => n.remove()); return clean(c.innerText); }
    const ph = el.getAttribute('placeholder'); if (ph) return clean(ph);
    return '';
  };
  const groupLabel = (el) => {
    const fs = el.closest('fieldset');
    if (fs) { const lg = fs.querySelector('legend'); if (lg) return clean(lg.innerText); }
    const g = el.closest('[role=group],[role=radiogroup]');
    if (g) { const al = g.getAttribute('aria-label'); if (al) return clean(al);
             const lb = g.getAttribute('aria-labelledby'); if (lb) { const e = document.getElementById(lb); if (e) return clean(e.innerText); } }
    return '';
  };
  const fields = [];
  const seenGroups = new Set();
  const controls = [...document.querySelectorAll('input, select, textarea')]
    .filter(el => !['hidden', 'submit', 'button', 'reset', 'image'].includes(el.type))
    .filter(visible);
  for (const el of controls) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || 'text').toLowerCase();
    let kind = 'text', options = null, value = '', label = '', required = !!el.required;
    if (tag === 'select') {
      kind = 'select';
      options = [...el.options].map(o => clean(o.textContent)).filter(t => t && !/^select/i.test(t));
      value = el.selectedIndex >= 0 && el.options[el.selectedIndex].value ? clean(el.options[el.selectedIndex].textContent) : '';
      label = labelFor(el);
    } else if (tag === 'textarea') {
      kind = 'textarea'; value = el.value || ''; label = labelFor(el);
    } else if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') === 'list' || el.getAttribute('list')) {
      kind = 'combobox'; value = el.value || ''; label = labelFor(el);
      const ctl = el.getAttribute('aria-controls') || el.getAttribute('aria-owns') || el.getAttribute('list');
      const lst = ctl ? document.getElementById(ctl) : null;
      if (lst) options = [...lst.querySelectorAll('[role=option], option, li')].map(o => clean(o.textContent)).filter(Boolean);
    } else if (type === 'radio') {
      const name = el.name || el.id;
      if (seenGroups.has(name)) continue;
      seenGroups.add(name);
      kind = 'radio';
      const group = [...document.querySelectorAll('input[type=radio][name="' + CSS.escape(el.name) + '"]')];
      options = group.map(r => labelFor(r) || r.value).map(clean).filter(Boolean);
      const checked = group.find(r => r.checked);
      value = checked ? (labelFor(checked) || checked.value) : '';
      label = groupLabel(el) || labelFor(el);
      required = group.some(r => r.required);
    } else if (type === 'checkbox') {
      kind = 'checkbox'; value = el.checked ? 'checked' : ''; label = labelFor(el);
    } else if (type === 'file') {
      kind = 'file'; value = (el.files && el.files.length) ? el.files[0].name : ''; label = labelFor(el);
    } else if (type === 'date' || type === 'datetime-local' || type === 'month') {
      kind = 'date'; value = el.value || ''; label = labelFor(el);
    } else {
      kind = 'text'; value = el.value || ''; label = labelFor(el);
    }
    if (!label) continue;
    fields.push({ label, kind, required, value, options });
  }
  const buttons = [...document.querySelectorAll('button, input[type=submit], input[type=button], a[role=button], [role=button]')]
    .filter(visible).map(b => clean(b.innerText || b.value || b.getAttribute('aria-label'))).filter(Boolean);
  const links = [...document.querySelectorAll('a')].filter(visible).map(a => clean(a.innerText)).filter(Boolean);
  const heading = [...document.querySelectorAll('h1, h2, h3, [role=heading]')].find(visible);
  return { title: document.title || '', section: heading ? clean(heading.innerText) : '', fields, buttons, links };
}
"""


def _template_path(url: str) -> str:
    from urllib.parse import urlparse

    u = urlparse(url)
    path = re.sub(r"/\d+(?=/|$)", "/*", u.path or "/")
    path = re.sub(r"/[0-9a-f]{16,}(?=/|$)", "/*", path, flags=re.I)
    return f"{u.hostname}{path}"


def _network_summary(browser: Browser) -> list[NetworkCall]:
    epoch = browser._nav_epoch
    out: list[NetworkCall] = []
    seen: set[str] = set()
    for rec in browser.seen_requests:
        if rec.get("epoch") != epoch or rec.get("type") not in ("xhr", "fetch"):
            continue
        key = f'{rec["m"]} {_template_path(rec["url"])}'
        if key in seen:
            continue
        seen.add(key)
        out.append(NetworkCall(m=rec["m"], url=_template_path(rec["url"]), st=rec.get("st")))
        if len(out) >= MAX_NETWORK:
            break
    return out


def _suggest(page_kind: PageKind, fields: list[Field], buttons: list[str], links: list[str]) -> SuggestedNext:
    if page_kind in (PageKind.review, PageKind.closed):
        return SuggestedNext(action="stop")
    if page_kind in (PageKind.login, PageKind.captcha):
        return SuggestedNext(action="ask_human")
    if page_kind == PageKind.job_description:
        apply = next((t for t in buttons + links if APPLY_RE.match(t)), "Apply")
        return SuggestedNext(action="click", targets=[apply])
    if page_kind == PageKind.form:
        for i, f in enumerate(fields):
            if f.required and not f.value:
                if f.kind == "button":
                    continue
                # pair a dial-code combobox with its phone-number neighbour
                nxt = fields[i + 1] if i + 1 < len(fields) else None
                prv = fields[i - 1] if i > 0 else None
                if CODE_LABEL_RE.search(f.label) and nxt and NUMBER_LABEL_RE.search(nxt.label) and not nxt.value:
                    return SuggestedNext(action="fill_group", targets=[f.label, nxt.label])
                if NUMBER_LABEL_RE.search(f.label) and prv and CODE_LABEL_RE.search(prv.label) and not prv.value:
                    return SuggestedNext(action="fill_group", targets=[prv.label, f.label])
                return SuggestedNext(action="fill", targets=[f.label])
        nxt_btn = next((t for t in buttons if NEXT_BUTTON_RE.match(t)), None)
        if nxt_btn:
            return SuggestedNext(action="click", targets=[nxt_btn])
        if any(SUBMIT_RE.match(t) for t in buttons):
            return SuggestedNext(action="stop")
        return SuggestedNext(action="stop")
    return SuggestedNext(action="wait")


def _trim(result: ObserveResult, cap_tokens: int) -> ObserveResult:
    if approx_tokens(result) <= cap_tokens:
        return result
    result.truncated = True
    # 1. drop network
    result.network = []
    if approx_tokens(result) <= cap_tokens:
        return result
    # 2. options preview to 4
    for f in result.fields:
        if f.options and len(f.options) > 4:
            more = len(f.options) - 4
            f.options = f.options[:4] + [f"+{more} more"]
    if approx_tokens(result) <= cap_tokens:
        return result
    # 3. drop non-required fields after the target (first required empty)
    target = next((i for i, f in enumerate(result.fields) if f.required and not f.value), len(result.fields))
    keep = [f for i, f in enumerate(result.fields) if i <= target or f.required]
    result.fields = keep
    if approx_tokens(result) <= cap_tokens:
        return result
    # 4. drop fields from the end until under the cap (never below the target + 2)
    floor = min(len(result.fields), target + 3)
    while approx_tokens(result) > cap_tokens and len(result.fields) > floor:
        result.fields.pop()
    return result


class _Cache:
    memory: dict[str, ObserveResult] = {}


def observe(
    browser: Browser,
    scope: str | None = None,
    cache_dir: Path | str | None = None,
    cap_tokens: int = 1500,
    mask_values: list[str] | None = None,
) -> ObserveResult:
    page = browser.page
    browser.settle()
    probe = browser.probe(page)
    kind = browser.classify(page, probe)
    if kind == PageKind.unknown:
        page.wait_for_timeout(800)
        probe = browser.probe(page)
        kind = browser.classify(page, probe)

    raw = page.evaluate(OBSERVE_JS)
    masks = sorted({str(v) for v in (mask_values or []) if v and len(str(v)) >= 3}, key=len, reverse=True)

    def masked(value: str) -> str:
        if not value:
            return ""
        for v in masks:
            if v.lower() in value.lower():
                return "***"
        return value

    fields: list[Field] = []
    seen_labels: set[str] = set()
    for f in raw["fields"]:
        if f["label"] in seen_labels:
            continue  # duplicate label within the section: keep the first
        seen_labels.add(f["label"])
        opts = f.get("options")
        if opts is not None and len(opts) > OPTIONS_PREVIEW:
            opts = opts[:OPTIONS_PREVIEW] + [f"+{len(opts) - OPTIONS_PREVIEW} more"]
        fields.append(Field(label=f["label"], kind=f["kind"], required=bool(f["required"]),
                            value=masked(f.get("value") or ""), options=opts))

    truncated = False
    if scope:
        s = scope.lower()
        scoped = [f for f in fields if s in f.label.lower()]
        if not scoped and s in (raw.get("section") or "").lower():
            scoped = fields
        fields = scoped or fields
    if len(fields) > MAX_FIELDS:
        target = next((i for i, f in enumerate(fields) if f.required and not f.value), 0)
        start = max(0, min(target, len(fields) - MAX_FIELDS))
        fields = fields[start:start + MAX_FIELDS]
        truncated = True

    buttons: list[str] = []
    # Apply calls to action are often plain links ("Apply for this job" on www.capgemini.com);
    # show them first so the model can click them.
    for t in raw.get("links", []):
        if APPLY_RE.match(t) and t not in buttons:
            buttons.append(t)
    for b in raw["buttons"]:
        if b not in buttons:
            buttons.append(b)

    result = ObserveResult(
        page_kind=kind.value,
        url=page.url,
        title=raw.get("title", "")[:120],
        section=raw.get("section", "")[:80],
        fields=fields,
        buttons=buttons[:12],
        network=_network_summary(browser),
        suggested_next=_suggest(kind, fields, buttons, raw.get("links", [])),
        truncated=truncated,
    )
    result = _trim(result, cap_tokens)

    digest_src = json.dumps(
        [page.url.split("#")[0], [(f.label, f.kind, f.required, bool(f.value)) for f in result.fields],
         result.buttons, scope or ""],
        separators=(",", ":"),
    )
    result.snapshot = hashlib.sha1(digest_src.encode("utf-8")).hexdigest()[:16]

    cached = _Cache.memory.get(result.snapshot)
    if cached is not None and scope is None:
        result = cached
    else:
        _Cache.memory[result.snapshot] = result
    if cache_dir:
        d = Path(cache_dir)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{result.snapshot}.json"
        if not path.exists():
            path.write_text(result.to_json(), encoding="utf-8")
    return result
