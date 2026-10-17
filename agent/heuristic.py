"""No-LLM baseline: turn observe.suggested_next into a helper call by rule.

Used by `bench --heuristic` and as the documented baseline (.claude/specs/03 §6.2).
"""

from __future__ import annotations

import re

from agent.tools import ToolCall
from core.observe import Field, ObserveResult
from core.profile import Profile

LABEL_TO_KEY: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^first\s*name", re.I), "first_name"),
    (re.compile(r"^(last|family|sur)\s*name", re.I), "last_name"),
    (re.compile(r"^full\s*name|^name$", re.I), "first_name"),
    (re.compile(r"e-?mail", re.I), "email"),
    (re.compile(r"country\s*code|dial", re.I), "phone_country_code"),
    (re.compile(r"phone|mobile|contact\s*number", re.I), "phone"),
    (re.compile(r"^country$|country\s*of\s*residence", re.I), "country"),
    (re.compile(r"^city|location|town", re.I), "city"),
    (re.compile(r"current\s*(title|role|designation)|job\s*title", re.I), "current_title"),
    (re.compile(r"experience", re.I), "total_experience_years"),
    (re.compile(r"notice", re.I), "notice_period_days"),
    (re.compile(r"current\s*(ctc|salary)", re.I), "current_ctc"),
    (re.compile(r"expected\s*(ctc|salary)", re.I), "expected_ctc"),
    (re.compile(r"degree|qualification|education", re.I), "highest_degree"),
    (re.compile(r"graduat|passing\s*year", re.I), "graduation_year"),
    (re.compile(r"linkedin", re.I), "linkedin"),
    (re.compile(r"resume|cv\b|curriculum", re.I), "resume"),
]
ANSWER_KEYS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"relocat", re.I), "willing_to_relocate"),
    (re.compile(r"authori[sz]", re.I), "work_authorization_india"),
    (re.compile(r"sponsor", re.I), "sponsorship_required"),
]


def profile_key_for(label: str, profile: Profile) -> str | None:
    for rx, key in LABEL_TO_KEY:
        if rx.search(label):
            return f"p.{key}"
    for rx, key in ANSWER_KEYS:
        if rx.search(label) and key in profile.answers:
            return f'p.answers["{key}"]'
    return None


def _slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return (s or "step")[:30]


def _code_for(field: Field, profile: Profile) -> str | None:
    key = profile_key_for(field.label, profile)
    if field.kind in ("text", "textarea", "date"):
        return f'fill(page, "{field.label}", {key})' if key else None
    if field.kind == "file":
        return f'upload(page, "{field.label}", p.resume)'
    if field.kind in ("select", "combobox"):
        if not key:
            return None
        value = getattr(profile, key[2:], None) if key.startswith("p.") and "answers" not in key else None
        if field.options and value is not None:
            match = next((o for o in field.options if str(value).lower() in o.lower()), None)
            if match and not match.startswith("+"):
                return f'pick(page, "{field.label}", "{match}")'
        return f'pick(page, "{field.label}", {key})'
    if field.kind in ("radio", "checkbox"):
        for rx, akey in ANSWER_KEYS:
            if rx.search(field.label) and akey in profile.answers:
                return f'check(page, "{field.label}", p.answers["{akey}"])'
        return None
    return None


class HeuristicLLM:
    """Drop-in for agent.llm.LLM that answers from rules only (bench --heuristic)."""

    def __init__(self) -> None:
        self.last_usage = (0, 0, True)
        self.last_seconds = 0.0
        self.last_raw = ""
        self.calls = 0
        self.supports_tools = True
        self.system_content = "heuristic"

    def call(self, task) -> ToolCall:
        from agent.llm import NoToolCall

        self.calls += 1
        if task.kind != "add":
            raise NoToolCall("heuristic baseline cannot fix steps")
        tc = suggest_step(task.observe, _profile_from_keys(task), len(task.done_steps) + 1)
        if tc is None:
            raise NoToolCall("heuristic baseline has no rule for this field")
        self.last_raw = f"{tc.name} {tc.args}"
        return tc


def _profile_from_keys(task) -> Profile:
    # The heuristic only needs the answer keys; values are resolved at run time via p.<key>.
    answers = {k[len('answers["'):-2]: "" for k in task.profile_keys if k.startswith("answers[")}
    return Profile(first_name="", last_name="", email="", phone="", resume="profiles/dummy_resume.pdf", answers=answers)


def suggest_step(observe: ObserveResult, profile: Profile, next_index: int) -> ToolCall | None:
    sn = observe.suggested_next
    name_prefix = f"step_{next_index:02d}_"
    if sn.action == "click" and sn.targets:
        target = sn.targets[0]
        expect = "navigation" if target.lower().startswith("apply") else "section"
        return ToolCall("add_step", {"name": name_prefix + _slug(target),
                                     "code": f'click(page, "{target}", expect="{expect}")'})
    if sn.action in ("fill", "fill_group") and sn.targets:
        lines = []
        for label in sn.targets:
            f = observe.field(label)
            if f is None:
                return None
            code = _code_for(f, profile)
            if code is None:
                return None
            lines.append(code)
        return ToolCall("add_step", {"name": name_prefix + _slug(sn.targets[-1]), "code": "\n".join(lines)})
    return None
