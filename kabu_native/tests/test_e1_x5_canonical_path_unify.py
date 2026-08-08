"""Regression: E1_X5 canonical path unification (decision_core only)."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from small_paper.e1_x5_forward_shadow import THRESHOLD
from small_paper.e1_x5_g1_confirmation_guard import E1X5GuardSession, GuardVariant

JST = ZoneInfo("Asia/Tokyo")


def test_guard_process_delegates_to_decision_core_only():
    import inspect

    from small_paper import e1_x5_g1_guard_process as gp

    src = inspect.getsource(gp.process_e1_x5_guard_event)
    assert "process_e1_x5_event" in src
    assert "provider.observe" not in src
    assert "KIND_SCORE" not in src or "process_e1_x5_event" in src


def test_standalone_base_and_g1_guard_off_ledger_hash_match(tmp_path, monkeypatch):
    """Same frozen synthetic events → identical trade ledger hash."""
    from small_paper.e1_x5_canonical_replay import trade_ledger_hash
    from small_paper.e1_x5_decision_core import process_e1_x5_event
    from small_paper.e1_x5_dmid_score_provider import DMidD4H6ScoreProvider
    from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession
    from small_paper.e1_x5_g1_confirmation_guard import E1X5GuardSession, GuardVariant
    from small_paper.e1_x5_g1_guard_process import process_e1_x5_guard_event

    # Tiny synthetic stream — provider may not score; hash equality still required
    events = []
    t0 = datetime(2026, 7, 23, 10, 0, 0, tzinfo=JST)
    for i in range(5):
        ts = t0 + timedelta(seconds=i)
        events.append(
            {
                "symbol": "7203.T",
                "payload": {
                    "Symbol": "7203",
                    "CurrentPrice": 2800.0,
                    "CurrentPriceTime": ts.isoformat(),
                    "Buy1": {"Price": 2799.0, "Qty": 1000},
                    "Sell1": {"Price": 2801.0, "Qty": 1000},
                    "TradingVolume": 100000,
                },
                "recv_ts": ts,
                "sequence": i + 1,
                "event_id": f"e{i}",
            }
        )

    prov_a = DMidD4H6ScoreProvider.maybe_create()
    prov_b = DMidD4H6ScoreProvider.maybe_create()
    if prov_a is None or prov_b is None:
        pytest.skip("score provider unavailable")

    sess_a = E1X5ForwardShadowSession(enabled=True)
    sess_b = E1X5GuardSession(enabled=True, variant=GuardVariant.BASE, state_rearm=False)
    for ev in events:
        process_e1_x5_event(
            provider=prov_a,
            session=sess_a,
            symbol=ev["symbol"],
            payload=ev["payload"],
            day="20260723",
            event_sequence=ev["sequence"],
            event_id=ev["event_id"],
            decision_time=ev["recv_ts"],
        )
        process_e1_x5_guard_event(
            provider=prov_b,
            session=sess_b,
            symbol=ev["symbol"],
            payload=ev["payload"],
            day="20260723",
            event_sequence=ev["sequence"],
            event_id=ev["event_id"],
            decision_time=ev["recv_ts"],
        )
    ha = trade_ledger_hash(sess_a.exits)
    hb = trade_ledger_hash(sess_b.exits)
    assert ha == hb


def test_window_builder_splits_on_gap_not_day_label():
    from small_paper.e1_x5_canonical_replay import ValidWindow, build_valid_windows
    from small_paper.replay_session_normalizer import NormalizedEvent

    t0 = datetime(2026, 7, 23, 10, 0, 0, tzinfo=JST)
    t1 = datetime(2026, 7, 23, 10, 1, 0, tzinfo=JST)
    t2 = datetime(2026, 7, 23, 12, 30, 0, tzinfo=JST)
    t3 = datetime(2026, 7, 23, 12, 31, 0, tzinfo=JST)

    def ev(sid, seq, ts):
        return NormalizedEvent(
            session_id=sid,
            sequence=seq,
            event_time=ts.isoformat(),
            received_at=ts.isoformat(),
            symbol="7203",
            payload={"Buy1": {"Price": 1}, "Sell1": {"Price": 2}},
            source_part="p",
            unique_key=f"{sid}:{seq}",
            ts=ts,
        )

    events = [ev("s", 1, t0), ev("s", 2, t1), ev("s", 3, t2), ev("s", 4, t3)]

    class R:
        gaps = [
            {
                "from": t1.isoformat(),
                "to": t2.isoformat(),
                "gap_sec": (t2 - t1).total_seconds(),
                "from_key": "s:2",
                "to_key": "s:3",
                "kind": "TIME_GAP",
            }
        ]
        sessions = ["s"]
        normalized_rows = 4

    windows, excl, segs = build_valid_windows("20260723", events, R(), day_label="PARTIAL_CAPTURE")
    assert len(windows) == 2
    assert all(isinstance(w, ValidWindow) for w in windows)
    assert all(w.classification == "VALID_COMPLETE_WINDOW" for w in windows)
    assert sum(len(s) for s in segs) == 4



def test_frozen_input_replay_deterministic():
    from small_paper.e1_x5_canonical_replay import trade_ledger_hash
    from small_paper.e1_x5_decision_core import process_e1_x5_event
    from small_paper.e1_x5_dmid_score_provider import DMidD4H6ScoreProvider
    from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession

    prov = DMidD4H6ScoreProvider.maybe_create()
    if prov is None:
        pytest.skip("score provider unavailable")
    t0 = datetime(2026, 7, 23, 10, 0, 0, tzinfo=JST)
    events = []
    for i in range(8):
        ts = t0 + timedelta(seconds=i)
        events.append(
            {
                "symbol": "6758.T",
                "payload": {
                    "Symbol": "6758",
                    "CurrentPrice": 12000.0 + i,
                    "CurrentPriceTime": ts.isoformat(),
                    "Buy1": {"Price": 11999.0, "Qty": 1000},
                    "Sell1": {"Price": 12001.0, "Qty": 1000},
                    "TradingVolume": 200000,
                },
                "recv_ts": ts,
                "sequence": i + 1,
                "event_id": f"d{i}",
            }
        )

    hashes = []
    for _ in range(2):
        sess = E1X5ForwardShadowSession(enabled=True)
        p = DMidD4H6ScoreProvider.maybe_create()
        for ev in events:
            process_e1_x5_event(
                provider=p,
                session=sess,
                symbol=ev["symbol"],
                payload=ev["payload"],
                day="20260723",
                event_sequence=ev["sequence"],
                event_id=ev["event_id"],
                decision_time=ev["recv_ts"],
            )
        hashes.append(trade_ledger_hash(sess.exits))
    assert hashes[0] == hashes[1]


def test_base_vs_candidate_diff_is_guard_only():
    """Candidate arming differs; BASE never pending."""
    s_base = E1X5GuardSession(enabled=True, variant=GuardVariant.BASE)
    s_c2 = E1X5GuardSession(enabled=True, variant=GuardVariant.C2_NO_LOWER_BID)
    ts = datetime(2026, 7, 23, 10, 0, 0, tzinfo=JST)
    kwargs = dict(
        symbol="1000.T",
        ts=ts,
        bid=100.0,
        ask=100.05,
        score=THRESHOLD + 0.1,
        spread_bps=5.0,
        event_sequence=1,
        day="20260723",
    )
    r_base = s_base.try_entry(**kwargs)
    r_c2 = s_c2.try_entry(**kwargs)
    assert r_base is None  # immediate enter
    assert "1000.T" in s_base.positions
    assert r_c2 == "PENDING_CONFIRMATION"
    assert "1000.T" not in s_c2.positions
    assert "1000.T" in s_c2.pending


def test_legacy_reference_not_used_as_hard_gate():
    from small_paper.e1_x5_canonical_replay import LEGACY_ENRICHED_REFERENCE

    assert LEGACY_ENRICHED_REFERENCE["label"] == "LEGACY_ENRICHED_REFERENCE"
    assert LEGACY_ENRICHED_REFERENCE["TRAIN"]["trades"] == 69
