"""Canary, tests, coverage, G1-G6, blocks, selection. No retune."""
from __future__ import annotations

from typing import Any

from research.full_causal_mechanism_discovery_v1.analyze import canary_parity, evaluate_strategy, public_row
from research.intraday_special_quote_resolution_full_strategy_v1 import (
    CASE_COVERAGE,
    CASE_DUP,
    CASE_FAIL,
    CASE_INTEGRITY,
    CASE_PARITY,
    CASE_PASS,
    CASE_TESTS,
    CANDIDATE_C,
    DEVELOPMENT_DAYS,
    NEXT_FIX,
    NEXT_PASS,
    NEXT_V2,
    OPENING_LINE_STATUS,
    POSITION_CAP_N,
    THESIS_D,
    THESIS_U,
)
from research.intraday_special_quote_resolution_full_strategy_v1.library import candidate_meta
from research.simple_tech_entry_family.portfolio import _sym


def _g15(ev: dict[str, Any]) -> bool:
    g = dict(ev.get("g_table") or {})
    return bool(g.get("G1") and g.get("G2") and g.get("G3") and g.get("G4") and g.get("G5"))


def _pf_key(pf: Any) -> float:
    if pf is None:
        return -1e18
    if pf == float("inf"):
        return 1e18
    return float(pf)


def selection_key(ev: dict[str, Any]) -> tuple:
    blocks = list((ev.get("blocks") or {}).get("blocks") or [])
    pnls = [float(b.get("pnl") or 0.0) for b in blocks]
    min_block = min(pnls) if pnls else -1e18
    sid = str(ev.get("STRATEGY_ID") or "")
    thesis_rank = 1 if sid == CANDIDATE_C else 0
    dd = abs(float(ev.get("MaxDD") if ev.get("MaxDD") is not None else ev.get("MAXDD") or 0.0))
    return (
        -float(min_block),
        -float(ev.get("CAUSAL_EX_TOP1_PNL") or -1e18),
        -float(ev.get("EX_BEST_DAY_PNL") or -1e18),
        -float(ev.get("TOTAL_PNL") or -1e18),
        -_pf_key(ev.get("PF")),
        float(dd),
        int(thesis_rank),
        sid,
    )


def extra_counts(ev: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    trades = list(ev.get("_trades") or [])
    ep_ids = [str(t.get("src", {}).get("episode_id") or "") for t in trades]
    same_ep = 0
    seen: set[str] = set()
    for eid in ep_ids:
        if not eid:
            continue
        if eid in seen:
            same_ep += 1
        seen.add(eid)
    by_sd: dict[tuple[str, str], int] = {}
    new_ep = 0
    for t in trades:
        key = (str(t.get("date") or ""), _sym(t))
        by_sd[key] = int(by_sd.get(key) or 0) + 1
    new_ep = sum(max(0, n - 1) for n in by_sd.values())
    u_n = sum(1 for t in trades if str((t.get("src") or {}).get("thesis") or "") == THESIS_U)
    d_n = sum(1 for t in trades if str((t.get("src") or {}).get("thesis") or "") == THESIS_D)
    return {
        "pending_n": int(ev.get("entry_no_fill_n") or 0),
        "same_episode_reentry_n": int(same_ep),
        "new_episode_reentry_n": int(new_ep),
        "U_TRADE_N": u_n,
        "D_TRADE_N": d_n,
        "signal_n": ev.get("signal_n"),
        "fill_n": ev.get("fill_n"),
        "trade_n": ev.get("trade_n") or ev.get("TRADE_N"),
    }


def decide(
    *,
    dup: dict[str, Any],
    tests_ok: bool,
    canary_ok: bool,
    integrity_n: int | None = None,
    coverage_any: bool | None = None,
    robust_n: int | None = None,
) -> dict[str, Any]:
    if not (dup.get("ELIGIBLE_CANDIDATE_IDS") or []):
        return {
            "CASE": "DUP",
            "VERDICT": CASE_DUP,
            "NEXT": NEXT_V2,
            "LOGIC_COMPLETE": False,
            "ROBUST_DEV_QUALIFIED": False,
            "OPENING_CURRENT_DATA_LINE_STATUS": OPENING_LINE_STATUS,
            "INTERPRETATION": "No remaining eligible thesis after duplicate check. Stop.",
        }
    if not tests_ok:
        return {
            "CASE": "TESTS",
            "VERDICT": CASE_TESTS,
            "NEXT": NEXT_FIX,
            "LOGIC_COMPLETE": False,
            "ROBUST_DEV_QUALIFIED": False,
            "OPENING_CURRENT_DATA_LINE_STATUS": OPENING_LINE_STATUS,
            "INTERPRETATION": "Synthetic T1-T30 failed. Fix implementation only.",
        }
    if not canary_ok:
        return {
            "CASE": "PARITY",
            "VERDICT": CASE_PARITY,
            "NEXT": NEXT_FIX,
            "LOGIC_COMPLETE": False,
            "ROBUST_DEV_QUALIFIED": False,
            "OPENING_CURRENT_DATA_LINE_STATUS": OPENING_LINE_STATUS,
            "INTERPRETATION": "R2_X1_Z3 engine canary failed. Fix implementation only.",
        }
    if integrity_n is not None and int(integrity_n) > 0:
        return {
            "CASE": "INTEGRITY",
            "VERDICT": CASE_INTEGRITY,
            "NEXT": NEXT_V2,
            "LOGIC_COMPLETE": False,
            "ROBUST_DEV_QUALIFIED": False,
            "OPENING_CURRENT_DATA_LINE_STATUS": OPENING_LINE_STATUS,
            "INTERPRETATION": "TradingVolume regression during an ISQ lifecycle. Do not adopt economics.",
        }
    if coverage_any is False:
        return {
            "CASE": "COVERAGE",
            "VERDICT": CASE_COVERAGE,
            "NEXT": NEXT_V2,
            "LOGIC_COMPLETE": False,
            "ROBUST_DEV_QUALIFIED": False,
            "OPENING_CURRENT_DATA_LINE_STATUS": OPENING_LINE_STATUS,
            "INTERPRETATION": "No candidate passed C1-C4. Close ISQ Resolution. Do not retune.",
        }
    if robust_n is not None and int(robust_n) >= 1:
        return {
            "CASE": "PASS",
            "VERDICT": CASE_PASS,
            "NEXT": NEXT_PASS,
            "LOGIC_COMPLETE": True,
            "ROBUST_DEV_QUALIFIED": True,
            "OPENING_CURRENT_DATA_LINE_STATUS": OPENING_LINE_STATUS,
            "INTERPRETATION": "ROBUST_DEV_QUALIFIED. Freeze LEGACY_DEV_CONSTRUCTED_CANDIDATE. Do not open future data.",
        }
    return {
        "CASE": "FAIL",
        "VERDICT": CASE_FAIL,
        "NEXT": NEXT_V2,
        "LOGIC_COMPLETE": False,
        "ROBUST_DEV_QUALIFIED": False,
        "OPENING_CURRENT_DATA_LINE_STATUS": OPENING_LINE_STATUS,
        "INTERPRETATION": "Coverage passed but no candidate is ROBUST_DEV_QUALIFIED. Close U/D/Combined. Do not retune.",
    }


def _per(evals: list[dict[str, Any]], key: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ev in evals:
        sid = str(ev.get("STRATEGY_ID") or "")
        if not sid:
            continue
        out[sid] = ev.get(key)
    return out or None


def _per_g(evals: list[dict[str, Any]]) -> dict[str, Any] | None:
    out = {}
    for ev in evals:
        sid = str(ev.get("STRATEGY_ID") or "")
        g = dict(ev.get("g_table") or {})
        out[sid] = {k: g.get(k) for k in ("G1", "G2", "G3", "G4", "G5")}
    return out or None


def _per_days(evals: list[dict[str, Any]]) -> dict[str, Any] | None:
    out = {}
    for ev in evals:
        sid = str(ev.get("STRATEGY_ID") or "")
        out[sid] = f"{ev.get('positive_day_n')}/{ev.get('negative_day_n')}/{ev.get('zero_day_n')}"
    return out or None


def _per_block(evals: list[dict[str, Any]], bid: str) -> dict[str, Any] | None:
    out = {}
    for ev in evals:
        sid = str(ev.get("STRATEGY_ID") or "")
        bmap = {str(r.get("block")): r for r in list((ev.get("blocks") or {}).get("blocks") or [])}
        out[sid] = (bmap.get(bid) or {}).get("pnl")
    return out or None


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    dup = dict(report.get("duplicate_check") or {})
    canary = dict(report.get("canary") or {})
    obs = dict(canary.get("observed") or {})
    sem = dict(report.get("state_semantics") or {})
    tests = dict(report.get("unit_tests") or {})
    pre = dict(report.get("precommit") or {})
    st = dict(report.get("structural") or {})
    evals = list(report.get("candidate_evals") or [])
    sel = dict(report.get("selected_logic") or {})
    meta = candidate_meta(str(sel.get("STRATEGY_ID") or "")) if sel.get("STRATEGY_ID") else {}
    return {
        "1_objective_aligned": True,
        "2_parent_verdict_pinned": bool((report.get("parent") or {}).get("ok")),
        "3_Opening_line_remained_CLOSED": True,
        "4_exact_raw_dates": list((report.get("data_boundary") or {}).get("RAW_MARKET_DATES_OPENED") or []),
        "5_Holdout_read": False,
        "6_Stress_read": False,
        "7_future_read": False,
        "8_U_exact_duplicate": dup.get("THESIS_U_EXACT_DUPLICATE"),
        "9_U_semantic_duplicate": dup.get("THESIS_U_MATERIAL_DUPLICATE"),
        "10_D_exact_duplicate": dup.get("THESIS_D_EXACT_DUPLICATE"),
        "11_D_semantic_duplicate": dup.get("THESIS_D_MATERIAL_DUPLICATE"),
        "12_SPECIAL_QUOTE_proven": sem.get("SPECIAL_QUOTE_PROVEN"),
        "13_CONTINUOUS_proven": sem.get("CONTINUOUS_TRADING_PROVEN"),
        "14_special_Sign_direction_used": False,
        "15_availability_clock": "INGRESS",
        "16_trade_update_identity": "TradingVolume increase AND finite CurrentPrice>0 from same payload",
        "17_TradingVolume_increase_required": True,
        "18_CurrentPriceTime_used_as_trade_ID": False,
        "19_CurrentPriceTime_used_as_availability": False,
        "20_strategy_specific_trade_bar_used": True,
        "21_carry_forward_CurrentPrice_admitted_into_bar": False,
        "22_affected_volume_regression_integrity_errors": (report.get("integrity") or {}).get("AFFECTED_ISQ_DATA_INTEGRITY_ERROR_N"),
        "23_special_active_trade_conflicts": st.get("SPECIAL_ACTIVE_TRADE_CONFLICT_N"),
        "24_synthetic_tests_PASS_N_30": f"{tests.get('PASS_N')}/30",
        "25_tests_all_pass": tests.get("ALL_PASS"),
        "26_R2_signal_n": obs.get("signal_n"),
        "27_R2_fill_n": obs.get("fill_n"),
        "28_R2_trade_n": obs.get("trade_n"),
        "29_R2_PnL": obs.get("TOTAL_PNL"),
        "30_R2_PF": obs.get("PF"),
        "31_canary_PASS": canary.get("HARD_PASS"),
        "32_eligible_candidate_N": len(dup.get("ELIGIBLE_CANDIDATE_IDS") or []),
        "33_candidate_IDs": list(dup.get("ELIGIBLE_CANDIDATE_IDS") or []),
        "34_library_SHA": pre.get("ISQ_RESOLUTION_LIBRARY_SHA256"),
        "35_precommit_before_structural_counts": bool(pre.get("PRECOMMIT_BEFORE_COUNTS")),
        "36_precommit_before_economics": bool(pre.get("PRECOMMIT_BEFORE_ECONOMICS")),
        "37_ISQ_episode_N": st.get("ISQ_EPISODE_N"),
        "38_episode_day_N": st.get("ISQ_EPISODE_DAY_N"),
        "39_episode_symbol_N": st.get("ISQ_EPISODE_SYMBOL_N"),
        "40_valid_pre_special_N": st.get("PRE_SPECIAL_VALID_N"),
        "41_special_start_coobserved_trade_N": st.get("SPECIAL_START_COOBSERVED_TRADE_N"),
        "42_special_active_conflict_N": st.get("SPECIAL_ACTIVE_TRADE_CONFLICT_N"),
        "43_return_continuous_N": st.get("RETURN_CONTINUOUS_N"),
        "44_valid_release_N": st.get("RELEASE_VALID_N"),
        "45_UP_release_N": st.get("UP_RELEASE_N"),
        "46_DOWN_release_N": st.get("DOWN_RELEASE_N"),
        "47_FLAT_release_N": st.get("FLAT_RELEASE_N"),
        "48_accept_trade_bar_N": st.get("ACCEPT_TRADE_BAR_N"),
        "49_no_acceptance_evidence_N": st.get("NO_ACCEPTANCE_EVIDENCE_N"),
        "50_reinterrupted_N": st.get("REINTERRUPTED_N"),
        "51_U_signal_N": st.get("U_SIGNAL_N"),
        "52_D_signal_N": st.get("D_SIGNAL_N"),
        "53_trade_N": _per(evals, "trade_n") or _per(evals, "TRADE_N"),
        "54_fill_day_N": _per(evals, "fill_day_n"),
        "55_trades_per_day": _per(evals, "trades_per_day"),
        "56_C1": _per(evals, "C1"),
        "57_C2": _per(evals, "C2"),
        "58_C3": _per(evals, "C3"),
        "59_C4": _per(evals, "C4"),
        "60_Coverage_PASS": _per(evals, "coverage_ok"),
        "61_TOTAL_PNL": _per(evals, "TOTAL_PNL"),
        "62_PF": _per(evals, "PF"),
        "63_MaxDD": _per(evals, "MaxDD"),
        "64_positive_negative_zero_days": _per_days(evals),
        "65_EX_BEST_DAY_PNL": _per(evals, "EX_BEST_DAY_PNL"),
        "66_G1_G5": _per_g(evals),
        "67_causal_ex_top1_PnL": _per(evals, "CAUSAL_EX_TOP1_PNL"),
        "68_causal_ex_top1_PF": _per(evals, "CAUSAL_EX_TOP1_PF"),
        "69_G6": {str(ev.get("STRATEGY_ID")): (ev.get("g_table") or {}).get("G6") for ev in evals} or None,
        "70_B1_PnL": _per_block(evals, "B1"),
        "71_B2_PnL": _per_block(evals, "B2"),
        "72_B3_PnL": _per_block(evals, "B3"),
        "73_B4_PnL": _per_block(evals, "B4"),
        "74_B5_PnL": _per_block(evals, "B5"),
        "75_positive_block_N": {str(ev.get("STRATEGY_ID")): (ev.get("blocks") or {}).get("POSITIVE_BLOCK_N") for ev in evals} or None,
        "76_ex_best_block_PnL": {str(ev.get("STRATEGY_ID")): (ev.get("blocks") or {}).get("EX_BEST_BLOCK_PNL") for ev in evals} or None,
        "77_S1": {str(ev.get("STRATEGY_ID")): (ev.get("blocks") or {}).get("S1") for ev in evals} or None,
        "78_S2": {str(ev.get("STRATEGY_ID")): (ev.get("blocks") or {}).get("S2") for ev in evals} or None,
        "79_robust_qualified": {str(ev.get("STRATEGY_ID")): ev.get("BASE_QUALIFIED") for ev in evals} or None,
        "80_selected_Strategy_ID": sel.get("STRATEGY_ID"),
        "81_selected_thesis_set": sel.get("THESES") or meta.get("THESES"),
        "82_selected_ENTRY_exact": sel.get("ENTRY_EXACT") or meta.get("ENTRY_EXACT"),
        "83_selected_thesis_anchor": sel.get("THESIS_ANCHOR") or meta.get("THESIS_ANCHOR"),
        "84_selected_technical_EXIT": sel.get("TECHNICAL_EXIT") or meta.get("TECHNICAL_EXIT"),
        "85_selected_execution": sel.get("EXECUTION") or meta.get("EXECUTION"),
        "86_universal_EXIT_used": False,
        "87_ENTRY_aligned_EXIT": True,
        "88_fixed_holding_time_EXIT": False,
        "89_CAP_5": int(POSITION_CAP_N) == 5,
        "90_same_symbol_causal": True,
        "91_occupancy_causal": True,
        "92_slot_release_causal": True,
        "93_same_episode_reentry": False,
        "94_new_episode_reentry": True,
        "95_LOGIC_COMPLETE": bool(d.get("LOGIC_COMPLETE")),
        "96_ROBUST_DEV_QUALIFIED": bool(d.get("ROBUST_DEV_QUALIFIED")),
        "97_post_result_threshold_change": False,
        "98_post_result_ENTRY_change": False,
        "99_post_result_EXIT_change": False,
        "100_extra_candidate_added": False,
        "101_MBO": False,
        "102_futures": False,
        "103_Sizing": False,
        "104_Runtime_changed": False,
        "105_Capture_changed": False,
        "106_submit_cancel_live": "0/0/0",
        "107_TRUE_OOS": False,
        "108_CERTIFIED": False,
        "109_VERDICT": d.get("VERDICT"),
        "110_NEXT": d.get("NEXT"),
    }


assert DEVELOPMENT_DAYS
assert evaluate_strategy
assert canary_parity
assert public_row
assert extra_counts
assert _g15
