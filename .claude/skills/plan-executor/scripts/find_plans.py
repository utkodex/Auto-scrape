#!/usr/bin/env python3
"""
Find and order plan files in a folder.

When the user points at a folder instead of a single plan, this lists the plan
markdown files and proposes an execution order so you don't have to crawl the
tree by hand. Order = numeric prefix first (01-, 02-, step1...), then any
"depends on / after" hint found in the text, then alphabetical.

Usage:
    python find_plans.py PATH       # PATH may be a folder or a single file

Output: JSON { "plans": [ {path, order, reason} ... ] } on stdout.
Treat the order as a sensible default, not a hard constraint — read the plans
and reorder if the content clearly implies a different sequence.
"""
import json
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}
PLAN_HINT = re.compile(
    r"(plan|spec|design|roadmap|todo|task|implementation|milestone|phase|feature)",
    re.I,
)
NUM_PREFIX = re.compile(r"(?:^|[^0-9])(\d{1,3})")
DEP_HINT = re.compile(r"(?:depends on|after|requires|follows)\s+([^\n.;,]+)", re.I)


def looks_like_plan(p: Path) -> bool:
    if p.suffix.lower() not in (".md", ".markdown", ".txt"):
        return False
    if PLAN_HINT.search(p.name):
        return True
    # fall back to content sniff: a plan tends to describe steps/goals
    try:
        head = p.read_text(encoding="utf-8", errors="ignore")[:2000].lower()
    except Exception:
        return False
    return any(k in head for k in ("## step", "## phase", "objective", "implement",
                                   "goal:", "deliverable", "acceptance", "plan"))


def collect(path: Path):
    if path.is_file():
        return [path]
    found = []
    for p in path.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and looks_like_plan(p):
            found.append(p)
    # If a dedicated plans/ directory exists, prefer just those.
    plans_dir = [f for f in found if "plans" in [pp.lower() for pp in f.parts]]
    return plans_dir or found


def sort_key(p: Path):
    m = NUM_PREFIX.search(p.stem)
    num = int(m.group(1)) if m else 9999
    return (num, p.name.lower())


def main():
    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    files = collect(target)
    files = sorted(set(files), key=sort_key)

    plans = []
    for i, f in enumerate(files, 1):
        reason_bits = []
        m = NUM_PREFIX.search(f.stem)
        if m:
            reason_bits.append(f"numeric prefix {m.group(1)}")
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")[:4000]
            dep = DEP_HINT.search(txt)
            if dep:
                reason_bits.append(f"hint: depends on '{dep.group(1).strip()[:40]}'")
        except Exception:
            pass
        plans.append({
            "path": str(f),
            "order": i,
            "reason": "; ".join(reason_bits) or "alphabetical",
        })

    print(json.dumps({"target": str(target), "count": len(plans), "plans": plans}, indent=2))


if __name__ == "__main__":
    main()
