import unittest

from small_paper.extended_entry_shadow import (
    RISE_5MIN_PCT_MIN,
    ROLLING_MFE_PCT_MIN,
    VWAP_DEV_PCT_MIN,
    ExtendedEntryShadowCounters,
    compute_entry_shadow_fields,
    enrich_exit_shadow_fields,
    forward_returns_from_ticks,
)


class TestPhase183ExtendedEntryShadow(unittest.TestCase):
    def test_extended_flag_rise_5min(self) -> None:
        ring = [(1000.0, 100.0), (1300.0, 101.6)]
        shadow = compute_entry_shadow_fields(
            trade={
                "symbol": "9999.T",
                "continuation_quality_score": 0.78,
                "rolling_mfe_pct": 0.005,
                "momentum_continuation_score": 0.25,
            },
            payload={"CurrentPrice": 102.0, "VWAP": 100.0, "HighPrice": 103.0},
            price_ring=ring,
            entry_ts=1300.0,
            session_momentum_samples=[0.4, 0.35],
        )
        self.assertTrue(shadow["extended_entry_shadow_flag"])
        self.assertIn("rise_5min", shadow["extended_entry_shadow_reasons"])

    def test_high_quality_low_momentum(self) -> None:
        shadow = compute_entry_shadow_fields(
            trade={
                "continuation_quality_score": 0.76,
                "momentum_continuation_score": 0.28,
                "rolling_mfe_pct": 0.005,
            },
            payload={"CurrentPrice": 1000.0, "VWAP": 990.0, "HighPrice": 1010.0},
            price_ring=[(1000.0, 1000.0)],
            entry_ts=1000.0,
            session_momentum_samples=[0.5, 0.45, 0.4],
        )
        self.assertTrue(shadow["high_quality_low_momentum_shadow_flag"])

    def test_forward_returns_and_early_adverse(self) -> None:
        ticks = [
            {"ts_epoch": 1000.0, "price": 100.0},
            {"ts_epoch": 1035.0, "price": 99.0},
            {"ts_epoch": 1070.0, "price": 98.5},
        ]
        fwd = forward_returns_from_ticks(ticks, entry_price=100.0, entry_ts=1000.0)
        self.assertEqual(fwd["r30_sec"], -1.0)
        exit_shadow = enrich_exit_shadow_fields(
            {"extended_entry_shadow_flag": True},
            rich_ticks=ticks,
            entry_price=100.0,
            entry_ts=1000.0,
        )
        self.assertTrue(exit_shadow["extended_plus_early_adverse_shadow_flag"])

    def test_counters(self) -> None:
        c = ExtendedEntryShadowCounters()
        c.record_accept({"extended_entry_shadow_flag": True, "high_quality_low_momentum_shadow_flag": True})
        c.record_exit(
            {
                "extended_entry_shadow_flag": True,
                "extended_plus_early_adverse_shadow_flag": True,
                "pnl_pct": -1.2,
                "exit_reason": "stop_hit",
            }
        )
        s = c.summary_fields()
        self.assertEqual(s["extended_entry_shadow_count"], 1)
        self.assertEqual(s["extended_plus_early_adverse_shadow_count"], 1)
        self.assertEqual(s["extended_entry_shadow_stop_hit_count"], 1)

    def test_fixed_thresholds_unchanged(self) -> None:
        self.assertEqual(RISE_5MIN_PCT_MIN, 1.5)
        self.assertEqual(VWAP_DEV_PCT_MIN, 2.5)
        self.assertEqual(ROLLING_MFE_PCT_MIN, 1.5)


if __name__ == "__main__":
    unittest.main()
