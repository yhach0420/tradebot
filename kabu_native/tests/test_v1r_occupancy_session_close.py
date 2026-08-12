"""Runtime occupancy release + Frozen session-close (no Strategy / threshold change)."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
for _k in (
    "KABU_V1R_ENTRY_WEBHOOK_URL",
    "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
    "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
):
    os.environ.pop(_k, None)

from notify.v1r_discord_routing import ROUTING_TABLE, V1RNotifyKind
from small_paper.v1r_live_dual_lane import (
    V1RLiveDualLane,
    canonical_symbol_key,
    ensure_dual_lane,
    reset_dual_lane_for_tests,
    session_end_for_position,
)
from small_paper.v1r_native_entry_live import (
    PendingOrder,
    V1RNativeEntryLive,
    reset_native_entry_for_tests,
    set_native_entry,
)
from small_paper.v1r_primary_runtime import POSITION_CAP

JST = ZoneInfo("Asia/Tokyo")


def _eng() -> V1RNativeEntryLive:
    return V1RNativeEntryLive(
        universe=["6098", "8050", "5985", "5803", "285A"],
        score_fn=lambda e: 0.0,
        model_ser={},
    )


def _payload(t: float, *, bid: float, ask: float, bq: float = 200.0, aq: float = 200.0) -> dict:
    return {
        "event_time": t,
        "CurrentPriceTime": datetime.fromtimestamp(t, JST).isoformat(timespec="milliseconds"),
        "Buy1": {"Price": bid, "Qty": bq},
        "Sell1": {"Price": ask, "Qty": aq},
        "CurrentPrice": (bid + ask) / 2.0,
        "board_age_sec": 0.0,
        "fresh_sec": 0.0,
        "SpecialQuote": False,
    }


def _fill_via_native(eng: V1RNativeEntryLive, *, symbol: str, t0: float, px: float, session: str) -> None:
    po = PendingOrder(
        symbol=canonical_symbol_key(symbol),
        signal_time=t0 - 0.5,
        limit_price=px,
        score=1.0,
        rank=1,
        anchor="test",
        session=session,
        date=datetime.fromtimestamp(t0, JST).strftime("%Y%m%d"),
    )
    eng.pending[po.symbol] = po
    eng._promote_fill(po, {"fill_price": px, "fill_t": t0})


def _boot(tmp_path: Path) -> tuple[V1RNativeEntryLive, V1RLiveDualLane]:
    reset_native_entry_for_tests()
    reset_dual_lane_for_tests()
    eng = _eng()
    eng.trace_dir = tmp_path
    set_native_entry(eng)
    dual = ensure_dual_lane(trace_dir=tmp_path)
    assert dual is not None
    return eng, dual


def test_canonical_6098_and_6098t_same_key():
    assert canonical_symbol_key("6098") == canonical_symbol_key("6098.T") == "6098"


def test_fill_increments_native_open_and_invariant(tmp_path: Path):
    eng, dual = _boot(tmp_path)
    t0 = datetime(2026, 8, 12, 9, 5, tzinfo=JST).timestamp()
    _fill_via_native(eng, symbol="6098.T", t0=t0, px=16655.0, session="AM")
    assert eng.open_n == 1
    assert eng.pending_n == 0
    assert eng.exposure() == 1
    assert dual.open_n("primary") == 1
    assert dual.open_n("control") == 1
    inv = eng.check_occupancy_invariant(dual=dual, event="FILL")
    assert inv["ok"] is True
    assert inv["native_open"] == 1
    assert inv["primary_open"] == 1


def test_primary_actual_exit_releases_native_open(tmp_path: Path):
    eng, dual = _boot(tmp_path)
    t0 = datetime(2026, 8, 12, 9, 15, tzinfo=JST).timestamp()
    px = 1000.0
    _fill_via_native(eng, symbol="6098", t0=t0, px=px, session="AM")
    assert eng.open_n == 1
    for s in range(0, 620, 5):
        bid = px * (1.0 + 0.001 * min(s, 100) / 100.0)
        dual.on_tick(
            symbol="6098.T",
            payload=_payload(t0 + s, bid=bid, ask=bid + 1.0),
            event_t=t0 + s,
        )
    assert dual.open_n("primary") == 0
    assert dual.open_n("control") == 0
    assert eng.open_n == 0
    assert eng.exposure() == 0
    releases = [e for e in eng.events if e.get("kind") == "V1R_NATIVE_PRIMARY_EXIT_RELEASE"]
    assert releases
    rec = releases[-1]
    assert rec["symbol"] == "6098"
    assert rec["native_open_before"] == 1
    assert rec["native_open_after"] == 0
    assert rec["native_exposure_before"] == 1
    assert rec["native_exposure_after"] == 0
    assert rec["reason"]
    assert rec["primary_exit_time"]
    assert rec["duplicate"] is False
    inv = [e for e in eng.events if e.get("kind") == "V1R_OCCUPANCY_INVARIANT"]
    assert inv and inv[-1]["ok"] is True


def test_control_exit_does_not_release_native_while_primary_open(tmp_path: Path):
    eng, dual = _boot(tmp_path)
    t0 = datetime(2026, 8, 12, 9, 25, tzinfo=JST).timestamp()
    px = 1000.0
    _fill_via_native(eng, symbol="6098", t0=t0, px=px, session="AM")
    for s in range(0, 620, 5):
        ret = min(80.0, 10.0 + s * 0.12)
        bid = px * (1.0 + ret / 10000.0)
        dual.on_tick(
            symbol="6098",
            payload=_payload(t0 + s, bid=bid, ask=bid + 1.0, bq=400.0, aq=100.0),
            event_t=t0 + s,
        )
    assert dual.open_n("control") == 0
    assert dual.open_n("primary") == 1
    assert eng.open_n == 1
    assert eng.exposure() == 1
    native_releases = [e for e in eng.events if e.get("kind") == "V1R_NATIVE_PRIMARY_EXIT_RELEASE"]
    assert native_releases == []


def test_expired_decrements_pending_not_native_open(tmp_path: Path):
    eng, dual = _boot(tmp_path)
    t0 = datetime(2026, 8, 12, 9, 5, tzinfo=JST).timestamp()
    po = PendingOrder(
        symbol="8050",
        signal_time=t0,
        limit_price=1.0,
        score=1.0,
        rank=1,
        anchor="09:05",
        session="AM",
        date="20260812",
    )
    eng.pending[po.symbol] = po
    assert eng.pending_n == 1
    assert eng.exposure() == 1
    done = eng.on_tick_fill_check(event_t=t0 + 1.0, payload=_payload(t0 + 1.0, bid=1.0, ask=2.0))
    assert any(e.get("kind") == "V1R_EXPIRED" for e in done)
    assert eng.pending_n == 0
    assert eng.open_n == 0
    assert eng.exposure() == 0
    assert dual.open_n("primary") == 0


def test_duplicate_close_idempotent(tmp_path: Path):
    eng, dual = _boot(tmp_path)
    t0 = datetime(2026, 8, 12, 9, 5, tzinfo=JST).timestamp()
    _fill_via_native(eng, symbol="6098.T", t0=t0, px=1000.0, session="AM")
    pos = dual.primary["6098"]
    decision = {
        "exit": True,
        "lane": "primary",
        "symbol": "6098",
        "reason": "CONT_EXIT_600",
        "triggered_guard": False,
        "extended": False,
        "exit_off": 600.0,
        "exit_time": t0 + 600.0,
        "exit_price": 1000.0,
    }
    dual._close(pos, decision, {})
    assert eng.open_n == 0
    assert dual.stats.primary_exits == 1
    dual._close(pos, decision, {})
    assert dual.stats.primary_exits == 1
    rec = eng.note_primary_exit("6098.T", exit_time=t0 + 600.0, reason="CONT_EXIT_600")
    assert rec["duplicate"] is True
    assert rec["native_open_before"] == rec["native_open_after"] == 0
    assert eng.open_n == 0


def test_native_open_cap_with_primary_empty_fails(tmp_path: Path):
    eng, dual = _boot(tmp_path)
    for i, s in enumerate(["6098", "8050", "5985", "5803", "285A"]):
        eng.open_symbols.add(s)
    assert eng.open_n == POSITION_CAP
    assert dual.open_n("primary") == 0
    inv = eng.check_occupancy_invariant(dual=dual, event="DESYNC")
    assert inv["ok"] is False
    assert inv["cap_desync"] is True
    assert dual.fail_closed is True
    assert dual.fail_reason == "OCCUPANCY_INVARIANT"


def _session_case(tmp_path: Path, *, hour: int, minute: int, session: str, close_hm: tuple[int, int]):
    eng, dual = _boot(tmp_path)
    t0 = datetime(2026, 8, 12, hour, minute, tzinfo=JST).timestamp()
    px = 5000.0
    last_bid = 4990.0
    _fill_via_native(eng, symbol="6098", t0=t0, px=px, session=session)
    for s in range(0, 12, 2):
        dual.on_tick(
            symbol="6098.T",
            payload=_payload(t0 + s, bid=last_bid, ask=last_bid + 10.0),
            event_t=t0 + s,
        )
    assert dual.open_n("primary") == 1
    assert dual.open_n("control") == 1
    assert eng.open_n == 1
    t_end = datetime(2026, 8, 12, close_hm[0], close_hm[1], tzinfo=JST).timestamp()
    assert session_end_for_position(date="20260812", session=session, fill_time=t0) == t_end
    exits = dual.maybe_session_close(event_t=t_end, session=session)
    prim = [e for e in exits if e.get("lane") == "primary"]
    ctrl = [e for e in exits if e.get("lane") == "control"]
    assert prim and ctrl
    assert prim[0]["reason"] == "SESSION_CLOSE"
    assert ctrl[0]["reason"] == "SESSION_CLOSE"
    assert float(prim[0]["exit_time"]) <= t_end + 1e-9
    assert float(prim[0]["exit_price"]) == last_bid
    assert dual.open_n("primary") == 0
    assert dual.open_n("control") == 0
    assert eng.open_n == 0
    assert eng.pending_n == 0
    assert eng.exposure() == 0
    exit_notifies = [n for n in eng.notify_sink if n.get("kind") == "EXIT"]
    assert exit_notifies
    assert ROUTING_TABLE[V1RNotifyKind.EXIT]["channel"] == "trade-notify"
    # Control must not emit native EXIT notify (Primary only).
    assert sum(1 for n in eng.notify_sink if n.get("kind") == "EXIT") == 1
    return {
        "primary": prim[0],
        "control": ctrl[0],
        "routing_channel": ROUTING_TABLE[V1RNotifyKind.EXIT]["channel"],
    }


def test_case_a_am_session_close(tmp_path: Path):
    out = _session_case(tmp_path, hour=11, minute=25, session="AM", close_hm=(11, 30))
    assert out["primary"]["reason"] == "SESSION_CLOSE"


def test_case_b_pm_session_close(tmp_path: Path):
    out = _session_case(tmp_path, hour=14, minute=55, session="PM", close_hm=(15, 0))
    assert out["control"]["reason"] == "SESSION_CLOSE"


def test_session_close_does_not_use_future_quote(tmp_path: Path):
    eng, dual = _boot(tmp_path)
    t0 = datetime(2026, 8, 12, 11, 25, tzinfo=JST).timestamp()
    t_end = datetime(2026, 8, 12, 11, 30, tzinfo=JST).timestamp()
    _fill_via_native(eng, symbol="6098", t0=t0, px=5000.0, session="AM")
    dual.on_tick(
        symbol="6098",
        payload=_payload(t0 + 5.0, bid=4990.0, ask=5000.0),
        event_t=t0 + 5.0,
    )
    # Future quote must not be consumed even if passed to on_tick after sess_end.
    dual.on_tick(
        symbol="6098",
        payload=_payload(t_end + 60.0, bid=1.0, ask=2.0),
        event_t=t_end + 60.0,
    )
    pos = dual.primary["6098"]
    assert pos.closed
    assert pos.exit_reason == "SESSION_CLOSE"
    assert pos.exit_price == 4990.0
    assert pos.exit_time <= t_end + 1e-9
    assert 1.0 not in pos.bid
