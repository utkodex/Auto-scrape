#!/usr/bin/env python3
"""
recon.py — token-efficient codebase inventory for AI/ML spec writing.

Produces a COMPACT structured summary instead of dumping file contents, so the
spec author understands the project shape for a tiny token cost, then reads only
the files that matter.

What it captures:
  - graphify graph location (if present) — read that first
  - languages / file-type counts
  - dependency manifests (requirements, pyproject, environment.yml, package.json)
  - detected ML/AI frameworks
  - likely entry points (train/predict/serve/main/app)
  - config files
  - data & model-artifact directories
  - test locations
  - rough size

Usage:
    python recon.py <repo_root> [--out recon.json] [--max-depth 6]
"""
import argparse
import json
import os
import re
import sys

GRAPHIFY_HINTS = ["graphify-out", "graphify_out", ".graphify", "graphify"]

ML_SIGNATURES = {
    "pytorch": ["torch", "pytorch_lightning", "lightning"],
    "tensorflow": ["tensorflow", "keras"],
    "jax": ["jax", "flax", "optax"],
    "sklearn": ["scikit-learn", "sklearn"],
    "transformers": ["transformers", "huggingface_hub", "datasets"],
    "llm-orchestration": ["langchain", "llama-index", "llama_index", "haystack",
                          "dspy", "guidance"],
    "vector-db": ["faiss", "chromadb", "pinecone", "weaviate", "qdrant",
                  "pgvector", "milvus"],
    "serving": ["fastapi", "flask", "bentoml", "ray", "triton", "vllm",
                "tgi", "litserve"],
    "experiment-tracking": ["mlflow", "wandb", "comet-ml", "neptune"],
    "orchestration": ["airflow", "prefect", "dagster", "kubeflow", "metaflow"],
    "data-versioning": ["dvc", "lakefs"],
    "data": ["pandas", "polars", "numpy", "spark", "pyspark", "dask"],
}

DEP_FILES = ["requirements.txt", "requirements-dev.txt", "pyproject.toml",
             "setup.py", "setup.cfg", "environment.yml", "environment.yaml",
             "Pipfile", "package.json", "poetry.lock"]

CONFIG_HINTS = (".yaml", ".yml", ".toml", ".ini", ".cfg", ".json", ".env")
CONFIG_NAMES = ("config", "conf", "settings", "params", "hydra")

ENTRY_HINTS = ("train", "predict", "infer", "serve", "main", "app", "pipeline",
               "evaluate", "eval", "run")

CODE_EXT = {".py": "python", ".ipynb": "notebook", ".js": "javascript",
            ".ts": "typescript", ".go": "go", ".rs": "rust", ".java": "java",
            ".scala": "scala", ".r": "r", ".sql": "sql", ".sh": "shell",
            ".cpp": "cpp", ".c": "c"}

DATA_DIR_HINTS = ("data", "datasets", "dataset", "raw", "processed")
MODEL_DIR_HINTS = ("models", "checkpoints", "ckpt", "weights", "artifacts",
                   "saved_model", "registry")
TEST_HINTS = ("test", "tests", "spec", "__tests__")

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env",
             ".mypy_cache", ".pytest_cache", ".idea", ".vscode", "dist",
             "build", ".egg-info", ".tox"}


def find_graphify(root):
    hits = []
    for name in GRAPHIFY_HINTS:
        p = os.path.join(root, name)
        if os.path.exists(p):
            hits.append(p)
    # also a shallow search
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath[len(root):].count(os.sep)
        if depth > 3:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for n in list(dirnames) + filenames:
            if "graphify" in n.lower():
                full = os.path.join(dirpath, n)
                if full not in hits:
                    hits.append(full)
    return sorted(set(hits))


def read_text(path, limit=20000):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(limit)
    except OSError:
        return ""


def detect_frameworks(dep_blob):
    blob = dep_blob.lower()
    found = {}
    for cat, sigs in ML_SIGNATURES.items():
        matched = [s for s in sigs if re.search(r"\b" + re.escape(s) + r"\b", blob)]
        if matched:
            found[cat] = sorted(set(matched))
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-depth", type=int, default=6)
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    lang_counts = {}
    entry_points, config_files = [], []
    data_dirs, model_dirs, test_dirs = set(), set(), set()
    dep_blob, dep_found = "", []
    total_code_files = 0

    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath[len(root):].count(os.sep)
        if depth >= args.max_depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS
                       and not d.endswith(".egg-info")]

        rel_dir = os.path.relpath(dirpath, root)
        low_dir = os.path.basename(dirpath).lower()
        if any(h == low_dir or low_dir.startswith(h) for h in DATA_DIR_HINTS):
            data_dirs.add(rel_dir)
        if any(h in low_dir for h in MODEL_DIR_HINTS):
            model_dirs.add(rel_dir)
        if any(h == low_dir for h in TEST_HINTS):
            test_dirs.add(rel_dir)

        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            low = fn.lower()
            rel = os.path.relpath(os.path.join(dirpath, fn), root)

            if ext in CODE_EXT:
                lang_counts[CODE_EXT[ext]] = lang_counts.get(CODE_EXT[ext], 0) + 1
                total_code_files += 1
                stem = os.path.splitext(low)[0]
                is_test = stem.startswith("test") or "/test" in rel.lower() \
                    or rel.lower().startswith("test")
                if not is_test and any(
                        stem == h or stem.startswith(h + "_")
                        or stem.endswith("_" + h) for h in ENTRY_HINTS):
                    entry_points.append(rel)

            if fn in DEP_FILES:
                dep_blob += "\n" + read_text(os.path.join(dirpath, fn))
                dep_found.append(rel)

            if (ext in CONFIG_HINTS and
                    any(h in low for h in CONFIG_NAMES)):
                config_files.append(rel)

    frameworks = detect_frameworks(dep_blob)

    summary = {
        "root": root,
        "graphify": find_graphify(root),
        "languages": dict(sorted(lang_counts.items(),
                                 key=lambda x: -x[1])),
        "total_code_files": total_code_files,
        "dependency_manifests": sorted(set(dep_found)),
        "ml_frameworks": frameworks,
        "entry_points": sorted(set(entry_points))[:25],
        "config_files": sorted(set(config_files))[:25],
        "data_dirs": sorted(data_dirs)[:25],
        "model_artifact_dirs": sorted(model_dirs)[:25],
        "test_dirs": sorted(test_dirs)[:25],
    }

    if args.out:
        with open(args.out, "w") as f:
            json.dump(summary, f, indent=2)

    # Compact human-readable summary to stdout.
    g = summary["graphify"]
    print("=== recon summary ===")
    print(f"root: {root}")
    print(f"graphify graph: {g[0] if g else 'NOT FOUND (consider running graphify)'}")
    print(f"languages: {summary['languages'] or 'none detected'}")
    print(f"code files: {total_code_files}")
    if frameworks:
        print("ml stack:")
        for cat, items in frameworks.items():
            print(f"  - {cat}: {', '.join(items)}")
    else:
        print("ml stack: none detected from manifests")
    print(f"entry points: {summary['entry_points'] or 'none obvious'}")
    print(f"config: {summary['config_files'] or 'none obvious'}")
    print(f"data dirs: {summary['data_dirs'] or 'none'}")
    print(f"model/artifact dirs: {summary['model_artifact_dirs'] or 'none'}")
    print(f"test dirs: {summary['test_dirs'] or 'none'}")
    if args.out:
        print(f"\nfull JSON: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
