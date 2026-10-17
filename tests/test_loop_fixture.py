"""End-to-end loop scenarios on the offline fixture with the scripted fake LLM (spec 03 §8.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import loop as loop_mod
from agent.config import Settings
from agent.loop import BuildLoop
from agent.runner import StepResult
from agent.steps_file import StepsFile
from agent.tools import ToolContext
from core import helpers as h
from core.log import ActionLog
from core.profile import load_profile
from tests.fake_llm import MASK_TEST_STRINGS, SCRIPTS, FakeLLM


def make_loop(name, browser, fixture_server, tmp_path, flags="", ask_human=None, settings=None):
    profile = load_profile("profiles/dummy.yaml")
    filler = tmp_path / "fillers" / "fixture"
    filler.mkdir(parents=True)
    sep = "&" if flags else ""
    (filler / "tests.yaml").write_text(
        f"samples:\n  - url: {fixture_server}job.html?id=1{sep}{flags}\n  - url: {fixture_server}job.html?id=2{sep}{flags}\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    ctx = ToolContext(
        browser=browser, steps_file=StepsFile(filler / "steps.py"), profile=profile,
        log=ActionLog(run_dir, mask_values=profile.mask_values()), company="fixture",
        filler_dir=filler, run_dir=run_dir, mode="build",
    )
    job_url = f"{fixture_server}job.html?id=1{sep}{flags}"
    llm = FakeLLM(SCRIPTS[name])
    lp = BuildLoop(settings or Settings(), ctx, llm, job_url, ask_human=ask_human or (lambda q: ""))
    return lp, ctx, llm


def rows(ctx):
    return ctx.log.rows()


def tools(ctx):
    return [(r["actor"], r["tool"], r.get("result", "")) for r in rows(ctx)]


def assert_masked(ctx):
    for f in ("actions.jsonl", "actions.md"):
        text = (ctx.run_dir / f).read_text(encoding="utf-8")
        for needle in MASK_TEST_STRINGS:
            assert needle not in text, f"{needle!r} leaked into {f}"


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch):
    monkeypatch.setattr(h, "DEFAULT_TIMEOUT_S", 2.0)


def test_happy_path(browser, fixture_server, tmp_path):
    lp, ctx, llm = make_loop("happy", browser, fixture_server, tmp_path)
    out = lp.run()
    assert out.status == "DONE", out.reason
    assert out.check.passed == 2 and out.check.total == 2
    names = ctx.steps_file.names()
    assert len(names) == 12 and ctx.steps_file.unlocked() is None
    s = ctx.log.summary()
    assert s["rounds"] == 12 and llm.calls == 12
    assert set(s["first_try_pass"]) == set(names)
    assert not any(t == "edit_file" for _, t, _ in tools(ctx))
    assert lp.state.edits_accepted == 0 and lp.state.regress_runs == 0
    assert rows(ctx)[-1]["tool"] == "done"
    assert [f["label"] for f in out.flags] == ["Why do you want to join?"]
    assert_masked(ctx)


def test_phone_fix_in_place(browser, fixture_server, tmp_path):
    lp, ctx, _ = make_loop("phone_fix", browser, fixture_server, tmp_path)
    out = lp.run()
    assert out.status == "DONE", out.reason
    edits = [r for r in rows(ctx) if r["tool"] == "edit_file"]
    assert len(edits) == 1 and edits[0]["result"].startswith("ok")
    fails = [r for r in rows(ctx) if r["tool"] == "run_step" and r["result"].startswith("FAIL")]
    assert len(fails) == 1 and fails[0]["retry_mode"] == "in_place"
    assert "no option '+91'" in fails[0]["result"]
    assert lp.state.edits_accepted == 1 and lp.state.regress_runs == 1  # REGRESS ran after the edit
    assert 'pick(page, "Country Code", "India (+91)")' in ctx.steps_file.read()
    assert ctx.log.summary()["tries_per_step"]["step_04_phone"] >= 2
    assert_masked(ctx)


def test_locked_edit_rejected_then_recovers(browser, fixture_server, tmp_path):
    lp, ctx, _ = make_loop("locked_edit", browser, fixture_server, tmp_path)
    out = lp.run()
    assert out.status == "DONE", out.reason
    rejected = [r for r in rows(ctx) if r["tool"] == "edit_file" and "locked" in r["result"]]
    assert len(rejected) == 1
    assert "p.first_name" in ctx.steps_file.source_of("step_02_name")
    assert lp.state.tries["step_04_phone"] >= 3


def test_regression_is_undone(browser, fixture_server, tmp_path, monkeypatch):
    lp, ctx, _ = make_loop("regression_undo", browser, fixture_server, tmp_path)
    real_replay = loop_mod.replay
    injected = {"done": False}

    def flaky_replay(browser_, steps_file, upto_index, profile, job_url, log=None, mode="build", round_no=None):
        if not injected["done"] and upto_index == 5:
            injected["done"] = True
            return StepResult(status="FAIL", step="step_02_name", error="First Name: not found (injected)")
        return real_replay(browser_, steps_file, upto_index, profile, job_url, log=log, mode=mode, round_no=round_no)

    monkeypatch.setattr(loop_mod, "replay", flaky_replay)
    out = lp.run()
    assert out.status == "DONE", out.reason
    undo = [r for r in rows(ctx) if r["tool"] == "undo"]
    assert len(undo) == 1 and "step_02_name" in undo[0]["result"]
    # after the undo the next fix task carried the regression message
    fix_tasks = [t for t in lp.llm.tasks if t.kind == "fix"]
    assert any("made the earlier step step_02_name fail" in (t.last_error or "") for t in fix_tasks)
    assert 'upload(page, "Resume", p.resume)' in ctx.steps_file.source_of("step_05_resume")
    assert ctx.steps_file.locked_names() == ctx.steps_file.names()


def test_always_fail_switch_ask_stop(browser, fixture_server, tmp_path):
    asked = []

    def human(q):
        asked.append(q)
        return ""

    lp, ctx, _ = make_loop("always_fail", browser, fixture_server, tmp_path, ask_human=human)
    out = lp.run()
    assert out.status == "STOP" and "8 tries on step_04_phone" in out.reason
    kinds = [(r["tool"], r["result"]) for r in rows(ctx)]
    switch = [k for k in kinds if k[0] == "switch"]
    assert len(switch) == 1 and "Country Code" in switch[0][1]
    assert len(asked) == 1 and "step_04_phone" in asked[0]
    assert kinds[-1][0] == "stop"
    assert ctx.steps_file.locked_names() == ["step_01_click_apply", "step_02_name", "step_03_email"]
    assert ctx.steps_file.unlocked() == "step_04_phone"
    assert lp.state.tries["step_04_phone"] == 8
    # the switch re-observed with a scope and offered a similar step
    switch_tasks = [t for t in lp.llm.tasks if t.similar_step]
    assert switch_tasks and "pick(" in switch_tasks[0].similar_step


def test_submit_attempt_rejected(browser, fixture_server, tmp_path):
    lp, ctx, _ = make_loop("submit_attempt", browser, fixture_server, tmp_path)
    out = lp.run()
    assert out.status == "DONE", out.reason
    rejected = [r for r in rows(ctx) if r["tool"] == "add_step" and "submit is not allowed" in r["result"]]
    assert len(rejected) == 1
    assert "Submit" not in ctx.steps_file.read()
    assert not browser.page.url.endswith("submitted.html")


def test_login_page_asks_human_then_resumes(browser, fixture_server, tmp_path):
    asked = []

    def human(q):
        asked.append(q)
        page = browser.page
        page.fill("#user", "tester")
        page.fill("#pass", "secret-password")
        page.click("#signin")
        page.wait_for_load_state("domcontentloaded")
        return "logged in"

    lp, ctx, _ = make_loop("login", browser, fixture_server, tmp_path, flags="login=1", ask_human=human)
    out = lp.run()
    assert out.status == "DONE", out.reason
    assert len(asked) == 1 and "login" in asked[0]
    assert any(r["tool"] == "ask_human" and r["actor"] == "human" for r in rows(ctx))
    assert "secret-password" not in (ctx.run_dir / "actions.jsonl").read_text(encoding="utf-8")
    browser.context.clear_cookies()


def test_popup_apply(browser, fixture_server, tmp_path):
    lp, ctx, _ = make_loop("popup", browser, fixture_server, tmp_path, flags="popup=1")
    out = lp.run()
    assert out.status == "DONE", out.reason
    obs_rows = [r for r in rows(ctx) if r["tool"] == "observe"]
    assert any("apply_1" in (r.get("in") or "") or "form" in r["result"] for r in obs_rows)
    assert 'click(page, "Apply", expect="navigation")' in ctx.steps_file.source_of("step_01_click_apply")
    browser.goto(fixture_server + "job.html")


def test_many_fields_observe_under_cap(browser, fixture_server, tmp_path):
    lp, ctx, _ = make_loop("many_fields", browser, fixture_server, tmp_path, flags="many=1")
    out = lp.run()
    assert out.status == "DONE", out.reason
    obs_rows = [r for r in rows(ctx) if r["tool"] == "observe"]
    assert obs_rows and all(r["tokens"] <= 1500 for r in obs_rows)
    assert any("truncated" in json.dumps(r) or r["tokens"] > 800 for r in obs_rows)


def test_invariants_single_unlocked_and_add_refused(browser, fixture_server, tmp_path):
    lp, ctx, _ = make_loop("phone_fix", browser, fixture_server, tmp_path)
    out = lp.run()
    assert out.status == "DONE"
    lock = json.loads((ctx.filler_dir / "steps.lock.json").read_text())
    assert all(v["status"] == "locked" for v in lock.values())
    ctx.steps_file.verify_lock_hashes()
    # REGRESS ran after every accepted edit
    assert lp.state.regress_runs == lp.state.edits_accepted == 1
    assert Path(ctx.run_dir / "probe").exists()


def test_second_sample_replays_locked_steps_without_the_model(browser, fixture_server, tmp_path):
    # Build once to a complete, locked steps.py.
    lp, ctx, _ = make_loop("happy", browser, fixture_server, tmp_path)
    assert lp.run().status == "DONE"
    # A new build on another sample of the same company starts on the job page again; the loop
    # must replay the locked steps to reach the review page, not ask the model for step N+1.
    profile = load_profile("profiles/dummy.yaml")
    run2 = tmp_path / "run2"
    ctx2 = ToolContext(
        browser=browser, steps_file=StepsFile(ctx.filler_dir / "steps.py"), profile=profile,
        log=ActionLog(run2, mask_values=profile.mask_values()), company="fixture",
        filler_dir=ctx.filler_dir, run_dir=run2, mode="build",
    )
    llm2 = FakeLLM(SCRIPTS["always_fail"])  # would derail the build if it were ever consulted
    lp2 = BuildLoop(Settings(), ctx2, llm2, f"{fixture_server}job.html?id=2", ask_human=lambda q: "")
    out = lp2.run()
    assert out.status == "DONE", out.reason
    assert not [r for r in ctx2.log.rows() if r["actor"] == "qwen"]
    assert [r for r in ctx2.log.rows() if r["tool"] == "replay"]
