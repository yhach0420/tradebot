"""V1R-native Discord notification wiring — no strategy mutation."""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from notify.v1r_discord_routing import ROUTING_TABLE, V1RNotifyKind, publish_v1r
from small_paper.v1r_live_dual_lane import V1RLiveDualLane, reset_dual_lane_for_tests
from small_paper.v1r_native_entry_live import V1RNativeEntryLive, reset_native_entry_for_tests


def test_notify_does_not_pass_dry_run():
    src = inspect.getsource(V1RNativeEntryLive._notify)
    assert "dry_run" not in src
    assert "publish_v1r(" in src
    assert "except Exception: pass" not in src.replace(" ", "").replace("\n", "")


def test_routing_table_formal_channels():
    assert ROUTING_TABLE[V1RNotifyKind.ENTRY]["channel"] == "trade-entry"
    assert ROUTING_TABLE[V1RNotifyKind.EXPIRED]["channel"] == "trade-entry"
    assert ROUTING_TABLE[V1RNotifyKind.FILL]["channel"] == "trade-notify"
    assert ROUTING_TABLE[V1RNotifyKind.EXIT]["channel"] == "trade-notify"
    assert ROUTING_TABLE[V1RNotifyKind.PBV2_SHADOW]["channel"] == "trade-research"


def test_pending_expired_delivery_audit(tmp_path: Path, monkeypatch):
    reset_native_entry_for_tests()
    monkeypatch.setenv("V1R_EXIT_V2_LIVE_PRIMARY", "1")
    reset_dual_lane_for_tests()

    calls: list[dict] = []

    def _fake_publish(kind, payload, **kwargs):
        from notify.v1r_discord_routing import V1RNotifyResult, ROUTING_TABLE, V1RNotifyKind as K

        k = K(kind) if not isinstance(kind, K) else kind
        assert "dry_run" not in kwargs
        calls.append({"kind": k.value, "payload": dict(payload), "kwargs": dict(kwargs)})
        meta = ROUTING_TABLE[k]
        return V1RNotifyResult(
            kind=k.value,
            status="QUEUED",
            channel=meta["channel"],
            env_key=meta["env_keys"][0],
            queued=True,
            notification_id="test-nid",
        )

    monkeypatch.setattr("notify.v1r_discord_routing.publish_v1r", _fake_publish)

    eng = V1RNativeEntryLive(
        universe=["3103"],
        score_fn=lambda _f: 1.0,
        model_ser={},
        ready=True,
        trace_dir=tmp_path,
        trading_date="20260812",
    )
    # Bypass allocator: inject PENDING directly then expire
    from small_paper.v1r_native_entry_live import PendingOrder

    t0 = 1_700_000_000.0
    eng.pending["3103"] = PendingOrder(
        symbol="3103",
        signal_time=t0,
        limit_price=1000.0,
        score=0.9,
        rank=1,
        anchor="12:40",
        session="PM",
        date="20260812",
    )
    eng._notify(
        "ENTRY",
        {
            "kind": "V1R_ENTRY_PENDING",
            "symbol": "3103",
            "anchor": "12:40",
            "limit": 1000.0,
        },
    )
    eng.boards["3103"] = [
        {
            "t": t0,
            "bid": 1000.0,
            "ask": 1010.0,
            "bid_qty": 500.0,
            "ask_qty": 500.0,
            "special": False,
            "fresh_sec": 0.1,
        },
        {
            "t": t0 + 0.5,
            "bid": 1000.0,
            "ask": 1010.0,
            "bid_qty": 500.0,
            "ask_qty": 500.0,
            "special": False,
            "fresh_sec": 0.1,
        },
    ]
    eng.on_tick_fill_check(event_t=t0 + 1.0 + 1e-6)

    kinds = [c["kind"] for c in calls]
    assert "ENTRY" in kinds
    assert "EXPIRED" in kinds
    assert all(c["payload"].get("source") == "v1r_native" for c in calls)
    assert all(c["payload"].get("role") == "PAPER_PRIMARY" for c in calls)
    entry = next(c for c in calls if c["kind"] == "ENTRY")
    assert entry["payload"]["status"] == "PENDING"
    assert entry["payload"]["limit"] == 1000.0
    assert entry["payload"]["anchor"] == "12:40"

    delivery = (tmp_path / "v1r_discord_delivery.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(delivery) >= 2
    rows = [json.loads(x) for x in delivery]
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["ENTRY"]["channel"] == "trade-entry"
    assert by_kind["EXPIRED"]["channel"] == "trade-entry"
    assert by_kind["ENTRY"]["queued"] is True


def test_fill_routes_trade_notify(tmp_path: Path, monkeypatch):
    reset_native_entry_for_tests()
    monkeypatch.setenv("V1R_EXIT_V2_LIVE_PRIMARY", "0")  # dual off — fill notify only
    reset_dual_lane_for_tests()

    channels: list[str] = []

    def _fake_publish(kind, payload, **kwargs):
        from notify.v1r_discord_routing import V1RNotifyResult, ROUTING_TABLE, V1RNotifyKind as K

        k = K(kind) if not isinstance(kind, K) else kind
        channels.append(ROUTING_TABLE[k]["channel"])
        return V1RNotifyResult(
            kind=k.value,
            status="QUEUED",
            channel=ROUTING_TABLE[k]["channel"],
            env_key=ROUTING_TABLE[k]["env_keys"][0],
            queued=True,
        )

    monkeypatch.setattr("notify.v1r_discord_routing.publish_v1r", _fake_publish)
    eng = V1RNativeEntryLive(
        universe=["4680"],
        score_fn=lambda _f: 1.0,
        model_ser={},
        ready=True,
        trace_dir=tmp_path,
    )
    from small_paper.v1r_native_entry_live import PendingOrder

    t0 = 1_700_000_100.0
    eng.pending["4680"] = PendingOrder(
        symbol="4680",
        signal_time=t0,
        limit_price=2000.0,
        score=0.9,
        rank=1,
        anchor="12:40",
        session="PM",
        date="20260812",
    )
    eng.boards["4680"] = [
        {
            "t": t0,
            "bid": 2000.0,
            "ask": 1999.0,
            "bid_qty": 500.0,
            "ask_qty": 200.0,
            "special": False,
            "fresh_sec": 0.1,
        },
        {
            "t": t0 + 0.2,
            "bid": 2000.0,
            "ask": 1999.0,
            "bid_qty": 500.0,
            "ask_qty": 200.0,
            "special": False,
            "fresh_sec": 0.1,
        },
    ]
    done = eng.on_tick_fill_check(event_t=t0 + 0.3)
    assert any(d.get("kind") == "V1R_FILL" for d in done)
    assert "trade-notify" in channels


def test_pbv2_shadow_not_trade_notify():
    r = publish_v1r(
        V1RNotifyKind.PBV2_SHADOW,
        {"symbol": "9999", "note": "isolation", "role": "SHADOW_ONLY"},
        test_only=True,
        sync_http=False,
    )
    assert r.channel == "trade-research"
    assert r.channel != "trade-notify"


def test_dual_primary_exit_calls_notify(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("V1R_EXIT_V2_LIVE_PRIMARY", "1")
    reset_dual_lane_for_tests()
    reset_native_entry_for_tests()
    seen: list[str] = []

    def _fake_publish(kind, payload, **kwargs):
        from notify.v1r_discord_routing import V1RNotifyResult, ROUTING_TABLE, V1RNotifyKind as K

        k = K(kind) if not isinstance(kind, K) else kind
        seen.append(k.value)
        return V1RNotifyResult(
            kind=k.value,
            status="QUEUED",
            channel=ROUTING_TABLE[k]["channel"],
            env_key=ROUTING_TABLE[k]["env_keys"][0],
            queued=True,
        )

    monkeypatch.setattr("notify.v1r_discord_routing.publish_v1r", _fake_publish)
    dual = V1RLiveDualLane(trace_dir=tmp_path)
    from small_paper.v1r_live_dual_lane import LanePosition

    pos = LanePosition(
        symbol="5803",
        lane="primary",
        fill_time=1_700_000_200.0,
        fill_price=1500.0,
        fill_iso="t",
    )
    dual._close(
        pos,
        {
            "reason": "CONT_EXIT_600",
            "exit_time": 1_700_000_800.0,
            "exit_price": 1510.0,
            "triggered_guard": False,
            "extended": False,
            "exit_off": 600.0,
        },
        {},
    )
    assert "EXIT" in seen
    rows = [
        json.loads(x)
        for x in (tmp_path / "v1r_discord_delivery.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    assert rows and rows[-1]["channel"] == "trade-notify"
    assert rows[-1]["kind"] == "EXIT"
