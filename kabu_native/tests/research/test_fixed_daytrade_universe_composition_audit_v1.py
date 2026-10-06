"""Composition audit tests. No strategy. V1 identity frozen. Factor sides precommitted."""
from __future__ import annotations

import inspect

from research.fixed_daytrade_universe_composition_audit_v1 import (
    CASE_EXPAND,
    CASE_UNIVERSE,
    CORR_REDUNDANT,
    ENTRY,
    EXIT,
    PNL_USED,
    STRATEGY_SEARCH_STARTED,
    UNIVERSE_CHANGED,
    V1_REWRITTEN,
)
from research.fixed_daytrade_universe_composition_audit_v1.analyze import decide
from research.fixed_daytrade_universe_composition_audit_v1.factors import ROLES, side_coverage
from research.fixed_daytrade_universe_composition_audit_v1.isolation import FREEZE_OUT, OUT, write_overlap_n
from research.fixed_daytrade_universe_composition_audit_v1.proxies import session_proxies
from research.fixed_daytrade_universe_composition_audit_v1.publish import SHEET_ORDER
from research.aligned_historical_panel_v1.universe_bind import bind_frozen_universe


def test_frozen_universe_still_bound():
    u = bind_frozen_universe()
    assert u["verified"] is True
    assert u["symbol_n"] == 45
    assert u["did_not_change_universe"] is True
    assert V1_REWRITTEN is False
    assert UNIVERSE_CHANGED is False


def test_proxy_formulas():
    p = session_proxies(prev_close=100.0, open_=101.0, high=104.0, low=99.0, close=102.0)
    assert abs(p["range_over_prev_close"] - 0.05) < 1e-12
    assert abs(p["abs_body_over_prev_close"] - 0.01) < 1e-12
    assert abs(p["gap_over_prev_close"] - 0.01) < 1e-12
    assert abs(p["open_to_high_over_prev_close"] - 0.03) < 1e-12
    assert abs(p["open_to_low_over_prev_close"] + 0.02) < 1e-12


def test_core_factor_sides_precommitted():
    symbols = sorted(ROLES)
    cov = {r["factor"]: r for r in side_coverage(symbols)}
    assert cov["JAPAN_RATES"]["both_sides"] is True
    assert cov["OIL"]["both_sides"] is True
    assert cov["USDJPY"]["both_sides"] is True
    assert cov["US_TECH_NQ"]["both_sides"] is True
    assert cov["CHINA_HK"]["both_sides"] is True
    assert "8766" not in ROLES
    assert "8604" not in ROLES


def test_decide_expand_on_necessary_gap():
    cov = side_coverage(sorted(ROLES))
    d = decide(universe_ok=True, daily_ok=True, coverage=cov, necessary_missing=[{"role": "insurance"}])
    assert d["VERDICT"] == CASE_EXPAND
    bad = decide(universe_ok=False, daily_ok=True, coverage=cov, necessary_missing=[])
    assert bad["VERDICT"] == CASE_UNIVERSE


def test_no_strategy_and_safety():
    import research.fixed_daytrade_universe_composition_audit_v1.analyze as a

    src = inspect.getsource(a)
    assert "harvest_development" not in src
    assert "evaluate_strategy" not in src
    assert "compute_pnl_yen_100" not in src
    assert STRATEGY_SEARCH_STARTED is False
    assert PNL_USED is False
    assert ENTRY is False and EXIT is False


def test_isolation_and_sheets():
    assert OUT.name == "fixed_daytrade_universe_composition_audit_v1"
    assert FREEZE_OUT.name == "fixed_daytrade_universe_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "SUMMARY"
    assert "V1_1_CANDIDATE" in SHEET_ORDER
    assert CORR_REDUNDANT == 0.85
