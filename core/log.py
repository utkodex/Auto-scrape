"""Action log: actions.jsonl (machine) + actions.md (human), masked of profile values and secrets.

Contract: .claude/specs/02.architecture.md §7.6.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from datetime import datetime
from pathlib import Path

SECRET_RE = re.compile(r"(password\s*=|cookie:|set-cookie|bearer\s+[a-z0-9\-_\.]{8,}|api[_-]?key\s*=)", re.I)
STEP_RE = re.compile(r"\bstep_\d{2}_[a-z0-9_]+\b")
MAX_FIELD = 200


class ActionLog:
    def __init__(self, run_dir: Path | str, mask_values: list[str] | None = None, run_id: str | None = None,
                 echo: bool = False) -> None:
        self.echo = echo
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl = self.run_dir / "actions.jsonl"
        self.md = self.run_dir / "actions.md"
        self.run_id = run_id or self.run_dir.name
        self.mask_values = [v for v in (mask_values or []) if v and len(str(v)) >= 3]
        self.mask_values.sort(key=len, reverse=True)
        self._mask_res = [re.compile(re.escape(str(v)), re.I) for v in self.mask_values]
        self._rows: list[dict] = []
        self._t0 = time.monotonic()
        if self.jsonl.exists():
            for line in self.jsonl.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        self._rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    # -- masking ------------------------------------------------------------------
    def mask(self, text: str | None) -> str:
        if text is None:
            return ""
        s = str(text)
        for rx in self._mask_res:
            s = rx.sub("***", s)
        return s

    def _clean(self, text) -> str:
        s = self.mask(text).replace("\n", " ⏎ ")
        return s if len(s) <= MAX_FIELD else s[: MAX_FIELD - 1] + "…"

    # -- writing ------------------------------------------------------------------
    def row(
        self,
        actor: str,
        tool: str,
        inp: str = "",
        result: str = "",
        tok_in: int | None = None,
        tok_out: int | None = None,
        s: float | None = None,
        round: int | None = None,
        **extra,
    ) -> dict:
        raw_in, raw_res = str(inp or ""), str(result or "")
        if SECRET_RE.search(raw_in) or SECRET_RE.search(raw_res):
            rec = {"t": _now(), "run": self.run_id, "round": round, "actor": "harness",
                   "tool": "redacted", "in": "", "result": "[redacted line]"}
        else:
            rec = {"t": _now(), "run": self.run_id, "round": round, "actor": actor, "tool": tool,
                   "in": self._clean(raw_in), "result": self._clean(raw_res)}
            if tok_in is not None:
                rec["tok_in"] = int(tok_in)
            if tok_out is not None:
                rec["tok_out"] = int(tok_out)
            if s is not None:
                rec["s"] = round_s(s)
            for k, v in extra.items():
                rec[k] = self._clean(v) if isinstance(v, str) else v
        self._rows.append(rec)
        with self.jsonl.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.write_md()
        if self.echo:
            self._echo(rec)
        return rec

    def _echo(self, rec: dict) -> None:
        meta = []
        if rec.get("tok_in") is not None:
            meta.append(f"{rec['tok_in']}/{rec.get('tok_out', 0)} tok")
        if rec.get("s") is not None:
            meta.append(f"{rec['s']}s")
        inp = f" {rec['in']}" if rec.get("in") else ""
        res = f" -> {rec['result']}" if rec.get("result") else ""
        tail = f"  ({', '.join(meta)})" if meta else ""
        rnd = rec.get("round")
        head = f"[r{rnd:02d}]" if isinstance(rnd, int) else "[ -- ]"
        print(f"{head} {rec.get('actor', '?'):7} {rec.get('tool', '?'):12}{inp}{res}{tail}", flush=True)

    def rows(self) -> list[dict]:
        return list(self._rows)

    def write_md(self) -> Path:
        lines = [f"# Actions — {self.run_id}", ""]
        current = None
        for r in self._rows:
            m = STEP_RE.search(r.get("in", "") or "")
            step = m.group(0) if m else None
            if step and step != current:
                lines.append(f"\n## {step}\n")
                current = step
            who = r.get("actor", "?")
            tool = r.get("tool", "?")
            meta = []
            if r.get("tok_in") is not None:
                meta.append(f"{r['tok_in']}/{r.get('tok_out', 0)} tok")
            if r.get("s") is not None:
                meta.append(f"{r['s']}s")
            suffix = f" ({', '.join(meta)})" if meta else ""
            inp = f" `{r['in']}`" if r.get("in") else ""
            res = f" → {r['result']}" if r.get("result") else ""
            lines.append(f"- {r.get('t', '')[11:19]} **{who}** {tool}{inp}{res}{suffix}")
        self.md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self.md

    # -- summary ------------------------------------------------------------------
    def summary(self) -> dict:
        qwen = [r for r in self._rows if r.get("actor") == "qwen"]
        tok_in = [r["tok_in"] for r in qwen if r.get("tok_in") is not None]
        tok_out = [r["tok_out"] for r in qwen if r.get("tok_out") is not None]
        secs = [r["s"] for r in self._rows if r.get("s") is not None]
        human = [r["s"] for r in self._rows if r.get("tool") == "ask_human" and r.get("s") is not None]
        tries: dict[str, int] = {}
        first_result: dict[str, str] = {}
        for r in self._rows:
            if r.get("tool") == "run_step" and r.get("actor") == "harness":
                m = STEP_RE.search(r.get("in", ""))
                if not m:
                    continue
                step = m.group(0)
                tries[step] = tries.get(step, 0) + 1
                first_result.setdefault(step, (r.get("result") or "")[:4])
        fixes = [r for r in self._rows if r.get("tool") == "edit_file" and (r.get("result") or "").startswith("ok")]
        return {
            "rounds": len(qwen),
            "tok_in_total": sum(tok_in),
            "tok_out_total": sum(tok_out),
            "tok_in_p95": _p95(tok_in),
            "tok_out_max": max(tok_out) if tok_out else 0,
            "qwen_seconds_p50": statistics.median([r["s"] for r in qwen if r.get("s") is not None]) if any(r.get("s") is not None for r in qwen) else 0.0,
            "seconds_total": round(sum(secs), 1),
            "seconds_human": round(sum(human), 1),
            "tries_per_step": tries,
            "first_try_pass": [s for s, res in first_result.items() if res == "PASS"],
            "steps_seen": list(tries.keys()),
            "undo_count": sum(1 for r in self._rows if r.get("tool") == "undo"),
            "fix_count": len(fixes),
            "stop_rows": [r.get("result") for r in self._rows if r.get("tool") == "stop"],
        }


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    vals = sorted(values)
    idx = max(0, int(round(0.95 * (len(vals) - 1))))
    return vals[idx]


def round_s(s: float) -> float:
    return round(float(s), 1)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
