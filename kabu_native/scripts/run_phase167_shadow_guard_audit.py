#!/usr/bin/env python3
"""
Phase 167: Audit entry_price_risk_guard behavior in shadow-only live sessions.

Outputs (under kabu_native/results/reports/):
- phase167_shadow_guard_audit.md
- phase167_shadow_guard_symbols.csv
- phase167_shadow_guard_flow.txt
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SessionSpec:
    label: str
    session_dir: Path


WANTED = ("7203.T", "9984.T", "8035.T", "6857.T")


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_jsonl(path: Path):
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    reports_dir = repo_root / "kabu_native" / "results" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    day = "20260527"
    base = repo_root / "kabu_native" / "results" / "small_paper" / day
    sessions = [
        SessionSpec("am", base / "live_session_082953"),
        SessionSpec("pm", base / "live_session_122531"),
    ]

    # Aggregate from errors.jsonl (authoritative guard trigger log)
    sym_counts: Counter[str] = Counter()
    sym_triggers: dict[str, Counter[str]] = defaultdict(Counter)
    total_events = 0
    for spec in sessions:
        for e in _iter_jsonl(spec.session_dir / "errors.jsonl"):
            if e.get("event_kind") != "entry_price_risk_guard_triggered":
                continue
            sym = str(e.get("symbol") or "").strip().upper()
            trig = str(e.get("trigger") or "").strip()
            if not sym:
                continue
            sym_counts[sym] += 1
            sym_triggers[sym][trig] += 1
            total_events += 1

    # Write top 100 symbols
    csv_path = reports_dir / "phase167_shadow_guard_symbols.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "symbol",
                "reject_count",
                "missing_price",
                "price_below_min",
                "tick_ratio_above_max",
            ]
        )
        for sym, n in sym_counts.most_common(100):
            tc = sym_triggers.get(sym, Counter())
            w.writerow(
                [
                    sym,
                    n,
                    int(tc.get("missing_price", 0)),
                    int(tc.get("price_below_min", 0)),
                    int(tc.get("tick_ratio_above_max", 0)),
                ]
            )

    # Load session summaries for accepted/rejected counts
    am_sum = _read_json(sessions[0].session_dir / "small_paper_summary.json")
    pm_sum = _read_json(sessions[1].session_dir / "small_paper_summary.json")

    wanted_hits = {s: int(sym_counts.get(s, 0)) for s in WANTED}

    flow_txt = """Phase167 shadow guard flow (entry_price_risk_guard)

Key point: entry_price_risk_guard_shadow=true (shadow_only) is NOT used to bypass rejection today.
It is only recorded as a config/summary field.

1) Candidate event is always created
   - kabu_native/src/small_paper/pilot_runner.py

2) Gate decision is computed
   - kabu_native/src/research/exposure_gate.py : ExposureGate.evaluate_entry()
   - If entry_price_risk_guard is configured:
       gr = entry_price_risk_guard.check(trade)
       if gr.blocked:
           return GateDecision(accept=False, reason='entry_price_risk_guard', ...)

3) Rejected path (decision.accept == False)
   - kabu_native/src/small_paper/pilot_runner.py
   - Writes a 'rejected' event and increments rejected_by_entry_price_risk_guard
   - Also writes errors.jsonl record:
       event_kind='entry_price_risk_guard_triggered'
"""
    flow_path = reports_dir / "phase167_shadow_guard_flow.txt"
    flow_path.write_text(flow_txt, encoding="utf-8")

    md = f"""## Phase167 Shadow Guard Audit (2026-05-27)

### Summary

- **accepted_count=0** is explained by the gate returning **accept=False** with reason **entry_price_risk_guard** for every candidate.
- **shadow_only=true / entry_price_risk_guard_shadow=true currently does not disable the rejection path**.
- The guard triggers recorded in `errors.jsonl` show the dominant trigger is **missing_price** (tick/ratio fields are 0.0).

### 1) shadow mode時に entry_price_risk_guard が reject_entry を実行していないか

**実行しています（現状の実装）**。
- Guard is evaluated in `ExposureGate.evaluate_entry()` and can return `accept=False`.

### 2) guard判定後のコードフロー

See `{flow_path.as_posix()}`.

### 3) accepted_count=0 の直接原因

- AM: accepted_count={am_sum.get("accepted_count")} rejected_by_entry_price_risk_guard={am_sum.get("rejected_by_entry_price_risk_guard")}
- PM: accepted_count={pm_sum.get("accepted_count")} rejected_by_entry_price_risk_guard={pm_sum.get("rejected_by_entry_price_risk_guard")}

### 4) entry_price_risk_guard に該当した銘柄 TOP100

Output: `{csv_path.as_posix()}`

### 5) 指定4銘柄が guard対象になっていないか

- 7203.T: {wanted_hits["7203.T"]}
- 9984.T: {wanted_hits["9984.T"]}
- 8035.T: {wanted_hits["8035.T"]}
- 6857.T: {wanted_hits["6857.T"]}

### 6) shadow_only=true なのに candidate が reject 扱いになる理由

- `shadow_only` is recorded on the guard config, but the current decision flow uses **accept/reject** for metrics and event streams.
- So a shadow run can still generate **rejected** events/counters even though **no orders** are placed.

### Evidence

- total guard trigger events (errors.jsonl): {total_events}
- top1 guarded symbol: {sym_counts.most_common(1)[0] if sym_counts else None}
"""
    md_path = reports_dir / "phase167_shadow_guard_audit.md"
    md_path.write_text(md, encoding="utf-8")

    print(json.dumps({"outputs": {"md": str(md_path), "csv": str(csv_path), "flow": str(flow_path)}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

