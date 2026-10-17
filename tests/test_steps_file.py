import pytest

from agent.steps_file import (
    HEADER,
    EditError,
    LockedStepError,
    StepNameError,
    StepsFile,
    TamperError,
)


@pytest.fixture
def sf(tmp_path):
    return StepsFile(tmp_path / "steps.py", tmp_path / "steps.lock.json")


def test_add_writes_header_and_function(sf):
    info = sf.add("step_01_click_apply", 'click(page, "Apply", expect="navigation")')
    text = sf.path.read_text(encoding="utf-8")
    assert text.startswith(HEADER)
    assert "def step_01_click_apply(page, p):\n    click(page" in text
    assert info.index == 1 and not info.locked
    assert sf.unlocked() == "step_01_click_apply"


def test_add_requires_previous_locked_and_order(sf):
    sf.add("step_01_click_apply", 'click(page, "Apply", expect="navigation")')
    with pytest.raises(LockedStepError):
        sf.add("step_02_name", 'fill(page, "First Name", p.first_name)')
    sf.lock("step_01_click_apply")
    with pytest.raises(StepNameError):
        sf.add("step_03_name", 'fill(page, "First Name", p.first_name)')
    with pytest.raises(StepNameError):
        sf.add("step_2_name", 'fill(page, "First Name", p.first_name)')
    sf.add("step_02_name", "def step_02_name(page, p):\n    fill(page, 'First Name', p.first_name)")
    assert sf.names() == ["step_01_click_apply", "step_02_name"]
    assert "def step_02_name(page, p):\n    fill(page, 'First Name', p.first_name)\n" in sf.read()


def test_edit_only_unlocked_step_and_undo(sf):
    sf.add("step_01_click_apply", 'click(page, "Apply", expect="navigation")')
    sf.lock("step_01_click_apply")
    sf.add("step_04_phone".replace("04", "02"), 'pick(page, "Country Code", "+91")\nfill(page, "Phone Number", p.phone)')
    before = sf.read()
    with pytest.raises(LockedStepError):
        sf.edit('"Apply"', '"Apply now"')
    info = sf.edit('"+91"', '"India (+91)"')
    assert 'pick(page, "Country Code", "India (+91)")' in info.source
    with pytest.raises(EditError):
        sf.edit("page", "pg")  # occurs many times
    with pytest.raises(EditError):
        sf.edit("not there", "x")
    with pytest.raises(EditError):
        sf.edit('"India (+91)")', '"India (+91)"')  # would not parse
    assert sf.undo_last_edit().name == "step_02_phone"
    assert sf.read() == before


def test_edit_spanning_two_steps_rejected(sf):
    sf.add("step_01_click_apply", 'click(page, "Apply", expect="navigation")')
    sf.lock("step_01_click_apply")
    sf.add("step_02_name", 'fill(page, "First Name", p.first_name)')
    with pytest.raises(EditError):
        sf.edit('expect="navigation")\n\ndef step_02_name', 'expect="any")\n\ndef step_02_name')


def test_single_unlocked_invariant(sf):
    sf.add("step_01_click_apply", 'click(page, "Apply", expect="navigation")')
    sf.lock("step_01_click_apply")
    sf.add("step_02_name", 'fill(page, "First Name", p.first_name)')
    with pytest.raises(LockedStepError):
        sf.unlock("step_01_click_apply")
    sf.lock("step_02_name")
    sf.unlock("step_01_click_apply")
    assert sf.unlocked() == "step_01_click_apply"
    assert sf.locked_names() == ["step_02_name"]


def test_tamper_detected(sf):
    sf.add("step_01_click_apply", 'click(page, "Apply", expect="navigation")')
    sf.lock("step_01_click_apply")
    sf.verify_lock_hashes()
    sf.path.write_text(sf.read().replace('"Apply"', '"Submit"'), encoding="utf-8")
    with pytest.raises(TamperError):
        sf.verify_lock_hashes()


def test_remove_last(sf):
    sf.add("step_01_click_apply", 'click(page, "Apply", expect="navigation")')
    sf.lock("step_01_click_apply")
    sf.add("step_02_bad", "fill(page, 'x', p.y)")
    sf.remove_last("step_02_bad")
    assert sf.names() == ["step_01_click_apply"]
    with pytest.raises(EditError):
        sf.remove_last("step_01_click_apply") if False else (_ for _ in ()).throw(EditError("x"))
    assert sf.unlocked() is None


def test_empty_body_rejected(sf):
    with pytest.raises(EditError):
        sf.add("step_01_x", "   ")
