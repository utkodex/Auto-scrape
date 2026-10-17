"""The three judges every step must pass: expectation verification, lint, sandbox.

Contracts: .claude/specs/02.architecture.md §7.5.2 (verified by), §7.8 (sandbox rules).
"""

from __future__ import annotations

import ast
import py_compile
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page

from core.browser import SUBMIT_RE
from core.helpers import (
    EXPECTATION_HELPERS,
    HELPER_NAMES,
    Checked,
    FileAttached,
    HelperError,
    NewFields,
    OptionSelected,
    PageChanged,
    PageOrFieldsChanged,
    ValueEquals,
    locate,
    norm,
)
from core.observe import OBSERVE_JS

STEP_NAME_RE = re.compile(r"^step_(\d{2})_([a-z0-9_]{1,30})$")
FORBIDDEN_NAMES = {"eval", "exec", "open", "__import__", "compile", "globals", "locals", "getattr", "setattr"}
ALLOWED_KEYWORDS = {"expect", "timeout_s"}


@dataclass
class CheckFailure:
    expectation: str
    error: str
    element_found: bool = True


def _labels(page: Page) -> list[str]:
    try:
        return [f["label"] for f in page.evaluate(OBSERVE_JS)["fields"]]
    except Exception:
        return []


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _verify_one(page: Page, exp) -> CheckFailure | None:
    name = repr(exp)
    try:
        if isinstance(exp, PageChanged):
            if page.url.split("#")[0] == exp.prev_url.split("#")[0]:
                return CheckFailure(name, f"page did not change (still {page.url})")
            return None
        if isinstance(exp, NewFields):
            new = [lbl for lbl in _labels(page) if lbl not in exp.prev_labels]
            if not new:
                return CheckFailure(name, "no new fields appeared after the click")
            return None
        if isinstance(exp, PageOrFieldsChanged):
            if page.url.split("#")[0] != exp.prev_url.split("#")[0]:
                return None
            if any(lbl not in exp.prev_labels for lbl in _labels(page)):
                return None
            return CheckFailure(name, "neither the page nor its fields changed after the click")
        if isinstance(exp, ValueEquals):
            loc = locate(page, exp.label, "text", timeout_s=2)
            actual = loc.input_value()
            if _ws(actual) != _ws(exp.value):
                return CheckFailure(name, f"{exp.label}: expected '{exp.value}', found '{actual}'")
            return None
        if isinstance(exp, OptionSelected):
            loc = locate(page, exp.label, "select", timeout_s=2)
            tag = loc.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                actual = loc.evaluate("el => el.selectedIndex >= 0 ? el.options[el.selectedIndex].textContent : ''")
            else:
                actual = loc.input_value()
            if norm(actual) != norm(exp.option) and norm(exp.option) not in norm(actual):
                return CheckFailure(name, f"{exp.label}: expected '{exp.option}' selected, shows '{_ws(actual)}'")
            return None
        if isinstance(exp, FileAttached):
            loc = locate(page, exp.label, "file", timeout_s=2)
            fname = loc.evaluate("el => (el.files && el.files.length) ? el.files[0].name : ''")
            if fname == exp.basename or page.get_by_text(exp.basename, exact=False).count() > 0:
                return None
            return CheckFailure(name, f"{exp.label}: file '{exp.basename}' is not attached")
        if isinstance(exp, Checked):
            group = page.get_by_role("group", name=exp.label)
            if group.count() == 0:
                group = page.get_by_role("radiogroup", name=exp.label)
            if group.count() >= 1:
                radio = group.first.get_by_role("radio", name=exp.option, exact=True)
                if radio.count() == 0:
                    radio = group.first.get_by_label(exp.option)
                if radio.count() and radio.first.is_checked():
                    return None
                return CheckFailure(name, f"{exp.label}: option '{exp.option}' is not selected")
            loc = locate(page, exp.label, "checkbox", timeout_s=2)
            want = norm(exp.option) in ("yes", "true", "1", "on", "checked") or norm(exp.option) == norm(exp.label)
            if loc.is_checked() == want:
                return None
            return CheckFailure(name, f"{exp.label}: checkbox is {'not ' if want else ''}checked")
        return CheckFailure(name, f"unknown expectation {type(exp).__name__}")
    except HelperError as exc:
        return CheckFailure(name, str(exc), element_found=exc.element_found)
    except Exception as exc:  # noqa: BLE001
        return CheckFailure(name, str(exc).splitlines()[0][:200])


def verify_expectations(page: Page, expectations: list) -> list[CheckFailure]:
    """Verify in order; stop at the first failure (empty list means PASS)."""
    for exp in expectations:
        failure = _verify_one(page, exp)
        if failure:
            return [failure]
    return []


# -- lint ----------------------------------------------------------------------------
def lint(path: str | Path) -> str | None:
    p = Path(path)
    try:
        proc = subprocess.run(
            # F401: the steps.py header imports all six helpers; unused ones are expected.
            # I001: import ordering is a style rule, not a correctness rule, for generated files.
            [sys.executable, "-m", "ruff", "check", "--quiet", "--no-cache", "--ignore", "F401,I001", str(p)],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            out = (proc.stdout or proc.stderr).strip().splitlines()
            return out[0] if out else "ruff reported errors"
    except Exception as exc:  # noqa: BLE001
        return f"ruff failed: {exc}"
    try:
        py_compile.compile(str(p), doraise=True)
    except py_compile.PyCompileError as exc:
        return str(exc).splitlines()[0]
    return None


# -- sandbox -------------------------------------------------------------------------
def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _check_value(node: ast.expr, line: int) -> str | None:
    """Allowed: str constants, f-strings, p.<attr>, p.answers["k"], + of those, the name `page`."""
    if isinstance(node, ast.Constant):
        return None if isinstance(node.value, (str, int, float)) else f"constant {node.value!r} is not allowed (line {line})"
    if isinstance(node, ast.JoinedStr):
        for v in node.values:
            if isinstance(v, ast.FormattedValue):
                err = _check_value(v.value, line)
                if err:
                    return err
        return None
    if isinstance(node, ast.Name):
        if node.id in ("page", "p"):
            return None
        if node.id in FORBIDDEN_NAMES:
            return f"forbidden name '{node.id}' (line {line})"
        return f"name '{node.id}' is not allowed (line {line})"
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == "page":
            return f"page.{node.attr} is not allowed; use the helpers (line {line})"
        if isinstance(node.value, ast.Name) and node.value.id == "p":
            return None
        return f"attribute access '{ast.unparse(node)}' is not allowed (line {line})"
    if isinstance(node, ast.Subscript):
        if (isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "p" and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            return None
        return f"subscript '{ast.unparse(node)}' is not allowed (line {line})"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _check_value(node.left, line) or _check_value(node.right, line)
    if isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return f"{type(node).__name__} is not allowed (line {line})"
    if isinstance(node, ast.Call):
        return f"nested call '{ast.unparse(node)[:40]}' is not allowed (line {line})"
    return f"expression '{ast.unparse(node)[:40]}' is not allowed (line {line})"


def sandbox_check(path: str | Path) -> str | None:
    src = Path(path).read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return f"syntax error: {exc.msg} (line {exc.lineno})"

    indices: list[int] = []
    for node in tree.body:
        if _is_docstring(node):
            continue
        if isinstance(node, ast.Import):
            return f"import of '{node.names[0].name}' is not allowed (line {node.lineno})"
        if isinstance(node, ast.ImportFrom):
            if node.module != "core.helpers":
                return f"import from '{node.module}' is not allowed (line {node.lineno})"
            bad = [a.name for a in node.names if a.name not in HELPER_NAMES or a.asname]
            if bad:
                return f"import of '{bad[0]}' is not allowed (line {node.lineno})"
            continue
        if not isinstance(node, ast.FunctionDef):
            return f"top-level {type(node).__name__} is not allowed (line {node.lineno})"
        m = STEP_NAME_RE.match(node.name)
        if not m:
            return f"bad step name '{node.name}' (line {node.lineno})"
        indices.append(int(m.group(1)))
        args = [a.arg for a in node.args.args]
        if args != ["page", "p"] or node.args.kwonlyargs or node.args.vararg or node.args.kwarg or node.decorator_list:
            return f"{node.name}: signature must be (page, p) (line {node.lineno})"
        has_expectation = False
        for stmt in node.body:
            if _is_docstring(stmt):
                continue
            if isinstance(stmt, ast.Pass):
                continue
            if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
                kw = {ast.If: "if", ast.For: "for", ast.While: "while", ast.Try: "try", ast.With: "with",
                      ast.Assign: "assignment", ast.AugAssign: "assignment", ast.Return: "return",
                      ast.Raise: "raise", ast.Assert: "assert"}.get(type(stmt), type(stmt).__name__)
                return f"'{kw}' is not allowed inside a step (line {stmt.lineno})"
            call = stmt.value
            if not isinstance(call.func, ast.Name):
                if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id == "page":
                    return f"page.{call.func.attr} is not allowed; use the helpers (line {stmt.lineno})"
                return f"call '{ast.unparse(call.func)[:40]}' is not allowed (line {stmt.lineno})"
            fname = call.func.id
            if fname not in HELPER_NAMES:
                return f"call to '{fname}' is not allowed (line {stmt.lineno})"
            if not call.args or not (isinstance(call.args[0], ast.Name) and call.args[0].id == "page"):
                return f"{fname}: first argument must be page (line {stmt.lineno})"
            for a in call.args[1:]:
                err = _check_value(a, stmt.lineno)
                if err:
                    return err
            for k in call.keywords:
                if k.arg not in ALLOWED_KEYWORDS:
                    return f"{fname}: keyword '{k.arg}' is not allowed (line {stmt.lineno})"
                err = _check_value(k.value, stmt.lineno)
                if err:
                    return err
            if fname == "click" and len(call.args) >= 2 and isinstance(call.args[1], ast.Constant) \
                    and isinstance(call.args[1].value, str) and SUBMIT_RE.match(call.args[1].value):
                return f"submit is not allowed (line {stmt.lineno})"
            if fname in EXPECTATION_HELPERS:
                has_expectation = True
        if not has_expectation:
            return f"{node.name}: no expectation — a step must call at least one of {', '.join(EXPECTATION_HELPERS)}"

    for i, idx in enumerate(indices, start=1):
        if idx != i:
            if idx in indices[: i - 1]:
                return f"duplicate step index {idx:02d}"
            return f"gap in step numbering: expected {i:02d}, found {idx:02d}"
    return None
