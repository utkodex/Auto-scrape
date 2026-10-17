# AI/ML Spec Template

This is the backbone for every spec. Use the section order below. Adapt depth to
the project, but keep the structure recognizable so specs are consistent and a
`plan.md` can be derived mechanically.

Two reading modes:
- **Single spec** → include all sections in one `NN.<purpose>.md`, scaling each to
  the work.
- **Suite** → map section groups onto files per `spec-decomposition.md`; put the
  front-matter + sections 1–3 in `01.overview.md` and cross-link the rest.

Write in the imperative and the concrete. Prefer tables and short paragraphs over
walls of prose. Use mermaid for diagrams when a picture helps; otherwise ASCII is
fine. If a graphify graph exists, reference/embed it in the Architecture section.

---

## Front matter (top of every spec)

```markdown
# <NN> — <Spec Title>

| Field | Value |
|---|---|
| Spec ID | <NN> |
| Status | Draft \| Reviewed \| Approved |
| Mode | new \| modify |
| Owner | <name/role> |
| Created | <YYYY-MM-DD> |
| Related specs | <NN.…>, … |
| Feeds plan | <path to future plan.md, e.g. plans/01.plan.md> |
| Repo / paths in scope | <module/dir paths from analysis> |
```

---

## 1. Context & problem statement
- What problem are we solving, for whom, and why now?
- What's the current situation? For `modify`: summarize what exists today from
  the project analysis (architecture, key modules, integration points) and what
  hurts. For `new`: the gap and the opportunity.
- One-paragraph summary a stakeholder could read and "get it".

## 2. Goals & non-goals
- **Goals**: bullet the outcomes this spec commits to.
- **Non-goals**: explicitly bullet what's out of scope. Non-goals prevent scope
  creep and make the plan tractable.

## 3. Success metrics
Three buckets — fill all that apply, each with a target and how it's measured:

| Type | Metric | Target / threshold | How measured |
|---|---|---|---|
| Offline | e.g. F1 / AUC / RMSE / exact-match / pass@k / eval-suite score | | eval set + harness |
| Online | e.g. CTR, conversion, task success, deflection | | A/B test / logs |
| Guardrail | latency p50/p95, cost-per-call, throughput, $/month | | load test / billing |
| Safety | bias gap, toxicity, hallucination/refusal rate, PII leakage | | red-team / eval |

Vague goals ("make it better") are the top cause of failed ML projects. If a
target is unknown, state the baseline and a method to set the target — don't omit
the metric.

## 4. Requirements
- **Functional**: what the system must do (capabilities, I/O, user-visible
  behavior).
- **Non-functional**: latency, throughput, scale (QPS, data volume), cost budget,
  availability, privacy/compliance (GDPR, HIPAA, data residency), security.

## 5. Data specification
The most under-specified and most decisive part of ML work. Cover:
- **Sources & access**: where data comes from, ownership, access path, refresh.
- **Schema & volume**: fields/types, size, growth rate.
- **Splits**: train/val/test strategy; leakage risks; temporal splits if needed.
- **Labeling**: how labels are produced, quality, inter-annotator agreement,
  cost.
- **Quality & bias**: known gaps, class imbalance, representation issues.
- **Drift**: expected distribution shift and how it's detected.
- **Governance**: PII handling, consent, retention, anonymization.
- **Versioning**: how datasets are versioned/reproduced (DVC, snapshots, hashes).

## 6. Modeling approach
- **Candidate approaches** considered (brief), with trade-offs.
- **Chosen approach + rationale** — why this over the alternatives.
- **Baselines** to beat (heuristic, current production model, simple model).
- **Architecture / method**: model family, size, key hyperparameters or ranges.
- **For LLM systems**: prompt design, RAG (retriever, index, chunking), tool use,
  fine-tuning vs. in-context, context-window budget, fallback behavior.
- **Training**: objective, regime (from scratch / fine-tune / continued
  pretraining), compute estimate, expected duration/cost.

## 7. System & architecture design
- **Components** and how they connect (ingest → features → train → registry →
  serve → monitor).
- **Data & control flow** — a diagram (mermaid/ASCII). Reference the graphify
  graph for the existing system; show the *delta* for `modify` projects.
- **Interfaces/contracts**: APIs, schemas, message formats between components.
- **Infra & orchestration**: where it runs, scheduler/orchestrator (Airflow,
  Prefect, K8s, etc.), feature store, model registry, artifact storage.
- **Integration points** (for `modify`): exact modules/files the change touches.

## 8. Evaluation & experimentation plan
- **Eval harness**: datasets, metrics (tie back to §3), and the command/tooling
  that runs them. Make evals reproducible and automatable.
- **Offline → online**: gating criteria to promote from eval to A/B to rollout.
- **Experiments**: the experiments/ablations to run, what each tests, and
  tracking (MLflow / Weights & Biases / Databricks). Define what "good enough to
  ship" means up front.
- **Regression & guardrail checks**: what must never regress.

## 9. Implementation plan (work breakdown)
The bridge to `plan.md`. Break the work into phases, then into tasks. For each
task specify **file-level** changes:

```markdown
### Phase 1 — <name>
- [ ] T1: <task>
  - Creates: `path/new_file.py`
  - Modifies: `path/existing.py` (function X, add Y)
  - Contract: <interface/signature/schema it must satisfy>
  - Depends on: <task ids / none>
  - Verify: <command / metric / trace that proves done>
```

Reference *real* paths discovered during analysis. Order by dependency so the
list is executable top-to-bottom.

## 10. Risks & mitigations
Specific to *this* project, not generic. Cover technical, data, cost, drift,
safety/ethical, and delivery risks.

| Risk | Likelihood | Impact | Mitigation / fallback |
|---|---|---|---|

## 11. Rollout, monitoring & lifecycle
- **Rollout**: shadow / canary / staged / A-B; rollback plan.
- **Monitoring**: model metrics, data drift, latency/cost, alerting thresholds.
- **Lifecycle**: retraining cadence/triggers, ownership, on-call.

## 12. Acceptance criteria
A checklist of concrete, verifiable conditions that mean "done". Each ties to a
metric threshold (§3), a test, or an observable behavior. These should map onto
the Verify lines in §9.

## 13. Open questions & decisions log
- **Open questions**: unresolved items blocking or shaping the work.
- **Decisions (ADR-style)**: `Decision — Context — Consequences`, so future
  readers know *why*.

## 14. Plan handoff
A distilled, ordered list of executable tasks lifted from §9, formatted so a
downstream `plan.md` / executor can consume it directly:

```markdown
1. <task> — files: <…> — verify: <…>
2. <task> — files: <…> — verify: <…> — depends on: 1
```

State the suggested `plan.md` path and note that each task carries its own
verification so execution can be validated step by step.
