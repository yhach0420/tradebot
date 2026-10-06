"""Multi-touch daily zone tests. Causal activation, no future pivots, no Confirmation."""
from __future__ import annotations

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add
from research.multi_touch_daily_zone_1m_price_action_v1 import (
    FROZEN_VALIDATION_OPENED,
    LUNCH_POLICY,
    OLD_CONFIRMATION_OPENED,
    RCA_EXECUTED,
    ZONE_HALF_ATR,
    ZONE_WIDTH_SELECTED_BY_PNL,
)
from research.multi_touch_daily_zone_1m_price_action_v1.analyze import decide
from research.multi_touch_daily_zone_1m_price_action_v1.daily import same_day_reactions
from research.multi_touch_daily_zone_1m_price_action_v1.isolation import OUT, REF_LEVEL_OUT, write_overlap_n
from research.multi_touch_daily_zone_1m_price_action_v1.limits import new_limit_order, try_fill
from research.multi_touch_daily_zone_1m_price_action_v1.publish import SHEET_ORDER
from research.multi_touch_daily_zone_1m_price_action_v1.zones import cluster_role


def test_constants_and_sheets():
    assert FROZEN_VALIDATION_OPENED is False
    assert OLD_CONFIRMATION_OPENED is False
    assert RCA_EXECUTED is False
    assert ZONE_HALF_ATR == 0.15
    assert ZONE_WIDTH_SELECTED_BY_PNL is False
    assert LUNCH_POLICY == "HOLD_THROUGH_LUNCH_RESUME_PM"
    assert OUT.name == "multi_touch_daily_zone_1m_price_action_v1"
    assert REF_LEVEL_OUT.name == "reference_level_1m_price_action_discovery_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert SHEET_ORDER[-1] == "Safety"
    assert "Limit_Adverse_Selection" in SHEET_ORDER
    assert "PDH_Control" in SHEET_ORDER


def test_same_day_reaction_not_available_same_day():
    day = {"date": "20240917", "high": 110.0, "low": 100.0, "close": 101.0, "range": 10.0, "volume": 1, "trading_value": 1}
    rx = same_day_reactions(day, atr=5.0, next_session="20240918")
    assert rx and rx[0]["role"] == "RESISTANCE"
    assert rx[0]["confirmed_at"] == "20240917"
    assert rx[0]["available_from"] == "20240918"
    assert rx[0]["future_pivot"] is False
    assert same_day_reactions(day, atr=5.0, next_session=None) == []


def test_zone_active_only_after_second_touch_next_session():
    atr = 10.0
    r1 = {"role": "RESISTANCE", "price": 100.0, "reaction_date": "20240917", "available_from": "20240918", "symbol": "6857"}
    r2 = {"role": "RESISTANCE", "price": 100.5, "reaction_date": "20240918", "available_from": "20240919", "symbol": "6857"}
    z_on_18 = cluster_role([r1], atr=atr, session_date="20240918", lookback_dates={"20240917"}, role="RESISTANCE")
    assert z_on_18 and z_on_18[0]["active"] is False
    z_on_19 = cluster_role([r1, r2], atr=atr, session_date="20240919", lookback_dates={"20240917", "20240918"}, role="RESISTANCE")
    assert z_on_19 and z_on_19[0]["active"] is True
    assert z_on_19[0]["ZONE_ACTIVATED_AT"] == "20240919"
    z_too_early = cluster_role([r1, r2], atr=atr, session_date="20240918", lookback_dates={"20240917", "20240918"}, role="RESISTANCE")
    assert z_too_early and z_too_early[0]["active"] is False


def test_limit_not_same_bar_and_conservative_fill():
    order = new_limit_order(placed_at="09:31", limit_price=100.0, zone={"zone_id": "x", "lo": 99.0, "hi": 100.0}, break_event={"feature_bar": "09:30", "symbol": "6857"})
    assert order["first_fill_eligible_bar"] == "09:32"
    assert order["same_bar_placement_and_fill"] is False
    got = try_fill(limit_price=100.0, o=101.0, h=101.5, l=99.5)
    assert got and got["fill_price"] == 100.0
    gap = try_fill(limit_price=100.0, o=99.0, h=99.5, l=98.5)
    assert gap and gap["fill_price"] == 100.0 and gap["price_improvement_claimed"] is False
    miss = try_fill(limit_price=100.0, o=102.0, h=103.0, l=101.0)
    assert miss is None
    assert hhmm_add("09:31", 1) == "09:32"


def test_decide_no_info_stop():
    d = decide(bind_ok=True, path_sep=False, part_sep=False, x1_ids=[], atlas_n=10)
    assert d["VERDICT"] == "MULTI_TOUCH_ZONE_NO_INCREMENTAL_INFORMATION_V1"
    assert "STOP" in d["NEXT"]
