"""Publish Family D. Summaries only."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.relative_strength_family_d.isolation import OUT, write_overlap_n


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


def _horizon_rows(label: str, block: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"population": label, "horizon": horizon, **stat} for horizon, stat in block.items()]


def publish(report: dict[str, Any]) -> None:
    if write_overlap_n() != 0:
        raise RuntimeError("write_isolation")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    decision = report["decision"]
    lines = [
        f"# {report['analysis_id']}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        "Family D is the predeclared relative-strength conjunction. It does not repair the breakout or impulse results.",
        "",
        f"FULL n: {decision['full_n']}",
        f"CONTROL n: {decision['control_n']}",
        f"+5m FULL mean: {(decision.get('primary') or {}).get('mean')}",
        f"+5m CONTROL mean: {(decision.get('control5') or {}).get('mean')}",
        f"FULL minus CONTROL: {decision.get('delta')}",
        f"Max historical date read: {report.get('max_date')}",
        f"Max capture date read: {report.get('max_capture_date_read')}",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    capture = report.get("capture") or {}
    wb = Workbook()
    wb.remove(wb.active)
    sheets = {
        "Manifest": [report["manifest"]],
        "Data_Seal": [report["data_seal"]],
        "Universe": [report["universe"]],
        "Market_Context": [report["market_context"]],
        "Sector_Context": [report["sector_context"]],
        "Relative_Strength": [report["relative_strength"]],
        "Participation": [report["participation"]],
        "Technical_Continuation": [report["technical_continuation"]],
        "Full": _horizon_rows("FULL", decision["horizons"]["FULL"]),
        "Control": _horizon_rows("CONTROL", decision["horizons"]["CONTROL"]),
        "DEV": [decision["dev"]],
        "C1": [decision["c1"]],
        "Folds": list(decision["dev_folds"]) + list(decision["c1_folds"]),
        "Relative_Return": [decision["relative"]],
        "Concentration": [decision["concentration"]],
        "Capture_Confirmation": [capture],
        "Actionability": [report["actionability"]],
        "Verdict": [{"verdict": report["verdict"], "next": report["next"], "historical_pass": decision["historical_pass"]}],
        "Safety": [report["safety"]],
    }
    for name, rows in sheets.items():
        _put(wb, name, rows)
    wb.save(OUT / "audit.xlsx")
