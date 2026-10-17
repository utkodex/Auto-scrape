from pathlib import Path

import pytest

from core.profile import PROFILES_DIR, ProfileError, load_profile

REPO = Path(__file__).resolve().parent.parent


def test_dummy_profile_loads():
    p = load_profile("profiles/dummy.yaml")
    assert p.first_name == "Test"
    assert p.answers["willing_to_relocate"] == "Yes"
    assert "dummy_resume.pdf" in p.values()
    assert Path(p.resume).exists()
    assert "first_name" in p.keys()
    assert 'answers["willing_to_relocate"]' in p.keys()


def test_mask_values_identifying_only():
    p = load_profile("profiles/dummy.yaml")
    mv = p.mask_values()
    assert {"Test", "Candidate", "test.candidate@example.com", "+91 90000 00000", "Pune", "dummy_resume.pdf"} <= set(mv)
    for generic in ("India", "+91", "Yes", "No", "Python", "B.Tech", "2019"):
        assert generic not in mv


def test_values_longest_first_and_no_empty():
    p = load_profile("profiles/dummy.yaml")
    vals = p.values()
    assert all(v.strip() for v in vals)
    assert vals == sorted(vals, key=len, reverse=True)
    assert "+91 90000 00000" in vals


def test_resume_outside_profiles_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "first_name: A\nlast_name: B\nemail: a@example.com\nphone: '1'\n"
        "resume: ../my-docs/x.pdf\n",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError):
        load_profile(bad)


def test_missing_resume_rejected(tmp_path):
    bad = tmp_path / "bad2.yaml"
    bad.write_text(
        "first_name: A\nlast_name: B\nemail: a@example.com\nphone: '1'\n"
        f"resume: {PROFILES_DIR.name}/does_not_exist.pdf\n",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError):
        load_profile(bad)


def test_dummy_profile_has_apply_form_extras():
    # SuccessFactors apply forms ask for address, employer, consent-style questions; the dummy
    # profile must carry fake values for all of them and expose the keys to the model.
    p = load_profile("profiles/dummy.yaml")
    for key in ("address_line1", "postal_code", "state", "current_employer", "availability", "how_heard",
                "cover_letter", 'answers["consent_data_processing"]'):
        assert key in p.keys(), key
    assert p.address_line1 and p.postal_code and p.current_employer
    assert "1 Test Street" in p.mask_values() and "Example Corp" in p.mask_values()
    assert "Maharashtra" not in p.mask_values()  # generic value, appears in option lists
