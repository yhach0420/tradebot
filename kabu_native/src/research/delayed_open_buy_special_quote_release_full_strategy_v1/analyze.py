"""Canary, duplicate, direction proof, Full Causal gates. No EXIT retune."""
from __future__ import annotations

from typing import Any

from research.delayed_open_buy_special_quote_release_full_strategy_v1 import (
    CASE_COVERAGE,
    CASE_DIRECTION,
    CASE_DUP,
    CASE_FAIL,
    CASE_PARITY,
    CASE_PASS,
    DEVELOPMENT_DAYS,
    NEXT_FIX,
    NEXT_PASS,
    NEXT_REDIRECT,
    POSITION_CAP,
    STRATEGY_ID,
)
from research.delayed_open_buy_special_quote_release_full_strategy_v1.duplicates import audit_duplicates
from research.delayed_open_buy_special_quote_release_full_strategy_v1.semantics import prove_market_states
from research.delayed_open_buy_special_quote_release_full_strategy_v1.strategy import freeze_sha, strategy_spec
from research.full_causal_mechanism_discovery_v1.analyze import canary_parity, evaluate_strategy, public_row


def decide(
    *,
    dup: dict[str, Any],
    canary_ok: bool,
    proven: dict[str, Any],
    coverage_ok: bool | None = None,
    robust: bool | None = None,
    g15: bool | None = None,
) -> dict[str, Any]:
    if dup.get("EXACT_COMPLETE_STRATEGY_DUPLICATE") or dup.get("MATERIAL_SEMANTIC_DUPLICATE"):
        return {
            "CASE": "DUP",
            "VERDICT": CASE_DUP,
            "NEXT": NEXT_REDIRECT,
            "LOGIC_COMPLETE": False,
            "ROBUST_DEV_QUALIFIED": False,
            "OPENING_CURRENT_DATA_LINE_STATUS": "CLOSED",
            "INTERPRETATION": "Exact or material semantic duplicate of a prior Complete Full Strategy. Stop before economics.",
        }
    if not canary_ok:
        return {
            "CASE": "PARITY",
            "VERDICT": CASE_PARITY,
            "NEXT": NEXT_FIX,
            "LOGIC_COMPLETE": False,
            "ROBUST_DEV_QUALIFIED": False,
            "OPENING_CURRENT_DATA_LINE_STATUS": None,
            "INTERPRETATION": "R2_X1_Z3 engine canary failed. Fix implementation only.",
        }
    if not proven.get("BUY_SPECIAL_DIRECTION_PROVEN"):
        return {
            "CASE": "DIRECTION",
            "VERDICT": CASE_DIRECTION,
            "NEXT": NEXT_REDIRECT,
            "LOGIC_COMPLETE": False,
            "ROBUST_DEV_QUALIFIED": False,
            "OPENING_CURRENT_DATA_LINE_STATUS": "CLOSED",
            "INTERPRETATION": (
                "Trusted executable_board maps special quote without buy vs sell. "
                "Do not invent BUY_SIDE_SPECIAL_QUOTE from Sign digits. Close this opening line."
            ),
        }
    if coverage_ok is False:
        return {
            "CASE": "COVERAGE",
            "VERDICT": CASE_COVERAGE,
            "NEXT": NEXT_REDIRECT,
            "LOGIC_COMPLETE": False,
            "ROBUST_DEV_QUALIFIED": False,
            "OPENING_CURRENT_DATA_LINE_STATUS": "CLOSED",
            "INTERPRETATION": "Canonical coverage C1-C4 failed. Do not retune.",
        }
    if robust:
        return {
            "CASE": "PASS",
            "VERDICT": CASE_PASS,
            "NEXT": NEXT_PASS,
            "LOGIC_COMPLETE": True,
            "ROBUST_DEV_QUALIFIED": True,
            "OPENING_CURRENT_DATA_LINE_STATUS": "OPEN",
            "INTERPRETATION": "ROBUST_DEV_QUALIFIED. Freeze LEGACY_DEV_CONSTRUCTED_CANDIDATE. Do not open future data.",
        }
    return {
        "CASE": "FAIL",
        "VERDICT": CASE_FAIL,
        "NEXT": NEXT_REDIRECT,
        "LOGIC_COMPLETE": False,
        "ROBUST_DEV_QUALIFIED": False,
        "OPENING_CURRENT_DATA_LINE_STATUS": "CLOSED",
        "INTERPRETATION": "Coverage passed but G1-G6 or S1-S2 failed. Close this architecture. Do not retune.",
        "g15": g15,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    a = dict(report.get("already_executed") or {})
    canary = dict(report.get("canary") or {})
    obs = dict(canary.get("observed") or {})
    sem = dict(report.get("state_semantics") or {})
    pre = dict(report.get("strategy_precommit") or {})
    ev = dict(report.get("selected_eval") or {})
    g = dict(ev.get("g_table") or {})
    blocks = dict(ev.get("blocks") or {})
    blist = list(blocks.get("blocks") or [])
    bmap = {str(r.get("block")): r for r in blist}
    pos = ev.get("positive_day_n")
    neg = ev.get("negative_day_n")
    zero = ev.get("zero_day_n")
    return {
        "1_objective_aligned": True,
        "2_parent_verdict_pinned": bool((report.get("parent") or {}).get("ok")),
        "3_exact_raw_dates_opened": list((report.get("data_boundary") or {}).get("RAW_MARKET_DATES_OPENED") or []),
        "4_Holdout_read": False,
        "5_Stress_read": False,
        "6_future_read": False,
        "7_exact_Complete_Strategy_duplicate": a.get("EXACT_COMPLETE_STRATEGY_DUPLICATE"),
        "8_semantic_duplicate": a.get("MATERIAL_SEMANTIC_DUPLICATE"),
        "9_R2_canary_signal_n": obs.get("signal_n"),
        "10_R2_canary_trade_n": obs.get("trade_n"),
        "11_R2_canary_PnL": obs.get("TOTAL_PNL"),
        "12_R2_canary_PF": obs.get("PF"),
        "13_canary_PASS": canary.get("HARD_PASS"),
        "14_classifier_source": sem.get("MARKET_STATE_CLASSIFIER_SOURCE"),
        "15_classifier_function": sem.get("MARKET_STATE_CLASSIFIER_FUNCTION"),
        "16_BUY_special_mapping": sem.get("BUY_SPECIAL_MAPPING"),
        "17_NORMAL_continuous_mapping": sem.get("NORMAL_CONTINUOUS_MAPPING"),
        "18_BUY_direction_proven": sem.get("BUY_SPECIAL_DIRECTION_PROVEN"),
        "19_CurrentPriceTime_used_for_quote_freshness": False,
        "20_strategy_ID": pre.get("STRATEGY_ID") if pre.get("FROZEN") else None,
        "21_strategy_precommit_SHA": pre.get("FULL_STRATEGY_SPEC_SHA256_DO_BSQ_V1"),
        "22_precommit_before_economics": bool(pre.get("FROZEN")),
        "23_BUY_special_delayed_open_episode_N": ev.get("episode_n"),
        "24_release_N": ev.get("release_n"),
        "25_accept_bar_available_N": ev.get("accept_bar_n"),
        "26_signal_N": ev.get("signal_n"),
        "27_signal_day_N": ev.get("signal_day_n"),
        "28_signal_symbol_N": ev.get("signal_symbol_n"),
        "29_fill_N": ev.get("fill_n"),
        "30_trade_N": ev.get("trade_n") or ev.get("TRADE_N"),
        "31_fill_day_N": ev.get("fill_day_n") or ev.get("TRADING_DAY_WITH_FILL_N"),
        "32_trades_per_day": ev.get("trades_per_day"),
        "33_C1": ev.get("C1"),
        "34_C2": ev.get("C2"),
        "35_C3": ev.get("C3"),
        "36_C4": ev.get("C4"),
        "37_Coverage_PASS": ev.get("coverage_ok"),
        "38_TOTAL_PNL": ev.get("TOTAL_PNL"),
        "39_PF": ev.get("PF"),
        "40_MaxDD": ev.get("MaxDD") if ev.get("MaxDD") is not None else ev.get("MAXDD"),
        "41_positive_negative_zero_days": (f"{pos}/{neg}/{zero}" if pos is not None else None),
        "42_EX_BEST_DAY_PNL": ev.get("EX_BEST_DAY_PNL"),
        "43_G1": g.get("G1"),
        "44_G2": g.get("G2"),
        "45_G3": g.get("G3"),
        "46_G4": g.get("G4"),
        "47_G5": g.get("G5"),
        "48_causal_ex_top1_PnL": ev.get("CAUSAL_EX_TOP1_PNL"),
        "49_causal_ex_top1_PF": ev.get("CAUSAL_EX_TOP1_PF"),
        "50_G6": g.get("G6"),
        "51_B1_PnL": (bmap.get("B1") or {}).get("pnl"),
        "52_B2_PnL": (bmap.get("B2") or {}).get("pnl"),
        "53_B3_PnL": (bmap.get("B3") or {}).get("pnl"),
        "54_B4_PnL": (bmap.get("B4") or {}).get("pnl"),
        "55_B5_PnL": (bmap.get("B5") or {}).get("pnl"),
        "56_positive_block_N": blocks.get("POSITIVE_BLOCK_N"),
        "57_ex_best_block_PnL": blocks.get("EX_BEST_BLOCK_PNL"),
        "58_S1": blocks.get("S1"),
        "59_S2": blocks.get("S2"),
        "60_universal_EXIT_used": False,
        "61_ENTRY_specific_EXIT_used": True,
        "62_fixed_holding_time_EXIT": False,
        "63_CAP_5": int(POSITION_CAP) == 5,
        "64_same_symbol_causal": True,
        "65_occupancy_causal": True,
        "66_slot_release_causal": True,
        "67_same_episode_reentry": False,
        "68_LOGIC_COMPLETE": bool(d.get("LOGIC_COMPLETE")),
        "69_ROBUST_DEV_QUALIFIED": bool(d.get("ROBUST_DEV_QUALIFIED")),
        "70_post_result_ENTRY_change": False,
        "71_post_result_EXIT_change": False,
        "72_second_strategy_attempted": False,
        "73_MBO_work": False,
        "74_futures_work": False,
        "75_Sizing": False,
        "76_Runtime_changed": False,
        "77_Capture_changed": False,
        "78_submit_cancel_live": "0/0/0",
        "79_TRUE_OOS": False,
        "80_CERTIFIED": False,
        "81_VERDICT": d.get("VERDICT"),
        "82_OPENING_CURRENT_DATA_LINE_STATUS": d.get("OPENING_CURRENT_DATA_LINE_STATUS"),
        "83_NEXT": d.get("NEXT"),
    }


assert DEVELOPMENT_DAYS
assert STRATEGY_ID
assert audit_duplicates
assert prove_market_states
assert freeze_sha
assert strategy_spec
assert evaluate_strategy
assert canary_parity
assert public_row
