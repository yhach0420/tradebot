"""EEC_v3 noise band / hysteresis unit tests."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from research.entry_exit_contract.constants import CONTRACT_VERSION
from research.entry_exit_contract.contract import EntryContract
from research.eec_noise_hysteresis.confirm import confirm_entry
from research.eec_noise_hysteresis.hysteresis import simulate_hysteresis_exit
from research.eec_noise_hysteresis.noise import compute_noise_band, iter_noise_grid
from research.price_flow_exit.path_mfe import PathBar

JST = ZoneInfo("Asia/Tokyo")


def _bar(t0: datetime, i: int, px: float, *, bid=None, ask=None, vd=1.0, td=1) -> PathBar:
    return PathBar(
        t=t0 + timedelta(seconds=i),
        px=px,
        bid=bid if bid is not None else px - 0.1,
        ask=ask if ask is not None else px + 0.1,
        bid_qty=200.0,
        ask_qty=200.0,
        volume_delta=vd,
        tick_direction=td,
        buy_aggression=0.6,
        spread_bps=2.0,
    )


def _ec2(t0: datetime | None = None) -> EntryContract:
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
        expected_horizon_sec=90.0,
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
        levels={
            "pullback_low": 990.0,
            "reclaim_level": 1000.0,
            "pre_pullback_high": 1020.0,
            "trend_reference": 1020.0,
        },
    )


def test_noise_grid_is_predefined_18():
    g = iter_noise_grid()
    assert len(g) == 18
    assert {"tick_mult", "range_mult", "spread_mult"} <= set(g[0])


def test_crossed_spread_omitted_tick_still_ok():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    # crossed ask < bid (observed in aggregate cache) — do not impute; use tick(+range)
    path = [
        PathBar(
            t=t0 + timedelta(seconds=i),
            px=1000.0 + i * 0.1,
            bid=1000.0,
            ask=999.0,
            bid_qty=100.0,
            ask_qty=100.0,
            volume_delta=1.0,
            tick_direction=1,
            buy_aggression=None,
            spread_bps=None,
        )
        for i in range(5)
    ]
    nb = compute_noise_band(path, 4, tick_mult=2, range_mult=0.35, spread_mult=1.0)
    assert nb["ok"] is True
    assert nb["spread_status"] == "NOT_EVALUABLE_SPREAD"
    assert nb["spread_band"] is None
    assert nb["noise_band"] == nb["tick_band"] or nb["noise_band"] == nb["range_band"]


def test_noise_band_short_range_falls_back_to_tick():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    path = [_bar(t0, 0, 1000.0)]
    nb = compute_noise_band(path, 0, tick_mult=1, range_mult=0.2, spread_mult=1.0)
    assert nb["ok"] is True
    assert nb["range_status"] == "NOT_EVALUABLE_RANGE"
    assert nb["noise_band"] == nb["tick_band"] or nb["noise_band"] == nb["spread_band"]


def test_confirm_n1_uses_confirmation_ask_not_signal_price():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    c = _ec2(t0)
    path = []
    for i in range(40):
        px = 1000.0 + min(i, 20) * 0.2
        path.append(_bar(t0, i, px, ask=px + 0.5, bid=px - 0.1, vd=10.0, td=1))
    conf = confirm_entry(c, path, mode="N1", tick_mult=1.0, range_mult=0.2, spread_mult=1.0)
    assert conf.confirmed
    assert conf.entry_time > c.entry_time
    assert conf.entry_price != c.entry_price
    idx = next(i for i, b in enumerate(path) if b.t == conf.entry_time)
    assert conf.entry_price == path[idx].ask


def test_warning_alone_does_not_exit():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    path = []
    for i in range(30):
        px = 1000.2 if i % 5 else 999.9
        path.append(_bar(t0, i, px, vd=5.0, td=1))
    hyst = simulate_hysteresis_exit(
        entry_time=t0,
        entry_price=1000.0,
        reclaim=1000.0,
        pullback_low=990.0,
        path=path,
        tick_mult=2.0,
        range_mult=0.35,
        spread_mult=1.0,
        immediate=False,
    )
    assert hyst.exit_reason in ("path_end", "session_close")


def test_invalidation_requires_persist_and_corroboration():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    path = []
    for i in range(10):
        path.append(_bar(t0, i, 1005.0, vd=20.0, td=1))
    for i in range(10, 25):
        path.append(_bar(t0, i, 995.0, vd=1.0, td=-1))
    hyst = simulate_hysteresis_exit(
        entry_time=t0,
        entry_price=1005.0,
        reclaim=1000.0,
        pullback_low=990.0,
        path=path,
        tick_mult=1.0,
        range_mult=0.2,
        spread_mult=1.0,
    )
    assert hyst.exit_reason == "hysteresis_invalidated"
    assert "INVALIDATED" in hyst.state_path
    assert "EXIT" in hyst.state_path


def test_immediate_exit_on_reclaim_break():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    path = [_bar(t0, 0, 1001.0), _bar(t0, 1, 999.0)]
    hyst = simulate_hysteresis_exit(
        entry_time=t0,
        entry_price=1001.0,
        reclaim=1000.0,
        pullback_low=990.0,
        path=path,
        tick_mult=1.0,
        range_mult=0.2,
        spread_mult=1.0,
        immediate=True,
    )
    assert hyst.exit_reason == "immediate_invalidation"
