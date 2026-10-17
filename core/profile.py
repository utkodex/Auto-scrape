"""Candidate profile loading and validation.

The profile is the only source of values written into forms. Steps access it as
``p.first_name`` or ``p.answers["willing_to_relocate"]``. ``values()`` feeds the
log masker so none of these strings ever appear in logs or reports.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO_ROOT / "profiles"


class ProfileError(Exception):
    """Raised when a profile file is missing, malformed, or unsafe to use."""


class Profile(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str
    phone_country_code: str = ""
    country: str = ""
    city: str = ""
    current_title: str = ""
    total_experience_years: int = 0
    notice_period_days: int = 0
    current_ctc: str = ""
    expected_ctc: str = ""
    highest_degree: str = ""
    graduation_year: int = 0
    skills: list[str] = Field(default_factory=list)
    linkedin: str = ""
    # Extra fields SuccessFactors apply forms commonly ask for (all optional).
    address_line1: str = ""
    postal_code: str = ""
    state: str = ""
    nationality: str = ""
    gender: str = ""
    current_employer: str = ""
    availability: str = ""  # e.g. "Immediate" or "30 days"
    how_heard: str = ""  # "How did you hear about us?"
    cover_letter: str = ""
    resume: str
    answers: dict[str, str] = Field(default_factory=dict)

    def values(self) -> list[str]:
        """All non-empty string values plus the resume file name (for masking)."""
        out: list[str] = []
        for name, value in self.model_dump().items():
            if name == "answers":
                out.extend(str(v) for v in value.values() if str(v).strip())
            elif name == "skills":
                out.extend(str(v) for v in value if str(v).strip())
            elif isinstance(value, str) and value.strip():
                out.append(value)
        out.append(Path(self.resume).name)
        # longest first so masking replaces full strings before substrings
        return sorted(set(out), key=len, reverse=True)

    # Values that identify the candidate. Generic values (country, dial code, skills, degree, Yes/No,
    # years) legitimately appear in page option lists and are not masked.
    IDENTIFYING: ClassVar[tuple[str, ...]] = ("first_name", "last_name", "email", "phone", "city", "current_title",
                                              "current_ctc", "expected_ctc", "linkedin", "address_line1",
                                              "postal_code", "current_employer", "cover_letter")

    def mask_values(self) -> list[str]:
        out = [str(getattr(self, k)) for k in self.IDENTIFYING if str(getattr(self, k) or "").strip()]
        out = [v for v in out if len(v) >= 3 and v not in ("0", "N/A")]
        out.append(Path(self.resume).name)
        return sorted(set(out), key=len, reverse=True)

    def keys(self) -> list[str]:
        """Profile keys the LLM may reference (``p.<key>`` and ``p.answers[...]``)."""
        base = [k for k in type(self).model_fields if k not in {"answers"}]
        return base + [f'answers["{k}"]' for k in self.answers]


def load_profile(path: str | Path) -> Profile:
    p = Path(path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    if not p.exists():
        raise ProfileError(f"profile not found: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        profile = Profile(**data)
    except Exception as exc:  # pydantic / yaml errors
        raise ProfileError(f"invalid profile {p.name}: {exc}") from exc

    resume = Path(profile.resume)
    if not resume.is_absolute():
        resume = REPO_ROOT / resume
    resume = resume.resolve()
    try:
        resume.relative_to(PROFILES_DIR.resolve())
    except ValueError:
        raise ProfileError(
            f"resume must live under {PROFILES_DIR.name}/ (got {profile.resume})"
        ) from None
    if not resume.exists():
        raise ProfileError(f"resume file not found: {resume}")
    profile.resume = str(resume)
    return profile
