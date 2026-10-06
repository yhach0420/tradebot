"""Frozen V4 semantic specification. No machine. No numeric gates. No event_n."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_opening_drive_location_reaccel_spec import (
    DAILY_BIAS_IS_GATE,
    DAILY_ROLE,
    EXEMPLAR_FAILED_OPEN,
    INVALID_OPENING_STATES,
    NUMERIC_1M_THRESHOLDS_FROZEN,
    ONE_M_CAN_CREATE_ELIGIBILITY,
    ONE_M_ROLE,
    OR_ALONE_AUTOMATICALLY_VALID,
    OR_ROLE,
    PRIMARY_SETUP_TIMEFRAME,
    TRADINGVALUE_IS_1M_TRIGGER_GATE,
    V4_IS_1M_STRATEGY,
    VALID_OPENING_STATES,
)

SEQUENCE = (
    "WHY_THIS_STOCK_TODAY",
    "FIVE_M_OPENING_DRIVE",
    "MEANINGFUL_PRICE_LOCATION",
    "BREAK_RETEST",
    "FIVE_M_CONTINUATION_STATE",
    "OPTIONAL_1M_EXECUTION_CONFIRMATION",
    "NEXT_EXECUTABLE_ENTRY",
)

ROLE_MAP = {
    "DAILY": "WHERE — location, extension, nearby S/R, market geometry. Contextual only.",
    "5M": "SETUP / DIRECTION / STRUCTURE — thesis lives or dies here.",
    "1M": "EXECUTION TIMING — may confirm a 5m-valid trade; may never create one.",
    "OR15": "SESSION REFERENCE — break/retest/failed-break/thesis-loss frame. Not proof of drive. Not automatic S/R.",
}

INVARIANTS = (
    "V4 is INTRADAY CONTINUATION, not a 1-minute scalp.",
    "If the 5m setup is not valid, 1m information can never make it eligible.",
    "Eligibility must remain valid if the 1m chart is hidden until the final execution stage.",
    "A 1m micro event cannot create a trade that does not already exist on the 5m setup.",
    "OR-half location is not opening drive.",
    "OR touched is not automatically a defended location.",
    "Acceleration is never defined as trigger range vs a tiny retest/prior bar.",
    "No 09:30 / 09:45 / 5-minute age cutoff. Thesis loss is a state.",
    "No daily-bias gate. No 1m TradingValue gate. No additive confluence score.",
    "Numeric 1m thresholds are not frozen from RCA medians.",
    "Do not automatically accept EARLY_REVERSAL from OR-half + tiny first-5m dip.",
)


def true_opening_drive_def() -> dict[str, Any]:
    return {
        "state": "TRUE_OPENING_DRIVE",
        "timeframe": "5m",
        "bars": ("09:00-09:04", "09:05-09:09", "09:10-09:14"),
        "subsequent_5m_allowed": "only while the opening drive is still causally forming; not to rescue a crawl",
        "required_concepts": [
            "meaningful directional displacement on completed 5m bars",
            "directional 5m bodies, not doji/crawl tape",
            "limited counter-directional auction",
            "visible movement relative to that stock's normal opening 5m range",
            "continued directional intent into the break",
        ],
        "not": [
            "09:14 close in upper/lower OR half",
            "three same-color 1m ticks",
            "tiny net that happens to finish in a directional OR half",
        ],
        "numeric_threshold_frozen": False,
        "chart_test": "On the 5m chart alone, a trader already sees an opening auction in one direction.",
    }


def failed_open_then_real_drive_def() -> dict[str, Any]:
    return {
        "state": "FAILED_OPEN_THEN_REAL_DRIVE",
        "exemplar": {"symbol": EXEMPLAR_FAILED_OPEN[0], "date": EXEMPLAR_FAILED_OPEN[1], "direction": EXEMPLAR_FAILED_OPEN[2]},
        "exemplar_is_numeric_template": False,
        "required_concepts": [
            "a REAL initial counter-move that is itself visible on 5m (large counter-body, not a 1m dip)",
            "then a REAL opposite directional 5m auction with directional bodies",
            "the reversal establishes a new directional auction, not an OR-half close",
        ],
        "not": [
            "tiny first-5m dip + 09:14 close in the opposite OR half",
            "EARLY_REVERSAL_THEN_DOMINANT_DRIVE inherited from V3.2",
            "micro leak after a failed open that never becomes a dump/drive",
        ],
        "3382_must_match": (
            "large visible opening counter-attempt → reversal → real 5m dump → "
            "OR_LOW / location hold → continuation. 1m only times the already-valid 5m trade."
        ),
        "tiny_or_half_must_reject_at": "FIVE_M_OPENING_DRIVE, before 1m",
        "numeric_threshold_frozen": False,
    }


def why_this_stock_today() -> dict[str, Any]:
    return {
        "semantic_requirement": "DISTINCTIVE_OPENING_ACTIVITY relative to the stock's own normal behavior",
        "trader_sentence": "this stock is actually moving today",
        "evidence_that_would_count": [
            "gap that is distinctive versus own ATR",
            "opening 5m range expanded versus prior same-clock 5m range baseline",
            "opening TradingValue elevated versus prior same-clock baseline",
            "cross-sectional activity that accompanies a real 5m move, never sufficient alone",
        ],
        "not": [
            "retune of frozen V2/V3 IN-PLAY constants in this spec",
            "additive score of gap + TV + xs",
            "xs rank alone",
            "own-clock TV elevated on a visually ordinary 5m crawl",
        ],
        "old_in_play_too_broad": True,
        "constants_not_retuned_here": True,
        "no_additive_score": True,
    }


def location_def() -> dict[str, Any]:
    return {
        "requirement": "the retest occurs at a level that is visibly defensible",
        "valid_forms": {
            "A": "CLEARED prior structural zone retested from the far side",
            "B": "OR boundary that has produced a visible acceptance / rejection / hold after a real drive/leave",
            "C": "OR boundary plus another causal level where confluence is genuinely visible, not counted",
        },
        "or_alone_automatically_valid": OR_ALONE_AUTOMATICALLY_VALID,
        "persist_for_every_candidate": [
            "what level is being defended",
            "why it was known beforehand",
            "what reaction occurred there",
            "whether price actually rejected / held it",
        ],
        "allowed_causal_references": (
            "frozen S/R zone",
            "PDH / PDL",
            "PDC",
            "recent causal swing",
            "VWAP",
            "daily SMA25 / SMA75",
            "OR boundary",
        ),
        "no_score": "2 levels = valid is forbidden",
        "or_touched_is_not_valid": True,
    }


def thesis_lost_def() -> dict[str, Any]:
    return {
        "clock_cutoffs_forbidden": ("5-minute age rule", "09:30", "09:45"),
        "state_not_clock": True,
        "lost_when": [
            "opening directional displacement has substantially unwound",
            "and/or multiple failed breaks occur",
            "and/or price repeatedly recrosses OR",
            "and/or a two-sided range is re-established without directional expansion",
        ],
    }


def five_m_continuation_def() -> dict[str, Any]:
    return {
        "required_before_1m": True,
        "must_show": [
            "hold of the defended location",
            "loss of counter-directional pressure",
            "renewed directional progress that already makes sense on the 5m chart",
        ],
        "not_required": "a full completed large 5m breakout bar if that would make entry too late",
        "hidden_1m_test": "If I only had daily + 5m, would I already want this trade? If no, 1m cannot rescue it.",
    }


def one_m_role_def() -> dict[str, Any]:
    return {
        "role": ONE_M_ROLE,
        "consulted_only_after": "FIVE_M_CONTINUATION_STATE",
        "may_answer": "Is this a reasonable moment to execute a trade already justified by 5m structure?",
        "may_not_answer": "Should we take this trade at all?",
        "can_create_eligibility_without_5m": ONE_M_CAN_CREATE_ELIGIBILITY,
        "state_change_families_allowed": [
            "3-bar directional net / NORMAL_1M_RANGE",
            "trigger range / NORMAL_1M_RANGE",
            "trigger body / NORMAL_1M_RANGE",
        ],
        "micro_cross": "may be the LAST confirmation step, never the definition of reacceleration",
        "forbidden_scale": [
            "trigger range >= retest bar range",
            "trigger range >= prior tiny bar",
        ],
        "scale_must_use": "symbol-normalized NORMAL_1M_RANGE or another stable prior-only baseline",
        "rca_medians_are_diagnostic_only": {
            "valid_3bar_net_over_n1m_approx": 2.34,
            "weak_3bar_net_over_n1m_approx": 1.40,
            "must_not_become_gate": True,
            "forbidden_examples": ("3bar >= 2.0", "range >= 2.0", "body >= 1.5"),
        },
        "numeric_thresholds_frozen": NUMERIC_1M_THRESHOLDS_FROZEN,
        "tradingvalue_is_1m_gate": TRADINGVALUE_IS_1M_TRIGGER_GATE,
        "tradingvalue_may_remain_in": "WHY_THIS_STOCK_TODAY only",
    }


def or_role_def() -> dict[str, Any]:
    return {
        "role": OR_ROLE,
        "useful_for": ["break reference", "retest reference", "failed-break state", "thesis-loss state"],
        "not": ["proof of drive", "automatic support/resistance", "sole justification of the trade"],
        "or_half_location": "forbidden as opening-drive definition",
    }


def state_diagram() -> dict[str, Any]:
    return {
        "nodes": [
            {"id": "S0", "name": "WHY_THIS_STOCK_TODAY"},
            {"id": "S1", "name": "FIVE_M_OPENING_DRIVE"},
            {"id": "S2", "name": "MEANINGFUL_PRICE_LOCATION"},
            {"id": "S3", "name": "BREAK_RETEST"},
            {"id": "S4", "name": "FIVE_M_CONTINUATION_STATE"},
            {"id": "S5", "name": "OPTIONAL_1M_EXECUTION_CONFIRMATION"},
            {"id": "S6", "name": "NEXT_EXECUTABLE_ENTRY"},
            {"id": "R", "name": "REJECT"},
            {"id": "W", "name": "WAIT_5M_STILL_ALIVE"},
        ],
        "transitions": [
            {"from": "S0", "to": "S1", "when": "distinctive opening activity versus own normal"},
            {"from": "S0", "to": "R", "when": "visually ordinary / not actually moving today"},
            {"from": "S1", "to": "S2", "when": "TRUE_OPENING_DRIVE or FAILED_OPEN_THEN_REAL_DRIVE"},
            {"from": "S1", "to": "R", "when": "TWO_SIDED_OPEN / FLAT_OR_CRAWL / LATE_RANGE_RESOLUTION / MICRO_OR_LEAK"},
            {"from": "S2", "to": "S3", "when": "location form A or B or visible C; reaction actually occurred"},
            {"from": "S2", "to": "R", "when": "OR touched only, or no known defensible level"},
            {"from": "S3", "to": "S4", "when": "first meaningful pullback while 5m thesis still alive"},
            {"from": "S3", "to": "R", "when": "opening thesis lost"},
            {"from": "S4", "to": "S5", "when": "5m already wants the trade: hold, lost counter-pressure, renewed progress"},
            {"from": "S4", "to": "W", "when": "thesis alive but continuation not yet visible on 5m"},
            {"from": "S4", "to": "R", "when": "thesis lost during wait"},
            {"from": "S5", "to": "S6", "when": "1m state-change vs NORMAL_1M_RANGE confirms execution moment"},
            {"from": "S5", "to": "W", "when": "5m still valid, 1m not yet a state change; do not force a micro-cross"},
            {"from": "R", "to": "S5", "when": "FORBIDDEN — 1m cannot revive a rejected 5m setup"},
            {"from": "S1", "to": "S6", "when": "FORBIDDEN — no skip of location/retest/5m continuation"},
        ],
        "absorbing_reject": True,
        "one_m_cannot_enter_from_reject": True,
    }


def candidate_descriptors() -> dict[str, Any]:
    return {
        "why_this_stock_today": [
            "abs_gap / ATR",
            "opening 5m range / prior same-clock opening 5m range",
            "opening TradingValue / prior same-clock TV",
            "cross-sectional activity (never sufficient alone)",
        ],
        "five_m_opening_drive": [
            "5m bar direction / body / range / close location / wicks",
            "net directional displacement / symbol-normal opening 5m range",
            "n same-direction 5m bars",
            "largest counter-directional 5m bar / normal opening 5m range",
            "sequence: same-direction / reversal / two-sided",
        ],
        "location": [
            "named level",
            "why known beforehand",
            "observed rejection/hold",
            "distance to PDH/PDL/VWAP/SMA/prior zone (persist, do not score)",
        ],
        "thesis_lost": [
            "opening displacement unwind",
            "failed-break count",
            "OR recross count",
            "two-sided range re-established without expansion",
        ],
        "one_m_execution_only": [
            "3-bar directional net / NORMAL_1M_RANGE",
            "trigger range / NORMAL_1M_RANGE",
            "trigger body / NORMAL_1M_RANGE",
        ],
        "not_descriptors_for_eligibility": [
            "OR-half close location as drive",
            "trigger range >= retest bar range",
            "1m TradingValue rise",
            "daily SMA alignment as gate",
        ],
        "no_grid_search": True,
        "no_event_n": True,
    }


def micro_cross_false_positives() -> dict[str, Any]:
    return {
        "pattern": "flat OR + tiny retest + tiny micro break",
        "why_trade_does_not_exist_on_5m": (
            "There is no TRUE_OPENING_DRIVE or FAILED_OPEN_THEN_REAL_DRIVE. "
            "The 5m chart is FLAT_OR_CRAWL or MICRO_OR_LEAK. "
            "A 1m cross of a micro high/low is not a continuation restart."
        ),
        "reject_at": "S1 FIVE_M_OPENING_DRIVE, before 1m execution logic",
        "examples": [
            {"symbol": "7011", "date": "20241205", "direction": "bull", "fail": "MICRO_OR_LEAK"},
            {"symbol": "8001", "date": "20250609", "direction": "bull", "fail": "FLAT_OR_CRAWL"},
            {"symbol": "7182", "date": "20251112", "direction": "bull", "fail": "MICRO_OR_LEAK"},
            {"symbol": "9432", "date": "20250110", "direction": "bear", "fail": "TWO_SIDED_OPEN / not a drive"},
        ],
    }


def specification() -> dict[str, Any]:
    return {
        "horizon": "INTRADAY_CONTINUATION",
        "v4_is_1m_strategy": V4_IS_1M_STRATEGY,
        "primary_setup_timeframe": PRIMARY_SETUP_TIMEFRAME,
        "one_m_role": ONE_M_ROLE,
        "daily_role": DAILY_ROLE,
        "or_role": OR_ROLE,
        "sequence": list(SEQUENCE),
        "role_map": dict(ROLE_MAP),
        "invariants": list(INVARIANTS),
        "why_this_stock_today": why_this_stock_today(),
        "valid_opening_states": list(VALID_OPENING_STATES),
        "invalid_opening_states": list(INVALID_OPENING_STATES),
        "TRUE_OPENING_DRIVE": true_opening_drive_def(),
        "FAILED_OPEN_THEN_REAL_DRIVE": failed_open_then_real_drive_def(),
        "or": or_role_def(),
        "location": location_def(),
        "retest": {
            "primary_problem": False,
            "keep": "first meaningful pullback after a real break / drive",
            "focus": "retest occurs while the 5m thesis is still alive",
            "do_not_redesign_aggressively": True,
        },
        "opening_thesis_lost": thesis_lost_def(),
        "five_m_continuation_before_1m": five_m_continuation_def(),
        "one_m_execution": one_m_role_def(),
        "daily_context": {
            "role": DAILY_ROLE,
            "gate": DAILY_BIAS_IS_GATE,
            "use_for": ["understand location", "extension", "nearby resistance/support", "market geometry"],
            "do_not_require": ["daily bullish bias for long", "daily bearish bias for short"],
        },
        "state_diagram": state_diagram(),
        "candidate_descriptors": candidate_descriptors(),
        "micro_cross_false_positives": micro_cross_false_positives(),
        "machine_not_implemented": True,
        "event_n_not_calculated": True,
        "returns_not_calculated": True,
    }
