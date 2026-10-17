# Token-Efficiency Playbook

The point of analysis is **maximum understanding per token**. A spec doesn't get
better because you read the whole repo; it gets better because you understood the
*right* parts. Spend tokens on the contracts and hotspots, not on boilerplate.

Apply these in roughly this order of leverage.

## 1. Graph before grep, grep before read

- **Graphify graph first.** If `graphify-out` (or similar) exists, read it before
  any source. A dependency/architecture graph hands you modules, edges, entry
  points, and central nodes for a fraction of the cost of crawling files. It tells
  you which 5–10 files actually matter so you can ignore the other 500.
- **Grep to locate.** Use `rg` to find where a symbol/config/route lives instead
  of opening files to look. `rg -l "class.*Trainer"`, `rg "def predict"`.
- **Read slices, not files.** Open the specific function/class/section, not the
  whole 2,000-line module. Read interfaces (signatures, schemas, configs) over
  implementations — a spec cares about contracts, not line-by-line logic.

## 2. Recon once, cache, reuse

Run `scripts/recon.py` for a compact JSON inventory and **save it**. If the skill
runs again on the same repo (e.g. a second spec), reuse the cached JSON instead
of re-scanning. Treat the inventory as the cheap map and the source files as the
expensive territory you only enter when needed.

## 3. Subagent teams (when available)

If subagents exist (Claude Code, Cowork), parallelize understanding with a small
**agent team**. This is the single biggest token saver on large repos because the
expensive file-reading happens in the subagents' contexts, and only compact
summaries return to yours.

Pattern:
- Assign one slice per agent: *data layer*, *model/training*, *serving/inference*,
  *config & infra*, *tests & CI*.
- Instruct each: **"Return a ≤200-word structured summary — key files, entry
  points, interfaces, risks, integration points. Do NOT paste file contents."**
- You receive 5 small summaries instead of 50 large files, then synthesize.

This is map-reduce: agents *map* over slices, you *reduce* into one architecture
picture.

No subagents → do the single-threaded budgeted version (graph → recon → read only
the contract files).

## 4. Map-reduce over large codebases

Even without subagents, when a directory is huge: summarize each subpackage into a
few lines (from filenames, `__init__`, and one representative module), then reason
over the summaries. Don't hold the whole tree in context at once.

## 5. Loop engineering for the writing itself

Quality comes from a tight draft→critique→revise loop, not from one giant pass.
- Draft the spec.
- Reread it adversarially against the step-5 checklist in SKILL.md.
- Fix the weakest 2–3 things.
One disciplined critique pass is worth more than endless rewriting; cap it so you
don't spin.

## 6. Don't pay twice

- Reference files by path instead of pasting them into the spec.
- Embed/point to the graphify diagram rather than redrawing the whole system.
- Cross-link between suite files instead of duplicating sections.
- Write findings into the spec as you learn them, so you don't re-derive them.

## Quick reference

| Situation | Do this | Avoid |
|---|---|---|
| Repo present, graph exists | read graph → open ~5 contract files | reading the tree |
| Repo present, no graph | recon.py → rg to locate → read slices | cat-ing modules |
| Large repo + subagents | agent team, ≤200-word summaries each | one agent reads all |
| Re-running on same repo | reuse cached recon JSON | re-scanning |
| Writing the spec | draft → one critique pass → revise | many shallow rewrites |
