#!/usr/bin/env python3
"""
scan_specs.py — compute the next NN spec number(s) and exact filenames for a
target folder, enforcing the NN.<purpose>.md naming convention.

Every spec is named `NN.<purpose>.md`: a zero-padded serial number, a dot, a
purpose-based slug describing what the spec covers, then `.md`. There is no
`.spec` segment and no generic `spec` fallback — the slug must name the content.

The numbering is additive: it never reuses or overwrites an existing spec's
number. It scans the folder for files matching the spec pattern and continues
from the highest one found.

Usage:
    # Single spec  -> prints the next NN.<slug>.md path
    python scan_specs.py specs/ --slug rag-retrieval

    # Suite        -> prints one NN.<slug>.md path per slug, in order
    python scan_specs.py specs/ --suite overview data modeling implementation

    # JSON output (for programmatic use)
    python scan_specs.py specs/ --suite overview data --json
"""
import argparse
import json
import os
import re
import sys

# Matches "01.rag-retrieval.md", "02.overview.md" (zero-padded 2+ digit prefix
# followed by a purpose slug). Also tolerates any legacy "*.spec.md" files so
# numbering stays additive over folders written before the convention changed.
SPEC_RE = re.compile(r"^(\d{2,})\..+\.md$", re.IGNORECASE)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
    return re.sub(r"-+", "-", s).strip("-")


def highest_existing(folder: str) -> int:
    """Return the highest NN prefix among existing spec files, or 0 if none."""
    if not os.path.isdir(folder):
        return 0
    highest = 0
    for name in os.listdir(folder):
        m = SPEC_RE.match(name)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest


def main() -> int:
    ap = argparse.ArgumentParser(description="Compute next spec filename(s).")
    ap.add_argument("folder", help="Target folder for spec files.")
    ap.add_argument(
        "--slug",
        metavar="SLUG",
        help="Purpose slug for a single spec (e.g. rag-retrieval). Required "
             "unless --suite is used.",
    )
    ap.add_argument(
        "--suite",
        nargs="+",
        metavar="SLUG",
        help="Create a numbered suite: one filename per purpose slug, in order.",
    )
    ap.add_argument("--json", action="store_true", help="Emit JSON.")
    ap.add_argument(
        "--pad",
        type=int,
        default=2,
        help="Zero-padding width for the number (default 2).",
    )
    args = ap.parse_args()

    start = highest_existing(args.folder) + 1
    pad = max(args.pad, 2)

    results = []
    if args.suite:
        for i, slug in enumerate(args.suite):
            n = start + i
            fname = f"{n:0{pad}d}.{slugify(slug)}.md"
            results.append({"number": n, "slug": slugify(slug),
                            "filename": fname,
                            "path": os.path.join(args.folder, fname)})
    else:
        if not args.slug:
            ap.error("a single spec needs a purpose name: pass --slug <purpose> "
                     "(e.g. --slug rag-retrieval), or use --suite for many.")
        n = start
        slug = slugify(args.slug)
        fname = f"{n:0{pad}d}.{slug}.md"
        results.append({"number": n, "slug": slug, "filename": fname,
                        "path": os.path.join(args.folder, fname)})

    if args.json:
        print(json.dumps({"folder": args.folder,
                          "next_number": start,
                          "files": results}, indent=2))
    else:
        for r in results:
            print(r["path"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
