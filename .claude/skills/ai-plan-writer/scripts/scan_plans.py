#!/usr/bin/env python3
"""
scan_plans.py — compute plan filenames for a target plans folder, mirroring the
ai-spec-writer naming convention so plans trace back to specs.

Modes:
  1. Mirror a whole spec folder: one plan per spec, SAME number + slug.
       python scan_plans.py plans/ --mirror-spec-folder specs/
  2. Mirror a single spec file: same number (+ slug) with .plan.md.
       python scan_plans.py plans/ --mirror-spec specs/02.data.spec.md
  3. Additive suite (no spec to mirror):
       python scan_plans.py plans/ --suite ingest train serve eval
  4. Additive single:
       python scan_plans.py plans/

Mirroring uses the spec's exact NN/slug for traceability (it does NOT renumber
from the plans folder). The script reports whether each target already exists so
the caller can decide to overwrite or skip. Slugs in --skip-slugs (default:
overview, index, risks, decisions) are flagged as likely "nothing to implement".
"""
import argparse
import json
import os
import re
import sys

SPEC_RE = re.compile(r"^(\d{2,})\.(?:(.+)\.)?spec\.md$", re.IGNORECASE)
PLAN_RE = re.compile(r"^(\d{2,})\.(?:.+\.)?plan\.md$", re.IGNORECASE)
DEFAULT_SKIP = {"overview", "index", "risks", "decisions", "risks-decisions"}


def slugify(text):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
    return re.sub(r"-+", "-", s).strip("-")


def highest(folder, regex):
    if not os.path.isdir(folder):
        return 0
    hi = 0
    for name in os.listdir(folder):
        m = regex.match(name)
        if m:
            hi = max(hi, int(m.group(1)))
    return hi


def list_specs(spec_folder):
    out = []
    for name in sorted(os.listdir(spec_folder)):
        m = SPEC_RE.match(name)
        if m:
            out.append((int(m.group(1)), m.group(2)))  # (number, slug-or-None)
    return out


def plan_name(number, slug, pad=2):
    if slug:
        return f"{number:0{pad}d}.{slugify(slug)}.plan.md"
    return f"{number:0{pad}d}.plan.md"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plans_folder")
    ap.add_argument("--mirror-spec-folder")
    ap.add_argument("--mirror-spec")
    ap.add_argument("--suite", nargs="+", metavar="SLUG")
    ap.add_argument("--skip-slugs", nargs="*", default=sorted(DEFAULT_SKIP))
    ap.add_argument("--pad", type=int, default=2)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pad = max(args.pad, 2)
    skip = {slugify(s) for s in args.skip_slugs}
    results = []

    if args.mirror_spec_folder:
        if not os.path.isdir(args.mirror_spec_folder):
            print(f"error: spec folder not found: {args.mirror_spec_folder}",
                  file=sys.stderr)
            return 1
        for number, slug in list_specs(args.mirror_spec_folder):
            fname = plan_name(number, slug, pad)
            path = os.path.join(args.plans_folder, fname)
            results.append({
                "number": number, "slug": slug, "filename": fname, "path": path,
                "exists": os.path.exists(path),
                "likely_skip": bool(slug) and slugify(slug) in skip,
            })

    elif args.mirror_spec:
        base = os.path.basename(args.mirror_spec)
        m = SPEC_RE.match(base)
        if not m:
            print(f"error: not a spec filename (NN[.slug].spec.md): {base}",
                  file=sys.stderr)
            return 1
        number, slug = int(m.group(1)), m.group(2)
        fname = plan_name(number, slug, pad)
        path = os.path.join(args.plans_folder, fname)
        results.append({"number": number, "slug": slug, "filename": fname,
                        "path": path, "exists": os.path.exists(path),
                        "likely_skip": bool(slug) and slugify(slug) in skip})

    elif args.suite:
        start = highest(args.plans_folder, PLAN_RE) + 1
        for i, slug in enumerate(args.suite):
            number = start + i
            fname = plan_name(number, slug, pad)
            results.append({"number": number, "slug": slugify(slug),
                            "filename": fname,
                            "path": os.path.join(args.plans_folder, fname),
                            "exists": False, "likely_skip": False})
    else:
        number = highest(args.plans_folder, PLAN_RE) + 1
        fname = plan_name(number, None, pad)
        results.append({"number": number, "slug": None, "filename": fname,
                        "path": os.path.join(args.plans_folder, fname),
                        "exists": False, "likely_skip": False})

    if args.json:
        print(json.dumps({"plans_folder": args.plans_folder,
                          "files": results}, indent=2))
    else:
        for r in results:
            tags = []
            if r["exists"]:
                tags.append("EXISTS")
            if r["likely_skip"]:
                tags.append("likely-skip")
            suffix = f"  [{', '.join(tags)}]" if tags else ""
            print(r["path"] + suffix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
