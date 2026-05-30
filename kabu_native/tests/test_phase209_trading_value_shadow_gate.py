import unittest
from pathlib import Path

from small_paper.trading_value_shadow_gate import (
    SHADOW_FIELD_KEYS,
    SUMMARY_FIELD_KEYS,
    classify_trading_value_band,
    compute_trading_value_shadow_fields,
    finalize_session_trading_value_shadow,
    is_sweet_band,
)


class TestPhase209TradingValueShadowGate(unittest.TestCase):
    def test_event_fields_include_shadow_columns(self) -> None:
        pilot_src = (
            Path(__file__).resolve().parents[1] / "src" / "small_paper" / "pilot_runner.py"
        ).read_text(encoding="utf-8")
        for key in SHADOW_FIELD_KEYS:
            self.assertIn(f'"{key}"', pilot_src)

    def test_sweet_band_classification(self) -> None:
        self.assertEqual(classify_trading_value_band(5e9), "1e8_1e10")
        self.assertEqual(classify_trading_value_band(5e10), "1e10_1e11")
        self.assertEqual(classify_trading_value_band(5e11), "ge_1e11")
        self.assertEqual(classify_trading_value_band(None), "missing")
        self.assertTrue(is_sweet_band(5e10))
        self.assertFalse(is_sweet_band(5e9))

    def test_shadow_fields_on_accept(self) -> None:
        fields = compute_trading_value_shadow_fields({"trading_value": 2.5e10})
        self.assertEqual(fields["trading_value_band"], "1e10_1e11")
        self.assertTrue(fields["tv_sweet_band_flag"])

    def test_session_summary_sweet_band_metrics(self) -> None:
        rows = [
            {
                "symbol": "A.T",
                "entry_time": "t1",
                "trading_value": 2e10,
            },
            {
                "symbol": "B.T",
                "entry_time": "t2",
                "trading_value": 5e9,
            },
        ]
        events = [
            {"event_type": "accepted", "symbol": "A.T", "entry_time": "t1", "trading_value": 2e10},
            {"event_type": "accepted", "symbol": "B.T", "entry_time": "t2", "trading_value": 5e9},
            {"event_type": "observer_exit", "symbol": "A.T", "entry_time": "t1", "pnl_pct": 2.0},
            {"event_type": "observer_exit", "symbol": "B.T", "entry_time": "t2", "pnl_pct": -1.0},
        ]
        summary = finalize_session_trading_value_shadow(rows, events)
        for key in SUMMARY_FIELD_KEYS:
            self.assertIn(key, summary)
        self.assertEqual(summary["sweet_band_trade_count"], 1)
        self.assertEqual(summary["sweet_band_total_pnl"], 2.0)
        self.assertTrue(summary["trading_value_shadow_gate_enabled"])
        self.assertTrue(rows[0]["tv_sweet_band_flag"])
        self.assertFalse(rows[1]["tv_sweet_band_flag"])


if __name__ == "__main__":
    unittest.main()
