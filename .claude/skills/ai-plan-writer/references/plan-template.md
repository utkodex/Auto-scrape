# Implementation Plan Template (plan.md)

A plan is an executable contract. Each task is small enough to do in one sitting,
names the exact files it touches, and ends with a check that proves it worked.
Write for a competent engineer (or `plan-executor`) who has the spec but hasn't
seen your reasoning.

Use the structure below. Scale the number of tasks to the work, but never drop
the per-task **Files** and **Verify** lines — they're what make a plan
executable instead of aspirational.

---

## Front matter (top of every plan)

```markdown
# <NN> — <Plan Title>

| Field | Value |
|---|---|
| Plan ID | <NN> |
| Implements spec | <NN.<slug>.spec.md> (relative path) |
| Status | Draft \| Ready \| In progress \| Done |
| Repo root | <path> |
| Paths in scope | <dirs/files this plan touches> |
| How to run | <env setup / test command / entrypoint> |
| Executor | plan-executor (or manual) |
```

---

## 1. Preconditions & setup
What must be true before task 1 starts. Keep it runnable.
- Environment: Python/CUDA versions, virtualenv/conda, `pip install -r …`.
- Dependencies to add (with versions) and where (`requirements.txt`/`pyproject`).
- Data & credentials needed (datasets, API keys, model/registry access) — name
  them; never inline secrets.
- Baseline state: branch to start from, current tests passing, any snapshot to
  take so changes are reversible.
- Tooling for verification available: test runner, eval harness, experiment
  tracker (MLflow/W&B), trace backend (LangSmith/Langfuse/OTel) as relevant.

## 2. Phases & tasks
Group tasks into phases; end each phase with a checkpoint. Every task uses this
shape:

```markdown
### Phase 1 — <name>
- [ ] T1: <imperative task title>
  - **Goal**: one line — what this task makes true.
  - **Creates**: `path/new_file.py`
  - **Modifies**: `path/existing.py` — <function/section and the change>
  - **Steps**:
    1. <concrete, code-level step>
    2. <…>
  - **Contract**: <signature / schema / API the task must satisfy>
  - **Depends on**: <task IDs, or none>
  - **Verify**: <exact command> → <expected result / metric threshold / trace to
    inspect, e.g. "pytest tests/test_x.py passes", "MLflow run logs recall@5 ≥
    0.90", "LangSmith trace shows retriever called before LLM">
  - **On failure**: <what to check / how to roll back this task>

- [ ] T2: …

#### Checkpoint after Phase 1
<command(s) to run and the observable state that means the phase is complete.>
```

Guidance:
- **Atomic & verifiable.** If you can't write a single clear Verify line, the
  task is too big — split it.
- **Real paths.** Reference files/modules confirmed from the codebase (graphify/
  recon), or mark them clearly as new.
- **Dependency-ordered.** Nothing depends on a later task; the plan runs top to
  bottom. Note explicit `Depends on` so the order is checkable (and so
  `validate_plan.py` can topo-sort it).
- **ML verification is trace + metric, not just "it ran".** For training/eval/
  serving tasks, the Verify line should reference the metric threshold and/or the
  trace/run that confirms behavior, mirroring the spec's success metrics.

## 3. End-to-end verification
After all phases: how to prove the whole thing works together.
- The command(s) to run the full pipeline / service / eval.
- The eval harness + datasets and the metric thresholds that gate "good".
- Traces/runs to inspect and what "healthy" looks like in them.
- Any A/B or shadow check before rollout (from the spec).

## 4. Risks & rollback (execution-time)
What could break *during execution* and how to recover. Per-phase rollback,
feature flags to disable new paths, how to restore the baseline snapshot. Distinct
from the spec's project-level risks — this is about safely running the plan.

## 5. Done criteria
A checklist that means the plan is complete. **Mirror the spec's acceptance
criteria** so "plan done" ⇔ "spec satisfied". Each item ties to a Verify line or
the end-to-end check.

```markdown
- [ ] All tasks complete and their Verify steps pass.
- [ ] <spec acceptance criterion 1> — confirmed by <check>.
- [ ] <metric> meets <threshold> in <run/trace>.
- [ ] No regression in <existing test/metric>.
```

## 6. Change log (for the executor)
A running log the executor fills in while working — task, what changed, result,
and any deviation from the plan (with why). This is how `plan-executor` records
iterations and plan revisions; leave it ready.

```markdown
| Date | Task | Change | Result / verify | Deviation & reason |
|---|---|---|---|---|
```
