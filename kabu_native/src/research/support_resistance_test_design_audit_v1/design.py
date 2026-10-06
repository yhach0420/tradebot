"""Frozen first-interaction machine and matched-control designs. No economic selection."""
from __future__ import annotations

from typing import Any


def first_interaction_design() -> dict[str, Any]:
    return {
        "name": "CAUSAL_FIRST_ZONE_INTERACTION_EPISODE_V1",
        "economic_unit": "ONE ZONE INTERACTION EPISODE per symbol × zone × session",
        "not": "every minute that satisfies a state",
        "no_retrospective_best_event": True,
        "no_pnl_parameter_tuning": True,
        "resistance_primary": [
            "BELOW",
            "FIRST_APPROACH_OF_THE_DAY",
            "FIRST_ENTER",
            "FIRST_RESOLUTION: exactly one of REJECT or BREAK",
        ],
        "if_break_then_one_post_break_sequence": [
            "CLEAR_OF_ZONE: at least one close strictly above hi",
            "then exactly one of ACCEPT | RETEST | RETEST_HOLD | FAILED_BREAK",
        ],
        "true_retest_hold": "BREAK → MOVE CLEAR OF ZONE → RETURN TO ZONE → FAIL TO RE-ENTER BELOW lo → MOVE AWAY AGAIN",
        "not_retest_hold": "price remains somewhere around the zone after the break bar",
        "reset_rule_predeclared": {
            "new_session": "always reset per zone at session open",
            "after_reject": "retire until close has left the zone by one full zone width for at least 15 completed minutes, then a new FIRST_APPROACH may start",
            "after_break_resolved": "retire that zone for the remainder of the session; one primary resolution per zone per day",
            "no_reemit": "do not emit the same kind again on a 10-minute refractory while still in the same episode",
        },
        "salient_zone_set_before_the_day": {
            "candidates_not_pnl_selected": [
                "nearest resistance above session open",
                "nearest support below session open",
                "most salient overhead resistance (most distinct days, most compact, most recent, not already broken)",
                "most salient underlying support (same)",
            ],
            "target": "the small set a trader could reasonably care about, not an arbitrary count",
            "pnl_must_not_choose_the_salience_rule": True,
        },
        "questions_kept_separate": {
            "A": "Does first touch cause rejection?",
            "B": "If resistance breaks, does a salient level produce stronger continuation?",
            "C": "After a real breakout, does the old resistance become support?",
            "D": "Can a failed breakout be identified causally?",
        },
        "implemented_economically_in_this_package": False,
        "diagnostic_only": "this audit counts how many raw events the old machine emits per true episode",
    }


def matched_control_design() -> dict[str, Any]:
    return {
        "name": "MATCHED_NON_ZONE_AND_PLACEBO_LEVEL_V1",
        "why_old_pdh_control_is_insufficient": (
            "Multi-touch break vs PDH break does not match time of day, pre-event momentum, "
            "volatility, volume, gap, or sector/market state. PDH is itself a salient level."
        ),
        "treatment": "first causal interaction episode with a salient zone (first approach → first test → first resolution)",
        "matched_non_zone": {
            "same_symbol": True,
            "similar_time_of_day": "same 30-minute clock bucket",
            "similar_pre_event_momentum": "1m/3m/5m return within 20 bps",
            "similar_volatility": "20-bar range relative within 25%",
            "similar_volume": "vol_rel20 within 40%",
            "similar_gap_state": "same sign of open vs prior close",
            "similar_sector_market_state": "same session-return vs market-median bucket when available",
            "must_not_overlap_any_active_machine_zone": True,
        },
        "causal_placebo_levels": [
            "prior-day midpoint (H+L)/2, same 0.15 ATR half-width, discarded if it overlaps a real active zone",
            "prior close + 0.5 ATR, same width, discarded if overlap",
            "a random completed-session price from the lookback window that is not a labeled reaction, same width",
        ],
        "question": "Does interaction with a salient zone change the path relative to an otherwise similar price move?",
        "outcomes_not_5pp_only": [
            "full return distribution",
            "MFE",
            "MAE",
            "MFE-before-MAE",
            "P(+20bps before -20bps)",
            "P(+40bps before -20bps)",
            "P(+80bps before -30bps)",
            "failed-break probability",
            "time to failure",
            "time to extension",
            "payoff asymmetry",
            "day/symbol-aware resampling CIs",
        ],
        "no_fixed_horizon_pnl_optimization": True,
        "no_future_clustering": True,
        "implemented_as_full_gate_in_this_package": False,
        "diagnostic_on_sample_days": True,
    }


def outcome_metric_review(parent: dict[str, Any]) -> dict[str, Any]:
    ans = dict(parent.get("answers") or parent)
    cmp_ = (parent.get("comparisons") or parent.get("path") or {}) if isinstance(parent, dict) else {}
    return {
        "old_primary_gate": "continuation-rate gap >= 5pp versus PDH or nested transition",
        "old_gate_appropriate_as_sole_criterion": False,
        "why": (
            "A 5pp continuation-rate difference on 30-bar path labels can miss rejection, failed-break, "
            "MFE/MAE asymmetry, and first-touch vs break mechanisms. It also treats every minute-event as iid."
        ),
        "required_instead": [
            "full return distribution",
            "MFE",
            "MAE",
            "MFE-before-MAE",
            "probability +20bps before -20bps",
            "probability +40bps before -20bps",
            "probability +80bps before -30bps",
            "failed-break probability",
            "time to failure",
            "time to extension",
            "payoff asymmetry",
            "day/symbol-aware resampling confidence intervals",
        ],
        "separate_questions": {
            "A_first_touch_rejection": "not answered by mixing all ENTER/REJECT minutes",
            "B_break_continuation": "not answered by pooling retest-hold with breaks",
            "C_flip_old_resistance_to_support": "not answered by continuation-rate vs PDH",
            "D_failed_break_identification": "current FAIL_BACK_BELOW is not a cleared retest",
        },
        "parent_multi_touch_vs_pdh_gap_pp": None,
        "parent_retest_hold_vs_break_gap_pp": None,
        "parent_touch_monotonic": ans.get("Touch-count monotonic?") or ans.get("touch_count_monotonic"),
        "parent_verdict": ans.get("VERDICT"),
        "5pp_gate_misaligned": True,
        "notes_from_parent": cmp_,
    }


def minute_limit_limitation() -> dict[str, Any]:
    return {
        "label": "HISTORICAL_MINUTE_BAR_ADVERSE_SELECTION_CLUE",
        "not_final_proof_that_retest_limits_are_bad": True,
        "why": (
            "The previous passive result used 1-minute OHLC. That cannot establish intrabar sequence, "
            "queue position, or true fill probability. Conservative fill-if-traded-through still "
            "cannot reconstruct the board."
        ),
        "parent_fill_rate": None,
        "parent_X0_after_fill": None,
        "actual_passive_execution_requires": "prospective board/tick data",
        "keep_as": "clue only",
    }


def retest_semantics_from_source() -> dict[str, Any]:
    return {
        "retest_and_retest_hold_mutually_exclusive": False,
        "retest_hold_and_failed_retest_mutually_exclusive_as_same_bar_labels": True,
        "retest_hold_and_failed_retest_mutually_exclusive_as_episode_labels": False,
        "why_retest_almost_equals_retest_hold": (
            "step_zone emits RETEST_ZONE whenever waiting_retest or flipped and the bar overlaps the zone "
            "with close >= lo. On the same bar, if close > lo it also emits RETEST_HOLD and clears waiting_retest. "
            "close == lo is the only same-bar gap, so counts differ by ~1. After a hold, flipped can remain True, "
            "so the pair re-fires every refractory 10 minutes while price lingers in the zone."
        ),
        "why_failed_retest_also_exists": (
            "FAIL_BACK_BELOW emits when waiting_retest or flipped and close < lo. That can happen on a later bar "
            "of the same zone-day after one or more RETEST_HOLD labels, or instead of a hold if the first post-break "
            "close is already below lo."
        ),
        "true_retest_hold_requires_clearance": True,
        "current_machine_requires_clearance": False,
        "waiting_retest_set_on_break_bar": True,
        "lingering_near_zone_counted_as_hold": True,
        "intended_sequence": "BREAK → MOVE CLEAR OF ZONE → RETURN → FAIL TO RE-ENTER BELOW → MOVE AWAY",
        "current_sequence": "BREAK (sets waiting_retest) → any later in-zone bar with close > lo → RETEST + RETEST_HOLD",
    }
