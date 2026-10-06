"""Publish the acceptance result. Prior entry artifacts stay untouched."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.event_time_breakout_acceptance_entry.isolation import OUT, write_overlap_n


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


def _compare_rows(block: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for horizon, pair in block.items():
        for name, stat in pair.items():
            rows.append({"horizon_sec": horizon, "population": name, **stat})
    return rows


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
        "The parent impulse signal is unchanged.",
        "Acceptance is five seconds above the frozen pre-break high.",
        "The accepted entry price is the confirmation ask.",
        "",
        f"Accepted: {decision.get('accepted_n')}. Failed: {decision.get('failed_n')}. Unavailable: {decision.get('unavailable_n')}.",
    ]
    if decision.get("information"):
        lines.extend(["", str(decision["information"])])
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    horizons = decision.get("horizons") or {}
    states = {"BREAKOUT_ACCEPTED_5S": 0, "BREAKOUT_FAILED_5S": 0, "ACCEPTANCE_UNAVAILABLE": 0, "BREAKOUT_NOT_ACCEPTED": 0}
    for row in signals:
        state = str(row.get("state"))
        if state in states:
            states[state] += 1
    wb = Workbook()
    wb.remove(wb.active)
    sheets = {
        "Manifest": [report.get("manifest") or {}],
        "Data_Seal": [report.get("data_seal") or {}],
        "Parent": [report.get("parent") or {}],
        "Acceptance_Contract": [decision.get("identities", {}).get("acceptance") or {}],
        "Signals": [{"state": key, "n": value} for key, value in states.items()],
        "Accepted": [horizons.get("180") or {}],
        "Failed": [((decision.get("signal_compare") or {}).get("180") or {}).get("failed") or {}],
        "Unavailable": [decision.get("unavailable_reasons") or {}],
        "Confirmation": [decision.get("confirm_latency") or {}],
        "Execution": [decision.get("identities", {}).get("execution") or {}],
        "30s": [horizons.get("30") or {}],
        "60s": [horizons.get("60") or {}],
        "180s": [horizons.get("180") or {}],
        "300s": [horizons.get("300") or {}],
        "Original18": [decision.get("original18") or {}],
        "Extension17": [decision.get("extension17") or {}],
        "Folds": list(decision.get("folds") or []),
        "Accepted_vs_Failed": _compare_rows(decision.get("signal_compare") or {}),
        "Concentration": [
            {"scope": "day", **(decision.get("day") or {})},
            {"scope": "symbol", **(decision.get("symbol") or {})},
            {"scope": "sector", **(decision.get("sector") or {})},
        ],
        "Verdict": [report.get("verdict_row") or {}],
        "Safety": [report.get("safety") or {}],
    }
    for name, sheet_rows in sheets.items():
        _put(wb, name, sheet_rows)
    wb.save(OUT / "audit.xlsx")
