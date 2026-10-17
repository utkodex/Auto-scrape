"""The eight LLM-facing tools: OpenAI function schemas plus the dispatcher that enforces the rules.

Contract: .claude/specs/02.architecture.md §7.3.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agent.steps_file import EditError, LockedStepError, StepNameError, StepsFile
from core.browser import REPO_ROOT, Browser
from core.checks import lint, sandbox_check
from core.log import ActionLog
from core.observe import ObserveResult, approx_tokens, observe
from core.profile import Profile

MAX_RESULT_CHARS = 1200
MAX_READ_LINES = 80


@dataclass
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)

    def short(self) -> str:
        a = self.args or {}
        if self.name == "add_step":
            return str(a.get("name", ""))
        if self.name == "edit_file":
            return f'{a.get("old", "")[:60]} → {a.get("new", "")[:60]}'
        if self.name in ("note", "ask_human"):
            return str(a.get("text") or a.get("question") or "")[:120]
        if self.name == "read_file":
            return str(a.get("path", ""))
        if self.name == "observe":
            return f'scope={a.get("scope")}' if a.get("scope") else "page"
        if self.name == "run_step":
            return str(a.get("name", ""))
        return json.dumps(a)[:120]


TOOL_SCHEMAS: list[dict] = [
    {"type": "function", "function": {
        "name": "observe",
        "description": "Look at the current page: fields, labels, buttons, options preview, suggested next action. Optional scope narrows to one label or section.",
        "parameters": {"type": "object", "properties": {"scope": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read up to 80 lines of the current steps.py, the worked example, or the helper signatures.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "add_step",
        "description": "Append ONE new step to steps.py. name = step_NN_<slug> with the next NN. code = the function body only (helper calls), no def line.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "code": {"type": "string"}},
                       "required": ["name", "code"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Fix the failing step: replace exactly one occurrence of `old` (text inside the unlocked step) with `new`.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
            "required": ["path", "old", "new"]}}},
    {"type": "function", "function": {
        "name": "run_step",
        "description": "Run the newest step in the live browser from the current page and check it.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "run_checks",
        "description": "Full test: lint, sandbox rules, then every step from the job link on every sample job. Returns only the first failure.",
        "parameters": {"type": "object", "properties": {"company": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {
        "name": "note",
        "description": "One short line explaining why you are taking the next step (max 200 chars).",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "ask_human",
        "description": "Stop and ask the human (login, CAPTCHA, or when stuck). Blocks until answered.",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}}},
]
TOOL_NAMES = [t["function"]["name"] for t in TOOL_SCHEMAS]


@dataclass
class ToolContext:
    browser: Browser
    steps_file: StepsFile
    profile: Profile
    log: ActionLog
    company: str
    filler_dir: Path
    run_dir: Path
    example_path: Path | None = None
    mode: str = "build"
    cap_tokens: int = 1500
    run_step_fn: Callable[[str], dict] | None = None
    run_checks_fn: Callable[[], dict] | None = None
    ask_human_fn: Callable[[str], str] | None = None
    round: int = 0
    last_notes: list[str] = field(default_factory=list)
    mutations_this_round: int = 0
    last_observe: ObserveResult | None = None


def trim_result(name: str, result: dict, max_chars: int = MAX_RESULT_CHARS) -> dict:
    if name == "observe":
        return result
    text = json.dumps(result, ensure_ascii=False)
    if len(text) <= max_chars:
        return result
    out = dict(result)
    for key in ("error", "text", "answer", "result"):
        if isinstance(out.get(key), str) and len(out[key]) > 400:
            lines = out[key].splitlines()
            out[key] = "\n".join(lines[-20:])[-600:]
    text = json.dumps(out, ensure_ascii=False)
    if len(text) > max_chars:
        for key in list(out):
            if isinstance(out[key], str) and len(out[key]) > 200:
                out[key] = out[key][:200] + "…"
    return out


class Dispatcher:
    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    # -- round bookkeeping --------------------------------------------------------------
    def new_round(self) -> int:
        self.ctx.round += 1
        self.ctx.mutations_this_round = 0
        return self.ctx.round

    # -- entry ------------------------------------------------------------------------------
    def call(
        self,
        tc: ToolCall,
        usage: tuple[int | None, int | None, float | None] | None = None,
        harness: bool = False,
    ) -> dict:
        ctx = self.ctx
        actor = "harness" if harness else "qwen"
        handler = getattr(self, f"_t_{tc.name}", None)
        tok_in, tok_out, secs = usage or (None, None, None)
        if handler is None:
            res = {"ok": False, "error": f"unknown tool '{tc.name}'; use one of {', '.join(TOOL_NAMES)}"}
            ctx.log.row(actor, tc.name or "?", tc.short(), res["error"], tok_in=tok_in, tok_out=tok_out, s=secs, round=ctx.round)
            return res
        try:
            res = handler(**(tc.args or {}))
        except TypeError as exc:
            res = {"ok": False, "error": f"{tc.name}: bad arguments ({str(exc).split(')')[-1].strip()[:120]})"}
        except (StepNameError, LockedStepError, EditError) as exc:
            res = {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "error": f"{tc.name}: {str(exc).splitlines()[0][:160]}"}
        res = trim_result(tc.name, res)
        extra = {"tokens": approx_tokens(json.dumps(res))} if tc.name == "observe" else {}
        if tc.name not in ("note", "ask_human"):
            summary = _summarise(tc.name, res)
            ctx.log.row(actor, tc.name, tc.short(), summary, tok_in=tok_in, tok_out=tok_out, s=secs, round=ctx.round, **extra)
        elif usage:
            ctx.log.row(actor, tc.name, tc.short(), "", tok_in=tok_in, tok_out=tok_out, s=secs, round=ctx.round)
        return res

    # -- tools ------------------------------------------------------------------------------
    def _t_observe(self, scope: str | None = None) -> dict:
        ctx = self.ctx
        result = observe(ctx.browser, scope=scope or None, cache_dir=ctx.run_dir / "probe", cap_tokens=ctx.cap_tokens,
                         mask_values=ctx.profile.mask_values())
        ctx.last_observe = result
        d = result.model_dump(exclude_none=True)
        d["ok"] = True
        return d

    def _allowed_read(self, path: str) -> tuple[Path | None, str | None]:
        p = Path(path)
        if not p.is_absolute():
            p = (REPO_ROOT / p).resolve()
        else:
            p = p.resolve()
        filler = self.ctx.filler_dir.resolve()
        helpers = (REPO_ROOT / "core" / "helpers.py").resolve()
        example = (REPO_ROOT / "agent" / "prompts" / "example_steps.py").resolve()
        allowed = [helpers, example]
        if self.ctx.example_path:
            allowed.append(Path(self.ctx.example_path).resolve())
        if p == helpers:
            return p, "signatures"
        if p in allowed:
            return p, None
        try:
            p.relative_to(filler)
            return p, None
        except ValueError:
            return None, None

    def _t_read_file(self, path: str, start: int | None = None, end: int | None = None) -> dict:
        p, mode = self._allowed_read(path)
        if p is None:
            return {"ok": False, "error": f"read_file: '{path}' is outside the allowed files (your filler folder, the worked example, core/helpers.py)"}
        if not p.exists():
            return {"ok": False, "error": f"read_file: {path} does not exist"}
        lines = p.read_text(encoding="utf-8").splitlines()
        if mode == "signatures":
            sig: list[str] = []
            for i, ln in enumerate(lines):
                if ln.startswith("def ") and not ln.startswith("def _"):
                    doc = lines[i + 1].strip() if i + 1 < len(lines) and lines[i + 1].strip().startswith('"""') else ""
                    sig.append(ln.rstrip(":") + ("  # " + doc.strip('"') if doc else ""))
            text = "\n".join(sig)
            return {"ok": True, "path": "core/helpers.py", "lines": f"1-{len(sig)}", "text": text}
        s = max(1, int(start or 1))
        e = min(len(lines), int(end or (s + MAX_READ_LINES - 1)), s + MAX_READ_LINES - 1)
        chunk = lines[s - 1: e]
        return {"ok": True, "path": str(p.relative_to(REPO_ROOT)) if _under(p, REPO_ROOT) else p.name,
                "lines": f"{s}-{e}", "text": "\n".join(chunk)}

    def _check_mutation_budget(self, tool: str) -> dict | None:
        if self.ctx.mutations_this_round >= 1:
            return {"ok": False, "error": f"{tool}: only one add_step or edit_file per round; wait for the result first"}
        return None

    def _t_add_step(self, name: str, code: str) -> dict:
        err = self._check_mutation_budget("add_step")
        if err:
            return err
        sf = self.ctx.steps_file
        info = sf.add(name, code)
        self.ctx.mutations_this_round += 1
        problem = lint(sf.path) or sandbox_check(sf.path)
        if problem:
            sf.remove_last(name)
            return {"ok": False, "error": f"rejected: {problem}"}
        return {"ok": True, "step": info.name, "index": info.index, "lint": "ok"}

    def _t_edit_file(self, path: str, old: str, new: str) -> dict:
        err = self._check_mutation_budget("edit_file")
        if err:
            return err
        sf = self.ctx.steps_file
        want = sf.path.resolve()
        given = Path(path)
        if not (given.name == "steps.py" and (not given.is_absolute() or given.resolve() == want)):
            return {"ok": False, "error": f"edit_file: only {want.name} of the current filler may be edited"}
        if not new:
            return {"ok": False, "error": "edit_file: new text must not be empty"}
        info = sf.edit(old, new)
        self.ctx.mutations_this_round += 1
        problem = lint(sf.path) or sandbox_check(sf.path)
        if problem:
            sf.undo_last_edit()
            return {"ok": False, "error": f"rejected: {problem}"}
        return {"ok": True, "step": info.name, "lint": "ok"}

    def _t_run_step(self, name: str) -> dict:
        if not self.ctx.run_step_fn:
            return {"ok": False, "error": "run_step is not available"}
        return self.ctx.run_step_fn(name)

    def _t_run_checks(self, company: str | None = None) -> dict:
        if not self.ctx.run_checks_fn:
            return {"ok": False, "error": "run_checks is not available"}
        return self.ctx.run_checks_fn()

    def _t_note(self, text: str) -> dict:
        text = (text or "").strip()[:200]
        self.ctx.last_notes.append(text)
        self.ctx.last_notes = self.ctx.last_notes[-3:]
        self.ctx.log.row("qwen", "note", text, "", round=self.ctx.round)
        return {"ok": True}

    def _t_ask_human(self, question: str) -> dict:
        question = (question or "").strip()[:300]
        if not self.ctx.ask_human_fn:
            return {"ok": False, "error": "no human is available in this run"}
        import time

        t0 = time.monotonic()
        answer = self.ctx.ask_human_fn(question) or ""
        self.ctx.log.row("human", "ask_human", question, answer[:200], s=time.monotonic() - t0, round=self.ctx.round)
        return {"ok": True, "answer": answer}


def _under(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _summarise(name: str, res: dict) -> str:
    if not res.get("ok", True) and res.get("error"):
        return f"error · {res['error']}"
    if name == "observe":
        sn = res.get("suggested_next", {})
        return f"{res.get('page_kind')} · {len(res.get('fields', []))} fields · next={sn.get('action')} {sn.get('targets', [])} · ~{approx_tokens(json.dumps(res))} tok"
    if name == "add_step":
        return f"ok · {res.get('step')} added"
    if name == "edit_file":
        return f"ok · {res.get('step')} edited"
    if name == "run_step":
        return f"{res.get('status')} · {(res.get('error') or '').splitlines()[0] if res.get('error') else ''}".strip(" ·")
    if name == "run_checks":
        return f"{res.get('status')} {res.get('passed')}/{res.get('total')}" + (f" · {res.get('error')}" if res.get("error") else "")
    if name == "read_file":
        return f"{res.get('path')} {res.get('lines')}"
    return re.sub(r"\s+", " ", json.dumps(res)[:120])
