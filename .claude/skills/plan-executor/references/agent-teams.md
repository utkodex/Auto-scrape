# Agent teams

A single subagent doing one isolated chunk is the simplest case. An **agent
team** is the next step up: several agents working a plan at once, sometimes in
different *roles*, coordinated by you as orchestrator. Used well, a team is the
biggest token saver available — the bulky reading, editing, and running happen in
workers' contexts and only compact conclusions come back to the main thread — and
it also raises quality, because an independent agent catches what the author
rationalized away. Used carelessly it clobbers files and burns tokens on
coordination. This file is about doing it well.

You orchestrate: you hold the plan, the probe result, the file-ownership map, and
the running status. Workers hold the heavy context. Form a team only when the
work actually splits or when independence buys correctness — for a three-line
config edit, just do it yourself.

## Team topologies

Pick the shape that fits the work; you can switch shapes between phases.

### 1. Parallel implementers (map → reduce)
When a plan (or a folder of plans) has **independent** units — separate modules,
separate plan files with no shared files — assign one worker per unit and run
them at once. Each returns a compact summary; you reduce: integrate, resolve any
cross-unit interface mismatches, then verify the seams. This is the workhorse
pattern for large plans.

Rule: two workers must never edit the same file in the same round. Give each
worker an explicit, disjoint set of files it owns. Anything touching shared files
(a common config, an `__init__`, a router) is done serially by you or by one
designated worker after the parallel round.

### 2. Implementer + verifier (independent check)
After a unit is built, hand verification to a **fresh** agent that did not write
the code: "run the tests, do a smoke run of this path, inspect the trace, check
the output data; report pass/fail with evidence." Independence matters — the
author tends to verify the behavior they intended, not the behavior they wrote.
The verifier's compact report (commands, exit codes, trace/metric values, data
shape) is what you trust, not the implementer's "looks good."

### 3. Diagnosis pair (for stubborn failures)
When a check keeps failing and the cause is unclear, spawn two agents with
**different hypotheses** ("it's the embedding-dim mismatch" vs. "it's the index
not being reloaded"), each to investigate and report findings only — no fixes
yet. Compare their evidence, decide the root cause, then make one targeted fix.
Cheaper and faster than one agent thrashing through hypotheses serially in your
context.

## The compact-return contract (every worker, every time)

Tell each worker to return only:
- files created/modified (paths),
- a short diff or per-file description of what changed,
- commands run with exit codes, and key trace/metric values,
- anything surprising or that the next step needs.

It must **not** paste whole file contents — those live on disk; you can read a
targeted range later if needed. This contract is what keeps the orchestrator
context lean no matter how much work the team does.

## Coordination rules

- **Disjoint ownership.** Declare which files each worker may touch; keep the sets
  non-overlapping within a round.
- **Same file → serial.** Edits to a shared file are never parallelized.
- **Orchestrator owns integration.** Wiring units together and verifying the seams
  is your job, not a worker's.
- **Right-size the team.** Spawning has overhead; if a unit is trivial or the
  units aren't truly independent, a team costs more than it saves. Solo or a
  single worker is often correct.
- **Keep the plan and probe with you.** Pass each worker just the slice it needs
  plus the probe result — not the whole repo history.

## When there are no subagents

You can't run a literal team, but you can borrow the *roles*: after implementing a
unit, switch into a deliberately skeptical "verifier" stance and check it against
its acceptance signal as if someone else wrote it; when stuck, enumerate
competing hypotheses explicitly before fixing. The discipline is what matters; the
parallelism is a bonus when the environment allows it.
