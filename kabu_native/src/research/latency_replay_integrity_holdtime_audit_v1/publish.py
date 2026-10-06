"""Publish the integrity audit. Later latency sheets stay empty when parity fails."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.latency_replay_integrity_holdtime_audit_v1.isolation import OUT, write_overlap_n


def _put(wb: Workbook, name: str, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(name)
    if not rows:
        ws.append(["none"])
        return
    keys: list[str] = []
    flat = []
    for row in rows:
        item = {}
        for key, value in row.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            item[str(key)] = value
            if str(key) not in keys:
                keys.append(str(key))
        flat.append(item)
    ws.append(keys)
    for item in flat:
        ws.append([item.get(key) for key in keys])


def publish(report: dict[str, Any]) -> None:
    if write_overlap_n() != 0:
        raise RuntimeError("write_isolation")
    OUT.mkdir(parents=True, exist_ok=True)
    decision = report["decision"]
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    lines = [
        f"# {report['analysis_id']}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        "Frozen V2 is the source of truth. Later latency results were not interpreted.",
        f"Trade parity: {decision['trade_parity']}",
        f"Mismatch n: {decision['mismatch_n']}",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    skipped = [{"status": "NOT_RUN", "reason": "zero-latency parity failed; latency interpretation stopped"}]
    wb = Workbook()
    wb.remove(wb.active)
    sheets = {
        "summary": [{"verdict": report["verdict"], "next": report["next"], "baseline_n": decision["baseline"]["trade_n"], "baseline_pnl": decision["baseline"]["pnl"], "baseline_pf": decision["baseline"]["pf"], "zero_n": decision["zero"]["trade_n"], "zero_pnl": decision["zero"]["pnl"], "zero_pf": decision["zero"]["pf"], "trade_parity": decision["trade_parity"]}],
        "zero_latency_parity": [{"aggregate_parity": decision["aggregate_parity"], "trade_parity": decision["trade_parity"], **decision["classes"]}],
        "first_divergence": [decision["first_divergence"] or {"none": True}],
        "holdtime_distribution": [{"book": "baseline_execution", **(decision["baseline_hold"]["execution_hold"] or {})}, {"book": "zero_signal", **(decision["zero_hold"]["signal_hold"] or {})}, {"book": "zero_execution", **(decision["zero_hold"]["execution_hold"] or {})}],
        "holdtime_buckets": skipped,
        "holdtime_identity_check": [{"baseline_violations": decision["baseline_hold"]["violations"], "zero_identity_violations": decision["zero_hold"]["identity_violations"], "exit_signal_before_entry_fill": decision["zero_hold"]["exit_signal_before_entry_fill"]}],
        "fixed_trade_price_only": skipped,
        "entry_exit_delay_split": skipped,
        "matched_trade_cohort": skipped,
        "removed_trade_economics": skipped,
        "pending_audit": [decision["pending"]],
        "cap_audit": [decision["cap"]],
        "event_order_audit": [decision["first_divergence"] or {"none": True}],
    }
    for name, rows in sheets.items():
        _put(wb, name, rows)
    wb.save(OUT / "audit.xlsx")
