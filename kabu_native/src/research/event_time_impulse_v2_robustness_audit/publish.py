"""Publish the robustness audit."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.event_time_impulse_v2_robustness_audit.isolation import OUT, write_overlap_n


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
    body = dict(report)
    body.pop("baseline_trades", None)
    (OUT / "report.json").write_text(json.dumps(body, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    audit = report["audit"]
    lines = [
        f"# {report['analysis_id']}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        "The frozen V2 rules are unchanged. Latency, re-entry, and hold time are diagnostics.",
        "",
        f"Parity exact: {audit['parity']['exact']}",
        f"Baseline trades / PnL / PF: {audit['parity']['trade_n']} / {audit['parity']['pnl']} / {audit['parity']['pf']}",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    wb = Workbook()
    wb.remove(wb.active)
    latency_rows = []
    for ms, block in audit["latency"].items():
        latency_rows.append({"latency_ms": ms, **block["all"], **block["counts"]})
    sheets = {
        "Parity": [audit["parity"]],
        "Baseline": [audit["baseline"]["all"]],
        "Latency": latency_rows,
        "Hold_Time": audit["hold_buckets"],
        "First_Entry": [audit["first_entry"]],
        "Reentry": [audit["reentry"]],
        "Reentry_Ordinal": audit["reentry_ordinal"],
        "Chatter": [audit["chatter"]],
        "Ratchet": audit["ratchet"],
        "Exit_Reasons": [{"latency_ms": ms, "reason": reason, **pack} for ms, block in audit["latency"].items() for reason, pack in block["reasons"].items()],
        "Friction": audit["friction"],
        "Daily": [audit["daily"]],
        "Original18": [{"latency_ms": ms, **pack} for ms, pack in audit["original_by_latency"].items()],
        "Extension17": [{"latency_ms": ms, **pack} for ms, pack in audit["extension_by_latency"].items()],
        "Folds": [{"latency_ms": ms, **fold} for ms, block in audit["latency"].items() for fold in block["folds"]],
        "Clock": [{"block": key, **pack} for key, pack in audit["clock"]["clock_30m"].items()],
        "Session": [{"session": key, **pack} for key, pack in audit["clock"]["session"].items()],
        "Symbol": [{"symbol": key, **pack} for key, pack in audit["symbol"].items()],
        "Sector": [{"sector": key, **pack} for key, pack in audit["sector"].items()],
        "Tails": [{"cut": key, **pack} for key, pack in audit["tails"].items()],
        "Opportunity": [audit["opportunity"]],
        "Verdict": [{"verdict": report["verdict"], "next": report["next"]}],
        "Safety": [report["safety"]],
    }
    for name, rows in sheets.items():
        _put(wb, name, rows)
    wb.save(OUT / "audit.xlsx")
