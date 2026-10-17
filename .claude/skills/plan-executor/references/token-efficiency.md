# Token-efficiency playbook

Executing a plan touches a lot of files and runs a lot of commands. Done naively,
the orchestrating context fills up with file dumps, command output, and dead ends,
and quality degrades as the window fills. The point of this playbook is to keep
the *main* thread lean and push the bulky work to the edges. None of this is busywork
for its own sake — every pattern here exists to spend tokens where they buy
correctness and save them everywhere else.

## The orchestrator / worker split (when subagents are available)

If you have a `Task`/subagent tool, run as an **orchestrator**: you hold the plan,
the probe result, and the running status; subagents do the heavy reading and
editing in their own contexts and hand back only conclusions.

Delegate a chunk to a subagent when it is **isolatable** — a coherent slice of the
plan that can be done and checked without the orchestrator watching every line
(e.g. "implement the retriever module and its unit test", "wire up MLflow logging
in the training loop"). Give the subagent: the relevant slice of the plan, the
probe result, the specific files in scope, and a crisp definition of done.

Tell each subagent to **return a compact summary, not its work**: files
created/modified, a short diff or description of what changed per file, the
commands it ran with exit codes, and anything that surprised it or that the next
step needs to know. It should **not** paste whole file contents back — those live
on disk; the orchestrator can read a targeted range later if needed.

Good things to parallelize: independent modules, independent plan files in a
folder that don't depend on each other, and verification of separate components.
Keep anything that edits the *same* files serial to avoid clobbering.

For the fuller treatment — running several workers as a **team**, role-splitting an
implementer from an independent verifier, and a diagnosis pair for stubborn bugs —
see `agent-teams.md`. For running the build/verify/fix cycle deliberately (defined
exit conditions, cheapest-checks-first, bounded attempts, a critic pass), see
`loop-engineering.md`.

## When there are no subagents (plain chat / Claude.ai)

Execute directly, but apply the same discipline to your own context:

- **Locate, then read narrowly.** Use grep/glob to find the exact symbol or file,
  then read just the relevant range — don't read whole files to find one function.
- **Don't re-read.** If a file is already in context and unchanged, work from what
  you have.
- **Edit in place with targeted replacements** rather than reprinting whole files.
- **Summarize as you go.** Keep a short running note of what's done and what's left
  so you don't re-derive state.

## Patterns that save tokens regardless of environment

- **Probe once, reuse.** Run `probe_project.py` a single time and keep the small
  JSON. Don't re-explore the project structure on every step.
- **Cheap checks first.** Run syntax/typecheck/imports before a full test suite or
  a real model call — most breakages surface in the cheap check, and you avoid
  paying for a long run that was doomed anyway. For GenAI work this also means
  fewer real API/model calls, which is latency and money, not just tokens.
- **Read output, don't echo it.** Pipe long command output to a file and grep for
  the failure (`... > /tmp/out.log 2>&1; tail -n 40 /tmp/out.log` or
  `grep -i error`) instead of letting thousands of lines into context.
- **Bound the loops.** Don't retry the same fix endlessly. After a couple of honest
  attempts at a stubborn failure, step back, reassess the root cause (and whether
  the *plan* is wrong), and if still stuck, report it clearly rather than burning
  the window thrashing.
- **Stop when verified.** Once a step's checks pass, move on — don't re-verify
  things that haven't changed.
