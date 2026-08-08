"""E1_X5 D-MID_D4_H6 score wiring via ExtensionBus (no Shadow recompute)."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from research.integrated_directional_entry_exit_strategy.constants import (
    FIXED_CANDIDATE,
    FIXED_THRESHOLD,
)
from small_paper.e1_x5_dmid_score_provider import (
    KIND_MISSING,
    KIND_NO_SAMPLE,
    KIND_SCORE,
    NO_EVALUATION_MISSING_SCORE,
    DMidD4H6ScoreProvider,
    ScorePacket,
    validate_score_identity,
)
from small_paper.e1_x5_forward_shadow import (
    THRESHOLD,
    E1X5ForwardShadowSession,
)
from small_paper.extension_bus import ExtensionBus
from small_paper.core_runtime_mode import CoreRuntimeMode

JST = ZoneInfo("Asia/Tokyo")


def _payload(
    *,
    symbol: str = "5242.T",
    bid: float = 1000.0,
    ask: float = 1000.4,
    ts: datetime | None = None,
    seq: int = 1,
    vol: float = 10000.0,
    px: float | None = None,
    bid_qty: float = 1000.0,
    ask_qty: float = 1000.0,
) -> dict:
    ts = ts or datetime(2026, 7, 27, 13, 5, 0, tzinfo=JST)
    mid = px if px is not None else (bid + ask) / 2.0
    return {
        "Symbol": symbol.replace(".T", ""),
        "CurrentPrice": mid,
        "CurrentPriceTime": ts.isoformat(),
        "TradingVolume": vol,
        "sequence": seq,
        "Buy1": {"Price": bid, "Qty": bid_qty},
        "Sell1": {"Price": ask, "Qty": ask_qty},
    }


def _bus_with_e1(e1: E1X5ForwardShadowSession, provider: DMidD4H6ScoreProvider | None = None):
    state = SimpleNamespace(
        e1_x5_forward_shadow=e1,
        e1_x5_dmid_score_provider=provider,
        realtime_board_exit_shadow=None,
        classic_momentum_forward_shadow=None,
    )
    bus = ExtensionBus(
        mode=CoreRuntimeMode.FULL_EXTENSION,
        config=SimpleNamespace(),
        state=state,
        writer=None,
    )
    return bus, state


def test_model_loads_and_key_is_dmid_d4_h6():
    prov = DMidD4H6ScoreProvider.maybe_create()
    assert prov.ready is True
    assert prov.model is not None
    assert prov.model_key == FIXED_CANDIDATE
    assert abs(float(FIXED_THRESHOLD) - float(THRESHOLD)) < 1e-15


def test_provider_score_reaches_e1_via_extension_bus():
    prov = DMidD4H6ScoreProvider.maybe_create()
    e1 = E1X5ForwardShadowSession(enabled=True)
    bus, _ = _bus_with_e1(e1, prov)
    t0 = datetime(2026, 7, 27, 13, 0, 0, tzinfo=JST)
    # Warmup 60s+ then sample cadence
    for i in range(70):
        ts = t0 + timedelta(seconds=i)
        bus.on_push_tick(
            symbol="5242.T",
            payload=_payload(ts=ts, seq=i, vol=10000 + i * 10, bid=1000.0, ask=1000.4),
            price_ring=[],
        )
    assert e1.evaluated_count > 0 or e1.missing_score_count == 0
    # After warmup, at least one SCORE or evaluation should occur
    assert e1.evaluated_count > 0
    assert e1.summary()["evaluation_status"] == "EVALUATED"
    assert e1.summary()["submit"] == 0


def test_threshold_boundaries():
    e1 = E1X5ForwardShadowSession(enabled=True)
    ts = datetime(2026, 7, 27, 13, 10, 0, tzinfo=JST)
    # below
    r = e1.try_entry(
        symbol="A.T", ts=ts, bid=1000.0, ask=1000.4,
        score=THRESHOLD - 1e-9, spread_bps=4.0, sample_id="b1",
    )
    assert r == "SCORE_BELOW_THRESHOLD"
    # exact match → ENTRY (score >= threshold)
    r = e1.try_entry(
        symbol="B.T", ts=ts, bid=1000.0, ask=1000.4,
        score=THRESHOLD, spread_bps=4.0, sample_id="b2",
    )
    assert r is None
    assert "B.T" in e1.positions
    assert e1.positions["B.T"].entry_ask == 1000.4
    # above
    r = e1.try_entry(
        symbol="C.T", ts=ts, bid=1000.0, ask=1000.4,
        score=THRESHOLD + 1e-6, spread_bps=4.0, sample_id="b3",
    )
    assert r is None


def test_spread_5bps_boundary():
    e1 = E1X5ForwardShadowSession(enabled=True)
    ts = datetime(2026, 7, 27, 13, 10, 0, tzinfo=JST)
    # ask=1000, bid such that spread == 5bps: (ask-bid)/ask*10000 = 5 → bid = ask*(1-5/10000)
    ask = 1000.0
    bid_exact = ask * (1.0 - 5.0 / 10000.0)
    r = e1.try_entry(
        symbol="S1.T", ts=ts, bid=bid_exact, ask=ask,
        score=THRESHOLD, spread_bps=5.0, sample_id="s1",
    )
    assert r is None
    r = e1.try_entry(
        symbol="S2.T", ts=ts, bid=bid_exact - 0.1, ask=ask,
        score=THRESHOLD, spread_bps=5.0 + 1e-6, sample_id="s2",
    )
    assert r == "SPREAD_OVER_5BPS"


def test_canonical_ask_entry_bid_exit():
    e1 = E1X5ForwardShadowSession(enabled=True)
    ts = datetime(2026, 7, 27, 13, 10, 0, tzinfo=JST)
    e1.try_entry(
        symbol="X.T", ts=ts, bid=1000.0, ask=1000.5,
        score=THRESHOLD, spread_bps=5.0, sample_id="x1",
    )
    assert e1.positions["X.T"].entry_ask == 1000.5
    e1.on_quote(symbol="X.T", ts=ts + timedelta(seconds=1), bid=999.0, ask=999.5, score=None)
    # STOP at -15bps from entry_ask 1000.5 → bid <= 1000.5 * (1 - 15/10000)
    stop_bid = 1000.5 * (1.0 - 15.0 / 10000.0)
    e1.on_quote(symbol="X.T", ts=ts + timedelta(seconds=2), bid=stop_bid, ask=stop_bid + 0.5, score=None)
    assert len(e1.exits) == 1
    assert e1.exits[0]["exit_bid"] == stop_bid
    assert e1.exits[0]["exit_reason"] == "STOP"


def test_missing_score_not_confused_with_zero_entry():
    e1 = E1X5ForwardShadowSession(enabled=True)
    ts = datetime(2026, 7, 27, 13, 10, 0, tzinfo=JST)
    e1.on_missing_score(symbol="M.T", ts=ts, reason=NO_EVALUATION_MISSING_SCORE)
    s = e1.summary()
    assert s["evaluated_count"] == 0
    assert s["missing_score_count"] == 1
    assert s["entries_n"] == 0
    assert s["trades"] == 0
    assert s["evaluation_status"] == "NO_EVALUATION_MISSING_SCORE"
    assert e1.candidates[0]["entry_decision"] == "NO_EVALUATION"


def test_identity_fail_close():
    ts = datetime(2026, 7, 27, 13, 10, 0, tzinfo=JST)
    pkt = ScorePacket(
        score=0.9,
        symbol="5242.T",
        day="20260727",
        event_time=ts,
        event_sequence=10,
        sample_id="20260727|5242.T|10|0|REGULAR",
        snapshot_id="20260727|5242.T|10|0|REGULAR",
        spread_bps=3.0,
        bid=1000.0,
        ask=1000.3,
        mid=1000.15,
    )
    assert validate_score_identity(packet=pkt, symbol="9999.T", event_time=ts) == "SYMBOL_MISMATCH"
    assert (
        validate_score_identity(
            packet=pkt, symbol="5242.T", event_time=ts + timedelta(seconds=5)
        )
        == "EVENT_TIME_MISMATCH"
    )
    assert (
        validate_score_identity(
            packet=pkt, symbol="5242.T", event_time=ts, snapshot_id="other"
        )
        == "SNAPSHOT_MISMATCH"
    )
    e1 = E1X5ForwardShadowSession(enabled=True)
    e1.on_identity_fail(symbol="5242.T", ts=ts, reason="SYMBOL_MISMATCH")
    assert e1.identity_fail_count == 1
    assert e1.evaluated_count == 0


def test_duplicate_event_evaluated_once():
    e1 = E1X5ForwardShadowSession(enabled=True)
    ts = datetime(2026, 7, 27, 13, 10, 0, tzinfo=JST)
    kwargs = dict(
        symbol="D.T", ts=ts, bid=1000.0, ask=1000.4,
        score=THRESHOLD, spread_bps=4.0, sample_id="dup1",
    )
    assert e1.try_entry(**kwargs) is None
    assert e1.try_entry(**kwargs) == "DUPLICATE_EVENT"
    assert e1.evaluated_count == 1
    assert e1.duplicate_eval_suppressed == 1
    assert len(e1.entries) == 1


def test_extension_bus_missing_score_path():
    # Provider forced not ready → sample due after warmup yields MISSING
    class _Broken(DMidD4H6ScoreProvider):
        def observe(self, **kwargs):
            from small_paper.e1_x5_dmid_score_provider import ScoreObserveResult

            return ScoreObserveResult(
                kind=KIND_MISSING,
                reason="MODEL_UNAVAILABLE",
                symbol=kwargs.get("symbol") or "",
                event_time=datetime(2026, 7, 27, 13, 10, 0, tzinfo=JST),
                event_sequence=1,
                snapshot_id="snap",
            )

    e1 = E1X5ForwardShadowSession(enabled=True)
    bus, _ = _bus_with_e1(e1, _Broken(model=None))
    bus.on_push_tick(symbol="5242.T", payload=_payload(), price_ring=[])
    assert e1.missing_score_count == 1
    assert e1.evaluated_count == 0
    assert e1.summary()["evaluation_status"] == "NO_EVALUATION_MISSING_SCORE"


def test_no_entry_score_v2_in_packet_path():
    prov = DMidD4H6ScoreProvider.maybe_create()
    # Score a synthetic warmed stream and ensure packet model_key is D-MID only
    t0 = datetime(2026, 7, 27, 13, 0, 0, tzinfo=JST)
    last = None
    for i in range(70):
        last = prov.observe(
            symbol="5242.T",
            payload=_payload(ts=t0 + timedelta(seconds=i), seq=i, vol=10000 + i * 10),
        )
    # Find any SCORE in a fresh loop collecting
    got = None
    for i in range(70, 90):
        r = prov.observe(
            symbol="5242.T",
            payload=_payload(ts=t0 + timedelta(seconds=i), seq=i, vol=10000 + i * 10),
        )
        if r.kind == KIND_SCORE:
            got = r
            break
    assert got is not None and got.packet is not None
    assert got.packet.model_key == FIXED_CANDIDATE
    assert not hasattr(got.packet, "entry_score_v2")
    assert 0.0 < got.packet.score < 1.0


def test_shadow_events_and_summary_counters():
    e1 = E1X5ForwardShadowSession(enabled=True)
    ts = datetime(2026, 7, 27, 13, 10, 0, tzinfo=JST)
    e1.try_entry(
        symbol="E.T", ts=ts, bid=1000.0, ask=1000.4,
        score=THRESHOLD, spread_bps=3.0, sample_id="e1",
    )
    e1.on_missing_score(symbol="F.T", ts=ts)
    s = e1.summary()
    assert s["evaluated_count"] == 1
    assert s["missing_score_count"] == 1
    assert s["candidate_count"] >= 2
    assert s["entries_n"] == 1
    assert s["submit"] == s["cancel"] == s["live_order"] == 0
