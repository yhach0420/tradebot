"""Frozen Validation blind-confirmation contract. Hashed before any FV bars are loaded.

Allowed-list and degeneracy thresholds are copied from Old Confirmation, not retuned.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep import FROZEN_IDENTITY
from research.pb1_v4_frozen_old_confirmation_blind_validation.precommit import (
    ALLOWED_THESIS_LOST_REASONS as OC_ALLOWED,
    REQUIRED_ZERO as OC_REQUIRED_ZERO,
)
from research.pb1_v4_frozen_validation_blind_confirmation import (
    CASE_FAIL,
    CASE_PASS,
    EVAL_FIRST,
    EVAL_LAST,
    EXPECTED_MACHINE_SHA256,
    EXPECTED_SOURCE_INVENTORY_SHA256,
    KNOWN_AUDIT_TAXONOMY_MISMATCH,
    LOOKBACK_TAIL_N,
    NEXT_CLOSE,
    NEXT_RCA,
    OC_FIRST,
    OC_LAST,
    PARENT_OC_PRECOMMIT_SHA256,
    POOL_N,
    PRECOMMIT_ID,
    PROSPECTIVE_FROM,
)
from research.pb1_v4_prospective_semantic_validation_preflight.logging import CANDIDATE_DAY_FIELDS, CAUSAL_FIELDS

ALLOWED_THESIS_LOST_REASONS = tuple(OC_ALLOWED)
REQUIRED_ZERO = tuple(OC_REQUIRED_ZERO)
assert "ACCEPTED_STRUCTURAL_FAILURE" not in ALLOWED_THESIS_LOST_REASONS
assert KNOWN_AUDIT_TAXONOMY_MISMATCH == "ACCEPTED_STRUCTURAL_FAILURE"

EVALUATED_STATES = (
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
)
ADJUDICATION_QUESTIONS = (
    "Did a real opening directional auction exist?",
    "Was ACTIVE genuinely alive?",
    "Was LOCATION identifiable causally?",
    "Did 1m only time an existing 5m thesis?",
    "Was THESIS_LOST based on an observable frozen death path?",
    "Was anything backdated?",
)


def fv_precommit(
    *,
    machine_sha: str,
    source_inventory_sha: str,
    frozen_validation_n: int,
    lookback_n: int,
    lookback_first: str | None,
    lookback_last: str | None,
    eval_first: str | None,
    eval_last: str | None,
    symbol_n: int,
) -> dict[str, Any]:
    body = {
        "precommit_id": PRECOMMIT_ID,
        "purpose": "SEMANTIC_CAUSAL_GENERALIZATION_CONFIRMATION",
        "not_purpose": "profitability_optimization",
        "evidence_stream": "FROZEN_VALIDATION_BLIND_CONFIRMATION",
        "independent_of_prospective": True,
        "independent_of_old_confirmation_scoring": True,
        "machine_identity": FROZEN_IDENTITY,
        "machine_sha": machine_sha,
        "EXPECTED_V4_MACHINE_SHA256": EXPECTED_MACHINE_SHA256,
        "source_inventory_sha": source_inventory_sha,
        "EXPECTED_SOURCE_INVENTORY_SHA256": EXPECTED_SOURCE_INVENTORY_SHA256,
        "parent_old_confirmation_precommit_sha256": PARENT_OC_PRECOMMIT_SHA256,
        "copied_from_old_confirmation_without_retune": True,
        "date_range": {
            "frozen_validation_first": EVAL_FIRST,
            "frozen_validation_last": EVAL_LAST,
            "frozen_validation_n_from_frozen_split": int(frozen_validation_n),
            "eval_first_from_split": eval_first,
            "eval_last_from_split": eval_last,
            "opened_this_task": ["FROZEN_VALIDATION"],
            "not_reused_as_blind": {
                "old_confirmation": {"first": OC_FIRST, "last": OC_LAST, "DEVELOPMENT_EXPOSED_AFTER_REVIEW": True},
            },
            "sealed_this_task": ["PROSPECTIVE"],
        },
        "symbol_universe": {
            "pool": "frozen_research_pool",
            "n": int(symbol_n) if int(symbol_n) else int(POOL_N),
            "expected_n": int(POOL_N),
            "no_kabu50": True,
            "no_yfinance": True,
            "no_inferred_bid_ask": True,
        },
        "lookback": {
            "role": "FEATURE_HISTORY_ONLY",
            "source": "Discovery + Old Confirmation already development-exposed",
            "not_a_holdout": True,
            "not_adjudicated_as_blind": True,
            "LOOKBACK_TAIL_N": int(LOOKBACK_TAIL_N),
            "lookback_n": int(lookback_n),
            "lookback_first": lookback_first,
            "lookback_last": lookback_last,
            "reason": "ATR20 SMA75 same-clock opening-5m noise baselines require prior completed sessions",
        },
        "sealed_periods": {
            "prospective": {"from": PROSPECTIVE_FROM, "opened": False},
        },
        "data_completeness_gate": {
            "native_1m_required": True,
            "native_5m_required": True,
            "am_window": "09:00-11:19 inclusive, BAR_START",
            "required_1m_n": 140,
            "atr20_from_prior_completed_sessions": True,
            "partial_AM_is_ineligible_not_semantic_fail": True,
            "candidate_unit": "symbol-date",
            "no_inferred_bid_ask": True,
            "no_new_paid_data": True,
            "flatten_1520_is_session_ops_not_thesis_death": True,
        },
        "candidate_definition": {
            "unit": "symbol-date",
            "must_be_frozen_validation_calendar": True,
            "must_pass_completeness_gate": True,
            "must_be_frozen_pool_symbol": True,
            "WHY_THIS_STOCK_required_for_candidate_day": True,
            "lookback_dates_are_not_candidates": True,
            "old_confirmation_dates_are_not_candidates": True,
            "prospective_dates_are_not_candidates": True,
        },
        "evaluated_states": list(EVALUATED_STATES),
        "seed_taxonomy": ["NO_VALID_DRIVE_SEED", "FAILED_OPEN_SEED", "TRUE_OPENING_DRIVE_SEED"],
        "unknown_seed_label_forbidden": True,
        "state_logging_schema": {
            "candidate_day": list(CANDIDATE_DAY_FIELDS),
            "causal_timestamps": list(CAUSAL_FIELDS),
        },
        "causal_timestamp_contract": {
            "fields": list(CAUSAL_FIELDS),
            "BAR_START": True,
            "entry_allowed_at_strictly_after_event_completed_at": True,
            "forbidden": [
                "backdating",
                "future_confirmation",
                "same_bar_entry",
                "future_hold_creating_past_location",
            ],
        },
        "hidden_1m_contract": {
            "HIDDEN_1M_THESIS_PARITY_required": True,
            "evaluate_5m_only_and_1m_visible_in_parallel": True,
            "1m_must_not_create_or_change": ["SEED", "ACTIVE", "direction", "LOCATION", "THESIS", "THESIS_LOST"],
            "1m_may_create": ["E1 timing only"],
        },
        "same_bar_prohibition": {
            "SAME_BAR_ENTRY_required_zero": True,
            "entry_on_completed_event_bar_forbidden": True,
        },
        "semantic_adjudication_rubric": {
            "when": "only after frozen V4 run completes on the full Frozen Validation set",
            "human_label_accuracy_is_not_primary_gate": True,
            "old_confirmation_cases_are_not_scoring_targets": True,
            "questions": list(ADJUDICATION_QUESTIONS),
            "allowed_thesis_lost_reasons_after_THESIS_REACHED": list(ALLOWED_THESIS_LOST_REASONS),
            "KNOWN_AUDIT_TAXONOMY_MISMATCH": KNOWN_AUDIT_TAXONOMY_MISMATCH,
            "taxonomy_mismatch_not_added_to_allowed_list": True,
            "primary_gates": [
                "causal_contract",
                "state_invariants",
                "hidden_1m",
                "no_future_leakage",
                "no_systematic_semantic_breakdown",
            ],
        },
        "pass_fail_rules": {
            "PASS_VERDICT": CASE_PASS,
            "FAIL_VERDICT": CASE_FAIL,
            "PASS_NEXT": NEXT_CLOSE,
            "FAIL_NEXT": NEXT_RCA,
            "REQUIRED_ZERO": list(REQUIRED_ZERO),
            "degeneracy": {
                "WHY_THIS_STOCK_eq_0_on_complete_frozen_validation_set": "FAIL",
                "WHY_ge_21_and_SEED_eq_0": "FAIL",
                "SEED_ge_10_and_ACTIVE_REACHED_eq_0": "FAIL",
                "THESIS_LOST_without_allowed_reason_share_gt_0.50_when_lost_n_ge_10": "FAIL",
            },
            "economic_metrics_not_used": [
                "PnL",
                "PF",
                "win_rate",
                "MFE",
                "MAE",
                "future_return",
                "best_exit",
                "fixed_hold",
            ],
            "do_not_retune_after_seeing_old_confirmation": True,
            "do_not_retune_after_seeing_frozen_validation": True,
        },
        "bug_version_invalidation_rule": {
            "mid_run_threshold_change": "forbidden",
            "single_case_exception": "forbidden",
            "symbol_specific_rescue": "forbidden",
            "semantic_definition_change": "forbidden",
            "allowed_list_change": "forbidden",
            "V4_1_creation_this_task": "forbidden",
            "old_confirmation_readjudication": "forbidden",
            "if_change_required": {
                "close_this_blind_batch": True,
                "verdict": CASE_FAIL,
                "new_version_required": True,
                "Frozen_Validation_becomes_exposed_not_unseen": True,
                "Prospective_remains_sealed": True,
            },
            "any_V4_source_or_threshold_or_state_definition_change_invalidates_frozen_identity": True,
        },
        "run_policy": {
            "frozen_v4_exactly_as_is": True,
            "once_only": True,
            "no_mid_run_learning": True,
        },
        "PNL_USED": False,
        "MFE_MAE_USED": False,
        "FUTURE_OUTCOME_USED": False,
        "OLD_CONFIRMATION_REUSED_AS_BLIND": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "V4_CHANGED": False,
        "SPEC_CHANGED": False,
        "THRESHOLD_RETUNED": False,
        "ALLOWED_LIST_CHANGED": False,
    }
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    body["PRECOMMIT_SHA256"] = hashlib.sha256(raw).hexdigest()
    return body
