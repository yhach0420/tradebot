"""Publish the sequential-setup architecture result. No raw episode dump."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.stock_specific_sequential_setup.isolation import OUT, write_overlap_n


def _put(wb: Workbook, name: str, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(name)
    if not rows:
        ws.append(["none"])
        return
    keys: list[str] = []
    flat = []
    for row in rows:
        item = {}
        for k, v in row.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
            item[str(k)] = v
            if str(k) not in keys:
                keys.append(str(k))
        flat.append(item)
    ws.append(keys)
    for item in flat:
        ws.append([item.get(k) for k in keys])


def publish(report: dict[str, Any]) -> None:
    if write_overlap_n() != 0:
        raise RuntimeError("write_isolation")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    selected = report.get("selected_primary_mechanism")
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"VERDICT: {report.get('verdict')}",
        f"NEXT: {report.get('next')}",
        "",
        "This run separates trend, impulse, pullback, stabilization, and reacceleration.",
        "It does not add a filter to SIMPLE_TECH_PULLBACK_V1 and it does not search an exit.",
        "",
        f"Historical episodes: {report.get('historical_episode_n')}.",
        f"Selected mechanism: {selected}.",
        f"Failed transition: {report.get('failed_transition')}.",
        f"Date span read: {report.get('min_date')} through {report.get('max_date')}.",
        "",
        "Forward medians are outcome labels. They are not entry features.",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    wb = Workbook()
    wb.remove(wb.active)
    sheets = {
        "Manifest": [report.get("manifest") or {}],
        "Data_Seal": [report.get("data_seal") or {}],
        "Universe": [report.get("universe") or {}],
        "Episode_Definition": report.get("episode_definition") or [],
        "Trend_Context": report.get("trend_rows") or [],
        "Impulse": report.get("impulse_rows") or [],
        "Pullback": report.get("pullback_rows") or [],
        "Volume_State": report.get("volume_rows") or [],
        "RCI_State": report.get("rci_rows") or [],
        "Structure": report.get("structure_rows") or [],
        "Reacceleration": report.get("reacceleration_rows") or [],
        "Historical_DEV": report.get("dev_rows") or [],
        "Historical_C1": report.get("c1_rows") or [],
        "Chronological_Folds": report.get("fold_rows") or [],
        "Concentration": report.get("concentration_rows") or [],
        "Capture_Confirmation": [report.get("capture") or {}],
        "Actionability": [report.get("actionability") or {}],
        "Mechanism_Explanation": report.get("explanation_rows") or [],
        "Verdict": [report.get("verdict_row") or {}],
        "Safety": [report.get("safety") or {}],
    }
    for name, rows in sheets.items():
        _put(wb, name, rows)
    wb.save(OUT / "audit.xlsx")
