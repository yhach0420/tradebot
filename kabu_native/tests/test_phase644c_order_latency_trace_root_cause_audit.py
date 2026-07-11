"""Phase644c order latency trace root cause audit tests."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase644c_order_latency_trace_root_cause_audit import (  # noqa: E402
    PHASE644C_VERDICT,
    diagnose_session,
    run,
)
from small_paper.live_order_api_wiring import LiveOrderWiringSession  # noqa: E402
from small_paper.order_latency_dryrun_trace import (  # noqa: E402
    OrderLatencyDryRunSession,
    TRACE_FILENAME,
)

JST = ZoneInfo("Asia/Tokyo")


class Phase644cAuditTests(unittest.TestCase):
    def test_diagnose_wiring_guard_session(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            summary = {
                "order_latency_dryrun_trace_enabled": True,
                "order_latency_dryrun_sample_count": 0,
                "live_order_api_wiring_enabled": True,
                "live_order_adapter_enabled": True,
                "accepted_count": 10,
                "source": "live",
            }
            (root / "small_paper_summary.json").write_text(
                __import__("json").dumps(summary), encoding="utf-8"
            )
            row = diagnose_session("20260706", "live_session_080937", root)
            self.assertEqual(row.diagnosis, "root_cause_wiring_skipped_by_legacy_guard")

    def test_execute_accepted_entry_calls_wiring_with_adapter_enabled(self) -> None:
        from small_paper.pilot_runner import _execute_accepted_entry

        tmp = Path(tempfile.mkdtemp())
        now = datetime.now(JST).isoformat(timespec="milliseconds")
        payload = {"CurrentPriceTime": now, "CurrentPrice": 1500.0, "AskPrice": 1501.0}
        trace = OrderLatencyDryRunSession(tmp)
        trace.begin_push(
            symbol="1234.T",
            payload=payload,
            message_index=1,
            t1_push_received_at=now,
            t2_mono=time.monotonic(),
        )
        trace.mark_enrich_end()
        trace.mark_freshness_end()
        trace.mark_decision_end(accepted=True, entry_route="pbv2", gate_reason="")
        trace.mark_direct_execute(entry_signal_mono=time.monotonic())

        class _Cfg:
            live_trading_enabled = False
            order_enabled = False
            live_order_dry_run_enabled = True
            live_order_api_wiring_enabled = True
            live_order_adapter_enabled = True
            live_capital_check_enabled = True
            position_cap_mode = False
            low_liquidity_shadow_enabled = False
            discord_observer_only = False
            pbv2_rise5_shadow_enabled = False
            pbv2_flat_band_shadow_enabled = False

        class _Writer:
            def append_position_row(self, *a, **k) -> None:
                pass

            def append_event(self, *a, **k) -> None:
                pass

            def append_live_order_latency(self, row) -> None:
                pass

            def append_live_order_would_send(self, row) -> None:
                pass

        gate = MagicMock()
        gate.state = MagicMock()
        gate.state.open_slots = []
        gate.record_accepted.return_value = None
        state = MagicMock()
        state.order_latency_dryrun = trace
        state.live_order_wiring = LiveOrderWiringSession()
        state.session_momentum_samples = []
        state.peak_open_slots = 0
        state.discord_ux = MagicMock()
        state.events = []
        state.pos_fields = []
        state.or_overlay = None
        ctx = MagicMock()
        ctx.config = _Cfg()
        ctx.state = state
        ctx.gate = gate
        ctx.writer = _Writer()
        ctx.discord = None
        ctx.observer = None
        ctx.pos_fields = []
        ctx.symbol_price_ring = {}
        ctx.source = "live"
        ctx.extension_bus = None

        decision = MagicMock()
        decision.accept = True
        decision.reason = ""
        decision.quality_tier = "high"
        trade = {
            "symbol": "1234.T",
            "entry_time": now,
            "day": "20260706",
            "entry_type": "PBV2",
            "continuation_quality_score": 0.8,
        }

        with patch("small_paper.live_order_adapter.live_order_adapter_enabled", return_value=True):
            with patch("small_paper.live_order_adapter.process_paper_entry"):
                with patch("small_paper.pilot_runner._should_record_entry_shadows", return_value=False):
                    with patch("small_paper.pilot_runner._maybe_reject_same_symbol_open_overlap", return_value=False):
                        _execute_accepted_entry(
                            ctx,
                            sym="1234.T",
                            trade=trade,
                            decision=decision,
                            payload=payload,
                            enriched=dict(payload),
                            msg_i=1,
                            bucket="am",
                            score5_ord=None,
                        )

        self.assertGreaterEqual(len(trace.samples), 1)
        self.assertTrue((tmp / TRACE_FILENAME).is_file())

    def test_run_produces_report(self) -> None:
        report = run(session_limit=5)
        self.assertEqual(report["verdict"], PHASE644C_VERDICT)


if __name__ == "__main__":
    unittest.main()
