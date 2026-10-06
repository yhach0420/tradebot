"""Publish the entry precommit. Signal rows stay in the workbook."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.event_time_volume_confirmed_impulse_entry.isolation import OUT, write_overlap_n


def _put(wb: Workbook, name: str, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(name[:31])
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


def publish(report: dict[str, Any], signals: list[dict[str, Any]]) -> None:
    if write_overlap_n() != 0:
        raise RuntimeError("write_isolation")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    decision = report.get("decision") or {}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"VERDICT: {report.get('verdict')}",
        f"NEXT: {report.get('next')}",
        "",
        "The signal predicate is the frozen parent FULL impulse.",
        "The only execution is a five-second passive bid at the signal bid.",
        "Fill price equals that bid. A lower ask does not improve the fill.",
        "",
        f"FULL signals: {decision.get('signal_n')}.",
        f"Filled: {decision.get('filled_n')}. Expired: {decision.get('expired_n')}.",
        f"Fill rate: {decision.get('fill_rate')}.",
        f"Signal SHA: {(decision.get('identities') or {}).get('ENTRY_SIGNAL_SHA256')}.",
        f"Execution SHA: {(decision.get('identities') or {}).get('EXECUTION_SHA256')}.",
    ]
    if decision.get("ENTRY_FROZEN"):
        lines.append(f"ENTRY SHA: {decision['identities']['ENTRY_SHA256']}.")
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    horizons = decision.get("horizons") or {}
    fills = list(decision.get("by_date") or []) + list(decision.get("by_symbol") or []) + list(decision.get("by_sector") or [])
    wb = Workbook()
    wb.remove(wb.active)
    sheets = {
        "Manifest": [report.get("manifest") or {}],
        "Data_Seal": [report.get("data_seal") or {}],
        "Parent_Mechanism": [report.get("parent") or {}],
        "Signal_Identity": [decision.get("identities", {}).get("signal") or {}],
        "Execution_Contract": [decision.get("identities", {}).get("execution") or {}],
        "Signals": signals,
        "Fills": fills,
        "Expired": [decision.get("expired_signal_bid_180") or {}],
        "Latency": [decision.get("latency") or {}],
        "PreFill_Path": [decision.get("prefill") or {}],
        "Filled_vs_Expired": [
            {"population": "FILLED", **(decision.get("filled_signal_bid_180") or {})},
            {"population": "EXPIRED", **(decision.get("expired_signal_bid_180") or {})},
        ],
        "30s": [horizons.get("30") or {}],
        "60s": [horizons.get("60") or {}],
        "180s": [horizons.get("180") or {}],
        "300s": [horizons.get("300") or {}],
        "Original18": [decision.get("original18") or {}],
        "Extension17": [decision.get("extension17") or {}],
        "Folds": list(decision.get("folds") or []),
        "Concentration": [{"scope": "day", **(decision.get("day") or {})}, {"scope": "symbol", **(decision.get("symbol") or {})}, {"scope": "sector", **(decision.get("sector") or {})}],
        "Entry_Thesis": [report.get("thesis") or {}],
        "Verdict": [report.get("verdict_row") or {}],
        "Safety": [report.get("safety") or {}],
    }
    for name, rows in sheets.items():
        _put(wb, name, rows)
    wb.save(OUT / "audit.xlsx")
