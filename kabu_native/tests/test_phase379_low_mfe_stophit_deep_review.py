"""Phase379/380 tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase379_380_period_b_eval import (  # noqa: E402
    evaluate_variant_shadow,
    is_low_mfe_stop,
    production_candidate_pass,
)
from research.phase379_low_mfe_stophit_deep_review import (  # noqa: E402
    Phase379LowMfeStophitDeepReview,
    feature_distribution_rows,
)
from research.phase380_board_quality_entry_signal_review import (  # noqa: E402
    Phase380BoardQualityEntrySignalReview,
    _board_tier,
    build_bucket_rows,
)


class TestPhase379380(unittest.TestCase):
    def _trades(self) -> list[dict]:
        return [
            {
                "day_key": "20260529",
                "symbol": "1000.T",
                "pnl_yen_100": -1000.0,
                "exit_reason_canonical": "stop_hit",
                "peak_mfe_pct": 0.1,
                "entry_momentum_score": 0.1,
                "board_dynamic_tier": "board_low",
                "entry_imbalance_percentile": 10.0,
                "universe_group": "dynamic40",
                "session_kind": "am",
            },
            {
                "day_key": "20260529",
                "symbol": "2000.T",
                "pnl_yen_100": 2000.0,
                "exit_reason_canonical": "trailing_mfe_exit",
                "peak_mfe_pct": 1.0,
                "entry_momentum_score": 0.8,
                "board_dynamic_tier": "board_high",
                "entry_imbalance_percentile": 80.0,
                "universe_group": "dynamic40",
                "session_kind": "am",
            },
            {
                "day_key": "20260601",
                "symbol": "3000.T",
                "pnl_yen_100": -500.0,
                "exit_reason_canonical": "stop_hit",
                "peak_mfe_pct": 0.0,
                "entry_momentum_score": 0.2,
                "board_dynamic_tier": "board_low",
                "entry_imbalance_percentile": 15.0,
                "universe_group": "dynamic40",
                "session_kind": "pm",
            },
        ]

    def test_is_low_mfe_stop(self) -> None:
        self.assertTrue(is_low_mfe_stop(self._trades()[0]))
        self.assertFalse(is_low_mfe_stop(self._trades()[1]))

    def test_evaluate_variant_shadow(self) -> None:
        m = evaluate_variant_shadow(
            self._trades(),
            variant_id="T",
            would_block=lambda t: str(t.get("board_dynamic_tier")) == "board_low",
        )
        self.assertEqual(m["removed_trade_count"], 2)
        self.assertGreater(m["low_mfe_stop_hit_reduction_count"], 0)

    def test_feature_distribution(self) -> None:
        trades = self._trades()
        losses = [t for t in trades if is_low_mfe_stop(t)]
        wins = [t for t in trades if float(t["pnl_yen_100"]) > 0]
        rows = feature_distribution_rows(losses, cohort="low", compare_trades=wins, compare_label="win")
        self.assertTrue(rows)

    def test_board_tier(self) -> None:
        self.assertEqual(_board_tier({"board_dynamic_tier": "board_mid"}), "board_mid")

    def test_build_bucket_rows(self) -> None:
        rows = build_bucket_rows(self._trades())
        self.assertTrue(any(r["bucket"] == "board_low" for r in rows))


if __name__ == "__main__":
    unittest.main()
