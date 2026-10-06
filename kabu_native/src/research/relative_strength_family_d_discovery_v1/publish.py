"""Publish the Family D V1 discovery."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.relative_strength_family_d_discovery_v1.isolation import OUT, write_overlap_n


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
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    decision = report["decision"]
    lines = [
        f"# {report['analysis_id']}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        "This closes only the predeclared V1 operationalization.",
        f"Failed premises: {', '.join(decision['failed_premises']) or 'none'}",
        "ENTRY_FROZEN: false",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    wb = Workbook()
    wb.remove(wb.active)
    sheets = {
        "Primary": [{"population": "FULL", **decision["primary"]}, {"population": "CONTROL", **decision["control5"]}],
        "Horizons": [{"population": name, "horizon": horizon, **stat} for name, block in decision["horizons"].items() for horizon, stat in block.items()],
        "Relative": [decision["relative"]],
        "Path": [decision["path"]],
        "DEV": [decision["dev"], decision["dev_control"]],
        "C1": [decision["c1"], decision["c1_control"]],
        "Folds": decision["dev_folds"] + decision["c1_folds"],
        "Frequency": [decision["frequency"]],
        "Anatomy": [decision["anatomy"]],
        "Premises": [{"premise": key, "supports": value} for key, value in decision["premise_direction"].items()],
        "Failed": [{"premise": name} for name in decision["failed_premises"]] or [{"premise": "none"}],
        "Verdict": [{"verdict": report["verdict"], "next": report["next"], "historical_pass": decision["historical_pass"]}],
        "Safety": [report["safety"]],
    }
    for name, rows in sheets.items():
        _put(wb, name, rows)
    wb.save(OUT / "audit.xlsx")
