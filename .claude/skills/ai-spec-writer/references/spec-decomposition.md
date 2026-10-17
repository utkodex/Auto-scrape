# Spec Decomposition: single file vs. numbered suite

Pick the smallest structure that keeps each file readable and each concern
findable. More files isn't more rigor — it's more navigation cost. Default to a
single file and only split when one file would genuinely be unwieldy.

## Decision rule

Choose a **single `NN.<purpose>.md`** when the work is one coherent change:
- a single feature, model swap, or pipeline stage
- a bounded refactor
- a small greenfield project
- anything where the full template fits comfortably in one readable document

This covers most `modify` requests.

Choose a **numbered suite** (`NN.<purpose>.md`) when:
- the work spans multiple subsystems (data + modeling + serving + infra), or
- it's a whole-project / platform-scale initiative, or
- the user explicitly asks for a multi-document spec, or
- a single file would exceed what someone wants to scroll (rough heuristic:
  more than ~3–4 distinct subsystems each needing real depth).

If unsure, prefer single. You can always add suite files later (numbering is
additive).

## Canonical suite layout

When splitting, group the template sections into files and create them together
as a cross-referenced set. A typical full suite (scale up or down — drop files
you don't need):

| Slug | Template sections | Purpose |
|---|---|---|
| `overview` | front matter, 1–4, index of the suite | the entry point; links every other file |
| `data` | 5 | data spec |
| `modeling` | 6 | approach, baselines, training |
| `architecture` | 7 | components, flow, interfaces, infra |
| `evaluation` | 8 | eval harness + experiments |
| `implementation` | 9, 14 | work breakdown + plan handoff |
| `rollout` | 11, 12 | rollout, monitoring, acceptance |
| `risks` | 10, 13 | risks + decisions log |

For mid-size projects, a common 4-file cut works well:
`overview`, `data-and-modeling`, `architecture-and-eval`, `implementation`.

## Rules for a suite

- **Number sequentially** from the next free index in the folder, in reading
  order, so `01` is always the overview/index.
- **The overview indexes the rest** — list every file with a one-line summary and
  a relative link.
- **Cross-link** related sections between files instead of duplicating content.
- **One Plan Handoff** — keep the executable handoff in `implementation` so the
  downstream `plan.md` has a single source.
- Keep each file self-identifying via the front-matter table (same Spec ID family,
  e.g. all part of feature "recsys-v2").
