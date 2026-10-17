"""Scripted stand-in for agent.llm.LLM so the loop can be tested offline.

A script is consumed in order. Each item declares what task it expects (kind, suggested targets,
or the step being fixed); a mismatch raises NoToolCall so a drifting loop fails loudly.
Scenarios follow .claude/specs/03.build-loop-and-eval.md §8.2.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.llm import NoToolCall, TaskBlock
from agent.tools import ToolCall
from core.observe import approx_tokens

MASK_TEST_STRINGS = ["Test Candidate", "+91 90000 00000", "password=", "cookie:"]


@dataclass
class ScriptItem:
    kind: str  # add | fix
    response: ToolCall
    targets: tuple[str, ...] | None = None  # for add: expected suggested_next.targets
    step: str | None = None  # for fix: expected fix_step
    repeat: bool = False  # stays usable after matching (for "always fail" loops)


def add(name: str, code: str, targets: tuple[str, ...] | None = None) -> ScriptItem:
    return ScriptItem("add", ToolCall("add_step", {"name": name, "code": code}), targets=targets)


def fix(step: str, old: str, new: str, repeat: bool = False) -> ScriptItem:
    return ScriptItem("fix", ToolCall("edit_file", {"path": "steps.py", "old": old, "new": new}), step=step, repeat=repeat)


def raw(kind: str, call: ToolCall, targets=None, step=None) -> ScriptItem:
    return ScriptItem(kind, call, targets=targets, step=step)


class FakeLLM:
    def __init__(self, script: list[ScriptItem]) -> None:
        self.script = list(script)
        self.pos = 0
        self.calls = 0
        self.last_usage: tuple[int, int, bool] = (0, 0, True)
        self.last_seconds = 0.0
        self.last_raw = ""
        self.supports_tools = True
        self.system_content = "fake"
        self.tasks: list[TaskBlock] = []

    def call(self, task: TaskBlock) -> ToolCall:
        self.calls += 1
        self.tasks.append(task)
        if self.pos >= len(self.script):
            raise NoToolCall(f"script exhausted at call {self.calls} (task={task.kind}, targets={task.observe.suggested_next.targets}, fix_step={task.fix_step})")
        item = self.script[self.pos]
        if item.kind != task.kind:
            raise NoToolCall(f"script expected a '{item.kind}' task but got '{task.kind}' (targets={task.observe.suggested_next.targets}, fix_step={task.fix_step}, error={task.last_error})")
        if item.kind == "add" and item.targets is not None and tuple(task.observe.suggested_next.targets) != tuple(item.targets):
            raise NoToolCall(f"script expected targets {item.targets} but page suggests {task.observe.suggested_next.targets}")
        if item.kind == "fix" and item.step is not None and task.fix_step != item.step:
            raise NoToolCall(f"script expected to fix {item.step} but loop asks for {task.fix_step}")
        if not item.repeat:
            self.pos += 1
        resp = item.response
        self.last_raw = f"{resp.name} {resp.args}"
        self.last_usage = (approx_tokens(task.to_json()), approx_tokens(self.last_raw), True)
        self.last_seconds = 0.01
        return ToolCall(resp.name, dict(resp.args))


# --------------------------------------------------------------------------------------------
# Scenario scripts (fixture form). Targets mirror core.observe.suggested_next on each page.
# --------------------------------------------------------------------------------------------
_APPLY = add("step_01_click_apply", 'click(page, "Apply", expect="navigation")', ("Apply",))
_NAME = add("step_02_name", 'fill(page, "First Name", p.first_name)\nfill(page, "Last Name", p.last_name)', ("First Name",))
_EMAIL = add("step_03_email", 'fill(page, "Email", p.email)', ("Email",))
_PHONE_OK = add("step_04_phone", 'pick(page, "Country Code", "India (+91)")\nfill(page, "Phone Number", p.phone)', ("Country Code", "Phone Number"))
_PHONE_BAD = add("step_04_phone", 'pick(page, "Country Code", "+91")\nfill(page, "Phone Number", p.phone)', ("Country Code", "Phone Number"))
_RESUME = add("step_05_resume", 'wait_for(page, "Resume")\nupload(page, "Resume", p.resume)', ("Resume",))
_NEXT1 = add("step_06_next", 'click(page, "Next", expect="section")', ("Next",))
_COUNTRY = add("step_07_country", 'pick(page, "Country", p.country)', ("Country",))
_CITY = add("step_08_city", 'fill(page, "City", p.city)', ("City",))
_RELOCATE = add("step_09_relocate", 'check(page, "Willing to relocate", p.answers["willing_to_relocate"])', ("Willing to relocate",))
_AUTH = add("step_10_authorization", 'check(page, "Work authorization in India", p.answers["work_authorization_india"])', ("Next",))
_NOTICE = add("step_11_notice", 'fill(page, "Notice period (days)", p.notice_period_days)', ("Next",))
_NEXT2 = add("step_12_next", 'click(page, "Next", expect="navigation")', ("Next",))

HAPPY = [_APPLY, _NAME, _EMAIL, _PHONE_OK, _RESUME, _NEXT1, _COUNTRY, _CITY, _RELOCATE, _AUTH, _NOTICE, _NEXT2]

PHONE_FIX = [_APPLY, _NAME, _EMAIL, _PHONE_BAD,
             fix("step_04_phone", '"+91"', '"India (+91)"'),
             _RESUME, _NEXT1, _COUNTRY, _CITY, _RELOCATE, _AUTH, _NOTICE, _NEXT2]

LOCKED_EDIT = [_APPLY, _NAME, _EMAIL, _PHONE_BAD,
               fix("step_04_phone", "p.first_name", "p.last_name"),  # inside locked step_02 → rejected
               fix("step_04_phone", '"+91"', '"India (+91)"'),
               _RESUME, _NEXT1, _COUNTRY, _CITY, _RELOCATE, _AUTH, _NOTICE, _NEXT2]

# step_05 first targets a label that does not exist; the fix is legal and passes on its own.
# The regression itself is injected by the test (monkeypatched replay failing step_02 once) because
# locked steps are immutable and the fixture is deterministic, so a real regression cannot occur.
REGRESSION_UNDO = [_APPLY, _NAME, _EMAIL, _PHONE_OK,
                   add("step_05_resume", 'upload(page, "CV", p.resume)', ("Resume",)),
                   fix("step_05_resume", '"CV"', '"Resume"'),  # first attempt: regression injected → undo
                   fix("step_05_resume", '"CV"', '"Resume"'),  # second attempt: clean
                   _NEXT1, _COUNTRY, _CITY, _RELOCATE, _AUTH, _NOTICE, _NEXT2]

ALWAYS_FAIL = [_APPLY, _NAME, _EMAIL, _PHONE_BAD,
               fix("step_04_phone", '"+91"', '"+92"'),
               fix("step_04_phone", '"+92"', '"+91"'),
               fix("step_04_phone", '"+91"', '"+92"'),
               fix("step_04_phone", '"+92"', '"+91"'),
               fix("step_04_phone", '"+91"', '"+92"'),
               fix("step_04_phone", '"+92"', '"+91"'),
               fix("step_04_phone", '"+91"', '"+92"'),
               fix("step_04_phone", '"+92"', '"+91"', repeat=True)]

SUBMIT_ATTEMPT = [_APPLY, _NAME, _EMAIL, _PHONE_OK, _RESUME,
                  add("step_06_submit", 'click(page, "Submit application")', ("Next",)),  # sandbox rejects
                  _NEXT1, _COUNTRY, _CITY, _RELOCATE, _AUTH, _NOTICE, _NEXT2]

LOGIN = list(HAPPY)        # the login page is handled by the harness (ask_human), not the model
POPUP = list(HAPPY)        # popup Apply is handled by click()/Browser
MANY_FIELDS = list(HAPPY)  # ?many=1 only changes observe size

SCRIPTS: dict[str, list[ScriptItem]] = {
    "happy": HAPPY,
    "phone_fix": PHONE_FIX,
    "locked_edit": LOCKED_EDIT,
    "regression_undo": REGRESSION_UNDO,
    "always_fail": ALWAYS_FAIL,
    "submit_attempt": SUBMIT_ATTEMPT,
    "login": LOGIN,
    "popup": POPUP,
    "many_fields": MANY_FIELDS,
}
