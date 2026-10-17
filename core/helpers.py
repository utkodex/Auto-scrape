"""The six helpers generated steps may call, each recording the check that proves it worked.

Contracts: .claude/specs/02.architecture.md §7.5. The LLM writes no assertions; every helper
appends an *expectation* to the active StepContext and `core.checks.verify_expectations`
verifies them after the step ran (decision D3).
"""

from __future__ import annotations

import contextvars
import difflib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PWTimeout

from core.browser import REPO_ROOT, SUBMIT_RE

PROFILES_DIR = (REPO_ROOT / "profiles").resolve()
HELPER_NAMES = ("click", "fill", "pick", "upload", "check", "wait_for")
EXPECTATION_HELPERS = ("click", "fill", "pick", "upload", "check")
DEFAULT_TIMEOUT_S = 10.0


# -- errors ------------------------------------------------------------------------
class HelperError(Exception):
    """A helper could not do what it was asked. Message is one actionable line."""

    def __init__(self, message: str, element_found: bool = True) -> None:
        super().__init__(message)
        self.element_found = element_found


class AmbiguousLabel(HelperError):
    pass


class ForbiddenAction(HelperError):
    pass


# -- expectations ------------------------------------------------------------------
@dataclass
class PageChanged:
    prev_url: str


@dataclass
class NewFields:
    prev_labels: list[str]


@dataclass
class PageOrFieldsChanged:
    prev_url: str
    prev_labels: list[str]


@dataclass
class ValueEquals:
    label: str
    value: str


@dataclass
class OptionSelected:
    label: str
    option: str


@dataclass
class FileAttached:
    label: str
    basename: str


@dataclass
class Checked:
    label: str
    option: str


Expectation = (
    PageChanged | NewFields | PageOrFieldsChanged | ValueEquals | OptionSelected | FileAttached | Checked
)


@dataclass
class StepContext:
    prev_url: str = ""
    prev_labels: list[str] = field(default_factory=list)
    mode: str = "build"
    expectations: list = field(default_factory=list)
    calls: int = 0


_ctx: contextvars.ContextVar[StepContext | None] = contextvars.ContextVar("step_ctx", default=None)


def begin(prev_url: str = "", prev_labels: list[str] | None = None, mode: str = "build") -> StepContext:
    ctx = StepContext(prev_url=prev_url, prev_labels=list(prev_labels or []), mode=mode)
    _ctx.set(ctx)
    return ctx


def end() -> list:
    ctx = _ctx.get()
    _ctx.set(None)
    return list(ctx.expectations) if ctx else []


def current() -> StepContext:
    ctx = _ctx.get()
    if ctx is None:
        ctx = StepContext()
        _ctx.set(ctx)
    return ctx


def _record(exp) -> None:
    ctx = current()
    ctx.expectations.append(exp)
    ctx.calls += 1


# -- label matching ----------------------------------------------------------------
def norm(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    s = re.sub(r"[\*:]+\s*$", "", s).strip()
    return s.casefold()


LABEL_OF_JS = r"""
(el) => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return clean(l.innerText); }
  const al = el.getAttribute('aria-label'); if (al) return clean(al);
  const lb = el.getAttribute('aria-labelledby');
  if (lb) { const t = lb.split(/\s+/).map(id => document.getElementById(id)).filter(Boolean).map(e => e.innerText).join(' '); if (t) return clean(t); }
  const wrap = el.closest('label');
  if (wrap) { const c = wrap.cloneNode(true); c.querySelectorAll('input,select,textarea').forEach(n => n.remove()); return clean(c.innerText); }
  return clean(el.getAttribute('placeholder'));
}
"""

ROLE_FOR_KIND = {
    "text": "textbox", "textarea": "textbox", "select": "combobox", "combobox": "combobox",
    "checkbox": "checkbox", "radio": "radio", "file": None, "date": "textbox", "button": "button",
}


def locate(page: Page, label: str, kind: str | None = None, timeout_s: float = DEFAULT_TIMEOUT_S) -> Locator:
    """Find exactly one control for `label`, polling up to `timeout_s` for it to exist.

    Order: exact label → normalised-exact among substring matches → unique substring match →
    role fallback. More than one surviving candidate raises AmbiguousLabel.
    """
    deadline = time.monotonic() + timeout_s
    want = norm(label)
    last_names: list[str] = []
    while True:
        exact = page.get_by_label(label, exact=True)
        n = exact.count()
        if n == 1:
            return exact.first
        loose = page.get_by_label(label)
        m = loose.count()
        if m >= 1:
            names: list[str] = []
            same: list[int] = []
            for i in range(m):
                try:
                    name = loose.nth(i).evaluate(LABEL_OF_JS)
                except Exception:
                    name = ""
                names.append(name)
                if norm(name) == want:
                    same.append(i)
            last_names = names
            if len(same) == 1:
                return loose.nth(same[0])
            if len(same) > 1 or (len(same) == 0 and m > 1 and len(set(norm(x) for x in names)) == 1):
                shown = ", ".join(f"'{x}'" for x in names[:4])
                raise AmbiguousLabel(f"{label}: {m} matches — {shown}")
            if m == 1:
                return loose.first
            if len(set(names)) > 1:
                shown = ", ".join(f"'{x}'" for x in sorted(set(names))[:4])
                raise AmbiguousLabel(f"{label}: {m} matches — {shown}")
        role = ROLE_FOR_KIND.get(kind or "", None)
        if role:
            by_role = page.get_by_role(role, name=label)
            if by_role.count() == 1:
                return by_role.first
        if time.monotonic() >= deadline:
            hint = f"; similar: {', '.join(repr(x) for x in last_names[:3])}" if last_names else ""
            raise HelperError(f"{label}: not found after {int(timeout_s)}s{hint}", element_found=False)
        page.wait_for_timeout(250)


def _wrap_timeout(label: str, exc: Exception) -> HelperError:
    if isinstance(exc, PWTimeout):
        return HelperError(f"{label}: not visible/enabled after {int(DEFAULT_TIMEOUT_S)}s", element_found=True)
    return HelperError(f"{label}: {str(exc).splitlines()[0][:160]}")


# -- helpers -----------------------------------------------------------------------
EFFECT_WAIT_S = 5.0  # how long a click may take to show its effect (cross-site Apply → SF sign-in ≈ 1–3 s)

_FIELD_COUNT_JS = "() => document.querySelectorAll('input:not([type=hidden]),select,textarea').length"


def _field_count(page: Page) -> int:
    try:
        return int(page.evaluate(_FIELD_COUNT_JS))
    except Exception:  # noqa: BLE001 — mid-navigation evaluate can fail; treat as "changing"
        return -1


def _wait_for_effect(page: Page, prev_url: str, fields_before: int, pages_before: int,
                     expect: str = "any") -> None:
    """Block until the click shows the effect it promised, or EFFECT_WAIT_S has passed.

    A cross-origin navigation started by the click has not committed 150 ms later, and
    `wait_for_load_state` returns at once because the *old* document is loaded; without this
    wait the navigation expectation reads the old URL and reports "page did not change".
    For expect="navigation" only a URL change or a new tab counts: field counts flicker
    (cookie banners, lazy widgets) and must not end the wait early.
    """
    deadline = time.monotonic() + EFFECT_WAIT_S
    while time.monotonic() < deadline:
        if page.url != prev_url or len(page.context.pages) > pages_before:
            return
        if expect != "navigation":
            n = _field_count(page)
            if n >= 0 and n != fields_before:
                return
        page.wait_for_timeout(100)


def click(page: Page, text: str, *, expect: str = "any") -> None:
    """Click a button or link by its visible text. expect: navigation | section | any."""
    ctx = current()
    if ctx.mode in ("build", "test") and SUBMIT_RE.match(text or ""):
        raise ForbiddenAction("submit is not allowed")
    if expect not in ("navigation", "section", "any"):
        raise HelperError(f"click: expect must be navigation|section|any, got {expect!r}")
    candidates = [
        page.get_by_role("button", name=text, exact=True),
        page.get_by_role("link", name=text, exact=True),
        page.get_by_role("button", name=text),
        page.get_by_role("link", name=text),
        page.get_by_text(text, exact=True),
    ]
    deadline = time.monotonic() + DEFAULT_TIMEOUT_S
    target: Locator | None = None
    while target is None:
        for loc in candidates:
            if loc.count() >= 1:
                target = loc.first
                break
        if target is None:
            if time.monotonic() >= deadline:
                raise HelperError(f"{text}: no button or link with that text", element_found=False)
            page.wait_for_timeout(250)

    context = page.context
    prev_url = page.url
    pages_before = len(context.pages)
    fields_before = _field_count(page)
    try:
        target.click(timeout=int(DEFAULT_TIMEOUT_S * 1000))
    except Exception as exc:  # noqa: BLE001
        raise _wrap_timeout(text, exc) from None
    page.wait_for_timeout(150)
    _wait_for_effect(page, prev_url, fields_before, pages_before, expect)
    if len(context.pages) > pages_before:
        try:
            context.pages[-1].wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
    else:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass

    if expect == "navigation":
        _record(PageChanged(prev_url=ctx.prev_url or prev_url))
    elif expect == "section":
        _record(NewFields(prev_labels=list(ctx.prev_labels)))
    else:
        _record(PageOrFieldsChanged(prev_url=ctx.prev_url or prev_url, prev_labels=list(ctx.prev_labels)))


def fill(page: Page, label: str, value: str) -> None:
    """Type `value` into the text box labelled `label`."""
    value = "" if value is None else str(value)
    loc = locate(page, label, "text")
    try:
        loc.fill(value, timeout=int(DEFAULT_TIMEOUT_S * 1000))
    except Exception as exc:  # noqa: BLE001
        raise _wrap_timeout(label, exc) from None
    _record(ValueEquals(label=label, value=value))


def _option_texts(page: Page, loc: Locator) -> list[str]:
    tag = loc.evaluate("el => el.tagName.toLowerCase()")
    if tag == "select":
        return [t.strip() for t in loc.evaluate("el => [...el.options].map(o => o.textContent)") if t.strip()]
    ctl = loc.get_attribute("aria-controls") or loc.get_attribute("aria-owns") or loc.get_attribute("list")
    if ctl:
        lst = page.locator(f"#{ctl}")
        if lst.count():
            return [t.strip() for t in lst.locator("[role=option], option, li").all_text_contents() if t.strip()]
    return [t.strip() for t in page.get_by_role("option").all_text_contents() if t.strip()]


def pick(page: Page, label: str, option: str) -> None:
    """Choose `option` (exact visible text) in a native select or a custom/searchable dropdown."""
    option = str(option)
    loc = locate(page, label, "select")
    tag = loc.evaluate("el => el.tagName.toLowerCase()")
    texts = _option_texts(page, loc)
    match = next((t for t in texts if norm(t) == norm(option)), None)
    if match is None:
        close = difflib.get_close_matches(option, texts, n=3, cutoff=0.0)
        for t in texts:
            if norm(option) in norm(t) and t not in close:
                close.insert(0, t)
        shown = ", ".join(f"'{t}'" for t in close[:3]) or "(no options found)"
        raise HelperError(f"{label}: no option '{option}'; options look like {shown}")
    try:
        if tag == "select":
            loc.select_option(label=match, timeout=int(DEFAULT_TIMEOUT_S * 1000))
        else:
            loc.click(timeout=int(DEFAULT_TIMEOUT_S * 1000))
            try:
                loc.fill("")
                loc.type(match, delay=10)
            except Exception:
                pass
            ctl = loc.get_attribute("aria-controls") or loc.get_attribute("aria-owns")
            scope = page.locator(f"#{ctl}") if ctl and page.locator(f"#{ctl}").count() else page
            opt = scope.get_by_role("option", name=match, exact=True)
            if opt.count() == 0:
                opt = scope.get_by_text(match, exact=True)
            opt.first.click(timeout=int(DEFAULT_TIMEOUT_S * 1000))
    except HelperError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap_timeout(label, exc) from None
    _record(OptionSelected(label=label, option=match))


def upload(page: Page, label: str, path: str) -> None:
    """Attach a file from profiles/ to the file input labelled `label`."""
    p = Path(str(path))
    if not p.is_absolute():
        p = REPO_ROOT / p
    p = p.resolve()
    try:
        p.relative_to(PROFILES_DIR)
    except ValueError:
        raise ForbiddenAction(f"upload: only files under profiles/ may be attached (got {path})") from None
    if not p.exists():
        raise HelperError(f"upload: file not found: {p.name}")
    loc = locate(page, label, "file")
    try:
        loc.set_input_files(str(p), timeout=int(DEFAULT_TIMEOUT_S * 1000))
    except Exception as exc:  # noqa: BLE001
        raise _wrap_timeout(label, exc) from None
    page.wait_for_timeout(100)
    _record(FileAttached(label=label, basename=p.name))


def _truthy(option: str) -> bool:
    return norm(option) in ("yes", "true", "1", "on", "checked")


def check(page: Page, label: str, option: str) -> None:
    """Select `option` in the radio group `label`, or tick/untick the checkbox `label`."""
    option = str(option)
    group = page.get_by_role("group", name=label)
    if group.count() == 0:
        group = page.get_by_role("radiogroup", name=label)
    if group.count() >= 1:
        g = group.first
        radio = g.get_by_role("radio", name=option, exact=True)
        if radio.count() == 0:
            radio = g.get_by_label(option)
        if radio.count() == 0:
            opts = g.get_by_role("radio").all()
            names = [r.evaluate(LABEL_OF_JS) for r in opts]
            shown = ", ".join(f"'{n}'" for n in names[:4])
            raise HelperError(f"no option '{option}' in '{label}'; options look like {shown}")
        try:
            radio.first.check(timeout=int(DEFAULT_TIMEOUT_S * 1000))
        except Exception as exc:  # noqa: BLE001
            raise _wrap_timeout(label, exc) from None
        _record(Checked(label=label, option=option))
        return
    loc = locate(page, label, "checkbox")
    try:
        if _truthy(option) or norm(option) == norm(label):
            loc.check(timeout=int(DEFAULT_TIMEOUT_S * 1000))
        else:
            loc.uncheck(timeout=int(DEFAULT_TIMEOUT_S * 1000))
    except Exception as exc:  # noqa: BLE001
        raise _wrap_timeout(label, exc) from None
    _record(Checked(label=label, option=option))


def wait_for(page: Page, label: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
    """Wait until the control labelled `label` is visible. Records no expectation."""
    loc = locate(page, label, None, timeout_s=float(timeout_s))
    try:
        loc.wait_for(state="visible", timeout=int(float(timeout_s) * 1000))
    except Exception as exc:  # noqa: BLE001
        raise _wrap_timeout(label, exc) from None
    current().calls += 1
