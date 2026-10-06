"""Frozen dataset, information, method, and missing-category requirements. Source/schema only."""
from __future__ import annotations

from typing import Any

from research.research_pause_and_data_requirements_redesign_v1 import (
    BURNED_HOLDOUT_DAYS,
    INTERNAL_FOLD_N,
    LEGACY_DEV_DAYS,
    NEW_DEV_MIN_CALENDAR_WEEKS,
    NEW_DEV_MIN_VALID_DAYS,
    QUARANTINE_DAYS,
    STRESS_DAYS,
    TRUE_OOS_MIN_VALID_DAYS,
)


def dataset_retirement() -> dict[str, Any]:
    return {
        "LEGACY_DEV_DAYS": list(LEGACY_DEV_DAYS),
        "LEGACY_DEV_RESEARCH_BURNED": True,
        "Classification": "LEGACY_DEV",
        "LEGACY_DEV_ALLOWED_FOR_NEW_STRATEGY_SELECTION": False,
        "ALLOWED_USES": [
            "engine canary",
            "implementation parity",
            "causal timestamp validation",
            "regression tests",
            "historical RCA when explicitly required",
        ],
        "FORBIDDEN_USES": [
            "new strategy discovery",
            "new candidate ranking",
            "new parameter selection",
            "new model training",
            "new threshold selection",
            "new architecture selection",
        ],
    }


def burned_data() -> dict[str, Any]:
    return {
        "HOLDOUT_DAYS": list(BURNED_HOLDOUT_DAYS),
        "STRESS_DAYS": list(STRESS_DAYS),
        "QUARANTINE_DAYS": list(QUARANTINE_DAYS),
        "BURNED_DATA_RECLASSIFIED_AS_TRUE_OOS": False,
        "BURNED_DATA_RECLASSIFIED_AS_NEW_DEV": False,
        "QUARANTINE_CHANGED": False,
        "PROSPECTIVE_SEALED": "20260907+",
        "PROSPECTIVE_DATA_CONSUMED": False,
        "PAPER_20260907_READ": False,
        "FUTURE_DATASET_DATES_ASSIGNED": False,
        "THIS_RUN_OPENED_HOLDOUT": False,
        "THIS_RUN_OPENED_STRESS": False,
        "THIS_RUN_OPENED_QUARANTINE": False,
        "THIS_RUN_OPENED_PROSPECTIVE": False,
    }


def current_information_boundary() -> dict[str, Any]:
    return {
        "RAW_OBJECT_N": 13,
        "UNDEREXPLORED_CAUSAL_OBJECT_N": 1,
        "SELECTED_OBJECT_WAS": "FULL_DEPTH_GEOMETRY",
        "SELECTED_OBJECT_JUSTIFIED_DOMAIN": "exhausted",
        "CURRENT_CAPTURE_INFORMATION_DOMAIN_CLOSED": True,
        "INCLUDED_AT_LEAST": [
            "completed price path / OHLCV",
            "MA / EMA",
            "BB",
            "RCI",
            "volume / trading activity",
            "session VWAP",
            "previous highs / prior-session context",
            "cross-sectional market state",
            "top-of-book state",
            "full-depth Buy1..10 / Sell1..10",
            "event/update flow",
            "quote freshness",
            "portfolio occupancy",
            "preopen context",
        ],
        "NEW_CAUSAL_INFORMATION_REQUIRED": True,
        "NEW_INFORMATION_MEANS": "raw causal information not derivable from the current 13-object domain",
        "NEW_INFORMATION_DOES_NOT_MEAN": [
            "another transformation",
            "another ratio",
            "another threshold",
            "another timeframe",
            "another k-level subset",
            "another weighting",
        ],
    }


def current_method_boundary() -> dict[str, Any]:
    return {
        "JUSTIFIED_RESEARCH_METHOD_SPACE_EXHAUSTED": True,
        "CLOSED_UNDER_CURRENT_UNIT": [
            "hand-designed deterministic rules",
            "finite precommitted libraries",
            "sequence/trajectory Full Causal",
            "portfolio-state/admission-aware",
            "joint ENTRY/EXIT Full Causal",
            "execution-policy Full Causal",
        ],
        "ENTRY_ONLY_NOT_AUTO_ELIGIBLE": [
            "supervised model",
            "learned threshold",
            "H5 outcome discovery",
        ],
        "ENTRY_ONLY_METHOD_REVIVAL": False,
    }


def missing_information_categories() -> list[dict[str, Any]]:
    """Local source/schema audit. No capture-day reads. No external fetch."""
    return [
        {
            "CATEGORY_ID": "ORDER_LEVEL_ADD_CANCEL_EXECUTE",
            "CURRENTLY_CAPTURED": False,
            "DERIVABLE_FROM_CURRENT_CAPTURE": False,
            "CAUSAL_TIMESTAMP_POSSIBLE": None,
            "HISTORICAL_REPLAY_POSSIBLE": False,
            "RUNTIME_AVAILABILITY_KNOWN": False,
            "ECONOMIC_INTERPRETATION": "Individual resting-order lifecycle, not snapshot ladder geometry.",
            "MATERIAL_INFORMATION_GAIN": True,
            "NOTES": "kabu board PUSH stores snapshot Buy/Sell levels, not order identity. Not in current Capture original_payload schema used by research.",
        },
        {
            "CATEGORY_ID": "QUEUE_POSITION",
            "CURRENTLY_CAPTURED": False,
            "DERIVABLE_FROM_CURRENT_CAPTURE": False,
            "CAUSAL_TIMESTAMP_POSSIBLE": False,
            "HISTORICAL_REPLAY_POSSIBLE": False,
            "RUNTIME_AVAILABILITY_KNOWN": False,
            "ECONOMIC_INTERPRETATION": "Own queue rank at a price. Prior research marked PASSIVE_QUEUE_FILL_UNOBSERVABLE.",
            "MATERIAL_INFORMATION_GAIN": True,
            "NOTES": "Cannot be reconstructed from displayed qty. Not a Capture field.",
        },
        {
            "CATEGORY_ID": "NATIVE_AGGRESSOR_TRADE_SIDE",
            "CURRENTLY_CAPTURED": False,
            "DERIVABLE_FROM_CURRENT_CAPTURE": True,
            "CAUSAL_TIMESTAMP_POSSIBLE": True,
            "HISTORICAL_REPLAY_POSSIBLE": True,
            "RUNTIME_AVAILABILITY_KNOWN": True,
            "ECONOMIC_INTERPRETATION": "Print vs L1 inferred aggressor. Already TRADE_FLOW_DYNAMICS / IOAR / UEIA.",
            "MATERIAL_INFORMATION_GAIN": False,
            "NOTES": "No native TradeSide on kabu board. Derivation is the closed 13-object domain, not new raw information.",
        },
        {
            "CATEGORY_ID": "EXCHANGE_NATIVE_EXECUTION_TAPE",
            "CURRENTLY_CAPTURED": False,
            "DERIVABLE_FROM_CURRENT_CAPTURE": False,
            "CAUSAL_TIMESTAMP_POSSIBLE": None,
            "HISTORICAL_REPLAY_POSSIBLE": False,
            "RUNTIME_AVAILABILITY_KNOWN": False,
            "ECONOMIC_INTERPRETATION": "Trade-by-trade identity/size/aggressor independent of board snapshots.",
            "MATERIAL_INFORMATION_GAIN": True,
            "NOTES": "Capture is quote-update snapshots plus CurrentPrice/TradingVolume. Snapshot deltas are EVENT_FLOW / TRADE_FLOW, already closed.",
        },
        {
            "CATEGORY_ID": "BROAD_MARKET_FUTURES_CONTEXT",
            "CURRENTLY_CAPTURED": False,
            "DERIVABLE_FROM_CURRENT_CAPTURE": False,
            "CAUSAL_TIMESTAMP_POSSIBLE": None,
            "HISTORICAL_REPLAY_POSSIBLE": False,
            "RUNTIME_AVAILABILITY_KNOWN": False,
            "ECONOMIC_INTERPRETATION": "Index/futures state not represented by the equity-universe cross-section already used (CSB/C4/OR rank).",
            "MATERIAL_INFORMATION_GAIN": True,
            "NOTES": "Would require a new subscription/source. Cross-sectional equity breadth is not this object. Do not acquire in this run.",
        },
        {
            "CATEGORY_ID": "AUCTION_BEYOND_CAPTURED_FIELDS",
            "CURRENTLY_CAPTURED": True,
            "DERIVABLE_FROM_CURRENT_CAPTURE": True,
            "CAUSAL_TIMESTAMP_POSSIBLE": True,
            "HISTORICAL_REPLAY_POSSIBLE": True,
            "RUNTIME_AVAILABILITY_KNOWN": True,
            "ECONOMIC_INTERPRETATION": "Pre-open MO/OverSell/UnderBuy already OPENING_AUCTION_CONTEXT (diagnostic only; PREOPEN_EXECUTION_VALID=false).",
            "MATERIAL_INFORMATION_GAIN": False,
            "NOTES": "Same raw fields. Not a new information source.",
        },
    ]


def d1_d10_for_local_unused() -> dict[str, bool]:
    """No unused local raw category passes D1-D10 jointly."""
    return {
        "D1_causal_before_decision": False,
        "D2_timestamp_auditable": False,
        "D3_not_reconstructable": True,
        "D4_not_mere_transform": True,
        "D5_coverage_or_defined_scope": False,
        "D6_same_form_as_future_runtime": False,
        "D7_economic_interpretation": True,
        "D8_state_begin_and_invalidate": False,
        "D9_no_future_return_leakage": True,
        "D10_can_join_full_causal": False,
        "ALL_PASS": False,
        "LOCALLY_AVAILABLE_UNUSED_RAW_CATEGORY": False,
    }


def future_method_requirements() -> dict[str, Any]:
    return {
        "METH1_finite_bounded_capacity": True,
        "METH2_TRAIN_only_candidate_construction": True,
        "METH3_Complete_Full_Causal_selection_unit": True,
        "METH4_ENTRY_execution_EXIT_before_selection": True,
        "METH5_CAP_same_symbol_occupancy_slot_reentry": True,
        "METH6_explainable_executable_frozen_strategy": True,
        "METH7_no_unlimited_threshold_search": True,
        "METH8_no_AutoML": True,
        "METH9_no_post_result_rule_modification": True,
        "METH10_fixed_internal_temporal_validation": True,
        "METHOD_DESIGNED_THIS_RUN": False,
    }


def future_dev_protocol() -> dict[str, Any]:
    return {
        "NEW_DEV_MIN_VALID_DAYS": int(NEW_DEV_MIN_VALID_DAYS),
        "NEW_DEV_MIN_CALENDAR_WEEKS": int(NEW_DEV_MIN_CALENDAR_WEEKS),
        "INTERNAL_FOLD_N": int(INTERNAL_FOLD_N),
        "FOLD_SCHEME": "five chronological blocks; each fold 4 TRAIN + 1 TEST; no random CV",
        "TEST_CANNOT_AFFECT": [
            "candidate construction",
            "feature choice",
            "threshold choice",
            "strategy definition",
        ],
        "INTERNAL_DEVELOPMENT_VALIDATION": True,
        "TRUE_OOS": False,
        "NO_HAND_PICKED_DAYS": True,
        "EXCLUDE_ONLY_PREDEFINED_INTEGRITY": True,
        "FUTURE_DATASET_DATES_ASSIGNED": False,
        "DATES_NOT_ASSIGNED": ["20260907+"],
    }


def future_oos_protocol() -> dict[str, Any]:
    return {
        "TRUE_OOS_MIN_VALID_DAYS": int(TRUE_OOS_MIN_VALID_DAYS),
        "BEGINS_ONLY_AFTER": [
            "strategy identity frozen",
            "implementation frozen",
            "all NEW_DEV selection complete",
        ],
        "MUST_BE": ["later in time", "never read during development", "never used for retune"],
        "FAIL_CLOSES_CANDIDATE": True,
        "NO_SECOND_CHANCE_THRESHOLD": True,
        "FUTURE_DATASET_DATES_ASSIGNED": False,
    }


def restart_gates(*, rg3: bool, rg4: bool) -> dict[str, Any]:
    g = {
        "RG1_LEGACY_DEV_retired": True,
        "RG2_closed_architectures_remain_closed": True,
        "RG3_new_causal_information_or_justified_premise": bool(rg3),
        "RG4_method_capacity_frozen_before_outcomes": bool(rg4),
        "RG5_NEW_DEV_date_selection_rule_frozen": True,
        "RG6_NEW_DEV_unread_until_above_frozen": True,
        "RG7_future_use_policy_explicitly_permits": False,
    }
    g["RESTART_ALLOWED"] = all(g.values())
    return g
