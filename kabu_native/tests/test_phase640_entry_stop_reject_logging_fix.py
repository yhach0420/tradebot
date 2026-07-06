"""Phase640: entry stop / outside_refresh_universe reject logging fix."""

from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dataclasses import replace

from small_paper.am_pm_session_policy import AmPmSessionPolicy
from small_paper.config import load_pilot_config
from small_paper.live_feature_bridge import LiveFeatureBridge
from small_paper.live_writer import LiveSessionWriter
from small_paper.pilot_runner import (
    EVENT_FIELDS,
    REJECT_OUTSIDE_REFRESH_UNIVERSE,
    _LiveRunState,
    _PushPipelineContext,
    _entry_stop_reject_logging_summary_fields,
    _make_entry_scan_controller,
    _process_push_payload,
    _stage1_evaluate_freshness,
    _stage6_record_candidate,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE_ROOT = Path(__file__).resolve().parents[1]
CFG_PATH = (
    NATIVE_ROOT
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)


def _sample_payload(now_iso: str, *, symbol: str = "6976") -> dict:
    return {
        "Symbol": symbol,
        "SymbolName": "TEST",
        "CurrentPrice": 1500.0,
        "CurrentPriceTime": now_iso,
        "CalcPrice": 1500.0,
        "PreviousClose": 1450.0,
        "BidPrice": 1499.0,
        "AskPrice": 1501.0,
        "BidTime": now_iso,
        "AskTime": now_iso,
        "TradingVolume": 500000,
        "TradingValue": 750000000,
        "HighPrice": 1520.0,
        "LowPrice": 1440.0,
        "OpeningPrice": 1460.0,
        "recorded_at": now_iso,
    }


def _mk_ctx(tmp_dir: Path, **kwargs: object) -> _PushPipelineContext:
    config = replace(load_pilot_config(CFG_PATH), discord_enabled=False)
    gate = config.make_exposure_gate(repo_root=NATIVE_ROOT.parent)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    writer = LiveSessionWriter(tmp_dir, incremental=False, event_fields=EVENT_FIELDS)
    state = _LiveRunState(started_mono=time.monotonic())
    ctx = _PushPipelineContext(
        config=config,
        gate=gate,
        feature_bridge=LiveFeatureBridge(config.feature_bridge_config()),
        state=state,
        writer=writer,
        code_to_symbol={"6976": "6976.T", "9984": "9984.T", "9999": "9999.T"},
        source="push-replay",
        pos_fields=(),
        entry_scan=_make_entry_scan_controller(config, source="push-replay", writer=writer),
        **kwargs,
    )
    return ctx


class Phase640EntryStopRejectLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = Path(tempfile.mkdtemp(prefix="phase640_"))

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_stage1_pre_gate_sets_ref_now(self) -> None:
        ctx = _mk_ctx(self._tmpdir / "s1")
        ctx.am_pm_policy = AmPmSessionPolicy.morning()
        after_stop = datetime(2026, 7, 1, 11, 25, tzinfo=JST).isoformat(timespec="seconds")
        payload = _sample_payload(after_stop)
        from small_paper.pilot_runner import _stage0_normalize_payload

        norm = _stage0_normalize_payload(ctx, payload, 1, t0_push_received_at=after_stop)
        assert norm is not None
        with patch.object(AmPmSessionPolicy, "entry_allowed_now", return_value=False):
            fresh = _stage1_evaluate_freshness(ctx, norm)
        self.assertIsNotNone(fresh.ref_now)
        self.assertFalse(fresh.ref_now_unbound)
        self.assertEqual(fresh.pre_gate_reason, "am_pm_entry_stop")

    def test_am_pm_entry_stop_records_rejected_event(self) -> None:
        ctx = _mk_ctx(self._tmpdir / "am_stop")
        ctx.am_pm_policy = AmPmSessionPolicy.morning()
        after_stop = datetime(2026, 7, 1, 11, 25, tzinfo=JST).isoformat(timespec="seconds")
        with patch.object(AmPmSessionPolicy, "entry_allowed_now", return_value=False):
            _process_push_payload(ctx, _sample_payload(after_stop), 1, t0_push_received_at=after_stop)
        cands = [e for e in ctx.state.events if e.get("event_type") == "candidate"]
        rejs = [e for e in ctx.state.events if e.get("event_type") == "rejected"]
        self.assertEqual(len(cands), 1)
        self.assertEqual(len(rejs), 1)
        self.assertEqual(rejs[0].get("gate_reject_reason"), "am_pm_entry_stop")
        self.assertEqual(len(ctx.state.reject_rows), 1)
        self.assertEqual(ctx.state.entry_stop_reject_logging_recovered_count, 1)
        self.assertEqual(ctx.state.accepted_rows, [])

    def test_outside_refresh_universe_records_rejected_event(self) -> None:
        ctx = _mk_ctx(self._tmpdir / "outside")
        now_iso = datetime.now(JST).isoformat(timespec="seconds")
        ctx.entry_eligible_symbols = {"6976.T"}
        _process_push_payload(
            ctx,
            _sample_payload(now_iso, symbol="9999"),
            1,
            symbol="9999.T",
            t0_push_received_at=now_iso,
        )
        rejs = [e for e in ctx.state.events if e.get("event_type") == "rejected"]
        self.assertEqual(len(rejs), 1)
        self.assertEqual(rejs[0].get("gate_reject_reason"), REJECT_OUTSIDE_REFRESH_UNIVERSE)
        self.assertEqual(ctx.state.entry_stop_reject_logging_recovered_count, 1)
        self.assertEqual(ctx.state.accepted_rows, [])

    def test_fresh_candidate_accepted_count_unchanged(self) -> None:
        ctx = _mk_ctx(self._tmpdir / "fresh")
        now_iso = datetime.now(JST).isoformat(timespec="seconds")
        _process_push_payload(ctx, _sample_payload(now_iso), 1, t0_push_received_at=now_iso)
        self.assertEqual(len([e for e in ctx.state.events if e.get("event_type") == "candidate"]), 1)
        self.assertEqual(ctx.state.entry_stop_reject_logging_recovered_count, 0)

    def test_stage6_audit_failure_increments_logging_error_count(self) -> None:
        ctx = _mk_ctx(self._tmpdir / "audit_err")
        ctx.am_pm_policy = AmPmSessionPolicy.morning()
        after_stop = datetime(2026, 7, 1, 11, 25, tzinfo=JST).isoformat(timespec="seconds")
        from small_paper.entry_pipeline_stages import Stage4FinalEntryDecision
        from small_paper.pilot_runner import _stage0_normalize_payload, _stage4_finalize_decision

        norm = _stage0_normalize_payload(ctx, _sample_payload(after_stop), 1, t0_push_received_at=after_stop)
        assert norm is not None
        with patch.object(AmPmSessionPolicy, "entry_allowed_now", return_value=False):
            fresh = _stage1_evaluate_freshness(ctx, norm)
        final = _stage4_finalize_decision(ctx, norm, fresh, None)
        assert final.decision is not None
        with patch.object(ctx.entry_scan, "record_symbol_eval", side_effect=RuntimeError("audit boom")):
            _stage6_record_candidate(ctx, norm, fresh, final)
        self.assertEqual(ctx.state.logging_error_count, 1)

    def test_summary_fields_include_recovered_count(self) -> None:
        state = _LiveRunState(started_mono=0.0)
        state.entry_stop_reject_logging_recovered_count = 3
        fields = _entry_stop_reject_logging_summary_fields(state)
        self.assertEqual(fields["entry_stop_reject_logging_recovered_count"], 3)
        self.assertEqual(fields["logging_error_count"], 0)


if __name__ == "__main__":
    unittest.main()
