"""Command-line entry points: build, test, bench, report.

Exit codes: build 0 DONE / 2 STOP / 3 human input needed but no TTY; test 0 PASS / 1 FAIL.
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import yaml

from agent.config import REPO_ROOT, Settings
from agent.llm import LLMUnavailable
from agent.loop import BuildLoop
from agent.report import build_report, write_report
from agent.runner import run_checks
from agent.steps_file import StepsFile
from agent.tools import ToolContext
from core.browser import Browser
from core.log import ActionLog
from core.profile import load_profile

SF_HOSTS = ["*.successfactors.com", "*.successfactors.eu", "rmkcdn.successfactors.com",
            "*.sapsf.eu", "*.sapsf.com"]  # candidate sign-in / apply lives on careerNN.sapsf.*
COMPANY_OVERRIDES = {"careers.hcltech.com": "hcltech", "hcltech.com": "hcltech", "capgemini.com": "capgemini",
                     "careers.capgemini.com": "capgemini"}


class NonInteractive(Exception):
    pass


def normalise_url(s: str) -> str:
    s = html.unescape((s or "").strip().strip('"').strip("'"))
    if not s:
        raise ValueError("empty URL")
    if "://" not in s:
        s = "https://" + s
    u = urlparse(s)
    return urlunparse((u.scheme.lower(), (u.netloc or "").lower(), u.path or "/", u.params, u.query, ""))


def registrable_domain(host: str) -> str:
    parts = host.lower().split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "ac", "gov") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def company_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host in COMPANY_OVERRIDES:
        return COMPANY_OVERRIDES[host]
    if host in ("127.0.0.1", "localhost"):
        return "fixture"
    dom = registrable_domain(host)
    name = dom.split(".")[0]
    return "".join(ch for ch in name if ch.isalnum() or ch == "_") or "site"


def site_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return "fixture" if host in ("127.0.0.1", "localhost") else registrable_domain(host)


def ensure_filler(filler_dir: Path, company: str, url: str, settings: Settings) -> dict:
    filler_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = filler_dir / "manifest.yaml"
    host = (urlparse(url).hostname or "").lower()
    if not manifest_path.exists():
        locale = ""
        q = urlparse(url).query
        for part in q.split("&"):
            if part.startswith("locale="):
                locale = part.split("=", 1)[1]
        manifest = {
            "company": company,
            "application_system": "unknown",
            "url_patterns": [f"{host}/*"],
            "allowed_hosts": [host],
            "locale": locale,
            "version": 1,
            "status": "draft",
            "built_with": {"model": settings.LLM_MODEL, "harness": settings.HARNESS_VERSION, "run": ""},
        }
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    tests_path = filler_dir / "tests.yaml"
    if not tests_path.exists():
        tests_path.write_text(
            "samples:\n" f"  - url: {url}\n" "    expect: {reaches: review, resume_attached: true}\n", encoding="utf-8"
        )
    return yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}


def save_manifest(filler_dir: Path, manifest: dict) -> None:
    (filler_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def make_browser(site: str, manifest: dict, headless: bool) -> Browser:
    return Browser(site=site, allowed_hosts=list(manifest.get("allowed_hosts") or []), headless=headless)


def console_ask(question: str) -> str:
    print("\n[ask_human] " + question, flush=True)
    if not sys.stdin or not sys.stdin.isatty():
        raise NonInteractive(question)
    try:
        return input("> ")
    except EOFError as exc:
        raise NonInteractive(question) from exc


def pick_llm(spec: str, settings: Settings, example_path: Path | None, dump_dir: Path | None, use_heuristic: bool = False):
    if use_heuristic:
        from agent.heuristic import HeuristicLLM

        return HeuristicLLM()
    if spec.startswith("fake:"):
        sys.path.insert(0, str(REPO_ROOT))
        from tests.fake_llm import SCRIPTS, FakeLLM

        name = spec.split(":", 1)[1]
        if name not in SCRIPTS:
            raise SystemExit(f"unknown fake script '{name}'; choose from {', '.join(SCRIPTS)}")
        return FakeLLM(SCRIPTS[name])
    from agent.llm import LLM

    return LLM(settings, example_path, dump_dir=dump_dir)


def _run_build(args, settings: Settings, bench: bool = False) -> int:
    url = normalise_url(args.url)
    if getattr(args, "dry_run_url", False):
        print(url)
        return 0
    company = args.company or company_from_url(url)
    profile = load_profile(args.profile)
    ts = time.strftime("%Y-%m-%dT%H-%M-%S")
    if bench:
        run_dir = REPO_ROOT / "runs" / company / "bench" / ts
        src = REPO_ROOT / "fillers" / company
        filler_dir = run_dir / "filler"
        filler_dir.mkdir(parents=True, exist_ok=True)
        for name in ("manifest.yaml", "tests.yaml"):
            if (src / name).exists():
                shutil.copy(src / name, filler_dir / name)
    else:
        run_dir = REPO_ROOT / "runs" / company / ts
        filler_dir = REPO_ROOT / "fillers" / company
    manifest = ensure_filler(filler_dir, company, url, settings)
    site = site_from_url(url)
    headless = bool(args.headless) or settings.HEADLESS
    browser = make_browser(site, manifest, headless)
    log = ActionLog(run_dir, mask_values=profile.mask_values(), run_id=f"{company}/{ts}", echo=True)
    example_path = Path(args.example) if getattr(args, "example", None) else None
    if getattr(args, "no_example", False):
        example_path = run_dir / "empty_example.py"
        example_path.write_text("# no worked example for this benchmark\n", encoding="utf-8")
    dump_dir = (run_dir / "prompts") if settings.DEBUG_PROMPTS else None
    llm = pick_llm(args.llm, settings, example_path, dump_dir, use_heuristic=getattr(args, "heuristic", False))
    ctx = ToolContext(browser=browser, steps_file=StepsFile(filler_dir / "steps.py"), profile=profile, log=log,
                      company=company, filler_dir=filler_dir, run_dir=run_dir, example_path=example_path,
                      mode="build", cap_tokens=settings.OBSERVE_TOKEN_CAP)
    loop = BuildLoop(settings, ctx, llm, url, ask_human=console_ask, report_fn=write_report,
                     use_suggest=not getattr(args, "no_suggest", False), verbose=bool(getattr(args, "verbose", False)))
    try:
        # detect the application system once the page is open (the loop opens it; peek afterwards)
        outcome = loop.run()
    except NonInteractive as exc:
        print(f"needs a human and stdin is not a TTY: {exc}", file=sys.stderr)
        write_report(loop, type("O", (), {"status": "ASK", "reason": str(exc), "flags": [], "check": None})())
        browser.close()
        return 3
    except LLMUnavailable as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        write_report(loop, type("O", (), {"status": "STOP", "reason": str(exc), "flags": [], "check": None})())
        browser.close()
        return 3
    finally:
        try:
            system = browser.detect_system()
        except Exception:
            system = "unknown"
    try:
        if system != "unknown":
            manifest["application_system"] = system
            hosts = set(manifest.get("allowed_hosts") or [])
            if system == "successfactors":
                hosts.update(SF_HOSTS)
            manifest["allowed_hosts"] = sorted(hosts)
        manifest.setdefault("built_with", {})
        manifest["built_with"].update({"model": settings.LLM_MODEL, "harness": settings.HARNESS_VERSION,
                                       "run": str(run_dir.relative_to(REPO_ROOT)).replace("\\", "/")})
        save_manifest(filler_dir, manifest)
    finally:
        browser.close()
    print(f"{outcome.status}: {outcome.reason}")
    if outcome.report_path:
        print(f"report: {outcome.report_path}")
    if bench and outcome.report_path:
        hist = run_dir.parent / "history.jsonl"
        m = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
        m.update({"ts": ts, "example": not getattr(args, "no_example", False),
                  "suggest": not getattr(args, "no_suggest", False), "heuristic": bool(getattr(args, "heuristic", False))})
        with hist.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(m) + "\n")
        print(f"bench metrics appended to {hist}")
    return 0 if outcome.status == "DONE" else 2


def _run_test(args, settings: Settings) -> int:
    company = args.company
    filler_dir = REPO_ROOT / "fillers" / company
    if not (filler_dir / "steps.py").exists():
        print(f"no steps.py under {filler_dir}", file=sys.stderr)
        return 1
    manifest = yaml.safe_load((filler_dir / "manifest.yaml").read_text(encoding="utf-8")) if (filler_dir / "manifest.yaml").exists() else {}
    profile = load_profile(args.profile)
    samples = yaml.safe_load((filler_dir / "tests.yaml").read_text(encoding="utf-8")) or {}
    first = (samples.get("samples") or [{}])[0]
    first_url = first["url"] if isinstance(first, dict) else str(first)
    site = site_from_url(first_url)
    ts = time.strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = REPO_ROOT / "runs" / company / f"{ts}-test"
    log = ActionLog(run_dir, mask_values=profile.mask_values(), run_id=f"{company}/{ts}-test", echo=True)
    browser = make_browser(site, manifest, bool(args.headless) or settings.HEADLESS)
    try:
        res = run_checks(filler_dir, browser, profile, log=log, mode="test")
    finally:
        browser.close()
    print(res.summary())
    return 0 if res.status == "PASS" else 1


def _run_report(args, settings: Settings) -> int:
    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = REPO_ROOT / run_dir
    company = run_dir.parent.name if run_dir.parent.name != "bench" else run_dir.parent.parent.name
    filler_dir = run_dir / "filler" if (run_dir / "filler").exists() else REPO_ROOT / "fillers" / company
    sf = StepsFile(filler_dir / "steps.py")
    log = ActionLog(run_dir, mask_values=[])
    rows = log.rows()
    status = "DONE" if any(r.get("tool") == "done" for r in rows) else ("STOP" if any(r.get("tool") == "stop" for r in rows) else "ASK")
    out = build_report(run_dir, company, "", sf.read(), sf.names() if sf.path.exists() else [], None, log, [], status)
    print(out)
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252; never crash on "→" or "·"
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="python -m agent", description="Job-form builder agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build a filler for a job URL")
    b.add_argument("url")
    b.add_argument("--company")
    b.add_argument("--profile", default="profiles/dummy.yaml")
    b.add_argument("--example", help="worked example steps.py to show the model")
    b.add_argument("--headless", action="store_true")
    b.add_argument("--llm", default="real", help="real | fake:<script>")
    b.add_argument("--no-suggest", action="store_true", help="hide suggested_next from the model")
    b.add_argument("--dry-run-url", action="store_true", help="print the normalised URL and exit")
    b.add_argument("-v", "--verbose", action="store_true", help="print each prompt, model reply and generated code")

    t = sub.add_parser("test", help="run the full check on a saved filler")
    t.add_argument("company")
    t.add_argument("--profile", default="profiles/dummy.yaml")
    t.add_argument("--headless", action="store_true")

    be = sub.add_parser("bench", help="from-scratch rebuild into runs/<company>/bench/ (never writes fillers/)")
    be.add_argument("company")
    be.add_argument("--url", help="job URL (default: first sample in fillers/<company>/tests.yaml)")
    be.add_argument("--profile", default="profiles/dummy.yaml")
    be.add_argument("--headless", action="store_true")
    be.add_argument("--llm", default="real")
    be.add_argument("--no-example", action="store_true")
    be.add_argument("--no-suggest", action="store_true")
    be.add_argument("--heuristic", action="store_true", help="no-LLM baseline")
    be.add_argument("-v", "--verbose", action="store_true", help="print each prompt, model reply and generated code")

    r = sub.add_parser("report", help="regenerate report.html for a run directory")
    r.add_argument("run_dir")

    args = parser.parse_args(argv)
    settings = Settings()
    if args.cmd == "build":
        return _run_build(args, settings)
    if args.cmd == "test":
        return _run_test(args, settings)
    if args.cmd == "bench":
        if not args.url:
            tests_path = REPO_ROOT / "fillers" / args.company / "tests.yaml"
            data = yaml.safe_load(tests_path.read_text(encoding="utf-8")) if tests_path.exists() else {}
            first = (data.get("samples") or [None])[0]
            if not first:
                print("bench needs --url or a sample in tests.yaml", file=sys.stderr)
                return 1
            args.url = first["url"] if isinstance(first, dict) else str(first)
        args.example = None
        args.dry_run_url = False
        return _run_build(args, settings, bench=True)
    if args.cmd == "report":
        return _run_report(args, settings)
    return 1
