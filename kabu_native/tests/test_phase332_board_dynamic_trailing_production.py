import unittest

from research.structural_exit_policies import (
    LEGACY_TRAILING_MFE_ACTIVATE_PCT,
    LEGACY_TRAILING_MFE_GIVEBACK_FRAC,
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW,
    combined_exit_signal_on_latest_tick,
    simulate_structural_policy,
    trailing_mfe_exit_triggered,
    trailing_mfe_params,
)
from small_paper.board_dynamic_trailing_shadow import (
    BOARD_HIGH_ACTIVATE_PCT,
    BOARD_LOW_ACTIVATE_PCT,
    LEGACY_FIXED_ACTIVATE_PCT,
    simulate_legacy_fixed_trailing_exit,
)
from small_paper.discord_message_builder import build_exit_detail


class _Cfg:
    hard_stop_pct = 1.20
    take_quality_drop = 0.08
    momentum_weaken_ratio = 0.85
    favorable_fade_ratio = 0.85
    structural_exit_policy = POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW


class TestPhase332BoardDynamicTrailingProduction(unittest.TestCase):
    def test_trailing_params_board_tiers(self) -> None:
        act, gb, tier = trailing_mfe_params(60.0)
        self.assertEqual(tier, "board_high")
        self.assertEqual(act, BOARD_HIGH_ACTIVATE_PCT)
        act, gb, tier = trailing_mfe_params(30.0)
        self.assertEqual(tier, "board_low")
        self.assertEqual(act, BOARD_LOW_ACTIVATE_PCT)

    def test_board_high_requires_higher_activate(self) -> None:
        self.assertFalse(
            trailing_mfe_exit_triggered(peak_pnl=0.9, pnl=0.5, entry_imbalance_percentile=60.0)
        )
        self.assertTrue(
            trailing_mfe_exit_triggered(peak_pnl=1.1, pnl=0.6, entry_imbalance_percentile=60.0)
        )

    def test_board_low_triggers_earlier(self) -> None:
        self.assertTrue(
            trailing_mfe_exit_triggered(peak_pnl=0.7, pnl=0.25, entry_imbalance_percentile=30.0)
        )

    def test_simulate_production_board_low_trailing(self) -> None:
        ticks = [
            {"price": 1000.0, "pnl_pct": 0.0},
            {"price": 1007.0, "pnl_pct": 0.7},
            {"price": 1002.0, "pnl_pct": 0.2},
        ]
        result = simulate_structural_policy(
            ticks,
            1000.0,
            POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW,
            _Cfg(),
            entry_imbalance_percentile=30.0,
        )
        self.assertIsNotNone(result)
        pnl, reason = result
        self.assertEqual(reason, "trailing_mfe_exit")
        self.assertAlmostEqual(pnl, 0.2, places=4)

    def test_combined_exit_signal_passes_imbalance(self) -> None:
        ticks = [
            {"price": 1000.0, "pnl_pct": 0.0},
            {"price": 1012.0, "pnl_pct": 1.2},
            {"price": 1006.0, "pnl_pct": 0.6},
        ]
        sig = combined_exit_signal_on_latest_tick(
            ticks,
            1000.0,
            _Cfg(),
            entry_imbalance_percentile=55.0,
        )
        self.assertIsNotNone(sig)
        pnl, reason, _px = sig
        self.assertEqual(reason, "trailing_mfe_exit")
        self.assertAlmostEqual(pnl, 0.6, places=4)

    def test_legacy_shadow_counterfactual(self) -> None:
        ticks = [
            {"ts_epoch": 100.0, "price": 1010.0, "pnl_pct": 1.0},
            {"ts_epoch": 110.0, "price": 1005.0, "pnl_pct": 0.5},
        ]
        out = simulate_legacy_fixed_trailing_exit(
            ticks,
            entry_price=1000.0,
            hard_stop_pct=1.2,
        )
        self.assertEqual(out["shadow_exit_reason"], "trailing_mfe_exit")
        self.assertEqual(out["shadow_board_dynamic_activate_pct"], LEGACY_FIXED_ACTIVATE_PCT)
        self.assertEqual(
            LEGACY_TRAILING_MFE_ACTIVATE_PCT,
            LEGACY_FIXED_ACTIVATE_PCT,
        )
        self.assertEqual(
            LEGACY_TRAILING_MFE_GIVEBACK_FRAC,
            0.5,
        )

    def test_discord_exit_shows_board_tier(self) -> None:
        detail = build_exit_detail(
            symbol="6981.T",
            entry_price=1000.0,
            exit_price=1006.0,
            pnl_pct=0.6,
            mfe_pct=1.2,
            mae_pct=-0.3,
            hold_minutes=12.0,
            exit_reason="trailing_mfe_exit",
            board_dynamic_trailing_tier="board_high",
            board_dynamic_trailing_activate_pct=1.0,
            board_dynamic_trailing_giveback_frac=0.6,
        )
        self.assertIn("board_high", detail)
        self.assertIn("activate 1.00%", detail)
        self.assertIn("giveback 60%", detail)


if __name__ == "__main__":
    unittest.main()
