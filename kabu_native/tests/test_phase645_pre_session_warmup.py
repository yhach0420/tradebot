"""Phase645: pre-session warmup register tests."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from small_paper.am_pm_session_policy import AmPmSessionPolicy  # noqa: E402
from small_paper.config import SmallPaperPilotConfig  # noqa: E402
from small_paper.pre_session_warmup import (  # noqa: E402
    compute_ready_delay_sec,
    entry_evaluation_allowed,
    resolve_warmup_init_plan,
    ring_only_warmup_active,
    warmup_start_dt,
)
from small_paper.pilot_runner import (  # noqa: E402
    _LiveRunState,
    _PushPipelineContext,
    _warmup_ring_only_push,
    _process_push_payload,
)

JST = ZoneInfo("Asia/Tokyo")


class _Cfg:
    pre_session_warmup_enabled = True
    pre_session_warmup_am_start = "08:50"
    pre_session_warmup_pm_start = "12:15"
    profile = "test"


class Phase645WarmupTests(unittest.TestCase):
    def test_warmup_start_dt_am(self) -> None:
        dt = warmup_start_dt(date(2026, 7, 6), session_kind="am", config=_Cfg())
        self.assertEqual(dt.hour, 8)
        self.assertEqual(dt.minute, 50)

    def test_resolve_plan_warmup_before_start(self) -> None:
        now = datetime(2026, 7, 6, 8, 6, tzinfo=JST)
        plan = resolve_warmup_init_plan(
            config=_Cfg(),
            full_session=True,
            wait_until_session=True,
            session_start="09:03",
            trade_date=date(2026, 7, 6),
            am_pm_policy=AmPmSessionPolicy.morning(),
            now=now,
        )
        self.assertTrue(plan.warmup_enabled)
        self.assertIsNotNone(plan.wait_until_init)
        self.assertEqual(plan.wait_until_init.hour, 8)
        self.assertEqual(plan.wait_until_init.minute, 50)
        self.assertFalse(plan.legacy_wait_until_session)

    def test_resolve_plan_legacy_when_disabled(self) -> None:
        class _Off(_Cfg):
            pre_session_warmup_enabled = False

        now = datetime(2026, 7, 6, 8, 6, tzinfo=JST)
        plan = resolve_warmup_init_plan(
            config=_Off(),
            full_session=True,
            wait_until_session=True,
            session_start="09:03",
            trade_date=date(2026, 7, 6),
            am_pm_policy=AmPmSessionPolicy.morning(),
            now=now,
        )
        self.assertFalse(plan.warmup_enabled)
        self.assertIsNotNone(plan.wait_until_init)
        self.assertEqual(plan.wait_until_init.hour, 9)
        self.assertEqual(plan.wait_until_init.minute, 3)

    def test_ring_only_before_entry_start(self) -> None:
        policy = AmPmSessionPolicy.morning()
        before = datetime(2026, 7, 6, 9, 0, tzinfo=JST)
        after = datetime(2026, 7, 6, 9, 5, tzinfo=JST)
        self.assertTrue(ring_only_warmup_active(config=_Cfg(), am_pm_policy=policy, now=before))
        self.assertFalse(ring_only_warmup_active(config=_Cfg(), am_pm_policy=policy, now=after))
        self.assertFalse(entry_evaluation_allowed(policy, now=before))
        self.assertTrue(entry_evaluation_allowed(policy, now=after))

    def test_warmup_ring_only_skips_gate(self) -> None:
        state = _LiveRunState(started_mono=0.0)
        ctx = _PushPipelineContext(
            config=_Cfg(),
            gate=MagicMock(),
            feature_bridge=MagicMock(),
            state=state,
            writer=MagicMock(),
            code_to_symbol={"9984": "9984.T"},
            source="live",
            pos_fields=[],
            am_pm_policy=AmPmSessionPolicy.morning(),
        )
        payload = {"Symbol": "9984", "CurrentPrice": 1000.0, "CurrentPriceTime": "2026-07-06T08:55:00+09:00"}
        with patch("small_paper.pre_session_warmup.ring_only_warmup_active", return_value=True):
            _process_push_payload(ctx, payload, 1)
        self.assertEqual(state.pre_session_warmup_ring_push_count, 1)
        self.assertIsNone(state.first_gate_eval_ts)
        self.assertEqual(state.gate_evaluations, 0)

    def test_full_eval_after_entry_start(self) -> None:
        state = _LiveRunState(started_mono=0.0)
        gate = MagicMock()
        gate.state.open_slots = []
        ctx = _PushPipelineContext(
            config=_Cfg(),
            gate=gate,
            feature_bridge=MagicMock(),
            state=state,
            writer=MagicMock(),
            code_to_symbol={"9984": "9984.T"},
            source="live",
            pos_fields=[],
            am_pm_policy=AmPmSessionPolicy.morning(),
        )
        ctx.feature_bridge.update.return_value = {}
        ctx.feature_bridge.enrich_payload.return_value = {"CurrentPrice": 1000.0}
        payload = {"Symbol": "9984", "CurrentPrice": 1000.0, "CurrentPriceTime": "2026-07-06T09:05:00+09:00"}
        with patch("small_paper.pre_session_warmup.ring_only_warmup_active", return_value=False):
            with patch("small_paper.pilot_runner._stage0_normalize_payload") as norm:
                norm.return_value = None
                _process_push_payload(ctx, payload, 1)
        self.assertIsNotNone(state.first_gate_eval_ts)

    def test_ready_delay_computation(self) -> None:
        delay = compute_ready_delay_sec(
            allowed_entry_start="09:03",
            first_gate_eval_ts="2026-07-06T09:03:45+09:00",
            trade_date=date(2026, 7, 6),
        )
        self.assertEqual(delay, 45.0)

    def test_pm_warmup_start(self) -> None:
        dt = warmup_start_dt(date(2026, 7, 6), session_kind="pm", config=_Cfg())
        self.assertEqual(dt.hour, 12)
        self.assertEqual(dt.minute, 15)
        policy = AmPmSessionPolicy.afternoon()
        before = datetime(2026, 7, 6, 12, 20, tzinfo=JST)
        self.assertTrue(ring_only_warmup_active(config=_Cfg(), am_pm_policy=policy, now=before))

    def test_warmup_ring_update_only(self) -> None:
        state = _LiveRunState(started_mono=0.0)
        ctx = _PushPipelineContext(
            config=_Cfg(),
            gate=MagicMock(),
            feature_bridge=MagicMock(),
            state=state,
            writer=MagicMock(),
            code_to_symbol={"1111": "1111.T"},
            source="live",
            pos_fields=[],
        )
        _warmup_ring_only_push(
            ctx,
            {"Symbol": "1111", "CurrentPrice": 500.0, "CurrentPriceTime": "2026-07-06T08:55:00+09:00"},
            2,
        )
        self.assertEqual(state.pre_session_warmup_ring_push_count, 1)
        self.assertIn("1111.T", ctx.symbol_price_ring)


if __name__ == "__main__":
    unittest.main()
