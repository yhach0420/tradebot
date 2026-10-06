"""CASE A-E, stress gates, answers 1-41. No threshold search. No new ENTRY family."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.am_c0_reused_history_stress_20260828_20260902_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    EXPECTED_C0_SPEC_SHA256,
    LABEL,
    MAX_RESEARCH_DATE,
    MIN_AUGMENT_TRADE_N,
    PROSPECTIVE_HARVEST_SUSPENDED,
    STRESS_DAYS,
    TRUE_OOS,
)
from research.am_entry_architecture_final_reassessment import C0
from research.am_entry_profit_improvement.metrics import _pf_num
from research.am_entry_research_final_decision import (
    PROSPECTIVE_CHALLENGER_NAME,
    PROSPECTIVE_STATUS,
)
from research.am_exit_research_final_decision import BEST_TESTED_EXIT_FOR_C0

CASE_A = "AM_C0_REUSED_HISTORY_STRESS_SUPPORTED"
CASE_B = "AM_C0_REUSED_HISTORY_STRESS_MIXED"
CASE_C = "AM_C0_REUSED_HISTORY_STRESS_FAILED"
CASE_D = "AM_C0_STRESS_INSUFFICIENT_ACTIONS"
CASE_E = "AM_C0_STRESS_INTEGRITY_FAILED"

NEXT_A = "C0_C14_RESEARCH_CANDIDATE_INTEGRATION_ON_EXISTING_DATA. NOT_FUTURE. NOT_TRUE_OOS."
NEXT_B = "C0_STRESS_FAILURE_ATTRIBUTION_ONCE. NO_THRESHOLD_OR_MODEL_TUNING. NO_NEW_ENTRY_FAMILY."
NEXT_C = "NEXT_RUN_NEW_ENTRY_FAMILY_DESIGN. NOT_THIS_RUN. NO_EXISTING_FAMILY_HOP."
NEXT_D = "HOLD_JUDGMENT. DO_NOT_MIGRATE_TO_NEW_ENTRY_FAMILY_ON_ECONOMICS."
NEXT_E = "STOP. DO_NOT_INTERPRET_ECONOMICS."


def _f(v: Any) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def fill_rate(admitted: int, fill_n: int) -> float | None:
    return (float(fill_n) / float(admitted)) if admitted else None


def concentration(aug_trades: list[dict[str, Any]], aug_daily: list[dict[str, Any]], paired: dict[str, Any]) -> dict[str, Any]:
    total = float(sum(_f(t.get("pnl_yen_100")) for t in aug_trades))
    day_pnls = [(str(r.get("date") or ""), _f(r.get("pnl_yen_100"))) for r in aug_daily]
    day_pnls.sort(key=lambda x: x[1], reverse=True)
    top_day = day_pnls[0] if day_pnls else ("", 0.0)
    top_day_share = (top_day[1] / total) if total != 0 else None
    by_sym: Counter[str] = Counter()
    for t in aug_trades:
        by_sym[str(t.get("symbol") or "")] += _f(t.get("pnl_yen_100"))
    top_sym = by_sym.most_common(1)[0] if by_sym else ("", 0.0)
    top_sym_share = (float(top_sym[1]) / total) if total != 0 and top_sym[0] else None
    deltas = [float(v) for v in (paired.get("deltas") or [])]
    ordered = sorted(deltas, reverse=True)
    overlay_delta = float(sum(deltas)) if deltas else 0.0
    ex_top1 = overlay_delta - ordered[0] if ordered else None
    ex_top2 = overlay_delta - float(sum(ordered[:2])) if len(ordered) >= 2 else (0.0 if ordered else None)
    return {
        "TOP_AUGMENT_DAY": top_day[0],
        "TOP_AUGMENT_DAY_PNL": top_day[1],
        "TOP_DAY_CONTRIBUTION": top_day_share,
        "TOP_SYMBOL": top_sym[0],
        "TOP_SYMBOL_PNL": float(top_sym[1]) if top_sym else 0.0,
        "TOP_SYMBOL_CONTRIBUTION": top_sym_share,
        "EX_TOP1_DELTA": ex_top1,
        "EX_TOP2_DELTA": ex_top2,
        "EX_BEST_DAY_PNL_DELTA": paired.get("EX_BEST_DAY_PNL_DELTA"),
        "EX_TOP3_DAYS_PNL_DELTA": paired.get("EX_TOP3_DAYS_PNL_DELTA"),
    }


def economic_gates(current: dict[str, Any], augment: dict[str, Any], overlay: dict[str, Any], paired: dict[str, Any]) -> dict[str, Any]:
    cur_net = _f(current.get("net_pnl_yen_100"))
    cur_pf = _pf_num(current.get("profit_factor"))
    cur_dd = _f(current.get("max_drawdown_yen_100"))
    aug_n = int(augment.get("trade_count") or 0)
    aug_net = _f(augment.get("net_pnl_yen_100"))
    aug_pf = _pf_num(augment.get("profit_factor"))
    ov_net = _f(overlay.get("net_pnl_yen_100"))
    ov_pf = _pf_num(overlay.get("profit_factor"))
    ov_dd = _f(overlay.get("max_drawdown_yen_100"))
    pos = int(paired.get("PAIRED_POS_DAYS") or 0)
    neg = int(paired.get("PAIRED_NEG_DAYS") or 0)
    gates = {
        "G3_AUGMENT_TRADE_N_GE_5": aug_n >= int(MIN_AUGMENT_TRADE_N),
        "G4_AUGMENT_PNL_GT_0": aug_net > 0.0,
        "G5_AUGMENT_PF_GT_1": aug_pf > 1.0,
        "G6_OVERLAY_PNL_GT_CURRENT": ov_net > cur_net,
        "G7_OVERLAY_PF_GE_CURRENT": ov_pf >= cur_pf,
        "G8_OVERLAY_MAXDD_NOT_WORSE": abs(ov_dd) <= abs(cur_dd),
        "G9_PAIRED_POS_GE_NEG": pos >= neg,
    }
    return {
        "gates": gates,
        "CURRENT_NET": cur_net,
        "CURRENT_PF": cur_pf,
        "CURRENT_DD": cur_dd,
        "AUGMENT_TRADE_N": aug_n,
        "AUGMENT_NET": aug_net,
        "AUGMENT_PF": aug_pf,
        "OVERLAY_NET": ov_net,
        "OVERLAY_PF": ov_pf,
        "OVERLAY_DD": ov_dd,
        "DELTA_NET": ov_net - cur_net,
        "DELTA_PF": ov_pf - cur_pf,
        "DELTA_DD": ov_dd - cur_dd,
        "PAIRED_POS_DAYS": pos,
        "PAIRED_NEG_DAYS": neg,
        "PAIRED_ZERO_DAYS": int(paired.get("PAIRED_ZERO_DAYS") or 0),
        "PAIRED_MEDIAN_DAILY_DELTA": paired.get("PAIRED_MEDIAN_DAILY_DELTA"),
    }


def decide(
    *,
    integrity_ok: bool,
    preservation_ok: bool,
    gates: dict[str, Any],
    econ: dict[str, Any],
) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "CASE": "E",
            "VERDICT": CASE_E,
            "NEXT": NEXT_E,
            "C0_RESEARCH_PRIORITY_MAINTAINED": False,
            "NEW_ENTRY_FAMILY_DESIGN_ALLOWED": False,
        }
    if not preservation_ok:
        return {
            "CASE": "E",
            "VERDICT": CASE_E,
            "NEXT": NEXT_E,
            "C0_RESEARCH_PRIORITY_MAINTAINED": False,
            "NEW_ENTRY_FAMILY_DESIGN_ALLOWED": False,
            "STOP_REASON": "CURRENT_PRESERVATION_FAILED",
        }
    aug_n = int(econ.get("AUGMENT_TRADE_N") or 0)
    if aug_n < int(MIN_AUGMENT_TRADE_N):
        return {
            "CASE": "D",
            "VERDICT": CASE_D,
            "NEXT": NEXT_D,
            "C0_RESEARCH_PRIORITY_MAINTAINED": False,
            "NEW_ENTRY_FAMILY_DESIGN_ALLOWED": False,
        }
    g = dict(gates or {})
    case_a = all(bool(g.get(k)) for k in (
        "G3_AUGMENT_TRADE_N_GE_5",
        "G4_AUGMENT_PNL_GT_0",
        "G5_AUGMENT_PF_GT_1",
        "G6_OVERLAY_PNL_GT_CURRENT",
        "G7_OVERLAY_PF_GE_CURRENT",
        "G8_OVERLAY_MAXDD_NOT_WORSE",
        "G9_PAIRED_POS_GE_NEG",
    ))
    if case_a:
        return {
            "CASE": "A",
            "VERDICT": CASE_A,
            "NEXT": NEXT_A,
            "C0_RESEARCH_PRIORITY_MAINTAINED": True,
            "NEW_ENTRY_FAMILY_DESIGN_ALLOWED": False,
        }
    aug_fail = _f(econ.get("AUGMENT_NET")) <= 0.0
    delta = _f(econ.get("DELTA_NET"))
    pf_worse = not bool(g.get("G7_OVERLAY_PF_GE_CURRENT"))
    dd_worse = not bool(g.get("G8_OVERLAY_MAXDD_NOT_WORSE"))
    overlay_fail = delta <= 0.0 and (pf_worse or dd_worse)
    if aug_fail or overlay_fail:
        return {
            "CASE": "C",
            "VERDICT": CASE_C,
            "NEXT": NEXT_C,
            "C0_RESEARCH_PRIORITY_MAINTAINED": False,
            "NEW_ENTRY_FAMILY_DESIGN_ALLOWED": True,
        }
    return {
        "CASE": "B",
        "VERDICT": CASE_B,
        "NEXT": NEXT_B,
        "C0_RESEARCH_PRIORITY_MAINTAINED": True,
        "NEW_ENTRY_FAMILY_DESIGN_ALLOWED": False,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    req = dict(report.get("required") or {})
    pin = dict(report.get("pin") or {})
    cur = dict(report.get("current") or {})
    aug = dict(report.get("augment") or {})
    ov = dict(report.get("overlay") or {})
    paired = dict(report.get("paired") or {})
    conc = dict(report.get("concentration") or {})
    pres = dict(report.get("preservation") or {})
    ig = dict(report.get("integrity_gates") or {})
    eg = dict(report.get("economic_gates") or {})
    decision = dict(report.get("decision") or {})
    return {
        "1_prior_simple_tech_closure": "SIMPLE_TECH_ENTRY_FAMILY_ABSOLUTE_EDGE_EXHAUSTED; CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED=true; Technical EXIT EXHAUSTED=true",
        "2_prior_p1_recovery_exhaustion": "V1R_P1_IMPLEMENTATION_RECOVERY_EXHAUSTED CASE C; canonical P1 extension CLOSED; BYTE_IDENTICAL=false; BEHAVIORAL_EQUIVALENCE=false; do not return to P1 source recovery",
        "3_why_c0_is_next": "Frozen non-Simple-Tech AM overlay C0_B0_PRIMARY_B1_CONFIRM+C14_ORIGINAL is the standing challenger (FROZEN_FOR_FUTURE_OOS_ONLY). 18-day FULL_GATE_PASS=false; this run tests economic direction on unused existing 4-day history before NEW ENTRY FAMILY DESIGN.",
        "4_c0_spec_sha_parity": bool(pin.get("C0_SPEC_SHA_PARITY")),
        "4_c0_spec_sha256": pin.get("C0_SPEC_SHA256") or EXPECTED_C0_SPEC_SHA256,
        "5_c0_source_reproducible": bool(pin.get("C0_SOURCE_REPRODUCIBLE")),
        "6_c14_reproducible": bool(pin.get("C14_REPRODUCIBLE")),
        "7_development_model_frozen": bool(pin.get("DEVELOPMENT_MODEL_FROZEN")),
        "8_stress_refit_n": int(report.get("STRESS_REFIT_N") or 0),
        "9_stress_days": list(STRESS_DAYS),
        "10_TRUE_OOS": TRUE_OOS,
        "11_label": LABEL,
        "12_CURRENT_trades_pnl_pf_dd": {
            "trade_n": cur.get("trade_count"),
            "PnL": cur.get("net_pnl_yen_100"),
            "PF": cur.get("profit_factor"),
            "MaxDD": cur.get("max_drawdown_yen_100"),
        },
        "13_C0_candidate_admitted_fill_trade_n": {
            "candidate_n": aug.get("candidate_n"),
            "admitted_n": aug.get("admitted_n"),
            "fill_n": aug.get("fill_n"),
            "expired_n": aug.get("expired_n"),
            "trade_n": aug.get("trade_n"),
        },
        "14_C0_pnl_pf_dd": {
            "PnL": aug.get("PnL"),
            "PF": aug.get("PF"),
            "MaxDD": aug.get("MaxDD"),
        },
        "15_C0_win_loss": {
            "win": aug.get("win_n"),
            "loss": aug.get("loss_n"),
            "flat": aug.get("flat_n"),
        },
        "16_OVERLAY_pnl_pf_dd": {
            "trade_n": ov.get("trade_n"),
            "PnL": ov.get("PnL"),
            "PF": ov.get("PF"),
            "MaxDD": ov.get("MaxDD"),
        },
        "17_delta_pnl_pf_dd": {
            "DELTA_NET": ov.get("DELTA_NET"),
            "DELTA_PF": ov.get("DELTA_PF"),
            "DELTA_DD": ov.get("DELTA_DD"),
        },
        "18_paired_positive_negative_zero": {
            "positive_n": paired.get("PAIRED_POS_DAYS"),
            "negative_n": paired.get("PAIRED_NEG_DAYS"),
            "zero_n": paired.get("PAIRED_ZERO_DAYS"),
        },
        "19_paired_median": paired.get("PAIRED_MEDIAN_DAILY_DELTA"),
        "20_top_day_concentration": {
            "day": conc.get("TOP_AUGMENT_DAY"),
            "pnl": conc.get("TOP_AUGMENT_DAY_PNL"),
            "contribution": conc.get("TOP_DAY_CONTRIBUTION"),
        },
        "21_top_symbol_concentration": {
            "symbol": conc.get("TOP_SYMBOL"),
            "pnl": conc.get("TOP_SYMBOL_PNL"),
            "contribution": conc.get("TOP_SYMBOL_CONTRIBUTION"),
        },
        "22_EX_TOP1": conc.get("EX_TOP1_DELTA"),
        "23_EX_TOP2": conc.get("EX_TOP2_DELTA"),
        "24_current_preservation_mismatch_counts": {
            "CURRENT_ENTRY_MISMATCH_N": pres.get("CURRENT_ENTRY_MISMATCH_N"),
            "CURRENT_FILL_LOST_N": pres.get("CURRENT_FILL_LOST_N"),
            "CURRENT_EXIT_MISMATCH_N": pres.get("CURRENT_EXIT_MISMATCH_N"),
            "CURRENT_PNL_MISMATCH_N": pres.get("CURRENT_PNL_MISMATCH_N"),
        },
        "25_integrity_gates": ig,
        "26_economic_gates": eg,
        "27_verdict": decision.get("VERDICT") or req.get("VERDICT"),
        "28_c0_priority_maintained": bool(decision.get("C0_RESEARCH_PRIORITY_MAINTAINED")),
        "29_new_entry_family_design_allowed": bool(decision.get("NEW_ENTRY_FAMILY_DESIGN_ALLOWED")),
        "30_sizing_allowed": False,
        "31_ENTRY_Runtime_changed": False,
        "32_EXIT_Runtime_changed": False,
        "33_CAP_changed": False,
        "34_model_changed": False,
        "35_future_data_used": False,
        "36_MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "37_prospective_suspended": PROSPECTIVE_HARVEST_SUSPENDED,
        "38_TRUE_OOS": TRUE_OOS,
        "39_CERTIFIED": CERTIFIED,
        "40_submit_cancel_live": "0/0/0",
        "41_next": decision.get("NEXT") or req.get("NEXT"),
        "ANALYSIS_ID": ANALYSIS_ID,
        "ENTRY": C0,
        "ENTRY_NAME": PROSPECTIVE_CHALLENGER_NAME,
        "ENTRY_STATUS": PROSPECTIVE_STATUS,
        "EXIT": BEST_TESTED_EXIT_FOR_C0,
        "DEVELOPMENT_DAYS_N": len(DEVELOPMENT_DAYS),
        "LABEL": LABEL,
    }
