---
name: ai-plan-writer
description: >-
  Turn AI/ML specification documents into executable implementation plans
  (plan.md) — granular, dependency-ordered, file-level task lists with concrete
  verification (commands, metric thresholds, traces like MLflow, LangSmith,
  Databricks, OTel) that an executor such as plan-executor can run end to end.
  Takes either a spec folder (a suite of numbered spec files) or a single spec
  file, or a described change when no spec exists, then writes numbered NN.plan.md
  files into a plans folder using the same naming convention as the specs so each
  plan traces back to its spec. Use whenever the user wants an implementation
  plan, build plan, execution plan, task breakdown, "turn this spec into a plan",
  "plan.md", "what are the steps to build this", or is about to hand work to
  plan-executor or start coding from a spec — even if they don't say "plan".
  Trigger after a spec is written, when handed a spec or specs folder, or when
  someone wants a step-by-step buildout of an ML feature, pipeline, model, agent,
  RAG system, or eval.
---

# AI/ML Implementation Plan Writer

Your job: convert a spec (or specs) into a **plan.md** that someone — or an
autonomous executor like `plan-executor` — can carry out task by task and verify
at each step, with no further context.

A spec says *what to build and why*. A plan says *exactly how, in what order, in
which files, and how you'll know each step worked*. The plan is more granular and
more execution-focused than the spec: atomic tasks, real file paths, concrete
commands, and verification — including trace/metric checks, since ML work isn't
"done" until the numbers and traces confirm it.

This skill is the bridge between `ai-spec-writer` upstream and `plan-executor`
downstream. Write plans that the executor can lift directly.

---

## The loop (follow in order)

1. **Resolve inputs** — the spec source (folder or file), the plans folder, and
   the repo root.
2. **Read the spec(s) cheaply** — the implementation/handoff sections first.
3. **Ground in the codebase** — graphify-out + recon, so task file paths are real.
4. **Build the task DAG** — extract atomic tasks, order by dependency.
5. **Draft** the plan(s) from the template.
6. **Validate + self-critique** (script + loop engineering), then revise.
7. **Write files** with the spec-mirroring naming convention and report.

The discipline that matters: get the cheap, structural understanding (spec
handoff + graph) before expensive reading, and make every task verifiable before
you write it down. An unverifiable task is a wish, not a plan.

---

## 1. Resolve inputs

Pull these from the conversation; ask only for what's missing, in one batch.

- **Spec source** (required): either a **spec folder** (contains
  `NN.*.spec.md`) or a **single spec file**. If the user has no spec but
  describes a change, you can plan from the description — but say so, and keep the
  plan honest about assumptions (a spec first is better; offer `ai-spec-writer`).
- **Plans folder** (where plans go): default to a `plans/` folder as a sibling of
  the spec folder unless the user names one. Create it if absent.
- **Repo root**: the codebase the plan will modify, so tasks reference real
  files. Often the parent of the spec folder; confirm if unclear.

If all three are evident, proceed without asking.

---

## 2. Read the spec(s) cheaply

The spec already did the analysis — don't redo it. Read the parts that define the
work, not the whole document.

- Jump to the **implementation / work-breakdown** and **Plan Handoff** sections
  first (the `ai-spec-writer` template puts the distilled, ordered task list
  there). Use `rg` to locate them rather than reading top to bottom:
  ```bash
  rg -n -i "plan handoff|implementation plan|work breakdown|acceptance criteria" <spec>
  ```
- Then skim **acceptance criteria**, **success metrics**, and **interfaces/
  contracts** — these become your verification steps.
- For a **suite**, map over the files: read each spec's implementation +
  acceptance sections, summarize to a compact task list, then reduce into one
  ordered set. Don't hold every full spec in context at once.

See `references/token-efficiency.md` for the full playbook.

---

## 3. Ground the plan in the codebase

A plan whose file paths don't exist is worse than no plan. Before writing tasks,
confirm where things actually live.

- **graphify-out first**: if a graphify graph exists, read it to map modules and
  integration points cheaply. Reuse any recon cache the spec stage left behind
  (e.g. `/tmp/spec_recon.json` or a `.spec-cache/`).
- Otherwise run the bundled recon:
  ```bash
  python scripts/recon.py <repo_root> --out /tmp/plan_recon.json
  ```
- Open only the files a task will touch, to get exact signatures/paths. Read
  interfaces, not whole implementations.
- **Subagent team (when available)**: parallelize by phase/subsystem — one agent
  drafts the tasks for *data*, one for *training*, one for *serving*, one for
  *eval* — each returning a **compact task list (IDs, files, verify), not file
  contents**. You reduce the small lists into one DAG. This keeps the expensive
  reading in their contexts. No subagents → do it single-threaded, budgeted.

---

## 4. Build the task DAG

Turn the spec's work breakdown into atomic, ordered tasks. Read
`references/plan-template.md` for the exact task shape. Principles:

- **Atomic**: one task = one coherent, independently verifiable change. If a task
  can't be verified on its own, split it.
- **File-level**: every task names the files it creates/modifies and the
  interface/contract it must satisfy.
- **Verifiable**: every task has a Verify line — a command to run, the expected
  result, and any metric threshold or trace to check (MLflow run, LangSmith/
  Langfuse trace, Databricks job, OTel span, output data shape). ML tasks aren't
  done until the numbers/traces confirm it.
- **Ordered by dependency**: declare `Depends on` per task; order the plan as a
  topological sort so it runs top to bottom. Group into phases with a checkpoint
  after each.

---

## 5. Draft from the template

Read `references/plan-template.md` and follow its structure: front matter
(including which spec it implements), preconditions/setup, phased tasks, an
end-to-end verification section, rollback notes, a change-log placeholder for the
executor, and done criteria. Mirror the spec's acceptance criteria into the
plan's done criteria so "spec satisfied" and "plan complete" mean the same thing.

Make it executor-friendly: `plan-executor` runs code, checks traces, and logs
every change while iterating. Leave it a clean place to do that — atomic tasks,
explicit verify steps, and a change-log section.

---

## 6. Validate and self-critique, then revise

Run the deterministic checker, then do one adversarial pass.

```bash
python scripts/validate_plan.py <draft_plan.md> --repo <repo_root>
```

It checks: unique task IDs, all `Depends on` references resolve, no dependency
cycles (it topologically sorts), every task has Files + Verify lines, and every
referenced *existing* file actually exists in the repo (new files are reported as
new, not errors). Fix what it flags.

Then reread adversarially (loop engineering — one good pass beats many shallow
ones):
- Could someone execute this with zero extra context?
- Is every task atomic and independently verifiable?
- Are the file paths real (or clearly new)?
- Is the ordering correct — nothing depends on a later task?
- Do the verify steps actually prove the thing (right command, right threshold,
  right trace)?
- Do the done criteria cover the spec's acceptance criteria?
- Cut filler; executors read plans under time pressure.

---

## 7. Write files with the naming convention

The convention mirrors `ai-spec-writer` so plans trace back to specs. Compute
names with the script:

```bash
# Mirror a whole spec folder -> one plan per implementable spec (same NN + slug)
python scripts/scan_plans.py <plans_folder> --mirror-spec-folder <spec_folder>

# Mirror a single spec file  -> same NN (+ slug) with .plan.md
python scripts/scan_plans.py <plans_folder> --mirror-spec <spec_file>

# No spec to mirror (planning from a description): additive numbering
python scripts/scan_plans.py <plans_folder>
python scripts/scan_plans.py <plans_folder> --suite ingest train serve eval
```

Convention:
- Single: `NN.plan.md`. Suite/mirror: `NN.<slug>.plan.md`.
- **Mirroring**: a plan takes the **same number and slug** as the spec it
  implements (`02.data.spec.md` → `02.data.plan.md`), so the pair is obvious.
  Skip specs that have nothing to implement (e.g. a pure `overview`/`risks`
  spec); the script reports each target and whether it already exists so you can
  decide.
- Without a spec: additive `NN` from the highest existing plan, never
  overwriting.

Then write the files and give a short report: the plans folder, each plan file
(with the spec it implements), a one-line summary, and a pointer that they're
ready for `plan-executor`. Present the created files.

---

## Reference files
- `references/plan-template.md` — the plan.md format and the per-task shape. Read
  before drafting (steps 4–5).
- `references/token-efficiency.md` — spec-handoff-first reading, graphify reuse,
  subagent teams per phase, DAG ordering, loop engineering, and caching. Read at
  steps 2–3 for non-trivial work.

## Scripts
- `scripts/recon.py` — token-efficient codebase inventory (+ graphify detection).
- `scripts/scan_plans.py` — computes plan filenames, including spec-mirroring mode.
- `scripts/validate_plan.py` — deterministic QA: task IDs, dependency DAG, verify
  lines, and file-path existence.
