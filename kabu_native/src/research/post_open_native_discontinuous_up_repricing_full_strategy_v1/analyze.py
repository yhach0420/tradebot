"""Verdict ladder. Semantics fail stops before scan/economics."""
from __future__ import annotations

from typing import Any

from research.post_open_native_discontinuous_up_repricing_full_strategy_v1 import (
    CASE_COVERAGE,
    CASE_DUP,
    CASE_ECON,
    CASE_INPUT,
    CASE_INTEGRITY,
    CASE_PARITY,
    CASE_PASS,
    CASE_ROBUST,
    CASE_SEMANTICS,
    CASE_TESTS,
    DEVELOPMENT_DAYS,
    NEXT_FIX,
    NEXT_PASS,
    NEXT_V5,
    OPENING_LINE_STATUS,
    STRATEGY_ID,
)


def decide(
    *,
    semantics_ok: bool | None = None,
    input_ok: bool | None = None,
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
        "CALC_PRICE_STRATEGY_STATUS": "STRATEGY_CLOSED",
        "CALC_PRICE_RETUNE": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }
    if semantics_ok is False:
        return {
            **base,
            "CASE": "SEMANTICS",
            "VERDICT": CASE_SEMANTICS,
            "NEXT": NEXT_V5,
            "INTERPRETATION": (
                "CurrentPriceChangeStatus UP/上昇 exact code is not in trusted local sources "
                "(no kabu_STATION_API.yaml enum table). Do not guess 0056. CLOSE. No scan. No economics."
            ),
        }
    if input_ok is False:
        return {
            **base,
            "CASE": "INPUT",
            "VERDICT": CASE_INPUT,
            "NEXT": NEXT_V5,
            "INTERPRETATION": "Eligible NATIVE_DISCONT_UP_EVENT coverage insufficient. Do not lower thresholds.",
        }
    if dup_ok is False:
        return {
            **base,
            "CASE": "DUP",
            "VERDICT": CASE_DUP,
            "NEXT": NEXT_V5,
            "INTERPRETATION": "Material duplicate of ISQ or another closed family. Stop. No ISQ rescue.",
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
            "NEXT": NEXT_V5,
            "INTERPRETATION": "Integrity error. Do not adopt economics.",
        }
    if coverage_ok is False:
        return {
            **base,
            "CASE": "COVERAGE",
            "VERDICT": CASE_COVERAGE,
            "NEXT": NEXT_V5,
            "INTERPRETATION": "C1-C4 Coverage FAIL. Economics diagnostic only. Do not change EXIT.",
        }
    if g15 is False:
        return {
            **base,
            "CASE": "ECON",
            "VERDICT": CASE_ECON,
            "NEXT": NEXT_V5,
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
        "NEXT": NEXT_V5,
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
    sem = dict(report.get("status_semantics") or {})
    raw = dict(report.get("raw_support") or {})
    ov = dict(report.get("isq_overlap") or {})
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
        "11_CurrentPriceStatus_semantics_proven": sem.get("STATUS_FIELD_PROVEN"),
        "12_CurrentPriceChangeStatus_semantics_proven": bool(sem.get("UP_CHANGE_STATUS_CODE")),
        "13_NORMAL_exact_code": sem.get("NORMAL_STATUS_CODE"),
        "14_DISCONTINUOUS_exact_code": sem.get("DISCONTINUOUS_STATUS_CODE"),
        "15_UP_exact_code": sem.get("UP_CHANGE_STATUS_CODE"),
        "16_trusted_source": {
            "status": sem.get("TRUSTED_SOURCE_STATUS"),
            "change": sem.get("TRUSTED_SOURCE_CHANGE"),
        },
        "17_status_distribution": raw.get("status_distribution"),
        "18_change_status_distribution": raw.get("change_status_distribution"),
        "19_discontinuous_row_N": raw.get("discontinuous_row_n"),
        "20_discontinuous_day_N": raw.get("discontinuous_day_n"),
        "21_discontinuous_symbol_N": raw.get("discontinuous_symbol_n"),
        "22_discont_UP_pair_N": raw.get("pair_row_n"),
        "23_eligible_event_N": st.get("eligible_event_n") or raw.get("eligible_event_n"),
        "24_eligible_day_N": st.get("eligible_day_n") or raw.get("eligible_day_n"),
        "25_eligible_symbol_N": st.get("eligible_symbol_n") or raw.get("eligible_symbol_n"),
        "26_special_quote_overlap_N_rate": {
            "n": ov.get("special_quote_overlap_n"),
            "rate": ov.get("special_quote_overlap_rate"),
        },
        "27_material_duplicate": dup.get("MATERIAL_SEMANTIC_DUPLICATE"),
        "28_exact_duplicate": dup.get("EXACT_DUPLICATE"),
        "29_strategy_ID": STRATEGY_ID,
        "30_SPEC_SHA": pre.get("SPEC_SHA256"),
        "31_precommit_before_economics": pre.get("PRECOMMIT_BEFORE_ECONOMICS"),
        "32_tests_PASS": tests.get("ALL_PASS"),
        "33_canary_PASS": canary.get("HARD_PASS"),
        "34_signal_N": extra.get("signal_n") if extra else st.get("signal_n"),
        "35_X1_fill_N": extra.get("X1_fill_n"),
        "36_portfolio_fill_N": extra.get("fill_n") if extra else ev.get("fill_n"),
        "37_trade_N": extra.get("trade_n") if extra else ev.get("trade_n"),
        "38_fill_day_N": ev.get("fill_day_n"),
        "39_trades_per_day": ev.get("trades_per_day"),
        "40_pre_fill_normal_return_expiry_N": st.get("pre_fill_normal_return_expire_n"),
        "41_technical_EXIT_fire_N": extra.get("technical_exit_fire_n"),
        "42_session_exit_unfilled_N": ev.get("session_exit_unfilled_n"),
        "43_C1": ev.get("C1"),
        "44_C2": ev.get("C2"),
        "45_C3": ev.get("C3"),
        "46_C4": ev.get("C4"),
        "47_TOTAL_PNL": ev.get("TOTAL_PNL") if eco else None,
        "48_PF": ev.get("PF") if eco else None,
        "49_MaxDD": ev.get("MaxDD") if ev.get("MaxDD") is not None else ev.get("MAXDD"),
        "50_pos_neg_zero_days": f"{ev.get('positive_day_n')}/{ev.get('negative_day_n')}/{ev.get('zero_day_n')}"
        if eco
        else None,
        "51_EX_BEST_DAY_PNL": ev.get("EX_BEST_DAY_PNL") if eco else None,
        "52_G1_G5": {k: g.get(k) for k in ("G1", "G2", "G3", "G4", "G5")} if eco else None,
        "53_causal_ex_top1_PnL": ev.get("CAUSAL_EX_TOP1_PNL"),
        "54_G6": g.get("G6"),
        "55_B1_B5": {k: (bmap.get(k) or {}).get("pnl") for k in ("B1", "B2", "B3", "B4", "B5")} if eco else None,
        "56_positive_block_N": blocks.get("POSITIVE_BLOCK_N"),
        "57_ex_best_block_PnL": blocks.get("EX_BEST_BLOCK_PNL"),
        "58_S1": blocks.get("S1"),
        "59_S2": blocks.get("S2"),
        "60_LOGIC_COMPLETE": d.get("LOGIC_COMPLETE"),
        "61_ROBUST_DEV_QUALIFIED": d.get("ROBUST_DEV_QUALIFIED"),
        "62_post_result_status_code_change": False,
        "63_ENTRY_changed": False,
        "64_EXIT_changed": False,
        "65_extra_candidate": False,
        "66_MBO": False,
        "67_futures": False,
        "68_sizing": False,
        "69_TRUE_OOS": False,
        "70_CERTIFIED": False,
        "71_VERDICT": d.get("VERDICT"),
        "72_NEXT": d.get("NEXT"),
    }
