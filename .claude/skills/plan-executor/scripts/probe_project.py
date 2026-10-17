#!/usr/bin/env python3
"""
Probe a project once and emit a compact JSON capabilities map.

Why this exists: executing a plan means running, testing, and verifying a
codebase many times. Re-discovering "how do I run this / test this / where are
the traces" on every step wastes a lot of context. Run this once, keep the
small JSON it returns, and reuse it for every later step.

Usage:
    python probe_project.py [PROJECT_DIR]   # defaults to current directory

Output: a single JSON object on stdout. Stdlib only, no dependencies.
The heuristics are best-effort hints, not gospel — trust the actual project
over the probe when they disagree, and prefer the project's README/Makefile.
"""
import json
import os
import re
import sys
from pathlib import Path

# Keep the scan cheap: skip noisy / heavy directories.
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".next", ".turbo", "site-packages", ".tox", ".idea", ".vscode",
}
MAX_FILES_SCAN = 6000  # bail out on enormous repos rather than crawling forever


def iter_files(root: Path):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git")]
        for f in filenames:
            count += 1
            if count > MAX_FILES_SCAN:
                return
            yield Path(dirpath) / f


def read_text(p: Path, limit=200_000):
    try:
        return p.read_text(encoding="utf-8", errors="ignore")[:limit]
    except Exception:
        return ""


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    result = {
        "root": str(root),
        "languages": [],
        "package_manager": None,
        "dependency_files": [],
        "build_cmds": [],
        "test_cmds": [],
        "run_cmds": [],
        "lint_typecheck_cmds": [],
        "tracing": [],          # e.g. mlflow / databricks-mlflow / langsmith / langfuse / opentelemetry / wandb
        "data_locations": [],   # dirs / artifacts worth checking after a run
        "make_targets": [],
        "env_files": [],
        "notes": [],
    }

    names = set()
    deps_blob = ""           # concatenated dependency manifests for keyword scans
    import_hits = set()      # source-code import signals
    py_seen = js_seen = False

    makefile = None
    for p in iter_files(root):
        rel = p.relative_to(root).as_posix()
        name = p.name
        names.add(name)
        low = name.lower()
        suffix = p.suffix.lower()

        if suffix == ".py":
            py_seen = True
        if suffix in (".ts", ".tsx", ".js", ".jsx"):
            js_seen = True

        if low in ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg",
                   "pipfile", "environment.yml", "environment.yaml", "package.json",
                   "uv.lock", "poetry.lock", "pixi.toml"):
            result["dependency_files"].append(rel)
            deps_blob += "\n" + read_text(p, 60_000)

        if low in ("makefile", "gnumakefile"):
            makefile = p
        if low.startswith(".env") or low == ".envrc":
            result["env_files"].append(rel)

        # Cheap source scan for tracing/run signals on a bounded set of files.
        if suffix == ".py" and len(import_hits) < 40:
            txt = read_text(p, 40_000)
            for kw in ("import mlflow", "from mlflow", "mlflow.",
                       "import langsmith", "from langsmith", "langsmith",
                       "import langfuse", "from langfuse", "langfuse",
                       "opentelemetry", "import wandb", "phoenix", "arize",
                       "set_tracking_uri", "databricks"):
                if kw in txt:
                    import_hits.add(kw)

    deps_low = deps_blob.lower()

    # ---- languages & package manager -------------------------------------
    if py_seen or any(d.endswith(("pyproject.toml", "requirements.txt", "setup.py", "Pipfile"))
                      for d in result["dependency_files"]):
        result["languages"].append("python")
    if js_seen or "package.json" in names:
        result["languages"].append("javascript/typescript")

    if "uv.lock" in names or "[tool.uv]" in deps_low:
        result["package_manager"] = "uv"
    elif "poetry.lock" in names or "[tool.poetry]" in deps_low:
        result["package_manager"] = "poetry"
    elif "Pipfile" in names:
        result["package_manager"] = "pipenv"
    elif "environment.yml" in names or "environment.yaml" in names:
        result["package_manager"] = "conda"
    elif "requirements.txt" in names or "pyproject.toml" in names:
        result["package_manager"] = "pip"
    elif "package.json" in names:
        result["package_manager"] = "npm/pnpm/yarn"

    # ---- test commands ----------------------------------------------------
    if "python" in result["languages"]:
        if "pytest" in deps_low or any((root / d).exists() for d in ("tests", "test")):
            runner = {"uv": "uv run pytest", "poetry": "poetry run pytest"}.get(
                result["package_manager"], "pytest")
            result["test_cmds"].append(runner + " -q")
        else:
            result["test_cmds"].append("python -m pytest -q  # if tests exist")
    if "package.json" in names:
        pkg = read_text(root / "package.json", 40_000)
        try:
            scripts = json.loads(pkg).get("scripts", {})
            for s in ("test", "build", "dev", "start", "lint", "typecheck", "type-check"):
                if s in scripts:
                    cmd = f"npm run {s}"
                    if s == "test":
                        result["test_cmds"].append(cmd)
                    elif s in ("build",):
                        result["build_cmds"].append(cmd)
                    elif s in ("dev", "start"):
                        result["run_cmds"].append(cmd)
                    else:
                        result["lint_typecheck_cmds"].append(cmd)
        except Exception:
            result["notes"].append("package.json present but scripts unparsed")

    # ---- run entrypoints --------------------------------------------------
    for cand in ("main.py", "app.py", "run.py", "src/main.py", "cli.py"):
        if (root / cand).exists():
            pref = {"uv": "uv run ", "poetry": "poetry run "}.get(result["package_manager"], "")
            result["run_cmds"].append(f"{pref}python {cand}")
    # package __main__.py -> python -m <pkg>
    src = root / "src"
    search_roots = [src] if src.exists() else [root]
    for base in search_roots:
        if base.exists():
            for child in base.iterdir():
                if child.is_dir() and (child / "__main__.py").exists() and child.name not in SKIP_DIRS:
                    result["run_cmds"].append(f"python -m {child.name}")

    # ---- lint / typecheck -------------------------------------------------
    if "python" in result["languages"]:
        if "ruff" in deps_low:
            result["lint_typecheck_cmds"].append("ruff check .")
        if "mypy" in deps_low:
            result["lint_typecheck_cmds"].append("mypy .")
        if "pyright" in deps_low:
            result["lint_typecheck_cmds"].append("pyright")

    # ---- tracing / observability -----------------------------------------
    blob_for_trace = deps_low + " " + " ".join(import_hits)
    is_databricks = ("databricks" in blob_for_trace
                     or "databricks.yml" in names or "databricks.yaml" in names
                     or ".databrickscfg" in names)
    if "mlflow" in blob_for_trace:
        result["tracing"].append("databricks-mlflow" if is_databricks else "mlflow")
        if (root / "mlruns").exists():
            result["data_locations"].append("mlruns/  # local MLflow runs & artifacts")
    if "langsmith" in blob_for_trace:
        result["tracing"].append("langsmith")
    if "langfuse" in blob_for_trace:
        result["tracing"].append("langfuse")
    if "opentelemetry" in blob_for_trace:
        result["tracing"].append("opentelemetry")
    if "wandb" in blob_for_trace:
        result["tracing"].append("wandb")
    if "phoenix" in blob_for_trace or "arize" in blob_for_trace:
        result["tracing"].append("phoenix/arize")
    if is_databricks and "databricks-mlflow" not in result["tracing"]:
        result["notes"].append("Databricks signals found; traces likely in Databricks MLflow.")

    # ---- data locations ---------------------------------------------------
    for d in ("data", "datasets", "artifacts", "outputs", "output", "results", "checkpoints"):
        if (root / d).exists():
            result["data_locations"].append(d + "/")
    for ext in (".parquet", ".csv", ".jsonl"):
        if any(name.endswith(ext) for name in names):
            result["data_locations"].append(f"*{ext} files present")

    # ---- makefile targets -------------------------------------------------
    if makefile:
        for m in re.finditer(r"^([a-zA-Z0-9_.-]+):", read_text(makefile, 40_000), re.M):
            t = m.group(1)
            if t not in ("PHONY", ".PHONY"):
                result["make_targets"].append(t)
        result["make_targets"] = sorted(set(result["make_targets"]))[:25]

    # de-dupe while preserving order
    for k in ("languages", "build_cmds", "test_cmds", "run_cmds",
              "lint_typecheck_cmds", "tracing", "data_locations", "dependency_files"):
        seen, out = set(), []
        for v in result[k]:
            if v not in seen:
                seen.add(v)
                out.append(v)
        result[k] = out

    if not result["run_cmds"]:
        result["notes"].append("No obvious entrypoint found — check README for how to run.")

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
