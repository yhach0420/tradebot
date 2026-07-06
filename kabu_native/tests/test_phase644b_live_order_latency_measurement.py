"""Phase644b: live paper order latency measurement tests."""

from __future__ import annotations

import json
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

from research.phase644b_live_order_latency_measurement import (  # noqa: E402
    Phase644bJob,
    build_mandatory_answers,
    load_live_traces,
)
from small_paper.discord_message_builder import format_order_latency_dryrun_lines  # noqa: E402
from small_paper.live_order_api_wiring import LiveOrderWiringSession, process_entry_wiring  # noqa: E402
from small_paper.order_latency_dryrun_trace import (  # noqa: E402
    TRACE_FILENAME,
    OrderLatencyDryRunSession,
    order_latency_dryrun_summary_fields,
)

JST = ZoneInfo("Asia/Tokyo")


def _write_live_session(root: Path, day: str, session: str, rows: list[dict]) -> Path:
    sess_dir = root / day / session
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "small_paper_summary.json").write_text(
        json.dumps({"source": "live", "stop_reason": "completed"}),
        encoding="utf-8",
    )
    trace_fp = sess_dir / TRACE_FILENAME
    with trace_fp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return trace_fp


class Phase644bMeasurementTests(unittest.TestCase):
    def test_load_live_traces_excludes_replay(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        _write_live_session(
            tmp,
            "20260706",
            "live_session_080000",
            [{"symbol": "1111.T", "sample_kind": "pbv2_accepted", "reached_dryrun": True, "push_to_order_sec": 0.2}],
        )
        replay = tmp / "20260707" / "live_session_090000"
        replay.mkdir(parents=True)
        (replay / "small_paper_summary.json").write_text(
            json.dumps({"source": "push-replay"}), encoding="utf-8"
        )
        (replay / TRACE_FILENAME).write_text('{"symbol":"2222.T"}\n', encoding="utf-8")
        samples, sources = load_live_traces(tmp)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["symbol"], "1111.T")
        self.assertEqual(len(sources), 1)

    def test_aggregate_mandatory_answers(self) -> None:
        samples = [
            {
                "sample_kind": "pbv2_accepted",
                "reached_dryrun": True,
                "push_to_order_sec": 0.3,
                "price_to_order_sec": 0.35,
                "decision_latency_ms": 40,
                "queue_latency_ms": 10,
                "order_build_ms": 5,
                "pbv2_or_latency_ms": 20,
                "payload_to_enrich_ms": 8,
                "symbol": "1111.T",
                "session_kind": "AM",
                "time_bucket": "10:00",
            },
            {
                "sample_kind": "or_accepted",
                "reached_dryrun": True,
                "push_to_order_sec": 0.5,
                "price_to_order_sec": 0.55,
                "decision_latency_ms": 60,
                "queue_latency_ms": 15,
                "order_build_ms": 6,
                "pbv2_or_latency_ms": 25,
                "payload_to_enrich_ms": 9,
                "symbol": "2222.T",
                "session_kind": "PM",
                "time_bucket": "13:00",
            },
        ]
        ans = build_mandatory_answers(samples, sources=["mock"])
        self.assertEqual(ans["1_live_trace_count"], 2)
        self.assertEqual(ans["2_pbv2_sample_count"], 1)
        self.assertEqual(ans["2_or_sample_count"], 1)
        self.assertIsNotNone(ans["3_push_to_order_p95_sec"])

    def test_phase644b_job_empty_live(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        native = tmp / "kabu_native"
        (native / "results" / "small_paper").mkdir(parents=True)
        job = Phase644bJob(native_root=native)
        result = job.run()
        self.assertEqual(result["verdict"], "phase644b_live_order_latency_measurement_done")
        self.assertTrue(result["mandatory_answers"]["no_live_traces_yet"])

    def test_discord_summary_enhanced(self) -> None:
        now = datetime.now(JST)
        cpt = (now - timedelta(milliseconds=40)).isoformat(timespec="milliseconds")
        recv = now.isoformat(timespec="milliseconds")
        payload = {"CurrentPriceTime": cpt, "CurrentPrice": 1000.0, "AskPrice": 1001.0}

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

        import tempfile

        tmp = Path(tempfile.mkdtemp())
        trace = OrderLatencyDryRunSession(tmp)
        trace.begin_push(symbol="1234.T", payload=payload, message_index=1, t1_push_received_at=recv, t2_mono=time.monotonic())
        trace.mark_enrich_end()
        trace.mark_freshness_end()
        trace.mark_decision_end(accepted=True, entry_route="pbv2", gate_reason="")
        trace.mark_direct_execute(entry_signal_mono=time.monotonic())
        process_entry_wiring(
            LiveOrderWiringSession(),
            symbol="1234.T",
            trade={"entry_time": recv},
            payload=payload,
            writer=_Writer(),
            config=_Cfg(),
            latency_session=trace,
        )
        fields = order_latency_dryrun_summary_fields(trace)
        lines = format_order_latency_dryrun_lines(fields)
        self.assertTrue(any("samples:" in l for l in lines))
        self.assertTrue(any("push→order p50" in l for l in lines))
        self.assertTrue(any("top bottleneck:" in l for l in lines))


if __name__ == "__main__":
    unittest.main()
