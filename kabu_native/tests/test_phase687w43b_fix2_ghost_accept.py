"""Phase687W43B-FIX2 — Ghost accept prevention regression tests."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (str(NATIVE / "src"), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

from small_paper.canonical_summary import collect_canonical_trades  # noqa: E402
from small_paper.entry_execution_integrity import (  # noqa: E402
    STAGE_ACCEPT_ABORTED,
    STAGE_OFFICIAL_ENTRY,
    EntryStageCounters,
    is_official_entry_ready,
    make_decision_id,
    validate_execution_payload,
)
from small_paper.observer_position_tracker import (  # noqa: E402
    ObserverPositionTracker,
    ObserverTrackerConfig,
)
from small_paper.entry_execution_integrity import EntryStageCounters as _ESC  # noqa: E402
from small_paper.pilot_runner import (  # noqa: E402
    _entry_order_path_allowed,
    _finalize_accepted_entry_stages,
)


def _writer() -> MagicMock:
    w = MagicMock()
    w.append_event = MagicMock()
    w.append_position_row = MagicMock()
    w.append_discord_entry_delivery = MagicMock()
    w.append_live_order_event = MagicMock()
    w.append_live_order_error = MagicMock()
    w.append_error = MagicMock()
    return w


def _ctx(
    *,
    with_observer: bool = True,
    discord_active: bool = True,
) -> SimpleNamespace:
    state = SimpleNamespace(
        events=[],
        accepted_rows=[],
        entry_stage_counters=_ESC(),
        discord_ux={},
        peak_open_slots=0,
        peak_observer_open=0,
        bucket_summary={"AM": {"accepted": 0}, "PM": {"accepted": 0}, "other": {"accepted": 0}},
        position_cap_stats=None,
        live_order_adapter=None,
        live_order_wiring=None,
        live_order_safety_bridge=None,
        live_order_dry_run=None,
        live_capital_manager=None,
        live_capital_read_client=None,
        live_capital_api_token="",
    )
    observer = None
    if with_observer:
        observer = ObserverPositionTracker(
            ObserverTrackerConfig(structural_exit_policy="combined_structural_exit_v1_trailing_mfe_shadow")
        )
    discord = MagicMock()
    discord.active = discord_active
    discord.notify_entry = MagicMock(
        return_value=SimpleNamespace(
            final_result="delivered",
            failure_classification=None,
            retry_count=0,
            sent_time="2026-07-17T09:05:15+09:00",
            http_status=204,
            discord_message_id="m1",
        )
    )
    config = SimpleNamespace(
        position_cap_mode=True,
        max_concurrent_positions=5,
        profile="test",
        discord_enabled=discord_active,
    )
    gate = SimpleNamespace(state=SimpleNamespace(open_slots={}, day_pnl={}))
    return SimpleNamespace(
        config=config,
        state=state,
        gate=gate,
        writer=_writer(),
        discord=discord,
        observer=observer,
        source="test",
        pos_fields=("symbol", "entry_time", "exit_time", "open_slots_after", "position_id", "decision_id"),
        symbol_price_ring={},
        symbol_board_ring={},
    )


def _run_finalize(
    ctx: SimpleNamespace,
    *,
    current_price: Any,
    entry_price: Any = None,
    symbol: str = "1234.T",
    quantity: Any = 100,
) -> dict[str, Any]:
    trade: dict[str, Any] = {
        "symbol": symbol,
        "entry_time": "2026-07-17T09:05:12+09:00",
        "day": "20260717",
        "side": "2",
        "quantity": quantity,
    }
    if entry_price is not None:
        trade["entry_price"] = entry_price
    payload = {"CurrentPrice": current_price, "AskPrice": 4430.0}
    enriched = dict(payload)
    decision = SimpleNamespace(quality_tier="A", accepted=True)
    acc = {
        "event_type": "accepted",
        "symbol": symbol,
        "entry_time": trade["entry_time"],
        "event_time": "2026-07-17T09:05:12+09:00",
        "current_price": current_price,
    }
    _finalize_accepted_entry_stages(
        ctx,
        sym=symbol,
        trade=trade,
        decision=decision,
        payload=payload,
        enriched=enriched,
        acc=acc,
        scan_meta={"scan_id": "s1", "entry_signal_mono": 1.0},
        bucket="AM",
        score5_ord=None,
        msg_i=1,
        slot_before=0,
        slot_after=0,
    )
    return acc


class TestValidateExecutionPayload(unittest.TestCase):
    def test_ok(self) -> None:
        v = validate_execution_payload(
            symbol="1234.T",
            trade={"entry_price": 100.0, "quantity": 100, "side": "2"},
            payload={"CurrentPrice": 100.0},
            event_time="2026-07-17T09:05:12+09:00",
        )
        self.assertTrue(v.ok)

    def test_null_price(self) -> None:
        v = validate_execution_payload(
            symbol="1234.T",
            trade={},
            payload={"CurrentPrice": None, "AskPrice": 4430.0},
            event_time="2026-07-17T09:05:12+09:00",
        )
        self.assertFalse(v.ok)
        self.assertIn("current_price_missing", v.reasons)

    def test_nan_price(self) -> None:
        v = validate_execution_payload(
            symbol="1234.T",
            trade={},
            payload={"CurrentPrice": float("nan")},
            event_time="2026-07-17T09:05:12+09:00",
        )
        self.assertFalse(v.ok)
        self.assertIn("current_price_non_finite", v.reasons)

    def test_entry_price_zero(self) -> None:
        v = validate_execution_payload(
            symbol="1234.T",
            trade={"entry_price": 0},
            payload={"CurrentPrice": 100.0},
            event_time="2026-07-17T09:05:12+09:00",
        )
        self.assertFalse(v.ok)
        self.assertIn("entry_price_non_positive", v.reasons)


class TestGhostAcceptCases(unittest.TestCase):
    def test_case1_normal_entry(self) -> None:
        ctx = _ctx()
        acc = _run_finalize(ctx, current_price=1000.0)
        self.assertTrue(acc.get("position_registered"))
        self.assertEqual(acc.get("accept_stage"), STAGE_OFFICIAL_ENTRY)
        self.assertTrue(is_official_entry_ready(acc))
        self.assertEqual(ctx.discord.notify_entry.call_count, 1)
        self.assertTrue(ctx.observer.has_open("1234.T"))
        counters = ctx.state.entry_stage_counters
        self.assertEqual(counters.official_entry_count, 1)
        self.assertEqual(counters.accept_aborted_count, 0)
        self.assertTrue(_entry_order_path_allowed(acc))

    def test_case2_current_price_null(self) -> None:
        ctx = _ctx()
        acc = _run_finalize(ctx, current_price=None)
        self.assertEqual(acc.get("accept_stage"), STAGE_ACCEPT_ABORTED)
        self.assertFalse(acc.get("position_registered"))
        self.assertEqual(ctx.discord.notify_entry.call_count, 0)
        self.assertFalse(ctx.observer.has_open("1234.T"))
        self.assertFalse(_entry_order_path_allowed(acc))
        self.assertEqual(ctx.state.entry_stage_counters.accept_aborted_count, 1)
        self.assertEqual(ctx.writer.append_discord_entry_delivery.call_count, 1)
        audit = ctx.writer.append_discord_entry_delivery.call_args[0][0]
        self.assertEqual(audit["notification_type"], "ENTRY_ABORTED")
        self.assertFalse(audit["official_entry"])
        order_ev = ctx.writer.append_live_order_event.call_args[0][0]
        self.assertEqual(order_ev["event_type"], "ORDER_INTENT_SKIPPED_INVALID_ENTRY_PAYLOAD")

    def test_case3_current_price_nan(self) -> None:
        ctx = _ctx()
        acc = _run_finalize(ctx, current_price=float("nan"))
        self.assertEqual(acc.get("accept_stage"), STAGE_ACCEPT_ABORTED)
        self.assertEqual(ctx.discord.notify_entry.call_count, 0)
        self.assertFalse(_entry_order_path_allowed(acc))
        self.assertIn("current_price_non_finite", str(acc.get("failure_reason") or ""))

    def test_case4_entry_price_zero(self) -> None:
        ctx = _ctx()
        acc = _run_finalize(ctx, current_price=1000.0, entry_price=0)
        self.assertEqual(acc.get("accept_stage"), STAGE_ACCEPT_ABORTED)
        self.assertEqual(ctx.discord.notify_entry.call_count, 0)
        self.assertFalse(ctx.observer.has_open("1234.T"))

    def test_case5_same_decision_no_duplicate_stages(self) -> None:
        counters = EntryStageCounters()
        did = make_decision_id(symbol="1234.T", entry_time="t1", message_index=1)
        self.assertTrue(counters.record(did, "gate_accepted"))
        self.assertFalse(counters.record(did, "gate_accepted"))
        self.assertEqual(counters.gate_accepted_count, 1)
        ctx = _ctx()
        acc1 = _run_finalize(ctx, current_price=1000.0)
        self.assertEqual(ctx.discord.notify_entry.call_count, 1)
        acc2 = _run_finalize(ctx, current_price=1000.0)
        self.assertEqual(acc1["decision_id"], acc2["decision_id"])
        self.assertTrue(acc2.get("duplicate_decision_skip"))
        self.assertEqual(ctx.discord.notify_entry.call_count, 1)
        self.assertEqual(ctx.state.entry_stage_counters.gate_accepted_count, 1)
        self.assertEqual(ctx.state.entry_stage_counters.official_entry_count, 1)

    def test_case6_canonical_trades_unchanged_count(self) -> None:
        import json

        am = NATIVE / "results" / "small_paper" / "20260717" / "live_session_081810" / "small_paper_events.jsonl"
        pm = NATIVE / "results" / "small_paper" / "20260717" / "live_session_122525" / "small_paper_events.jsonl"
        if not am.is_file() or not pm.is_file():
            self.skipTest("20260717 session artifacts missing")
        events = []
        for path in (am, pm):
            for line in path.open(encoding="utf-8"):
                if line.strip():
                    events.append(json.loads(line))
        can = collect_canonical_trades(events)
        self.assertEqual(len(can), 78)
        # Validation would pass for all canonical entries that have positive prices
        # (gate conditions themselves are unchanged by FIX2).
        priced = [t for t in can if (t.get("entry_price") or 0) and float(t["entry_price"]) > 0]
        self.assertGreaterEqual(len(priced), 70)


class TestOfficialEntryGuard(unittest.TestCase):
    def test_no_official_without_position(self) -> None:
        self.assertFalse(
            is_official_entry_ready(
                {
                    "accept_stage": "gate_accepted",
                    "position_registered": False,
                    "entry_price": 100,
                }
            )
        )

    def test_ask_fallback_blocked_by_validation(self) -> None:
        v = validate_execution_payload(
            symbol="6327.T",
            trade={},
            payload={"CurrentPrice": None, "AskPrice": 4430.0, "CalcPrice": 4400.0},
            event_time="2026-07-17T09:05:12+09:00",
        )
        self.assertFalse(v.ok)
        self.assertIsNone(v.current_price)


if __name__ == "__main__":
    unittest.main()
