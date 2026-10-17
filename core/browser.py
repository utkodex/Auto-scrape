"""Playwright wrapper: persistent profile per site, current-page tracking (popups),
host allowlist, screenshots, and the page-kind classifier.

Contracts: .claude/specs/02.architecture.md §7.1 (Browser row), §7.4 (classifier),
§7.7 (application-system markers).
"""

from __future__ import annotations

import fnmatch
import re
import time
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page, Playwright, Request, Route, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent

# The only button texts that may ever submit an application. `click` refuses them in
# build/test mode; the classifier uses them to recognise the review page.
# "Apply now" is NOT here: on SuccessFactors it opens the application form (job page, step 1).
SUBMIT_RE = re.compile(
    r"^\s*(submit|submit application|send application|complete application)\s*$",
    re.IGNORECASE,
)
# Calls to action that open the application ("Apply", "Apply now", "Apply for this job" on
# www.capgemini.com, "Apply online" …). Never a submit button: those are in SUBMIT_RE.
APPLY_RE = re.compile(
    r"^\s*apply(\s+now|\s+here|\s+online|\s+today|\s+for\s+(this\s+)?(job|position|role|vacancy|opening))?\s*$",
    re.IGNORECASE,
)
LOGIN_PATH_RE = re.compile(r"/(login|signin|sign-in|auth)(/|\.|$)", re.IGNORECASE)
CAPTCHA_TEXT_RE = re.compile(r"verify (that )?you are human|unusual traffic|are you a robot", re.I)
# What SuccessFactors sites say when a requisition is unposted (HCLTech 180703 redirected to "/"
# with this text after Apply now).
CLOSED_TEXT_RE = re.compile(
    r"not available at this time|no longer (available|accepting applications|open)|position has been filled"
    r"|job (has been|is) closed|this job is not available",
    re.I,
)

SYSTEM_MARKERS = {
    "successfactors": ("rmkcdn.successfactors.com", "/talentcommunity/apply/", "successfactors.com",
                       "successfactors.eu"),
    "workday": ("myworkdayjobs.com",),
}


class PageKind(str, Enum):
    job_description = "job_description"
    form = "form"
    login = "login"
    captcha = "captcha"
    review = "review"
    closed = "closed"  # the posting no longer accepts applications
    unknown = "unknown"


# One DOM pass that returns everything the classifier needs.
PAGE_PROBE_JS = r"""
() => {
  const visible = (el) => {
    if (!el || !(el instanceof Element)) return false;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    if (el.closest('[hidden],[aria-hidden="true"]')) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const txt = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
  const buttons = [...document.querySelectorAll('button, input[type=submit], input[type=button], a[role=button], [role=button]')]
    .filter(visible).map(txt).filter(Boolean);
  const links = [...document.querySelectorAll('a')].filter(visible).map(txt).filter(Boolean);
  const hasLabel = (el) => {
    if (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]')) return true;
    if (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')) return true;
    if (el.closest('label')) return true;
    if (el.type === 'radio' || el.type === 'checkbox') return !!el.closest('fieldset');
    return false;
  };
  const controls = [...document.querySelectorAll('input, select, textarea')]
    .filter(el => !['hidden', 'submit', 'button', 'reset', 'image'].includes(el.type))
    .filter(visible);
  const labelled = controls.filter(hasLabel).length;
  const seen = new Set();
  let requiredEmpty = 0;
  for (const el of controls) {
    if (!el.required) continue;
    if (el.type === 'radio') {
      if (seen.has(el.name)) continue;
      seen.add(el.name);
      const group = [...document.querySelectorAll('input[type=radio][name="' + CSS.escape(el.name) + '"]')];
      if (!group.some(r => r.checked)) requiredEmpty++;
    } else if (el.type === 'checkbox') {
      if (!el.checked) requiredEmpty++;
    } else if (el.type === 'file') {
      if (!el.files || el.files.length === 0) requiredEmpty++;
    } else if (!el.value) {
      requiredEmpty++;
    }
  }
  const captchaFrame = !!document.querySelector('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"]');
  const bodyText = (document.body ? document.body.innerText : '').slice(0, 4000);
  return {
    has_password: [...document.querySelectorAll('input[type=password]')].some(visible),
    buttons, links, labelled, required_empty: requiredEmpty, captcha_frame: captchaFrame, body_text: bodyText,
  };
}
"""


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


class Browser:
    """Owns one persistent Chromium context for one site."""

    def __init__(
        self,
        site: str,
        allowed_hosts: list[str] | None = None,
        headless: bool = True,
        profiles_root: Path | str = REPO_ROOT / "browser_profiles",
    ) -> None:
        self.site = site
        self.allowed_hosts: list[str] = [h.lower() for h in (allowed_hosts or [])]
        self.extra_hosts: set[str] = set()
        self.blocked_requests = 0
        self.blocked: list[str] = []
        self.seen_requests: list[dict] = []
        self._nav_epoch = 0

        self.user_data_dir = Path(profiles_root) / site
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self._pw: Playwright = sync_playwright().start()
        self.context: BrowserContext = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=headless,
            accept_downloads=False,
            viewport={"width": 1280, "height": 900},
        )
        self.pages: list[Page] = list(self.context.pages)
        self.page: Page = self.pages[0] if self.pages else self.context.new_page()
        if self.page not in self.pages:
            self.pages.append(self.page)
        self.context.on("page", self._on_page)
        self.context.on("request", self._on_request)
        self.context.on("response", self._on_response)
        self.context.route("**/*", self._route)

    # -- events -----------------------------------------------------------------
    def _on_page(self, page: Page) -> None:
        self.pages.append(page)
        self.page = page
        page.on("close", lambda p: self._on_close(p))

    def _on_close(self, page: Page) -> None:
        if page in self.pages:
            self.pages.remove(page)
        if self.page is page and self.pages:
            self.page = self.pages[-1]

    def _on_request(self, request: Request) -> None:
        if request.resource_type in ("xhr", "fetch", "document"):
            self.seen_requests.append(
                {"m": request.method, "url": request.url, "type": request.resource_type,
                 "epoch": self._nav_epoch}
            )

    def _on_response(self, response) -> None:
        url = response.url
        for rec in reversed(self.seen_requests):
            if rec["url"] == url and "st" not in rec:
                rec["st"] = response.status
                break

    def _route(self, route: Route, request: Request) -> None:
        host = _host_of(request.url)
        if host and not self.host_allowed(host):
            self.blocked_requests += 1
            self.blocked.append(request.url)
            route.abort("blockedbyclient")
            return
        route.continue_()

    def host_allowed(self, host: str) -> bool:
        host = host.lower()
        if host in self.extra_hosts:
            return True
        for pattern in self.allowed_hosts:
            if host == pattern or fnmatch.fnmatch(host, pattern):
                return True
            if pattern.startswith("*.") and host == pattern[2:]:
                return True
        return False

    # -- navigation --------------------------------------------------------------
    def open(self, url: str) -> Page:
        """Open a URL on the current page; its host is added to the allowlist."""
        host = _host_of(url)
        if host:
            self.extra_hosts.add(host)
        self._nav_epoch += 1
        self.page.goto(url, wait_until="domcontentloaded")
        return self.page

    def goto(self, url: str) -> Page:
        """Navigate the primary page to `url`, closing any popups first (replay)."""
        primary = self.pages[0] if self.pages else self.context.new_page()
        for extra in list(self.pages[1:]):
            try:
                extra.close()
            except Exception:
                pass
        self.pages = [primary]
        self.page = primary
        return self.open(url)

    def settle(self, timeout_ms: int = 3000) -> None:
        """Wait for the load event (bounded) plus a short paint delay.

        `observe` re-probes after a longer pause when the page is still unrecognised, which
        covers pages filled by script after load (e.g. an Apply button injected from an API).
        """
        try:
            self.page.wait_for_load_state("load", timeout=timeout_ms)
        except Exception:
            pass
        self.page.wait_for_timeout(100)

    def screenshot(self, directory: Path | str) -> Path:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{time.strftime('%H%M%S')}-{int(time.time() * 1000) % 1000:03d}.png"
        try:
            self.page.screenshot(path=str(path), full_page=False)
        except Exception:
            return path
        return path

    # -- classification ----------------------------------------------------------
    def probe(self, page: Page | None = None) -> dict:
        page = page or self.page
        try:
            return page.evaluate(PAGE_PROBE_JS)
        except Exception:
            return {"has_password": False, "buttons": [], "links": [], "labelled": 0,
                    "required_empty": 0, "captcha_frame": False, "body_text": ""}

    def classify(self, page: Page | None = None, probe: dict | None = None) -> PageKind:
        page = page or self.page
        p = probe or self.probe(page)
        url = page.url or ""
        if p["captcha_frame"] or CAPTCHA_TEXT_RE.search(p["body_text"] or ""):
            return PageKind.captcha
        if p["has_password"] or LOGIN_PATH_RE.search(urlparse(url).path or ""):
            return PageKind.login
        if p["labelled"] == 0 and CLOSED_TEXT_RE.search(p["body_text"] or ""):
            return PageKind.closed
        has_submit = any(SUBMIT_RE.match(t) for t in p["buttons"])
        if has_submit and p["required_empty"] == 0:
            return PageKind.review
        has_apply = any(APPLY_RE.match(t) for t in p["buttons"] + p["links"])
        # A job page often carries a site search box (one or two optional fields); the apply
        # link decides, as long as nothing required is waiting to be filled.
        if has_apply and p["required_empty"] == 0 and p["labelled"] <= 2:
            return PageKind.job_description
        if p["labelled"] >= 1:
            return PageKind.form
        if has_apply:
            return PageKind.job_description
        return PageKind.unknown

    def detect_system(self, page: Page | None = None) -> str:
        page = page or self.page
        haystack = [page.url or ""] + [r["url"] for r in self.seen_requests]
        for system, markers in SYSTEM_MARKERS.items():
            if any(m in u for u in haystack for m in markers):
                return system
        return "unknown"

    # -- lifecycle ---------------------------------------------------------------
    def close(self) -> None:
        try:
            self.context.close()
        finally:
            self._pw.stop()
