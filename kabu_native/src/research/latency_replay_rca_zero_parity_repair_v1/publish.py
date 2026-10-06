"""Publish the parity repair audit."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.latency_replay_rca_zero_parity_repair_v1.isolation import OUT, write_overlap_n


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
        f"Trade parity: {decision['trade_parity']}",
        f"Mismatch n: {decision['mismatch_n']}",
        "Frozen V2 was not modified.",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    wb = Workbook()
    wb.remove(wb.active)
    fixed_rows = []
    causal_rows = []
    for ms, modes in (decision.get("fixed") or {}).items():
        for mode, econ in modes.items():
            fixed_rows.append({"latency_ms": ms, "mode": mode, **econ})
    for ms, block in (decision.get("causal") or {}).items():
        causal_rows.append({"latency_ms": ms, "trade_n": block["trade_n"], "pnl": block["pnl"], "pf": block["pf"], **block["counts"]})
    sheets = {
        "summary": [{"verdict": report["verdict"], "next": report["next"], "trade_parity": decision["trade_parity"], "mismatch_n": decision["mismatch_n"], "hold_parity": decision.get("hold_parity")}],
        "missing_rca": decision["missing_rca"],
        "zero_parity": [{"baseline": decision["baseline"], "zero": decision["zero"], "classes": decision["classes"]}],
        "hold": [decision["baseline_execution_quant"], decision["zero_execution_quant"]],
        "exit_delays": decision["exit_delays"]["top"],
        "fixed_price": fixed_rows or [{"status": "not_interpreted"}],
        "causal": causal_rows or [{"status": "not_interpreted"}],
        "repair": [decision["repair"]],
        "safety": [report["safety"]],
    }
    for name, rows in sheets.items():
        _put(wb, name, rows)
    wb.save(OUT / "audit.xlsx")
