import shutil
from pathlib import Path

import pytest

from agent.runner import load_samples, replay, run_checks, run_step
from agent.steps_file import EditError, StepsFile
from core import helpers as h
from core.log import ActionLog
from core.profile import load_profile

EXAMPLE = Path(__file__).resolve().parent.parent / "agent" / "prompts" / "example_steps.py"


def make_filler(tmp_path, fixture_server, steps_text: str | None = None, samples=(1, 2)) -> Path:
    d = tmp_path / "fillers" / "fixture"
    d.mkdir(parents=True)
    if steps_text is None:
        shutil.copy(EXAMPLE, d / "steps.py")
    else:
        (d / "steps.py").write_text(steps_text, encoding="utf-8")
    (d / "tests.yaml").write_text(
        "samples:\n" + "".join(f"  - url: {fixture_server}job.html?id={i}\n" for i in samples), encoding="utf-8"
    )
    sf = StepsFile(d / "steps.py")
    try:
        for s in sf.steps():
            sf.lock(s.name)
    except EditError:
        pass  # deliberately broken files are used by the lint-stage test
    return d


@pytest.fixture
def profile():
    return load_profile("profiles/dummy.yaml")


def test_run_checks_example_passes_two_samples(browser, fixture_server, tmp_path, profile):
    d = make_filler(tmp_path, fixture_server)
    log = ActionLog(tmp_path / "run", mask_values=profile.values())
    res = run_checks(d, browser, profile, log=log, mode="test")
    assert res.status == "PASS", res.summary()
    assert res.passed == 2 and res.total == 2 and res.resume_attached
    assert res.s < 60
    assert log.rows()[-1]["tool"] == "run_checks" and log.rows()[-1]["result"] == "PASS 2/2"
    assert browser.page.url.endswith("review.html?id=2")


def test_run_checks_reports_first_failure(browser, fixture_server, tmp_path, profile):
    text = EXAMPLE.read_text(encoding="utf-8").replace('"India (+91)"', '"+91"')
    d = make_filler(tmp_path, fixture_server, text)
    res = run_checks(d, browser, profile, mode="test")
    assert res.status == "FAIL" and res.stage == "fill"
    assert res.step == "step_04_phone"
    assert "no option '+91'" in res.error and len(res.error.splitlines()) <= 20
    assert res.sample.endswith("job.html?id=1") and res.passed == 0


def test_run_checks_lint_and_sandbox_stages(browser, fixture_server, tmp_path, profile):
    d = make_filler(tmp_path, fixture_server, "def step_01_x(page, p)\n    pass\n")
    assert run_checks(d, browser, profile).stage == "lint"
    d2 = make_filler(tmp_path / "b", fixture_server,
                     "from core.helpers import click\n\n\ndef step_01_x(page, p):\n    page.click('x')\n")
    res = run_checks(d2, browser, profile)
    assert res.stage == "sandbox" and "page." in res.error


def test_run_step_pass_and_fail_in_place(browser, fixture_server, tmp_path, profile):
    text = EXAMPLE.read_text(encoding="utf-8").replace('"India (+91)"', '"+91"')
    d = make_filler(tmp_path, fixture_server, text)
    sf = StepsFile(d / "steps.py")
    log = ActionLog(tmp_path / "run", mask_values=profile.values())
    browser.goto(fixture_server + "job.html")
    r1 = run_step(browser, sf, "step_01_click_apply", profile, log=log, mode="build")
    assert r1.status == "PASS" and r1.checks == 1 and r1.page_kind_after == "form"
    r2 = run_step(browser, sf, "step_02_name", profile, log=log, mode="build")
    assert r2.status == "PASS" and r2.checks == 2
    run_step(browser, sf, "step_03_email", profile, log=log)
    r4 = run_step(browser, sf, "step_04_phone", profile, log=log, mode="build", screenshots_dir=tmp_path / "shots")
    assert r4.status == "FAIL" and r4.retry_mode == "in_place" and r4.element_found
    assert "no option '+91'" in r4.error
    assert list((tmp_path / "shots").glob("*.png"))
    assert log.rows()[-1]["result"].startswith("FAIL") and log.rows()[-1]["retry_mode"] == "in_place"
    assert log.summary()["tries_per_step"]["step_04_phone"] == 1


def test_run_step_element_not_found(browser, fixture_server, tmp_path, profile, monkeypatch):
    text = EXAMPLE.read_text(encoding="utf-8").replace('"Apply"', '"Launch"')
    d = make_filler(tmp_path, fixture_server, text)
    sf = StepsFile(d / "steps.py")
    monkeypatch.setattr(h, "DEFAULT_TIMEOUT_S", 1.0)
    browser.goto(fixture_server + "job.html")
    r = run_step(browser, sf, "step_01_click_apply", profile, mode="build")
    assert r.status == "FAIL" and r.element_found is False


def test_run_step_failed_expectation(browser, fixture_server, tmp_path, profile):
    # step passes its action but the value read back differs (fixture rejects nothing, so force it)
    text = (
        "from core.helpers import fill\n\n\n"
        "def step_01_name(page, p):\n    fill(page, 'First Name', p.first_name)\n"
    )
    d = make_filler(tmp_path, fixture_server, text)
    sf = StepsFile(d / "steps.py")
    browser.goto(fixture_server + "apply_1.html")
    browser.page.evaluate("() => document.getElementById('first').addEventListener('input', e => { e.target.value = 'X' })")
    r = run_step(browser, sf, "step_01_name", profile, mode="build")
    assert r.status == "FAIL" and r.failed_check.startswith("ValueEquals") and "expected 'Test'" in r.error


def test_replay_runs_locked_steps_from_job_link(browser, fixture_server, tmp_path, profile):
    d = make_filler(tmp_path, fixture_server)
    sf = StepsFile(d / "steps.py")
    log = ActionLog(tmp_path / "run", mask_values=profile.values())
    assert replay(browser, sf, 5, profile, fixture_server + "job.html", log=log) is None
    assert browser.page.url.endswith("apply_1.html")
    assert browser.page.input_value("#phone") == "+91 90000 00000"
    assert log.rows()[-1]["tool"] == "replay" and log.rows()[-1]["result"] == "PASS"


def test_load_samples_formats(tmp_path):
    (tmp_path / "tests.yaml").write_text(
        "samples:\n  - http://a/1\n  - url: http://a/2\n    expect: {reaches: review, resume_attached: false}\n",
        encoding="utf-8",
    )
    s = load_samples(tmp_path)
    assert s[0] == {"url": "http://a/1", "expect": {"reaches": "review", "resume_attached": True}}
    assert s[1]["expect"]["resume_attached"] is False
