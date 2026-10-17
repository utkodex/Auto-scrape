---
name: plan-executor
description: >-
  Execute one or more written implementation plans end to end: read the plan(s),
  make the code changes (creating new files and modifying existing ones across a
  project), then actually run the code and verify it works — checking execution,
  traces (MLflow/Databricks, LangSmith, Langfuse, OTel, etc.), and output data —
  and iterate to fix what's broken, repairing code and revising the plan as
  needed while logging every change. Use this whenever the user points at a plan
  file or a folder of plans and wants them carried out: "execute this plan",
  "implement plan.md", "work through my plans/ folder", "build this out and make
  sure it runs", or hands over a markdown plan/spec/design doc for a GenAI or ML
  project and expects working, verified code at the end. Also trigger when the
  user wants existing code modified or new files created according to a written
  plan and then validated. Runs token-efficiently using agent teams and
  engineered verify-fix loops where subagents are available.
---

# Plan Executor

This skill turns a written plan into working, verified code. Plans are usually
plain prose — paragraphs describing intent and goals, not neat checklists — so
the first job is always to read the plan as *intent* and derive the concrete
steps yourself. The second job, which people often skip, is to **prove the result
works**: run it, read the traces, check the data. A plan implemented but never
run is half-done.

The user builds GenAI/ML projects, so verification leans on running real
inference paths and inspecting traces (MLflow/Databricks by default, but check
whatever the project actually uses).

## Inputs

- **A single plan file** → execute that one plan.
- **A folder** → execute every plan in it. Run `scripts/find_plans.py <folder>`
  to discover and order them, then work through them in order. Plans that depend
  on earlier ones must come after them; the script proposes an order but read the
  content and reorder if the dependencies clearly differ.

Either way you may be modifying an existing project or creating files from
scratch — the plan tells you which.

## Workflow at a glance

1. Resolve the input and read the plan(s); derive concrete, ordered, checkable steps.
2. Probe the project **once** so you know how to build/test/run/trace it.
3. Execute the steps token-efficiently — delegate to subagents or an agent team
   when work isolates.
4. Verify it actually works: code runs, traces are healthy, data is correct.
5. If checks fail, run an engineered fix loop: diagnose root cause, repair code
   *and* revise the plan when the plan itself was wrong, log changes, re-verify.
6. Report a concise summary with evidence.

---

## Step 1 — Read the plan and derive steps

Read the plan file(s). Because the plans are prose, don't expect a step list —
extract one. Pull out the objective, the concrete units of work, anything that
must happen in a particular order, and what "done" looks like for each unit
(the acceptance signal you'll verify in step 4). Keep this derived checklist
somewhere light (a scratch todo), not as a giant block pasted into context.

If the plan is genuinely ambiguous on something that changes what you'd build —
not just a detail you can reasonably decide — surface it before writing lots of
code, since building the wrong thing is the most expensive mistake here.

## Step 2 — Probe the project once

Run the prober and keep its compact JSON for the rest of the session:

```bash
python scripts/probe_project.py <project-dir>
```

It reports the language and package manager, the build/test/run commands, lint
and typecheck commands, which tracing/observability stack is in use, and where
data and artifacts live. This exists so you don't re-explore the project on every
step — that re-exploration is one of the biggest silent token sinks. Treat the
output as strong hints; trust the actual project (README, Makefile) when they
disagree. If the project doesn't exist yet (a from-scratch plan), skip this until
there's something to probe.

## Step 3 — Execute the plan

Make the changes the plan calls for: create new files, modify existing ones. Work
in small, individually verifiable increments rather than one giant change, so that
when something breaks you know which step did it.

**Be token-efficient — this matters and the details are in
`references/token-efficiency.md`.** The short version:

- If you have subagents, act as an **orchestrator**: hold the plan and probe
  result yourself, and delegate isolatable chunks (a module + its test, wiring up
  tracing, an independent plan file) to subagents. Tell each subagent to return a
  **compact summary** — files changed, short diffs, commands run with exit codes —
  **not** whole file contents. Keep edits to the same files serial.
- When the work splits into several independent units, run an **agent team** rather
  than one worker at a time: parallel implementers each owning a disjoint set of
  files (map), then you integrate and verify the seams (reduce); and, for
  correctness, hand verification to a *fresh* agent that didn't write the code. The
  team topologies, the disjoint-ownership rule, and the compact-return contract are
  in `references/agent-teams.md` — read it before parallelizing a non-trivial plan.
- If you don't have subagents, apply the same discipline to your own context:
  locate with grep/glob then read narrow ranges, edit in place, don't re-read
  unchanged files, don't echo long output into context.

Read `references/token-efficiency.md` for the full playbook the first time you
execute a non-trivial plan.

## Step 4 — Verify it actually works

This is the part that makes the skill worth using. Don't declare success from the
fact that the edits were made — get evidence. Use the probe result for the exact
commands. Read `references/verification.md` for the full checklist; the three
buckets are:

- **Code works** — imports/typecheck, then tests, then a real smoke run of the
  path the plan touched; check exit codes and stderr, not just printed output.
- **Traces** — for GenAI paths, confirm the run logged a healthy trace in whatever
  the project uses (MLflow/Databricks MLflow, LangSmith, Langfuse, OTel, …): the
  expected spans/params/metrics are present and non-empty and nothing errored. A
  green exit with an empty or malformed trace is a red flag, not a pass.
- **Data** — anything ingested/transformed/embedded/indexed/generated exists,
  has the right shape and schema, sensible counts, and passes a spot-check.

Skip buckets that don't apply. Where something genuinely can't be verified in the
current environment (e.g. the Databricks UI isn't reachable), confirm the logging
calls at least execute without error and say plainly what couldn't be confirmed.

## Step 5 — Fix and improve (engineered loop)

Run the fix cycle as a **deliberate loop**, not ad-hoc poking — defined exit
condition, cheapest checks first, bounded attempts. The full discipline is in
`references/loop-engineering.md`; read it the first time a non-trivial check
fails. The essentials:

When a check fails, diagnose the **root cause** before changing anything — read
the actual error and trace, don't pattern-match a fix. Then:

- **Fix the code** to make the check pass.
- **If the plan itself was wrong, incomplete, or based on a bad assumption, revise
  the plan file too.** The plan is a living document, not a sacred one — editing
  it so it matches reality is part of the job, not a deviation from it. When you
  revise a plan, make the edit in the markdown and add a short note under a
  `## Revisions` heading at the end: the date, what changed, and why (e.g.
  "2026-06-19 — switched from FAISS to Chroma; FAISS wheels unavailable on the
  target runtime"). This keeps the plan trustworthy for the next person and
  records the decision.
- **Log every change** as you go (see the log format below) so the run is
  auditable and you can see what you've tried.

Then re-verify the affected checks. **Bound the loop**: after a couple of honest,
*different* attempts at a stubborn failure, stop — reassess the root cause (and
whether the plan is wrong), or escalate to a diagnosis pair (`agent-teams.md`). If
truly stuck, report clearly with what you tried rather than thrashing. Don't
re-verify things that didn't change.

**Before declaring a unit done, do one critic pass** against its acceptance
signal, as if someone else wrote it: does the verify actually prove the behavior
(not just that code ran)? Did you check the trace and the data, not only the exit
code? With subagents, make this a fresh verifier agent; solo, switch into a
skeptical stance. See `references/loop-engineering.md`.

## Step 6 — Report

Close with a concise, honest summary:

- What was implemented (per plan / per major step).
- Verification results: for each bucket that applied, the command/check run and
  its result, with brief evidence.
- Any plan revisions made and why.
- Anything left undone, unverifiable, or flagged for the user's attention.

Keep it tight — the user wants to know it works and what changed, not a replay of
every command.

---

## Execution log format

Keep a running log so the work is auditable. Append to `EXECUTION_LOG.md` in the
project root (create it if absent). One entry per meaningful action:

```markdown
## 2026-06-19 14:02 — <plan name> / <step>
- Action: implemented retriever module (src/rag/retriever.py, +tests)
- Verify: `uv run pytest -q` → pass (12 passed); smoke run logged MLflow run abc123
- Plan change: none
```

For a fix or plan revision, record what failed, the root cause, the fix, and any
plan edit. The log is for humans skimming later — keep entries to a few lines.

## Bundled resources

- `scripts/probe_project.py` — one-shot project capability map (stdlib only).
- `scripts/find_plans.py` — discover and order plan files in a folder.
- `references/verification.md` — full checklist for code / traces / data.
- `references/token-efficiency.md` — orchestrator/worker and context-saving patterns.
- `references/agent-teams.md` — team topologies (parallel implementers, independent
  verifier, diagnosis pair) and coordination rules.
- `references/loop-engineering.md` — running the build/verify/fix cycle deliberately:
  exit conditions, inner/outer loops, bounding, and the critic pass.
