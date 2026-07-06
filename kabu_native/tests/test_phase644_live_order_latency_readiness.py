"""Phase644: live order latency dry-run trace tests."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase644_live_order_latency_readiness import (  # noqa: E402
    aggregate_samples,
    run_synthetic_probe,
)
from small_paper.live_order_api_wiring import LiveOrderWiringSession, process_entry_wiring  # noqa: E402
from small_paper.order_latency_dryrun_trace import (  # noqa: E402
    OrderLatencyDryRunSession,
    format_order_latency_dryrun_lines,
    order_latency_dryrun_summary_fields,
)

JST = ZoneInfo("Asia/Tokyo")


class Phase644LatencyTests(unittest.TestCase):
    def test_full_dryrun_trace_pipeline(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        now = datetime.now(JST)
        cpt = (now - timedelta(milliseconds=40)).isoformat(timespec="milliseconds")
        recv = now.isoformat(timespec="milliseconds")
        payload = {
            "CurrentPriceTime": cpt,
            "recorded_at": recv,
            "CurrentPrice": 2000.0,
            "AskPrice": 2001.0,
        }

        class _Writer:
            def append_live_order_latency(self, row) -> None:
                pass

            def append_live_order_would_send(self, row) -> None:
                pass

        class _Cfg:
            live_trading_enabled = False
            order_enabled = False
            live_order_dry_run_enabled = True
            live_order_api_wiring_enabled = True
            order_latency_dryrun_trace_enabled = True

        trace = OrderLatencyDryRunSession(tmp)
        trace.begin_push(
            symbol="5678.T",
            payload=payload,
            message_index=3,
            t1_push_received_at=recv,
            t2_mono=time.monotonic(),
        )
        trace.mark_enrich_end()
        trace.mark_freshness_end()
        trace.mark_decision_end(accepted=True, entry_route="pbv2", gate_reason="")
        trace.mark_direct_execute(entry_signal_mono=time.monotonic())
        process_entry_wiring(
            LiveOrderWiringSession(),
            symbol="5678.T",
            trade={"entry_time": recv, "entry_type": "PBV2"},
            payload=payload,
            writer=_Writer(),
            config=_Cfg(),
            latency_session=trace,
        )
        self.assertEqual(len(trace.samples), 1)
        row = trace.samples[0]
        self.assertEqual(row["sample_kind"], "pbv2_accepted")
        self.assertTrue(row["reached_dryrun"])
        self.assertIsNotNone(row["push_to_order_sec"])
        self.assertIsNotNone(row["price_to_order_sec"])
        self.assertLess(row["push_to_order_sec"], 2.0)

    def test_cap_blocked_sample(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        trace = OrderLatencyDryRunSession(tmp)
        trace.begin_push(
            symbol="9999.T",
            payload={"CurrentPriceTime": datetime.now(JST).isoformat()},
            message_index=1,
            t1_push_received_at=datetime.now(JST).isoformat(),
            t2_mono=time.monotonic(),
        )
        trace.mark_enrich_end()
        trace.mark_freshness_end()
        trace.mark_decision_end(accepted=False, entry_route="reject", gate_reason="max_concurrent")
        trace.finish_reject(gate_reason="max_concurrent", entry_route="reject")
        self.assertEqual(trace.samples[0]["sample_kind"], "cap_blocked")
        self.assertFalse(trace.samples[0]["reached_dryrun"])

    def test_discord_summary_lines(self) -> None:
        summary = {
            "order_latency_dryrun_trace_enabled": True,
            "order_latency_push_to_order_p50_sec": 0.12,
            "order_latency_push_to_order_p95_sec": 0.45,
            "order_latency_push_to_order_max_sec": 0.88,
            "order_latency_price_to_order_p50_sec": 0.15,
            "order_latency_price_to_order_p95_sec": 0.50,
        }
        lines = format_order_latency_dryrun_lines(summary)
        self.assertTrue(any("push→order p50/p95/p99/max" in l for l in lines))
        obs = format_order_latency_dryrun_lines(summary)
        self.assertIn("[Order Latency DryRun]", obs[0])

    def test_synthetic_probe_and_aggregate(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        samples = run_synthetic_probe(Path(tmp))
        self.assertGreaterEqual(len(samples), 1)
        answers = aggregate_samples(samples)
        self.assertIsNotNone(answers.get("1_push_to_order_p50_sec"))
        self.assertIn("3_dominant_delay_stage", answers)

    def test_summary_fields(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        samples = run_synthetic_probe(Path(tmp))
        sess = OrderLatencyDryRunSession(Path(tmp))
        sess.samples = samples
        fields = order_latency_dryrun_summary_fields(sess)
        self.assertTrue(fields.get("order_latency_dryrun_trace_enabled"))


if __name__ == "__main__":
    unittest.main()
