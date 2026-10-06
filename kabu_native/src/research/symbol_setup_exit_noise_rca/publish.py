"""Publish the exit-noise RCA."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.symbol_setup_exit_noise_rca.isolation import OUT, write_overlap_n


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
    analysis = report.get("analysis") or {}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"VERDICT: {report.get('verdict')}",
        f"NEXT: {report.get('next')}",
        "",
        "K remains 1. This diagnosis does not choose a longer persistence.",
        "Board-pass paths are state sequences, not trades.",
        "Later bids after an actual exit are labeled COUNTERFACTUAL_DIAGNOSTIC and are not portfolio PnL.",
        "",
        f"Board-pass slope-only episodes: {analysis.get('slope_episode_n')}.",
        f"Temporary thesis recovery: {analysis.get('temporary_n')} ({analysis.get('temporary_rate')}).",
        f"Temporary and price recovered: {analysis.get('price_recovered_n')} ({analysis.get('price_recovered_rate')}).",
        f"Terminal: {analysis.get('terminal_n')} ({analysis.get('terminal_rate')}).",
        f"Ambiguous: {analysis.get('ambiguous_n')} ({analysis.get('ambiguous_rate')}).",
        f"K1 noise hypothesis: {analysis.get('k1_noise_hypothesis')}.",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    episodes = report.get("episodes") or []
    slope = [row for row in episodes if row.get("slope_only")]
    wb = Workbook()
    wb.remove(wb.active)
    _put(wb, "Manifest", [{"verdict": report.get("verdict"), "next": report.get("next"), "k1_noise_hypothesis": analysis.get("k1_noise_hypothesis")}])
    _put(wb, "Identity", [report.get("identity") or {}])
    _put(wb, "Population_Parity", [report.get("parity") or {}])
    _put(wb, "Actual_Fills", analysis.get("actual_rows") or [])
    _put(wb, "BoardPass_State_Episodes", episodes)
    _put(wb, "Slope_Loss_Episodes", slope)
    _put(wb, "Loss_Duration", [analysis.get("duration") or {}])
    _put(wb, "Thesis_Recovery", analysis.get("recovery_by_reason") or [])
    _put(wb, "Price_Recovery", analysis.get("price_response") or [])
    _put(wb, "Actual_Fill_Counterfactual_Path", analysis.get("counterfactual_rows") or [])
    _put(wb, "Session_Close_Separate", analysis.get("session_close_rows") or [])
    _put(wb, "Prior_V27_V28_Context", [report.get("prior_context") or {}])
    _put(wb, "Classification", [{"class": "TEMPORARY_SLOPE_INTERRUPTION", "n": analysis.get("temporary_n"), "rate": analysis.get("temporary_rate")}, {"class": "TEMPORARY_AND_PRICE_RECOVERED", "n": analysis.get("price_recovered_n"), "rate": analysis.get("price_recovered_rate")}, {"class": "TERMINAL_SLOPE_FAILURE", "n": analysis.get("terminal_n"), "rate": analysis.get("terminal_rate")}, {"class": "AMBIGUOUS_SLOPE_FAILURE", "n": analysis.get("ambiguous_n"), "rate": analysis.get("ambiguous_rate")}])
    _put(wb, "Verdict", [{"verdict": report.get("verdict"), "next": report.get("next"), "persistence_mechanism_warranted": analysis.get("persistence_mechanism_warranted"), "k_selected": False}])
    _put(wb, "No_Repair", [report.get("no_repair") or {}])
    _put(wb, "Prospective_Firewall", [{"opened": False, "rows_read": 0}])
    _put(wb, "Safety", [{"research_only": True, "submit": 0, "cancel": 0, "live": 0}])
    wb.save(OUT / "audit.xlsx")
