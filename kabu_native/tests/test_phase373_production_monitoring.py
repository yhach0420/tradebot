"""Phase373: production monitoring pack tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase373_production_monitoring import (  # noqa: E402
    Phase373ProductionMonitoring,
    build_reject_row,
    build_stophit_row,
    classify_guard_reject,
    session_metrics_from_parts,
    summarize_metrics,
)
from small_paper.near_day_high_low_momentum_dynamic40_entry_guard import (  # noqa: E402
    REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD,
)
from small_paper.pullback_misread_dynamic40_entry_guard import (  # noqa: E402
    REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD,
)


class TestPhase373ProductionMonitoring(unittest.TestCase):
    def test_classify_guard_reject_from_gate_reject_reason(self) -> None:
        row = {"gate_reject_reason": REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD}
        self.assertEqual(classify_guard_reject(row), REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD)

    def test_classify_guard_reject_ignores_other_reasons(self) -> None:
        row = {"gate_reject_reason": "max_concurrent"}
        self.assertIsNone(classify_guard_reject(row))

    def test_build_reject_row_flags_core10_anomaly(self) -> None:
        row = {
            "gate_reject_reason": REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD,
            "symbol": "3905.T",
            "universe_slot": "core",
            "entry_momentum_score": "0.1",
            "day_high_distance_pct": "0.5",
        }
        meta = {"session_id": "20260612/live_session_080806", "day_key": "20260612"}
        out = build_reject_row(row, session_meta=meta, session_kind="am")
        self.assertTrue(out["core10_guard_anomaly"])
        self.assertEqual(out["reject_reason"], REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD)

    def test_session_metrics_from_parts(self) -> None:
        reject_rows = [
            {
                "reject_reason": REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD,
                "core10_guard_anomaly": False,
            },
            {
                "reject_reason": REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD,
                "core10_guard_anomaly": True,
            },
        ]
        production_trades = [
            {"loss_60s_0p3": True, "loss_120s_0p5": False},
            {"loss_60s_0p3": False, "loss_120s_0p5": True},
        ]
        stophit_rows = [
            {
                "exit_reason_canonical": "stop_hit",
                "is_low_mfe_stop": True,
                "is_dynamic40": True,
                "is_core10": False,
                "is_dynamic40_low_mfe": True,
                "is_core10_low_mfe": False,
            },
            {
                "exit_reason_canonical": "stop_hit",
                "is_low_mfe_stop": False,
                "is_dynamic40": False,
                "is_core10": True,
                "is_dynamic40_low_mfe": False,
                "is_core10_low_mfe": False,
            },
        ]
        metrics = session_metrics_from_parts(
            reject_rows=reject_rows,
            production_trades=production_trades,
            stophit_rows=stophit_rows,
            raw_accepted=10,
            raw_rejected=100,
        )
        self.assertEqual(metrics["pullback_misread_dynamic40_reject_count"], 1)
        self.assertEqual(metrics["near_day_high_low_momentum_dynamic40_reject_count"], 1)
        self.assertEqual(metrics["total_guard_reject_count"], 2)
        self.assertEqual(metrics["core10_guard_reject_count"], 1)
        self.assertEqual(metrics["accepted_trade_count"], 2)
        self.assertEqual(metrics["stop_hit_count"], 2)
        self.assertEqual(metrics["low_mfe_stop_hit_count"], 1)
        self.assertEqual(metrics["dynamic40_stop_hit_count"], 1)
        self.assertEqual(metrics["core10_stop_hit_count"], 1)
        self.assertEqual(metrics["dynamic40_low_mfe_stop_hit_count"], 1)
        self.assertEqual(metrics["immediate_death_60s_count"], 1)
        self.assertEqual(metrics["immediate_death_120s_count"], 1)

    def test_build_stophit_row(self) -> None:
        trade = {
            "session_id": "20260612/live_session_080806",
            "day_key": "20260612",
            "session_kind": "am",
            "universe_group": "dynamic40",
            "universe_slot": "dynamic",
            "symbol": "6976.T",
            "entry_time": "2026-06-12T09:15:00+09:00",
            "exit_time": "2026-06-12T09:20:00+09:00",
            "pnl_yen_100": -1200.0,
            "peak_mfe_pct": 0.1,
            "exit_reason_canonical": "stop_hit",
        }
        death = {"loss_60s_0p3": True, "loss_120s_0p5": False, "min_pnl_first_60s": -0.4}
        row = build_stophit_row(trade, death=death)
        self.assertTrue(row["is_low_mfe_stop"])
        self.assertTrue(row["loss_60s_0p3"])

    def test_finalize_outputs_writes_summary(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            audit = Phase373ProductionMonitoring(reports_dir=reports)
            audit.ingest_session(
                {
                    "session_meta": {
                        "session_id": "20260612/live_session_080806",
                        "day_key": "20260612",
                        "session_kind": "am",
                    },
                    "metrics": session_metrics_from_parts(
                        reject_rows=[
                            {
                                "reject_reason": REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD,
                                "core10_guard_anomaly": False,
                                "symbol": "6976.T",
                            }
                        ],
                        production_trades=[{"loss_60s_0p3": True, "loss_120s_0p5": False}],
                        stophit_rows=[
                            build_stophit_row(
                                {
                                    "session_id": "20260612/live_session_080806",
                                    "day_key": "20260612",
                                    "session_kind": "am",
                                    "universe_group": "dynamic40",
                                    "universe_slot": "dynamic",
                                    "symbol": "6976.T",
                                    "entry_time": "t1",
                                    "exit_time": "t2",
                                    "pnl_yen_100": -100,
                                    "peak_mfe_pct": 0.1,
                                    "exit_reason_canonical": "stop_hit",
                                },
                                death={"loss_60s_0p3": True},
                            )
                        ],
                        raw_accepted=1,
                        raw_rejected=1,
                    ),
                    "reject_rows": [
                        build_reject_row(
                            {
                                "gate_reject_reason": REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD,
                                "symbol": "6976.T",
                                "universe_slot": "dynamic",
                            },
                            session_meta={
                                "session_id": "20260612/live_session_080806",
                                "day_key": "20260612",
                            },
                            session_kind="am",
                        )
                    ],
                    "stophit_rows": [
                        build_stophit_row(
                            {
                                "session_id": "20260612/live_session_080806",
                                "day_key": "20260612",
                                "session_kind": "am",
                                "universe_group": "dynamic40",
                                "universe_slot": "dynamic",
                                "symbol": "6976.T",
                                "entry_time": "t1",
                                "exit_time": "t2",
                                "pnl_yen_100": -100,
                                "peak_mfe_pct": 0.1,
                                "exit_reason_canonical": "stop_hit",
                            },
                            death={"loss_60s_0p3": True},
                        )
                    ],
                    "production_trades": [{"symbol": "6976.T", "loss_60s_0p3": True}],
                }
            )
            paths = audit.finalize_outputs(
                wall_runtime_sec=0.1,
                sessions_discovered=1,
                sessions_evaluated=1,
            )
            summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
            self.assertEqual(summary["phase"], 373)
            self.assertEqual(summary["metrics"]["total_guard_reject_count"], 1)
            self.assertEqual(summary["metrics"]["stop_hit_count"], 1)
            self.assertTrue(paths["summary"].is_file())
            self.assertTrue(paths["by_symbol"].is_file())
            self.assertTrue(paths["rejects"].is_file())
            self.assertTrue(paths["stophit"].is_file())

    def test_summarize_metrics(self) -> None:
        rows = [
            {"accepted_trade_count": 3, "stop_hit_count": 1},
            {"accepted_trade_count": 2, "stop_hit_count": 2},
        ]
        out = summarize_metrics(rows)
        self.assertEqual(out["accepted_trade_count"], 5)
        self.assertEqual(out["stop_hit_count"], 3)


if __name__ == "__main__":
    unittest.main()
