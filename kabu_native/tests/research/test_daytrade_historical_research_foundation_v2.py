"""V2 foundation tests. No strategy. V1 not rewritten. V1.1 not adopted."""
from __future__ import annotations

import inspect

from research.daytrade_historical_research_foundation_v2 import (
    CASE_ENTITLEMENT,
    CASE_TIME,
    ENTRY,
    EXIT,
    PNL_USED,
    STRATEGY_SEARCH_STARTED,
    V1_1_ADOPTED,
    V1_MODIFIED,
)
from research.daytrade_historical_research_foundation_v2.analyze import decide
from research.daytrade_historical_research_foundation_v2.eligibility import LABEL_OPS, LABEL_TRADE, classify
from research.daytrade_historical_research_foundation_v2.isolation import FREEZE_OUT, OUT, write_overlap_n
from research.daytrade_historical_research_foundation_v2.pool import range_bps
from research.daytrade_historical_research_foundation_v2.publish import SHEET_ORDER


def test_entitlement_and_time_fail_closed():
    ent = decide(
        entitlement={"minute": {"available": False, "addon_required": True, "http_status": 403, "reason": "jquants_http_403"}},
        semantics="UNKNOWN",
        pool_ok=True,
        panel={"ok": True},
        state={"buildable": True},
    )
    assert ent["VERDICT"] == CASE_ENTITLEMENT
    time_block = decide(
        entitlement={"minute": {"available": True, "http_status": 200}},
        semantics="UNKNOWN",
        pool_ok=True,
        panel={"ok": True},
        state={"buildable": True},
    )
    assert time_block["VERDICT"] == CASE_TIME


def test_range_bps_and_ops_label():
    assert abs((range_bps(100.0, 101.0, 99.0) or 0) - 200.0) < 1e-9
    ops = classify(pool_row={"symbol": "6861", "notional_100": 5_000_000, "median_range_bps": 200.0}, minute_row=None)
    assert ops["label"] == LABEL_OPS
    missing = classify(
        pool_row={"symbol": "7203", "notional_100": 300_000, "median_range_bps": 120.0, "in_liquidity_top80": True},
        minute_row=None,
    )
    assert missing["label"] == LABEL_OPS
    assert missing["label"] != LABEL_TRADE
    trade = classify(
        pool_row={"symbol": "7203", "notional_100": 300_000, "median_range_bps": 120.0, "in_liquidity_top80": True},
        minute_row={"minute_metrics_complete": True, "active_minute_ratio": 0.5, "first_half_median_va": 1, "second_half_median_va": 1},
    )
    assert trade["label"] == LABEL_TRADE


def test_safety_and_isolation():
    import research.daytrade_historical_research_foundation_v2.analyze as a
    import research.daytrade_historical_research_foundation_v2.pool as p

    src = inspect.getsource(a)
    pool_src = inspect.getsource(p)
    assert "harvest_development" not in src
    assert "evaluate_strategy" not in src
    assert "if sym in {\"9983\"" not in pool_src
    assert "if symbol in {\"9983\"" not in pool_src
    assert STRATEGY_SEARCH_STARTED is False
    assert PNL_USED is False
    assert V1_MODIFIED is False
    assert V1_1_ADOPTED is False
    assert ENTRY is False and EXIT is False
    assert OUT.name == "daytrade_historical_research_foundation_v2"
    assert FREEZE_OUT.name == "fixed_daytrade_universe_v1"
    assert write_overlap_n("", "") == 0
    assert "RESEARCH_POOL" in SHEET_ORDER
    assert tuple(SHEET_ORDER) == (
        "SUMMARY",
        "RESEARCH_POOL",
        "ELIGIBILITY",
        "MINUTE_COVERAGE",
        "CONTEXT",
        "MARKET_STATE",
    )
