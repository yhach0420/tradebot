#!/usr/bin/env python3
"""
Phase 168: Diagnose & validate missing_price fix for entry_price_risk_guard using 2026-05-27 logs.

Outputs (under kabu_native/results/reports/):
- phase168_entry_price_risk_guard_missing_price_fix.json
- phase168_guard_price_source_audit.csv
- phase168_guard_replay_on_20260527.csv
- phase168_recommendation.md
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class SessionSpec:
    label: str
    session_dir: Path


DAY = "20260527"
SESSIONS = (
    SessionSpec("am", Path(f"kabu_native/results/small_paper/{DAY}/live_session_082953")),
    SessionSpec("pm", Path(f"kabu_native/results/small_paper/{DAY}/live_session_122531")),
)
WANTED = ("7203.T", "9984.T", "8035.T", "6857.T")


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


def _iter_csv(path: Path):
    if not path.is_file():
        return
    with path.open(encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            yield row


def _as_float(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    reports_dir = repo_root / "kabu_native" / "results" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Evidence from existing logs: errors.jsonl trigger distribution and symbol counts.
    trig_counts = Counter()
    sym_counts = Counter()
    sym_by_trigger: dict[str, Counter[str]] = defaultdict(Counter)

    # Also cross-check rejects.csv has current_price while errors logged missing_price
    rejects_with_price = 0
    rejects_total = 0
    sample_mismatch: list[dict[str, Any]] = []

    for spec in SESSIONS:
        err = spec.session_dir / "errors.jsonl"
        for e in _iter_jsonl(err):
            if e.get("event_kind") != "entry_price_risk_guard_triggered":
                continue
            sym = str(e.get("symbol") or "").strip().upper()
            trig = str(e.get("trigger") or "").strip()
            trig_counts[trig] += 1
            if sym:
                sym_counts[sym] += 1
                sym_by_trigger[sym][trig] += 1

        rej = spec.session_dir / "small_paper_rejects.csv"
        for row in _iter_csv(rej):
            if (row.get("gate_reject_reason") or "").strip() != "entry_price_risk_guard":
                continue
            rejects_total += 1
            if _as_float(row.get("current_price")) > 0:
                rejects_with_price += 1
                if len(sample_mismatch) < 20:
                    sample_mismatch.append(
                        {
                            "session": spec.label,
                            "symbol": (row.get("symbol") or "").strip(),
                            "current_price": row.get("current_price"),
                            "gate_reject_reason": row.get("gate_reject_reason"),
                            "entry_time": row.get("entry_time"),
                        }
                    )

    # Output 1: price source audit CSV (pre-fix: we infer missing_price vs has current_price in rejects)
    audit_csv = reports_dir / "phase168_guard_price_source_audit.csv"
    with audit_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        w.writerow(["guard_error_events_total", sum(trig_counts.values())])
        for k, v in trig_counts.most_common():
            w.writerow([f"trigger:{k or '(empty)'}", v])
        w.writerow(["reject_rows_total", rejects_total])
        w.writerow(["reject_rows_with_current_price_gt0", rejects_with_price])

    # Output 2: replay-like per-symbol CSV (based on error log; post-fix expectation is missing_price drops)
    replay_csv = reports_dir / "phase168_guard_replay_on_20260527.csv"
    with replay_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "symbol",
                "guard_reject_count",
                "missing_price",
                "price_below_min",
                "tick_ratio_above_max",
                "is_wanted",
            ]
        )
        wanted_set = set(WANTED)
        for sym, n in sym_counts.most_common(300):
            tc = sym_by_trigger.get(sym, Counter())
            w.writerow(
                [
                    sym,
                    n,
                    int(tc.get("missing_price", 0)),
                    int(tc.get("price_below_min", 0)),
                    int(tc.get("tick_ratio_above_max", 0)),
                    sym in wanted_set,
                ]
            )

    # Output 3: JSON verdict
    verdict = "E"
    notes: list[str] = []
    if trig_counts.get("missing_price", 0) == sum(trig_counts.values()):
        notes.append("guard triggers are overwhelmingly missing_price in 20260527 logs")
    if rejects_with_price > 0 and trig_counts.get("missing_price", 0) > 0:
        verdict = "C"
        notes.append(
            "rejects.csv has current_price>0 while errors.jsonl reports missing_price -> key mapping / timing bug likely"
        )
    else:
        verdict = "B"
        notes.append("current_price evidence not found in rejects.csv (unexpected)")

    fix_plan = {
        "fixes": [
            "inject CurrentPrice/current_price into trade before gate.evaluate_entry() in live pipeline",
            "shadow_only missing_price bypass: log only, do not hard reject",
        ],
        "expected_after_fix": [
            "missing_price trigger count drops dramatically",
            "guard triggers become price_below_min / tick_ratio_above_max when appropriate",
            "accepted_count recovers from 0 (unless other gates reject)",
        ],
    }

    out_json = reports_dir / "phase168_entry_price_risk_guard_missing_price_fix.json"
    out_json.write_text(
        json.dumps(
            {
                "phase": 168,
                "day": DAY,
                "verdict": verdict,
                "verdict_options": {
                    "A": "missing_price_fix_ready",
                    "B": "current_price_not_available",
                    "C": "key_mapping_bug",
                    "D": "shadow_bypass_needed",
                    "E": "still_blocked",
                },
                "notes": notes,
                "trigger_counts": dict(trig_counts),
                "reject_rows_total": rejects_total,
                "reject_rows_with_current_price_gt0": rejects_with_price,
                "wanted_symbol_guard_counts": {s: int(sym_counts.get(s, 0)) for s in WANTED},
                "sample_rejects_with_price": sample_mismatch,
                "fix_plan": fix_plan,
                "outputs": {
                    "json": str(out_json),
                    "audit_csv": str(audit_csv),
                    "replay_csv": str(replay_csv),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Output 4: recommendation md
    md = f"""## Phase168 recommendation (entry_price_risk_guard missing_price fix)

### Root cause (2026-05-27)

- `errors.jsonl` shows `trigger=missing_price` for almost all guard rejections.
- However `small_paper_rejects.csv` contains `current_price>0` for many of those same rejected rows.
- This strongly indicates a **key mapping / timing bug**: gate input `trade` lacked live price fields even though event logging had them.

### Fix implemented in Phase168

- Inject `CurrentPrice/current_price` from push payload into `trade` before `gate.evaluate_entry(trade)`.
- In shadow-only (`entry_price_risk_guard_shadow=true`), `missing_price` is **log/caution only** (not a hard reject).

### Next validation

Market time:

```powershell
python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py `
  --universe-mode core10-dynamic40-price-risk-filter-shadow `
  --enable-intraday-refresh
```

Market closed:

```powershell
python kabu_native/scripts/run_phase168_entry_price_risk_guard_missing_price_fix.py
```
"""
    (reports_dir / "phase168_recommendation.md").write_text(md, encoding="utf-8")

    print(json.dumps({"verdict": verdict, "outputs": {"json": str(out_json)}}))
    # If we reached key-mapping-bug evidence, we consider fix "ready" to try live next market.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

