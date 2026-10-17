#!/usr/bin/env python3
"""
validate_plan.py — deterministic quality checks for a drafted plan.md.

Catches the failure modes that make plans break in execution:
  - duplicate task IDs
  - `Depends on` references to non-existent tasks
  - dependency cycles (via topological sort)
  - tasks missing a Files (Creates/Modifies) or Verify line
  - `Modifies` paths that don't exist in the repo (errors)
  - `Creates` paths that already exist (warnings — you'd overwrite)

Usage:
    python validate_plan.py path/to/01.plan.md --repo /path/to/repo
    python validate_plan.py path/to/01.plan.md            # skip file-existence

Exit code is non-zero if any ERROR is found (warnings don't fail).
"""
import argparse
import os
import re
import sys

TASK_RE = re.compile(r"^\s*-\s*\[[ xX]?\]\s*(T\d+)\s*:\s*(.*)$")
HEADER_RE = re.compile(r"^\s{0,3}#{1,6}\s")
DEPENDS_RE = re.compile(r"depends on", re.IGNORECASE)
CREATES_RE = re.compile(r"\bcreates?\b", re.IGNORECASE)
MODIFIES_RE = re.compile(r"\bmodif(?:y|ies|ied)\b", re.IGNORECASE)
VERIFY_RE = re.compile(r"\bverif(?:y|ies|ication)\b", re.IGNORECASE)
TASKID_RE = re.compile(r"\bT\d+\b")
BACKTICK_RE = re.compile(r"`([^`]+)`")

# A backtick token is treated as a file path if it has a path separator or a
# plausible file extension — so `some/dir/file.py` counts, `function foo` does not.
PATH_HINT = re.compile(r"[\\/]|\.[A-Za-z0-9]{1,6}$")


def looks_like_path(tok):
    tok = tok.strip()
    if not tok or " " in tok.strip().rstrip("/"):
        # allow spaces only if it still looks like a quoted path? keep strict.
        if " " in tok:
            return False
    return bool(PATH_HINT.search(tok))


def parse_tasks(lines):
    """Return list of task dicts with id, title, and the text block."""
    tasks = []
    cur = None
    for ln in lines:
        m = TASK_RE.match(ln)
        if m:
            if cur:
                tasks.append(cur)
            cur = {"id": m.group(1), "title": m.group(2).strip(), "block": []}
            continue
        if cur is not None:
            # a markdown header ends the current task block
            if HEADER_RE.match(ln):
                tasks.append(cur)
                cur = None
                continue
            # a new top-level bullet at column 0 that's not part of the task
            cur["block"].append(ln)
    if cur:
        tasks.append(cur)
    return tasks


def extract_paths(block_lines, which_re):
    paths = []
    for ln in block_lines:
        if which_re.search(ln):
            for tok in BACKTICK_RE.findall(ln):
                if looks_like_path(tok):
                    paths.append(tok.strip())
    return paths


def extract_depends(block_lines):
    deps = []
    for ln in block_lines:
        if DEPENDS_RE.search(ln):
            if re.search(r"\bnone\b", ln, re.IGNORECASE):
                continue
            deps.extend(TASKID_RE.findall(ln))
    return deps


def topo_has_cycle(graph):
    """Kahn's algorithm. Return list of nodes in a cycle (empty if acyclic)."""
    indeg = {n: 0 for n in graph}
    for n in graph:
        for d in graph[n]:
            if d in indeg:
                indeg[n] += 1  # n depends on d -> edge d->n
    queue = [n for n in indeg if indeg[n] == 0]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for m in graph:
            if n in graph[m]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    queue.append(m)
    if seen == len(graph):
        return []
    return [n for n in indeg if indeg[n] > 0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--repo", default=None, help="Repo root for file checks.")
    args = ap.parse_args()

    if not os.path.isfile(args.plan):
        print(f"error: plan not found: {args.plan}", file=sys.stderr)
        return 2

    with open(args.plan, encoding="utf-8", errors="ignore") as f:
        lines = f.read().splitlines()

    tasks = parse_tasks(lines)
    errors, warnings = [], []

    if not tasks:
        errors.append("No tasks found (expected lines like '- [ ] T1: ...').")

    ids = [t["id"] for t in tasks]
    seen = set()
    for tid in ids:
        if tid in seen:
            errors.append(f"Duplicate task ID: {tid}")
        seen.add(tid)
    id_set = set(ids)

    graph = {}
    for t in tasks:
        block = t["block"]
        deps = extract_depends(block)
        graph[t["id"]] = [d for d in deps if d != t["id"]]
        for d in deps:
            if d not in id_set:
                errors.append(f"{t['id']}: depends on unknown task {d}")
            if d == t["id"]:
                errors.append(f"{t['id']}: depends on itself")

        has_files = bool(extract_paths(block, CREATES_RE)) or \
            bool(extract_paths(block, MODIFIES_RE)) or \
            any(CREATES_RE.search(l) or MODIFIES_RE.search(l) for l in block)
        if not has_files:
            warnings.append(f"{t['id']}: no Creates/Modifies (Files) line found")
        if not any(VERIFY_RE.search(l) for l in block):
            errors.append(f"{t['id']}: no Verify line found (tasks must be "
                          f"verifiable)")

    cycle = topo_has_cycle(graph)
    if cycle:
        errors.append(f"Dependency cycle among tasks: {', '.join(sorted(cycle))}")

    if args.repo:
        repo = os.path.abspath(args.repo)
        for t in tasks:
            for p in extract_paths(t["block"], MODIFIES_RE):
                if not os.path.exists(os.path.join(repo, p)):
                    errors.append(f"{t['id']}: Modifies missing file '{p}' "
                                  f"(not found under repo)")
            for p in extract_paths(t["block"], CREATES_RE):
                if os.path.exists(os.path.join(repo, p)):
                    warnings.append(f"{t['id']}: Creates '{p}' but it already "
                                    f"exists (would overwrite)")

    print(f"Tasks found: {len(tasks)}  ({', '.join(ids) if ids else 'none'})")
    for w in warnings:
        print(f"  WARN  {w}")
    for e in errors:
        print(f"  ERROR {e}")

    if errors:
        print(f"\nFAILED: {len(errors)} error(s), {len(warnings)} warning(s).")
        return 1
    print(f"\nOK: 0 errors, {len(warnings)} warning(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
