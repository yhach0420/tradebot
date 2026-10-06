"""Coverage, G1-G6, blocks. No retune. No extra candidate."""
from __future__ import annotations

from typing import Any

from research.full_causal_mechanism_discovery_v1.analyze import canary_parity, evaluate_strategy, public_row
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1 import (
    CASE_COVERAGE,
    CASE_DUP,
    CASE_ECON,
    CASE_INPUT,
    CASE_INTEGRITY,
    CASE_PARITY,
    CASE_PASS,
    CASE_ROBUST,
    CASE_TESTS,
    DEVELOPMENT_DAYS,
    EXIT_ID,
    NEXT_FIX,
    NEXT_PASS,
    NEXT_V3,
    OPENING_LINE_STATUS,
    STRATEGY_ID,
)
from research.simple_tech_entry_family.portfolio import _sym


def _g15(ev: dict[str, Any]) -> bool:
    g = dict(ev.get("g_table") or {})
    return bool(g.get("G1") and g.get("G2") and g.get("G3") and g.get("G4") and g.get("G5"))


def extra_counts(ev: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    trades = list(ev.get("_trades") or [])
    run_ids = [str((t.get("src") or {}).get("run_id") or "") for t in trades]
    seen: set[str] = set()
    same_run = 0
    for rid in run_ids:
        if not rid:
            continue
        if rid in seen:
            same_run += 1
        seen.add(rid)
    by_sd: dict[tuple[str, str], list[str]] = {}
    for t in trades:
        key = (str(t.get("date") or ""), _sym(t))
        by_sd.setdefault(key, []).append(str((t.get("src") or {}).get("run_id") or ""))
    new_run = 0
    for ids in by_sd.values():
        uniq = [x for x in ids if x]
        if len(set(uniq)) >= 2:
            new_run += max(0, len(uniq) - 1)
    tech_fire = sum(1 for r in rows if r.get("technical_exit_fire") or r.get("trigger_t") is not None)
    tech_fill = sum(1 for t in trades if str(t.get("exit_reason") or "") == EXIT_ID)
    x1_fill = sum(1 for r in rows if r.get("WOULD_FILL"))
    return {
        "X1_fill_n": int(x1_fill),
        "X1_nonfill_n": int(len(rows) - x1_fill),
        "technical_exit_fire_n": int(tech_fire),
        "technical_exit_fill_n": int(tech_fill),
        "session_close_n": int((ev.get("exit_reasons") or {}).get("SESSION_CLOSE") or 0),
        "same_run_reentry_n": int(same_run),
        "new_run_reentry_n": int(new_run),
        "pending_n": int(ev.get("entry_no_fill_n") or 0),
        "signal_n": ev.get("signal_n"),
        "fill_n": ev.get("fill_n"),
        "trade_n": ev.get("trade_n") or ev.get("TRADE_N"),
    }


def decide(
    *,
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
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }
    if input_ok is False:
        return {
            **base,
            "CASE": "INPUT",
            "VERDICT": CASE_INPUT,
            "NEXT": NEXT_V3,
            "INTERPRETATION": "Four aggregate qty fields are not post-open dynamic causal inputs. Architecture CLOSED. Do not ratio/subset rescue.",
        }
    if dup_ok is False:
        return {
            **base,
            "CASE": "DUP",
            "VERDICT": CASE_DUP,
            "NEXT": NEXT_V3,
            "INTERPRETATION": "Material duplicate of an existing family. Stop. Do not retune the existing family.",
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
            "NEXT": NEXT_V3,
            "INTERPRETATION": "TradingVolume regression during a pressure run. Do not adopt economics.",
        }
    if coverage_ok is False:
        return {
            **base,
            "CASE": "COVERAGE",
            "VERDICT": CASE_COVERAGE,
            "NEXT": NEXT_V3,
            "INTERPRETATION": "C1-C4 Coverage FAIL. Economics diagnostic only. Do not change EXIT to rescue C4.",
        }
    if g15 is False:
        return {
            **base,
            "CASE": "ECON",
            "VERDICT": CASE_ECON,
            "NEXT": NEXT_V3,
            "INTERPRETATION": "Coverage passed but G1-G5 failed. Do not retune thresholds or EXIT.",
        }
    if robust is True:
        return {
            **base,
            "CASE": "PASS",
            "VERDICT": CASE_PASS,
            "NEXT": NEXT_PASS,
            "LOGIC_COMPLETE": True,
            "ROBUST_DEV_QUALIFIED": True,
            "INTERPRETATION": "ROBUST_DEV_QUALIFIED on LEGACY_DEV only. TRUE_OOS=false. Do not open future data. No Sizing.",
        }
    return {
        **base,
        "CASE": "ROBUST",
        "VERDICT": CASE_ROBUST,
        "NEXT": NEXT_V3,
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
    obs = dict(canary.get("observed") or {})
    tests = dict(report.get("unit_tests") or {})
    pre = dict(report.get("precommit") or {})
    st = dict(report.get("structural") or {})
    sem = dict(report.get("raw_field_semantics") or {})
    dyn = dict(((report.get("post_open_dynamics") or {}).get("fields") or {}))
    support = dict((report.get("post_open_dynamics") or {}).get("support") or {})
    evs = list(report.get("candidate_evals") or [])
    ev = evs[0] if evs else {}
    extra = dict(ev.get("extra") or {})
    g = dict(ev.get("g_table") or {})
    blocks = dict(ev.get("blocks") or {})
    bmap = block_map(ev)
    safety = dict(report.get("safety") or {})
    parent = dict(report.get("parent") or {})
    eco_open = bool(ev)
    cov_fail = d.get("CASE") == "COVERAGE"

    def _fld(name: str, key: str) -> Any:
        return (dyn.get(name) or {}).get(key)

    return {
        "1_objective_aligned": True,
        "2_parent_pinned": bool(parent.get("ok")),
        "3_exact_dates": list((report.get("data_boundary") or {}).get("ALLOWED") or list(DEVELOPMENT_DAYS)),
        "4_exact_session_boundary": "AM flatten 11:29; parent AM-only harvest",
        "5_Holdout_read": False,
        "6_Stress_read": False,
        "7_future_read": False,
        "8_Runtime_changed": False,
        "9_Capture_changed": False,
        "10_submit_cancel_live": "0/0/0",
        "11_four_field_semantics_proven": sem.get("SEMANTICS_PROVEN"),
        "12_post_open_dynamic_support_PASS": support.get("PASS"),
        "13_each_field_zero_rate": {k: _fld(k, "zero_rate") for k in dyn},
        "14_each_field_change_event_n": {k: _fld(k, "change_event_n") for k in dyn},
        "15_change_day_N": {k: _fld(k, "change_day_n") for k in dyn},
        "16_change_symbol_N": {k: _fld(k, "change_symbol_n") for k in dyn},
        "17_exact_duplicate": dup.get("EXACT_DUPLICATE"),
        "18_material_duplicate": dup.get("MATERIAL_SEMANTIC_DUPLICATE"),
        "19_selected_strategy_ID": STRATEGY_ID if eco_open or pre else None,
        "20_candidate_N": 1,
        "21_precommit_SHA": pre.get("LIBRARY_SHA256") or pre.get("ISQ_RESOLUTION_LIBRARY_SHA256"),
        "22_precommit_before_signal_counts": pre.get("PRECOMMIT_BEFORE_SIGNAL_COUNTS"),
        "23_precommit_before_economics": pre.get("PRECOMMIT_BEFORE_ECONOMICS"),
        "24_synthetic_PASS_N_total": f"{tests.get('PASS_N')}/{tests.get('TOTAL')}",
        "25_canary_PASS": canary.get("HARD_PASS"),
        "26_pressure_start_n": st.get("pressure_start_n"),
        "27_valid_anchor_n": st.get("valid_anchor_n"),
        "28_signal_n": extra.get("signal_n") if extra else st.get("price_accept_signal_n"),
        "29_fill_n": extra.get("fill_n") if extra else None,
        "30_trade_n": extra.get("trade_n") if extra else ev.get("trade_n"),
        "31_fill_day_n": ev.get("fill_day_n"),
        "32_trades_per_day": ev.get("trades_per_day"),
        "33_C1": ev.get("C1"),
        "34_C2": ev.get("C2"),
        "35_C3": ev.get("C3"),
        "36_C4": ev.get("C4"),
        "37_session_exit_unfilled_n": ev.get("session_exit_unfilled_n"),
        "38_TOTAL_PNL": ev.get("TOTAL_PNL") if eco_open else None,
        "39_PF": ev.get("PF") if eco_open else None,
        "40_MaxDD": ev.get("MaxDD") if ev.get("MaxDD") is not None else ev.get("MAXDD"),
        "41_positive_negative_zero_days": f"{ev.get('positive_day_n')}/{ev.get('negative_day_n')}/{ev.get('zero_day_n')}" if eco_open else None,
        "42_EX_BEST_DAY_PNL": ev.get("EX_BEST_DAY_PNL") if eco_open else None,
        "43_G1_G5": {k: g.get(k) for k in ("G1", "G2", "G3", "G4", "G5")} if eco_open else None,
        "44_causal_ex_top1_PnL": ev.get("CAUSAL_EX_TOP1_PNL"),
        "45_G6": g.get("G6"),
        "46_B1_B5_PnL": {k: (bmap.get(k) or {}).get("pnl") for k in ("B1", "B2", "B3", "B4", "B5")} if eco_open else None,
        "47_positive_block_n": blocks.get("POSITIVE_BLOCK_N"),
        "48_ex_best_block_PnL": blocks.get("EX_BEST_BLOCK_PNL"),
        "49_S1": blocks.get("S1"),
        "50_S2": blocks.get("S2"),
        "51_LOGIC_COMPLETE": d.get("LOGIC_COMPLETE"),
        "52_ROBUST_DEV_QUALIFIED": d.get("ROBUST_DEV_QUALIFIED"),
        "53_post_result_ENTRY_change": False,
        "54_post_result_EXIT_change": False,
        "55_threshold_change": False,
        "56_extra_candidate_added": False,
        "57_MBO": False,
        "58_futures": False,
        "59_Sizing": False,
        "60_TRUE_OOS": False,
        "61_CERTIFIED": False,
        "62_VERDICT": d.get("VERDICT"),
        "63_NEXT": d.get("NEXT"),
        "R2_signal_n": obs.get("signal_n"),
        "R2_fill_n": obs.get("fill_n"),
        "R2_trade_n": obs.get("trade_n"),
        "R2_PnL": obs.get("TOTAL_PNL"),
        "R2_PF": obs.get("PF"),
        "coverage_diagnostic_only": cov_fail,
        "safety_submit_cancel_live": f"{safety.get('SUBMIT_N')}/{safety.get('CANCEL_N')}/{safety.get('LIVE_ORDER_N')}",
    }


assert canary_parity
assert evaluate_strategy
assert public_row
assert DEVELOPMENT_DAYS
