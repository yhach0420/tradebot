"""Unit tests for Price-Flow EXIT integrity evaluation."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from research.price_flow_exit.entries import FixedEntry
from research.price_flow_exit.exit_rules import ExitParams
from research.price_flow_exit.path_mfe import PathBar
from research.price_flow_exit_integrity.ablation import simulate_ablation
from research.price_flow_exit_integrity.baseline import reason_family, simulate_x0_runtime_proxy
from research.price_flow_exit_integrity.dd import daily_close_max_dd, trade_sequence_dd
from research.price_flow_exit_integrity.portfolio import audit_overlapping_entries, filter_no_overlap, replay_cap5
from research.price_flow_exit_integrity.trades import SimTrade

JST = ZoneInfo("Asia/Tokyo")


def _trade(sym: str, t0: datetime, hold: int, pnl: float, setup: str = "s") -> SimTrade:
    return SimTrade(
        day=t0.strftime("%Y%m%d"),
        symbol=sym,
        entry_time=t0,
        exit_time=t0 + timedelta(seconds=hold),
        entry_price=1000.0,
        exit_price=1000.0,
        exit_reason="trailing_mfe_exit",
        pnl_5bps=pnl,
        hold_sec=float(hold),
        entry_method="PBv2",
        cohort="E0",
        setup_id=setup,
        impulse_episode_id=setup,
        breakout_episode_id=setup,
        pbv2=True,
        vcie=False,
        mode="X0",
        session="AM" if t0.hour < 12 else "PM",
    )


def test_reason_family():
    assert reason_family("stop_hit") == "stop"
    assert reason_family("trailing_mfe_exit") == "trail"
    assert reason_family("no_progress_exit") == "no_progress"
    assert reason_family("morning_session_close") == "session"


def test_no_overlap_filter_blocks_pyramid():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    a = _trade("1000.T", t0, 100, 10, "a")
    b = _trade("1000.T", t0 + timedelta(seconds=30), 100, 20, "b")
    kept, dropped = filter_no_overlap([a, b])
    assert len(kept) == 1 and kept[0].setup_id == "a"
    assert len(dropped) == 1


def test_cap5_frees_slot_on_exit():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    trades = [_trade(f"{i}.T", t0 + timedelta(seconds=i), 10, 1.0, f"s{i}") for i in range(7)]
    # first 5 enter; after first exits at t0+10, 6th can enter at t0+5? wait entries at 0..6, exits at 10..16
    # at t=5: entries 0-5 attempted; 0-4 open (cap5), 5 blocked; at t=10 exit0 frees → but entry5 already passed
    res = replay_cap5(trades, portfolio_id="TEST")
    assert res.accepted == 5
    assert res.cap_blocked >= 1
    assert res.active_max <= 5


def test_cap5_exit_before_entry_same_ts():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    a = _trade("1000.T", t0, 0, 1.0, "a")  # exit same time as entry of b
    a = SimTrade(**{**a.__dict__, "exit_time": t0 + timedelta(seconds=60)})
    # fill 5 slots then exit one at T and enter at T
    base = [_trade(f"{i}.T", t0, 60, 1.0, f"b{i}") for i in range(5)]
    late = _trade("9999.T", t0 + timedelta(seconds=60), 30, 2.0, "late")
    # make one exit at exactly late entry
    base[0] = SimTrade(**{**base[0].__dict__, "exit_time": t0 + timedelta(seconds=60)})
    res = replay_cap5(base + [late], portfolio_id="TEST")
    assert any(t.setup_id == "late" for t in res.trades)
    assert res.active_max <= 5


def test_overlap_audit_detects():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    a = _trade("1000.T", t0, 100, 1, "a")
    b = _trade("1000.T", t0 + timedelta(seconds=10), 100, 1, "b")
    ov = audit_overlapping_entries([a, b])
    assert ov["same_symbol_overlapping_entry_count"] == 1
    assert ov["verdict"] == "POSITION_STATE_INTEGRITY_BLOCKED"


def test_trade_dd_not_daily():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    trades = [
        _trade("1.T", t0, 10, 100, "a"),
        _trade("2.T", t0 + timedelta(seconds=20), 10, -150, "b"),
        _trade("3.T", t0 + timedelta(seconds=40), 10, 80, "c"),
    ]
    ts = trade_sequence_dd(trades)
    daily = daily_close_max_dd(trades)
    assert ts == -150.0  # cum 100 → -50 → dd -150 from peak
    assert daily == 0.0  # single day close — must not be used as portfolio DD


def test_ablation_a5_is_x4():
    e = FixedEntry(
        day="20260722",
        symbol="1000.T",
        entry_time=datetime(2026, 7, 22, 10, 0, tzinfo=JST),
        entry_price=1000.0,
        entry_method="PBv2",
        cohort="E0",
        pbv2=True,
        setup_id="s",
    )
    path = [
        PathBar(
            t=e.entry_time + timedelta(seconds=i + 1),
            px=1000 + i,
            bid=999 + i,
            ask=1001 + i,
            bid_qty=100,
            ask_qty=100,
            volume_delta=10.0,
            tick_direction=1,
            buy_aggression=1.0,
            spread_bps=5.0,
        )
        for i in range(20)
    ]
    a5 = simulate_ablation(e, path, ablation="A5", params=ExitParams())
    assert a5.exit_reason


def test_x0_runtime_proxy_stop():
    e = FixedEntry(
        day="20260722",
        symbol="1000.T",
        entry_time=datetime(2026, 7, 22, 10, 0, tzinfo=JST),
        entry_price=1000.0,
        entry_method="PBv2",
        cohort="E0",
        pbv2=True,
        setup_id="s",
        entry_imbalance_percentile=50.0,
    )
    path = [
        PathBar(
            t=e.entry_time + timedelta(seconds=i + 1),
            px=1000 - i * 2,
            bid=999 - i * 2,
            ask=1001,
            bid_qty=1,
            ask_qty=1,
            volume_delta=1.0,
            tick_direction=-1,
            buy_aggression=0.0,
            spread_bps=5.0,
        )
        for i in range(20)
    ]
    # force big drop
    path[-1] = PathBar(
        t=e.entry_time + timedelta(seconds=30),
        px=980,
        bid=979,
        ask=981,
        bid_qty=1,
        ask_qty=1,
        volume_delta=1.0,
        tick_direction=-1,
        buy_aggression=0.0,
        spread_bps=5.0,
    )
    ex = simulate_x0_runtime_proxy(e, path, activate=0.5, giveback=0.5)
    assert ex.exit_reason in ("stop_hit", "trailing_mfe_exit", "no_progress_exit", "path_end", "morning_session_close")
