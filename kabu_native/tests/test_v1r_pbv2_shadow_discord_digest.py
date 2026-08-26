"""PBv2 SHADOW Discord must be digest-aggregated, never per-PUSH."""
from __future__ import annotations

import time
from pathlib import Path

from notify.v1r_discord_routing import ROUTING_TABLE, V1RNotifyKind
from small_paper.v1r_pbv2_shadow_discord_digest import (
    PBv2ShadowDiscordDigest,
    reset_pbv2_shadow_discord_digest_for_tests,
)


def test_pbv2_routes_research_only():
    assert ROUTING_TABLE[V1RNotifyKind.PBV2_SHADOW]["channel"] == "trade-research"
    assert ROUTING_TABLE[V1RNotifyKind.ENTRY]["channel"] == "trade-entry"
    assert ROUTING_TABLE[V1RNotifyKind.FILL]["channel"] == "trade-notify"


def test_digest_suppresses_per_accept_and_flushes_once(tmp_path: Path, monkeypatch):
    reset_pbv2_shadow_discord_digest_for_tests()
    published: list[dict] = []

    def _fake_publish(kind, payload, **kwargs):
        from notify.v1r_discord_routing import V1RNotifyResult, ROUTING_TABLE, V1RNotifyKind as K

        k = K(kind) if not isinstance(kind, K) else kind
        published.append({"kind": k.value, "payload": dict(payload)})
        meta = ROUTING_TABLE[k]
        return V1RNotifyResult(
            kind=k.value,
            status="QUEUED",
            channel=meta["channel"],
            env_key=meta["env_keys"][0],
            queued=True,
        )

    monkeypatch.setattr("notify.v1r_discord_routing.publish_v1r", _fake_publish)
    d = PBv2ShadowDiscordDigest(trace_dir=tmp_path, interval_sec=300)
    # 50 divert attempts (mix admit/dup/cap) — no Discord yet
    for i in range(50):
        if i == 0:
            admit = {"admitted": True, "reason": ""}
        elif i < 10:
            admit = {"admitted": False, "reason": "already_open"}
        else:
            admit = {"admitted": False, "reason": "shadow_cap"}
        out = d.note_accept_attempt(
            symbol="3103" if i < 5 else f"{1000+i}",
            shadow_admit=admit,
            entry_price=1000.0 + i,
            trading_date="20260812",
            open_n=4,
            cap=4,
        )
        assert out.get("flushed") is False
    assert published == []
    assert d.evaluated == 50
    assert d.accepted == 1
    assert d.already_open == 9
    assert d.cap_blocked == 40

    flushed = d.maybe_flush(trading_date="20260812", open_n=4, cap=4, force=True)
    assert flushed["flushed"] is True
    assert len(published) == 1
    payload = published[0]["payload"]
    assert published[0]["kind"] == "PBV2_SHADOW"
    assert payload["digest"] is True
    assert payload["evaluated"] == 50
    assert payload["accepted"] == 1
    assert payload["hypothetical_fills"] == 1
    assert payload["role"] == "SHADOW_ONLY"
    assert "3103" in payload["symbols"]
    assert (tmp_path / "v1r_pbv2_shadow_discord_digest.jsonl").is_file()


def test_pilot_no_longer_calls_immediate_publish_v1r_pbv2():
    src = Path(__file__).resolve().parents[1] / "src" / "small_paper" / "pilot_runner.py"
    text = src.read_text(encoding="utf-8")
    # Immediate per-accept PBV2 publish block must be gone
    assert "status\": \"SHADOW_ACCEPT\"" not in text
    assert "get_pbv2_shadow_discord_digest" in text
    assert "note_accept_attempt" in text


def test_window_roll_flushes(tmp_path: Path, monkeypatch):
    reset_pbv2_shadow_discord_digest_for_tests()
    published: list = []

    def _fake_publish(kind, payload, **kwargs):
        from notify.v1r_discord_routing import V1RNotifyResult, ROUTING_TABLE, V1RNotifyKind as K

        k = K(kind) if not isinstance(kind, K) else kind
        published.append(dict(payload))
        return V1RNotifyResult(
            kind=k.value,
            status="QUEUED",
            channel=ROUTING_TABLE[k]["channel"],
            env_key=ROUTING_TABLE[k]["env_keys"][0],
            queued=True,
        )

    monkeypatch.setattr("notify.v1r_discord_routing.publish_v1r", _fake_publish)
    d = PBv2ShadowDiscordDigest(trace_dir=tmp_path, interval_sec=300)
    d.window_id = "20260812|13:00"
    d.window_started_mono = time.monotonic() - 1.0
    d.note_accept_attempt(
        symbol="4680",
        shadow_admit={"admitted": True, "reason": ""},
        trading_date="20260812",
    )
    # Force window id mismatch on next check
    d.window_id = "20260812|12:55"  # older than current → due
    out = d.maybe_flush(trading_date="20260812")
    assert out["flushed"] is False
    assert published == []



def test_am_pm_session_summary_once_each(tmp_path: Path, monkeypatch):
    reset_pbv2_shadow_discord_digest_for_tests()
    published: list[dict] = []

    def _fake_publish(kind, payload, **kwargs):
        from notify.v1r_discord_routing import V1RNotifyResult, ROUTING_TABLE, V1RNotifyKind as K

        k = K(kind) if not isinstance(kind, K) else kind
        published.append({"kind": k.value, "payload": dict(payload)})
        meta = ROUTING_TABLE[k]
        return V1RNotifyResult(
            kind=k.value,
            status="QUEUED",
            channel=meta["channel"],
            env_key=meta["env_keys"][0],
            queued=True,
        )

    monkeypatch.setattr("notify.v1r_discord_routing.publish_v1r", _fake_publish)
    d = PBv2ShadowDiscordDigest(trace_dir=tmp_path, interval_sec=300)
    d.note_accept_attempt(
        symbol="3103",
        shadow_admit={"admitted": True, "reason": ""},
        trading_date="20260812",
        open_n=1,
        cap=4,
    )
    am = d.flush_session_summary(
        session_kind="AM", trading_date="20260812", open_n=1, cap=4
    )
    assert am["flushed"] is True
    am2 = d.flush_session_summary(
        session_kind="AM", trading_date="20260812", open_n=1, cap=4
    )
    assert am2["flushed"] is False
    d.note_accept_attempt(
        symbol="4680",
        shadow_admit={"admitted": True, "reason": ""},
        trading_date="20260812",
        open_n=1,
        cap=4,
    )
    pm = d.flush_session_summary(
        session_kind="PM", trading_date="20260812", open_n=1, cap=4
    )
    assert pm["flushed"] is True
    assert len(published) == 2
    assert published[0]["payload"]["session_kind"] == "AM"
    assert published[1]["payload"]["session_kind"] == "PM"
    for row in published:
        p = row["payload"]
        assert p["session_summary"] is True
        for key in (
            "evaluated",
            "accepted",
            "newly_admitted",
            "already_open",
            "shadow_cap",
            "shadow_trade_count",
            "wins",
            "losses",
            "pnl",
            "pf",
        ):
            assert key in p


def test_publish_processing_error_is_immediate(tmp_path: Path, monkeypatch):
    reset_pbv2_shadow_discord_digest_for_tests()
    published: list[dict] = []

    def _fake_publish(kind, payload, **kwargs):
        from notify.v1r_discord_routing import V1RNotifyResult, ROUTING_TABLE, V1RNotifyKind as K

        k = K(kind) if not isinstance(kind, K) else kind
        published.append(dict(payload))
        meta = ROUTING_TABLE[k]
        return V1RNotifyResult(
            kind=k.value,
            status="QUEUED",
            channel=meta["channel"],
            env_key=meta["env_keys"][0],
            queued=True,
        )

    monkeypatch.setattr("notify.v1r_discord_routing.publish_v1r", _fake_publish)
    d = PBv2ShadowDiscordDigest(trace_dir=tmp_path, interval_sec=300)
    out = d.publish_processing_error(where="unit", error="boom", symbol="3103")
    assert out.get("discord_status") == "QUEUED"
    assert len(published) == 1
    assert published[0]["status"] == "ERROR"
    assert published[0]["digest"] is False


def test_session_end_publishes_pbv2_shadow_summary(tmp_path: Path, monkeypatch):
    from small_paper.discord_notifier import notify_discord_session_end
    from small_paper.v1r_pbv2_shadow_discord_digest import (
        get_pbv2_shadow_discord_digest,
        publish_pbv2_shadow_session_summary_for_finalize,
    )

    reset_pbv2_shadow_discord_digest_for_tests()
    published: list[dict] = []

    def _fake_publish(kind, payload, **kwargs):
        from notify.v1r_discord_routing import V1RNotifyResult, ROUTING_TABLE, V1RNotifyKind as K

        k = K(kind) if not isinstance(kind, K) else kind
        published.append({"kind": k.value, "payload": dict(payload)})
        meta = ROUTING_TABLE[k]
        return V1RNotifyResult(
            kind=k.value,
            status="QUEUED",
            channel=meta["channel"],
            env_key=meta["env_keys"][0],
            queued=True,
        )

    monkeypatch.setattr("notify.v1r_discord_routing.publish_v1r", _fake_publish)
    monkeypatch.setattr(
        "small_paper.shadow_summary_runtime_hook.enqueue_shadow_summary_for_session",
        lambda *a, **k: None,
    )
    d = get_pbv2_shadow_discord_digest(trace_dir=tmp_path)
    d.note_accept_attempt(
        symbol="3103",
        shadow_admit={"admitted": True, "reason": ""},
        trading_date="20260812",
        cap=4,
    )
    notify_discord_session_end(
        None,
        events=[],
        summary={
            "trading_date": "20260812",
            "am_pm_session": {"kind": "am"},
            "stop_reason": "morning_session_close",
        },
        native_root=tmp_path,
        output_dir=tmp_path,
    )
    kinds = [p["payload"].get("session_kind") for p in published if p["kind"] == "PBV2_SHADOW"]
    assert kinds == ["AM"]
    # second AM finalize must not duplicate
    n = len(published)
    notify_discord_session_end(
        None,
        events=[],
        summary={
            "trading_date": "20260812",
            "am_pm_session": {"kind": "am"},
            "stop_reason": "morning_session_close",
        },
        native_root=tmp_path,
        output_dir=tmp_path,
    )
    assert len(published) == n
    pm = publish_pbv2_shadow_session_summary_for_finalize(
        {"trading_date": "20260812", "am_pm_session": {"kind": "pm"}},
        output_dir=tmp_path,
    )
    assert pm.get("flushed") is True
