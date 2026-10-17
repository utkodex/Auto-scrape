"""report.html + metrics + secret scan for one run.

Metric names match .claude/specs/01.overview.md §3 ("How measured" column).
"""

from __future__ import annotations

import difflib
import json
import re
import statistics
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.log import SECRET_RE, ActionLog

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
TEXT_SUFFIXES = {".jsonl", ".md", ".html", ".json", ".txt", ".yaml", ".yml", ".py"}


def metrics(
    summary: dict,
    steps_names: list[str],
    check: dict | None,
    status: str,
    log_rows: list[dict],
    blocked_requests: int = 0,
    wall_seconds: float = 0.0,
    secret_hits: int = 0,
) -> dict:
    tries = summary.get("tries_per_step") or {}
    tries_vals = list(tries.values())
    first_try = summary.get("first_try_pass") or []
    seen = summary.get("steps_seen") or []
    observe_tokens = [r.get("tokens", 0) for r in log_rows if r.get("tool") == "observe" and r.get("tokens") is not None]
    rc_rows = [r for r in log_rows if r.get("tool") == "run_checks" and r.get("s") is not None]
    rc_per_sample = 0.0
    if rc_rows and check and check.get("total"):
        rc_per_sample = round(rc_rows[-1]["s"] / max(1, check["total"]), 1)
    undo = summary.get("undo_count", 0)
    rejected_locked = sum(1 for r in log_rows if r.get("tool") == "edit_file" and "locked" in (r.get("result") or ""))
    submit_blocked = sum(1 for r in log_rows if "submit is not allowed" in (r.get("result") or ""))
    submit_presses = sum(1 for r in log_rows if "submitted" in (r.get("page_kind") or ""))
    return {
        "status": status,
        "rounds_total": summary.get("rounds", 0),
        "steps_total": len(steps_names),
        "first_try_pass_rate": round(len(first_try) / len(seen), 3) if seen else 0.0,
        "tries_per_step_median": statistics.median(tries_vals) if tries_vals else 0,
        "tries_per_step_max": max(tries_vals) if tries_vals else 0,
        "fix_count": summary.get("fix_count", 0),
        "fix_locality_violations": 0,  # enforced by agent/steps_file.py; rejected attempts below
        "fix_locality_rejected": rejected_locked,
        "regressions_undone": undo,
        "regressions_open": 0 if status == "DONE" else undo,
        "tok_in_total": summary.get("tok_in_total", 0),
        "tok_out_total": summary.get("tok_out_total", 0),
        "tokens_total": summary.get("tok_in_total", 0) + summary.get("tok_out_total", 0),
        "tok_in_p95": summary.get("tok_in_p95", 0),
        "tok_out_max": summary.get("tok_out_max", 0),
        "qwen_seconds_p50": summary.get("qwen_seconds_p50", 0.0),
        "seconds_total": summary.get("seconds_total", 0.0),
        "seconds_human": summary.get("seconds_human", 0.0),
        "seconds_wall": round(wall_seconds, 1),
        "seconds_wall_excluding_human": round(max(0.0, wall_seconds - summary.get("seconds_human", 0.0)), 1),
        "run_checks_seconds_per_sample": rc_per_sample,
        "observe_tokens_max": max(observe_tokens) if observe_tokens else 0,
        "submit_presses": submit_presses,
        "submit_blocked": submit_blocked,
        "blocked_requests": blocked_requests,
        "secret_hits": secret_hits,
        "samples_passed": (check or {}).get("passed", 0),
        "samples_total": (check or {}).get("total", 0),
    }


def scan_for_secrets(run_dir: Path | str, mask_values: list[str]) -> list[str]:
    hits: list[str] = []
    needles = [v for v in mask_values if v and len(str(v)) >= 3]
    for path in Path(run_dir).rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        low = text.lower()
        for v in needles:
            if str(v).lower() in low:
                hits.append(f"{path.name}: {v[:3]}…")
        if SECRET_RE.search(text) and "[redacted line]" not in text:
            hits.append(f"{path.name}: secret pattern")
    return hits


def build_report(
    run_dir: Path | str,
    company: str,
    steps_text_before: str,
    steps_text_after: str,
    steps_names: list[str],
    check: dict | None,
    log: ActionLog,
    flags: list[dict],
    status: str,
    reason: str = "",
    blocked_requests: int = 0,
    wall_seconds: float = 0.0,
    mask_values: list[str] | None = None,
) -> Path:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = log.summary()
    rows = log.rows()
    hits = scan_for_secrets(run_dir, mask_values or [])
    m = metrics(summary, steps_names, check, status, rows, blocked_requests, wall_seconds, len(hits))
    diff = "\n".join(difflib.unified_diff(
        steps_text_before.splitlines(), steps_text_after.splitlines(),
        fromfile="steps.py (run start)", tofile="steps.py (now)", lineterm="",
    ))
    env = Environment(loader=FileSystemLoader(str(PROMPTS_DIR)), autoescape=select_autoescape(["html", "j2"]))
    tpl = env.get_template("report.html.j2")
    html = tpl.render(
        company=company, status=status, reason=log.mask(reason), generated=time.strftime("%Y-%m-%d %H:%M:%S"),
        metrics=m, metrics_json=json.dumps(m, indent=2), diff=log.mask(diff) or "(no changes)",
        check=check or {}, flags=flags, md=log.mask((run_dir / "actions.md").read_text(encoding="utf-8")) if (run_dir / "actions.md").exists() else "",
        secret_hits=hits, steps=steps_names, tries=summary.get("tries_per_step") or {},
    )
    out = run_dir / "report.html"
    out.write_text(html, encoding="utf-8")
    (run_dir / "metrics.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
    return out


def write_report(loop, outcome) -> Path:
    """Adapter used by BuildLoop.report_fn."""
    ctx = loop.ctx
    check = outcome.check.to_tool_result() if outcome.check else None
    if check is None:
        last = [r for r in ctx.log.rows() if r.get("tool") == "run_checks"]
        if last:
            m = re.search(r"(PASS|FAIL)\D*(\d+)/(\d+)", last[-1].get("result", ""))
            if m:
                check = {"status": m.group(1), "passed": int(m.group(2)), "total": int(m.group(3))}
    return build_report(
        ctx.run_dir, ctx.company, loop.start_text, ctx.steps_file.read(), ctx.steps_file.names(), check, ctx.log,
        outcome.flags, outcome.status, outcome.reason, blocked_requests=getattr(ctx.browser, "blocked_requests", 0),
        wall_seconds=time.monotonic() - loop.t0, mask_values=ctx.profile.mask_values(),
    )
