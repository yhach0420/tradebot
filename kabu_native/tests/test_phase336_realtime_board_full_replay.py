import json
import tempfile
import unittest
from pathlib import Path

from research.phase336_realtime_board_full_replay import (
    Phase336Aggregator,
    discover_push_jsonl_sessions,
    entry_session_bucket,
    write_phase336_outputs,
)


class TestPhase336RealtimeBoardFullReplay(unittest.TestCase):
    def test_entry_session_bucket(self) -> None:
        self.assertEqual(entry_session_bucket("2026-06-05T10:00:00+09:00"), "am")
        self.assertEqual(entry_session_bucket("2026-06-05T14:00:00+09:00"), "pm")
        self.assertEqual(entry_session_bucket("2026-06-05T12:00:00+09:00"), "other")

    def test_discover_push_jsonl_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = root / "2026-06-05"
            day.mkdir()
            (day / "9984.T.jsonl").write_text("{}\n", encoding="utf-8")
            found = discover_push_jsonl_sessions(root)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["day_key"], "20260605")

    def test_aggregator_verdict_adopt(self) -> None:
        agg = Phase336Aggregator()
        trades = [
            {
                "symbol": "9984.T",
                "position_id": "9984.T_1",
                "entry_time": "2026-06-05T10:00:00+09:00",
                "actual_pnl_yen_100": -1000.0,
                "shadow_pnl_yen_100": 500.0,
                "realtime_board_vs_actual_delta_yen": 1500.0,
                "actual_exit_reason": "stop_hit",
                "shadow_exit_reason": "loss_acceleration_exit",
                "no_shadow_exit": False,
            },
            {
                "symbol": "7203.T",
                "position_id": "7203.T_1",
                "entry_time": "2026-06-05T14:00:00+09:00",
                "actual_pnl_yen_100": 200.0,
                "shadow_pnl_yen_100": 300.0,
                "realtime_board_vs_actual_delta_yen": 100.0,
                "actual_exit_reason": "trailing_mfe_exit",
                "shadow_exit_reason": "profit_protect_exit",
                "no_shadow_exit": False,
            },
        ]
        agg.add_session_result(
            session_meta={"session_id": "push_jsonl/2026-06-05", "day_key": "20260605", "source": "push_jsonl"},
            trade_rows=trades,
            push_rows=1000,
            runtime_sec=1.0,
        )
        agg.add_session_result(
            session_meta={"session_id": "push_jsonl/2026-06-04", "day_key": "20260604", "source": "push_jsonl"},
            trade_rows=[
                {
                    **trades[0],
                    "symbol": "6758.T",
                    "position_id": "6758.T_1",
                    "actual_pnl_yen_100": -50.0,
                    "shadow_pnl_yen_100": 0.0,
                    "realtime_board_vs_actual_delta_yen": 50.0,
                }
            ],
            push_rows=500,
            runtime_sec=1.0,
        )
        summary = agg.build_summary()
        self.assertEqual(summary["trades_evaluated"], 3)
        self.assertEqual(summary["sessions_evaluated"], 2)
        self.assertGreater(summary["shadow_total_pnl_yen_100"], summary["actual_total_pnl_yen_100"])
        self.assertIn("verdict", summary)
        self.assertIn("adopt_candidate", summary["verdict"])

    def test_aggregator_failed_session_continues(self) -> None:
        agg = Phase336Aggregator()
        agg.add_session_result(
            session_meta={"session_id": "bad", "day_key": "20260601"},
            trade_rows=[],
            push_rows=0,
            runtime_sec=0.0,
            error="push_dir_missing",
        )
        summary = agg.build_summary()
        self.assertEqual(summary["sessions_failed"], 1)
        self.assertEqual(summary["sessions_evaluated"], 0)

    def test_write_outputs(self) -> None:
        agg = Phase336Aggregator()
        agg.add_session_result(
            session_meta={"session_id": "push_jsonl/2026-06-05", "day_key": "20260605", "push_dir": "/x"},
            trade_rows=[
                {
                    "symbol": "9984.T",
                    "position_id": "p1",
                    "entry_time": "2026-06-05T10:00:00+09:00",
                    "actual_pnl_yen_100": 100.0,
                    "shadow_pnl_yen_100": 200.0,
                    "realtime_board_vs_actual_delta_yen": 100.0,
                    "actual_exit_reason": "trailing_mfe_exit",
                    "shadow_exit_reason": "board_collapse_profit_exit",
                    "no_shadow_exit": False,
                }
            ],
            push_rows=10,
            runtime_sec=0.5,
        )
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_phase336_outputs(agg, Path(tmp))
            summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
            self.assertEqual(summary["phase"], 336)
            self.assertTrue(Path(paths["trades"]).is_file())
            self.assertTrue(Path(paths["symbols"]).is_file())


if __name__ == "__main__":
    unittest.main()
