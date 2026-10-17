"""Append-only manager for fillers/<company>/steps.py with locking, undo and tamper detection.

Contracts: .claude/specs/02.architecture.md §7.3 (add_step / edit_file rules), §7.7 (package),
.claude/specs/03.build-loop-and-eval.md §6.4 (invariants: at most one unlocked step).
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

HEADER = "from core.helpers import check, click, fill, pick, upload, wait_for\n"
STEP_NAME_RE = re.compile(r"^step_(\d{2})_([a-z0-9_]{1,30})$")


class StepNameError(Exception):
    pass


class LockedStepError(Exception):
    pass


class EditError(Exception):
    pass


class TamperError(Exception):
    pass


@dataclass
class StepInfo:
    name: str
    index: int
    source: str
    locked: bool
    lineno: int = 0
    end_lineno: int = 0


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class StepsFile:
    def __init__(self, path: Path | str, lock_path: Path | str | None = None) -> None:
        self.path = Path(path)
        self.lock_path = Path(lock_path) if lock_path else self.path.with_name("steps.lock.json")
        self._edit_history: list[tuple[str, str]] = []  # (step name, text before the edit)

    # -- io -------------------------------------------------------------------------
    def read(self) -> str:
        return self.path.read_text(encoding="utf-8") if self.path.exists() else ""

    def _write(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")

    def _lock_data(self) -> dict:
        if not self.lock_path.exists():
            return {}
        try:
            return json.loads(self.lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save_lock(self, data: dict) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # -- parsing --------------------------------------------------------------------
    def steps(self) -> list[StepInfo]:
        text = self.read()
        if not text.strip():
            return []
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            raise EditError(f"steps.py does not parse: {exc.msg} (line {exc.lineno})") from None
        lines = text.splitlines(keepends=True)
        lock = self._lock_data()
        out: list[StepInfo] = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                m = STEP_NAME_RE.match(node.name)
                if not m:
                    continue
                source = "".join(lines[node.lineno - 1: node.end_lineno])
                locked = lock.get(node.name, {}).get("status") == "locked"
                out.append(StepInfo(node.name, int(m.group(1)), source, locked, node.lineno, node.end_lineno))
        return out

    def names(self) -> list[str]:
        return [s.name for s in self.steps()]

    def source_of(self, name: str) -> str:
        for s in self.steps():
            if s.name == name:
                return s.source
        raise StepNameError(f"unknown step {name}")

    def unlocked(self) -> str | None:
        for s in self.steps():
            if not s.locked:
                return s.name
        return None

    def locked_names(self) -> list[str]:
        return [s.name for s in self.steps() if s.locked]

    # -- mutation -------------------------------------------------------------------
    @staticmethod
    def _normalise_body(body: str) -> str:
        lines = [ln.rstrip() for ln in textwrap.dedent(body or "").strip("\n").splitlines()]
        kept = []
        for ln in lines:
            s = ln.strip()
            if re.match(r"^def\s+step_", s) or s.startswith("from core.helpers import"):
                continue
            kept.append(ln)
        text = textwrap.dedent("\n".join(kept)).strip("\n")
        if not text.strip():
            raise EditError("step body is empty")
        return textwrap.indent(text, "    ") + "\n"

    def add(self, name: str, body: str) -> StepInfo:
        m = STEP_NAME_RE.match(name or "")
        if not m:
            raise StepNameError(f"bad step name '{name}': expected step_NN_<slug> (slug: [a-z0-9_]{{1,30}})")
        existing = self.steps()
        expected = len(existing) + 1
        if int(m.group(1)) != expected:
            raise StepNameError(f"next step index must be {expected:02d}, got {m.group(1)}")
        if any(s.name == name for s in existing):
            raise StepNameError(f"step {name} already exists")
        pending = self.unlocked()
        if pending is not None:
            raise LockedStepError(f"{pending} is not locked yet; fix or lock it before adding a new step")
        body_text = self._normalise_body(body)
        text = self.read()
        if not text.strip():
            text = HEADER + "\n"
        if not text.endswith("\n"):
            text += "\n"
        text += f"\ndef {name}(page, p):\n{body_text}"
        try:
            ast.parse(text)
        except SyntaxError as exc:
            raise EditError(f"step body does not parse: {exc.msg} (line {exc.lineno})") from None
        self._write(text)
        lock = self._lock_data()
        lock[name] = {"sha1": sha1(self.source_of(name)), "status": "unlocked"}
        self._save_lock(lock)
        return next(s for s in self.steps() if s.name == name)

    def edit(self, old: str, new: str) -> StepInfo:
        if not old:
            raise EditError("old text must not be empty")
        text = self.read()
        count = text.count(old)
        if count == 0:
            raise EditError("old text not found in steps.py")
        if count > 1:
            raise EditError(f"old text occurs {count} times; make it unique")
        start = text.index(old)
        end = start + len(old)
        line_start = text.count("\n", 0, start) + 1
        line_end = text.count("\n", 0, max(end - 1, start)) + 1
        target = None
        for s in self.steps():
            if s.lineno <= line_start and line_end <= s.end_lineno:
                target = s
                break
        if target is None:
            raise EditError("old text must lie entirely inside one step function")
        if target.locked:
            raise LockedStepError(f"{target.name} is locked")
        if self.unlocked() != target.name:
            raise LockedStepError(f"only the unlocked step may be edited (unlocked: {self.unlocked()})")
        new_text = text[:start] + new + text[end:]
        try:
            ast.parse(new_text)
        except SyntaxError as exc:
            raise EditError(f"edit does not parse: {exc.msg} (line {exc.lineno})") from None
        self._edit_history.append((target.name, text))
        self._write(new_text)
        lock = self._lock_data()
        lock[target.name] = {"sha1": sha1(self.source_of(target.name)), "status": "unlocked"}
        self._save_lock(lock)
        return next(s for s in self.steps() if s.name == target.name)

    def undo_last_edit(self) -> StepInfo | None:
        if not self._edit_history:
            return None
        name, before = self._edit_history.pop()
        self._write(before)
        lock = self._lock_data()
        if name in lock:
            lock[name]["sha1"] = sha1(self.source_of(name))
        self._save_lock(lock)
        return next((s for s in self.steps() if s.name == name), None)

    def remove_last(self, name: str) -> None:
        """Drop a just-added step that failed lint/sandbox (never a locked one)."""
        steps = self.steps()
        if not steps or steps[-1].name != name:
            raise EditError(f"{name} is not the last step")
        if steps[-1].locked:
            raise LockedStepError(f"{name} is locked")
        text = self.read()
        lines = text.splitlines(keepends=True)
        kept = "".join(lines[: steps[-1].lineno - 1]).rstrip("\n") + "\n"
        self._write(kept)
        lock = self._lock_data()
        lock.pop(name, None)
        self._save_lock(lock)

    # -- locking ---------------------------------------------------------------------
    def lock(self, name: str) -> None:
        lock = self._lock_data()
        lock[name] = {"sha1": sha1(self.source_of(name)), "status": "locked"}
        self._save_lock(lock)

    def unlock(self, name: str) -> None:
        pending = self.unlocked()
        if pending is not None and pending != name:
            raise LockedStepError(f"{pending} is already unlocked; only one step may be unlocked")
        self.source_of(name)
        lock = self._lock_data()
        lock[name] = {"sha1": lock.get(name, {}).get("sha1", sha1(self.source_of(name))), "status": "unlocked"}
        self._save_lock(lock)

    def verify_lock_hashes(self) -> None:
        lock = self._lock_data()
        for s in self.steps():
            rec = lock.get(s.name)
            if rec and rec.get("status") == "locked" and rec.get("sha1") != sha1(s.source):
                raise TamperError(f"{s.name} was modified outside the harness")
