"""Phase684 — I/H/C counterfactual reconstruction tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from small_paper.ihc_shadow_counterfactual import (  # noqa: E402
    DEFAULT_SHADOW_CFG,
    build_daily_shadow_summary,
    enrich_trades_with_shadow,
    evaluate_trade_shadow_fields,
    finalize_session_ihc_shadow_summary,
    format_entry_shadow_discord_lines,
    scenario_metrics,
)


def _trade(**kw: object) -> dict:
    base = {
        "position_id": "p1",
        "symbol": "AAA.T",
        "entry_time": "2026-07-10T09:30:00+09:00",
        "exit_time": "2026-07-10T09:40:00+09:00",
        "pnl_yen_100": -1000.0,
        "exit_reason": "stop_hit",
        "hold_sec": 120,
        "live_feature_complete": False,
        "entry_expectancy_score_v2": 2.0,
        "readiness_bounce_from_recent_low_accept": 0.5,
        "microseq_bounce_from_recent_low": 0.25,
        "microseq_fall_from_recent_high": -0.2,
        "microseq_slope_5min": 0.1,
        "microsequence_pre_entry_ok": True,
    }
    base.update(kw)
    return base


class TestCounterfactualMath(unittest.TestCase):
    def test_individual_delta(self) -> None:
        trades = [_trade(pnl_yen_100=-1000.0), _trade(position_id="p2", pnl_yen_100=500.0, entry_expectancy_score_v2=5.0)]
        enriched = enrich_trades_with_shadow(trades, price_idx={})
        m = scenario_metrics(enriched, block_pred=lambda t: bool(t.get("I_block")), actual_total_pnl=-500.0)
        self.assertEqual(m["blocked_actual_pnl_yen"], enriched[0]["pnl_yen_100"])
        self.assertEqual(m["delta_pnl_yen"], 1000.0)
        self.assertEqual(m["counterfactual_total_pnl_yen"], 500.0)

    def test_union_dedup_not_double_count(self) -> None:
        t = _trade(I_block=True, H_block=True, C_block=True)
        shadow = evaluate_trade_shadow_fields(t, saved_flags=t)
        self.assertTrue(shadow["IHC_union_block"])
        enriched = [{**t, **shadow}]
        m = scenario_metrics(enriched, block_pred=lambda x: bool(x.get("IHC_union_block")), actual_total_pnl=-1000.0)
        self.assertEqual(m["blocked_count"], 1)

    def test_winner_block_lost_profit(self) -> None:
        t = _trade(pnl_yen_100=3000.0, exit_reason="take_profit")
        enriched = enrich_trades_with_shadow([t], price_idx={})
        m = scenario_metrics(enriched, block_pred=lambda x: bool(x.get("I_block")), actual_total_pnl=3000.0)
        self.assertEqual(m["lost_profit_yen"], 3000.0)
        self.assertEqual(m["avoided_loss_yen"], 0.0)

    def test_loser_block_avoided_loss(self) -> None:
        enriched = enrich_trades_with_shadow([_trade()], price_idx={})
        m = scenario_metrics(enriched, block_pred=lambda x: bool(x.get("I_block")), actual_total_pnl=-1000.0)
        self.assertEqual(m["avoided_loss_yen"], 1000.0)

    def test_actual_plus_delta_equals_counterfactual(self) -> None:
        enriched = enrich_trades_with_shadow([_trade(), _trade(position_id="p2", pnl_yen_100=200.0)], price_idx={})
        actual = -800.0
        m = scenario_metrics(enriched, block_pred=lambda x: bool(x.get("H_block")), actual_total_pnl=actual)
        self.assertEqual(m["counterfactual_total_pnl_yen"], actual + m["delta_pnl_yen"])

    def test_am_pm_sum_matches_daily(self) -> None:
        am = [_trade(position_id="a", pnl_yen_100=100.0)]
        pm = [_trade(position_id="b", pnl_yen_100=-50.0)]
        daily = enrich_trades_with_shadow(am + pm, price_idx={})
        daily_pnl = 50.0
        m = scenario_metrics(daily, block_pred=lambda x: bool(x.get("C_block")), actual_total_pnl=daily_pnl)
        self.assertEqual(m["actual_trade_count"], 2)

    def test_h_uses_accept_bounce(self) -> None:
        t = _trade(
            readiness_bounce_from_recent_low_accept=0.5,
            bounce_from_recent_low=0.01,
        )
        shadow = evaluate_trade_shadow_fields(t, saved_flags={"readiness_bounce_from_recent_low_accept": 0.5})
        self.assertTrue(shadow["H_block"])

    def test_c_uses_microseq_namespace(self) -> None:
        t = _trade(
            microseq_bounce_from_recent_low=0.25,
            microseq_fall_from_recent_high=-0.2,
            microseq_slope_5min=0.1,
            bounce_from_recent_low=0.01,
        )
        shadow = evaluate_trade_shadow_fields(
            t,
            saved_flags={
                "microseq_bounce_from_recent_low": 0.25,
                "microseq_fall_from_recent_high": -0.2,
                "microseq_slope_5min": 0.1,
            },
        )
        self.assertTrue(shadow["C_block"])

    def test_h_missing_no_c_substitute(self) -> None:
        t = _trade(readiness_bounce_from_recent_low_accept=None)
        shadow = evaluate_trade_shadow_fields(t, price_idx={})
        self.assertFalse(shadow["H_evaluable"])
        self.assertFalse(shadow["H_block"])

    def test_c_missing_no_h_substitute(self) -> None:
        t = _trade(
            microseq_bounce_from_recent_low=None,
            readiness_bounce_from_recent_low_accept=0.9,
        )
        shadow = evaluate_trade_shadow_fields(t, saved_flags={"readiness_bounce_from_recent_low_accept": 0.9})
        self.assertFalse(shadow["C_evaluable"])
        self.assertFalse(shadow["C_block"])

    def test_open_trade_excluded_from_finalize(self) -> None:
        events = [
            {"event_type": "accepted", "symbol": "AAA.T", "entry_time": "2026-07-10T09:30:00+09:00"},
        ]
        out = finalize_session_ihc_shadow_summary(
            events,
            events,
            session_dir=Path("."),
        )
        self.assertEqual(out, {})

    def test_duplicate_trade_not_double_counted(self) -> None:
        from small_paper.ihc_shadow_counterfactual import load_session_canonical_trades

        session = NATIVE / "results" / "small_paper" / "20260710" / "live_session_084821"
        if not session.is_dir():
            self.skipTest("7/10 AM session missing")
        trades, _ = load_session_canonical_trades(session, session_label="AM")
        ids = [t.get("position_id") for t in trades]
        self.assertEqual(len(ids), len(set(ids)))

    def test_daily_summary_persistence_shape(self) -> None:
        enriched = enrich_trades_with_shadow([_trade()], price_idx={})
        summary = build_daily_shadow_summary(enriched, actual_total_pnl=-1000.0)
        for key in ("readiness_precision_shadow", "readiness_economics_shadow", "microsequence_recovery_fail_shadow"):
            lane = summary[key]
            self.assertIn("block_count", lane)
            self.assertIn("counterfactual_total_pnl_yen", lane)
        self.assertIn("I_OR_H_OR_C_block_count", summary["shadow_ihc_portfolio"])
        lines = format_entry_shadow_discord_lines(summary)
        self.assertTrue(any("[ENTRY SHADOW]" in ln for ln in lines))


def test_phase684_audit_smoke():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper" / "20260710").is_dir():
        pytest.skip("7/10 paper missing")
    from research.phase684_20260710_ihc_counterfactual import run_audit

    report = run_audit(write_outputs=True)
    assert report["canonical_verification"]["DAILY"]["trade_count"] == 74
    assert report["canonical_verification"]["DAILY"]["total_pnl_yen_100"] == -28300.0
    assert report["verdict"] in (
        "IHC_20260710_COUNTERFACTUAL_READY",
        "PARTIAL_FEATURE_COVERAGE",
        "SHADOW_LOGGING_GAP_FOUND",
    )
