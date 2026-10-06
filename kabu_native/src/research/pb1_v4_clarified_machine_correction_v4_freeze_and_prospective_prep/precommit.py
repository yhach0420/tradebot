"""Prospective precommit. Hashed before any prospective data is opened."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.pb1_v3_2_face_failure_rca import PARENT_UNSEEN_N
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep import (
    DISCOVERY_FIRST,
    DISCOVERY_LAST,
    EXPECTED_V4_MACHINE_SHA256,
    FROZEN_IDENTITY,
    FROZEN_VALIDATION_FIRST,
    FROZEN_VALIDATION_LAST,
    OLD_CONFIRMATION_FIRST,
    OLD_CONFIRMATION_LAST,
    PRECOMMIT_FIRST_ELIGIBLE_SESSION,
    PRECOMMIT_MAX_SESSIONS,
    PRECOMMIT_MIN_CANDIDATE_DAYS,
)


def prospective_precommit(*, frozen_at: str, machine_sha: str, source_inventory_sha: str, ledger_n: int) -> dict[str, Any]:
    body = {
        "purpose": "SEMANTIC_CAUSAL_PROSPECTIVE_VALIDATION",
        "not_purpose": "strategy_profitability_certification",
        "machine_identity": FROZEN_IDENTITY,
        "machine_sha": machine_sha,
        "EXPECTED_V4_MACHINE_SHA256": EXPECTED_V4_MACHINE_SHA256,
        "source_inventory_sha": source_inventory_sha,
        "frozen_at": frozen_at,
        "data_eligibility": {
            "rule": "first complete JP cash session strictly after freeze_at, not in contamination ledger",
            "first_eligible_session": PRECOMMIT_FIRST_ELIGIBLE_SESSION,
            "backdating_forbidden": True,
            "dates_before_freeze_are_not_prospective": True,
            "not_in_contamination_ledger": True,
            "not_in_semantic_88": True,
            "not_in_rca_comparison_set": True,
            "not_previously_inspected_for_pb1_v4_development": True,
            "causal_raw_data_required": True,
            "required_5m_complete": True,
            "required_1m_complete_if_e1_evaluated": True,
            "contamination_ledger_n": int(ledger_n),
        },
        "sealed_periods_never_opened_this_task": {
            "discovery": {"first": DISCOVERY_FIRST, "last": DISCOVERY_LAST, "role": "DEVELOPMENT_EXPOSED_CALENDAR"},
            "old_confirmation": {"first": OLD_CONFIRMATION_FIRST, "last": OLD_CONFIRMATION_LAST, "opened": False},
            "frozen_validation": {"first": FROZEN_VALIDATION_FIRST, "last": FROZEN_VALIDATION_LAST, "opened": False},
        },
        "observation_period_stopping_rule": {
            "policy_source": "PARENT_UNSEEN_N frozen at V3.2 independent face sample; session cap precommitted in this prep, not after seeing prospective",
            "PARENT_UNSEEN_N": int(PARENT_UNSEEN_N),
            "min_candidate_days_WHY_THIS_STOCK": int(PRECOMMIT_MIN_CANDIDATE_DAYS),
            "max_sessions": int(PRECOMMIT_MAX_SESSIONS),
            "close_when": "WHY_THIS_STOCK candidate-days >= 21 OR sessions == 40, whichever first",
            "if_underpowered": "close batch; verdict UNDERPOWERED_NOT_A_PASS; do not extend after seeing results",
            "chosen_after_seeing_prospective": False,
        },
        "inclusion_exclusion": {
            "include": "native TSE cash names in the frozen research pool with complete 5m 09:00-11:19",
            "exclude": "contamination ledger symbol-dates; Kabu50; yfinance; inferred Bid/Ask; lunch truncation as implicit death",
            "full_partial_day": "partial AM data => day ineligible, not a semantic fail",
        },
        "market_data_quality_gate": {
            "native_1m_and_5m_required": True,
            "atr20_required": True,
            "no_inferred_bid_ask": True,
            "no_new_paid_data": True,
            "BAR_START_convention": True,
            "flatten_1520_is_session_ops_not_thesis_death": True,
        },
        "evaluated_states": [
            "WHY_THIS_STOCK",
            "SEED",
            "ACTIVE_REACHED",
            "ACTIVE_LIVE",
            "LOCATION_IDENTIFIED",
            "THESIS_REACHED",
            "THESIS_LIVE",
            "THESIS_LOST",
            "E0",
            "E1",
            "EXECUTION_READY",
        ],
        "state_logging_schema": {
            "candidate_day": [
                "candidate_day_id",
                "symbol",
                "date",
                "WHY_THIS_STOCK",
                "SEED_ELIGIBILITY",
                "OPENING_STATE",
                "POST_DOMINANT_PATH_STATE",
                "opening_seed_id",
                "opening_drive_id",
                "ACTIVE_REACHED",
                "ACTIVE_LIVE",
                "progress_event_sequence",
                "FAILED_PROBE_PENDING_transitions",
                "AUCTION_ENDED_AS_RANGE_event",
                "THESIS_LOST_event_time_reason",
                "LOCATION_IDENTIFIED",
                "location_family",
                "location_id",
                "THESIS_REACHED",
                "THESIS_LIVE",
                "thesis_id",
                "E0",
                "E1",
                "execution_id",
                "hidden_1m_fields",
                "same_bar_fields",
            ],
            "causal_timestamps": [
                "information_available_at",
                "event_completed_at",
                "state_changed_at",
                "entry_allowed_at",
            ],
            "forbidden": [
                "backdating",
                "future_confirmation_assigned_to_earlier_timestamp",
                "same_bar_execution",
                "future_hold_required_to_create_past_location",
                "FAILED_BREAK_REACCEPTED_before_confirmation_completed_bar",
            ],
        },
        "hidden_1m_contract": {
            "HIDDEN_1M_THESIS_PARITY_required": True,
            "1m_may_create": ["E1 timing only"],
            "1m_must_not_create_or_change": ["seed", "active", "direction", "location", "thesis", "death"],
        },
        "semantic_adjudication_procedure": {
            "when": "only after prospective batch closure",
            "machine_untouched_during_batch": True,
            "questions": [
                "Did the claimed opening auction actually exist?",
                "Was ACTIVE still alive at the claimed time?",
                "Was LOCATION already identifiable causally?",
                "Did 1m only time the pre-existing 5m thesis?",
                "Was THESIS_LOST caused by an observable auction-end event?",
                "Was any event backdated?",
            ],
            "human_label_accuracy_is_not_primary_gate": True,
            "primary_gates": [
                "causal_correctness",
                "state_contract_correctness",
                "failure_layer_correctness",
                "no_hidden_future_use",
                "no_1m_thesis_creation",
            ],
        },
        "pass_fail_rules": {
            "batch_not_opened_this_task": True,
            "economic_metrics_not_used": ["PnL", "PF", "win_rate", "MFE", "MAE", "best_exit", "fixed_holding_time"],
            "complete_strategy_economic_eval_deferred": True,
        },
        "bug_handling": {
            "mid_batch_code_change": "forbidden",
            "mid_batch_threshold_change": "forbidden",
            "single_case_exception": "forbidden",
            "if_change_required": {
                "close_v4_prospective_batch": True,
                "new_version_required": True,
                "old_prospective_data_becomes_development_exposed": True,
                "cannot_reuse_as_unseen_for_new_version": True,
            },
        },
        "version_invalidation_rule": {
            "any_V4_source_or_threshold_or_state_definition_change_invalidates_this_frozen_identity": True,
            "invalidated_identity_cannot_claim_this_precommit": True,
        },
        "no_mid_batch_learning": True,
        "PNL_USED": False,
        "PROSPECTIVE_DATA_OPENED": False,
    }
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    body["PRECOMMIT_SHA256"] = hashlib.sha256(raw).hexdigest()
    return body
