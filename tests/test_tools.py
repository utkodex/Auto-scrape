from pathlib import Path

import pytest

from agent.steps_file import StepsFile
from agent.tools import TOOL_NAMES, TOOL_SCHEMAS, Dispatcher, ToolCall, ToolContext, trim_result
from core.log import ActionLog
from core.profile import load_profile

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def ctx(browser, fixture_server, tmp_path):
    profile = load_profile("profiles/dummy.yaml")
    filler = tmp_path / "fillers" / "fixture"
    filler.mkdir(parents=True)
    run_dir = tmp_path / "run"
    sf = StepsFile(filler / "steps.py")
    c = ToolContext(
        browser=browser, steps_file=sf, profile=profile,
        log=ActionLog(run_dir, mask_values=profile.values()),
        company="fixture", filler_dir=filler, run_dir=run_dir,
        run_step_fn=lambda name: {"ok": True, "status": "PASS", "step": name},
        run_checks_fn=lambda: {"ok": True, "status": "PASS", "passed": 1, "total": 1},
        ask_human_fn=lambda q: "done",
    )
    browser.goto(fixture_server + "apply_1.html")
    return c


def test_schemas_shape():
    assert len(TOOL_SCHEMAS) == 8
    for t in TOOL_SCHEMAS:
        assert t["type"] == "function"
        assert set(t["function"]) >= {"name", "description", "parameters"}
        assert t["function"]["parameters"]["type"] == "object"
    assert TOOL_NAMES == ["observe", "read_file", "add_step", "edit_file", "run_step", "run_checks", "note", "ask_human"]


def test_read_file_allowlist(ctx):
    d = Dispatcher(ctx)
    assert d.call(ToolCall("read_file", {"path": "core/submit.py"}))["ok"] is False
    assert d.call(ToolCall("read_file", {"path": "profiles/dummy.yaml"}))["ok"] is False
    res = d.call(ToolCall("read_file", {"path": "core/helpers.py"}))
    assert res["ok"] and "def pick(" in res["text"] and "locate(" not in res["text"].split("def pick(")[0].splitlines()[-1]
    assert "_record" not in res["text"]
    res = d.call(ToolCall("read_file", {"path": "agent/prompts/example_steps.py", "start": 1, "end": 5}))
    assert res["ok"] and res["lines"] == "1-5" and len(res["text"].splitlines()) == 5
    res = d.call(ToolCall("read_file", {"path": "agent/prompts/example_steps.py", "start": 1, "end": 500}))
    assert len(res["text"].splitlines()) <= 80


def test_add_step_happy_and_rejections(ctx):
    d = Dispatcher(ctx)
    d.new_round()
    res = d.call(ToolCall("add_step", {"name": "step_01_apply", "code": "click(page, 'Apply', expect='navigation')"}))
    assert res == {"ok": True, "step": "step_01_apply", "index": 1, "lint": "ok"}
    assert ctx.steps_file.read().startswith("from core.helpers import")
    # second mutation in the same round is refused
    res = d.call(ToolCall("add_step", {"name": "step_02_x", "code": "fill(page, 'First Name', p.first_name)"}))
    assert res["ok"] is False and "one add_step" in res["error"]
    d.new_round()
    res = d.call(ToolCall("add_step", {"name": "step_1_x", "code": "fill(page, 'First Name', p.first_name)"}))
    assert res["ok"] is False and "bad step name" in res["error"]
    ctx.steps_file.lock("step_01_apply")
    d.new_round()
    res = d.call(ToolCall("add_step", {"name": "step_02_x", "code": "page.click('#first')"}))
    assert res["ok"] is False and "page." in res["error"]
    assert ctx.steps_file.names() == ["step_01_apply"]  # rejected step was removed
    d.new_round()
    res = d.call(ToolCall("add_step", {"name": "step_02_x", "code": "click(page, 'Submit application')"}))
    assert res["ok"] is False and "submit is not allowed" in res["error"]


def test_edit_file_rules(ctx):
    d = Dispatcher(ctx)
    d.new_round()
    d.call(ToolCall("add_step", {"name": "step_01_apply", "code": "click(page, 'Apply', expect='navigation')"}))
    ctx.steps_file.lock("step_01_apply")
    d.new_round()
    d.call(ToolCall("add_step", {"name": "step_02_phone", "code": 'pick(page, "Country Code", "+91")'}))
    d.new_round()
    res = d.call(ToolCall("edit_file", {"path": "other.py", "old": "+91", "new": "x"}))
    assert res["ok"] is False and "steps.py" in res["error"]
    res = d.call(ToolCall("edit_file", {"path": "steps.py", "old": "'Apply'", "new": "'Go'"}))
    assert res["ok"] is False and "locked" in res["error"]
    res = d.call(ToolCall("edit_file", {"path": "steps.py", "old": '"+91"', "new": '"India (+91)"'}))
    assert res == {"ok": True, "step": "step_02_phone", "lint": "ok"}
    d.new_round()
    res = d.call(ToolCall("edit_file", {"path": "steps.py", "old": '"India (+91)"', "new": "p.x[0]"}))
    assert res["ok"] is False and "rejected" in res["error"]
    assert '"India (+91)"' in ctx.steps_file.read()  # bad edit was undone


def test_observe_note_ask_human_and_unknown(ctx):
    d = Dispatcher(ctx)
    res = d.call(ToolCall("observe", {}))
    assert res["ok"] and res["page_kind"] == "form" and ctx.last_observe is not None
    res = d.call(ToolCall("observe", {"scope": "Phone"}))
    assert [f["label"] for f in res["fields"]] == ["Phone Number"]
    for i in range(5):
        d.call(ToolCall("note", {"text": f"note {i}"}))
    assert ctx.last_notes == ["note 2", "note 3", "note 4"]
    assert d.call(ToolCall("ask_human", {"question": "login?"})) == {"ok": True, "answer": "done"}
    assert d.call(ToolCall("launch", {}))["ok"] is False
    assert d.call(ToolCall("run_step", {"name": "step_01"}))["status"] == "PASS"
    assert d.call(ToolCall("run_checks", {}))["passed"] == 1
    res = d.call(ToolCall("add_step", {"bogus": 1}))
    assert res["ok"] is False and "bad arguments" in res["error"]
    rows = ctx.log.rows()
    assert any(r["tool"] == "ask_human" and r["actor"] == "human" for r in rows)
    assert any(r["tool"] == "note" and r["actor"] == "qwen" for r in rows)


def test_trim_result():
    big = {"ok": False, "error": "\n".join(f"line {i}" for i in range(200))}
    out = trim_result("run_step", big)
    assert len(out["error"]) <= 600 and out["error"].endswith("line 199")
    assert trim_result("observe", big) is big
