"""Phase315: 100-share yen PnL evaluation metrics."""

from __future__ import annotations

import unittest

from replay.metrics import _summary_block, trades_to_rows
from replay.pnl_yen import (
    compute_pnl_yen_100,
    enrich_trade_pnl_yen,
    summarize_pnl_yen_100,
    trade_pnl_yen_100,
)


class TestPhase315PnlYen100(unittest.TestCase):
    def test_long_pnl_formula(self) -> None:
        self.assertEqual(compute_pnl_yen_100(1000.0, 1050.0), 5000.0)
        self.assertEqual(compute_pnl_yen_100(1000.0, 950.0), -5000.0)

    def test_short_pnl_sign_flip(self) -> None:
        self.assertEqual(compute_pnl_yen_100(1000.0, 950.0, side="short"), 5000.0)
        self.assertEqual(compute_pnl_yen_100(1000.0, 1050.0, side="sell"), -5000.0)

    def test_enrich_trade_row(self) -> None:
        row = enrich_trade_pnl_yen(
            {
                "symbol": "9984.T",
                "entry_price": 3000.0,
                "exit_price": 3010.0,
                "pnl_pct": 0.333333,
            }
        )
        self.assertEqual(row["pnl_yen_100"], 1000.0)

    def test_summarize_pnl_yen_100(self) -> None:
        trades = [
            {"entry_price": 1000.0, "exit_price": 1050.0},
            {"entry_price": 2000.0, "exit_price": 1980.0},
            {"entry_price": 500.0, "exit_price": 530.0},
        ]
        summary = summarize_pnl_yen_100(trades)
        self.assertEqual(summary["total_pnl_yen_100"], 6000.0)
        self.assertEqual(summary["avg_pnl_yen_100"], 2000.0)
        self.assertEqual(summary["gross_profit_yen_100"], 8000.0)
        self.assertEqual(summary["gross_loss_yen_100"], 2000.0)
        self.assertEqual(summary["profit_factor_yen_100"], 4.0)
        self.assertEqual(summary["max_win_yen_100"], 5000.0)
        self.assertEqual(summary["max_loss_yen_100"], -2000.0)

    def test_summary_block_includes_yen_metrics(self) -> None:
        trades = [
            {"entry_price": 1000.0, "exit_price": 1010.0, "pnl_pct": 1.0},
            {"entry_price": 1000.0, "exit_price": 990.0, "pnl_pct": -1.0},
        ]
        block = _summary_block(trades)
        self.assertEqual(block["total_pnl_yen_100"], 0.0)
        self.assertEqual(block["avg_pnl_yen_100"], 0.0)
        self.assertIn("profit_factor_yen_100", block)
        self.assertIn("max_win_yen_100", block)
        self.assertIn("max_loss_yen_100", block)

    def test_trades_to_rows_adds_pnl_yen_100(self) -> None:
        class _Trade:
            def to_row(self) -> dict:
                return {
                    "symbol": "7203.T",
                    "entry_price": 2500.0,
                    "exit_price": 2525.0,
                    "pnl_pct": 1.0,
                }

        rows = trades_to_rows([_Trade()])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pnl_yen_100"], 2500.0)

    def test_trade_pnl_yen_100_from_attributes(self) -> None:
        class _Trade:
            entry_price = 1500.0
            exit_price = 1485.0
            side = "long"

        self.assertEqual(trade_pnl_yen_100(_Trade()), -1500.0)


if __name__ == "__main__":
    unittest.main()
