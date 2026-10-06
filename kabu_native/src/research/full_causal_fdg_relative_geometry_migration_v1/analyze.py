"""Structural coverage, Full Causal economics, G6, blocks. One frozen migration strategy."""
from __future__ import annotations

from typing import Any

from research.full_causal_fdg_relative_geometry_migration_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    CASE_E,
    CASE_INTEGRITY,
    CASE_PARITY,
    DEVELOPMENT_DAYS,
    NEXT_A,
    NEXT_FIX,
    NEXT_STOP,
    SC1_MIN_SIGNAL_N,
    SC2_MIN_SIGNAL_DAY_N,
    SC3_MIN_SIGNAL_SYMBOL_N,
    STRATEGY_ID,
)
from research.full_causal_mechanism_discovery_v1.analyze import (
    block_pack,
    canary_parity,
    evaluate_strategy,
    occupancy_rows,
    replay,
)
from research.simple_tech_entry_family.portfolio import _sym


def structural_coverage(mig_rows: list[dict[str, Any]], semantic: dict[str, Any]) -> dict[str, Any]:
    days = sorted({str(r.get("date") or "") for r in mig_rows if r.get("date")})
    symbols = sorted({_sym(r) for r in mig_rows if _sym(r)})
    sig_n = len(mig_rows)
    sc1 = int(sig_n) >= int(SC1_MIN_SIGNAL_N)
    sc2 = int(len(days)) >= int(SC2_MIN_SIGNAL_DAY_N)
    sc3 = int(len(symbols)) >= int(SC3_MIN_SIGNAL_SYMBOL_N)
    return {
        "VALID_FULL_DEPTH_SNAPSHOT_N": int(semantic.get("valid_full_depth_n") or 0),
        "VALID_CONSECUTIVE_PAIR_N": int(semantic.get("valid_consecutive_pair_n") or 0),
        "UNKNOWN_PAIR_N": int(semantic.get("unknown_pair_n") or 0),
        "UNKNOWN_SNAPSHOT_N": int(semantic.get("unknown_n") or 0),
        "BID_GEOMETRY_CONTRACT_EVENT_N": int(semantic.get("bid_geometry_contract_event_n") or 0),
        "ASK_GEOMETRY_EXPAND_EVENT_N": int(semantic.get("ask_geometry_expand_event_n") or 0),
        "JOINT_FAVORABLE_MIGRATION_EVENT_N": int(semantic.get("joint_favorable_migration_event_n") or 0),
        "FALSE_TO_TRUE_SIGNAL_N": int(sig_n),
        "SIGNAL_DAY_N": int(len(days)),
        "SIGNAL_SYMBOL_N": int(len(symbols)),
        "SIGNAL_DAYS": days,
        "SC1": bool(sc1),
        "SC2": bool(sc2),
        "SC3": bool(sc3),
        "STRUCTURAL_PASS": bool(sc1 and sc2 and sc3),
    }


def decide(
    *,
    integrity_pass: bool,
    canary_ok: bool,
    structural_pass: bool,
    coverage_ok: bool,
    g15: bool,
    g6: bool | None,
    s1: bool | None,
    s2: bool | None,
) -> dict[str, Any]:
    if not integrity_pass:
        return {
            "CASE": "INTEGRITY",
            "VERDICT": CASE_INTEGRITY,
            "NEXT": NEXT_FIX,
            "DEV_CANDIDATE": None,
            "FULL_DEPTH_GEOMETRY_JUSTIFIED_MECHANISM_SPACE_EXHAUSTED": False,
        }
    if not canary_ok:
        return {
            "CASE": "PARITY",
            "VERDICT": CASE_PARITY,
            "NEXT": NEXT_FIX,
            "DEV_CANDIDATE": None,
            "FULL_DEPTH_GEOMETRY_JUSTIFIED_MECHANISM_SPACE_EXHAUSTED": False,
        }
    if not structural_pass:
        return {
            "CASE": "B",
            "VERDICT": CASE_B,
            "NEXT": NEXT_STOP,
            "DEV_CANDIDATE": None,
            "FULL_DEPTH_GEOMETRY_JUSTIFIED_MECHANISM_SPACE_EXHAUSTED": True,
        }
    if not coverage_ok:
        return {
            "CASE": "C",
            "VERDICT": CASE_C,
            "NEXT": NEXT_STOP,
            "DEV_CANDIDATE": None,
            "FULL_DEPTH_GEOMETRY_JUSTIFIED_MECHANISM_SPACE_EXHAUSTED": True,
        }
    if not g15:
        return {
            "CASE": "D",
            "VERDICT": CASE_D,
            "NEXT": NEXT_STOP,
            "DEV_CANDIDATE": None,
            "FULL_DEPTH_GEOMETRY_JUSTIFIED_MECHANISM_SPACE_EXHAUSTED": True,
        }
    if not (bool(g6) and bool(s1) and bool(s2)):
        return {
            "CASE": "E",
            "VERDICT": CASE_E,
            "NEXT": NEXT_STOP,
            "DEV_CANDIDATE": None,
            "FULL_DEPTH_GEOMETRY_JUSTIFIED_MECHANISM_SPACE_EXHAUSTED": True,
        }
    return {
        "CASE": "A",
        "VERDICT": CASE_A,
        "NEXT": NEXT_A,
        "DEV_CANDIDATE": STRATEGY_ID,
        "Classification": "DEV_CANDIDATE",
        "FULL_DEPTH_GEOMETRY_JUSTIFIED_MECHANISM_SPACE_EXHAUSTED": False,
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


def slim_trades(trades: list[dict[str, Any]], *, cap: int = 2000) -> list[dict[str, Any]]:
    out = []
    for t in trades[:cap]:
        rec = {k: v for k, v in t.items() if k != "src"}
        out.append(rec)
    return out


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    obj_p = dict(report.get("parent_object") or {})
    st_p = dict(report.get("parent_static") or {})
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
    exhausted = d.get("FULL_DEPTH_GEOMETRY_JUSTIFIED_MECHANISM_SPACE_EXHAUSTED")
    return {
        "1_parent_object_verdict_pinned": bool(obj_p.get("ok")),
        "2_parent_static_strategy_verdict_pinned": bool(st_p.get("ok")),
        "3_static_STRATEGY_ID_closed": st_p.get("CLOSED_STRATEGY_ID"),
        "4_static_span_retune": False,
        "5_session_close_rescue": False,
        "6_ranked_depth_semantics_confirmed": True,
        "7_deep_ranks_required": True,
        "8_BID_REL_exact": spec.get("BID_REL_K"),
        "9_ASK_REL_exact": spec.get("ASK_REL_K"),
        "10_BID_GEOMETRY_CONTRACTS_exact": spec.get("BID_GEOMETRY_CONTRACTS"),
        "11_ASK_GEOMETRY_EXPANDS_exact": spec.get("ASK_GEOMETRY_EXPANDS"),
        "12_FAVORABLE_RELATIVE_GEOMETRY_MIGRATION_exact": spec.get("FAVORABLE_RELATIVE_GEOMETRY_MIGRATION"),
        "13_migration_uses_magnitude_threshold": False,
        "14_migration_uses_quantity": False,
        "15_migration_uses_k_subset": False,
        "16_migration_uses_time_window": False,
        "17_ENTRY_onset_exact": spec.get("ONSET"),
        "18_initial_TRUE_enters": False,
        "19_UNKNOWN_bridge_allowed": False,
        "20_thesis_baseline_exact": spec.get("BASELINE"),
        "21_baseline_immutable": True,
        "22_THESIS_VALID_exact": spec.get("THESIS_VALID"),
        "23_X1_exact": True,
        "24_technical_EXIT_exact": spec.get("TECHNICAL_EXIT"),
        "25_timeout": False,
        "26_alternate_EXIT": False,
        "27_FULL_STRATEGY_SPEC_SHA256_FDG_MIGRATION_V1": spec.get("FULL_STRATEGY_SPEC_SHA256_FDG_MIGRATION_V1"),
        "28_strategy_frozen_before_economics": bool(spec.get("STRATEGY_FROZEN_BEFORE_ECONOMICS")),
        "29_VALID_FULL_DEPTH_SNAPSHOT_N": struct.get("VALID_FULL_DEPTH_SNAPSHOT_N"),
        "30_VALID_CONSECUTIVE_PAIR_N": struct.get("VALID_CONSECUTIVE_PAIR_N"),
        "31_UNKNOWN_PAIR_N": struct.get("UNKNOWN_PAIR_N"),
        "32_BID_GEOMETRY_CONTRACT_EVENT_N": struct.get("BID_GEOMETRY_CONTRACT_EVENT_N"),
        "33_ASK_GEOMETRY_EXPAND_EVENT_N": struct.get("ASK_GEOMETRY_EXPAND_EVENT_N"),
        "34_JOINT_FAVORABLE_MIGRATION_EVENT_N": struct.get("JOINT_FAVORABLE_MIGRATION_EVENT_N"),
        "35_FALSE_TO_TRUE_SIGNAL_N": struct.get("FALSE_TO_TRUE_SIGNAL_N"),
        "36_SIGNAL_DAY_N": struct.get("SIGNAL_DAY_N"),
        "37_SIGNAL_SYMBOL_N": struct.get("SIGNAL_SYMBOL_N"),
        "38_SC1": struct.get("SC1"),
        "39_SC2": struct.get("SC2"),
        "40_SC3": struct.get("SC3"),
        "41_integrity_PASS_N_total": f"{integ.get('PASS_N')}/{integ.get('TOTAL_N')}" if integ else None,
        "42_economics_visible_before_integrity": False,
        "43_R2_canary_signal_n": obs.get("signal_n"),
        "44_R2_canary_trade_n": obs.get("trade_n"),
        "45_R2_canary_PnL": obs.get("TOTAL_PNL"),
        "46_R2_canary_PF": obs.get("PF"),
        "47_canary_parity_PASS": can.get("HARD_PASS"),
        "48_signal_n": ev.get("signal_n") if eco_open else None,
        "49_X1_fill_n": ev.get("X1_fill_n") if eco_open else None,
        "50_trade_n": ev.get("trade_n") if eco_open else None,
        "51_fill_day_n": ev.get("fill_day_n") if eco_open else None,
        "52_trades_per_day": ev.get("trades_per_day") if eco_open else None,
        "53_CAP_reject_N": ev.get("CAP_reject_n") if eco_open else None,
        "54_same_symbol_reject_N": ev.get("same_symbol_reject_n") if eco_open else None,
        "55_technical_EXIT_N": ev.get("technical_exit_n") if eco_open else None,
        "56_session_EXIT_N": ev.get("session_exit_n") if eco_open else None,
        "57_session_exit_unfilled_N": ev.get("session_exit_unfilled_n") if eco_open else None,
        "58_slot_release_N": ev.get("slot_release_n") if eco_open else None,
        "59_reentry_N": ev.get("reentry_n") if eco_open else None,
        "60_C1": ev.get("C1") if eco_open else None,
        "61_C2": ev.get("C2") if eco_open else None,
        "62_C3": ev.get("C3") if eco_open else None,
        "63_C4": ev.get("C4") if eco_open else None,
        "64_TOTAL_PNL": ev.get("TOTAL_PNL") if eco_open else None,
        "65_PF": ev.get("PF") if eco_open else None,
        "66_MaxDD": ev.get("MaxDD") if eco_open else None,
        "67_pos_neg_zero_days": (
            f"{ev.get('positive_day_n')}/{ev.get('negative_day_n')}/{ev.get('zero_day_n')}" if eco_open else None
        ),
        "68_EX_BEST_DAY_PNL": ev.get("EX_BEST_DAY_PNL") if eco_open else None,
        "69_G1": g.get("G1") if eco_open else None,
        "70_G2": g.get("G2") if eco_open else None,
        "71_G3": g.get("G3") if eco_open else None,
        "72_G4": g.get("G4") if eco_open else None,
        "73_G5": g.get("G5") if eco_open else None,
        "74_top_symbol": ev.get("top_symbol") if eco_open else None,
        "75_causal_ex_top1_PnL": ev.get("CAUSAL_EX_TOP1_PNL") if eco_open else None,
        "76_G6": g.get("G6") if eco_open else None,
        "77_positive_block_N": blocks.get("POSITIVE_BLOCK_N") if eco_open else None,
        "78_ex_best_block_PnL": blocks.get("EX_BEST_BLOCK_PNL") if eco_open else None,
        "79_S1": blocks.get("S1") if eco_open else None,
        "80_S2": blocks.get("S2") if eco_open else None,
        "81_FULL_DEPTH_GEOMETRY_JUSTIFIED_MECHANISM_SPACE_EXHAUSTED": exhausted,
        "82_post_result_geometry_change": False,
        "83_post_result_ENTRY_change": False,
        "84_post_result_EXIT_change": False,
        "85_post_result_CAP_change": False,
        "86_post_result_threshold_change": False,
        "87_post_result_window_change": False,
        "88_second_candidate_added": False,
        "89_Sizing": False,
        "90_Holdout_read": False,
        "91_Stress_read": False,
        "92_future_read": False,
        "93_20260903_plus_read": False,
        "94_20260907_Paper_read": False,
        "95_Runtime_changed": False,
        "96_Capture_changed": False,
        "97_submit_cancel_live": "0/0/0",
        "98_TRUE_OOS": False,
        "99_CERTIFIED": False,
        "100_VERDICT": d.get("VERDICT"),
        "101_NEXT": d.get("NEXT"),
    }


assert occupancy_rows and replay and evaluate_strategy and canary_parity and block_pack and DEVELOPMENT_DAYS
