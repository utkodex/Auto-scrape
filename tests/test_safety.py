"""Safety-critical checks (run first via `pytest -m safety`)."""

import ast
from pathlib import Path

import pytest

from agent.cli import company_from_url, normalise_url
from core import helpers as h
from core.checks import sandbox_check
from core.helpers import ForbiddenAction
from core.profile import ProfileError, load_profile
from tests.fake_llm import MASK_TEST_STRINGS
from tests.test_loop_fixture import make_loop

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "tests" / "data"

pytestmark = pytest.mark.safety


def test_submit_guard_helper_level(browser, fixture_server):
    browser.goto(fixture_server + "review.html")
    for mode in ("build", "test"):
        h.begin(mode=mode)
        with pytest.raises(ForbiddenAction):
            h.click(browser.page, "Submit application")
        h.end()
    assert browser.page.url.endswith("review.html")


def test_submit_guard_sandbox_level():
    assert "submit" in sandbox_check(DATA / "steps_bad_submit.py")


def test_all_bad_step_files_rejected():
    bad = sorted(DATA.glob("steps_bad_*.py"))
    assert len(bad) >= 7
    for f in bad:
        assert sandbox_check(f) is not None, f.name


def test_host_allowlist_blocks(browser, fixture_server):
    browser.goto(fixture_server + "apply_1.html")
    before = browser.blocked_requests
    browser.page.evaluate("() => fetch('http://127.0.0.2:1/x').catch(() => 'blocked')")
    assert browser.blocked_requests == before + 1


def test_no_submit_import_in_build_path():
    for folder in ("agent", "core"):
        for py in (REPO / folder).rglob("*.py"):
            if py.name == "submit.py":
                continue
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and "core.submit" in node.module:
                    pytest.fail(f"{py} imports core.submit")
                if isinstance(node, ast.Import) and any("core.submit" in a.name for a in node.names):
                    pytest.fail(f"{py} imports core.submit")


def test_profile_loader_rejects_outside_resume(tmp_path):
    bad = tmp_path / "p.yaml"
    bad.write_text("first_name: A\nlast_name: B\nemail: a@example.com\nphone: '1'\nresume: ../my-docs/x.pdf\n", encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile(bad)


def test_login_page_stops_loop_and_masks(browser, fixture_server, tmp_path, monkeypatch):
    monkeypatch.setattr(h, "DEFAULT_TIMEOUT_S", 2.0)
    asked = []

    def human(q):
        asked.append(q)
        page = browser.page
        page.fill("#user", "tester")
        page.fill("#pass", "secret-password")
        page.click("#signin")
        page.wait_for_load_state("domcontentloaded")
        return "ok"

    lp, ctx, _ = make_loop("login", browser, fixture_server, tmp_path, flags="login=1", ask_human=human)
    out = lp.run()
    assert out.status == "DONE" and len(asked) == 1
    for f in ("actions.jsonl", "actions.md"):
        text = (ctx.run_dir / f).read_text(encoding="utf-8")
        assert "secret-password" not in text
        for needle in MASK_TEST_STRINGS:
            assert needle not in text
    browser.context.clear_cookies()


def test_url_normalisation_and_company():
    assert normalise_url("https://x.example/jobs/1&amp;x=1") == "https://x.example/jobs/1&x=1"
    assert normalise_url(" capgemini.com/in-en/jobs/196626-en_GB+sap_btp ") == "https://capgemini.com/in-en/jobs/196626-en_GB+sap_btp"
    assert company_from_url("https://careers.hcltech.com/job/x/180703-en_US") == "hcltech"
    assert company_from_url("https://www.capgemini.com/in-en/jobs/1") == "capgemini"
    assert company_from_url("http://127.0.0.1:5000/job.html") == "fixture"
