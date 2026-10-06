"""Resolver unit tests A–E. No Phase 2 outcomes. No PnL."""
from __future__ import annotations

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401

from research.causal_driver_pb1.response.cases import (
    case_a_missing_exact_minute_uses_prior_completed_bar,
    case_b_no_future_observation,
    case_c_no_prior_day_across_session,
    case_d_exact_minute_matches_asof_when_bar_exists,
    case_e_sparse_symbol_resolves_same_session,
    case_locf_matches_last_completed_and_ignores_future,
    case_pre_session_bar_not_used,
)


def test_a_missing_exact_minute_uses_prior_completed_bar():
    case_a_missing_exact_minute_uses_prior_completed_bar()


def test_b_no_future_observation():
    case_b_no_future_observation()


def test_c_no_prior_day_across_session():
    case_c_no_prior_day_across_session()


def test_d_exact_minute_matches_asof_when_bar_exists():
    case_d_exact_minute_matches_asof_when_bar_exists()


def test_e_sparse_symbol_resolves_same_session():
    case_e_sparse_symbol_resolves_same_session()


def test_pre_session_bar_not_used():
    case_pre_session_bar_not_used()


def test_locf_matches_last_completed_and_ignores_future():
    case_locf_matches_last_completed_and_ignores_future()
