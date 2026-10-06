"""Publish the complete-strategy result."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.event_time_impulse_complete_strategy_v2.isolation import OUT, write_overlap_n


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
    slim = dict(report)
    slim["trades"] = []
    (OUT / "report.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    econ = decision["economics"]
    lines = [
        f"# {report['analysis_id']}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        "The parent impulse signal is unchanged. Entry is the signal event's own ask.",
        "Exit uses the break level as support, then participation, activity, and a 30-second reconfirm gap.",
        "",
        f"Trades: {econ['trade_n']}",
        f"PnL yen: {econ['pnl']}",
        f"PF: {econ['pf']}",
        f"Primary failure: {decision.get('primary_failure')}",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    counts = report["counts"]
    wb = Workbook()
    wb.remove(wb.active)
    sheets = {
        "Manifest": [report["manifest"]],
        "Data_Seal": [report["data_seal"]],
        "Parent": [report["parent"]],
        "Entry": [report["entry"]],
        "Entry_Funnel": [counts],
        "Thesis_State": [report["thesis"]],
        "Support_Ratchet": [{"ratchet_n": counts.get("ratchet")}],
        "Reconfirm": [report["reconfirm"]],
        "Exit_Events": [{"reason": key, **value} for key, value in decision["reasons"].items()],
        "Trades": report["trades"],
        "MFE_MAE": [decision["path"]],
        "Giveback": [decision["path"]],
        "Exit_Reasons": [{"reason": key, **value} for key, value in decision["reasons"].items()],
        "Daily": [{"date": day, "trades": n} for day, n in decision["frequency"]["daily"].items()],
        "Trade_Frequency": [decision["frequency"]],
        "CAP": [{"max_concurrent": 5, "cap_blocked": counts.get("cap")}],
        "Same_Symbol": [{"same_symbol_blocked": counts.get("same_symbol")}],
        "Occupancy": [{"occupancy_blocked": counts.get("occupancy")}],
        "Reentry": [{"first_entry": counts.get("first_entry"), "reentry": counts.get("reentry")}],
        "Original18": [decision["original"]],
        "Extension17": [decision["extension"]],
        "Folds": decision["folds"],
        "Concentration": [decision["concentration"]],
        "Economics": [econ],
        "Verdict": [{"verdict": report["verdict"], "next": report["next"], "primary_failure": decision.get("primary_failure")}],
        "Safety": [report["safety"]],
    }
    for name, rows in sheets.items():
        _put(wb, name, rows)
    wb.save(OUT / "audit.xlsx")
