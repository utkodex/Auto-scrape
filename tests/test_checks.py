from pathlib import Path

import pytest

from core import helpers as h
from core.checks import lint, sandbox_check, verify_expectations
from core.helpers import Checked, FileAttached, NewFields, OptionSelected, PageChanged, ValueEquals

DATA = Path(__file__).parent / "data"

BAD = {
    "steps_bad_import.py": "import",
    "steps_bad_page_attr.py": "page.",
    "steps_bad_if.py": "'if'",
    "steps_bad_submit.py": "submit",
    "steps_bad_gap.py": "gap",
    "steps_bad_noexpect.py": "no expectation",
    "steps_bad_name.py": "bad step name",
}


def test_good_file_passes_lint_and_sandbox():
    assert lint(DATA / "steps_good.py") is None
    assert sandbox_check(DATA / "steps_good.py") is None


@pytest.mark.parametrize("name,needle", BAD.items())
def test_bad_files_rejected(name, needle):
    err = sandbox_check(DATA / name)
    assert err is not None and needle in err, err


def test_sandbox_rejects_inline_snippets(tmp_path):
    cases = {
        "for x in p.skills:\n        fill(page, 'Skills', x)": "'for'",
        "fill(page, 'Email', open('x').read())": "nested call",
        "fill(page, 'Email', eval('1'))": "nested call",
        "fill(page, 'Email', p.email, timeout=3)": "keyword 'timeout'",
        "fill('Email', p.email)": "first argument must be page",
        "x = 1\n        fill(page, 'Email', p.email)": "'assignment'",
        "fill(page, 'Email', something)": "name 'something'",
    }
    for body, needle in cases.items():
        f = tmp_path / "s.py"
        f.write_text(
            "from core.helpers import fill\n\n\ndef step_01_x(page, p):\n        " + body + "\n",
            encoding="utf-8",
        )
        err = sandbox_check(f)
        assert err is not None and needle in err, (body, err)


def test_sandbox_allows_fstrings_and_answers(tmp_path):
    f = tmp_path / "s.py"
    f.write_text(
        "from core.helpers import check, fill\n\n\n"
        "def step_01_x(page, p):\n"
        "    fill(page, 'Full name', f'{p.first_name} {p.last_name}')\n"
        "    fill(page, 'Years', p.first_name + ' y')\n"
        "    check(page, 'Relocate', p.answers['willing_to_relocate'])\n",
        encoding="utf-8",
    )
    assert sandbox_check(f) is None


def test_lint_reports_syntax_error(tmp_path):
    f = tmp_path / "s.py"
    f.write_text("def step_01_x(page, p)\n    pass\n", encoding="utf-8")
    assert lint(f) is not None


def test_verify_expectations_on_fixture(browser, fixture_server):
    browser.goto(fixture_server + "apply_1.html")
    page = browser.page
    h.begin(mode="build")
    h.fill(page, "First Name", "Test")
    h.pick(page, "Country Code", "India (+91)")
    h.upload(page, "Resume", "profiles/dummy_resume.pdf")
    exps = h.end()
    assert verify_expectations(page, exps) == []

    fails = verify_expectations(page, [ValueEquals("First Name", "Other")])
    assert len(fails) == 1 and "expected 'Other'" in fails[0].error and fails[0].element_found

    fails = verify_expectations(page, [OptionSelected("Country Code", "United States (+1)")])
    assert len(fails) == 1 and "United States" in fails[0].error

    fails = verify_expectations(page, [FileAttached("Resume", "other.pdf")])
    assert len(fails) == 1

    fails = verify_expectations(page, [ValueEquals("Nonexistent", "x")])
    assert len(fails) == 1 and fails[0].element_found is False

    # page change / new fields
    assert verify_expectations(page, [PageChanged(prev_url=page.url)])[0].error.startswith("page did not change")
    assert verify_expectations(page, [PageChanged(prev_url="http://other/")]) == []
    assert verify_expectations(page, [NewFields(prev_labels=["First Name"])]) == []
    all_labels = ["First Name", "Last Name", "Email", "Country Code", "Phone Number", "Resume"]
    assert len(verify_expectations(page, [NewFields(prev_labels=all_labels)])) == 1


def test_verify_checked(browser, fixture_server):
    browser.goto(fixture_server + "apply_2.html")
    page = browser.page
    h.begin(mode="build")
    h.check(page, "Willing to relocate", "No")
    exps = h.end()
    assert verify_expectations(page, exps) == []
    assert len(verify_expectations(page, [Checked("Willing to relocate", "Yes")])) == 1
    assert len(verify_expectations(page, [Checked("Work authorization in India", "Yes")])) == 1
    assert verify_expectations(page, [Checked("Work authorization in India", "No")]) == []
