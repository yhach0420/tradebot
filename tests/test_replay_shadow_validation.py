"""Shadow filter validation helpers (replay shared-engine diagnostics)."""
from __future__ import annotations

import unittest

from yahoo_kabu_watch import (
    _apply_replay_config_to_flags,
    _build_multi_day_shadow_summary,
    _build_replay_shadow_filter_validation,
    _chase_extension_bucket,
    _compute_market_context_scores,
    _enrich_signal_dicts_quality_ranks,
    _paper_trade_phase2_compute_entry_context,
    _paper_trade_phase2_record_signal,
    _paper_trade_phase2_shadow_from_csv_rows,
    _paper_trade_rtc_merge_phase2_from_cfg,
    _parse_replay_shadow_multi_day_list,
    _replay_shared_engine_trace_row,
    _run_replay_validate_params,
)


def _sig(
    *,
    sym: str = "9984.T",
    pnl: float = -100.0,
    er: str = "VWAP_BREAK_EARLY",
    pc_prev: float | None = 0.6,
    hu: int = 7,
    eq: float = 0.4,
) -> dict:
    mdf: dict = {"prev_signal_exists": pc_prev is not None}
    if pc_prev is not None:
        mdf["price_change_pct_from_prev_signal"] = pc_prev
        mdf["volume_efficiency_pct"] = 0.2
    return {
        "symbol": sym,
        "position_kind": "BASE",
        "excluded_from_eval": False,
        "pnl_yen_100_shares": pnl,
        "exit_reason": er,
        "entry_price": 100.0,
        "high_update_count_before_entry": hu,
        "breakout_volume_continuation_score": 0.3,
        "entry_quality_score": eq,
        "vwap_break_early_risk_score": 0.7,
        "momentum_decay_features": mdf,
        "structure_take_reject_reason": "BELOW_GLOBAL_RR_FLOOR_NOT_RELAXABLE",
        "take_structure_selection": "DYNAMIC",
        "nearest_resistance": 105.0,
    }


class TestReplayShadowValidation(unittest.TestCase):
    def test_replay_date_fixed_bypasses_replay_range(self) -> None:
        code, msg = _run_replay_validate_params("fixed", "2026-05-15")
        self.assertEqual(code, 0)
        self.assertEqual(msg, "")

    def test_invalid_replay_range_without_fixed_date(self) -> None:
        code, msg = _run_replay_validate_params("fixed", "")
        self.assertEqual(code, 2)
        self.assertIn("replay_range_invalid", msg)

    def test_parse_shadow_multi_day_list(self) -> None:
        days, err = _parse_replay_shadow_multi_day_list("2026-05-13, 2026-05-14")
        self.assertEqual(err, "")
        self.assertEqual(days, ["2026-05-13", "2026-05-14"])

    def test_chase_extension_shadow_blocks_high_extension(self) -> None:
        cohort = [_sig(pc_prev=0.6), _sig(pc_prev=0.1, pnl=50.0, er="TAKE_HIT")]
        rep = _build_replay_shadow_filter_validation(cohort)
        rows = rep.get("shadow_chase_extension_table") or []
        r03 = next(x for x in rows if x.get("threshold") == ">=0.3%")
        self.assertEqual(int(r03["blocked_count"]), 1)
        self.assertGreater(float(r03["pnl_improvement"]), 0.0)

    def test_shared_engine_trace_row_fields(self) -> None:
        row = _replay_shared_engine_trace_row(
            event="SIGNAL_OPEN",
            symbol="7203.T",
            timestamp_jst="2026-05-15 09:10:00",
            engine_mode="paper_position_exec",
            entry_price=100.0,
            stop_price=98.0,
            take_price=104.0,
            eq_scores={"entry_quality_score": 0.55, "breakout_freshness_score": 0.6},
            take_csv={
                "take_structure_selection": "DYNAMIC",
                "structure_take_reject_reason": "BELOW_GLOBAL_RR_FLOOR",
                "nearest_resistance": "105.50",
                "dynamic_fallback_policy": "wall_scalp",
            },
        )
        self.assertEqual(row["take_structure_selection"], "DYNAMIC")
        self.assertEqual(row["event_type"], "OPEN_POSITION")
        self.assertAlmostEqual(float(row["nearest_resistance"]), 105.5)
        self.assertEqual(row["dynamic_fallback_reason"], "wall_scalp")

    def test_market_weakness_scores(self) -> None:
        m = _compute_market_context_scores(
            rising_ratio=0.35,
            high_ratio=0.1,
            topix_chg=-1.6,
            market_regime="CRASH",
            fail_rate30=0.7,
        )
        self.assertGreater(float(m["market_weakness_score"]), 0.7)
        self.assertAlmostEqual(float(m["lt50_ratio"]), 0.65)

    def test_chase_extension_bucket(self) -> None:
        self.assertEqual(_chase_extension_bucket(0.2), "lt_0.3")
        self.assertEqual(_chase_extension_bucket(0.55), "0.5_0.7")

    def test_config_phase2_defaults_off(self) -> None:
        flags = _apply_replay_config_to_flags(cfg={})
        self.assertFalse(flags.get("replay_chase_extension_autoblock_enabled"))
        self.assertTrue(flags.get("same_symbol_cooldown_shadow_only"))
        self.assertIsNone(flags.get("paper_entry_quality_min_for_open"))

    def test_paper_rtc_merge_phase2(self) -> None:
        rtc = _paper_trade_rtc_merge_phase2_from_cfg(
            {"replay_chase_extension_autoblock_enabled": True, "same_symbol_cooldown_sec": 600},
            {"same_symbol_cooldown_sec": 300},
        )
        self.assertTrue(rtc.get("replay_chase_extension_autoblock_enabled"))
        self.assertEqual(int(rtc.get("same_symbol_cooldown_sec")), 600)

    def test_paper_phase2_shadow_from_csv_rows(self) -> None:
        rows = [
            {
                "signal_type": "LIVE",
                "skipped": "0",
                "symbol": "7203.T",
                "pnl_yen_100_shares": "-50",
                "chase_extension_pct": "0.6",
                "entry_quality_score": "0.35",
                "rising_ratio": "0.4",
                "lt50_ratio": "0.6",
                "market_weakness_score": "0.75",
            }
        ]
        sh = _paper_trade_phase2_shadow_from_csv_rows(rows)
        self.assertIn("shadow_market_weakness_block_table", sh)

    def test_paper_phase2_cooldown_would_block(self) -> None:
        from datetime import datetime, timezone

        st: dict = {}
        t0 = datetime(2026, 5, 15, 9, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 5, 15, 9, 2, tzinfo=timezone.utc)
        _paper_trade_phase2_record_signal(
            st, day_jst="2026-05-15", symbol="7203.T", entry_price=99.0, signal_time=t0
        )
        ctx = _paper_trade_phase2_compute_entry_context(
            state=st,
            rtc={"same_symbol_cooldown_sec": 300, "same_symbol_cooldown_shadow_only": True},
            day_jst="2026-05-15",
            symbol="7203.T",
            entry_nf=100.0,
            signal_time=t1,
            market_snap={"market_regime": "NORMAL", "market_weakness_score": 0.5},
            entry_quality_score=0.5,
        )
        self.assertTrue(ctx.get("same_symbol_cooldown_would_block"))
        self.assertFalse(ctx.get("phase2_would_hard_block"))

    def test_quality_rank_enrichment(self) -> None:
        sigs = [
            {**_sig(eq=0.2), "day_jst": "2026-05-15", "symbol": "A.T"},
            {**_sig(eq=0.8, pnl=10.0, er="TAKE_HIT"), "day_jst": "2026-05-15", "symbol": "A.T"},
        ]
        _enrich_signal_dicts_quality_ranks(sigs)
        self.assertEqual(float(sigs[0]["quality_rank_in_symbol"]), 0.0)
        self.assertEqual(float(sigs[1]["quality_rank_in_symbol"]), 1.0)

    def test_multi_day_summary_aggregates(self) -> None:
        d1 = {
            "day_jst": "2026-05-13",
            "shadow_validation": {
                "shadow_chase_extension_table": [
                    {"threshold": ">=0.3%", "pnl_improvement": 100.0, "blocked_count": 1},
                ],
            },
        }
        d2 = {
            "day_jst": "2026-05-14",
            "shadow_validation": {
                "shadow_chase_extension_table": [
                    {"threshold": ">=0.3%", "pnl_improvement": -50.0, "blocked_count": 2},
                ],
            },
        }
        multi = _build_multi_day_shadow_summary([d1, d2])
        rows = multi.get("multi_day_shadow_summary") or []
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(float(rows[0]["avg_improvement"]), 25.0)
        self.assertEqual(float(rows[0]["days_positive_ratio"]), 0.5)
        self.assertIn("multi_day_cooldown_summary", multi)


if __name__ == "__main__":
    unittest.main()
