"""Publish the nominal-versus-actual fill audit."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.nominal_latency_actual_fill_delay_audit_v1.isolation import OUT, write_overlap_n


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
        "Current fill function is _at: first executable quote at or after the target.",
        f"0ms parity: {decision['trade_parity']}",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    wb = Workbook()
    wb.remove(wb.active)
    delay_rows = []
    causal_rows = []
    for model, by_ms in decision["fixed"].items():
        for ms, block in by_ms.items():
            delay_rows.append({"model": model, "latency_ms": ms, "n": block["n"], "pnl": block["pnl"], "pf": block["pf"], **{f"wait_{k}": v for k, v in block["target_wait"].items()}, **{f"actual_{k}": v for k, v in block["actual_signal_to_fill"].items()}})
    for model, by_ms in decision["causal_models"].items():
        for ms, block in by_ms.items():
            causal_rows.append({"model": model, "latency_ms": ms, "trade_n": block["trade_n"], "pnl": block["pnl"], "pf": block["pf"], **block["counts"]})
    sheets = {
        "summary": [{"verdict": report["verdict"], "fill": decision["fill_semantic"], "parity": decision["trade_parity"], "forced_last_bid": decision["forced_last_bid_close_n"]}],
        "delay": delay_rows,
        "causal": causal_rows,
        "safety": [report["safety"]],
    }
    for name, rows in sheets.items():
        _put(wb, name, rows)
    wb.save(OUT / "audit.xlsx")
