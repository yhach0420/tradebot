"""Orchestrate Canonical Strategy Root Cause Closure."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.canonical_strategy_root_cause.constants import (
    CANCEL,
    LIVE_ORDER,
    OUT_ROOT,
    SAMPLE_STRIDE,
    SOT_REPAIR,
    SUBMIT,
)
from research.canonical_strategy_root_cause.engine import run_full_analysis
from research.canonical_strategy_root_cause.report import emit_artifacts

JST = ZoneInfo("Asia/Tokyo")


def run_root_cause(
    *,
    run_id: Optional[str] = None,
    days: Optional[list[str]] = None,
    out_root: Optional[Path] = None,
    stride: int = SAMPLE_STRIDE,
    test_results: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = (out_root or OUT_ROOT) / run_id
    analysis = run_full_analysis(days=days, stride=stride)
    tests = test_results or {"rows": [{"name": "deferred", "status": "pending"}], "all_passed": False}

    decisions = analysis.get("decisions") or []
    final = "CANONICAL_ROOT_CAUSE_CLOSED"
    if "CANONICAL_STRATEGY_REBUILD_REQUIRED" in decisions:
        final = "CANONICAL_STRATEGY_REBUILD_REQUIRED"
    elif "PBV2_MOMENTUM_CORE_REJECT" in decisions and "CURRENT_BOARD_EXIT_ARCHITECTURE_REJECT" in decisions:
        final = "MULTIPLE_ROOT_CAUSES_CLOSED"

    ce = analysis.get("C_event") or {}
    opp = analysis.get("opportunity") or {}
    xc = analysis.get("exit_controls") or {}
    ep = analysis.get("episodes") or {}
    sp = analysis.get("spread_stop") or {}
    imm = analysis.get("immediate_exit") or {}
    attr = analysis.get("attribution") or {}
    reentry = analysis.get("reentry") or {}

    verdict = {
        "final_verdict": final,
        "primary_root_cause": analysis.get("primary_root_cause"),
        "LEGACY_REPLAY_DETERMINISM_PASS": analysis.get("determinism_pass"),
        "LEGACY_RUNTIME_PARITY_NOT_EVALUABLE": True,
        "CAPTURE_ONLY_CONTINUE": True,
        "NO_PAPER_ENTRY": True,
        "LIVE_TRADING_BLOCKED": True,
        "decisions": decisions,
    }

    payload: dict[str, Any] = {
        "run_id": run_id,
        "phase": "canonical_strategy_root_cause",
        "sot": str(SOT_REPAIR),
        "submit": SUBMIT,
        "cancel": CANCEL,
        "live_order": LIVE_ORDER,
        "mainline_changed": False,
        "source_audit": {
            "repair_sot": str(SOT_REPAIR),
            "P0_pnl": -47028.61,
            "P1_pnl": -397775.52,
            "P2_pnl": -782397.62,
            "P3_pnl": -1187151.21,
            "canonical_integrity": "PASS",
            "paper_entry": "FORBIDDEN",
            "capture_only": True,
        },
        "analysis": analysis,
        "verdict": verdict,
        "tests": tests,
        "completion": {
            "1_final_verdict": final,
            "2_replay_determinism": "PASS" if analysis.get("determinism_pass") else "FAIL",
            "3_runtime_parity": "LEGACY_RUNTIME_PARITY_NOT_EVALUABLE",
            "4_cohort_counts": analysis.get("cohort_counts"),
            "5_momentum_pre_exit": opp.get("E0"),
            "6_canonical_board_entry_effect": {
                "opp_E2_vs_E0_never_prof": (
                    (opp.get("E2") or {}).get("never_profitable_rate"),
                    (opp.get("E0") or {}).get("never_profitable_rate"),
                ),
                "pnl_C4_minus_C0": attr.get("CANONICAL_BOARD_ENTRY_DELTA"),
            },
            "7_X0_X4": {k: {"pnl": v.get("pnl_5bps"), "PF": v.get("PF_5bps"), "trades": v.get("trades")} for k, v in xc.items()},
            "8_board_exit_pnl_impact": {
                "X3_vs_X2": attr.get("BOARD_EXIT_X3_DELTA"),
                "X4_vs_X3": attr.get("BOARD_EXIT_X4_DELTA"),
            },
            "9_false_board_collapse": (imm.get("X4") or {}).get("false_collapse"),
            "10_exit_0_1s": (imm.get("X4") or {}).get("exit_0_1s"),
            "11_exit_1_5s": (imm.get("X4") or {}).get("exit_1_5s"),
            "12_spread_consumed_stops": (sp.get("class_counts") or {}).get("SPREAD_CONSUMED_STOP"),
            "13_raw_candidates": ep.get("raw_e2"),
            "14_true_episodes": ep.get("true_episodes"),
            "15_same_episode_reentry": ep.get("same_episode_reentry"),
            "16_one_episode_one_entry": {
                "R0": {k: (reentry.get("R0_event") or {}).get(k) for k in ("pnl_5bps", "PF_5bps", "trades")},
                "R1": {k: (reentry.get("R1_one_ep") or {}).get(k) for k in ("pnl_5bps", "PF_5bps", "trades")},
            },
            "17_C0_C8": {k: {"pnl": v.get("pnl_5bps"), "PF": v.get("PF_5bps"), "trades": v.get("trades")} for k, v in ce.items()},
            "18_entry_root": [c for c in (analysis.get("causes") or []) if "ENTRY" in c[0] or "BOARD_ENTRY" in c[0]],
            "19_exit_root": [c for c in (analysis.get("causes") or []) if "EXIT" in c[0] or "COLLAPSE" in c[0]],
            "20_reentry_root": [c for c in (analysis.get("causes") or []) if "REENTRY" in c[0]],
            "21_spread_root": [c for c in (analysis.get("causes") or []) if "SPREAD" in c[0]],
            "22_max_loss_contribution": max(analysis.get("causes") or [("NONE", 0)], key=lambda x: x[1]),
            "23_pbv2_momentum": "PBV2_MOMENTUM_CORE_REJECT" if "PBV2_MOMENTUM_CORE_REJECT" in decisions else "PBV2_MOMENTUM_CORE_PROVISIONAL",
            "24_canonical_board_entry": "CANONICAL_BOARD_ENTRY_COMPONENT_REJECT" if "CANONICAL_BOARD_ENTRY_COMPONENT_REJECT" in decisions else "CANONICAL_BOARD_ENTRY_COMPONENT_PROVISIONAL",
            "25_board_exit": "CURRENT_BOARD_EXIT_ARCHITECTURE_REJECT" if "CURRENT_BOARD_EXIT_ARCHITECTURE_REJECT" in decisions else "CURRENT_BOARD_EXIT_ARCHITECTURE_PROVISIONAL",
            "26_capture_only": True,
            "27_paper": "NO_PAPER_ENTRY",
            "28_live": "LIVE_TRADING_BLOCKED",
            "29_submit": SUBMIT,
            "30_cancel": CANCEL,
            "31_live_order": LIVE_ORDER,
            "32_tests": tests,
            "33_mainline_changed": False,
            "34_artifacts": str(out_dir),
        },
    }
    emit_artifacts(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    return payload
