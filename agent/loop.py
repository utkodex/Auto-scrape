"""The build loop: observe → propose one step → run it → lock or fix → … → full test → report.

Contracts: .claude/specs/03.build-loop-and-eval.md §6.4 (states, invariants), §6.6 (stop conditions,
retry policy D4, regression + auto-undo), §6.3 (approach switch). Decisions in 01.overview.md §13.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agent.config import Settings
from agent.llm import NoToolCall, TaskBlock
from agent.runner import CheckResult, StepResult, replay, run_checks, run_step
from agent.steps_file import STEP_NAME_RE, StepsFile
from agent.tools import Dispatcher, ToolCall, ToolContext
from core.observe import ObserveResult

MUTATING = ("add_step", "edit_file")
LOOKUPS = ("observe", "read_file", "note", "run_step", "run_checks")
STOP_WORDS = ("stop", "abort", "quit", "cancel")


class LoopInvariantError(AssertionError):
    pass


@dataclass
class LoopOutcome:
    status: str  # DONE | STOP | ASK
    reason: str = ""
    report_path: Path | None = None
    rounds: int = 0
    flags: list[dict] = field(default_factory=list)
    check: CheckResult | None = None


@dataclass
class LoopState:
    rounds: int = 0
    tries: dict[str, int] = field(default_factory=dict)
    same_error: dict[str, int] = field(default_factory=dict)
    replayed_on_start: bool = False
    human_noops: int = 0  # consecutive login/captcha prompts after which the page was unchanged
    last_error_norm: dict[str, str] = field(default_factory=dict)
    parse_failures: int = 0
    lookups_in_a_row: int = 0
    unknown_pages: int = 0
    edits_accepted: int = 0
    regress_runs: int = 0
    switches: dict[str, int] = field(default_factory=dict)
    phase: str = "build"  # build | fulltest
    last_tool_result: dict | None = None


def normalise_error(text: str | None) -> str:
    s = (text or "").splitlines()[0] if text else ""
    s = re.sub(r"\d+", "#", s)
    s = re.sub(r"['\"][^'\"]*['\"]", "'…'", s)
    return re.sub(r"\s+", " ", s).strip().lower()[:160]


def label_from_failure(result: StepResult | None, error: str | None) -> str | None:
    """Best guess of the field label a failure is about (for a scoped re-observe)."""
    if result is not None and result.failed_check:
        m = re.search(r"['\"]([^'\"]{2,60})['\"]", result.failed_check)
        if m:
            return m.group(1)
    text = (error or "").splitlines()[0] if error else ""
    head = text.split(":")[0].strip()
    if 2 <= len(head) <= 60 and "'" not in head and '"' not in head and not head.lower().startswith(("no ", "helpererror", "error")):
        return head
    m = re.search(r"['\"]([^'\"]{2,60})['\"]", text)
    return m.group(1) if m else None


def _show(title: str, body: str) -> None:
    """Verbose console trace: what the model was sent and what it answered."""
    bar = "=" * 78
    print("\n".join(["", bar, f" {title}", bar, body, ""]), flush=True)


class BuildLoop:
    def __init__(
        self,
        settings: Settings,
        ctx: ToolContext,
        llm,
        job_url: str,
        ask_human: Callable[[str], str] | None = None,
        report_fn: Callable[["BuildLoop", LoopOutcome], Path | None] | None = None,
        use_suggest: bool = True,
        verbose: bool = False,
    ) -> None:
        self.verbose = verbose
        self.settings = settings
        self.ctx = ctx
        self.llm = llm
        self.job_url = job_url
        self.state = LoopState()
        self.flags: dict[str, dict] = {}
        self.report_fn = report_fn
        self.use_suggest = use_suggest
        self.ask_human_fn = ask_human or ctx.ask_human_fn or (lambda q: "")
        ctx.ask_human_fn = self._ask_human
        ctx.run_step_fn = lambda name: self._run_step(name).to_tool_result()
        ctx.run_checks_fn = lambda: self._run_checks().to_tool_result()
        self.dispatcher = Dispatcher(ctx)
        self.start_text = ctx.steps_file.read()
        self.t0 = time.monotonic()

    # -- shorthands ---------------------------------------------------------------------
    @property
    def sf(self) -> StepsFile:
        return self.ctx.steps_file

    @property
    def log(self):
        return self.ctx.log

    def _mode(self) -> str:
        return "test" if self.state.phase == "fulltest" else "build"

    # -- harness-side actions ------------------------------------------------------------
    def _observe(self, scope: str | None = None) -> ObserveResult:
        self.dispatcher.call(ToolCall("observe", {"scope": scope} if scope else {}), harness=True)
        obs = self.ctx.last_observe
        if obs.page_kind == "form":
            for f in obs.fields:
                if not f.required and not f.value and f.label not in self.flags:
                    self.flags[f.label] = {"label": f.label, "section": obs.section, "kind": f.kind, "url": obs.url}
        return obs

    def _run_step(self, name: str) -> StepResult:
        return run_step(self.ctx.browser, self.sf, name, self.ctx.profile, log=self.log, mode=self._mode(),
                        screenshots_dir=self.ctx.run_dir / "screenshots", round_no=self.ctx.round)

    def _run_checks(self) -> CheckResult:
        return run_checks(self.ctx.filler_dir, self.ctx.browser, self.ctx.profile, log=self.log, mode="test",
                          round_no=self.ctx.round)

    def _replay_all(self, upto_index: int) -> StepResult | None:
        self.state.regress_runs += 1
        return replay(self.ctx.browser, self.sf, upto_index, self.ctx.profile, self.job_url, log=self.log,
                      mode=self._mode(), round_no=self.ctx.round)

    def _note_unchanged_observe(self, before_snapshot, obs: ObserveResult, tool: str) -> None:
        """Tell the model when an observe returned the very page it already had (Qwen on the
        live HCLTech page called observe 15 times in a row without writing anything)."""
        if before_snapshot and getattr(obs, "snapshot", None) == before_snapshot:
            self.state.last_tool_result = {
                "ok": True,
                "note": f"observe returned the same page as before (snapshot unchanged). "
                        f"Do not observe again; call {tool} now.",
            }

    def _human_gate(self, kind: str, url: str) -> tuple[LoopOutcome | None, ObserveResult]:
        """A login or captcha page: ask the human, re-observe, and stop after three prompts that
        changed nothing (a non-interactive stdin answers "" forever)."""
        before = getattr(self.ctx.last_observe, "snapshot", None)
        answer = self._ask_human(f"The page needs you ({kind}). Log in or solve it in the browser window, "
                                 f"then press Enter. URL: {url}")
        if answer.lower() in STOP_WORDS:
            return self._stop(f"human stopped at {kind}"), self.ctx.last_observe
        obs = self._observe()
        if obs.page_kind == kind and getattr(obs, "snapshot", None) == before:
            self.state.human_noops += 1
            if self.state.human_noops >= 3:
                return self._stop(f"{kind} page unchanged after 3 prompts ({url}); log in in the browser "
                                  "window and run build again"), obs
        else:
            self.state.human_noops = 0
        return None, obs

    def _ask_human(self, question: str) -> str:
        t0 = time.monotonic()
        answer = (self.ask_human_fn(question) or "").strip()
        self.log.row("human", "ask_human", question, answer[:200], s=time.monotonic() - t0, round=self.ctx.round)
        return answer

    def _stop(self, reason: str) -> LoopOutcome:
        self.log.row("harness", "stop", "", reason, round=self.ctx.round)
        outcome = LoopOutcome(status="STOP", reason=reason, rounds=self.state.rounds, flags=self._flag_list())
        outcome.report_path = self._report(outcome)
        return outcome

    def _report(self, outcome: LoopOutcome) -> Path | None:
        if self.report_fn:
            try:
                return self.report_fn(self, outcome)
            except Exception as exc:  # noqa: BLE001
                self.log.row("harness", "report", "", f"report failed: {exc}")
        return None

    def _flag_list(self) -> list[dict]:
        text = self.sf.read()
        return [f for f in self.flags.values() if f'"{f["label"]}"' not in text and f"'{f['label']}'" not in text]

    # -- LLM round -------------------------------------------------------------------------
    def _ask_llm(self, task: TaskBlock) -> ToolCall | None:
        """One round. Returns the ToolCall, or None when the model produced no usable call."""
        if self.state.rounds >= self.settings.MAX_ROUNDS_PER_COMPANY:
            return None
        self.dispatcher.new_round()
        self.state.rounds += 1
        if self.state.last_tool_result:

            task.last_tool_result = json.loads(self.log.mask(json.dumps(self.state.last_tool_result)))
        self.state.last_tool_result = None
        if self.verbose:
            _show("TASK -> model", task.to_json())
        try:
            tc = self.llm.call(task)
        except NoToolCall as exc:
            if self.verbose:
                _show("MODEL (no usable tool call)", getattr(self.llm, "last_raw", "") or str(exc))
            tok_in, tok_out, _ = getattr(self.llm, "last_usage", (None, None, True))
            self.log.row("qwen", "no_tool_call", "", str(exc)[:160], tok_in=tok_in, tok_out=tok_out,
                         s=getattr(self.llm, "last_seconds", None), round=self.ctx.round)
            self.state.parse_failures += 1
            return None
        self.state.parse_failures = 0
        if self.verbose:
            a = dict(tc.args or {})
            code = a.pop("code", None)
            body = f"{tc.name} {json.dumps(a, ensure_ascii=False)}"
            if code:
                body += "\n" + code.rstrip()
            if tc.name == "edit_file":
                body = "\n".join(["edit_file", "--- old", str(a.get("old", "")), "+++ new", str(a.get("new", ""))])
            _show("MODEL -> tool call", body)
        return tc

    def _usage(self) -> tuple[int | None, int | None, float | None]:
        tok_in, tok_out, _est = getattr(self.llm, "last_usage", (None, None, True))
        return tok_in, tok_out, getattr(self.llm, "last_seconds", None)

    def _task(self, kind: str, obs: ObserveResult, instruction: str, **kw) -> TaskBlock:
        # Profile values never reach the prompt: mask free text the way the log does.
        for key in ("last_error", "similar_step", "fix_source"):
            if kw.get(key):
                kw[key] = self.log.mask(kw[key])
        return TaskBlock(kind=kind, observe=obs, done_steps=self.sf.names(),
                         profile_keys=self.ctx.profile.keys(), last_notes=[self.log.mask(n) for n in self.ctx.last_notes],
                         instruction=instruction, **kw)

    # -- main --------------------------------------------------------------------------------
    def run(self) -> LoopOutcome:
        self.sf.verify_lock_hashes()
        self.ctx.browser.open(self.job_url)
        while True:
            if self.state.rounds >= self.settings.MAX_ROUNDS_PER_COMPANY:
                return self._stop(f"limit: {self.settings.MAX_ROUNDS_PER_COMPANY} rounds for {self.ctx.company}")

            # steps.py already holds locked steps (second sample, or a resumed build): the page
            # is still the job link, so replay them all before asking for step N+1
            if not self.state.replayed_on_start and self.state.phase == "build":
                self.state.replayed_on_start = True
                names = self.sf.names()
                if names and self.sf.unlocked() is None:
                    res = self._replay_all(len(names))
                    if res is not None:
                        self.sf.unlock(res.step)
                        outcome = self._fix_loop(res.step, res, retry_mode="replay")
                        if outcome:
                            return outcome
                    continue

            # a step left unlocked by an earlier run (resume): replay to it first
            pending = self.sf.unlocked()
            if pending is not None and self.state.phase == "build":
                idx = int(STEP_NAME_RE.match(pending).group(1))
                res = self._replay_all(idx)
                if res is None:
                    self.sf.lock(pending)
                else:
                    outcome = self._fix_loop(res.step, res, retry_mode="replay")
                    if outcome:
                        return outcome
                continue

            if self.state.phase == "fulltest":
                outcome = self._full_test()
                if outcome:
                    return outcome
                continue

            obs = self._observe()
            kind = obs.page_kind
            if kind in ("login", "captcha"):
                outcome, _ = self._human_gate(kind, obs.url)
                if outcome:
                    return outcome
                continue
            if kind == "review":
                self.state.phase = "fulltest"
                continue
            if kind == "closed":
                return self._stop(f"job closed: the site says it no longer accepts applications ({obs.url}); "
                                  "pick another sample URL for this company")
            if kind == "unknown":
                self.state.unknown_pages += 1
                if self.state.unknown_pages >= 3:
                    answer = self._ask_human(f"I cannot recognise this page ({obs.url}). Navigate to the job or form and press Enter, or type stop.")
                    self.state.unknown_pages = 0
                    if answer.lower() in STOP_WORDS:
                        return self._stop("human stopped at unknown page")
                else:
                    self.ctx.browser.page.wait_for_timeout(1000)
                continue
            self.state.unknown_pages = 0

            outcome = self._propose(obs)
            if outcome:
                return outcome

    # -- PROPOSE ------------------------------------------------------------------------------
    def _propose(self, obs: ObserveResult) -> LoopOutcome | None:
        if self.sf.unlocked() is not None:
            raise LoopInvariantError("add phase entered with an unlocked step")
        next_index = len(self.sf.steps()) + 1
        last_error: str | None = None
        proposed = f"step_{next_index:02d}"
        while True:
            # the model's own observe may reveal a page the harness must handle (login, closed,
            # review): hand back to run(), which re-observes and acts
            if obs.page_kind in ("login", "captcha", "closed", "review"):
                return None
            # the instruction follows the *current* page: a late-rendered job page must not keep
            # the "no Next button on this form" wording from the first observe
            sn = obs.suggested_next
            if self.use_suggest:
                hint = f"suggested_next={sn.action} {sn.targets}"
            else:
                hint = "the next empty required field or the Next/Apply button"
            instruction = f"Write the next step for {hint} using add_step (name must start with step_{next_index:02d}_)."
            if sn.action == "stop":
                instruction = ("No required empty field and no Next button were found on this form page. Add the step "
                               f"that continues to the next page (name step_{next_index:02d}_...) or call ask_human.")
            if self.state.lookups_in_a_row >= 3:
                instruction += " You have done enough lookups; call add_step now."
            if self.state.parse_failures:
                instruction += " Reply with exactly one tool call and nothing else."
            tc = self._ask_llm(self._task("add", obs, instruction, last_error=last_error))
            if tc is None:
                if self.state.rounds >= self.settings.MAX_ROUNDS_PER_COMPANY:
                    return self._stop(f"limit: {self.settings.MAX_ROUNDS_PER_COMPANY} rounds for {self.ctx.company}")
                if self.state.parse_failures >= 3:
                    self.state.parse_failures = 0
                    self._count_try(proposed, "no tool call")
                    if self.state.tries[proposed] >= self.settings.MAX_TRIES_PER_STEP:
                        return self._stop(f"limit: {self.settings.MAX_TRIES_PER_STEP} tries on {proposed}")
                last_error = "Your reply contained no tool call."
                continue
            if tc.name == "edit_file":
                last_error = "No step is unlocked. Use add_step to write the next step."
                self.log.row("qwen", "edit_file", tc.short(), f"error · {last_error}", *(), round=self.ctx.round)
                self._count_try(proposed, last_error)
                continue
            if tc.name == "ask_human":
                res = self.dispatcher.call(tc, usage=self._usage())
                if (res.get("answer") or "").lower() in STOP_WORDS:
                    return self._stop("human stopped")
                if not (res.get("answer") or "").strip():
                    res["note"] = "the human gave no answer; do not ask again, write the step with add_step"
                    self._count_try(proposed, "asked the human, no answer")
                    if self.state.tries[proposed] >= self.settings.MAX_TRIES_PER_STEP:
                        return self._stop(f"limit: {self.settings.MAX_TRIES_PER_STEP} tries on {proposed}")
                self.state.last_tool_result = res
                obs = self._observe()
                continue
            if tc.name in LOOKUPS:
                res = self.dispatcher.call(tc, usage=self._usage())
                self.state.lookups_in_a_row += 1
                if tc.name == "observe" and res.get("ok"):
                    before = getattr(obs, "snapshot", None)
                    obs = self.ctx.last_observe
                    self._note_unchanged_observe(before, obs, "add_step")
                else:
                    self.state.last_tool_result = res
                if self.state.lookups_in_a_row >= 5:
                    self._count_try(proposed, "too many lookups without add_step")
                    self.state.lookups_in_a_row = 0
                    if self.state.tries[proposed] >= self.settings.MAX_TRIES_PER_STEP:
                        return self._stop(f"limit: {self.settings.MAX_TRIES_PER_STEP} tries on {proposed}")
                continue
            # add_step
            self.state.lookups_in_a_row = 0
            name = str(tc.args.get("name", proposed))
            res = self.dispatcher.call(tc, usage=self._usage())
            if not res.get("ok"):
                last_error = res.get("error")
                self._count_try(name if STEP_NAME_RE.match(name) else proposed, last_error)
                key = name if STEP_NAME_RE.match(name) else proposed
                if self.state.tries[key] >= self.settings.MAX_TRIES_PER_STEP:
                    return self._stop(f"limit: {self.settings.MAX_TRIES_PER_STEP} tries on {key}")
                continue
            name = res["step"]
            self._count_try(name, None)
            result = self._run_step(name)
            if result.status == "PASS":
                self.sf.lock(name)
                return None
            return self._fix_loop(name, result, retry_mode=result.retry_mode or "in_place")

    def _count_try(self, name: str, error: str | None) -> None:
        self.state.tries[name] = self.state.tries.get(name, 0) + 1
        if error is None:
            return
        norm = normalise_error(error)
        if norm and norm == self.state.last_error_norm.get(name):
            self.state.same_error[name] = self.state.same_error.get(name, 0) + 1
        else:
            self.state.same_error[name] = 1
        self.state.last_error_norm[name] = norm

    # -- FIX ------------------------------------------------------------------------------------
    def _fix_loop(self, name: str, result: StepResult | None, retry_mode: str, error: str | None = None) -> LoopOutcome | None:
        """Repair `name` until it passes (returns None, step locked) or a stop condition fires."""
        if self.sf.unlocked() != name:
            if self.sf.unlocked() is not None:
                raise LoopInvariantError(f"two unlocked steps: {self.sf.unlocked()} and {name}")
            self.sf.unlock(name)
        idx = int(STEP_NAME_RE.match(name).group(1))
        last_error = (result.error if result else None) or error or "step failed"
        if result is not None:
            self._register_error(name, result.error)
        obs = self.ctx.last_observe or self._observe()
        similar: str | None = None
        while True:
            # the harness, not the model, deals with login / captcha / closed pages mid-fix
            if obs.page_kind in ("login", "captcha"):
                outcome, obs = self._human_gate(obs.page_kind, obs.url)
                if outcome:
                    return outcome
                continue
            if obs.page_kind == "closed":
                return self._stop(f"job closed: the site says it no longer accepts applications ({obs.url}); "
                                  "pick another sample URL for this company")
            tries = self.state.tries.get(name, 0)
            same = self.state.same_error.get(name, 0)
            if tries >= self.settings.MAX_TRIES_PER_STEP:
                return self._stop(f"limit: {self.settings.MAX_TRIES_PER_STEP} tries on {name}")
            if self.state.rounds >= self.settings.MAX_ROUNDS_PER_COMPANY:
                return self._stop(f"limit: {self.settings.MAX_ROUNDS_PER_COMPANY} rounds for {self.ctx.company}")
            if same == self.settings.SAME_ERROR_SWITCH and self.state.switches.get(name, 0) < 1:
                self.state.switches[name] = 1
                label = label_from_failure(result, last_error)
                obs = self._observe(scope=label) if label else self._observe()
                similar = self._similar_step(name)
                self.log.row("harness", "switch", name, f"same error {same}× · re-observed scope={label!r} · example={'yes' if similar else 'no'}", round=self.ctx.round)
            if same >= self.settings.SAME_ERROR_ASK and self.state.switches.get(name, 0) < 2:
                self.state.switches[name] = 2
                answer = self._ask_human(f"{name} keeps failing the same way ({same}×): {last_error.splitlines()[0]}\nSource:\n{self.sf.source_of(name)}\nType a hint, press Enter to let it keep trying, or type stop.")
                if answer.lower() in STOP_WORDS:
                    return self._stop(f"human stopped on {name}")
                if answer:
                    self.ctx.last_notes.append(f"human hint: {answer[:150]}")
                    self.ctx.last_notes = self.ctx.last_notes[-3:]

            instruction = f"Fix {name} using edit_file: replace only the wrong text (old → new) so the step passes. Do not rewrite the step."
            if self.state.parse_failures:
                instruction += " Reply with exactly one tool call and nothing else."
            task = self._task("fix", obs, instruction, last_error=last_error, fix_step=name,
                              fix_source=self.sf.source_of(name), similar_step=similar)
            tc = self._ask_llm(task)
            if tc is None:
                if self.state.rounds >= self.settings.MAX_ROUNDS_PER_COMPANY:
                    return self._stop(f"limit: {self.settings.MAX_ROUNDS_PER_COMPANY} rounds for {self.ctx.company}")
                if self.state.parse_failures >= 3:
                    self.state.parse_failures = 0
                    self._count_try(name, "no tool call")
                continue
            if tc.name == "add_step":
                last_error = f"{name} is unlocked and failing; fix it with edit_file before adding a new step."
                self.log.row("qwen", "add_step", tc.short(), f"error · {last_error}", round=self.ctx.round)
                self._count_try(name, last_error)
                continue
            if tc.name == "ask_human":
                res = self.dispatcher.call(tc, usage=self._usage())
                if (res.get("answer") or "").lower() in STOP_WORDS:
                    return self._stop("human stopped")
                if not (res.get("answer") or "").strip():
                    res["note"] = "the human gave no answer; do not ask again, fix the step with edit_file"
                    self._count_try(name, "asked the human, no answer")
                self.state.last_tool_result = res
                continue
            if tc.name in LOOKUPS:
                res = self.dispatcher.call(tc, usage=self._usage())
                self.state.lookups_in_a_row += 1
                if tc.name == "observe" and res.get("ok"):
                    before = getattr(obs, "snapshot", None)
                    obs = self.ctx.last_observe
                    self._note_unchanged_observe(before, obs, "edit_file")
                elif tc.name == "run_step" and res.get("status") == "PASS":
                    # the model re-ran the step and it passed: confirm with a replay and lock
                    rep = self._replay_all(idx)
                    if rep is None:
                        self.sf.lock(name)
                        return None
                    last_error = rep.error
                    self._register_error(name, rep.error)
                else:
                    self.state.last_tool_result = res
                if self.state.lookups_in_a_row >= 5:
                    self.state.lookups_in_a_row = 0
                    self._count_try(name, "too many lookups without edit_file")
                continue
            # edit_file
            self.state.lookups_in_a_row = 0
            res = self.dispatcher.call(tc, usage=self._usage())
            if not res.get("ok"):
                last_error = res.get("error") or "edit rejected"
                self._count_try(name, last_error)
                continue
            self.state.edits_accepted += 1
            self._count_try(name, None)
            if retry_mode == "in_place":
                quick = self._run_step(name)
                if quick.status == "FAIL":
                    result, last_error = quick, quick.error
                    self._register_error(name, quick.error)
                    retry_mode = quick.retry_mode or "in_place"
                    continue
            # REGRESS: replay every step from the job link, including this one
            rep = self._replay_all(idx)
            if rep is None:
                self.sf.lock(name)
                return None
            if rep.step != name:
                self.sf.undo_last_edit()
                self.state.edits_accepted -= 0  # the edit is reverted but still counted as attempted
                self.log.row("harness", "undo", name, f"regressed {rep.step} · reverted edit", round=self.ctx.round)
                last_error = f"Your edit to {name} made the earlier step {rep.step} fail: {rep.error}. The edit was reverted. Fix {name} differently."
                self._register_error(name, last_error)
                retry_mode = "replay"
                result = None
                continue
            result, last_error = rep, rep.error
            self._register_error(name, rep.error)
            retry_mode = "replay"

    def _register_error(self, name: str, error: str | None) -> None:
        norm = normalise_error(error)
        if norm and norm == self.state.last_error_norm.get(name):
            self.state.same_error[name] = self.state.same_error.get(name, 0) + 1
        else:
            self.state.same_error[name] = 1
        self.state.last_error_norm[name] = norm

    def _similar_step(self, failing: str) -> str | None:
        src = self.sf.source_of(failing)
        helpers = re.findall(r"\b(click|fill|pick|upload|check)\(", src)
        if not helpers:
            return None
        primary = helpers[0]
        for s in self.sf.steps():
            if s.locked and f"{primary}(" in s.source:
                return s.source.strip()
        example = self.ctx.example_path or (Path(__file__).resolve().parent / "prompts" / "example_steps.py")
        try:
            text = Path(example).read_text(encoding="utf-8")
        except OSError:
            return None
        for block in re.split(r"\n(?=def )", text):
            if block.startswith("def ") and f"{primary}(" in block:
                return block.strip()
        return None

    # -- FULL TEST --------------------------------------------------------------------------------
    def _full_test(self) -> LoopOutcome | None:
        if self.sf.unlocked() is not None:
            raise LoopInvariantError("full test entered with an unlocked step")
        res = self._run_checks()
        if res.status == "PASS":
            self.log.row("harness", "done", self.ctx.company, f"PASS {res.passed}/{res.total} · awaiting human review", round=self.ctx.round)
            outcome = LoopOutcome(status="DONE", reason=f"PASS {res.passed}/{res.total}", rounds=self.state.rounds,
                                  flags=self._flag_list(), check=res)
            outcome.report_path = self._report(outcome)
            return outcome
        step = res.step
        if step is None or step not in self.sf.names():
            step = self._step_for_line(res.error) or (self.sf.names()[-1] if self.sf.names() else None)
        if step is None:
            return self._stop(f"full test failed with no step to fix: {res.error}")
        self.log.row("harness", "unlock", step, f"full test failed · {res.stage}", round=self.ctx.round)
        self.ctx.last_observe = None
        self.state.phase = "build"
        try:
            outcome = self._fix_loop(step, None, retry_mode="replay", error=res.error)
        finally:
            self.state.phase = "fulltest"
        return outcome

    def _step_for_line(self, error: str | None) -> str | None:
        m = re.search(r"line (\d+)", error or "")
        if not m:
            return None
        line = int(m.group(1))
        for s in self.sf.steps():
            if s.lineno <= line <= s.end_lineno:
                return s.name
        return None
