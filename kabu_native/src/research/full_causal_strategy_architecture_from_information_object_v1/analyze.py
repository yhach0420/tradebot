"""Structural coverage, Full Causal economics, G6, blocks. No second candidate."""
from __future__ import annotations

from typing import Any

from research.full_causal_mechanism_discovery_v1.analyze import (
    block_pack,
    canary_parity,
    evaluate_strategy,
    occupancy_rows,
    replay,
)
from research.full_causal_strategy_architecture_from_information_object_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    CASE_E,
    CASE_INTEGRITY,
    CASE_PARITY,
    CASE_SEMANTIC,
    DEVELOPMENT_DAYS,
    INTERNAL_HOLE_RATE_MAX,
    NEXT_A,
    NEXT_FIX,
    NEXT_REMAINING,
    NEXT_SEMANTIC,
    SC1_MIN_SIGNAL_N,
    SC2_MIN_SIGNAL_DAY_N,
    STRATEGY_ID,
)
from research.full_causal_strategy_architecture_from_information_object_v1.geometry import semantic_valid


def structural_coverage(fdg_rows: list[dict[str, Any]], semantic: dict[str, Any]) -> dict[str, Any]:
    days = sorted({str(r.get("date") or "") for r in fdg_rows if r.get("date")})
    sig_n = len(fdg_rows)
    sc1 = int(sig_n) >= int(SC1_MIN_SIGNAL_N)
    sc2 = int(len(days)) >= int(SC2_MIN_SIGNAL_DAY_N)
    return {
        "VALID_FULL_DEPTH_SNAPSHOT_N": int(semantic.get("valid_full_depth_n") or 0),
        "UNKNOWN_SNAPSHOT_N": int(semantic.get("unknown_n") or 0),
        "LONG_STATE_TRUE_N": int(semantic.get("long_state_true_n") or 0),
        "FALSE_TO_TRUE_SIGNAL_N": int(sig_n),
        "SIGNAL_DAY_N": int(len(days)),
        "SIGNAL_DAYS": days,
        "SC1": bool(sc1),
        "SC2": bool(sc2),
        "STRUCTURAL_PASS": bool(sc1 and sc2),
    }


def depth_rank_decision(semantic: dict[str, Any]) -> dict[str, Any]:
    snap_n = int(semantic.get("snapshot_n") or 0)
    bid_h = int(semantic.get("bid_internal_zero_then_nonzero_n") or 0)
    ask_h = int(semantic.get("ask_internal_zero_then_nonzero_n") or 0)
    ok = semantic_valid(snapshot_n=snap_n, bid_hole_n=bid_h, ask_hole_n=ask_h, rate_max=float(INTERNAL_HOLE_RATE_MAX))
    return {
        "DEPTH_RANK_SEMANTICS_VALID": bool(ok),
        "snapshot_n": snap_n,
        "bid_internal_zero_then_nonzero_n": bid_h,
        "ask_internal_zero_then_nonzero_n": ask_h,
        "bid_all10_present_n": int(semantic.get("bid_all10_present_n") or 0),
        "ask_all10_present_n": int(semantic.get("ask_all10_present_n") or 0),
        "bid_strict_order_n": int(semantic.get("bid_strict_order_n") or 0),
        "ask_strict_order_n": int(semantic.get("ask_strict_order_n") or 0),
        "INTERNAL_HOLE_RATE_MAX": INTERNAL_HOLE_RATE_MAX,
        "bid_hole_rate": (float(bid_h) / float(snap_n)) if snap_n else None,
        "ask_hole_rate": (float(ask_h) / float(snap_n)) if snap_n else None,
        "treatment": "ordered displayed price ranks" if ok else "unresolved_fixed_sparse_slots",
    }


def decide(
    *,
    semantic_ok: bool,
    integrity_pass: bool,
    canary_ok: bool,
    structural_pass: bool,
    coverage_ok: bool,
    g15: bool,
    g6: bool | None,
    s1: bool | None,
    s2: bool | None,
) -> dict[str, Any]:
    if not semantic_ok:
        return {"CASE": "SEMANTIC", "VERDICT": CASE_SEMANTIC, "NEXT": NEXT_SEMANTIC, "DEV_CANDIDATE": None}
    if not integrity_pass:
        return {"CASE": "INTEGRITY", "VERDICT": CASE_INTEGRITY, "NEXT": NEXT_FIX, "DEV_CANDIDATE": None}
    if not canary_ok:
        return {"CASE": "PARITY", "VERDICT": CASE_PARITY, "NEXT": NEXT_FIX, "DEV_CANDIDATE": None}
    if not structural_pass:
        return {"CASE": "B", "VERDICT": CASE_B, "NEXT": NEXT_REMAINING, "DEV_CANDIDATE": None}
    if not coverage_ok:
        return {"CASE": "C", "VERDICT": CASE_C, "NEXT": NEXT_REMAINING, "DEV_CANDIDATE": None}
    if not g15:
        return {"CASE": "D", "VERDICT": CASE_D, "NEXT": NEXT_REMAINING, "DEV_CANDIDATE": None}
    if not (bool(g6) and bool(s1) and bool(s2)):
        return {"CASE": "E", "VERDICT": CASE_E, "NEXT": NEXT_REMAINING, "DEV_CANDIDATE": None}
    return {
        "CASE": "A",
        "VERDICT": CASE_A,
        "NEXT": NEXT_A,
        "DEV_CANDIDATE": STRATEGY_ID,
        "Classification": "DEV_CANDIDATE",
    }


def public_row(ev: dict[str, Any] | None) -> dict[str, Any] | None:
    if not ev:
        return None
    keep = (
        "STRATEGY_ID",
        "signal_n",
        "X1_fill_n",
        "trade_n",
        "fill_day_n",
        "trades_per_day",
        "entry_no_fill_n",
        "CAP_reject_n",
        "same_symbol_reject_n",
        "technical_exit_n",
        "session_exit_n",
        "session_exit_unfilled_n",
        "slot_release_n",
        "reentry_n",
        "TOTAL_PNL",
        "PF",
        "MaxDD",
        "positive_day_n",
        "negative_day_n",
        "zero_day_n",
        "best_day",
        "best_day_pnl",
        "worst_day",
        "worst_day_pnl",
        "EX_BEST_DAY_PNL",
        "top_symbol",
        "top_symbol_pnl",
        "CAUSAL_EX_TOP1_PNL",
        "g_table",
        "coverage_ok",
        "C1",
        "C2",
        "C3",
        "C4",
        "blocks",
        "daily",
        "exit_reasons",
    )
    return {k: ev.get(k) for k in keep}


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    parent = dict(report.get("parent") or {})
    sem = dict(report.get("depth_rank") or {})
    spec = dict(report.get("spec") or {})
    struct = dict(report.get("structural") or {})
    integ = dict(report.get("integrity") or {})
    can = dict(report.get("canary") or {})
    obs = dict(can.get("observed") or {})
    ev = dict(report.get("evaluated") or {})
    g = dict(ev.get("g_table") or {})
    blocks = dict(ev.get("blocks") or {})
    d = dict(report.get("decision") or {})
    eco_open = bool(report.get("ECONOMICS_OPENED"))
    return {
        "1_parent_verdict_pinned": bool(parent.get("ok")),
        "2_selected_object_ID": parent.get("SELECTED_OBJECT_ID"),
        "3_object_SHA_exact": bool(parent.get("ok")),
        "4_current_42_reopened": False,
        "5_depth_rank_semantic_audit_complete": bool(report.get("semantic_audit_complete")),
        "6_bid_internal_zero_deeper_nonzero_N": sem.get("bid_internal_zero_then_nonzero_n"),
        "7_ask_internal_zero_deeper_nonzero_N": sem.get("ask_internal_zero_then_nonzero_n"),
        "8_bid_all10_present_N": sem.get("bid_all10_present_n"),
        "9_ask_all10_present_N": sem.get("ask_all10_present_n"),
        "10_DEPTH_RANK_SEMANTICS_VALID": sem.get("DEPTH_RANK_SEMANTICS_VALID"),
        "11_BID_COMPLETE_ASK_BROKEN_architecture_used": False,
        "12_STRATEGY_ID": STRATEGY_ID if sem.get("DEPTH_RANK_SEMANTICS_VALID") else None,
        "13_BID_SPAN": spec.get("BID_SPAN"),
        "14_ASK_SPAN": spec.get("ASK_SPAN"),
        "15_LONG_GEOMETRY_STATE": spec.get("LONG_GEOMETRY_STATE"),
        "16_UNKNOWN_semantics": spec.get("UNKNOWN_SEMANTICS"),
        "17_ENTRY_transition": spec.get("ENTRY"),
        "18_initial_TRUE_enters": False,
        "19_persistence_window_used": False,
        "20_X1_exact": True,
        "21_technical_EXIT": spec.get("TECHNICAL_EXIT"),
        "22_timeout": False,
        "23_alternate_EXIT": False,
        "24_quantity_imbalance_used": False,
        "25_quantity_magnitude_comparison_used": False,
        "26_k_level_search_used": False,
        "27_migration_rule_used": False,
        "28_raw_H5_candidate_screening_used": False,
        "29_FULL_STRATEGY_SPEC_SHA256_FDG_V1": spec.get("FULL_STRATEGY_SPEC_SHA256_FDG_V1") if sem.get("DEPTH_RANK_SEMANTICS_VALID") else None,
        "30_strategy_frozen_before_economics": bool(spec.get("STRATEGY_FROZEN_BEFORE_ECONOMICS")) if sem.get("DEPTH_RANK_SEMANTICS_VALID") else False,
        "31_VALID_FULL_DEPTH_SNAPSHOT_N": struct.get("VALID_FULL_DEPTH_SNAPSHOT_N"),
        "32_UNKNOWN_SNAPSHOT_N": struct.get("UNKNOWN_SNAPSHOT_N"),
        "33_LONG_STATE_TRUE_N": struct.get("LONG_STATE_TRUE_N"),
        "34_FALSE_TO_TRUE_SIGNAL_N": struct.get("FALSE_TO_TRUE_SIGNAL_N"),
        "35_SIGNAL_DAY_N": struct.get("SIGNAL_DAY_N"),
        "36_SC1": struct.get("SC1"),
        "37_SC2": struct.get("SC2"),
        "38_integrity_PASS_N_total": f"{integ.get('PASS_N')}/{integ.get('TOTAL_N')}" if integ else None,
        "39_economics_visible_before_integrity": False,
        "40_R2_canary_signal_n": obs.get("signal_n"),
        "41_R2_canary_trade_n": obs.get("trade_n"),
        "42_R2_canary_PnL": obs.get("TOTAL_PNL"),
        "43_R2_canary_PF": obs.get("PF"),
        "44_canary_parity_PASS": can.get("HARD_PASS"),
        "45_signal_n": ev.get("signal_n") if eco_open else None,
        "46_X1_fill_n": ev.get("X1_fill_n") if eco_open else None,
        "47_trade_n": ev.get("trade_n") if eco_open else None,
        "48_fill_day_n": ev.get("fill_day_n") if eco_open else None,
        "49_trades_per_day": ev.get("trades_per_day") if eco_open else None,
        "50_CAP_reject_N": ev.get("CAP_reject_n") if eco_open else None,
        "51_same_symbol_reject_N": ev.get("same_symbol_reject_n") if eco_open else None,
        "52_technical_EXIT_N": ev.get("technical_exit_n") if eco_open else None,
        "53_session_EXIT_N": ev.get("session_exit_n") if eco_open else None,
        "54_session_exit_unfilled_N": ev.get("session_exit_unfilled_n") if eco_open else None,
        "55_slot_release_N": ev.get("slot_release_n") if eco_open else None,
        "56_reentry_N": ev.get("reentry_n") if eco_open else None,
        "57_C1": ev.get("C1") if eco_open else None,
        "58_C2": ev.get("C2") if eco_open else None,
        "59_C3": ev.get("C3") if eco_open else None,
        "60_C4": ev.get("C4") if eco_open else None,
        "61_TOTAL_PNL": ev.get("TOTAL_PNL") if eco_open else None,
        "62_PF": ev.get("PF") if eco_open else None,
        "63_MaxDD": ev.get("MaxDD") if eco_open else None,
        "64_pos_neg_zero_days": (
            f"{ev.get('positive_day_n')}/{ev.get('negative_day_n')}/{ev.get('zero_day_n')}" if eco_open else None
        ),
        "65_EX_BEST_DAY_PNL": ev.get("EX_BEST_DAY_PNL") if eco_open else None,
        "66_G1": g.get("G1") if eco_open else None,
        "67_G2": g.get("G2") if eco_open else None,
        "68_G3": g.get("G3") if eco_open else None,
        "69_G4": g.get("G4") if eco_open else None,
        "70_G5": g.get("G5") if eco_open else None,
        "71_top_symbol": ev.get("top_symbol") if eco_open else None,
        "72_causal_ex_top1_PnL": ev.get("CAUSAL_EX_TOP1_PNL") if eco_open else None,
        "73_G6": g.get("G6") if eco_open else None,
        "74_positive_block_N": blocks.get("POSITIVE_BLOCK_N") if eco_open else None,
        "75_ex_best_block_PnL": blocks.get("EX_BEST_BLOCK_PNL") if eco_open else None,
        "76_S1": blocks.get("S1") if eco_open else None,
        "77_S2": blocks.get("S2") if eco_open else None,
        "78_post_result_state_change": False,
        "79_post_result_EXIT_change": False,
        "80_post_result_CAP_change": False,
        "81_post_result_threshold_change": False,
        "82_post_result_k_change": False,
        "83_second_candidate_added": False,
        "84_Sizing": False,
        "85_Holdout_read": False,
        "86_Stress_read": False,
        "87_future_read": False,
        "88_20260903_plus_read": False,
        "89_20260907_Paper_read": False,
        "90_Runtime_changed": False,
        "91_Capture_changed": False,
        "92_submit_cancel_live": "0/0/0",
        "93_TRUE_OOS": False,
        "94_CERTIFIED": False,
        "95_VERDICT": d.get("VERDICT"),
        "96_NEXT": d.get("NEXT"),
    }


assert occupancy_rows and replay and evaluate_strategy and canary_parity and block_pack and DEVELOPMENT_DAYS
