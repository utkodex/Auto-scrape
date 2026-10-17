"""Manual smoke test: build the fixture form with the REAL model (T19 step 2).

Not collected by pytest. Requires the LLM server at LLM_BASE_URL (prefix caching on).

    python tests/live_fixture_smoke.py [--headless]

Passes when the loop reaches DONE within 25 rounds. Writes runs/fixture/latency_baseline.json with
p50/p95 round latency, which sets the §3 latency baseline.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent.config import Settings  # noqa: E402
from agent.llm import LLM  # noqa: E402
from agent.loop import BuildLoop  # noqa: E402
from agent.report import write_report  # noqa: E402
from agent.steps_file import StepsFile  # noqa: E402
from agent.tools import ToolContext  # noqa: E402
from core.browser import Browser  # noqa: E402
from core.log import ActionLog  # noqa: E402
from core.profile import load_profile  # noqa: E402
from tests.fixtures.sf_like.server import start  # noqa: E402

MAX_ROUNDS = 25


def main() -> int:
    headless = "--headless" in sys.argv
    settings = Settings()
    url, stop = start()
    ts = time.strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = REPO / "runs" / "fixture" / f"{ts}-live-smoke"
    filler = run_dir / "filler"
    filler.mkdir(parents=True)
    (filler / "tests.yaml").write_text(
        f"samples:\n  - url: {url}job.html?id=1\n  - url: {url}job.html?id=2\n", encoding="utf-8"
    )
    profile = load_profile("profiles/dummy.yaml")
    browser = Browser(site="fixture", allowed_hosts=["127.0.0.1"], headless=headless,
                      profiles_root=run_dir / "browser_profiles")
    log = ActionLog(run_dir, mask_values=profile.mask_values(), run_id=f"fixture/{ts}-live-smoke")
    ctx = ToolContext(browser=browser, steps_file=StepsFile(filler / "steps.py"), profile=profile, log=log,
                      company="fixture", filler_dir=filler, run_dir=run_dir, cap_tokens=settings.OBSERVE_TOKEN_CAP)
    llm = LLM(settings, dump_dir=run_dir / "prompts" if settings.DEBUG_PROMPTS else None)
    loop = BuildLoop(settings, ctx, llm, f"{url}job.html?id=1", ask_human=lambda q: input(f"\n[ask_human] {q}\n> "),
                     report_fn=write_report)
    try:
        outcome = loop.run()
    finally:
        browser.close()
        stop()
    qwen_secs = [r["s"] for r in log.rows() if r.get("actor") == "qwen" and r.get("s") is not None]
    baseline = {
        "model": settings.LLM_MODEL, "base_url": settings.LLM_BASE_URL, "ts": ts,
        "status": outcome.status, "rounds": outcome.rounds,
        "round_seconds_p50": round(statistics.median(qwen_secs), 2) if qwen_secs else None,
        "round_seconds_p95": round(sorted(qwen_secs)[int(0.95 * (len(qwen_secs) - 1))], 2) if qwen_secs else None,
        "tok_in_p95": log.summary()["tok_in_p95"], "tok_out_max": log.summary()["tok_out_max"],
        "report": str(outcome.report_path),
    }
    (REPO / "runs" / "fixture" / "latency_baseline.json").write_text(json.dumps(baseline, indent=2), encoding="utf-8")
    print(json.dumps(baseline, indent=2))
    print(f"{outcome.status} · rounds {outcome.rounds} (limit {MAX_ROUNDS}) · {outcome.reason}")
    return 0 if outcome.status == "DONE" and outcome.rounds <= MAX_ROUNDS else 1


if __name__ == "__main__":
    sys.exit(main())
