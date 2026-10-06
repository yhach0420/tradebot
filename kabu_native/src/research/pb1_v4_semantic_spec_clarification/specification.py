"""Clarified V4 semantic specification. No machine. No numeric gates. Does not overwrite READY_V1."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_semantic_spec_clarification import (
    E1_CAN_CREATE_TRADE_WITHOUT_THESIS_READY,
    E1_CAN_INVENT_SUPPORT_RESISTANCE,
    FAIL_EXTEND_MAX_BARS_IN_SPEC,
    LAST_BREAK_IN_SPEC,
    MACHINE_IMPLEMENTED,
    NORMAL_OPENING_FIRST_BAR_ONLY,
    NUMERIC_THRESHOLD_FROZEN,
    ONE_M_CAN_CREATE_DIRECTION,
    ONE_M_CAN_CREATE_LOCATION_IDENTITY,
    ONE_M_CAN_CREATE_OPENING_DRIVE,
    ONE_M_CAN_CREATE_STOCK_SELECTION,
    ONE_M_MAY_OBSERVE_PREIDENTIFIED_LEVEL,
    OPENING_DRIVE_IS_PERMANENT_0915_LABEL,
    OPENING_TAXONOMY,
    PATH_EFFICIENCY_THRESHOLD_FROZEN,
    PRIMARY_SETUP_TIMEFRAME,
    S4_NUMERIC_SCALE_IN_SPEC,
    S4_REQUIRES_HUGE_BREAKOUT_BAR,
    SAME_CLOCK_OPENING_BASELINES_REQUIRED,
    TIMEFRAME_INTERPRETATION,
    TIMEFRAME_INTERPRETATION_NAME,
    V4_IS_1M_STRATEGY,
    VALID_ACTIVE_DRIVES,
    VALID_DRIVE_SEEDS,
)

PARENT_SPEC_PRESERVED = "PB1_V4_SEMANTIC_SPEC_READY_V1"

ROLE_MAP = {
    "DAILY": "WHERE / CONTEXT — location, extension, nearby S/R, market geometry. Never stock-selection or direction gate.",
    "5M": "THESIS / DIRECTION / STRUCTURE — stock-today, opening drive, structural level identity, thesis validity live here.",
    "1M": "CAUSAL EXECUTION OBSERVATION AT A PRE-IDENTIFIED 5M LEVEL — may time touch/hold/reject/reacceleration; may never create thesis.",
    "OR15": "SESSION REFERENCE — break/retest/failed-break/thesis-loss frame. Not proof of drive. Not automatic S/R.",
}

INVARIANTS = (
    "V4 is INTRADAY 5M CONTINUATION, not 1-minute scalping.",
    "Interpretation B is frozen: 5M_THESIS_PLUS_CAUSAL_INTRABAR_EXECUTION.",
    "A 5m strategy means the trade thesis already exists from daily + 5m structure, not that every interaction waits for a completed 5m candle.",
    "1m may never establish stock selection, opening drive, trade direction, or structural level identity.",
    "1m may observe touch / hold / rejection / reacceleration only at a pre-identified 5m level.",
    "That boundary does not turn PB1 into a 1m strategy.",
    "THESIS_READY and EXECUTION_READY are separate. SETUP_ELIGIBLE must not mix them.",
    "No execution adapter may convert THESIS_INVALID into THESIS_READY.",
    "Opening drive is a state (SEED then ACTIVE), not a permanent 09:15 label.",
    "A TRUE seed at 09:15 does not automatically mean a valid PB1 event at 09:35 or 10:00.",
    "Continued directional intent is an explicit required concept and a STATE, not a frozen path-efficiency number.",
    "One large bar plus subsequent crawl is not by itself a TRUE directional auction.",
    "Opening taxonomy is mutually exclusive by auction path. Do not check TRUE first and permanently prevent FAILED_OPEN.",
    "FAILED_OPEN remains eligible until opposite drive is established or the opening auction resolves without a drive. Not within 6 bars.",
    "A wide doji/range rejection may seed FAILED_OPEN if it is a visible failed attempt on 5m.",
    "LOCATION_IDENTIFIED is a 5m/HTF thesis concept. LOCATION_INTERACTION is a later event.",
    "OR touch alone is not a meaningful location. 1m cannot create LOCATION_IDENTIFIED.",
    "An unsuccessful first observation does not automatically kill an otherwise-live level.",
    "Thesis invalidation and execution cancellation are separate.",
    "No 09:30 / 09:45 / N-minute / N-bar / FAIL_EXTEND_MAX_BARS=6 / LAST_BREAK=10:00 as semantic definition.",
    "S4 does not require a huge completed 5m breakout bar. S4 numeric scale constants are not part of this spec.",
    "Same-clock prior-only opening 5m baselines are required. First-bar-only normalization of all three bars is forbidden.",
    "ATR20 is a scale sanity reference, not a profit-tuned drive threshold.",
    "Interpretation A (STRICT_COMPLETED_5M_EVERYTHING) is rejected. Interpretation C (IN_PROGRESS_5M) is rejected.",
    "Hidden-1m test: if hiding 1m removes knowledge of stock, direction, level, or thesis, 1m is illegally creating the strategy.",
)


def timeframe_contract() -> dict[str, Any]:
    return {
        "horizon": "INTRADAY_5M_CONTINUATION",
        "v4_is_1m_strategy": V4_IS_1M_STRATEGY,
        "primary_setup_timeframe": PRIMARY_SETUP_TIMEFRAME,
        "adopted_interpretation": TIMEFRAME_INTERPRETATION,
        "adopted_interpretation_name": TIMEFRAME_INTERPRETATION_NAME,
        "rejected": {
            "A": {
                "name": "STRICT_COMPLETED_5M_EVERYTHING",
                "why_rejected": (
                    "Not supported by frozen semantic intent. Would require every leave/retest/hold to wait for a completed 5m close, "
                    "and incorrectly kills sequences such as 3382 before later valid 5m evidence appears."
                ),
            },
            "C": {
                "name": "IN_PROGRESS_5M",
                "why_rejected": "Changes the completed-bar timestamp contract used everywhere else in native research.",
            },
        },
        "five_m_strategy_means": "the trade thesis must already exist from daily + 5m structure",
        "five_m_strategy_does_not_mean": "every interaction must wait for a completed 5m candle",
        "one_m_may_never_establish": {
            "stock_selection": ONE_M_CAN_CREATE_STOCK_SELECTION,
            "opening_drive": ONE_M_CAN_CREATE_OPENING_DRIVE,
            "trade_direction": ONE_M_CAN_CREATE_DIRECTION,
            "structural_level_identity": ONE_M_CAN_CREATE_LOCATION_IDENTITY,
        },
        "one_m_may_observe_at_preidentified_5m_level": {
            "touched": True,
            "held": True,
            "rejected": True,
            "reaccelerated_from": True,
            "this_is": "execution timing, not strategy creation",
            "allowed": ONE_M_MAY_OBSERVE_PREIDENTIFIED_LEVEL,
        },
        "hidden_1m_test": {
            "ask_before_consulting_1m": (
                "If I hide the 1m chart, do I already know the stock, direction, structural level, and continuation thesis?"
            ),
            "if_no": "1m is illegally creating the strategy",
            "if_yes": "1m may time execution",
        },
    }


def thesis_ready_def() -> dict[str, Any]:
    return {
        "state": "THESIS_READY",
        "determinable_without_1m_execution_information": True,
        "required": [
            "WHY_THIS_STOCK_TODAY",
            "OPENING_DRIVE_ACTIVE",
            "LOCATION_IDENTIFIED",
            "opening thesis still alive",
        ],
        "trader_already_knows": ["which stock", "which direction", "which structural level", "what continuation thesis"],
        "exact_entry_moment_known": False,
        "must_not_be_named": "SETUP_ELIGIBLE",
        "note": "SETUP_ELIGIBLE mixed thesis and execution. The machine must no longer use one state for both.",
    }


def execution_ready_def() -> dict[str, Any]:
    return {
        "state": "EXECUTION_READY",
        "only_after": "THESIS_READY",
        "role": "causal observations determine actual entry timing",
        "adapters_may_differ": True,
        "forbidden": "any adapter converting THESIS_INVALID into THESIS_READY",
        "e1_can_create_trade_without_thesis_ready": E1_CAN_CREATE_TRADE_WITHOUT_THESIS_READY,
    }


def why_this_stock_today() -> dict[str, Any]:
    return {
        "semantic_requirement": "DISTINCTIVE_OPENING_ACTIVITY relative to the stock's own normal behavior",
        "trader_sentence": "this stock is actually moving today",
        "evidence_that_would_count": [
            "gap distinctive versus own ATR",
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
        "one_m_cannot_create_this": True,
    }


def opening_drive_seed_def() -> dict[str, Any]:
    return {
        "state": "OPENING_DRIVE_SEED",
        "when": "09:15, from completed first-three 5m bars (09:00-09:04, 09:05-09:09, 09:10-09:14)",
        "possible": list(VALID_DRIVE_SEEDS) + ["NO_VALID_DRIVE_SEED"],
        "is_permanent_setup_eligibility": OPENING_DRIVE_IS_PERMANENT_0915_LABEL,
        "is_only_the_initial_state": True,
        "one_m_cannot_create_this": True,
        "chart_test": "On the completed opening 5m chart, is there a directional-auction seed or a visible failed-open seed?",
    }


def true_opening_drive_seed_def() -> dict[str, Any]:
    return {
        "state": "TRUE_OPENING_DRIVE_SEED",
        "timeframe": "5m",
        "required_concepts": [
            "meaningful directional displacement on completed 5m bars",
            "directional 5m bodies, not doji/crawl tape as the whole auction",
            "limited counter-directional auction",
            "visible movement relative to the stock's normal opening activity",
            "continued directional intent (state, not a frozen number)",
        ],
        "not_sufficient_by_itself": "one large bar plus subsequent crawl",
        "must_represent": "a directional auction, not merely numerical displacement",
        "not": [
            "09:14 close in upper/lower OR half",
            "three same-color 1m ticks",
            "tiny net that happens to finish in a directional OR half",
            "max-of-three range vs first-bar-only baseline with later crawl bars",
        ],
        "numeric_threshold_frozen": NUMERIC_THRESHOLD_FROZEN,
        "path_efficiency_threshold_frozen": PATH_EFFICIENCY_THRESHOLD_FROZEN,
        "candidate_descriptor_families_not_frozen": [
            "path efficiency / net vs gross",
            "close-to-close progression",
            "one-bar domination share",
            "same-direction close sequence",
        ],
    }


def continued_directional_intent_def() -> dict[str, Any]:
    return {
        "explicitly_part_of_spec": True,
        "was_in_frozen_READY_V1": True,
        "was_encoded_in_corrected_machine": False,
        "kind": "STATE concept",
        "means": (
            "after the initial directional move, the auction does not collapse into "
            "two-sided balance, flat crawl, repeated failed progress, or full displacement unwind"
        ),
        "numeric_path_efficiency_threshold_frozen": False,
        "why_no_threshold": "RCA TRUE vs MICRO efficiency gap was modest; do not freeze 0.8 vs 0.9 here",
        "addresses_without_micro_specific_rule": [
            "7011/20241205 one-bar then crawl",
            "6920/20250404 dump then doji crawl",
            "TWO_SIDED_OPEN false positives",
            "LATE_RANGE_RESOLUTION false positives from a 09:15 TRUE seed that later dies",
        ],
    }


def opening_drive_active_def() -> dict[str, Any]:
    return {
        "state": "OPENING_DRIVE_ACTIVE",
        "after": "09:15",
        "seed_remains_active_only_while": [
            "directional structure persists",
            "the opening auction has not reset into a two-sided/range state",
        ],
        "required_at_break_retest_research": True,
        "true_seed_does_not_automatically_mean_valid_event_later": True,
        "valid_active_labels": list(VALID_ACTIVE_DRIVES),
        "becomes_false_if": [
            "directional displacement is materially unwound",
            "two-sided balance is re-established",
            "repeated failed progress occurs",
            "the move becomes a stale range-resolution event",
            "the directional auction clearly ends",
        ],
        "clock_cutoffs_forbidden": (
            "09:30",
            "09:45",
            "N-minute expiry",
            "N-bar expiry",
            "FAIL_EXTEND_MAX_BARS=6",
            "LAST_BREAK=10:00",
            "fixed retest age",
        ),
        "state_not_clock": True,
    }


def failed_open_def() -> dict[str, Any]:
    return {
        "seed_state": "FAILED_OPEN_SEED",
        "established_state": "FAILED_OPEN_THEN_REAL_DRIVE",
        "transition": "FAILED_OPEN_SEED → OPPOSITE_DRIVE_ESTABLISHED",
        "seed_may_be": [
            "a visible directional counter-attempt on 5m",
            "OR a wide opening rejection / failed auction that is obvious on 5m",
        ],
        "wide_doji_or_range_rejection_may_be_valid_seed": True,
        "wide_doji_condition": "if it represents a real failed attempt, not a 1m nick",
        "must_not_require_only": "a large counter-body",
        "opposite_drive_must": [
            "be visible on completed 5m structure",
            "establish a new directional auction",
            "remain part of the opening auction",
        ],
        "must_not_be_defined_as": "within 6 bars",
        "FAIL_EXTEND_MAX_BARS_in_semantic_spec": FAIL_EXTEND_MAX_BARS_IN_SPEC,
        "expires_when": "state resolution, not bar count",
        "remains_eligible_until": {
            "A": "OPPOSITE_DRIVE_ESTABLISHED",
            "B": "OPENING_AUCTION_RESOLVED_WITHOUT_DRIVE",
        },
        "resolved_without_drive_includes": [
            "two-sided balance",
            "stale range",
            "failed directional progress",
            "thesis loss",
        ],
        "distinguish_7011_20250523_from_6758_20250613": (
            "7011: opening auction still forming and a real opposite drive establishes. "
            "6758: opening move already resolved into leftover/stale behavior. "
            "Distinguish by state, not a hard bar count."
        ),
        "3382_must_be_representable_without_special_case": True,
        "numeric_threshold_frozen": False,
        "not": [
            "tiny first-5m nick + 09:14 close in the opposite OR half with no failed auction",
            "EARLY_REVERSAL_THEN_DOMINANT_DRIVE inherited from V3.2",
            "micro leak after a failed open that never becomes a dump/drive",
        ],
    }


def taxonomy_precedence() -> dict[str, Any]:
    return {
        "mutually_exclusive_by_auction_path": True,
        "do_not_check_TRUE_first_and_permanently_prevent_FAILED_OPEN": True,
        "labels": list(OPENING_TAXONOMY),
        "verify_on": {"symbol": "4063", "date": "20251118"},
        "no_symbol_override": True,
        "path_rule": (
            "If the opening path is a visible failed attempt then a new opposite directional auction, "
            "the label is FAILED_OPEN_THEN_REAL_DRIVE even if a TRUE numeric displacement would also fire."
        ),
    }


def normal_opening_scale_def() -> dict[str, Any]:
    return {
        "first_bar_only_normalization_of_all_three_bars": NORMAL_OPENING_FIRST_BAR_ONLY,
        "same_clock_opening_baselines_required": SAME_CLOCK_OPENING_BASELINES_REQUIRED,
        "each_completed_5m_bar_interpretable_against": "prior-only same-clock opening baseline",
        "example_clocks": ("09:00-09:04", "09:05-09:09", "09:10-09:14"),
        "robust_aggregation": "implementation detail to be frozen later using semantic correspondence only",
        "no_economic_optimization": True,
        "atr20_role": {
            "may_use_as": "scale sanity reference",
            "must_not_become": "a profit-tuned drive threshold",
            "purpose": "prevent an abnormally tiny local baseline from making ordinary movement look like a large drive",
        },
        "split_max_min_ge_4": "diagnostic only; not a corporate-action fact unless independently documented",
        "must_persist": [
            "same-clock opening 5m baselines",
            "observation counts",
            "ATR20",
            "sequence-level normalized scale",
        ],
    }


def location_identified_def() -> dict[str, Any]:
    return {
        "state": "LOCATION_IDENTIFIED",
        "kind": "5m / higher-timeframe thesis concept",
        "must_be_known_before_execution": True,
        "one_m_cannot_create_this": True,
        "candidate_first": "LOCATION_CANDIDATE",
        "candidate_becomes_relevant_because": "the 5m thesis is approaching, breaking, or defending that pre-known level",
        "actual_retest_hold_is_later": True,
        "allowed_references": (
            "prior causal S/R",
            "cleared zone",
            "PDH / PDL / PDC",
            "recent causal swing",
            "VWAP",
            "daily SMA context",
            "OR boundary after a genuine opening drive",
        ),
        "or_touch_alone_is_not_meaningful": True,
        "or_alone_automatically_valid": False,
        "no_score": "2 levels = valid is forbidden",
        "do_not_kill_thesis_because_first_attempted_interaction_is_not_yet_the_final_valid_hold": True,
        "forms_unchanged_from_parent": {
            "A": "CLEARED prior structural zone retested from the far side (interaction comes later)",
            "B": "OR boundary that has produced a visible acceptance / rejection / hold after a real drive/leave",
            "C": "OR boundary plus another causal level where confluence is genuinely visible, not counted",
        },
    }


def location_interaction_def() -> dict[str, Any]:
    return {
        "state": "LOCATION_INTERACTION",
        "separate_from": "LOCATION_IDENTIFIED",
        "s2_must_not_conflate": "this is a meaningful level vs the level has already been retested and held",
        "first_unsuccessful_observation_does_not_automatically_kill": True,
        "state_remains_alive_until": [
            "a valid first interaction resolves",
            "OR the thesis is causally invalidated",
        ],
        "current_3382_bug_forbidden": (
            "committing on the first 5m close after a 1m retest observation and dying OR_TOUCH_ONLY "
            "even though later completed 5m leave and completed 5m hold exist"
        ),
        "5m_determines": ["direction", "opening drive", "structural level identity", "thesis validity"],
        "1m_may_observe_only_at_already_known_level": ["first touch", "first retest", "hold / rejection", "execution reacceleration"],
        "does_not_turn_pb1_into_1m_strategy": True,
        "retest_extreme_breach": {
            "not_automatically_thesis_dead": True,
            "distinguish": ["temporary penetration", "accepted structural failure"],
            "numeric_acceptance_rule_frozen": False,
        },
    }


def five_m_continuation_def() -> dict[str, Any]:
    return {
        "state": "FIVE_M_CONTINUATION_STATE",
        "means": [
            "defended location still valid",
            "counter-directional pressure weakened",
            "renewed progress visible in the original 5m direction",
        ],
        "semantic_question": "Would the 5m trader still want the continuation trade?",
        "not_the_question": "Did a large 5m candle print?",
        "may_be_visible_through": [
            "directional close progression",
            "failed counter-push",
            "hold + renewed body expansion",
            "reclaim of local 5m structure",
        ],
        "requires_huge_completed_5m_candle": S4_REQUIRES_HUGE_BREAKOUT_BAR,
        "numeric_scale_in_clarified_spec": S4_NUMERIC_SCALE_IN_SPEC,
        "not_frozen_here": ["0.35 × opening5m", "1.5 × N1M", "any other numeric range gate"],
        "those_are": "implementation encodings to be justified later",
    }


def e0_def() -> dict[str, Any]:
    return {
        "id": "E0",
        "name": "5M_CONFIRMED_EXECUTION",
        "from": "THESIS_READY",
        "path": "Wait for completed 5m retest/hold + continuation state, then enter next causal 1m open.",
        "shares_with_e1": ["stock", "direction", "opening-drive state", "location identity", "thesis"],
        "not_a_different_alpha_strategy": True,
        "economic_comparison_later_not_now": True,
    }


def e1_def() -> dict[str, Any]:
    return {
        "id": "E1",
        "name": "1M_TIMED_EXECUTION",
        "from": "THESIS_READY",
        "path": (
            "At the same pre-identified 5m structural level, completed 1m may observe retest / hold / "
            "state-change / reacceleration, then enter next causal 1m open."
        ),
        "may_say": "the already-identified 5m level has just been retested and held",
        "must_not_say": "this 1m low looks like support, therefore trade",
        "can_create_trade_without_thesis_ready": E1_CAN_CREATE_TRADE_WITHOUT_THESIS_READY,
        "can_invent_support_resistance": E1_CAN_INVENT_SUPPORT_RESISTANCE,
        "shares_with_e0": ["stock", "direction", "opening-drive state", "location identity", "thesis"],
        "not_a_different_alpha_strategy": True,
        "economic_comparison_later_not_now": True,
        "descriptor_families_allowed_after_thesis_ready": [
            "3-bar directional net / NORMAL_1M_RANGE",
            "trigger range / NORMAL_1M_RANGE",
            "trigger body / NORMAL_1M_RANGE",
        ],
        "numeric_thresholds_frozen": False,
        "micro_cross": "may be the LAST confirmation step, never the definition of reacceleration",
        "forbidden_scale": ["trigger range >= retest bar range", "trigger range >= prior tiny bar"],
    }


def invalidation_def() -> dict[str, Any]:
    return {
        "thesis_invalidation": [
            "opening drive state lost",
            "meaningful level structurally fails",
            "two-sided balance re-established",
        ],
        "execution_cancellation": "the particular E1 opportunity fails while the broader thesis may remain alive",
        "do_not_collapse_both": True,
        "THESIS_LOST": "absorbing for that PB1 opportunity",
        "clock_cutoffs_forbidden": True,
        "LAST_BREAK_in_semantic_spec": LAST_BREAK_IN_SPEC,
        "session_operational_limits": "may exist later; they are not semantic definition of PB1",
    }


def s1_representation_requirements() -> dict[str, Any]:
    return {
        "future_implementation_must_encode": [
            "meaningful directional displacement",
            "directional 5m bodies",
            "limited counter-auction",
            "movement relative to normal opening behavior",
            "continued directional intent",
        ],
        "corrected_machine_encodes_1_to_4": "partially",
        "corrected_machine_encodes_5": "not at all",
        "main_fix_addresses": "state persistence, not only MICRO magnitude",
        "also_audit": {"TWO_SIDED_OPEN": 9, "LATE_RANGE_RESOLUTION": 7},
        "no_micro_specific_rule": True,
    }


def allowed_transitions() -> list[dict[str, str]]:
    return [
        {"from": "WHY_THIS_STOCK", "to": "OPENING_DRIVE_SEED", "when": "distinctive opening activity versus own normal"},
        {"from": "OPENING_DRIVE_SEED", "to": "OPENING_DRIVE_ACTIVE", "when": "TRUE or FAILED_OPEN seed and directional structure persists"},
        {"from": "FAILED_OPEN_SEED", "to": "OPENING_DRIVE_ACTIVE", "when": "OPPOSITE_DRIVE_ESTABLISHED while still the opening auction"},
        {"from": "OPENING_DRIVE_ACTIVE", "to": "LOCATION_IDENTIFIED", "when": "pre-known meaningful 5m/HTF level is identified without 1m creating it"},
        {"from": "LOCATION_IDENTIFIED", "to": "THESIS_READY", "when": "stock, direction, level, and live opening thesis are known without 1m"},
        {"from": "THESIS_READY", "to": "E0_5M_CONFIRMATION", "when": "choose completed-5m retest/hold + continuation path"},
        {"from": "THESIS_READY", "to": "E1_1M_LEVEL_INTERACTION", "when": "choose 1m timing at the already-identified 5m level"},
        {"from": "E0_5M_CONFIRMATION", "to": "EXECUTION_READY", "when": "5m continuation state visible; enter next causal 1m open"},
        {"from": "E1_1M_LEVEL_INTERACTION", "to": "EXECUTION_READY", "when": "completed 1m observes retest/hold/reacceleration at that 5m level"},
        {"from": "LOCATION_CANDIDATE", "to": "LOCATION_INTERACTION", "when": "thesis approaches/breaks/defends the pre-known level"},
        {"from": "LOCATION_INTERACTION", "to": "LOCATION_INTERACTION", "when": "unsuccessful first observation; thesis still alive; wait for valid interaction"},
        {"from": "E1_1M_LEVEL_INTERACTION", "to": "THESIS_READY", "when": "this E1 attempt cancelled; thesis still alive"},
    ]


def forbidden_transitions() -> list[dict[str, str]]:
    return [
        {"from": "THESIS_INVALID", "to": "THESIS_READY", "when": "FORBIDDEN — no execution adapter may revive thesis"},
        {"from": "NO_VALID_DRIVE_SEED", "to": "EXECUTION_READY", "when": "FORBIDDEN — 1m cannot create a drive"},
        {"from": "OPENING_DRIVE_SEED", "to": "EXECUTION_READY", "when": "FORBIDDEN — skip of ACTIVE + LOCATION + THESIS_READY"},
        {"from": "*", "to": "LOCATION_IDENTIFIED", "when": "FORBIDDEN if the only evidence is 1m"},
        {"from": "*", "to": "TRUE_OPENING_DRIVE_SEED", "when": "FORBIDDEN from 1m ticks or OR-half close"},
        {"from": "TRUE_OPENING_DRIVE_SEED", "to": "THESIS_READY", "when": "FORBIDDEN solely because 09:15 TRUE survived as a permanent label"},
        {"from": "FAILED_OPEN_SEED", "to": "NO_VALID_DRIVE_SEED", "when": "FORBIDDEN solely because 6 extra 5m bars elapsed"},
        {"from": "LOCATION_INTERACTION", "to": "THESIS_LOST", "when": "FORBIDDEN solely because the first observation was OR_TOUCH_ONLY"},
        {"from": "FAILED_OPEN_SEED", "to": "TRUE_OPENING_DRIVE", "when": "FORBIDDEN if TRUE was checked first and blocked the failed-open path"},
        {"from": "IN_PROGRESS_5M", "to": "*", "when": "FORBIDDEN — interpretation C rejected"},
    ]


def state_diagram() -> dict[str, Any]:
    return {
        "nodes": [
            {"id": "S0", "name": "WHY_THIS_STOCK"},
            {"id": "SEED", "name": "OPENING_DRIVE_SEED"},
            {"id": "ACTIVE", "name": "OPENING_DRIVE_ACTIVE"},
            {"id": "LOC", "name": "LOCATION_IDENTIFIED"},
            {"id": "T", "name": "THESIS_READY"},
            {"id": "E0", "name": "E0_5M_CONFIRMATION"},
            {"id": "E1", "name": "E1_1M_LEVEL_INTERACTION"},
            {"id": "X", "name": "EXECUTION_READY"},
            {"id": "L", "name": "THESIS_LOST"},
        ],
        "conceptual": (
            "WHY_THIS_STOCK → OPENING_DRIVE_SEED → OPENING_DRIVE_ACTIVE → LOCATION_IDENTIFIED → THESIS_READY "
            "then branch E0_5M_CONFIRMATION → EXECUTION_READY or E1_1M_LEVEL_INTERACTION → EXECUTION_READY. "
            "THESIS_LOST is absorbing for that PB1 opportunity at every point."
        ),
        "allowed": allowed_transitions(),
        "forbidden": forbidden_transitions(),
        "absorbing_reject": "THESIS_LOST",
        "one_m_cannot_enter_from_lost": True,
        "mermaid": (
            "flowchart TD\n"
            "  S0[WHY_THIS_STOCK] --> SEED[OPENING_DRIVE_SEED]\n"
            "  SEED --> ACTIVE[OPENING_DRIVE_ACTIVE]\n"
            "  ACTIVE --> LOC[LOCATION_IDENTIFIED]\n"
            "  LOC --> T[THESIS_READY]\n"
            "  T --> E0[E0_5M_CONFIRMATION]\n"
            "  T --> E1[E1_1M_LEVEL_INTERACTION]\n"
            "  E0 --> X[EXECUTION_READY]\n"
            "  E1 --> X\n"
            "  S0 -.-> L[THESIS_LOST]\n"
            "  SEED -.-> L\n"
            "  ACTIVE -.-> L\n"
            "  LOC -.-> L\n"
            "  T -.-> L\n"
            "  E0 -.-> L\n"
            "  E1 -.-> L\n"
        ),
    }


def what_changed_vs_parent() -> dict[str, Any]:
    return {
        "parent_preserved": PARENT_SPEC_PRESERVED,
        "overwrite_parent": False,
        "clarifications": [
            "Interpretation B frozen; A and C rejected",
            "5m strategy ≠ wait for every completed 5m candle",
            "THESIS_READY vs EXECUTION_READY; drop mixed SETUP_ELIGIBLE",
            "OPENING_DRIVE_SEED vs OPENING_DRIVE_ACTIVE",
            "continued directional intent explicit as state",
            "same-clock baselines; ATR sanity",
            "FAILED_OPEN seed includes wide rejection; no 6-bar life",
            "taxonomy by auction path, not TRUE-first",
            "LOCATION_IDENTIFIED vs LOCATION_INTERACTION",
            "first unsuccessful interaction is not automatically fatal",
            "E0/E1 are execution variants of the same thesis",
            "S4 numeric scale not in semantic spec",
            "no FAIL_EXTEND / LAST_BREAK / 09:30 / 09:45 in semantic spec",
            "thesis invalidation vs execution cancellation",
        ],
        "not_a_new_strategy": True,
        "not_v4_1": True,
    }


def specification() -> dict[str, Any]:
    return {
        "clarified_spec_id": "PB1_V4_SEMANTIC_SPEC_CLARIFIED_V2",
        "parent_spec_preserved": PARENT_SPEC_PRESERVED,
        "overwrite_parent": False,
        "machine_implemented": MACHINE_IMPLEMENTED,
        "horizon": "INTRADAY_5M_CONTINUATION",
        "v4_is_1m_strategy": V4_IS_1M_STRATEGY,
        "primary_setup_timeframe": PRIMARY_SETUP_TIMEFRAME,
        "role_map": dict(ROLE_MAP),
        "invariants": list(INVARIANTS),
        "timeframe_contract": timeframe_contract(),
        "why_this_stock_today": why_this_stock_today(),
        "opening_drive_seed": opening_drive_seed_def(),
        "TRUE_OPENING_DRIVE_SEED": true_opening_drive_seed_def(),
        "continued_directional_intent": continued_directional_intent_def(),
        "opening_drive_active": opening_drive_active_def(),
        "FAILED_OPEN": failed_open_def(),
        "taxonomy_precedence": taxonomy_precedence(),
        "normal_opening_scale": normal_opening_scale_def(),
        "location_identified": location_identified_def(),
        "location_interaction": location_interaction_def(),
        "THESIS_READY": thesis_ready_def(),
        "EXECUTION_READY": execution_ready_def(),
        "E0": e0_def(),
        "E1": e1_def(),
        "five_m_continuation": five_m_continuation_def(),
        "invalidation": invalidation_def(),
        "s1_representation_requirements": s1_representation_requirements(),
        "state_diagram": state_diagram(),
        "what_changed_vs_parent": what_changed_vs_parent(),
        "numeric_threshold_frozen": NUMERIC_THRESHOLD_FROZEN,
        "event_n_not_calculated": True,
        "returns_not_calculated": True,
    }
