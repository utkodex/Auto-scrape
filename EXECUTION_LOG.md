# Execution log — .claude/plans/01.plan.md

## 2026-10-15 09:00 — 01.plan / T1 scaffold
- Action: pyproject, .gitignore, .env.example, agent/config.py (pydantic-settings), package inits; `git init`; `.venv` (Python 3.14.5); `pip install -e .`; `playwright install chromium`.
- Verify: `Settings()` defaults assert → ok; `Settings(MODE='x')` → ValidationError.
- Plan change: none.

## 2026-10-15 09:05 — 01.plan / T2 dummy profile + loader
- Action: profiles/dummy.yaml, scripts/make_dummy_resume.py (reportlab), core/profile.py (Profile, load_profile, values(), mask_values(), keys()), tests/test_profile.py.
- Verify: `python scripts/make_dummy_resume.py` → 1843-byte PDF; `pytest tests/test_profile.py -q` → pass.
- Plan change: none.

## 2026-10-15 09:12 — 01.plan / T3 fixture site
- Action: tests/fixtures/sf_like/{common.js,job,apply_1,apply_2,review,login,submitted,ambiguous}.html + server.py; tests/conftest.py (`fixture_server`, session `browser`); tests/test_fixture_server.py.
- Verify: `pytest tests/test_fixture_server.py -q` → 9 passed; `start()` prints a 127.0.0.1 URL.
- Plan change: `tests/conftest.py` moved from T1 to T3 (validator: a file cannot be Modified before it exists). Git commits are NOT made by the executor: a user hook blocks `git add`; the user stages and commits at each checkpoint.

## 2026-10-15 09:20 — 01.plan / T4 browser wrapper
- Action: core/browser.py (persistent context, popup tracking, host allowlist with glob, probe JS, 6-rule classifier, detect_system, response status capture); tests/test_browser.py.
- Verify: `pytest tests/test_browser.py -q` → 7 passed.

## 2026-10-15 09:24 — 01.plan / T5 observe
- Action: core/observe.py (ObserveResult, trim rules, suggested_next with phone-pair detection, content-addressed cache, value masking); tests/test_observe.py.
- Fix: cache file was skipped on an in-memory hit → now written whenever missing.
- Verify: `pytest tests/test_observe.py -q` → 10 passed (≤ 1,500 tokens on the 120-field variant).

## 2026-10-15 09:30 — 01.plan / T6–T9 helpers, checks, log, submit
- Action: core/helpers.py (six helpers, expectations in a contextvar StepContext, submit guard, label matcher with AmbiguousLabel), core/checks.py (verify_expectations, lint, sandbox AST rules), core/log.py (jsonl+md, masking, summary), core/submit.py (guarded stub); tests for each + tests/data/steps_*.py.
- Verify: `pytest tests/test_helpers.py tests/test_checks.py tests/test_log.py tests/test_submit_isolation.py -q` → 37 passed.
- Plan change: `lint()` ignores F401 and I001 for generated files (the steps.py header imports all six helpers; import order is style, not correctness). Found by the T14 lint-stage test.

## 2026-10-15 09:40 — 01.plan / T10 + T14 steps file, runner
- Action: agent/steps_file.py (append-only, lock/unlock/undo/tamper), agent/runner.py (run_step, replay, run_checks), agent/prompts/example_steps.py (12-step fixture example); tests.
- Fix: a parameter named `round` shadowed the builtin; renamed `round_no`.
- Verify: `pytest tests/test_steps_file.py tests/test_runner.py tests/test_checks.py -q` → 29 passed; example passes `run_checks` 2/2 on the fixture.

## 2026-10-15 09:50 — 01.plan / T11–T13 tools, LLM client, fake LLM
- Action: agent/tools.py (8 schemas, dispatcher, read allowlist, one-mutation-per-round), agent/prompts/system.md (≤ 700 tokens), agent/llm.py (stable prefix, trimming, tool-call + fenced-JSON parsing, JSON-mode fallback, usage), tests/fake_llm.py (9 scripts); tests.
- Fix: steps.py header sorted alphabetically (ruff I001); system prompt trimmed under budget.
- Verify: `pytest tests/test_tools.py tests/test_llm_prompt.py -q` → pass; 9 fake scripts importable.

## 2026-10-15 10:10 — 01.plan / T15 build loop + heuristic
- Action: agent/loop.py (state machine, counters, D4 retry, REGRESS = replay 1..N after every accepted edit, auto-undo, approach switch with scoped observe + similar step, ask_human on login/captcha/limits, invariants), agent/heuristic.py (rule baseline + HeuristicLLM), tests/test_loop_fixture.py (10 tests over the 9 scenarios).
- Fix: approach-switch label extraction preferred the quoted option over the field label → `pick` errors now start with the label and `label_from_failure` prefers the head. Profile values are masked in observe values, task-block text and tool results so they never reach prompts or the probe cache.
- Verify: `pytest tests/test_loop_fixture.py -q` → 10 passed. happy: 12 rounds, first-try 100 %; phone_fix: 1 edit, in_place retry, 1 regression replay; always_fail: switch at 3, ask at 5, stop at 8 tries; submit attempt rejected by sandbox; login resumes after human; popup Apply handled; many-fields observe ≤ 1,500 tokens.
- Plan change: the "regression" scenario injects the earlier-step failure by monkeypatching `agent.loop.replay` (as the plan anticipated), because locked steps are immutable and the fixture is deterministic.

## 2026-10-15 10:25 — 01.plan / T16–T18 report, CLI, safety gate
- Action: agent/report.py (metrics, report.html, metrics.json, secret scan), agent/prompts/report.html.j2, agent/cli.py + agent/__main__.py (build/test/bench/report, URL normalisation, filler scaffolding, exit codes), fillers/README.md, tests/test_report.py, tests/test_safety.py, scripts/check.ps1.
- Fix: secret scan flagged generic profile values (`India`, `Yes`, `+91`) that legitimately appear in dropdown option lists → masking now uses `Profile.mask_values()` (names, email, phone, city, title, salary, LinkedIn, resume name).
- Verify (T17): `python -m agent build "https://x.example/jobs/1&amp;x=1" --dry-run-url` → `https://x.example/jobs/1&x=1`; `python -m agent build http://127.0.0.1:5055/job.html --company fixture --llm fake:happy --headless` → exit 0, `DONE: PASS 1/1`, steps.py + report.html written, manifest created; with a second sample `python -m agent test fixture --headless` → `PASS 2/2`, exit 0; `python -m agent report runs/fixture/<ts>` regenerates the report.
- Verify (T18): `powershell -File scripts/check.ps1` → ruff clean, safety group green, `116 passed in 75.76s`, 86 s wall-clock, exit 0. No GPU, no network.
- Plan change: suite took 179 s single-threaded → `Browser.settle` waits for `load` (not `networkidle`), post-click pause 300→150 ms, one helper test uses a 1 s timeout, and `check.ps1` runs pytest with `-n 2` (pytest-xdist added to dependencies).

## 2026-10-15 10:40 — 01.plan / Phase 3 preparation (not executed)
- Action: tests/live_fixture_smoke.py (T19 step 2, manual), fillers/hcltech/{manifest.yaml,tests.yaml}, fillers/capgemini/{manifest.yaml,tests.yaml} pre-created from the spec (§7.7 hosts and samples). No live site, no model server, no human login were used.
- Not verified: T19–T21 need the Qwen server and a human at the keyboard. HCLTech still has 1 sample URL (Q5).

## 2026-10-15 12:05 — 01.plan / fix after first live HCLTech attempt
- Failed: `python -m agent build <hcltech url>` stopped at round 0 with `steps.py is empty` (runs/hcltech/2026-10-15T11-58-46).
- Root cause: `SUBMIT_RE` listed "apply now", so the job page's "Apply now" button made the classifier return `review` (and the submit guard would have refused the click anyway). On SuccessFactors "Apply now" opens the form; it never submits.
- Fix: "apply now" removed from `SUBMIT_RE` (core/browser.py) and from rule 5 of agent/prompts/system.md; `tests/test_browser.py::test_apply_now_is_job_page_not_review` added. `ActionLog(echo=True)` prints every action row to the console; the CLI enables it for `build` and `test` so a human can watch rounds.
- Verify: `ruff check` clean; `pytest -q -n 2` → 117 passed in 80.5 s; `python -m agent build http://127.0.0.1:5055/job.html --company fixture --llm fake:happy --headless` → `DONE: PASS 1/1` with per-round console lines.
- Plan change: §Revisions entry "Apply now is a navigation button, not a submit button".

## 2026-10-15 12:20 — 01.plan / verbose build trace
- Action: `python -m agent build … -v` (also `bench -v`) prints, per round, the task block sent to the model, the tool call it answered with (full step code, or old/new for edit_file), then the harness result line. `BuildLoop(verbose=)`; `main()` sets stdout/stderr to `errors="replace"` (cp1252 consoles crashed on "→"); `console_ask` turns EOF on stdin into NonInteractive.
- Verify: ruff clean; `pytest -q -n 2` → 117 passed; `build … --llm fake:phone_fix --headless -v` → DONE: PASS 1/1 with the fix round visible (`edit_file "+91" → "India (+91)"`, then replay 01–04 PASS).

## 2026-10-15 12:35 — 01.plan / T19 first live HCLTech round
- Observed: with the "Apply now" fix the live job page is classified `job_description`, `next=click ['Apply now']`, about 130 observe tokens; the build stopped because no model server was listening on LLM_BASE_URL (port 8000).
- Fix: `agent.llm.LLMUnavailable` wraps openai connection/timeout errors; the CLI prints one `STOP:` line with the URL and the remedy, writes the report, exits 3 (was a 60-line traceback).
- Verify: ruff clean; `pytest tests/test_llm_prompt.py tests/test_report.py tests/test_safety.py` → 16 passed; `build … --company fixture` with no server → `STOP: no model server at LLM_BASE_URL=…`.

## 2026-10-15 13:30 — 01.plan / T19 live runs with the real model (Ollama, qwen3-coder:30b)
- Setup: Ollama was already serving `qwen3-coder:30b` on 11434; `.env` now points there (was port 8000).
- Fixture with the real model: `build http://127.0.0.1:5055/job.html --company fixture --headless -v` → DONE: PASS 1/1 in 11 rounds, 10 steps, one model-made fix (`+91` → `India (+91)`), ~2.3k prompt tokens/round, 40–60 output tokens, 5 s/round after the first call.
- Live HCLTech, three failures found and fixed:
  1. Sample 180703 is closed: Apply now → `/` with "You can't view this job because it's not available at this time". New `PageKind.closed` (text rule, only when the page has no fields) → loop stops with "job closed … pick another sample"; `tests.yaml` now lists two open postings from the careers search (Q5 answered: 71971, 71972).
  2. Apply now on an open posting leads to the SuccessFactors sign-in on `career55.sapsf.eu`, which the allowlist blocked. `*.sapsf.eu`/`*.sapsf.com` added to `SF_HOSTS` and both manifests. The password field is in the main document, so the classifier reports `login`.
  3. Every Apply now click reported "page did not change": the cross-site navigation had not committed 150 ms after the click and `wait_for_load_state` returned at once for the old document. `click()` now waits up to 5 s for the promised effect (URL change / new tab for `expect="navigation"`; field-count change otherwise). Reproduced with `run_step` before (FAIL, url_after already sapsf) and after (PASS, kind_after=login).
- Loop robustness found on the same runs: a build on a second sample started at step N+1 on the job page (locked steps are now replayed from the job link first; new test `test_second_sample_replays_locked_steps_without_the_model`); the model called `observe` 15× in a row (unchanged snapshot is now reported back as a note); the model called `ask_human` 60× when stdin answered "" (empty answer now counts as a try); login/captcha/closed pages inside the fix loop are now handled by the harness (`_human_gate`, stop after 3 prompts that change nothing).
- Verify: ruff clean; `pytest -q -n 2` → 119 passed; live `build https://careers.hcltech.com/job/Hunter/71971-en_US --headless -v` → step_01 PASS (5.1 s), observe → `login · 2 fields`, asks the human, exit 3 without a TTY. `fillers/hcltech/steps.py` holds the locked step_01 only.
- Not done: the sign-in itself (needs the project owner at the keyboard, headed run); Capgemini's visible "Apply now" opens a chooser (Apply Now / LinkedIn) and the direct apply link is hidden — to be handled in T20.

## 2026-10-15 14:20 — 01.plan / T20 first live Capgemini round (project owner's run + headless repro)
- Failed: `build https://www.capgemini.com/in-en/jobs/355474-en_GB+sap_btp -v` observed `form · 1 fields · next=stop`; the model alternated observe/ask_human and could not act on the owner's hint "click on apply for this job button".
- Root cause: the call to action is a plain `<a>` "Apply for this job" (not role=button) beside the site's optional "location" search box. `APPLY_RE` only knew "Apply"/"Apply now", links were never shown to the model, and one labelled field made the classifier say `form`. A second defect: `_propose` built its instruction once, so after the model's own observe revealed the job page it still read "no Next button on this form page", and a login page reached inside `_propose` was never handed to the harness.
- Fix: `APPLY_RE` accepts "apply for this job / here / online / today"; observe lists apply links first in `buttons`; classifier: apply control + no empty required field + ≤ 2 labelled fields → `job_description`; `_propose` recomputes the instruction every round and returns to `run()` on login / captcha / closed / review. Test `test_apply_link_beats_site_search_box`.
- Verify: ruff clean; `pytest -q -n 2` → 120 passed. Headless repro under a scratch company with the Capgemini allowlist: r01 observe → `job_description · next=click ['Apply for this job']`, r02 `add_step step_01_click_apply` → PASS (5.7 s) → careers.capgemini.com → sign-in on `career5.successfactors.eu` (21 fields, 1,214 observe tokens) → harness asks the human, exit 3 without a TTY. Note: a scratch company whose manifest lacks `cg-jobstream-api.azurewebsites.net` never renders the Apply link — the allowlist must include it (the real manifest does).
- Next: the owner signs in (headed run); then the SuccessFactors form itself.

## 2026-10-15 14:45 — 01.plan / T2 follow-up: dummy profile extended for SuccessFactors forms
- Action: `Profile` gained optional fields address_line1, postal_code, state, nationality, gender, current_employer, availability, how_heard, cover_letter; `profiles/dummy.yaml` carries fake values for all of them plus five more screening answers (previously_employed_here, previously_applied, willing_to_travel, disability, consent_data_processing). Address, postal code, employer and cover letter are masked (identifying); state/nationality/gender are not (option-list values). Test `test_dummy_profile_has_apply_form_extras`.
- Verify: ruff clean; `pytest -q -n 2` → 121 passed; fixture build with the fake model and the larger profile → DONE: PASS 1/1, tok_in p95 451, observe max 228 (budget unaffected). The submit guard is unchanged: `click()` refuses Submit / Submit application / Send application / Complete application in build and test mode, the sandbox rejects such calls in steps.py, and the loop stops at the review page.
