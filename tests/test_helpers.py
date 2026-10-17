import pytest

from core import helpers as h
from core.helpers import (
    AmbiguousLabel,
    Checked,
    FileAttached,
    ForbiddenAction,
    HelperError,
    OptionSelected,
    PageChanged,
    ValueEquals,
    check,
    click,
    fill,
    pick,
    upload,
    wait_for,
)


@pytest.fixture
def page(browser, fixture_server):
    browser.goto(fixture_server + "apply_1.html")
    h.begin(prev_url=browser.page.url, prev_labels=["First Name"], mode="build")
    yield browser.page
    h.end()


def test_fill_records_value_equals(page):
    fill(page, "First Name", "Test")
    exps = h.end()
    assert len(exps) == 1 and isinstance(exps[0], ValueEquals)
    assert exps[0] == ValueEquals("First Name", "Test")
    assert page.input_value("#first") == "Test"


def test_fill_matches_label_with_asterisk_and_case(page):
    fill(page, "email", "a@example.com")
    assert page.input_value("#email") == "a@example.com"


def test_pick_exact_option_in_combobox(page):
    pick(page, "Country Code", "India (+91)")
    exps = h.end()
    assert exps == [OptionSelected("Country Code", "India (+91)")]
    assert page.input_value("#cc") == "India (+91)"


def test_pick_wrong_option_lists_close_matches(page):
    with pytest.raises(HelperError) as ei:
        pick(page, "Country Code", "+91")
    assert "no option '+91'" in str(ei.value)
    assert "India (+91)" in str(ei.value)
    assert h.end() == []


def test_pick_native_select(browser, fixture_server):
    browser.goto(fixture_server + "apply_2.html")
    h.begin(mode="build")
    pick(browser.page, "Country", "India")
    assert h.end() == [OptionSelected("Country", "India")]
    assert browser.page.locator("#country").evaluate("el => el.value") == "India"


def test_upload_records_file_attached(page):
    upload(page, "Resume", "profiles/dummy_resume.pdf")
    assert h.end() == [FileAttached("Resume", "dummy_resume.pdf")]
    assert page.inner_text("#resume-name") == "dummy_resume.pdf"


def test_upload_outside_profiles_forbidden(page):
    with pytest.raises(ForbiddenAction):
        upload(page, "Resume", "my-docs/x.pdf")


def test_check_radio_and_checkbox(browser, fixture_server):
    browser.goto(fixture_server + "apply_2.html")
    h.begin(mode="build")
    check(browser.page, "Willing to relocate", "Yes")
    check(browser.page, "Work authorization in India", "Yes")
    exps = h.end()
    assert exps == [Checked("Willing to relocate", "Yes"), Checked("Work authorization in India", "Yes")]
    assert browser.page.is_checked("input[name=relocate][value=Yes]")
    assert browser.page.is_checked("#auth")


def test_check_unknown_radio_option(browser, fixture_server):
    browser.goto(fixture_server + "apply_2.html")
    h.begin(mode="build")
    with pytest.raises(HelperError) as ei:
        check(browser.page, "Willing to relocate", "Maybe")
    assert "'Yes'" in str(ei.value)


def test_click_next_records_section_expectation(page):
    click(page, "Save", expect="section")
    exps = h.end()
    assert len(exps) == 1 and isinstance(exps[0], h.NewFields)


def test_click_apply_navigation(browser, fixture_server):
    browser.goto(fixture_server + "job.html")
    h.begin(prev_url=browser.page.url, mode="build")
    click(browser.page, "Apply", expect="navigation")
    exps = h.end()
    assert isinstance(exps[0], PageChanged)
    assert "apply_1.html" in browser.page.url


def test_click_apply_popup_switches_page(browser, fixture_server):
    browser.goto(fixture_server + "job.html?popup=1")
    h.begin(prev_url=browser.page.url, mode="build")
    click(browser.page, "Apply", expect="navigation")
    h.end()
    assert "apply_1.html" in browser.page.url and len(browser.pages) == 2
    browser.goto(fixture_server + "job.html")


def test_submit_guard_in_build_mode(browser, fixture_server):
    browser.goto(fixture_server + "review.html")
    h.begin(mode="build")
    with pytest.raises(ForbiddenAction):
        click(browser.page, "Submit application")
    assert browser.page.url.endswith("review.html")
    h.end()
    h.begin(mode="test")
    with pytest.raises(ForbiddenAction):
        click(browser.page, "submit")
    h.end()


def test_click_missing_button(page):
    h.DEFAULT_TIMEOUT_S, saved = 1.0, h.DEFAULT_TIMEOUT_S
    try:
        with pytest.raises(HelperError) as ei:
            click(page, "Launch rocket")
        assert ei.value.element_found is False
    finally:
        h.DEFAULT_TIMEOUT_S = saved


def test_ambiguous_label(browser, fixture_server):
    browser.goto(fixture_server + "ambiguous.html")
    h.begin(mode="build")
    with pytest.raises(AmbiguousLabel) as ei:
        fill(browser.page, "Phone", "1")
    assert "2 matches" in str(ei.value)
    h.end()


def test_wait_for_records_nothing(page):
    wait_for(page, "Phone Number")
    assert h.end() == []


def test_missing_label_reports_not_found(page, monkeypatch):
    monkeypatch.setattr(h, "DEFAULT_TIMEOUT_S", 1.0)
    with pytest.raises(HelperError) as ei:
        fill(page, "Favourite colour", "blue")
    assert ei.value.element_found is False
    assert "not found" in str(ei.value)


def test_fill_label_timeout_is_short_for_tests():
    # guard against accidental slow tests: default must be the spec's 10 s
    assert h.DEFAULT_TIMEOUT_S == 10.0
