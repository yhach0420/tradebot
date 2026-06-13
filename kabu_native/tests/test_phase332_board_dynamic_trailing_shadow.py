import unittest
from pathlib import Path

from small_paper.board_dynamic_trailing_shadow import (
    BOARD_HIGH_ACTIVATE_PCT,
    BOARD_HIGH_GIVEBACK_FRAC,
    BOARD_LOW_ACTIVATE_PCT,
    BOARD_LOW_GIVEBACK_FRAC,
    BOARD_SPLIT_PERCENTILE,
    SHADOW_FIELD_KEYS,
    SUMMARY_FIELD_KEYS,
    BoardDynamicTrailingShadowCounters,
    board_tier_from_percentile,
    enrich_exit_board_dynamic_shadow_fields,
    simulate_board_dynamic_shadow_exit,
    trailing_params_for_board_tier,
)


class TestPhase332BoardDynamicTrailingShadow(unittest.TestCase):
    def test_event_fields_in_pilot_runner(self) -> None:
        pilot_src = (
            Path(__file__).resolve().parents[1] / "src" / "small_paper" / "pilot_runner.py"
        ).read_text(encoding="utf-8")
        for key in SHADOW_FIELD_KEYS:
            self.assertIn(f'"{key}"', pilot_src)

    def test_board_tier_cutoff(self) -> None:
        self.assertEqual(BOARD_SPLIT_PERCENTILE, 47.62)
        self.assertEqual(board_tier_from_percentile(50.0), "board_high")
        self.assertEqual(board_tier_from_percentile(47.62), "board_high")
        self.assertEqual(board_tier_from_percentile(40.0), "board_low")
        self.assertEqual(board_tier_from_percentile(None), "board_low")

    def test_trailing_params(self) -> None:
        act, gb, tier = trailing_params_for_board_tier(60.0)
        self.assertEqual(tier, "board_high")
        self.assertEqual(act, BOARD_HIGH_ACTIVATE_PCT)
        self.assertEqual(gb, BOARD_HIGH_GIVEBACK_FRAC)
        act, gb, tier = trailing_params_for_board_tier(30.0)
        self.assertEqual(tier, "board_low")
        self.assertEqual(act, BOARD_LOW_ACTIVATE_PCT)
        self.assertEqual(gb, BOARD_LOW_GIVEBACK_FRAC)

    def test_simulate_stop_hit(self) -> None:
        ticks = [
            {"ts_epoch": 100.0, "price": 1000.0, "pnl_pct": 0.0},
            {"ts_epoch": 110.0, "price": 985.0, "pnl_pct": -1.5},
        ]
        out = simulate_board_dynamic_shadow_exit(
            ticks,
            entry_price=1000.0,
            hard_stop_pct=1.2,
            entry_imbalance_percentile=30.0,
        )
        self.assertEqual(out["shadow_exit_reason"], "stop_hit")
        self.assertEqual(out["shadow_board_dynamic_tier"], "board_low")
        self.assertAlmostEqual(out["shadow_pnl_pct"], -1.5, places=4)

    def test_simulate_trailing_mfe_board_high(self) -> None:
        ticks = [
            {"ts_epoch": 100.0, "price": 1010.0, "pnl_pct": 1.0},
            {"ts_epoch": 110.0, "price": 1005.0, "pnl_pct": 0.5},
        ]
        out = simulate_board_dynamic_shadow_exit(
            ticks,
            entry_price=1000.0,
            hard_stop_pct=1.2,
            entry_imbalance_percentile=55.0,
        )
        self.assertEqual(out["shadow_exit_reason"], "trailing_mfe_exit")
        self.assertEqual(out["shadow_board_dynamic_activate_pct"], 1.0)
        self.assertEqual(out["shadow_board_dynamic_giveback_frac"], 0.6)

    def test_shadow_enrich_uses_legacy_fixed_counterfactual(self) -> None:
        from small_paper.board_dynamic_trailing_shadow import (
            LEGACY_FIXED_ACTIVATE_PCT,
            enrich_exit_board_dynamic_shadow_fields,
        )

        ticks = [
            {"ts_epoch": 100.0, "price": 1010.0, "pnl_pct": 1.0},
            {"ts_epoch": 110.0, "price": 1005.0, "pnl_pct": 0.5},
        ]
        out = enrich_exit_board_dynamic_shadow_fields(
            {"entry_imbalance_percentile": 55.0},
            rich_ticks=ticks,
            entry_price=1000.0,
            entry_ts=100.0,
            hard_stop_pct=1.2,
            actual_exit_time=200.0,
            actual_exit_price=1006.0,
            actual_pnl_pct=0.6,
        )
        self.assertEqual(out["shadow_board_dynamic_activate_pct"], LEGACY_FIXED_ACTIVATE_PCT)
        self.assertEqual(out["shadow_board_dynamic_tier"], "legacy_fixed")

    def test_enrich_delta_vs_actual(self) -> None:
        ticks = [
            {"ts_epoch": 100.0, "price": 1010.0, "pnl_pct": 1.0},
            {"ts_epoch": 110.0, "price": 1005.0, "pnl_pct": 0.5},
        ]
        out = enrich_exit_board_dynamic_shadow_fields(
            {"entry_imbalance_percentile": 55.0},
            rich_ticks=ticks,
            entry_price=1000.0,
            entry_ts=100.0,
            hard_stop_pct=1.2,
            actual_exit_time=200.0,
            actual_exit_price=1002.0,
            actual_pnl_pct=0.2,
        )
        for key in SHADOW_FIELD_KEYS:
            self.assertIn(key, out)
        self.assertGreater(out["actual_vs_shadow_delta_pct"], 0.0)
        self.assertGreater(out["actual_vs_shadow_delta_yen"], 0.0)

    def test_counters_summary(self) -> None:
        counters = BoardDynamicTrailingShadowCounters()
        row = {
            "shadow_exit_reason": "trailing_mfe_exit",
            "actual_vs_shadow_delta_yen": 500.0,
        }
        counters.record_exit(row)
        summary = counters.summary_fields()
        for key in SUMMARY_FIELD_KEYS:
            self.assertIn(key, summary)
        self.assertEqual(summary["board_dynamic_shadow_exit_count"], 1)
        self.assertEqual(summary["board_dynamic_shadow_improved_count"], 1)
        self.assertEqual(summary["board_dynamic_shadow_trailing_mfe_count"], 1)

    def test_observer_tracker_integration(self) -> None:
        obs_src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "small_paper"
            / "observer_position_tracker.py"
        ).read_text(encoding="utf-8")
        self.assertIn("enrich_exit_board_dynamic_shadow_fields", obs_src)
        self.assertIn("board_dynamic_shadow", obs_src)


if __name__ == "__main__":
    unittest.main()
