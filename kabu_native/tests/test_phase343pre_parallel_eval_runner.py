import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research.streaming_eval_parallel_runner import (
    ParallelEvalConfig,
    directory_size_mb,
    ingest_session_results_to_aggregator,
    write_parallel_eval_benchmark,
)
from research.streaming_eval_session_runners import SessionEvalResult


class TestPhase343PreParallelEvalRunner(unittest.TestCase):
    def test_parallel_config_defaults(self) -> None:
        cfg = ParallelEvalConfig()
        self.assertEqual(cfg.effective_workers(), 1)
        cfg2 = ParallelEvalConfig(parallel=True, max_workers=2)
        self.assertEqual(cfg2.effective_workers(), 2)

    def test_session_result_roundtrip(self) -> None:
        src = SessionEvalResult(
            session_meta={"session_id": "s1", "day_key": "20260518"},
            session_index=0,
            trade_rows=[{"position_id": "p1", "shadow_pnl_yen_100": 1.0}],
            push_rows=100,
            runtime_sec=12.3,
            error="",
            peak_memory_mb=50.0,
        )
        restored = SessionEvalResult.from_dict(src.to_dict())
        self.assertEqual(restored.session_meta["session_id"], "s1")
        self.assertEqual(len(restored.trade_rows), 1)

    def test_ingest_session_results(self) -> None:
        class _Agg:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def ingest_session(
                self,
                *,
                session_meta,
                trade_rows,
                push_rows,
                runtime_sec,
                error="",
                vwap_coverage_pct=None,
            ):
                self.calls.append(
                    {
                        "session_meta": session_meta,
                        "trade_rows": trade_rows,
                        "push_rows": push_rows,
                        "runtime_sec": runtime_sec,
                        "error": error,
                        "vwap_coverage_pct": vwap_coverage_pct,
                    }
                )

        agg = _Agg()
        run = mock.Mock()
        run.session_results = [
            SessionEvalResult(
                session_meta={"session_id": "a"},
                trade_rows=[],
                push_rows=1,
                runtime_sec=1.0,
                vwap_coverage_pct=99.0,
            )
        ]
        ingest_session_results_to_aggregator(agg, run)
        self.assertEqual(len(agg.calls), 1)
        self.assertEqual(agg.calls[0]["vwap_coverage_pct"], 99.0)

    def test_write_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bench.json"
            payload = write_parallel_eval_benchmark(
                path,
                sequential_runtime_sec=4000.0,
                parallel_runtime_sec=2000.0,
                max_workers=2,
                sessions_evaluated=3,
                sessions_failed=0,
                peak_memory_mb=128.0,
                output_size_mb=0.5,
            )
            self.assertEqual(payload["speedup_ratio"], 2.0)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["max_workers"], 2)

    def test_directory_size_mb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fp = Path(tmp) / "x.json"
            fp.write_text("x" * 2048, encoding="utf-8")
            self.assertGreaterEqual(directory_size_mb(fp), 0.0)


if __name__ == "__main__":
    unittest.main()
