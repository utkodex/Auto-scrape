"""run_step (one step from the current browser state) and run_checks (the full three-stage test).

Contracts: .claude/specs/02.architecture.md §7.3 (run_step / run_checks rows), §7.5.3 (StepResult),
.claude/specs/03.build-loop-and-eval.md §8.1 (stages). Never imports core.submit.
"""

from __future__ import annotations

import importlib.util
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from agent.steps_file import StepsFile
from core import helpers
from core.browser import Browser, PageKind
from core.checks import lint, sandbox_check, verify_expectations
from core.helpers import FileAttached, HelperError
from core.log import ActionLog
from core.observe import OBSERVE_JS
from core.profile import Profile


@dataclass
class StepResult:
    status: str  # PASS | FAIL
    step: str
    checks: int = 0
    page_kind_after: str = "unknown"
    url_after: str = ""
    s: float = 0.0
    failed_check: str | None = None
    error: str | None = None
    element_found: bool = True
    retry_mode: str | None = None
    expectations: list = field(default_factory=list, repr=False)

    def to_tool_result(self) -> dict:
        d = asdict(self)
        d.pop("expectations", None)
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class CheckResult:
    status: str  # PASS | FAIL
    stage: str  # lint | sandbox | fill
    sample: str | None = None
    step: str | None = None
    error: str | None = None
    passed: int = 0
    total: int = 0
    s: float = 0.0
    resume_attached: bool = False

    def to_tool_result(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def summary(self) -> str:
        if self.status == "PASS":
            return f"PASS {self.passed}/{self.total}"
        where = f" sample {self.sample}" if self.sample else ""
        step = f" {self.step}" if self.step else ""
        return f"FAIL · {self.stage}{where}{step}: {self.error}"


def load_steps_module(path: Path | str):
    """Fresh import every time so edits are picked up (no stale module cache)."""
    path = Path(path)
    name = f"steps_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _labels(page) -> list[str]:
    try:
        return [f["label"] for f in page.evaluate(OBSERVE_JS)["fields"]]
    except Exception:
        return []


def _tail(text: str, n: int = 20) -> str:
    lines = (text or "").strip().splitlines()
    return "\n".join(lines[-n:])


def run_step(
    browser: Browser,
    steps_file: StepsFile,
    name: str,
    profile: Profile,
    log: ActionLog | None = None,
    mode: str = "build",
    screenshots_dir: Path | str | None = None,
    prev_labels: list[str] | None = None,
    round_no: int | None = None,
) -> StepResult:
    t0 = time.monotonic()
    page = browser.page
    prev_url = page.url
    prev_kind = browser.classify(page).value
    labels = prev_labels if prev_labels is not None else _labels(page)
    helpers.begin(prev_url=prev_url, prev_labels=labels, mode=mode)
    error: str | None = None
    element_found = True
    try:
        module = load_steps_module(steps_file.path)
        fn = getattr(module, name, None)
        if fn is None:
            raise HelperError(f"{name} not found in steps.py")
        fn(page, profile)
    except HelperError as exc:
        error = str(exc)
        element_found = exc.element_found
    except Exception as exc:  # noqa: BLE001
        error = _tail("".join(traceback.format_exception_only(type(exc), exc)), 20)
    finally:
        expectations = helpers.end()

    result = StepResult(status="PASS", step=name, checks=len(expectations), expectations=expectations)
    if error is None:
        failures = verify_expectations(browser.page, expectations)
        if failures:
            f = failures[0]
            result.status = "FAIL"
            result.failed_check = f.expectation
            result.error = _tail(f.error, 20)
            result.element_found = f.element_found
    else:
        result.status = "FAIL"
        result.error = _tail(error, 20)
        result.element_found = element_found

    after_kind = browser.classify(browser.page).value
    result.page_kind_after = after_kind
    result.url_after = browser.page.url
    if result.status == "FAIL":
        same = (browser.page.url.split("#")[0] == prev_url.split("#")[0]) and after_kind == prev_kind
        result.retry_mode = "in_place" if same else "replay"
        if screenshots_dir:
            browser.screenshot(screenshots_dir)
    result.s = round(time.monotonic() - t0, 1)
    if log:
        summary = "PASS" if result.status == "PASS" else f"FAIL · {result.error.splitlines()[0] if result.error else ''}"
        if result.status == "PASS" and result.checks:
            summary += f" · {result.checks} check(s)"
        log.row("harness", "run_step", name, summary, s=result.s, round=round_no,
                page_kind=after_kind, **({"retry_mode": result.retry_mode} if result.retry_mode else {}))
    return result


def replay(
    browser: Browser,
    steps_file: StepsFile,
    upto_index: int,
    profile: Profile,
    job_url: str,
    log: ActionLog | None = None,
    mode: str = "build",
    round_no: int | None = None,
) -> StepResult | None:
    """Reload the job link and run locked steps 1..upto_index. Returns the first FAIL or None."""
    browser.goto(job_url)
    for s in steps_file.steps():
        if s.index > upto_index:
            break
        res = run_step(browser, steps_file, s.name, profile, log=None, mode=mode, round_no=round_no)
        if res.status == "FAIL":
            if log:
                log.row("harness", "replay", s.name, f"FAIL · {res.error.splitlines()[0] if res.error else ''}", s=res.s, round=round_no)
            return res
    if log:
        log.row("harness", "replay", f"steps 01–{upto_index:02d}", "PASS", round=round_no)
    return None


def load_samples(filler_dir: Path) -> list[dict]:
    tests_yaml = Path(filler_dir) / "tests.yaml"
    if not tests_yaml.exists():
        return []
    data = yaml.safe_load(tests_yaml.read_text(encoding="utf-8")) or {}
    samples = data.get("samples") or []
    out = []
    for s in samples:
        if isinstance(s, str):
            out.append({"url": s, "expect": {"reaches": "review", "resume_attached": True}})
        elif isinstance(s, dict) and s.get("url"):
            exp = s.get("expect") or {}
            out.append({"url": s["url"], "expect": {"reaches": exp.get("reaches", "review"),
                                                    "resume_attached": bool(exp.get("resume_attached", True))}})
    return out


def run_checks(
    filler_dir: Path | str,
    browser: Browser,
    profile: Profile,
    log: ActionLog | None = None,
    mode: str = "test",
    round_no: int | None = None,
) -> CheckResult:
    t0 = time.monotonic()
    filler_dir = Path(filler_dir)
    steps_path = filler_dir / "steps.py"
    steps_file = StepsFile(steps_path, filler_dir / "steps.lock.json")

    def done(res: CheckResult) -> CheckResult:
        res.s = round(time.monotonic() - t0, 1)
        if log:
            log.row("harness", "run_checks", f"{filler_dir.name}: {res.total} sample(s)", res.summary(), s=res.s, round=round_no)
        return res

    if not steps_path.exists() or not steps_path.read_text(encoding="utf-8").strip():
        return done(CheckResult("FAIL", "lint", error="steps.py is empty"))
    err = lint(steps_path)
    if err:
        return done(CheckResult("FAIL", "lint", error=err))
    err = sandbox_check(steps_path)
    if err:
        return done(CheckResult("FAIL", "sandbox", error=err))
    samples = load_samples(filler_dir)
    if not samples:
        return done(CheckResult("FAIL", "fill", error="tests.yaml has no samples"))

    steps = steps_file.steps()
    passed = 0
    resume_any = False
    for sample in samples:
        url = sample["url"]
        browser.goto(url)
        attached = False
        for s in steps:
            res = run_step(browser, steps_file, s.name, profile, log=None, mode=mode, round_no=round_no)
            if res.status == "FAIL":
                return done(CheckResult("FAIL", "fill", sample=url, step=s.name, error=res.error,
                                        passed=passed, total=len(samples)))
            if any(isinstance(e, FileAttached) for e in res.expectations):
                attached = True
            if browser.classify(browser.page) == PageKind.review:
                break
        kind = browser.classify(browser.page)
        want = sample["expect"]["reaches"]
        if kind.value != want:
            return done(CheckResult("FAIL", "fill", sample=url, step=steps[-1].name if steps else None,
                                    error=f"did not reach {want} page (page_kind={kind.value})",
                                    passed=passed, total=len(samples)))
        if sample["expect"]["resume_attached"] and not attached:
            return done(CheckResult("FAIL", "fill", sample=url, step=None,
                                    error="resume was never attached (no upload step passed)",
                                    passed=passed, total=len(samples)))
        probe = browser.probe(browser.page)
        if probe.get("required_empty", 0):
            return done(CheckResult("FAIL", "fill", sample=url, step=None,
                                    error=f"{probe['required_empty']} required field(s) still empty",
                                    passed=passed, total=len(samples)))
        passed += 1
        resume_any = resume_any or attached
    return done(CheckResult("PASS", "fill", passed=passed, total=len(samples), resume_attached=resume_any))
