"""Phase687W4 — Runtime dry-run wiring + readonly + latency tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from small_paper.live_order_account_status import AccountReadStatus
from small_paper.live_order_runtime_bridge import (
    ENTRY_SOURCE_ACTUAL,
    EXIT_SOURCE_ACTUAL,
    build_runtime_bridge,
    safety_sm_enabled,
)
from small_paper.live_order_safety_sm import KabuBrokerAdapter, OrderLifecycleState


def _cfg(**kw):
    base = dict(
        live_trading_enabled=False,
        order_enabled=False,
        dry_run=True,
        live_order_safety_sm_enabled=True,
        max_concurrent_positions=3,
        safety_sm_allow_mock_capital=True,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _bridge(td: Path):
    return build_runtime_bridge(
        output_dir=td,
        session_id="w4/test",
        config=_cfg(),
        allow_mock_capital=True,
    )


def test_safety_sm_enabled_gates():
    assert safety_sm_enabled(_cfg()) is True
    assert safety_sm_enabled(_cfg(live_trading_enabled=True)) is False
    assert safety_sm_enabled(_cfg(order_enabled=True)) is False
    assert safety_sm_enabled(_cfg(live_order_safety_sm_enabled=False)) is False


def test_actual_entry_unique_intent_and_shadow_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        b = _bridge(Path(tmp))
        b.startup()
        r1 = b.on_actual_entry(symbol="A.T", price=1000.0, position_id="p1", source_kind=ENTRY_SOURCE_ACTUAL)
        assert r1["would_submit"] is True
        r2 = b.on_actual_entry(symbol="A.T", price=1000.0, position_id="p1", source_kind=ENTRY_SOURCE_ACTUAL)
        assert r2["order_id"] == r1["order_id"]
        assert b.duplicate_intent_prevented_count >= 1
        assert b.duplicate_intent_created_count == 0
        shadow = b.on_actual_entry(
            symbol="B.T", price=1000.0, position_id="p2", source_kind="shadow"
        )
        assert shadow["would_submit"] is False
        reject = b.on_actual_entry(
            symbol="C.T", price=1000.0, position_id="p3", source_kind="reject"
        )
        assert reject["would_submit"] is False
        cap = b.on_actual_entry(
            symbol="D.T", price=1000.0, position_id="p4", source_kind="capacity_blocked"
        )
        assert cap["would_submit"] is False


def test_actual_exit_and_duplicates():
    with tempfile.TemporaryDirectory() as tmp:
        b = _bridge(Path(tmp))
        b.startup()
        b.on_actual_entry(symbol="E.T", price=1000.0, position_id="pe", source_kind=ENTRY_SOURCE_ACTUAL)
        x1 = b.on_actual_exit(
            symbol="E.T",
            position_id="pe",
            exit_reason="stop_hit",
            source_kind=EXIT_SOURCE_ACTUAL,
        )
        assert x1["would_submit"] is True
        x2 = b.on_actual_exit(
            symbol="E.T",
            position_id="pe",
            exit_reason="stop_hit",
            source_kind=EXIT_SOURCE_ACTUAL,
        )
        assert x2["order_id"] == x1["order_id"]
        assert b.duplicate_intent_prevented_count >= 1


def test_session_close_and_no_progress_exits():
    with tempfile.TemporaryDirectory() as tmp:
        b = _bridge(Path(tmp))
        b.startup()
        b.on_actual_entry(symbol="F.T", price=1000.0, position_id="pf", source_kind=ENTRY_SOURCE_ACTUAL)
        for reason in ("no_progress_exit", "trailing_mfe_exit", "morning_session_close"):
            # re-entry for each reason path using unique position
            pid = f"pf_{reason}"
            b.on_actual_entry(symbol=f"F{reason[:2]}.T", price=1000.0, position_id=pid, source_kind=ENTRY_SOURCE_ACTUAL)
            r = b.on_actual_exit(
                symbol=f"F{reason[:2]}.T",
                position_id=pid,
                exit_reason=reason,
                source_kind=EXIT_SOURCE_ACTUAL,
            )
            assert r["ok"] is True


def test_account_status_matrix_and_kabu_hard_fail():
    kabu = KabuBrokerAdapter()
    st = kabu.refresh_readonly()
    assert st == AccountReadStatus.CLIENT_NOT_CONFIGURED.value
    assert kabu.get_account_status()["online"] is False
    kabu2 = KabuBrokerAdapter(client=object(), token="")
    assert kabu2.refresh_readonly() == AccountReadStatus.TOKEN_REQUEST_FAILED.value
    with pytest.raises(RuntimeError, match="HARD_FAIL"):
        kabu.submit_entry_order({"symbol": "X"})
    with pytest.raises(RuntimeError, match="HARD_FAIL"):
        kabu.cancel_order("x")
    with pytest.raises(RuntimeError, match="HARD_FAIL"):
        kabu.emergency_flatten()


def test_journal_files_and_replay():
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        b = _bridge(td)
        b.startup()
        b.on_actual_entry(symbol="G.T", price=1000.0, position_id="pg", source_kind=ENTRY_SOURCE_ACTUAL)
        out = td
        assert (out / "order_intents.jsonl").is_file()
        assert (out / "order_state_events.jsonl").is_file()
        assert (out / "capital_reservations.jsonl").is_file()
        info = b.engine.restore_from_journal()
        assert info["resubmit"] is False
        assert b.engine.actual_broker_submit_count() == 0


def test_latency_and_integrity():
    with tempfile.TemporaryDirectory() as tmp:
        b = _bridge(Path(tmp))
        b.startup()
        b.on_actual_entry(
            symbol="H.T",
            price=1000.0,
            position_id="ph",
            source_kind=ENTRY_SOURCE_ACTUAL,
            timestamps={"push_received_mono": 1.0, "accepted_mono": 1.05},
            freshness={"price_age_sec": 0.2, "board_age_sec": 0.1},
        )
        b.on_actual_exit(
            symbol="H.T", position_id="ph", exit_reason="stop_hit", source_kind=EXIT_SOURCE_ACTUAL
        )
        integ = b.session_integrity(canonical_entry_count=1, canonical_exit_count=1)
        assert integ["missing_intent_count"] == 0
        assert integ["duplicate_intent_created_count"] == 0
        assert integ["reservation_leak"] == 0
        assert integ["actual_broker_submit_count"] == 0
        lat = b.latency_summary()
        assert lat["latency_sample_count"] >= 1
        assert lat["kabu_submit_ack_unmeasured"] is True


def test_stale_price_blocks_would_submit():
    with tempfile.TemporaryDirectory() as tmp:
        b = _bridge(Path(tmp))
        b.startup()
        r = b.on_actual_entry(
            symbol="I.T",
            price=1000.0,
            position_id="pi",
            source_kind=ENTRY_SOURCE_ACTUAL,
            freshness={"price_age_sec": 10.0, "board_age_sec": 0.1},
        )
        assert r["would_submit"] is False
        assert r["reject_reason"] == "stale_price"
