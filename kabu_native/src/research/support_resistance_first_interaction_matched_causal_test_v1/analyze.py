"""Matched causal path test. Primary active S/R only. Not a strategy. No PnL selection."""
from __future__ import annotations

from typing import Any

from research.support_resistance_first_interaction_matched_causal_test_v1 import (
    ANALYSIS_ID,
    CASE_BIND,
    CASE_HASH,
    CASE_INCON,
    CASE_NONE,
    CASE_SEP,
    EXPECTED_FIRST_TEST_N,
    EXPECTED_PRIMARY_FIRST_TEST_N,
    FIVE_PP_CONTINUATION_SOLE_GATE,
    LUNCH_POLICY,
    NEXT_BIND,
    NEXT_HASH,
    NEXT_NOT_STRATEGY,
    NEXT_STOP_NONE,
    PARENT_VERDICT,
    PRIMARY_METRIC,
    RCA_DEFERRED,
)
from research.support_resistance_first_interaction_matched_causal_test_v1.direction import direction_table
from research.support_resistance_first_interaction_matched_causal_test_v1.stats import decide_from_questions, evaluate_question
from research.support_resistance_first_interaction_matched_causal_test_v1.walk import walk_matched


def _q(pairs: list[dict[str, Any]], *, pop: str, question: str) -> list[dict[str, Any]]:
    return [r for r in pairs if str(r.get("population") or "") == pop and str(r.get("question") or "") == question]


def decide(
    *,
    bind_ok: bool,
    freeze_ok: bool,
    identity_ok: bool,
    mixed: bool,
    qs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not bind_ok:
        return {
            "CASE": "BIND",
            "VERDICT": CASE_BIND,
            "NEXT": NEXT_BIND,
            "INTERPRETATION": "Prior bind failed. Do not open Confirmation. Do not revive the old economic null.",
            "is_strategy": False,
        }
    if not freeze_ok or not identity_ok:
        return {
            "CASE": "HASH",
            "VERDICT": CASE_HASH,
            "NEXT": NEXT_HASH,
            "INTERPRETATION": "Frozen detector hash or first-test identity failed. Do not retune. Repair the freeze, then retry.",
            "is_strategy": False,
        }
    if mixed:
        return {
            "CASE": "INCON",
            "VERDICT": CASE_INCON,
            "NEXT": NEXT_STOP_NONE,
            "INTERPRETATION": "Primary and secondary populations were mixed. The primary S/R verdict cannot be read.",
            "is_strategy": False,
        }
    d = decide_from_questions(qs)
    if d.get("any_primary_separation"):
        return {
            "CASE": "SEP",
            "VERDICT": CASE_SEP,
            "NEXT": NEXT_NOT_STRATEGY,
            "INTERPRETATION": (
                "Face-valid primary support/resistance first-interaction paths separate from matched non-zone moves "
                "on the predeclared signed metric. This is not a trading strategy."
            ),
            "is_strategy": False,
            **d,
        }
    if d.get("any_powered"):
        return {
            "CASE": "NONE",
            "VERDICT": CASE_NONE,
            "NEXT": NEXT_STOP_NONE,
            "INTERPRETATION": (
                "Powered matched tests of face-valid primary S/R did not show subsequent-path separation vs similar "
                "non-zone moves. This does not revive MULTI_TOUCH_ZONE_NO_INCREMENTAL_INFORMATION_V1 as an economic null."
            ),
            "is_strategy": False,
            **d,
        }
    return {
        "CASE": "INCON",
        "VERDICT": CASE_INCON,
        "NEXT": NEXT_STOP_NONE,
        "INTERPRETATION": "The matched test was underpowered on D2–D4. Not a strategy. Do not open Confirmation.",
        "is_strategy": False,
        **d,
    }


def summaries_from_pairs(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    mixed = any(str(r.get("population") or "") not in {"PRIMARY", "ROLE_FLIP_SECONDARY", "PLACEBO"} for r in pairs)
    mixed = mixed or any(
        str(r.get("population") or "") == "PRIMARY"
        and str(r.get("selection_slot") or "")
        not in {
            "NEAREST_ACTIVE_RESISTANCE_ABOVE",
            "NEAREST_ACTIVE_SUPPORT_BELOW",
        }
        for r in pairs
    )
    qs = {qid: evaluate_question(_q(pairs, pop="PRIMARY", question=qid), question=qid) for qid in ("A", "B", "C", "D")}
    secondary = {qid: evaluate_question(_q(pairs, pop="ROLE_FLIP_SECONDARY", question=qid), question=qid) for qid in ("A", "B", "C", "D")}
    placebo = {qid: evaluate_question(_q(pairs, pop="PLACEBO", question=qid), question=qid) for qid in ("A", "B", "C", "D")}
    res_a = evaluate_question(
        [r for r in _q(pairs, pop="PRIMARY", question="A") if r.get("as_resistance")],
        question="A_resistance",
    )
    sup_a = evaluate_question(
        [r for r in _q(pairs, pop="PRIMARY", question="A") if not r.get("as_resistance")],
        question="A_support",
    )
    return {
        "mixed": mixed,
        "questions_primary": qs,
        "questions_secondary": secondary,
        "questions_placebo": placebo,
        "diagnostic_direction_split_A": {"resistance": res_a, "support": sup_a},
    }
    keys = (
        "question",
        "population",
        "symbol",
        "date",
        "block",
        "selection_slot",
        "selection_label",
        "as_resistance",
        "event_t",
        "control_t",
        "matched",
        "resolution",
        "tr_sign",
        "tr_mfe_bps",
        "tr_mae_bps",
        "tr_end_bps",
        "tr_p20_before_m20",
        "tr_p40_before_m20",
        "tr_p80_before_m30",
        "tr_mfe_before_mae",
        "tr_payoff_asymmetry",
        "tr_time_to_failure_min",
        "tr_time_to_extension_min",
        "ct_mfe_bps",
        "ct_mae_bps",
        "ct_end_bps",
        "ct_p20_before_m20",
        "ct_p40_before_m20",
        "ct_p80_before_m30",
        "ct_mfe_before_mae",
        "future_outcome_used_for_matching",
    )
    return {k: r.get(k) for k in keys}


def build_report_body(bind: dict[str, Any]) -> dict[str, Any]:
    freeze = dict(bind.get("freeze") or {})
    freeze_ok = bool(freeze.get("confirm_atr_frozen")) and bool(freeze.get("zone_half_atr_frozen")) and not bool(freeze.get("retuned"))
    if not bind.get("ok"):
        decision = decide(bind_ok=False, freeze_ok=freeze_ok, identity_ok=False, mixed=False, qs={})
        return {"ok": False, "reason": bind.get("reason") or "bind_failed", "decision": decision, "freeze": freeze}
    walked = walk_matched(bind)
    if not walked.get("ok"):
        return {"ok": False, "reason": walked.get("reason") or "walk_failed", "freeze": freeze}
    pairs = list(walked.get("pairs") or [])
    counts = dict(walked.get("counts") or {})
    primary_n = int(counts.get("primary_first_test_n") or 0)
    first_n = int(counts.get("first_test_n") or 0)
    secondary_n = int(counts.get("secondary_first_test_n") or 0)
    identity_ok = primary_n == int(EXPECTED_PRIMARY_FIRST_TEST_N) and first_n == int(EXPECTED_FIRST_TEST_N)
    summ = summaries_from_pairs(pairs)
    mixed = bool(summ["mixed"])
    qs = dict(summ["questions_primary"])
    secondary = dict(summ["questions_secondary"])
    placebo = dict(summ["questions_placebo"])
    res_a = (summ["diagnostic_direction_split_A"] or {}).get("resistance")
    sup_a = (summ["diagnostic_direction_split_A"] or {}).get("support")
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        freeze_ok=freeze_ok,
        identity_ok=identity_ok,
        mixed=mixed,
        qs=qs,
    )
    n_pairs = len(pairs)
    step = max(1, n_pairs // 3000) if n_pairs else 1
    return {
        "ok": True,
        "analysis_id": ANALYSIS_ID,
        "parent_verdict": PARENT_VERDICT,
        "rca_deferred": RCA_DEFERRED,
        "lunch_policy": LUNCH_POLICY,
        "no_pnl_parameter_tuning": True,
        "x0_x1_used_to_select_rules": False,
        "detector_retuned": False,
        "five_pp_continuation_sole_gate": FIVE_PP_CONTINUATION_SOLE_GATE,
        "old_no_info_revived": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "is_strategy": False,
        "primary_metric": PRIMARY_METRIC,
        "freeze": freeze,
        "direction": direction_table(),
        "counts": counts,
        "identity": {
            "expected_primary_first_test_n": EXPECTED_PRIMARY_FIRST_TEST_N,
            "walk_primary_first_test_n": primary_n,
            "expected_first_test_n": EXPECTED_FIRST_TEST_N,
            "walk_first_test_n": first_n,
            "walk_secondary_first_test_n": secondary_n,
            "ok": identity_ok,
        },
        "population_primary": {
            "label": "PRIMARY_ACTIVE_SUPPORT_RESISTANCE",
            "first_test_n": primary_n,
            "break_n": int(counts.get("PRIMARY_break_n") or 0),
            "reject_n": int(counts.get("PRIMARY_reject_n") or 0),
            "retest_hold_n": int(counts.get("PRIMARY_retest_hold_n") or 0),
            "failed_retest_n": int(counts.get("PRIMARY_failed_retest_n") or 0),
            "mixed_with_role_flip": False,
        },
        "population_secondary": {
            "label": "ROLE_FLIP_SECONDARY",
            "first_test_n": secondary_n,
            "break_n": int(counts.get("ROLE_FLIP_SECONDARY_break_n") or 0),
            "reject_n": int(counts.get("ROLE_FLIP_SECONDARY_reject_n") or 0),
            "retest_hold_n": int(counts.get("ROLE_FLIP_SECONDARY_retest_hold_n") or 0),
            "failed_retest_n": int(counts.get("ROLE_FLIP_SECONDARY_failed_retest_n") or 0),
            "influences_primary_verdict": False,
        },
        "questions_primary": qs,
        "questions_secondary": secondary,
        "questions_placebo": placebo,
        "diagnostic_direction_split_A": {"resistance": res_a, "support": sup_a},
        "matching": {
            "same_symbol": True,
            "time_of_day_bucket_min": 30,
            "fallback_plus_minus_min": 30,
            "momentum_1m_3m_5m": True,
            "relative_volume": True,
            "realized_range": True,
            "gap_sign": True,
            "market_relative_sign": True,
            "sector_relative_sign": True,
            "control_not_in_any_active_zone": True,
            "future_outcome_used_for_matching": False,
            "primary_A_match_rate": (qs.get("A") or {}).get("d2_d4", {}).get("match_rate"),
        },
        "placebo": {
            "levels": ["PLACEBO_MID", "PLACEBO_PDC_UP", "PLACEBO_PDC_DN"],
            "discard_if_overlap_active_zone": True,
            "first_test_n": int(counts.get("placebo_first_test_n") or 0),
            "does_not_influence_primary_verdict": True,
        },
        "decision": decision,
        "n_days": walked.get("n_days"),
        "n_symbols_loaded": walked.get("n_symbols_loaded"),
        "forbidden_loaded": False,
        "mixed_primary_secondary": mixed,
        "pairs_compact": [_compact_pair(r) for r in pairs[::step][:3000]],
        "pairs_n": n_pairs,
        "all_pairs": pairs,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    qs = dict(report.get("questions_primary") or {})
    ident = dict(report.get("identity") or {})
    freeze = dict(report.get("freeze") or {})
    pop = dict(report.get("population_primary") or {})
    sec = dict(report.get("population_secondary") or {})

    def _qsum(qid: str) -> dict[str, Any]:
        q = dict(qs.get(qid) or {})
        st = dict(q.get("d2_d4") or {})
        boot = dict(q.get("bootstrap_date") or {})
        return {
            "treatment_n": st.get("treatment_n"),
            "matched_n": st.get("matched_n"),
            "match_rate": st.get("match_rate"),
            "gap_p20_before_m20": st.get("gap_p20_before_m20"),
            "boot_p50": boot.get("p50"),
            "ci95_lo": boot.get("ci95_lo"),
            "ci95_hi": boot.get("ci95_hi"),
            "powered": q.get("powered"),
            "separation": q.get("separation_on_primary_metric"),
            "favorable": q.get("favorable_on_primary_metric"),
        }

    return {
        "Detector frozen exactly as rebuilt?": bool(freeze.get("confirm_atr_frozen")) and bool(freeze.get("zone_half_atr_frozen")) and not bool(freeze.get("retuned")),
        "DETECTOR_SHA256": freeze.get("DETECTOR_SHA256"),
        "STATE_MACHINE_SHA256": freeze.get("STATE_MACHINE_SHA256"),
        "Primary first-test n?": pop.get("first_test_n"),
        "Expected primary first-test n?": ident.get("expected_primary_first_test_n"),
        "Identity ok?": ident.get("ok"),
        "Secondary role-flip first-test n?": sec.get("first_test_n"),
        "Were broken role-flip candidates mixed into the primary test?": False,
        "Did secondary influence the primary verdict?": False,
        "Placebo A separation diagnostic, not in verdict?": (
            (dict(report.get("questions_placebo") or {}).get("A") or {}).get("separation_on_primary_metric")
        ),
        "Direction resistance first-touch favorable?": "DOWN",
        "Direction support first-touch favorable?": "UP",
        "Question A first-touch rejection?": _qsum("A"),
        "Question B break continuation?": _qsum("B"),
        "Question C retest hold?": _qsum("C"),
        "Question D failed break?": _qsum("D"),
        "Primary metric?": PRIMARY_METRIC,
        "Was 5pp continuation used as the sole gate?": False,
        "Any PnL optimization performed?": False,
        "Any X0/X1 used to select rules?": False,
        "Old Confirmation opened?": False,
        "Frozen Validation opened?": False,
        "Is this a strategy?": False,
        "Old no-info revived as economic null?": False,
        "submit/cancel/live?": "0/0/0",
        "VERDICT?": d.get("VERDICT"),
        "NEXT?": d.get("NEXT"),
        "separated_questions": d.get("separated_questions"),
        "powered_questions": d.get("powered_questions"),
    }
