"""Precommitted CASE A-G classification. No threshold search. No overlay policy."""
from __future__ import annotations

from typing import Any, Optional

from research.am_entry_profit_failure_decomposition_v2 import EQUAL_SHARE


def zero_as_safe_supported(*, nonfill_minus_neg: Optional[float], top3_nonfill: Optional[float], current_nonfill: Optional[float]) -> bool:
    if nonfill_minus_neg is None or top3_nonfill is None or current_nonfill is None:
        return False
    return bool(float(nonfill_minus_neg) > 0 and float(top3_nonfill) > float(current_nonfill))


def price_scale_supported(
    *,
    spearman_abs_error: Optional[float],
    high_abs_error_share: Optional[float],
    high_gross_loss_share: Optional[float],
) -> bool:
    if spearman_abs_error is None or high_abs_error_share is None or high_gross_loss_share is None:
        return False
    return bool(
        float(spearman_abs_error) > 0
        and float(high_abs_error_share) > float(EQUAL_SHARE)
        and float(high_gross_loss_share) > float(EQUAL_SHARE)
    )


def decide_case(
    *,
    integrity_ok: bool,
    veto: bool,
    augment: bool,
    internal_improvement: bool,
    oracle_gate_pass: bool,
    oracle_addition: bool,
    zero_safe: bool,
    price_scale: bool,
) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "CASE": "G",
            "VERDICT": "AM_ENTRY_PROFIT_FAILURE_DECOMPOSITION_INTEGRITY_FAILED",
            "NEXT": "STOP",
            "PRIMARY_FAILURE_MECHANISM": "INTEGRITY_FAILURE",
        }
    if veto and augment:
        return {
            "CASE": "C",
            "VERDICT": "AM_UTILITY_OVERLAY_ROLE_SUPPORTED",
            "NEXT": "AM_CURRENT_ENTRY_OVERLAY_ARCHITECTURE_PRECOMMIT",
            "PRIMARY_FAILURE_MECHANISM": "UTILITY_SIGNAL_HAS_VALUE_AS_CURRENT_OVERLAY",
        }
    if veto and internal_improvement:
        return {
            "CASE": "A",
            "VERDICT": "AM_UTILITY_REPLACEMENT_FAILED_VETO_ROLE_SUPPORTED",
            "NEXT": "AM_CURRENT_ENTRY_UTILITY_VETO_ARCHITECTURE_PRECOMMIT",
            "PRIMARY_FAILURE_MECHANISM": "UTILITY_SIGNAL_HAS_VALUE_AS_CURRENT_VETO_NOT_REPLACEMENT",
        }
    if augment and oracle_addition:
        return {
            "CASE": "B",
            "VERDICT": "AM_UTILITY_REPLACEMENT_FAILED_AUGMENT_ROLE_SUPPORTED",
            "NEXT": "AM_CURRENT_ENTRY_UTILITY_AUGMENT_ARCHITECTURE_PRECOMMIT",
            "PRIMARY_FAILURE_MECHANISM": "UTILITY_SIGNAL_HAS_VALUE_AS_SELECTIVE_AUGMENT",
        }
    if not veto and not augment:
        if zero_safe or price_scale:
            return {
                "CASE": "D",
                "VERDICT": "AM_REALIZED_UTILITY_TARGET_ARCHITECTURE_MISALIGNED",
                "NEXT": "AM_UTILITY_TARGET_ARCHITECTURE_REASSESSMENT",
                "PRIMARY_FAILURE_MECHANISM": (
                    "ZERO_AS_SAFE_OUTCOME_AND_PRICE_SCALE_TAIL" if zero_safe and price_scale
                    else "ZERO_AS_SAFE_OUTCOME_SELECTION_COLLAPSE" if zero_safe
                    else "PRICE_SCALE_TAIL_DISTORTION"
                ),
            }
        if not oracle_gate_pass:
            return {
                "CASE": "F",
                "VERDICT": "AM_CURRENT_ENTRY_UNIVERSE_EXECUTION_EXIT_LIMIT",
                "NEXT": "AM_ENTRY_EXIT_EXECUTION_ARCHITECTURE_REASSESSMENT",
                "PRIMARY_FAILURE_MECHANISM": "ORACLE_RANKING_CANNOT_BEAT_CURRENT_ECONOMIC_GATE",
            }
        return {
            "CASE": "E",
            "VERDICT": "AM_REALIZED_PROFIT_OPPORTUNITY_NOT_IDENTIFIED",
            "NEXT": "AM_ENTRY_INFORMATION_EXPANSION_REASSESSMENT",
            "PRIMARY_FAILURE_MECHANISM": "ORACLE_OPPORTUNITY_EXISTS_FROZEN_SCORES_DO_NOT_IDENTIFY_IT",
        }
    if veto and not internal_improvement:
        if zero_safe or price_scale:
            return {
                "CASE": "D",
                "VERDICT": "AM_REALIZED_UTILITY_TARGET_ARCHITECTURE_MISALIGNED",
                "NEXT": "AM_UTILITY_TARGET_ARCHITECTURE_REASSESSMENT",
                "PRIMARY_FAILURE_MECHANISM": "VETO_RANK_SIGNAL_WITHOUT_INTERNAL_ORACLE_IMPROVEMENT_TARGET_DISTORTION",
            }
        if not oracle_gate_pass:
            return {
                "CASE": "F",
                "VERDICT": "AM_CURRENT_ENTRY_UNIVERSE_EXECUTION_EXIT_LIMIT",
                "NEXT": "AM_ENTRY_EXIT_EXECUTION_ARCHITECTURE_REASSESSMENT",
                "PRIMARY_FAILURE_MECHANISM": "VETO_RANK_SIGNAL_WITHOUT_ORACLE_GATE_PASS",
            }
        return {
            "CASE": "E",
            "VERDICT": "AM_REALIZED_PROFIT_OPPORTUNITY_NOT_IDENTIFIED",
            "NEXT": "AM_ENTRY_INFORMATION_EXPANSION_REASSESSMENT",
            "PRIMARY_FAILURE_MECHANISM": "VETO_RANK_SIGNAL_WITHOUT_INTERNAL_ORACLE_IMPROVEMENT",
        }
    if augment and not oracle_addition:
        if not oracle_gate_pass:
            return {
                "CASE": "F",
                "VERDICT": "AM_CURRENT_ENTRY_UNIVERSE_EXECUTION_EXIT_LIMIT",
                "NEXT": "AM_ENTRY_EXIT_EXECUTION_ARCHITECTURE_REASSESSMENT",
                "PRIMARY_FAILURE_MECHANISM": "AUGMENT_RANK_SIGNAL_WITHOUT_ORACLE_ADDITION_OR_GATE",
            }
        return {
            "CASE": "E",
            "VERDICT": "AM_REALIZED_PROFIT_OPPORTUNITY_NOT_IDENTIFIED",
            "NEXT": "AM_ENTRY_INFORMATION_EXPANSION_REASSESSMENT",
            "PRIMARY_FAILURE_MECHANISM": "AUGMENT_RANK_SIGNAL_WITHOUT_ORACLE_ADDITION_OPPORTUNITY",
        }
    return {
        "CASE": "E",
        "VERDICT": "AM_REALIZED_PROFIT_OPPORTUNITY_NOT_IDENTIFIED",
        "NEXT": "AM_ENTRY_INFORMATION_EXPANSION_REASSESSMENT",
        "PRIMARY_FAILURE_MECHANISM": "NO_PRECOMMITTED_OVERLAY_OR_ORACLE_PATH",
    }
