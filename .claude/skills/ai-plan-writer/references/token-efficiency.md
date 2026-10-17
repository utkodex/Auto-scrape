# Token-Efficiency Playbook (plan generation)

The spec already paid for the analysis. The cardinal rule here: **don't re-read
what the spec already distilled, and don't read the codebase wider than the tasks
require.** Spend tokens turning the spec's intent into verifiable, correctly
ordered tasks — not re-deriving the design.

Apply in order of leverage.

## 1. Read the spec's handoff, not the whole spec

The `ai-spec-writer` template ends with a **Plan Handoff** and an
**Implementation Plan** section that are already an ordered, file-level task list.
Start there.

```bash
rg -n -i "plan handoff|implementation plan|work breakdown|acceptance criteria|success metrics" <spec>
```

Read those sections plus interfaces/contracts. Only fall back to reading the full
spec when the handoff is thin or missing.

## 2. Reuse the spec stage's analysis

If the spec was written by `ai-spec-writer`, a graphify graph and/or a recon
cache likely already exist. Reuse them instead of re-scanning:
- graphify-out (`graphify-out/`, `.graphify`, …) — read for module/integration
  layout.
- a recon cache (`/tmp/spec_recon.json`, `.spec-cache/…`) — load instead of
  re-running recon.
Only run `recon.py` fresh if nothing is cached.

## 3. Open only what a task touches

You need exact file paths and signatures for the tasks, nothing more. Use `rg`
to find the symbol/route/config, then read that slice. Reading interfaces (a
function signature, a config schema) is enough to write a correct task; you don't
need the implementation body.

## 4. Subagent teams by phase (when available)

The biggest saver on large plans: spawn an **agent team**, one per phase or
subsystem (*data*, *training*, *serving*, *eval*, *infra*). Instruct each:

> "From this spec section + these files, produce the atomic tasks for <phase>:
> ID, goal, files created/modified, contract, depends-on, and a Verify line.
> Return only the task list — no file contents, no prose."

You receive small task lists, then **reduce**: merge, renumber, resolve
cross-phase dependencies, and topologically sort. The expensive file-reading
stays in the subagents' contexts. No subagents → do the phases yourself,
single-threaded and budgeted.

## 5. Build the DAG, then sort (deterministic)

Tasks form a dependency graph. Construct it explicitly (each task's `Depends on`),
then order the plan by topological sort so it runs top to bottom. After drafting,
`scripts/validate_plan.py` re-derives the DAG to catch cycles and dangling
dependencies — a cheap deterministic check that beats eyeballing order.

## 6. Loop engineering for the plan itself

Draft → validate (script) → one adversarial critique → revise. The critique
focuses on the three things that make plans fail in execution: non-atomic tasks,
fake file paths, and verify steps that don't actually prove the result. Fix those
and stop; don't endlessly polish.

## 7. Don't pay twice

- Reference spec sections by path; don't copy the spec into the plan.
- Point to the graphify diagram instead of redrawing architecture.
- Mirror the spec's acceptance criteria into done criteria by reference, not by
  re-deriving them.
- Write tasks as you confirm paths, so you don't re-open files later.

## Quick reference

| Situation | Do this | Avoid |
|---|---|---|
| Spec has a Plan Handoff | start from it | re-reading the whole spec |
| graphify/recon cache exists | reuse it | re-scanning the repo |
| Need a file's path/signature | rg → read slice | cat-ing the module |
| Large multi-phase plan + subagents | one agent per phase, task-lists only | one agent reads everything |
| Ordering tasks | declare depends-on → topo sort → validate_plan.py | eyeballing order |
| Polishing | draft → validate → one critique | endless rewrites |
