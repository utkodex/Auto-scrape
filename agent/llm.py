"""OpenAI-compatible LLM client: byte-stable prefix + small task block → one ToolCall.

Contracts: .claude/specs/03.build-loop-and-eval.md §6.2–6.3. The only module that talks to the server.
"""

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agent.config import Settings
from agent.tools import TOOL_NAMES, TOOL_SCHEMAS, ToolCall
from core.observe import ObserveResult, _trim, approx_tokens

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
JSON_MODE_FOOTER = (
    "\n\n## Output format\nYou cannot call tools directly. Reply with ONLY one JSON object inside a ```json fence: "
    '{"tool": "<tool name>", "args": {...}}'
)
FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


class NoToolCall(Exception):
    pass


class LLMUnavailable(Exception):
    """The model server did not answer (not running, wrong LLM_BASE_URL, or timed out)."""


@dataclass
class TaskBlock:
    kind: Literal["add", "fix"]
    observe: ObserveResult
    done_steps: list[str] = field(default_factory=list)
    profile_keys: list[str] = field(default_factory=list)
    last_notes: list[str] = field(default_factory=list)
    last_error: str | None = None
    fix_step: str | None = None
    fix_source: str | None = None
    instruction: str = ""
    similar_step: str | None = None
    last_tool_result: dict | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "task": self.kind,
            "instruction": self.instruction,
            "page": self.observe.model_dump(exclude_none=True),
            "done_steps": self.done_steps,
            "profile_keys": self.profile_keys,
        }
        if self.last_notes:
            d["last_notes"] = self.last_notes
        if self.last_error:
            d["last_error"] = self.last_error
        if self.kind == "fix":
            d["fix_step"] = self.fix_step
            d["fix_source"] = self.fix_source
        if self.similar_step:
            d["similar_step"] = self.similar_step
        if self.last_tool_result:
            d["last_tool_result"] = self.last_tool_result
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def parse_text(text: str) -> ToolCall:
    """Fallback parser: a fenced JSON block {"tool":..,"args":..} (or {"name":..,"arguments":..})."""
    if not text:
        raise NoToolCall("empty response")
    candidates = FENCE_RE.findall(text)
    if not candidates:
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            candidates = [stripped]
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        name = obj.get("tool") or obj.get("name")
        args = obj.get("args") if "args" in obj else obj.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if name in TOOL_NAMES:
            return ToolCall(name=name, args=args or {})
    raise NoToolCall("no tool call found in the response")


class LLM:
    def __init__(self, settings: Settings, example_path: Path | str | None = None, dump_dir: Path | None = None) -> None:
        self.settings = settings
        self.example_path = Path(example_path) if example_path else PROMPTS_DIR / "example_steps.py"
        self.dump_dir = dump_dir
        self.supports_tools = not settings.LLM_FORCE_JSON_MODE
        self.last_usage: tuple[int, int, bool] = (0, 0, True)
        self.last_raw: str = ""
        self.calls = 0
        self._client = None
        base = (PROMPTS_DIR / "system.md").read_text(encoding="utf-8").rstrip()
        example = self.example_path.read_text(encoding="utf-8").rstrip() if self.example_path.exists() else ""
        self._system_base = base + "\n\n# Worked example (a filler that passed on a similar form)\n```python\n" + example + "\n```"
        self.system_content = self._system_base + ("" if self.supports_tools else JSON_MODE_FOOTER)

    # -- prompt assembly -----------------------------------------------------------------
    def trim(self, task: TaskBlock) -> TaskBlock:
        cap = self.settings.PROMPT_TOKEN_CAP
        t = copy.deepcopy(task)
        base = approx_tokens(self.system_content)

        def total() -> int:
            return base + approx_tokens(t.to_json())

        if total() <= cap:
            return t
        remaining = max(400, cap - base - approx_tokens(json.dumps({k: v for k, v in t.to_dict().items() if k != "page"})))
        t.observe = _trim(copy.deepcopy(t.observe), remaining)
        if total() <= cap:
            return t
        t.last_notes = []
        if total() <= cap and t.fix_source:
            return t
        if t.fix_source and len(t.fix_source) > 600:
            t.fix_source = t.fix_source[-600:]
        if t.similar_step and total() > cap:
            t.similar_step = None
        if t.last_tool_result and total() > cap:
            t.last_tool_result = None
        while total() > cap and len(t.observe.fields) > 2:
            t.observe.fields.pop()
            t.observe.truncated = True
        return t

    def build_messages(self, task: TaskBlock) -> list[dict]:
        t = self.trim(task)
        return [
            {"role": "system", "content": self.system_content},
            {"role": "user", "content": t.to_json()},
        ]

    # -- server ----------------------------------------------------------------------------
    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=self.settings.LLM_BASE_URL, api_key="local", timeout=180.0, max_retries=1)
        return self._client

    def _request(self, messages: list[dict], use_tools: bool, tool_choice: str):
        kwargs = dict(
            model=self.settings.LLM_MODEL,
            messages=messages,
            temperature=self.settings.LLM_TEMPERATURE,
            max_tokens=self.settings.LLM_MAX_OUTPUT_TOKENS,
        )
        try:
            kwargs["seed"] = 7
        except Exception:
            pass
        if use_tools:
            kwargs["tools"] = TOOL_SCHEMAS
            kwargs["tool_choice"] = tool_choice
        from openai import APIConnectionError, APITimeoutError

        try:
            return self._get_client().chat.completions.create(**kwargs)
        except (APIConnectionError, APITimeoutError) as exc:
            raise LLMUnavailable(
                f"no model server at LLM_BASE_URL={self.settings.LLM_BASE_URL} ({exc}). "
                "Start the OpenAI-compatible server (vLLM / llama.cpp / Ollama / LM Studio) or fix .env; "
                "check with: curl <LLM_BASE_URL>/models"
            ) from exc

    def call(self, task: TaskBlock) -> ToolCall:
        from openai import BadRequestError, UnprocessableEntityError

        messages = self.build_messages(task)
        self.calls += 1
        t0 = time.monotonic()
        response = None
        if self.supports_tools:
            try:
                response = self._request(messages, True, "required")
            except (BadRequestError, UnprocessableEntityError):
                try:
                    response = self._request(messages, True, "auto")
                except (BadRequestError, UnprocessableEntityError):
                    self.supports_tools = False
                    self.system_content = self._system_base + JSON_MODE_FOOTER
                    messages = self.build_messages(task)
        if response is None:
            response = self._request(messages, False, "none")
        secs = time.monotonic() - t0
        if self.dump_dir:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
            (self.dump_dir / f"round_{self.calls:03d}.json").write_text(
                json.dumps({"messages": messages, "response": _safe_dump(response)}, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        usage = getattr(response, "usage", None)
        if usage and getattr(usage, "prompt_tokens", None) is not None:
            self.last_usage = (int(usage.prompt_tokens), int(usage.completion_tokens or 0), False)
        else:
            self.last_usage = (approx_tokens("".join(m["content"] for m in messages)), approx_tokens(self.last_raw), True)
        self.last_seconds = secs
        return self._parse_response(response.choices[0].message)

    def _parse_response(self, message) -> ToolCall:
        tool_calls = getattr(message, "tool_calls", None) or []
        content = getattr(message, "content", None) or ""
        self.last_raw = content
        if tool_calls:
            tc = tool_calls[0]
            fn = getattr(tc, "function", tc)
            name = getattr(fn, "name", None)
            raw_args = getattr(fn, "arguments", "{}") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                raise NoToolCall(f"tool call '{name}' had malformed JSON arguments") from None
            if name not in TOOL_NAMES:
                raise NoToolCall(f"unknown tool '{name}'")
            self.last_raw = json.dumps({"tool": name, "args": args})
            return ToolCall(name=name, args=args or {})
        return parse_text(content)


def _safe_dump(response) -> dict | str:
    try:
        return response.model_dump()
    except Exception:
        return str(response)
