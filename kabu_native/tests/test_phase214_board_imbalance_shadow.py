import unittest
from pathlib import Path

from small_paper.board_imbalance_shadow import (
    IMBALANCE_TIER_CUTOFFS,
    PRIMARY_TIER,
    SHADOW_FIELD_KEYS,
    SUMMARY_FIELD_KEYS,
    compute_board_imbalance_shadow_fields,
    finalize_session_board_imbalance_shadow,
    highest_imbalance_tier,
    is_imbalance_shadow_candidate,
    session_imbalance_percentile,
)


class TestPhase214BoardImbalanceShadow(unittest.TestCase):
    def test_event_fields_include_shadow_columns(self) -> None:
        pilot_src = (
            Path(__file__).resolve().parents[1] / "src" / "small_paper" / "pilot_runner.py"
        ).read_text(encoding="utf-8")
        for key in SHADOW_FIELD_KEYS:
            self.assertIn(f'"{key}"', pilot_src)

    def test_tier_cutoffs(self) -> None:
        self.assertAlmostEqual(IMBALANCE_TIER_CUTOFFS["10%"], 0.612652, places=6)
        self.assertAlmostEqual(IMBALANCE_TIER_CUTOFFS["20%"], 0.560790, places=6)
        self.assertAlmostEqual(IMBALANCE_TIER_CUTOFFS["30%"], 0.533987, places=6)
        self.assertEqual(highest_imbalance_tier(0.62), "10%")
        self.assertEqual(highest_imbalance_tier(0.57), "20%")
        self.assertEqual(highest_imbalance_tier(0.54), "30%")
        self.assertEqual(highest_imbalance_tier(0.50), "")

    def test_session_percentile_prior_only(self) -> None:
        samples: list[float] = [0.4, 0.5, 0.6]
        self.assertEqual(session_imbalance_percentile(samples, 0.55), 66.67)
        self.assertIsNone(session_imbalance_percentile([], 0.55))

    def test_shadow_fields_on_accept(self) -> None:
        payload = {
            "BidQty": 700,
            "AskQty": 300,
        }
        trade = {
            "trading_value": 2e8,
            "entry_vwap_dev_pct": 1.0,
        }
        samples: list[float] = [0.45, 0.50]
        fields = compute_board_imbalance_shadow_fields(
            trade=trade,
            payload=payload,
            session_imbalance_samples=samples,
        )
        self.assertAlmostEqual(fields["entry_order_book_imbalance"], 0.7, places=4)
        self.assertEqual(fields["entry_imbalance_percentile"], 100.0)
        self.assertTrue(fields["imbalance_shadow_candidate"])
        self.assertEqual(fields["imbalance_shadow_tier"], "10%")
        self.assertEqual(len(samples), 3)

    def test_vwap_reject_excluded_from_candidate(self) -> None:
        trade = {
            "trading_value": 2e8,
            "entry_vwap_dev_pct": 3.0,
            "entry_order_book_imbalance": 0.65,
        }
        self.assertFalse(is_imbalance_shadow_candidate(trade, tier=PRIMARY_TIER))

    def test_low_liq_excluded_from_candidate(self) -> None:
        trade = {
            "trading_value": 5e7,
            "entry_vwap_dev_pct": 1.0,
            "entry_order_book_imbalance": 0.65,
        }
        self.assertFalse(is_imbalance_shadow_candidate(trade, tier=PRIMARY_TIER))

    def test_session_summary_metrics(self) -> None:
        rows = [
            {
                "symbol": "A.T",
                "entry_time": "t1",
                "trading_value": 2e8,
                "entry_vwap_dev_pct": 1.0,
                "entry_order_book_imbalance": 0.62,
                "imbalance_shadow_candidate": True,
                "imbalance_shadow_tier": "10%",
            },
            {
                "symbol": "B.T",
                "entry_time": "t2",
                "trading_value": 2e8,
                "entry_vwap_dev_pct": 1.0,
                "entry_order_book_imbalance": 0.55,
                "imbalance_shadow_candidate": False,
                "imbalance_shadow_tier": "30%",
            },
        ]
        events = [
            {"event_type": "accepted", "symbol": "A.T", "entry_time": "t1"},
            {"event_type": "accepted", "symbol": "B.T", "entry_time": "t2"},
            {
                "event_type": "observer_exit",
                "symbol": "A.T",
                "entry_time": "t1",
                "pnl_pct": 2.0,
                "exit_reason": "stop_hit",
                "stop_hit": True,
            },
            {
                "event_type": "observer_exit",
                "symbol": "B.T",
                "entry_time": "t2",
                "pnl_pct": -1.0,
                "exit_reason": "trailing_mfe_exit",
                "trailing_mfe_exit": True,
            },
        ]
        summary = finalize_session_board_imbalance_shadow(rows, events)
        for key in SUMMARY_FIELD_KEYS:
            self.assertIn(key, summary)
        self.assertEqual(summary["imbalance_shadow_count"], 1)
        self.assertEqual(summary["imbalance_shadow_total_pnl"], 2.0)
        self.assertEqual(summary["imbalance_shadow_stop_hit_count"], 1)
        self.assertEqual(summary["imbalance_shadow_t20_count"], 1)
        self.assertEqual(summary["imbalance_shadow_t30_count"], 2)


if __name__ == "__main__":
    unittest.main()
