# Verification reference

After implementing a plan, the goal is evidence that it actually works — not a
claim that it should. Verification falls into three buckets: **code runs**,
**traces are healthy**, and **data is correct**. Use the probe result
(`probe_project.py` output) for the exact commands; the items below are what to
look for, not a fixed script. Skip buckets that don't apply (a plan with no LLM
calls has no traces to check).

## 1. Code works

Work outward from cheap-and-fast to slow-and-thorough, stopping early only if
something breaks:

1. **Imports / syntax / typecheck** — does it even load? (`python -c "import pkg"`,
   `ruff check`, `mypy`/`pyright` if present). Catches the dumb breakages instantly.
2. **Unit tests** — run the project's test command. If the plan added behaviour,
   confirm there are tests for it; if not, that's a gap worth flagging or filling.
3. **Smoke run** — actually execute the entrypoint (or the specific path the plan
   touched) on a tiny input. For GenAI code this is the real test: does a request
   go out, does a response parse, does the chain/agent complete one turn?
4. **Exit status + stderr** — a process that prints results and then exits non-zero,
   or floods warnings, is not "working". Check the return code and read stderr.

Capture the actual command, its exit code, and the salient output. "Tests pass"
with no evidence is not verification.

## 2. Traces / observability

GenAI systems fail quietly — a call silently returns an empty completion, a tool
never fires, retries balloon latency. Traces are where you catch this. Check
whatever the project uses (the probe reports it); common cases:

- **MLflow (incl. Databricks MLflow)** — confirm a run/trace was actually logged
  for the smoke run. Check that expected params/metrics/artifacts are present and
  non-empty, spans cover the steps the plan added, and nothing logged an error
  status. For Databricks, traces land in the workspace's MLflow experiment rather
  than a local `mlruns/`; if you can't reach the workspace, verify the logging
  *calls* execute without error and note that visual confirmation needs the
  Databricks UI. Locally, inspect `mlruns/` or query via the `mlflow` client.
- **LangSmith / Langfuse / Phoenix / OpenTelemetry** — confirm the run appears,
  the trace tree matches the intended flow (no missing or duplicated spans), token
  counts and latencies look sane, and there are no error events on spans.
- **No formal tracing** — fall back to logs: did the run emit the log lines the
  plan implies, with sane values and no swallowed exceptions?

The question to answer: *does the trace show the system doing what the plan said
it would, end to end?* A green exit code with an empty or malformed trace is a red
flag, not a pass.

## 3. Data

Plans that ingest, transform, embed, index, or output data need their data
checked, because code can "succeed" while producing garbage:

- **Existence + shape** — the expected files/tables/indexes were created in the
  expected location (see `data_locations` from the probe). Row/record counts are
  non-zero and roughly what the plan implies.
- **Schema + types** — columns/fields match what downstream steps expect; no
  silent type coercion.
- **Sanity** — spot-check a few records. For embeddings: right dimensionality, not
  all-zeros. For a vector index: a known query returns plausible neighbours. For
  generated/labelled data: a couple of examples actually look correct.
- **No silent truncation/dedup** — counts before vs after a transform make sense;
  you didn't lose 90% of rows to a bad join or filter.

## Writing it up

End with a short, honest verification summary: for each bucket, the command/check
run, the result, and the evidence. If something is unverifiable in the current
environment (e.g. Databricks UI not reachable), say so explicitly rather than
implying it passed.
