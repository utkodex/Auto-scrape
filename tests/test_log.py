import json

from core.log import ActionLog
from core.profile import load_profile


def test_masking_and_md(tmp_path):
    p = load_profile("profiles/dummy.yaml")
    log = ActionLog(tmp_path / "run", mask_values=p.values())
    log.row("qwen", "add_step", "step_02_name", "fill First Name = Test Candidate, phone +91 90000 00000", tok_in=1400, tok_out=90, s=2.9, round=2)
    log.row("harness", "run_step", "step_02_name", "PASS · dummy_resume.pdf attached", s=0.9)
    text = (tmp_path / "run" / "actions.jsonl").read_text(encoding="utf-8")
    md = (tmp_path / "run" / "actions.md").read_text(encoding="utf-8")
    for needle in ("Test Candidate", "+91 90000 00000", "dummy_resume.pdf", "Candidate"):
        assert needle not in text and needle not in md
    assert "***" in text
    assert "## step_02_name" in md
    rows = [json.loads(line) for line in text.splitlines()]
    assert rows[0]["tok_in"] == 1400 and rows[0]["actor"] == "qwen" and rows[0]["round"] == 2
    assert rows[1]["s"] == 0.9


def test_secret_lines_redacted(tmp_path):
    log = ActionLog(tmp_path / "run", mask_values=[])
    log.row("qwen", "note", "setting password=abc123 for login", "ok")
    log.row("harness", "observe", "page", "Set-Cookie: sid=1")
    rows = [json.loads(line) for line in (tmp_path / "run" / "actions.jsonl").read_text().splitlines()]
    assert all(r["result"] == "[redacted line]" and r["tool"] == "redacted" for r in rows)
    assert "abc123" not in (tmp_path / "run" / "actions.jsonl").read_text()


def test_summary_counts(tmp_path):
    log = ActionLog(tmp_path / "run", mask_values=["Test"])
    log.row("harness", "observe", "job", "job description", s=1.0)
    log.row("qwen", "add_step", "step_01_click_apply", "ok", tok_in=1000, tok_out=50, s=3.0, round=1)
    log.row("harness", "run_step", "step_01_click_apply", "PASS", s=1.0)
    log.row("qwen", "add_step", "step_02_name", "ok", tok_in=1200, tok_out=60, s=3.0, round=2)
    log.row("harness", "run_step", "step_02_name", "FAIL · x", s=1.0)
    log.row("qwen", "edit_file", "step_02_name", "ok", tok_in=600, tok_out=40, s=2.0, round=3)
    log.row("harness", "run_step", "step_02_name", "PASS", s=1.0)
    log.row("harness", "undo", "step_02_name", "regressed step_01")
    log.row("human", "ask_human", "login", "done", s=30.0)
    s = log.summary()
    assert s["rounds"] == 3
    assert s["tok_in_total"] == 2800 and s["tok_out_max"] == 60
    assert s["tok_in_p95"] == 1200
    assert s["tries_per_step"] == {"step_01_click_apply": 1, "step_02_name": 2}
    assert s["first_try_pass"] == ["step_01_click_apply"]
    assert s["undo_count"] == 1 and s["fix_count"] == 1
    assert s["seconds_human"] == 30.0


def test_log_reloads_existing_rows(tmp_path):
    log = ActionLog(tmp_path / "run", mask_values=[])
    log.row("qwen", "note", "a", "b", tok_in=1, tok_out=1)
    again = ActionLog(tmp_path / "run", mask_values=[])
    assert len(again.rows()) == 1 and again.summary()["rounds"] == 1
