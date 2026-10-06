"""Publish the cumulative exit-evidence lane."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.symbol_setup_exit_causal_evidence.isolation import OUT, write_overlap_n


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
    summary = report.get("running_summary") or {}
    state = summary.get("state") or {}
    fills = summary.get("fills") or {}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"VERDICT: {report.get('verdict')}",
        f"NEXT: {report.get('next')}",
        "",
        "The K=1 exit is unchanged. This directory only accumulates later evidence.",
        "Historical development counts stay in their own ledger.",
        "",
        f"As of: {report.get('as_of')}.",
        f"Admitted prospective sessions: {report.get('admitted_session_n')}.",
        f"Combined slope-only state episodes: {state.get('combined_n')} / {state.get('target')}.",
        f"Combined filled slope-only exits: {fills.get('combined_n')} / {fills.get('target')}.",
        f"Slope versus fast-slow comparison: {summary.get('SLOPE_VS_FAST_SLOW_NOISE_COMPARISON')}.",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    wb = Workbook()
    wb.remove(wb.active)
    _put(wb, "Manifest", [{"verdict": report.get("verdict"), "next": report.get("next"), "as_of": report.get("as_of"), "admitted_session_n": report.get("admitted_session_n")}])
    _put(wb, "Frozen_Identity", [report.get("identity") or {}])
    _put(wb, "Historical_Baseline", [report.get("historical") or {}])
    _put(wb, "Prospective_Daily", report.get("daily") or [])
    _put(wb, "State_Episodes", report.get("state_episodes") or [])
    _put(wb, "Shadow_Fills", report.get("shadow_fills") or [])
    _put(wb, "Loss_Reasons", report.get("loss_reasons") or [])
    _put(wb, "Recovery", report.get("recovery") or [])
    _put(wb, "Price_Path", report.get("price_path") or [])
    _put(wb, "Session_Close", report.get("session_close") or [])
    _put(wb, "Evidence_Target", [report.get("target") or {}])
    _put(wb, "Running_Summary", [summary])
    _put(wb, "Safety", [{"research_only": True, "submit": 0, "cancel": 0, "live": 0}])
    wb.save(OUT / "audit.xlsx")
