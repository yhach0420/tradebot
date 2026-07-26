"""Unit tests for confirmation causal integrity."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from research.entry_exit_contract.constants import CONTRACT_VERSION
from research.entry_exit_contract.contract import EntryContract
from research.eec_confirmation_integrity.causal import ask_status, first_n1_price_confirm
from research.eec_confirmation_integrity.expiry import find_episode_expiry, session_of
from research.eec_confirmation_integrity.parity import summarize_parity
from research.price_flow_exit.path_mfe import PathBar

JST = ZoneInfo("Asia/Tokyo")


def _bar(t0, i, px, *, ask=None, bid=None, aq=200.0):
    return PathBar(
        t=t0 + timedelta(seconds=i),
        px=px,
        bid=bid if bid is not None else px - 0.1,
        ask=ask if ask is not None else px + 0.1,
        bid_qty=200.0,
        ask_qty=aq,
        volume_delta=5.0,
        tick_direction=1,
        buy_aggression=0.6,
        spread_bps=2.0,
    )


def _ec2(t0=None):
    t0 = t0 or datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    return EntryContract(
        strategy_id="EC2",
        contract_version=CONTRACT_VERSION,
        symbol="1000.T",
        day="20260722",
        session="AM",
        entry_signal_time=t0,
        entry_time=t0,
        entry_price=1000.0,
        entry_reason="reclaim",
        entry_feature_snapshot={},
        expected_market_path="x",
        expected_horizon_sec=180.0,
        invalidation_level=990.0,
        invalidation_reason_definition="x",
        hold_condition_definition="x",
        profit_exit_definition="x",
        emergency_exit_definition="x",
        setup_id="s1",
        episode_id="EC2:1000.T:20260722:AM:ep1",
        source_quality="OK",
        quote_quality="OK",
        volume_quality="OK",
        trade_side_quality="OK",
        levels={"pullback_low": 990.0, "reclaim_level": 1000.0, "pre_pullback_high": 1020.0, "trend_reference": 1020.0},
    )


def test_horizon_expiry_at_180():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    c = _ec2(t0)
    path = [_bar(t0, i, 1005.0) for i in range(0, 200)]
    exp = find_episode_expiry(c, path, entry_i=0)
    assert exp.reason == "horizon_180"
    assert (exp.t - t0).total_seconds() == 180


def test_pullback_low_expires_episode():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    c = _ec2(t0)
    path = [_bar(t0, 0, 1005.0), _bar(t0, 1, 1004.0), _bar(t0, 2, 989.0)]
    exp = find_episode_expiry(c, path, entry_i=0)
    assert exp.reason == "pullback_low_break"


def test_session_of():
    assert session_of(datetime(2026, 7, 22, 10, 0, tzinfo=JST)) == "AM"
    assert session_of(datetime(2026, 7, 22, 13, 0, tzinfo=JST)) == "PM"


def test_ask_status_crossed_and_qty():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    crossed = _bar(t0, 0, 1000.0, ask=999.0, bid=1000.0, aq=200.0)
    assert ask_status(crossed) == "NOT_EVALUABLE_ASK_CROSSED"
    low_qty = _bar(t0, 0, 1000.0, ask=1000.1, bid=1000.0, aq=10.0)
    assert ask_status(low_qty) == "NOT_EVALUABLE_ASKQTY_LT_100"
    ok = _bar(t0, 0, 1000.0, ask=1000.1, bid=1000.0, aq=200.0)
    assert ask_status(ok) == "OK"


def test_price_confirm_and_reject_after_expiry():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    c = _ec2(t0)
    path2 = [_bar(t0, i, 1000.0 + min(i, 20) * 0.3, ask=1000.0 + min(i, 20) * 0.3 + 0.5, aq=200.0) for i in range(40)]
    conf2 = first_n1_price_confirm(c, path2, expire_at=t0 + timedelta(seconds=180))
    assert conf2 is not None
    path = [_bar(t0, 0, 1001.0), _bar(t0, 1, 989.0)] + [_bar(t0, i, 1010.0) for i in range(2, 30)]
    exp = find_episode_expiry(c, path, entry_i=0)
    assert exp.reason == "pullback_low_break"
    conf = first_n1_price_confirm(c, path, expire_at=exp.t)
    assert conf is None


def test_parity_summary_explained():
    rows = [
        {"v2_economic_success": False, "v3_economic_success": True, "classification_changed_reason": "v3_looser_profit_zone_hold"},
        {"v2_economic_success": True, "v3_economic_success": True, "classification_changed_reason": "agree"},
    ]
    s = summarize_parity(rows)
    assert s["verdict"] == "ECONOMIC_SUCCESS_PARITY_PASS"
    assert s["disagree_n"] == 1
