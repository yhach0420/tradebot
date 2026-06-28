"""Phase563 — EXIT shadow daily monitor pilot tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
KABU = REPO / "kabu_native"
for p in (KABU / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from small_paper.board_dynamic_trailing_shadow import (  # noqa: E402
    BOARD_HIGH_ACTIVATE_PCT,
    BOARD_LOW_ACTIVATE_PCT,
    trailing_params_for_board_tier,
)
from small_paper.config import load_pilot_config  # noqa: E402
from small_paper.discord_message_builder import format_research_shadow_daily_summary_lines  # noqa: E402
from small_paper.exit_shadow_monitor import (  # noqa: E402
    PHASE563_VERDICT,
    SUMMARY_FIELD_KEYS,
    ExitShadowMonitorConfig,
    config_from_pilot,
    enrich_exit_shadow_monitor_fields,
    finalize_session_exit_shadow_monitor,
    finalize_session_exit_shadow_monitor_safe,
    format_exit_shadow_monitor_discord_lines,
    _trailing_params_t2,
    _trailing_params_t3,
)
from small_paper.pilot_runner import _apply_exit_shadow_monitor_finalize  # noqa: E402


def _ticks(prices: list[float], start: float = 1_750_000_000.0, step: float = 60.0) -> list[dict]:
    return [{"ts_epoch": start + i * step, "price": px} for i, px in enumerate(prices)]


def _exit_event(
    *,
    pnl_pct: float = 0.5,
    mfe: float = 1.2,
    reason: str = "trailing_mfe_exit",
    imb: float = 60.0,
    t2: float | None = 800.0,
    t3: float | None = 900.0,
) -> dict:
    return {
        "event_type": "observer_exit",
        "symbol": "5074.T",
        "entry_price": 1000.0,
        "exit_price": 1000.0 * (1 + pnl_pct / 100),
        "pnl_pct": pnl_pct,
        "peak_mfe_pct": mfe,
        "exit_reason": reason,
        "entry_imbalance_percentile": imb,
        "exit_shadow_t2_pnl_yen_100": t2,
        "exit_shadow_t3_pnl_yen_100": t3,
    }


class TestExitShadowMonitorZeroTrades(unittest.TestCase):
    def test_zero_trades_summary(self) -> None:
        out = finalize_session_exit_shadow_monitor([], monitor=ExitShadowMonitorConfig(enabled=True))
        self.assertEqual(out["exit_shadow_monitor_status"], "ok")
        self.assertEqual(out["shadow_exit_t2_pnl"], 0.0)
        self.assertEqual(out["exit_early_profit_take_count"], 0)

    def test_disabled_monitor(self) -> None:
        out = finalize_session_exit_shadow_monitor([], monitor=ExitShadowMonitorConfig(enabled=False))
        self.assertFalse(out["exit_shadow_monitor_enabled"])


class TestExitShadowMonitorReplay(unittest.TestCase):
    def test_t3_board_high_params(self) -> None:
        act, gb, tier = _trailing_params_t3(60.0)
        self.assertEqual(tier, "board_high")
        self.assertEqual(act, 1.2)
        self.assertEqual(gb, 0.70)

    def test_t3_board_low_unchanged(self) -> None:
        act, gb, tier = _trailing_params_t3(30.0)
        exp_act, exp_gb, exp_tier = trailing_params_for_board_tier(30.0)
        self.assertEqual(tier, "board_low")
        self.assertEqual(act, exp_act)
        self.assertEqual(gb, exp_gb)
        self.assertEqual(exp_tier, "board_low")

    def test_t2_faster_params(self) -> None:
        act, gb, tier = _trailing_params_t2(60.0)
        base_act, base_gb, _ = trailing_params_for_board_tier(60.0)
        self.assertAlmostEqual(act, base_act - 0.2)
        self.assertAlmostEqual(gb, base_gb - 0.10)

    def test_t2_t3_enrich_on_ticks(self) -> None:
        ticks = _ticks([1000, 1005, 1010, 1008, 1006])
        out = enrich_exit_shadow_monitor_fields(
            rich_ticks=ticks,
            entry_price=1000.0,
            hard_stop_pct=1.2,
            entry_imbalance_percentile=60.0,
            actual_exit_time=ticks[-1]["ts_epoch"],
            actual_exit_price=1006.0,
            actual_pnl_pct=0.6,
            monitor=ExitShadowMonitorConfig(enabled=True),
        )
        self.assertIn("exit_shadow_t2_pnl_yen_100", out)
        self.assertIn("exit_shadow_t3_pnl_yen_100", out)

    def test_enrich_disabled_returns_empty(self) -> None:
        out = enrich_exit_shadow_monitor_fields(
            rich_ticks=_ticks([1000, 1005]),
            entry_price=1000.0,
            hard_stop_pct=1.2,
            entry_imbalance_percentile=60.0,
            actual_exit_time=1.0,
            actual_exit_price=1005.0,
            actual_pnl_pct=0.5,
            monitor=ExitShadowMonitorConfig(enabled=False),
        )
        self.assertEqual(out, {})


class TestExitShadowMonitorAggregate(unittest.TestCase):
    def test_one_trade_summary(self) -> None:
        events = [_exit_event(pnl_pct=0.5, mfe=1.2, t2=800, t3=900)]
        out = finalize_session_exit_shadow_monitor(events, monitor=ExitShadowMonitorConfig(enabled=True))
        self.assertEqual(out["exit_shadow_monitor_trade_count"], 1)
        self.assertEqual(out["shadow_exit_t2_delta"], 300.0)
        self.assertEqual(out["shadow_exit_t3_delta"], 400.0)

    def test_opportunity_loss_metric(self) -> None:
        events = [_exit_event(pnl_pct=0.3, mfe=1.5)]
        out = finalize_session_exit_shadow_monitor(events, monitor=ExitShadowMonitorConfig(enabled=True))
        self.assertAlmostEqual(out["exit_opportunity_loss_avg"], 1.2, places=3)

    def test_early_profit_take_count(self) -> None:
        events = [_exit_event(pnl_pct=0.2, mfe=1.5)]
        out = finalize_session_exit_shadow_monitor(events, monitor=ExitShadowMonitorConfig(enabled=True))
        self.assertEqual(out["exit_early_profit_take_count"], 1)

    def test_missing_mfe_data(self) -> None:
        ev = _exit_event(pnl_pct=0.5, mfe=0.0)
        ev.pop("peak_mfe_pct")
        out = finalize_session_exit_shadow_monitor([ev], monitor=ExitShadowMonitorConfig(enabled=True))
        self.assertEqual(out["exit_mfe_capture_ratio"], 0.0)

    def test_t2_worse_profit_day_flag(self) -> None:
        events = [_exit_event(pnl_pct=1.0, mfe=2.0, t2=500.0, t3=1200.0)]
        out = finalize_session_exit_shadow_monitor(events, monitor=ExitShadowMonitorConfig(enabled=True))
        self.assertTrue(out["shadow_exit_t2_worse_profit_day"])

    def test_summary_has_all_fields(self) -> None:
        out = finalize_session_exit_shadow_monitor([], monitor=ExitShadowMonitorConfig(enabled=True))
        for key in SUMMARY_FIELD_KEYS:
            self.assertIn(key, out)


class TestExitShadowMonitorSafety(unittest.TestCase):
    def test_shadow_failure_non_blocking(self) -> None:
        with patch(
            "small_paper.exit_shadow_monitor._observer_exits",
            side_effect=RuntimeError("boom"),
        ):
            out = finalize_session_exit_shadow_monitor_safe(
                [],
                monitor=ExitShadowMonitorConfig(enabled=True),
            )
        self.assertEqual(out["exit_shadow_monitor_status"], "warning")

    def test_pilot_finalize_wrapper(self) -> None:
        from small_paper.config import SmallPaperPilotConfig
        from small_paper.pilot_runner import _LiveRunState

        state = _LiveRunState(started_mono=0.0)
        summary: dict = {}
        cfg = SmallPaperPilotConfig(exit_shadow_monitor_enabled=True)
        _apply_exit_shadow_monitor_finalize(state, summary, config=cfg)
        self.assertTrue(summary["exit_shadow_monitor_enabled"])


class TestExitShadowMonitorDiscord(unittest.TestCase):
    def test_discord_formatter(self) -> None:
        summary = {
            "exit_shadow_monitor_enabled": True,
            "exit_shadow_monitor_t2_enabled": True,
            "exit_shadow_monitor_t3_enabled": True,
            "exit_mfe_capture_ratio": 0.42,
            "exit_opportunity_loss_avg": 1.8,
            "exit_early_profit_take_count": 5,
            "shadow_exit_t3_pnl": 4200.0,
            "shadow_exit_t3_delta": 1300.0,
            "shadow_exit_t2_pnl": -2500.0,
            "shadow_exit_t2_delta": -7000.0,
            "shadow_exit_t2_worse_profit_day": True,
        }
        lines = format_exit_shadow_monitor_discord_lines(summary)
        joined = "\n".join(lines)
        self.assertIn("EXIT Monitor:", joined)
        self.assertIn("T3 shadow:", joined)
        self.assertIn("warn_profit_day=true", joined)

    def test_research_shadow_includes_exit_monitor(self) -> None:
        lines = format_research_shadow_daily_summary_lines(
            {
                "exit_shadow_monitor_enabled": True,
                "exit_shadow_monitor_t2_enabled": True,
                "exit_shadow_monitor_t3_enabled": True,
                "exit_mfe_capture_ratio": 0.42,
                "exit_opportunity_loss_avg": 1.8,
                "exit_early_profit_take_count": 5,
                "shadow_exit_t3_pnl": 4200.0,
                "shadow_exit_t3_delta": 1300.0,
                "shadow_exit_t2_pnl": -2500.0,
                "shadow_exit_t2_delta": -7000.0,
                "shadow_exit_t2_worse_profit_day": True,
            }
        )
        self.assertTrue(any("EXIT Monitor:" in ln for ln in lines))


class TestExitShadowMonitorConfigRollback(unittest.TestCase):
    def test_yaml_enables_monitor(self) -> None:
        cfg_path = (
            KABU
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        config = load_pilot_config(cfg_path)
        self.assertTrue(config.exit_shadow_monitor_enabled)

    def test_rollback_disabled(self) -> None:
        from small_paper.config import SmallPaperPilotConfig

        cfg = config_from_pilot(SmallPaperPilotConfig(exit_shadow_monitor_enabled=False))
        self.assertFalse(cfg.enabled)
        out = finalize_session_exit_shadow_monitor([], monitor=cfg)
        self.assertFalse(out["exit_shadow_monitor_enabled"])

    def test_t3_only_monitor(self) -> None:
        cfg = ExitShadowMonitorConfig(enabled=True, t2_enabled=False, t3_enabled=True)
        out = enrich_exit_shadow_monitor_fields(
            rich_ticks=_ticks([1000, 1010, 1008]),
            entry_price=1000.0,
            hard_stop_pct=1.2,
            entry_imbalance_percentile=60.0,
            actual_exit_time=120.0,
            actual_exit_price=1008.0,
            actual_pnl_pct=0.8,
            monitor=cfg,
        )
        self.assertIn("exit_shadow_t3_pnl_yen_100", out)
        self.assertNotIn("exit_shadow_t2_pnl_yen_100", out)


class TestExitShadowMonitorIntegration(unittest.TestCase):
    def test_smoke_check(self) -> None:
        from small_paper.production_startup_smoke_test import run_production_startup_smoke_test

        smoke = run_production_startup_smoke_test(repo_root=REPO)
        self.assertTrue(smoke.checks.get("exit_shadow_monitor_summary"))

    def test_verdict_constant(self) -> None:
        self.assertEqual(PHASE563_VERDICT, "phase563_shadow_exit_daily_monitor_pilot_ready")


if __name__ == "__main__":
    unittest.main()
