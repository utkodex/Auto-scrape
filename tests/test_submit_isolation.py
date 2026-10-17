import ast
from pathlib import Path

import pytest

from core.helpers import ForbiddenAction
from core.submit import submit

REPO = Path(__file__).resolve().parent.parent


def _imports_submit(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "core.submit" in node.module:
            return True
        if isinstance(node, ast.Import) and any("core.submit" in a.name for a in node.names):
            return True
    return False


@pytest.mark.safety
def test_build_path_never_imports_submit():
    offenders = []
    for folder in ("agent", "core"):
        for py in (REPO / folder).rglob("*.py"):
            if py.name == "submit.py":
                continue
            if _imports_submit(py):
                offenders.append(str(py.relative_to(REPO)))
    assert offenders == []


@pytest.mark.safety
def test_submit_raises_in_build_mode(monkeypatch):
    monkeypatch.setenv("MODE", "build")
    with pytest.raises(ForbiddenAction):
        submit(None, "HUMAN-CONFIRMED")
    monkeypatch.setenv("MODE", "test")
    with pytest.raises(ForbiddenAction):
        submit(None, "HUMAN-CONFIRMED")
