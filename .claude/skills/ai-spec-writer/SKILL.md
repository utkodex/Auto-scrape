---
name: ai-spec-writer
description: >-
  Author state-of-the-art specification documents (.md) for AI/ML and data
  projects — both greenfield ("build X from scratch") and modifications to an
  existing repo ("add RAG to our chatbot", "swap the model", "refactor the
  training pipeline"). First analyzes the whole project (reading a graphify
  dependency/architecture graph when present, else token-efficient code recon),
  then writes one or more numbered spec files named for their purpose
  (01.<purpose>.md, 02.<purpose>.md, ...) into a folder the user names. Specs
  blend PRD + technical design doc + executable
  build blueprint and are structured to feed a downstream plan.md (e.g. for
  plan-executor). Use whenever the user wants a spec, design doc, technical plan,
  blueprint, PRD, RFC, or "write up what we should build" for anything involving
  models, training, inference, data pipelines, agents, RAG, evals, or ML infra —
  even if they don't say "spec". Trigger before writing a plan.md, on "spec this
  out", "document the design", or "plan this feature". Prefer over ad-hoc docs.
---

# AI/ML Spec Writer

Your job: turn a project (a repo, a rough idea, or both) into rigorous,
buildable specification documents that an engineer — or an autonomous executor
like `plan-executor` — can act on with no further context.

A good spec from this skill is three things at once:
- a **PRD** (what we're building and why, success metrics),
- a **technical design doc** (architecture, data, modeling, trade-offs), and
- an **executable blueprint** (file-level work breakdown + acceptance criteria
  that map cleanly onto a future `plan.md`).

Specialize hard for AI/ML. Generic software specs skip the things that make ML
projects succeed or fail: data lineage and drift, offline vs. online metrics,
eval harnesses, baselines, experiment tracking, model/version governance,
latency/cost budgets at inference, and safety/bias guardrails. Treat those as
first-class.

---

## The loop (follow in order)

1. **Resolve inputs** — confirm the output folder, the mode, and the scope.
2. **Analyze the project** — graphify-out first, then token-efficient recon.
3. **Decide the file plan** — single spec vs. numbered suite.
4. **Draft** the spec(s) from the AI/ML template.
5. **Self-critique and revise** (loop engineering) once before presenting.
6. **Write files** with the `NN.<purpose>.md` naming convention and report.

Do the cheap, structural understanding before the expensive reading, and the
expensive reading before writing. Most wasted tokens come from reading whole
files when a graph, a grep, or a subagent summary would do.

---

## 1. Resolve inputs

You need three things. Pull them from the conversation first; only ask for what's
genuinely missing, and ask in one batch.

- **Output folder** (required). The user names where specs go, e.g. `specs/`,
  `docs/specs/`. Create it if it doesn't exist. Never scatter specs across the
  repo root unless asked.
- **Mode**: `new` (greenfield) or `modify` (existing project). Infer from
  phrasing — "build a … from scratch" → new; "add / change / refactor / fix in
  our …" → modify. When a repo is present, it's almost always `modify` even for
  big features.
- **Scope**: the feature or system the spec covers. If vague ("spec out the
  project"), the scope is the whole project.

If the user gave all three, don't ask anything — proceed.

---

## 2. Analyze the project (token-efficient)

The quality of the spec is capped by how well you understand the project. The
goal is *maximum understanding per token*. Read
`references/token-efficiency.md` for the full playbook; the essentials:

### 2a. Look for graphify output first

Before reading any source, check for a graphify graph — the dependency/
architecture graph the user produces with the graphify tool. Look in common
spots:

```bash
ls -d graphify-out graphify_out .graphify graphify 2>/dev/null
find . -maxdepth 3 -iname '*graphify*' 2>/dev/null | head
```

If found, **read it first**. A graph gives you modules, dependencies, entry
points, and hotspots for a tiny fraction of the tokens that reading the tree
costs, and it directly serves the "code & architecture" focus these specs need.
Use it to decide which few files are actually worth opening.

If graphify-out is absent and the `graphify` CLI appears available, mention that
running it would make analysis cheaper and better — but don't block on it; fall
through to recon.

### 2b. Recon (when there's a codebase)

Run the bundled recon script for a compact, structured inventory instead of
hand-crawling the tree:

```bash
python scripts/recon.py <repo_root> --out /tmp/spec_recon.json
```

It inventories languages, dependencies, ML frameworks, entry points, config,
data/model artifact locations, tests, and any graphify graph — and prints a
short summary. Cache it; if you re-run the skill on the same repo, reuse the
JSON rather than re-scanning.

**Then go deep only where it matters.** Using the recon + graph, open the
handful of files that define the system's contracts: the training entry point,
the inference/serving path, the data loaders, the config schema, and the public
interfaces you'll touch. Use `rg` (ripgrep) to locate, and read slices, not
whole files.

### 2c. Use a subagent team when available (big token savings)

If you're in an environment with subagents (Claude Code, Cowork), parallelize
recon: spawn a small **agent team**, each owning one slice — e.g. *data layer*,
*model/training*, *serving/inference*, *configs & infra*, *tests & CI*. Instruct
each to **return a ≤200-word structured summary, not file contents**, so the
synthesis stays small. You read five cheap summaries instead of fifty expensive
files. Then synthesize. (No subagents — e.g. plain Claude.ai — do the budgeted
single-threaded recon in 2b instead.)

### 2d. For greenfield (`new`) projects

There's no code to read. Instead, interrogate the idea: intended users, the ML
task type, data availability, latency/cost/scale constraints, and any stack
preferences. Capture assumptions explicitly in the spec rather than inventing
silent ones.

At the end of analysis you should be able to state, in a few sentences, what
exists today (or what's assumed), where the new work plugs in, and the main
risks. If you can't, analyze a little more before drafting.

---

## 3. Decide the file plan

Pick the structure that fits the project's size — read
`references/spec-decomposition.md` for the decision rule and the canonical
section order.

- **Single file** (`NN.<purpose>.md`): a focused change or a small project — one
  feature, one model swap, one pipeline. Most modifications land here. Name it
  for what it specs (e.g. `01.rag-retrieval.md`), never a generic `spec`.
- **Numbered suite** (`NN.<purpose>.md`): a whole project or a large
  initiative, where one file would be unwieldy. A suite typically splits into
  overview, data, modeling, architecture, evaluation, implementation, and
  rollout/risks — created together as a coherent, cross-referenced set with an
  index in `01`.

Default to a single file unless the work clearly spans multiple subsystems or
the user asked for a project-wide spec. When in doubt, propose the plan in one
line before writing ("I'll write a 4-file suite: overview, data, modeling,
implementation — sound right?") only if it's a genuinely large suite; otherwise
just proceed.

---

## 4. Draft from the template

Read `references/spec-template-ml.md` and use it as the backbone. It is the
heart of this skill — follow its section structure, and fill every section with
project-specific substance, not boilerplate. Empty headers are worse than
omitted ones; if a section truly doesn't apply, write one line saying why.

Non-negotiable content for AI/ML specs:
- **Success metrics** split into offline (eval-set metrics), online (product/
  business KPIs), and guardrails (latency, cost-per-call, safety, bias,
  hallucination/refusal rates for LLM systems). Vague goals are the #1 cause of
  failed ML projects — make them measurable.
- **Data spec**: sources, schema, volume, splits, labeling, quality, drift, PII/
  governance, and versioning. ML is data first.
- **Modeling**: candidate approaches, the chosen one *with rationale*, baselines
  to beat, and (for LLM work) the prompt/RAG/fine-tune strategy.
- **Evaluation plan**: the eval harness and datasets, offline + online, and
  regression/guardrail checks. Eval-driven, not vibes-driven.
- **Implementation plan**: a phased, **file-level** work breakdown (new files,
  modified files, interfaces/contracts) — this is what becomes `plan.md`.
- **Acceptance criteria**: concrete and verifiable, each tied to how it's
  checked (a command, a metric threshold, a trace).

### Make it feed plan.md

These specs are upstream of a `plan.md`. Always end with a **Plan Handoff**
section that lists the executable tasks in dependency order, each with: the files
it creates/modifies, the interface or contract it must satisfy, and the
verification step that proves it's done. Write it so an executor can lift tasks
directly. Mirror the user's structure if they have a plan convention.

---

## 5. Self-critique once, then revise (loop engineering)

Before writing files, reread your draft adversarially against this checklist and
fix what's weak. One disciplined pass beats ten shallow ones.

- Could an engineer who's never seen the repo build this from the spec alone?
- Are all metrics measurable, with thresholds and how-to-measure?
- Is the data section honest about gaps, drift, and governance?
- Does the implementation plan reference *real* files/modules from the analysis
  (correct paths), not invented ones?
- Are risks specific (this model, this dataset) rather than generic platitudes?
- Does the Plan Handoff map 1:1 onto buildable, verifiable tasks?
- Is anything padded? Cut filler; specs are read by busy people.

---

## 6. Write files with the naming convention

Determine the next number and exact filenames with the bundled script — it scans
the folder so you never overwrite an existing spec:

```bash
python scripts/scan_specs.py <output_folder> --slug rag-retrieval   # single -> next NN.rag-retrieval.md
python scripts/scan_specs.py <output_folder> --suite overview data modeling implementation
```

Convention:
- Every spec is `NN.<purpose>.md`: a zero-padded serial number, a dot, a
  purpose-based slug naming what the spec covers, then `.md`. No `.spec` segment,
  and never a generic name like `spec` — the slug must describe the content.
- Single: one `NN.<purpose>.md` (e.g. `01.rag-retrieval.md`, `02.model-swap.md`);
  pass the purpose via `--slug`.
- Suite: one `NN.<purpose>.md` per slug (e.g. `01.overview.md`, `02.data.md`),
  numbered sequentially from the next free index, in logical reading order.
- Numbers continue from the highest existing spec in the folder — additive, never
  clobbering.

Write the files, then give the user a short report: the folder, the files
created (with their numbers), a one-line summary of each, and a pointer that the
implementation/Plan Handoff section is ready to become a `plan.md`. If files
were created, present them so the user can open them.

---

## Reference files

- `references/spec-template-ml.md` — the full AI/ML spec template and section
  guidance. Read before drafting (step 4).
- `references/spec-decomposition.md` — single-file vs. suite decision rule and
  the canonical section/file order. Read at step 3.
- `references/token-efficiency.md` — the detailed playbook for graphify-first
  analysis, subagent teams, map-reduce over large repos, loop engineering, and
  caching. Read at step 2 when the project is non-trivial.

## Scripts

- `scripts/recon.py` — token-efficient codebase inventory (+ graphify detection),
  emits compact JSON.
- `scripts/scan_specs.py` — computes the next `NN` and exact filenames for a
  single spec or a suite; enforces the `NN.<purpose>.md` naming convention.
