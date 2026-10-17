from types import SimpleNamespace

import pytest

from agent.config import Settings
from agent.llm import LLM, NoToolCall, TaskBlock, parse_text
from core.checks import lint, sandbox_check
from core.observe import Field, ObserveResult, SuggestedNext, approx_tokens

EXAMPLE = "agent/prompts/example_steps.py"


def _obs(n_fields: int = 6) -> ObserveResult:
    fields = [Field(label=f"A fairly long field label number {i}", kind="select", required=(i % 2 == 0),
                    options=[f"Option number {j} for field {i}" for j in range(8)]) for i in range(n_fields)]
    fields.insert(3, Field(label="Country Code", kind="combobox", required=True,
                           options=[f"Option {i} (+{i})" for i in range(8)] + ["+40 more"]))
    return ObserveResult(page_kind="form", url="http://x/apply", title="t", section="s", fields=fields,
                         buttons=["Next"], suggested_next=SuggestedNext(action="fill", targets=["Field 0"]))


def test_system_prefix_is_stable_and_within_budget():
    llm = LLM(Settings(), EXAMPLE)
    a = llm.build_messages(TaskBlock(kind="add", observe=_obs(), instruction="Write the next step"))
    b = llm.build_messages(TaskBlock(kind="fix", observe=_obs(3), fix_step="step_02_name",
                                     fix_source="def step_02_name(page, p):\n    fill(page, 'x', p.y)",
                                     last_error="x: not found", instruction="Fix"))
    assert a[0] == b[0] and a[0]["role"] == "system"
    assert approx_tokens(a[0]["content"]) <= 1200
    assert approx_tokens(a[0]["content"]) - approx_tokens(open(EXAMPLE, encoding="utf-8").read()) <= 700
    assert a[1]["role"] == "user" and '"task":"add"' in a[1]["content"]
    assert '"fix_source"' in b[1]["content"] and '"fix_source"' not in a[1]["content"]


def test_trim_large_observe_under_cap():
    s = Settings()
    llm = LLM(s, EXAMPLE)
    task = TaskBlock(kind="add", observe=_obs(120), last_notes=["a", "b", "c"], instruction="Write the next step")
    msgs = llm.build_messages(task)
    total = sum(approx_tokens(m["content"]) for m in msgs)
    assert total <= s.PROMPT_TOKEN_CAP
    assert '"truncated":true' in msgs[1]["content"]
    assert len(task.observe.fields) == 121  # the caller's task is not mutated


def test_parse_fenced_json_and_prose():
    tc = parse_text('Sure.\n```json\n{"tool": "add_step", "args": {"name": "step_01_x", "code": "click(page, \\"Apply\\")"}}\n```')
    assert tc.name == "add_step" and tc.args["name"] == "step_01_x"
    tc = parse_text('{"name": "note", "arguments": "{\\"text\\": \\"hi\\"}"}')
    assert tc.name == "note" and tc.args == {"text": "hi"}
    with pytest.raises(NoToolCall):
        parse_text("I think we should click Apply next.")
    with pytest.raises(NoToolCall):
        parse_text('```json\n{"tool": "launch_rocket", "args": {}}\n```')


def test_parse_response_tool_calls_and_fallback():
    llm = LLM(Settings(), EXAMPLE)
    msg = SimpleNamespace(tool_calls=[SimpleNamespace(function=SimpleNamespace(name="note", arguments='{"text": "why"}'))], content=None)
    assert llm._parse_response(msg).args == {"text": "why"}
    msg = SimpleNamespace(tool_calls=None, content='```json\n{"tool":"observe","args":{}}\n```')
    assert llm._parse_response(msg).name == "observe"
    msg = SimpleNamespace(tool_calls=[SimpleNamespace(function=SimpleNamespace(name="note", arguments="{bad"))], content=None)
    with pytest.raises(NoToolCall):
        llm._parse_response(msg)


def test_json_mode_footer_when_forced():
    s = Settings(LLM_FORCE_JSON_MODE=True)
    llm = LLM(s, EXAMPLE)
    assert "Output format" in llm.system_content and not llm.supports_tools


def test_example_steps_pass_lint_and_sandbox():
    assert lint(EXAMPLE) is None
    assert sandbox_check(EXAMPLE) is None
