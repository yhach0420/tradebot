"""Coverage, G1-G6, blocks. No retune. No extra candidate."""
from __future__ import annotations

from typing import Any

from research.full_causal_mechanism_discovery_v1.analyze import canary_parity, evaluate_strategy, public_row
from research.post_open_calc_price_lead_acceptance_full_strategy_v1 import (
    CASE_COVERAGE,
    CASE_DUP,
    CASE_ECON,
    CASE_INDEP,
    CASE_INPUT,
    CASE_INTEGRITY,
    CASE_PARITY,
    CASE_PASS,
    CASE_ROBUST,
    CASE_SEMANTICS,
    CASE_TESTS,
    DEVELOPMENT_DAYS,
    EXIT_ID,
    NEXT_FIX,
    NEXT_PASS,
    NEXT_V4,
    OPENING_LINE_STATUS,
    STRATEGY_ID,
)
from research.simple_tech_entry_family.portfolio import _sym


def _g15(ev: dict[str, Any]) -> bool:
    g = dict(ev.get("g_table") or {})
    return bool(g.get("G1") and g.get("G2") and g.get("G3") and g.get("G4") and g.get("G5"))


def extra_counts(ev: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    trades = list(ev.get("_trades") or [])
    ep_ids = [str((t.get("src") or {}).get("episode_id") or "") for t in trades]
    seen: set[str] = set()
    same_ep = 0
    for eid in ep_ids:
        if not eid:
            continue
        if eid in seen:
            same_ep += 1
        seen.add(eid)
    by_sd: dict[tuple[str, str], list[str]] = {}
    for t in trades:
        key = (str(t.get("date") or ""), _sym(t))
        by_sd.setdefault(key, []).append(str((t.get("src") or {}).get("episode_id") or ""))
    new_ep = 0
    for ids in by_sd.values():
        uniq = [x for x in ids if x]
        if len(set(uniq)) >= 2:
            new_ep += max(0, len(uniq) - 1)
    tech_fire = sum(1 for r in rows if r.get("technical_exit_fire") or r.get("trigger_t") is not None)
    tech_fill = sum(1 for t in trades if str(t.get("exit_reason") or "") == EXIT_ID)
    x1_fill = sum(1 for r in rows if r.get("WOULD_FILL"))
    return {
        "X1_fill_n": int(x1_fill),
        "X1_nonfill_n": int(len(rows) - x1_fill),
        "technical_exit_fire_n": int(tech_fire),
        "technical_exit_fill_n": int(tech_fill),
        "session_close_n": int((ev.get("exit_reasons") or {}).get("SESSION_CLOSE") or 0),
        "same_episode_reentry_n": int(same_ep),
        "new_episode_reentry_n": int(new_ep),
        "signal_n": ev.get("signal_n"),
        "fill_n": ev.get("fill_n"),
        "trade_n": ev.get("trade_n") or ev.get("TRADE_N"),
    }


def decide(
    *,
    semantics_ok: bool | None = None,
    input_ok: bool | None = None,
    indep_ok: bool | None = None,
    dup_ok: bool | None = None,
    tests_ok: bool | None = None,
    canary_ok: bool | None = None,
    integrity_n: int | None = None,
    coverage_ok: bool | None = None,
    g15: bool | None = None,
    robust: bool | None = None,
) -> dict[str, Any]:
    base = {
        "LOGIC_COMPLETE": False,
        "ROBUST_DEV_QUALIFIED": False,
        "OPENING_CURRENT_DATA_LINE_STATUS": OPENING_LINE_STATUS,
        "ISQ_RESOLUTION_STATUS": "CLOSED",
        "AOP_ARCHITECTURE_STATUS": "CLOSED",
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }
    if semantics_ok is False:
        return {
            **base,
            "CASE": "SEMANTICS",
            "VERDICT": CASE_SEMANTICS,
            "NEXT": NEXT_V4,
            "INTERPRETATION": "CalcPrice official identity is not proven enough for the lead-acceptance thesis. CLOSE. No economics.",
        }
    if indep_ok is False:
        return {
            **base,
            "CASE": "INDEP",
            "VERDICT": CASE_INDEP,
            "NEXT": NEXT_V4,
            "INTERPRETATION": "CalcPrice is substantially a CurrentPrice copy. No independent state. CLOSE.",
        }
    if input_ok is False:
        return {
            **base,
            "CASE": "INPUT",
            "VERDICT": CASE_INPUT,
            "NEXT": NEXT_V4,
            "INTERPRETATION": "CalcPrice is not a post-open dynamic causal input. CLOSE. Do not threshold-rescue.",
        }
    if dup_ok is False:
        return {
            **base,
            "CASE": "DUP",
            "VERDICT": CASE_DUP,
            "NEXT": NEXT_V4,
            "INTERPRETATION": "Material duplicate of an existing family or top-of-book state. Stop.",
        }
    if tests_ok is False:
        return {
            **base,
            "CASE": "TESTS",
            "VERDICT": CASE_TESTS,
            "NEXT": NEXT_FIX,
            "INTERPRETATION": "Synthetic tests failed. Fix implementation only.",
        }
    if canary_ok is False:
        return {
            **base,
            "CASE": "PARITY",
            "VERDICT": CASE_PARITY,
            "NEXT": NEXT_FIX,
            "INTERPRETATION": "R2_X1_Z3 engine canary failed. Fix implementation only.",
        }
    if integrity_n is not None and int(integrity_n) > 0:
        return {
            **base,
            "CASE": "INTEGRITY",
            "VERDICT": CASE_INTEGRITY,
            "NEXT": NEXT_V4,
            "INTERPRETATION": "TradingVolume regression during a lead episode. Do not adopt economics.",
        }
    if coverage_ok is False:
        return {
            **base,
            "CASE": "COVERAGE",
            "VERDICT": CASE_COVERAGE,
            "NEXT": NEXT_V4,
            "INTERPRETATION": "C1-C4 Coverage FAIL. Economics diagnostic only. Do not change EXIT.",
        }
    if g15 is False:
        return {
            **base,
            "CASE": "ECON",
            "VERDICT": CASE_ECON,
            "NEXT": NEXT_V4,
            "INTERPRETATION": "Coverage passed but G1-G5 failed. Do not retune.",
        }
    if robust is True:
        return {
            **base,
            "CASE": "PASS",
            "VERDICT": CASE_PASS,
            "NEXT": NEXT_PASS,
            "LOGIC_COMPLETE": True,
            "ROBUST_DEV_QUALIFIED": True,
            "INTERPRETATION": "ROBUST_DEV_QUALIFIED on LEGACY_DEV only. TRUE_OOS=false. No Sizing.",
        }
    return {
        **base,
        "CASE": "ROBUST",
        "VERDICT": CASE_ROBUST,
        "NEXT": NEXT_V4,
        "INTERPRETATION": "Coverage and G1-G5 passed but G6 or S1-S2 failed. Do not retune.",
    }


def block_map(ev: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for b in list((ev.get("blocks") or {}).get("blocks") or []):
        out[str(b.get("block"))] = b
    return out


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    dup = dict(report.get("duplicate_check") or {})
    canary = dict(report.get("canary") or {})
    tests = dict(report.get("unit_tests") or {})
    pre = dict(report.get("precommit") or {})
    st = dict(report.get("structural") or {})
    sem = dict(report.get("calcprice_semantics") or {})
    dyn = dict(((report.get("calcprice_dynamics") or {}).get("fields") or {}))
    support = dict((report.get("calcprice_dynamics") or {}).get("support") or {})
    evs = list(report.get("candidate_evals") or [])
    ev = evs[0] if evs else {}
    extra = dict(ev.get("extra") or {})
    g = dict(ev.get("g_table") or {})
    blocks = dict(ev.get("blocks") or {})
    bmap = block_map(ev)
    parent = dict(report.get("parent") or {})
    eco = bool(ev)
    return {
        "1_objective_aligned": True,
        "2_parent_pinned": bool(parent.get("ok")),
        "3_exact_dates": list((report.get("data_boundary") or {}).get("ALLOWED") or list(DEVELOPMENT_DAYS)),
        "4_session_AM_only": True,
        "5_Holdout_read": False,
        "6_Stress_read": False,
        "7_future_read": False,
        "8_Runtime_changed": False,
        "9_Capture_changed": False,
        "10_submit_cancel_live": "0/0/0",
        "11_CalcPrice_semantics_proven": sem.get("SEMANTICS_PROVEN"),
        "12_exact_trusted_meaning": sem.get("OFFICIAL_MEANING"),
        "13_post_open_dynamic_PASS": support.get("PASS"),
        "14_finite_rate": dyn.get("finite_rate"),
        "15_zero_rate": dyn.get("zero_rate"),
        "16_unique_value_N": dyn.get("unique_value_n"),
        "17_change_event_N": dyn.get("change_event_n"),
        "18_change_day_N": dyn.get("change_day_n"),
        "19_change_symbol_N": dyn.get("change_symbol_n"),
        "20_CalcPrice_eq_CurrentPrice_rate": dyn.get("eq_current_rate"),
        "21_CalcPrice_eq_Bid1_rate": dyn.get("eq_bid1_rate"),
        "22_CalcPrice_eq_Ask1_rate": dyn.get("eq_ask1_rate"),
        "23_independent_state_PASS": dyn.get("INDEPENDENT_OF_CURRENT"),
        "24_exact_duplicate": dup.get("EXACT_DUPLICATE"),
        "25_material_duplicate": dup.get("MATERIAL_SEMANTIC_DUPLICATE"),
        "26_strategy_ID": STRATEGY_ID,
        "27_precommit_SHA": pre.get("SPEC_SHA256") or pre.get("LIBRARY_SHA256"),
        "28_precommit_before_counts": pre.get("PRECOMMIT_BEFORE_STRUCTURAL_COUNTS"),
        "29_precommit_before_economics": pre.get("PRECOMMIT_BEFORE_ECONOMICS"),
        "30_tests_PASS": tests.get("ALL_PASS"),
        "31_canary_PASS": canary.get("HARD_PASS"),
        "32_calc_lead_start_N": st.get("calc_lead_start_n"),
        "33_valid_anchor_N": st.get("valid_anchor_n"),
        "34_signal_N": extra.get("signal_n") if extra else st.get("accept_signal_n"),
        "35_fill_N": extra.get("fill_n") if extra else None,
        "36_trade_N": extra.get("trade_n") if extra else ev.get("trade_n"),
        "37_fill_day_N": ev.get("fill_day_n"),
        "38_trades_per_day": ev.get("trades_per_day"),
        "39_C1": ev.get("C1"),
        "40_C2": ev.get("C2"),
        "41_C3": ev.get("C3"),
        "42_C4": ev.get("C4"),
        "43_session_exit_unfilled_n": ev.get("session_exit_unfilled_n"),
        "44_TOTAL_PNL": ev.get("TOTAL_PNL") if eco else None,
        "45_PF": ev.get("PF") if eco else None,
        "46_MaxDD": ev.get("MaxDD") if ev.get("MaxDD") is not None else ev.get("MAXDD"),
        "47_pos_neg_zero_days": f"{ev.get('positive_day_n')}/{ev.get('negative_day_n')}/{ev.get('zero_day_n')}" if eco else None,
        "48_EX_BEST_DAY_PNL": ev.get("EX_BEST_DAY_PNL") if eco else None,
        "49_G1_G5": {k: g.get(k) for k in ("G1", "G2", "G3", "G4", "G5")} if eco else None,
        "50_causal_ex_top1_PnL": ev.get("CAUSAL_EX_TOP1_PNL"),
        "51_G6": g.get("G6"),
        "52_B1_B5": {k: (bmap.get(k) or {}).get("pnl") for k in ("B1", "B2", "B3", "B4", "B5")} if eco else None,
        "53_positive_block_N": blocks.get("POSITIVE_BLOCK_N"),
        "54_ex_best_block_PnL": blocks.get("EX_BEST_BLOCK_PNL"),
        "55_S1": blocks.get("S1"),
        "56_S2": blocks.get("S2"),
        "57_LOGIC_COMPLETE": d.get("LOGIC_COMPLETE"),
        "58_ROBUST_DEV_QUALIFIED": d.get("ROBUST_DEV_QUALIFIED"),
        "59_threshold_changed": False,
        "60_ENTRY_changed": False,
        "61_EXIT_changed": False,
        "62_extra_candidate": False,
        "63_MBO": False,
        "64_futures": False,
        "65_Sizing": False,
        "66_TRUE_OOS": False,
        "67_CERTIFIED": False,
        "68_VERDICT": d.get("VERDICT"),
        "69_NEXT": d.get("NEXT"),
        "formula_proven": sem.get("FORMULA_PROVEN"),
        "eq_mid_rate": dyn.get("eq_mid_rate"),
    }


assert canary_parity
assert evaluate_strategy
assert public_row
assert DEVELOPMENT_DAYS
assert STRATEGY_ID
