"""Resolver cases A–E. Shared by pytest and the corrected-rerun report. No Phase 2 outcomes."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np

from research.causal_driver_pb1.contracts.time import JST, bar_start_available_at
from research.causal_driver_pb1.phase2_discovery.clock import N_GRID, bar_index_for_available_t, grid_index
from research.causal_driver_pb1.response.asof import last_completed_price, locf_same_session, price_at_decision


def _bar(start: datetime, close: float) -> dict:
    return {"bar_start": start, "available_at": bar_start_available_at(start), "close": close}


def case_a_missing_exact_minute_uses_prior_completed_bar() -> None:
    d = datetime(2024, 9, 17, tzinfo=JST)
    b29 = _bar(d.replace(hour=9, minute=29), 100.0)
    b31 = _bar(d.replace(hour=9, minute=31), 101.0)
    decision = d.replace(hour=9, minute=31)
    got = last_completed_price(bars=[b29, b31], decision_time=decision)
    assert got is not None
    assert got.close == 100.0
    assert got.available_at == b29["available_at"]
    assert got.available_at <= decision
    assert b31["available_at"] > decision
    assert got.price_age_sec == 60.0


def case_b_no_future_observation() -> None:
    d = datetime(2024, 9, 17, tzinfo=JST)
    future = _bar(d.replace(hour=9, minute=31), 101.0)
    decision = d.replace(hour=9, minute=31)
    got = last_completed_price(bars=[future], decision_time=decision)
    assert got is None


def case_c_no_prior_day_across_session() -> None:
    prev = datetime(2024, 9, 13, 15, 0, tzinfo=JST)
    decision = datetime(2024, 9, 17, 9, 31, tzinfo=JST)
    got = last_completed_price(bars=[_bar(prev, 99.0)], decision_time=decision)
    assert got is None


def case_d_exact_minute_matches_asof_when_bar_exists() -> None:
    d = datetime(2024, 9, 17, tzinfo=JST)
    exact = _bar(d.replace(hour=9, minute=30), 55.5)
    decision = d.replace(hour=9, minute=31)
    got = last_completed_price(bars=[exact], decision_time=decision)
    assert got is not None
    assert got.close == 55.5
    assert got.bar_start == exact["bar_start"]
    assert got.available_at == exact["available_at"]
    assert got.price_age_sec == 0.0


def case_e_sparse_symbol_resolves_same_session() -> None:
    d = datetime(2024, 9, 17, tzinfo=JST)
    open_bar = _bar(d.replace(hour=9, minute=1), 12.0)
    later = _bar(d.replace(hour=10, minute=0), 13.0)
    decision = d.replace(hour=9, minute=40)
    got = last_completed_price(bars=[open_bar, later], decision_time=decision)
    assert got is not None
    assert got.close == 12.0
    assert got.bar_start == open_bar["bar_start"]
    assert later["available_at"] > decision


def case_pre_session_bar_not_used() -> None:
    d = datetime(2024, 9, 17, tzinfo=JST)
    pre = _bar(d.replace(hour=8, minute=30), 1.0)
    decision = d.replace(hour=9, minute=10)
    got = last_completed_price(bars=[pre], decision_time=decision)
    assert got is None


def case_locf_matches_last_completed_and_ignores_future() -> None:
    close = np.full((1, 1, N_GRID), np.nan, dtype=np.float64)
    close[0, 0, grid_index(9 * 60 + 29)] = 100.0
    close[0, 0, grid_index(9 * 60 + 31)] = 101.0
    asof, src = locf_same_session(close)
    px = float(price_at_decision(asof, 9 * 60 + 31)[0, 0])
    assert px == 100.0
    gi31 = bar_index_for_available_t(9 * 60 + 31)
    assert int(src[0, 0, gi31]) == grid_index(9 * 60 + 29)
    d = datetime(2024, 9, 17, tzinfo=JST)
    got = last_completed_price(
        bars=[
            _bar(d.replace(hour=9, minute=29), 100.0),
            _bar(d.replace(hour=9, minute=31), 101.0),
        ],
        decision_time=d.replace(hour=9, minute=31),
    )
    assert got is not None and got.close == px


CASES = (
    ("A_missing_exact_minute", case_a_missing_exact_minute_uses_prior_completed_bar),
    ("B_no_future", case_b_no_future_observation),
    ("C_no_prior_day", case_c_no_prior_day_across_session),
    ("D_exact_equals_asof", case_d_exact_minute_matches_asof_when_bar_exists),
    ("E_sparse_same_session", case_e_sparse_symbol_resolves_same_session),
    ("pre_session_excluded", case_pre_session_bar_not_used),
    ("locf_matches_scalar", case_locf_matches_last_completed_and_ignores_future),
)


def run_resolver_tests() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, fn in CASES:
        try:
            fn()
            rows.append({"test": name, "pass": True, "error": None})
        except Exception as exc:
            rows.append({"test": name, "pass": False, "error": str(exc)})
    return rows
