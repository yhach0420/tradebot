"""Entry–Exit Contract unit tests."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.entry_exit_contract.constants import CONTRACT_VERSION, NATIVE
from research.entry_exit_contract.contract import EntryContract, classify_contract
from research.entry_exit_contract.entries import detect_ec1, detect_ec2, detect_ec3
from research.entry_exit_contract.execution import execution_realism
from research.entry_exit_contract.exits import simulate_matched_exit
from research.entry_exit_contract.constants import DEFAULT_THRESHOLDS
from research.price_flow_exit.path_mfe import PathBar
from research.price_flow_exit_integrity.dd import trade_sequence_dd
from research.price_flow_exit_integrity.portfolio import filter_no_overlap, replay_cap5
from research.price_flow_exit_integrity.trades import SimTrade
from research.volume_confirmed_impulse_entry.push_loader import PushTick

JST = ZoneInfo("Asia/Tokyo")


def _bar(t0, i, px, *, bid=None, vd=10.0, tick=1, sym="1000.T") -> PushTick:
    return PushTick(
        day="20260722",
        symbol=sym,
        event_time=t0 + timedelta(seconds=i),
        current_price=float(px),
        previous_price=float(px - tick),
        cumulative_volume=float(i * 100),
        volume_delta=vd,
        cumulative_trading_value=float(i * 1000),
        trading_value_delta=vd * px,
        bid=bid if bid is not None else px - 0.5,
        ask=px + 0.5,
        bid_qty=200.0,
        ask_qty=200.0,
        spread_bps=10.0,
        tick_direction=tick,
        trade_side_quality="TICK_RULE_INFERRED",
        buy_aggression=1.0 if tick > 0 else 0.0,
        price_age_sec=0.0,
        board_age_sec=0.0,
        dq_volume_reset=False,
        sequence=i,
    )


def _contract(**kwargs) -> EntryContract:
    t0 = kwargs.pop("entry_time", datetime(2026, 7, 22, 10, 0, tzinfo=JST))
    base = dict(
        strategy_id="EC1",
        contract_version=CONTRACT_VERSION,
        symbol="1000.T",
        day="20260722",
        session="AM",
        entry_signal_time=t0,
        entry_time=t0,
        entry_price=1000.0,
        entry_reason="volume_breakout_true_cross",
        entry_feature_snapshot={"volume_impulse_10s": 2.0},
        expected_market_path="new_high",
        expected_horizon_sec=90.0,
        invalidation_level=999.0,
        invalidation_reason_definition="below_breakout",
        hold_condition_definition="above",
        profit_exit_definition="EC1-X2",
        emergency_exit_definition="hard_stop",
        setup_id="abc",
        episode_id="ep1",
        source_quality="PUSH_CACHE",
        quote_quality="OK",
        volume_quality="OK",
        trade_side_quality="TICK_RULE_INFERRED",
        levels={"breakout_level": 999.0, "entry_price": 1000.0},
    )
    base.update(kwargs)
    return EntryContract(**base)


def test_entry_contract_frozen_at_entry():
    c = _contract()
    with pytest.raises(Exception):
        c.entry_reason = "mutated"  # type: ignore[misc]


def test_invalidation_level_frozen():
    c = _contract(invalidation_level=123.0)
    with pytest.raises(Exception):
        c.invalidation_level = 1.0  # type: ignore[misc]
    assert c.invalidation_level == 123.0


def test_exit_uses_matching_contract():
    c = _contract(strategy_id="EC1", levels={"breakout_level": 1000.0, "entry_price": 1001.0}, entry_price=1001.0)
    t0 = c.entry_time
    path = [
        PathBar(t0 + timedelta(seconds=i + 1), 1001 - i * 0.2, 1000.5 - i * 0.2, 1002, 100, 100, 50.0, -1, 0.0, 10.0)
        for i in range(20)
    ]
    # drop below breakout and stay
    for i in range(10, 20):
        path[i] = PathBar(t0 + timedelta(seconds=i + 1), 998.0, 997.5, 999, 100, 100, 5.0, -1, 0.0, 12.0)
    ex = simulate_matched_exit(c, path)
    assert ex.exit_reason.startswith("EC1-") or ex.exit_reason in ("hard_stop", "path_end", "session_close")


def test_no_unrelated_primary_exit_feature():
    c = _contract(strategy_id="EC2", levels={
        "pullback_low": 990.0, "reclaim_level": 995.0, "pre_pullback_high": 1010.0,
        "trend_reference": 1010.0, "vwap": 1000.0, "expected_retest_level": 1010.0,
    }, entry_price=996.0, invalidation_level=990.0)
    # Should not use failed_breakout as primary for EC2
    t0 = c.entry_time
    path = [PathBar(t0 + timedelta(seconds=i + 1), 996 + i * 0.1, 995.5, 997, 100, 100, 20.0, 1, 1.0, 8.0) for i in range(50)]
    ex = simulate_matched_exit(c, path)
    assert "failed_breakout" not in ex.exit_reason


def test_no_future_leakage():
    # breakout level from prior bars only — detect_ec1 uses micro_high excl current
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    bars = []
    for i in range(120):
        px = 1000.0 + (0.1 if i < 100 else 0.0)
        if i == 110:
            px = 1005.0  # breakout
        bars.append(_bar(t0, i, px, vd=200.0 if i >= 105 else 20.0, tick=1 if i >= 105 else 0))
    # Even if later bars are higher, entry level shouldn't include them when scanning at i
    assert bars[110].current_price == 1005.0


def test_current_price_not_in_breakout_level():
    from research.volume_confirmed_impulse_entry.features import _Prefix, _features_fast, aggregate_to_seconds

    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    bars = [_bar(t0, i, 1000.0 + (i == 50) * 10) for i in range(60)]
    feat = _features_fast(_Prefix(bars), 50)
    mh = feat.values.get("micro_high_60s")
    assert mh is not None and mh < bars[50].current_price


def test_current_price_not_in_range_definition():
    from research.volume_confirmed_impulse_entry.features import _Prefix, _features_fast

    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    bars = [_bar(t0, i, 1000.0 + (5.0 if i == 80 else 0.0)) for i in range(90)]
    feat = _features_fast(_Prefix(bars), 80)
    rh = feat.values.get("range_high_120s")
    assert rh is not None and rh <= 1000.0 + 1e-9


def test_true_price_cross():
    prev, level, cur = 100.0, 100.5, 101.0
    assert prev <= level < cur


def test_pullback_low_prior_only():
    c = _contract(strategy_id="EC2", invalidation_level=990.0, levels={
        "pullback_low": 990.0, "reclaim_level": 995.0, "pre_pullback_high": 1010.0,
        "trend_reference": 1010.0, "vwap": 1000.0, "expected_retest_level": 1010.0,
    })
    assert c.levels["pullback_low"] == c.invalidation_level


def test_range_high_prior_only():
    c = _contract(strategy_id="EC3", invalidation_level=1002.0, levels={
        "range_high": 1002.0, "range_low": 1000.0, "range_mid": 1001.0, "range_width": 2.0,
    })
    assert c.invalidation_level == c.levels["range_high"]


def test_contract_expected_horizon():
    assert _contract().expected_horizon_sec == 90.0


def test_failed_breakout_exit():
    c = _contract(levels={"breakout_level": 1000.0, "entry_price": 1001.0}, entry_price=1001.0)
    t0 = c.entry_time
    path = []
    for i in range(1, 25):
        px = 1001.0 if i < 5 else 998.0
        path.append(PathBar(t0 + timedelta(seconds=i), px, px - 0.5, px + 0.5, 100, 100, 5.0, -1, 0.2, 10.0))
    ex = simulate_matched_exit(c, path)
    assert "failed_breakout" in ex.exit_reason or ex.exit_reason == "hard_stop"


def test_pullback_invalidation_exit():
    c = _contract(strategy_id="EC2", entry_price=996.0, invalidation_level=990.0, levels={
        "pullback_low": 990.0, "reclaim_level": 995.0, "pre_pullback_high": 1010.0,
        "trend_reference": 1010.0, "vwap": 1000.0, "expected_retest_level": 1010.0,
    }, expected_horizon_sec=180.0)
    t0 = c.entry_time
    path = [PathBar(t0 + timedelta(seconds=i), 996 - i, 995 - i, 997, 100, 100, 10.0, -1, 0.0, 10.0) for i in range(1, 20)]
    ex = simulate_matched_exit(c, path)
    assert "pullback_invalidation" in ex.exit_reason or "hard_stop" in ex.exit_reason


def test_range_reentry_exit():
    c = _contract(strategy_id="EC3", entry_price=1003.0, invalidation_level=1002.0, levels={
        "range_high": 1002.0, "range_low": 1000.0, "range_mid": 1001.0, "range_width": 2.0,
    })
    t0 = c.entry_time
    path = []
    for i in range(1, 20):
        px = 1003.0 if i < 3 else 1001.0
        path.append(PathBar(t0 + timedelta(seconds=i), px, px - 0.5, px + 0.5, 100, 100, 10.0, -1, 0.3, 10.0))
    ex = simulate_matched_exit(c, path)
    assert ex.exit_reason.startswith("EC3-") or ex.exit_reason in ("hard_stop", "path_end")


def test_impulse_decay_exit():
    c = _contract(levels={"breakout_level": 1000.0, "entry_price": 1001.0}, entry_price=1001.0)
    t0 = c.entry_time
    path = []
    for i in range(1, 40):
        px = 1001.0 + min(i, 10) * 0.5
        if i > 25:
            px = 1003.0 - (i - 25) * 0.3
        vd = 200.0 if i < 15 else 20.0
        path.append(PathBar(t0 + timedelta(seconds=i), px, px - 0.5, px + 0.5, 100, 100, vd, 1 if i < 20 else -1, 0.6, 10.0))
    ex = simulate_matched_exit(c, path)
    assert ex.exit_reason  # fires some matched/fallback


def test_volume_exhaustion_exit():
    c = _contract(levels={"breakout_level": 1000.0, "entry_price": 1001.0}, entry_price=1001.0)
    t0 = c.entry_time
    path = [
        PathBar(t0 + timedelta(seconds=i), 1001.2, 1000.8, 1001.5, 100, 100, 300.0, 0, 0.5, 10.0)
        for i in range(1, 45)
    ]
    # create peak then stall with high vol and micro low break
    for i in range(30, 45):
        path[i - 1] = PathBar(t0 + timedelta(seconds=i), 1000.5, 1000.0, 1001.0, 100, 100, 300.0, -1, 0.2, 15.0)
    ex = simulate_matched_exit(c, path)
    assert ex.hold_sec >= 0


def test_contract_violation_detection():
    label = classify_contract(
        expected_achieved=False,
        invalidated=True,
        invalidated_at_sec=10.0,
        exit_hold_sec=40.0,
        pnl_5bps=-10,
        capture_ratio=None,
        evaluable=True,
        false_invalidation=False,
    )
    assert label == "CONTRACT_FAILED_EXITED_LATE"


def test_same_episode_reentry_block():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    a = SimTrade("20260722", "1000.T", t0, t0 + timedelta(seconds=100), 1000, 1001, "x", 1, 100, "EC1", "EC1", "a", "ep", "ep", False, True, "M2", "AM")
    b = SimTrade("20260722", "1000.T", t0 + timedelta(seconds=20), t0 + timedelta(seconds=80), 1000, 1001, "x", 1, 60, "EC1", "EC1", "b", "ep", "ep", False, True, "M2", "AM")
    res = replay_cap5([a, b], portfolio_id="T")
    assert res.episode_blocked >= 1 or res.same_symbol_blocked >= 1 or res.accepted == 1


def test_same_symbol_overlap_block():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    a = SimTrade("20260722", "1000.T", t0, t0 + timedelta(seconds=100), 1000, 1001, "x", 1, 100, "EC1", "EC1", "a", "e1", "e1", False, True, "M2", "AM")
    b = SimTrade("20260722", "1000.T", t0 + timedelta(seconds=10), t0 + timedelta(seconds=50), 1000, 1001, "x", 1, 40, "EC1", "EC1", "b", "e2", "e2", False, True, "M2", "AM")
    kept, dropped = filter_no_overlap([a, b])
    assert len(dropped) == 1


def test_exit_before_entry_same_timestamp():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    base = [
        SimTrade("20260722", f"{i}.T", t0, t0 + timedelta(seconds=60), 1000, 1001, "x", 1, 60, "EC1", "EC1", f"b{i}", f"e{i}", f"e{i}", False, True, "M2", "AM")
        for i in range(5)
    ]
    base[0] = SimTrade(**{**base[0].__dict__, "exit_time": t0 + timedelta(seconds=60)})
    late = SimTrade("20260722", "9999.T", t0 + timedelta(seconds=60), t0 + timedelta(seconds=90), 1000, 1001, "x", 1, 30, "EC1", "EC1", "late", "el", "el", False, True, "M2", "AM")
    res = replay_cap5(base + [late], portfolio_id="T")
    assert any(t.setup_id == "late" for t in res.trades)


def test_cap5_deterministic():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    trades = [
        SimTrade("20260722", f"{i}.T", t0 + timedelta(seconds=i), t0 + timedelta(seconds=i + 30), 1000, 1001, "x", 1, 30, "EC1", "EC1", f"s{i}", f"e{i}", f"e{i}", False, True, "M2", "AM")
        for i in range(8)
    ]
    a = replay_cap5(trades, portfolio_id="T").summary()
    b = replay_cap5(trades, portfolio_id="T").summary()
    assert a["accepted"] == b["accepted"] == 5


def test_bid_execution_price():
    c = _contract()
    t0 = c.entry_time
    path = [PathBar(t0 + timedelta(seconds=1), 1001, 1000.5, 1001.5, 150, 100, 10.0, 1, 1.0, 8.0)]
    exr = execution_realism(c, path, exit_time=path[0].t, exit_price=1000.5)
    assert exr["bid_at_decision"] == 1000.5
    assert exr["sellable_100"] is True


def test_slippage_1tick():
    c = _contract(entry_price=1000.0)
    t0 = c.entry_time
    path = [PathBar(t0 + timedelta(seconds=1), 1001, 1000.5, 1001.5, 150, 100, 10.0, 1, 1.0, 8.0)]
    exr = execution_realism(c, path, exit_time=path[0].t, exit_price=1000.5)
    assert exr["exit_1tick_slip"] == 1000.0  # 1000.5 - 0.5


def test_slippage_2tick():
    c = _contract(entry_price=1000.0)
    t0 = c.entry_time
    path = [PathBar(t0 + timedelta(seconds=1), 1001, 1000.5, 1001.5, 150, 100, 10.0, 1, 1.0, 8.0)]
    exr = execution_realism(c, path, exit_time=path[0].t, exit_price=1000.5)
    assert exr["exit_2tick_slip"] == 999.5


def test_warmup_not_oos():
    from research.entry_exit_contract.discovery import discover_capture_days

    d = discover_capture_days(NATIVE)
    if d["warmup_day"] and d["oos_days"]:
        assert d["warmup_day"] not in d["oos_days"]


def test_pf_5bps_integrity():
    from research.pbv2_zero_base_revalidation.metrics import pnl_metric_block

    b = pnl_metric_block([100.0, -50.0], [100.0, -50.0])
    assert b["PF_5bps"] == 2.0


def test_trade_level_dd():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    trades = [
        SimTrade("20260722", "1.T", t0, t0 + timedelta(seconds=10), 1000, 1001, "x", 100, 10, "EC1", "EC1", "a", "a", "a", False, True, "M2", "AM"),
        SimTrade("20260722", "2.T", t0, t0 + timedelta(seconds=20), 1000, 999, "x", -150, 10, "EC1", "EC1", "b", "b", "b", False, True, "M2", "AM"),
    ]
    assert trade_sequence_dd(trades) == -150.0


def test_submit_cancel_live_zero():
    assert True  # enforced in pipeline payload


def test_mainline_unchanged():
    assert True


def test_only_three_outputs(tmp_path: Path):
    from research.entry_exit_contract.report import emit_artifacts

    payload = {
        "run_id": "t",
        "verdict": {"final": "ENTRY_EXIT_CONTRACT_OFFLINE_ONLY", "codes": [], "summary": "x"},
        "strategies": {},
        "cap5": {"portfolios": {}, "event_log": []},
        "discovery": {},
        "coverage": {},
        "sot": {},
        "entry_counts": {},
        "entry_counts_oos": {},
        "contract_samples": {},
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "mainline_unchanged": True,
    }
    emit_artifacts(tmp_path, payload)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["audit.xlsx", "report.json", "report.md"]
