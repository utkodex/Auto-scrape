# Loop engineering

Executing a plan is a series of loops: build a unit, prove it works, and if it
doesn't, diagnose and repair until it does. "Loop engineering" just means running
those loops *deliberately* — with a defined exit condition, the cheapest checks
first, and a hard bound on attempts — instead of ad-hoc poking that fills the
context with dead ends. A well-engineered loop is how you get to verified code
without thrashing.

## Define the exit condition before you start the loop

Before writing a unit, know its **acceptance signal** — the specific, observable
thing that means "done": a test passes, a smoke run exits 0 and logs a healthy
trace, a metric clears a threshold, an output file has the right shape and count.
This comes from the plan's intent (and, if the plan came from `ai-plan-writer`,
its explicit Verify lines and done criteria). A loop with no defined exit either
stops too early ("the edit was made") or never stops. Write the signal down in
your scratch checklist next to the unit.

## Inner loop: one failing check

```
generate → verify (cheapest first) → if fail: diagnose root cause → repair → re-verify
```

- **Cheapest checks first.** Imports/typecheck before tests before a real model
  call. Most breakages surface in the cheap check; you avoid paying for a long run
  that was doomed. For GenAI this also saves real API/model calls.
- **Diagnose before you change anything.** Read the actual error and trace; don't
  pattern-match a fix. A wrong fix applied fast costs more than a correct one
  found slowly.
- **Repair the right artifact.** Sometimes the code is wrong; sometimes the
  *plan* was wrong (bad assumption, unavailable dependency, infeasible step). Fix
  whichever is actually at fault — revising the plan is part of the job, not a
  deviation (record it under the plan's `## Revisions`).
- **Re-verify only what changed.** Don't rerun green checks.

**Bound it.** After two honest, *different* attempts at the same failure, stop
repeating fixes. Step back: is the root cause something else? Is the plan asking
for something that can't work here? Escalate to a diagnosis pair (see
`agent-teams.md`) or report clearly with what you tried. Repeating the same fix a
third time is thrash — it burns the window and rarely works.

## Outer loop: the whole plan

```
for each unit in dependency order:
    run the inner loop until its acceptance signal passes (or is escalated)
    log the result
then: integrate, run end-to-end verification, confirm done criteria
```

Work in **small, individually verifiable increments** so a failure localizes to
the unit that caused it. Don't batch ten changes and then run everything — you
won't know which one broke. Bound the outer loop too: if the plan as a whole
isn't converging, that's a signal to reassess the plan with the user, not to keep
grinding.

## The critic pass (before declaring done)

The step people skip. Before reporting success, do one **adversarial review** of
the unit against its acceptance signal, as if someone else wrote it:

- Does the verify actually prove the behavior, or just that code ran? (A green
  exit with an empty or malformed trace is not a pass.)
- Did I check the trace *and* the data, not just the exit code?
- Are there obvious inputs that would break this that I didn't try?
- Did I leave the plan and log consistent with what actually happened?

When you have subagents, make this a **separate verifier agent** with fresh
context (see `agent-teams.md`) — independence catches the author's blind spots.
Solo, switch into a deliberately skeptical stance and run the check yourself.

## Log each iteration

Each meaningful loop iteration gets a short `EXECUTION_LOG.md` entry: what you
tried, what the check returned, the root cause if it failed, and the fix. This
keeps the run auditable and stops you from re-trying something that already
failed. Keep entries to a few lines — the log is for a human skimming later.

## The point

Engineered loops spend tokens where they buy correctness — diagnosis, real
verification, an independent critic — and refuse to spend them on repeated
guesses. Defined exit, cheapest-first, bounded attempts, one skeptical pass. That
is the whole discipline.
