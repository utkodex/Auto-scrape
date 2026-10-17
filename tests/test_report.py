import json

from agent.report import scan_for_secrets, write_report
from core import helpers as h
from tests.fake_llm import MASK_TEST_STRINGS
from tests.test_loop_fixture import make_loop


def test_report_after_happy_and_phone_fix(browser, fixture_server, tmp_path, monkeypatch):
    monkeypatch.setattr(h, "DEFAULT_TIMEOUT_S", 2.0)
    lp, ctx, _ = make_loop("happy", browser, fixture_server, tmp_path / "a")
    lp.report_fn = write_report
    out = lp.run()
    assert out.status == "DONE" and out.report_path and out.report_path.exists()
    html = out.report_path.read_text(encoding="utf-8")
    m = json.loads((ctx.run_dir / "metrics.json").read_text())
    assert m["first_try_pass_rate"] == 1.0 and m["regressions_open"] == 0 and m["secret_hits"] == 0
    assert m["samples_passed"] == 2 and m["fix_count"] == 0 and m["rounds_total"] == 12
    assert m["observe_tokens_max"] <= 1500 and m["submit_presses"] == 0
    assert "<h2>Diff</h2>" in html and "+def step_12_next(page, p):" in html
    assert "Why do you want to join?" in html  # flagged screening question
    assert scan_for_secrets(ctx.run_dir, ctx.profile.mask_values()) == []
    for needle in MASK_TEST_STRINGS:
        assert needle not in html

    lp2, ctx2, _ = make_loop("phone_fix", browser, fixture_server, tmp_path / "b")
    lp2.report_fn = write_report
    out2 = lp2.run()
    assert out2.status == "DONE"
    m2 = json.loads((ctx2.run_dir / "metrics.json").read_text())
    assert m2["fix_count"] == 1 and m2["fix_locality_violations"] == 0 and m2["regressions_undone"] == 0
    html2 = out2.report_path.read_text(encoding="utf-8")
    # the diff is taken from run start (empty file), so it shows the fixed line as an addition
    diff_section = html2.split("<h2>Diff</h2>")[1].split("<h2>Log summary</h2>")[0]
    assert "+    pick(page" in diff_section and "India (+91)" in diff_section
    assert "&#34;+91&#34;)" not in diff_section  # the wrong option is gone from the final file


def test_scan_for_secrets_detects(tmp_path):
    (tmp_path / "x.md").write_text("hello Test Candidate", encoding="utf-8")
    (tmp_path / "y.json").write_text('{"a": "password=abc"}', encoding="utf-8")
    hits = scan_for_secrets(tmp_path, ["Test Candidate"])
    assert any("x.md" in h for h in hits) and any("y.json" in h for h in hits)
