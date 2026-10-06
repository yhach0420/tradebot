"""Publish the first-reconfirm candidate."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.event_time_reconfirmed_impulse_complete_strategy_v1.isolation import OUT, write_overlap_n


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
    zero = decision["blocks"]["0"]["all"]
    lines = [
        f"# {report['analysis_id']}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        "The initial FULL signal starts a watch. Entry is the first higher reconfirm.",
        "",
        f"Zero-delay trades / PnL / PF: {zero['trade_n']} / {zero['pnl']} / {zero['pf']}",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    wb = Workbook()
    wb.remove(wb.active)
    sheets = {
        "Funnel": [{"latency_ms": ms, **counts} for ms, counts in decision["counts"].items()],
        "Economics": [{"latency_ms": ms, **block["all"]} for ms, block in decision["blocks"].items()],
        "Original18": [{"latency_ms": ms, **block["ORIGINAL18"]} for ms, block in decision["blocks"].items()],
        "Extension17": [{"latency_ms": ms, **block["EXTENSION17"]} for ms, block in decision["blocks"].items()],
        "Folds": [{"latency_ms": ms, **fold} for ms, block in decision["blocks"].items() for fold in block["folds"]],
        "Exit_Reasons": [{"latency_ms": ms, "reason": reason, **pack} for ms, block in decision["blocks"].items() if ms in ("0", "100", "250") for reason, pack in block["reasons"].items()],
        "Post_Entry_Ratchet": [{"latency_ms": ms, **row} for ms, block in decision["blocks"].items() if ms in ("0", "100", "250") for row in block["ratchets"]],
        "Path": [{"latency_ms": ms, **block["path"]} for ms, block in decision["blocks"].items()],
        "Friction_100": decision["friction_100"],
        "Concentration": [decision["concentration_100"]],
        "Frequency": [decision["frequency"]],
        "Verdict": [{"verdict": report["verdict"], "next": report["next"]}],
        "Safety": [report["safety"]],
    }
    for name, rows in sheets.items():
        _put(wb, name, rows)
    wb.save(OUT / "audit.xlsx")
