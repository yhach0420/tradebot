"""Phase351: limit-up proximity ENTRY guard production shadow."""

from __future__ import annotations

import unittest

from small_paper.limit_up_proximity_entry_guard_shadow import (
    LimitUpProximityEntryGuardShadowCounters,
    compute_limit_up_proximity_guard_fields,
    enrich_exit_limit_up_proximity_shadow_fields,
    would_block_limit_up_proximity_guard,
)


class TestLimitUpProximityEntryGuardShadow(unittest.TestCase):
    def test_blocks_near_limit_up(self) -> None:
        # prev_close 1000 -> limit_up 1150 (JPX +150 band)
        fields = compute_limit_up_proximity_guard_fields(
            entry_px=1145.0,
            prev_close=1000.0,
            entry_near_day_high_pct=0.2,
        )
        self.assertTrue(would_block_limit_up_proximity_guard(fields))
        self.assertIn("distance_to_limit_up", fields["limit_up_proximity_guard_shadow_reason"])

    def test_blocks_day_high_near_limit(self) -> None:
        fields = compute_limit_up_proximity_guard_fields(
            entry_px=1140.0,
            prev_close=1000.0,
            board_high=1146.0,
        )
        self.assertTrue(fields["day_high_near_limit"])
        self.assertTrue(would_block_limit_up_proximity_guard(fields))

    def test_keeps_normal_entry(self) -> None:
        fields = compute_limit_up_proximity_guard_fields(
            entry_px=1010.0,
            prev_close=1000.0,
            entry_near_day_high_pct=5.0,
            board_high=1015.0,
        )
        self.assertFalse(would_block_limit_up_proximity_guard(fields))

    def test_exit_shadow_removes_blocked_pnl(self) -> None:
        entry_shadow = compute_limit_up_proximity_guard_fields(
            entry_px=1145.0,
            prev_close=1000.0,
        )
        exit_fields = enrich_exit_limit_up_proximity_shadow_fields(
            entry_shadow,
            entry_price=1145.0,
            exit_price=1130.0,
            exit_reason="stop_hit",
        )
        self.assertEqual(exit_fields["limit_up_proximity_shadow_pnl_yen_100"], 0.0)
        self.assertGreater(exit_fields["limit_up_proximity_shadow_delta_yen"], 0.0)

    def test_counters_aggregate_delta(self) -> None:
        counters = LimitUpProximityEntryGuardShadowCounters()
        blocked = compute_limit_up_proximity_guard_fields(entry_px=1145.0, prev_close=1000.0)
        kept = compute_limit_up_proximity_guard_fields(entry_px=1010.0, prev_close=1000.0)
        counters.record_accept(blocked)
        counters.record_accept(kept)
        counters.record_exit(
            enrich_exit_limit_up_proximity_shadow_fields(
                blocked,
                entry_price=1145.0,
                exit_price=1130.0,
                exit_reason="stop_hit",
            )
        )
        counters.record_exit(
            enrich_exit_limit_up_proximity_shadow_fields(
                kept,
                entry_price=1010.0,
                exit_price=1020.0,
                exit_reason="trailing_mfe_exit",
            )
        )
        summary = counters.summary_fields()
        self.assertEqual(summary["limit_up_proximity_guard_shadow_blocked_count"], 1)
        self.assertGreater(summary["limit_up_proximity_guard_shadow_delta_yen"], 0.0)
        self.assertLess(summary["limit_up_proximity_guard_shadow_skipped_trade_pnl_actual"], 0.0)


if __name__ == "__main__":
    unittest.main()
