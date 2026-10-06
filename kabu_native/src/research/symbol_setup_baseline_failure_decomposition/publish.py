"""Publish the failure decomposition. Does not touch the frozen result directory."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from research.am_c0_indicator_exit.isolation import FORBIDDEN_WRITE_PREFIXES, TODAY

_ = TODAY
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "symbol_setup_baseline_failure_decomposition_v1"
PRIOR = (
    NATIVE / "results" / "research" / "symbol_setup_baseline_complete_strategy_development_v1",
    NATIVE / "results" / "research" / "symbol_setup_baseline_complete_strategy_precommit_v1",
    NATIVE / "src" / "research" / "symbol_setup_baseline_complete_strategy_precommit",
    NATIVE / "src" / "research" / "simple_tech_entry_family",
    NATIVE / "data" / "market_capture",
)


def write_overlap_n() -> int:
    n = 0
    ws = str(OUT.resolve())
    forbidden = [p.resolve() for p in PRIOR if p.exists()]
    for pref in FORBIDDEN_WRITE_PREFIXES:
        if pref.exists():
            forbidden.append(pref.resolve())
    for item in forbidden:
        fs = str(item)
        if ws == fs or ws.startswith(fs + os.sep) or fs.startswith(ws + os.sep):
            n += 1
    return n


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


def _horizons(pack: dict[str, Any], population_name: str) -> list[dict[str, Any]]:
    rows = []
    if not pack:
        return rows
    for key, val in pack.items():
        if not isinstance(val, dict) or "mean" not in val:
            continue
        rows.append({"population": population_name, "horizon": key, **val})
    return rows


def publish(report: dict[str, Any]) -> None:
    if write_overlap_n() != 0:
        raise RuntimeError("write_isolation")
    OUT.mkdir(parents=True, exist_ok=True)
    signals = list(report.get("signals") or [])
    pending = list(report.get("pending_rows") or [])
    trades = list(report.get("trades") or [])
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    body = report
    verdict = body.get("verdict")
    primary = body.get("primary_deficiency")
    lines = [
        f"# {body.get('analysis_id')}",
        "",
        f"VERDICT: {verdict}",
        f"NEXT: {body.get('next')}",
        "",
        f"PRIMARY_DEFICIENCY: {primary}",
        "",
        "The frozen complete strategy was not modified.",
        "No second candidate was run.",
        "Diagnostic markouts are not portfolio PnL.",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    analysis = body.get("analysis") or {}
    wb = Workbook()
    wb.remove(wb.active)
    _put(wb, "Manifest", [{"verdict": verdict, "next": body.get("next"), "primary_deficiency": primary, "strategy_sha256": body.get("complete_strategy_sha256")}])
    _put(wb, "Frozen_Failure", [body.get("frozen_failure") or {}])
    _put(wb, "Stage_Funnel", analysis.get("stages") or [])
    signal_rows: list[dict[str, Any]] = []
    signal_rows.extend(_horizons(analysis.get("signal_response") or {}, "TECHNICAL_121"))
    for name, pack in (analysis.get("signal_by_group") or {}).items():
        signal_rows.extend(_horizons(pack, name))
    _put(wb, "Signal_Response", signal_rows or signals)
    _put(wb, "Stage_Contribution", analysis.get("stages") or [])
    board = analysis.get("board") or {}
    board_rows = _horizons(board.get("pass") or {}, "BOARD_PASS") + _horizons(board.get("veto") or {}, "BOARD_VETO")
    for key, diff in (board.get("pass_minus_veto") or {}).items():
        board_rows.append({"population": "PASS_MINUS_VETO", "horizon": key, **(diff or {})})
    _put(wb, "Board_Comparison", board_rows)
    pend = analysis.get("pending") or {}
    pend_rows = _horizons(pend.get("filled") or {}, "FILLED") + _horizons(pend.get("expired") or {}, "EXPIRED")
    for key, diff in (pend.get("filled_minus_expired") or {}).items():
        pend_rows.append({"population": "FILLED_MINUS_EXPIRED", "horizon": key, **(diff or {})})
    pend_rows.append({
        "population": "ATTRIBUTES",
        "filled_mean_spread_bps": pend.get("filled_mean_spread_bps"),
        "expired_mean_spread_bps": pend.get("expired_mean_spread_bps"),
        "filled_mean_ask_bid_qty_ratio": pend.get("filled_mean_ask_bid_qty_ratio"),
        "expired_mean_ask_bid_qty_ratio": pend.get("expired_mean_ask_bid_qty_ratio"),
        "adverse_selection_supported": pend.get("adverse_selection_supported"),
    })
    _put(wb, "Pending_Fill_Comparison", pend_rows or pending)
    _put(wb, "Filled_Trade_Paths", trades)
    _put(wb, "MFE_MAE", [{k: t.get(k) for k in ("date", "symbol", "mfe_bps", "mae_bps", "time_to_mfe", "time_to_mae", "realized_bps", "giveback_bps", "path_label")} for t in trades])
    _put(wb, "Exit_Loss_Analysis", trades)
    _put(wb, "Slow_Slope_Loss", [t for t in trades if t.get("exit_reason") == "SLOW_TREND_SLOPE_LOSS"])
    _put(wb, "Session_Close_RCA", [t for t in trades if t.get("exit_reason") == "SESSION_FAIL_CLOSE"])
    _put(wb, "Post_Exit_Recovery", [{k: t.get(k) for k in ("date", "symbol", "exit_reason", "recovery", "post_exit", "exit_too_early_conjunction")} for t in trades])
    htf = analysis.get("htf") or {}
    htf_rows = _horizons(htf.get("aligned") or {}, "HTF_TREND_ALIGNED") + _horizons(htf.get("not_aligned_observable") or {}, "HTF_TREND_NOT_ALIGNED_OBSERVABLE")
    htf_rows.append({"population": "COVERAGE", "observable_n": htf.get("observable_n"), "aligned_n": htf.get("aligned_n"), "unobservable_n": htf.get("unobservable_n"), "clear_and_repeatable": htf.get("clear_and_repeatable")})
    _put(wb, "Timeframe_Diagnostic", htf_rows)
    _put(wb, "Structure_Diagnostic", [{"status": analysis.get("structure_status") or body.get("structure_status"), "reason": "No causal swing lookback is bound to this baseline. A different research line's 60-day zone model was not imported."}])
    winner = [t for t in trades if t.get("date") == "20260821" and t.get("symbol") == "285A"]
    losers = [t for t in trades if not (t.get("date") == "20260821" and t.get("symbol") == "285A")]
    _put(wb, "Winner_Loser", [{"role": "WINNER", **t} for t in winner] + [{"role": "LOSER", **t} for t in losers])
    lineage_rows = []
    for name, block in (analysis.get("lineage") or {}).items():
        lineage_rows.append({"group": name, "fill_trade_n": block.get("fill_trade_n"), "fill_net_pnl_yen": block.get("fill_net_pnl_yen")})
        for label in ("candidate_signal", "board_pass", "pending", "fill_signal_response"):
            lineage_rows.extend(_horizons(block.get(label) or {}, f"{name}:{label}"))
    _put(wb, "Original18_Extension17", lineage_rows)
    _put(wb, "Primary_Deficiency", [{"primary_deficiency": primary, "verdict": verdict, "next": body.get("next"), "patterns": analysis.get("patterns"), "criterion_met": analysis.get("criterion_met")}])
    _put(wb, "No_Repair", [body.get("no_repair") or {}])
    _put(wb, "Prospective_Firewall", [{"opened": False, "rows_read": 0}])
    _put(wb, "Safety", [{"research_only": True, "submit": 0, "cancel": 0, "live": 0}])
    wb.save(OUT / "audit.xlsx")
