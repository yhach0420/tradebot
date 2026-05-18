"""paper_trade dry-run replay と lag guard 行更新のユニットテスト。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from market.yahoo.watch import (
    JST,
    IntradaySignals,
    PAPER_TRADE_LOGIC_VERSION,
    Quote,
    _PAPER_TRADE_ENTRY_QUALITY_CSV_FIELDNAMES,
    _PAPER_TRADE_VWAP_DIAG_CSV_FIELDS,
    _paper_trade_breakout_entry_quality_scores,
    _paper_trade_bump_entry_quality_summary,
    _paper_trade_bump_take_selection_counters,
    _paper_trade_compute_stop_take_for_signal,
    _paper_trade_csv_header_extend_phase2,
    _paper_trade_deferral_row_set_lag_fields,
    _paper_trade_default_runtime_controls,
    _paper_trade_entry_quality_csv_columns,
    _paper_trade_execution_counters_blank,
    _paper_trade_merge_runtime_controls,
    _paper_trade_recent_5m_range_from_extras,
    _paper_trade_structure_relaxed_gate,
)


class TestPaperTradeDeferralLagGuard(unittest.TestCase):
    def test_stale_signal_sets_notify_sent_zero(self) -> None:
        det = datetime(2026, 5, 13, 10, 0, 0, tzinfo=JST)
        notify = det + timedelta(seconds=200)
        row: dict[str, str] = {
            "notify_sent": "1",
            "skipped": "0",
            "skip_reason": "",
        }
        lag_sec, stale = _paper_trade_deferral_row_set_lag_fields(
            row,
            det_at=det,
            notify_t=notify,
            max_lag=120.0,
            lag_guard=True,
            poll_finished_at_jst="2026-05-13 10:03:20",
        )
        self.assertGreater(lag_sec, 120.0)
        self.assertTrue(stale)
        self.assertEqual(row["notify_sent"], "0")
        self.assertEqual(row["skipped"], "1")
        self.assertIn("STALE_SIGNAL_LAG_GT_120SEC", row["skip_reason"])
        self.assertEqual(row["signal_lag_sec"], "200.000")

    def test_fresh_signal_not_stale(self) -> None:
        det = datetime(2026, 5, 13, 10, 0, 0, tzinfo=JST)
        notify = det + timedelta(seconds=5)
        row: dict[str, str] = {"notify_sent": "0", "skipped": "0", "skip_reason": ""}
        lag_sec, stale = _paper_trade_deferral_row_set_lag_fields(
            row,
            det_at=det,
            notify_t=notify,
            max_lag=120.0,
            lag_guard=True,
            poll_finished_at_jst="2026-05-13 10:00:10",
        )
        self.assertFalse(stale)
        self.assertEqual(lag_sec, 5.0)
        self.assertNotIn("STALE_SIGNAL", row.get("skip_reason", ""))

    def test_dry_run_stale_continue_default_true(self) -> None:
        d = _paper_trade_default_runtime_controls()
        self.assertTrue(d.get("paper_trade_dry_run_continue_execution_on_stale", False))
        self.assertEqual(d.get("paper_structure_take_min_rr"), 0.55)
        self.assertEqual(d.get("paper_structure_relaxed_min_rr"), 0.35)
        self.assertTrue(d.get("paper_structure_take_priority"))
        self.assertEqual(d.get("paper_structure_resistance_proximity_pct"), 0.006)
        self.assertEqual(d.get("paper_structure_take_adjust_progress_pct"), 60.0)
        self.assertEqual(d.get("paper_structure_take_adjust_pullback_pct"), 0.2)
        self.assertEqual(d.get("paper_structure_take_adjust_peak_fail_count"), 2)
        self.assertTrue(d.get("paper_early_weak_exit_enabled"))
        self.assertEqual(d.get("paper_early_weak_exit_min_hold_sec"), 600.0)
        self.assertEqual(d.get("paper_early_weak_exit_progress_pct"), -10.0)
        self.assertTrue(d.get("paper_early_weak_exit_require_peak_fail"))
        self.assertEqual(d.get("paper_min_structure_rr_for_entry"), 0.15)
        self.assertEqual(d.get("paper_low_structure_rr_tier_mult"), 0.88)
        self.assertTrue(d.get("paper_low_structure_rr_entry_suppress_enabled"))
        self.assertTrue(d.get("paper_low_structure_rr_tier2_exclude_enabled"))
        m = _paper_trade_merge_runtime_controls({}, None)
        self.assertTrue(m.get("paper_trade_dry_run_continue_execution_on_stale", False))

    def test_execution_counters_include_stale_continued(self) -> None:
        c = _paper_trade_execution_counters_blank()
        self.assertIn("stale_execution_continued_count", c)
        self.assertEqual(int(c["stale_execution_continued_count"]), 0)
        self.assertIn("resistance_take_hit_count", c)
        self.assertIn("fixed_take_hit_count", c)
        self.assertIn("take_before_resistance_count", c)
        self.assertIn("structure_take_selected_count", c)
        self.assertIn("dynamic_rr_fallback_count", c)
        self.assertIn("structure_take_rr_relaxed_count", c)
        self.assertIn("early_weak_exit_count", c)
        self.assertIn("dynamic_rr_fallback_reason_counts", c)
        self.assertEqual(int(c["early_weak_exit_count"]), 0)
        self.assertEqual(int(c.get("low_structure_rr_suppressed_count") or 0), 0)
        self.assertEqual(c["dynamic_rr_fallback_reason_counts"], {})

    def test_bump_take_selection_counters_on_open(self) -> None:
        c = _paper_trade_execution_counters_blank()
        _paper_trade_bump_take_selection_counters(
            c,
            {"take_structure_selection": "STRUCTURE_RELAXED", "nearest_resistance": 10340.0},
        )
        self.assertEqual(int(c["structure_take_selected_count"]), 1)
        self.assertEqual(int(c["structure_take_rr_relaxed_count"]), 1)
        self.assertEqual(int(c.get("dynamic_rr_fallback_count") or 0), 0)
        _paper_trade_bump_take_selection_counters(
            c,
            {
                "take_structure_selection": "DYNAMIC",
                "nearest_resistance": 10340.0,
                "structure_take_reject_reason": "BELOW_GLOBAL_RR_FLOOR_NOT_RELAXABLE",
            },
        )
        self.assertEqual(int(c["dynamic_rr_fallback_count"]), 1)
        rc = c.get("dynamic_rr_fallback_reason_counts")
        self.assertIsInstance(rc, dict)
        self.assertEqual(int(rc.get("BELOW_GLOBAL_RR_FLOOR_NOT_RELAXABLE") or 0), 1)


class TestPaperTradeDynamicTake(unittest.TestCase):
    def test_compute_stop_take_includes_structure_diag_on_dynamic(self) -> None:
        rtc = dict(_paper_trade_default_runtime_controls())
        rtc["paper_dynamic_take_enabled"] = True
        rtc["paper_structure_take_enabled"] = True
        rtc["paper_structure_resistance_proximity_pct"] = 0.0005
        q = Quote(
            symbol="9999.T",
            price=1000.0,
            currency="JPY",
            previous_close=990.0,
            change_percent=1.0,
            day_high=1008.0,
            day_low=995.0,
            volume=1e6,
            market_time_utc=datetime(2026, 5, 13, 1, 0, 0, tzinfo=timezone.utc),
            market_cap=None,
        )
        intr = IntradaySignals(
            recent_5m_high=1002.0,
            price_5min_ago=998.0,
            vwap=999.0,
            vwap_distance_pct=0.1,
            vol_3m_gt_prev_3m=True,
        )
        _, _, ex = _paper_trade_compute_stop_take_for_signal(
            entry_nf=1000.0,
            q=q,
            intr=intr,
            pt_ex={},
            rtc=rtc,
        )
        self.assertIn("structure_take_candidate_count", ex)
        self.assertIn("structure_take_best_rr", ex)
        if ex.get("take_structure_selection") == "DYNAMIC":
            self.assertNotEqual(str(ex.get("structure_take_reject_reason") or "").strip(), "")

    def test_recent_5m_range_from_extras(self) -> None:
        highs = [100.0] * 6 + [102.0, 105.0]
        lows = [99.0] * 6 + [100.5, 101.0]
        hv = [float(x) for x in highs if isinstance(x, (int, float))]
        lv = [float(x) for x in lows if isinstance(x, (int, float))]
        w_h = hv[-6:-1]
        w_l = lv[-6:-1]
        expected = float(max(w_h)) - float(min(w_l))
        r = _paper_trade_recent_5m_range_from_extras({"highs_1m": highs, "lows_1m": lows})
        self.assertIsNotNone(r)
        self.assertAlmostEqual(float(r), expected, places=6)

    def test_dynamic_take_respects_day_high_and_rr_floor(self) -> None:
        rtc = dict(_paper_trade_default_runtime_controls())
        rtc["paper_dynamic_take_enabled"] = True
        # 近い recent_5m 壁が近接緩和で構造Takeに吸われないよう、近接しきい値を狭める
        rtc["paper_structure_resistance_proximity_pct"] = 0.002
        q = Quote(
            symbol="9999.T",
            price=1000.0,
            currency="JPY",
            previous_close=990.0,
            change_percent=1.0,
            day_high=1008.0,
            day_low=995.0,
            volume=1e6,
            market_time_utc=datetime(2026, 5, 13, 1, 0, 0, tzinfo=timezone.utc),
            market_cap=None,
        )
        intr = IntradaySignals(
            recent_5m_high=1002.0,
            price_5min_ago=998.0,
            vwap=999.0,
            vwap_distance_pct=0.1,
            vol_3m_gt_prev_3m=True,
        )
        stop, take, ex = _paper_trade_compute_stop_take_for_signal(
            entry_nf=1000.0,
            q=q,
            intr=intr,
            pt_ex={},
            rtc=rtc,
        )
        self.assertAlmostEqual(stop, 980.0, places=4)
        self.assertGreaterEqual(take, 1000.0 + (1000.0 - 980.0) * 1.0)
        self.assertIn(ex["take_calc_method"], ("dynamic_min_rr_floor", "structure_nearest_resistance"))
        self.assertIn("take_distance_pct", ex)
        self.assertIn("take_exit_kind", ex)

    def test_structure_take_prefers_previous_day_high_wall(self) -> None:
        rtc = dict(_paper_trade_default_runtime_controls())
        rtc["paper_structure_take_enabled"] = True
        rtc["paper_dynamic_take_enabled"] = True
        q = Quote(
            symbol="9999.T",
            price=1000.0,
            currency="JPY",
            previous_close=990.0,
            change_percent=1.0,
            day_high=None,
            day_low=995.0,
            volume=1e6,
            market_time_utc=datetime(2026, 5, 13, 1, 0, 0, tzinfo=timezone.utc),
            market_cap=None,
        )
        intr = IntradaySignals(
            recent_5m_high=None,
            price_5min_ago=998.0,
            vwap=995.0,
            vwap_distance_pct=0.1,
            vol_3m_gt_prev_3m=True,
        )
        _, take, ex = _paper_trade_compute_stop_take_for_signal(
            entry_nf=1000.0,
            q=q,
            intr=intr,
            pt_ex={"paper_daily_structure": {"previous_day_high": 1025.0}},
            rtc=rtc,
            ma25_screen=None,
        )
        self.assertEqual(ex["take_calc_method"], "structure_nearest_resistance")
        self.assertEqual(ex["take_selected_by"], "previous_day_high")
        self.assertLess(float(take), 1030.0)

    def test_structure_take_proximity_prefers_wall_over_rr_floor(self) -> None:
        """近接レジスタンスでは RR floor 未達でも構造Take（緩和）を採用する。"""
        rtc = dict(_paper_trade_default_runtime_controls())
        rtc["paper_structure_take_enabled"] = True
        rtc["paper_dynamic_take_enabled"] = True
        rtc["paper_structure_take_priority"] = True
        rtc["paper_structure_resistance_proximity_pct"] = 0.006
        rtc["paper_take_rr_floor_mult"] = 1.0
        entry = 10280.0
        q = Quote(
            symbol="9999.T",
            price=entry,
            currency="JPY",
            previous_close=10100.0,
            change_percent=0.5,
            day_high=11000.0,
            day_low=10100.0,
            volume=1e6,
            market_time_utc=datetime(2026, 5, 13, 1, 0, 0, tzinfo=timezone.utc),
            market_cap=None,
        )
        intr = IntradaySignals(
            recent_5m_high=None,
            price_5min_ago=entry - 10.0,
            vwap=10150.0,
            vwap_distance_pct=0.1,
            vol_3m_gt_prev_3m=True,
        )
        _, take, ex = _paper_trade_compute_stop_take_for_signal(
            entry_nf=entry,
            q=q,
            intr=intr,
            pt_ex={"paper_daily_structure": {"previous_day_high": 10340.0}},
            rtc=rtc,
            ma25_screen=None,
        )
        self.assertEqual(ex["take_calc_method"], "structure_nearest_resistance")
        self.assertEqual(ex["take_exit_kind"], "structure")
        self.assertEqual(ex["take_selected_by"], "nearest_resistance")
        self.assertEqual(ex["resistance_take_preferred"], "1")
        self.assertEqual(ex["take_structure_selection"], "STRUCTURE_RELAXED")
        self.assertLess(float(take), entry + (entry * 0.02) * 1.0 + 1.0)

    def test_structure_take_uses_lower_rr_when_global_floor_higher(self) -> None:
        """paper_take_rr_floor_mult > paper_structure_take_min_rr のとき、構造下限だけで採用。"""
        rtc = dict(_paper_trade_default_runtime_controls())
        rtc["paper_structure_take_enabled"] = True
        rtc["paper_dynamic_take_enabled"] = True
        rtc["paper_take_rr_floor_mult"] = 1.5
        rtc["paper_structure_take_min_rr"] = 1.0
        rtc["paper_structure_resistance_proximity_pct"] = 0.002
        entry = 1000.0
        q = Quote(
            symbol="9999.T",
            price=entry,
            currency="JPY",
            previous_close=990.0,
            change_percent=0.5,
            day_high=None,
            day_low=995.0,
            volume=1e6,
            market_time_utc=datetime(2026, 5, 13, 1, 0, 0, tzinfo=timezone.utc),
            market_cap=None,
        )
        intr = IntradaySignals(
            recent_5m_high=None,
            price_5min_ago=998.0,
            vwap=995.0,
            vwap_distance_pct=0.1,
            vol_3m_gt_prev_3m=True,
        )
        _, take, ex = _paper_trade_compute_stop_take_for_signal(
            entry_nf=entry,
            q=q,
            intr=intr,
            pt_ex={"paper_daily_structure": {"previous_day_high": 1025.0}},
            rtc=rtc,
            ma25_screen=None,
        )
        self.assertEqual(ex["take_calc_method"], "structure_nearest_resistance")
        self.assertEqual(ex["take_exit_kind"], "structure")
        self.assertEqual(ex["take_structure_selection"], "STRUCTURE_RELAXED")
        self.assertGreaterEqual(float(take), entry + (entry - entry * 0.98) * 1.0 - 1.0)
        self.assertLess(float(take), entry + (entry - entry * 0.98) * 1.5 - 1.0)

    def test_legacy_take_when_dynamic_disabled(self) -> None:
        rtc = dict(_paper_trade_default_runtime_controls())
        rtc["paper_dynamic_take_enabled"] = False
        q = Quote(
            symbol="9999.T",
            price=1000.0,
            currency="JPY",
            previous_close=990.0,
            change_percent=1.0,
            day_high=2000.0,
            day_low=900.0,
            volume=1e6,
            market_time_utc=datetime(2026, 5, 13, 1, 0, 0, tzinfo=timezone.utc),
            market_cap=None,
        )
        _, take, ex = _paper_trade_compute_stop_take_for_signal(
            entry_nf=1000.0,
            q=q,
            intr=None,
            pt_ex={},
            rtc=rtc,
        )
        self.assertAlmostEqual(take, 1040.0, places=4)
        self.assertEqual(ex["take_calc_method"], "legacy_fixed_4pct")


class TestPaperTradeEntryQuality(unittest.TestCase):
    def _synthetic_breakout_extras(self, n: int = 20) -> dict:
        entry = 1000.0
        opens, highs, lows, closes, vols = [], [], [], [], []
        for i in range(n - 1):
            opens.append(entry - 0.5)
            highs.append(entry - 0.2)
            lows.append(entry - 1.0)
            closes.append(entry - 0.3)
            vols.append(1000.0)
        opens.append(entry + 0.2)
        highs.append(entry + 2.5)
        lows.append(entry + 0.1)
        closes.append(entry + 2.0)
        vols.append(2500.0)
        for j in range(3):
            opens.append(entry + 1.5)
            highs.append(entry + 2.8 + j * 0.1)
            lows.append(entry + 1.0)
            closes.append(entry + 2.2)
            vols.append(1800.0)
        return {
            "opens_1m": opens,
            "highs_1m": highs,
            "lows_1m": lows,
            "closes_1m": closes,
            "vols_1m": vols,
        }

    def test_breakout_entry_quality_scores_in_range(self) -> None:
        rtc = dict(_paper_trade_default_runtime_controls())
        intr = IntradaySignals(
            recent_5m_high=1000.0,
            price_5min_ago=995.0,
            vwap=998.0,
            vwap_distance_pct=0.25,
            vol_3m_gt_prev_3m=True,
        )
        sc = _paper_trade_breakout_entry_quality_scores(
            self._synthetic_breakout_extras(),
            intr,
            price=1002.0,
            entry_nf=1000.0,
            rtc=rtc,
        )
        for k in (
            "breakout_vwap_hold_score",
            "breakout_candle_quality_score",
            "breakout_pullback_quality_score",
            "breakout_volume_continuation_score",
            "breakout_failure_risk_score",
            "entry_quality_score",
        ):
            self.assertIn(k, sc)
            self.assertGreaterEqual(float(sc[k]), 0.0)
            self.assertLessEqual(float(sc[k]), 1.0)
        self.assertGreater(float(sc["entry_quality_score"]), 0.35)

    def test_entry_quality_csv_columns_and_summary_bump(self) -> None:
        sc = {
            "breakout_vwap_hold_score": 0.8,
            "breakout_candle_quality_score": 0.7,
            "breakout_pullback_quality_score": 0.75,
            "breakout_volume_continuation_score": 0.6,
            "breakout_failure_risk_score": 0.2,
            "entry_quality_score": 0.78,
        }
        cols = _paper_trade_entry_quality_csv_columns(sc)
        self.assertEqual(cols["entry_quality_score"], "0.7800")
        self.assertEqual(cols["failure_reversal_penalty"], "")
        c = _paper_trade_execution_counters_blank()
        rtc = dict(_paper_trade_default_runtime_controls())
        _paper_trade_bump_entry_quality_summary(c, scores=sc, crossed=True, rtc=rtc)
        self.assertEqual(int(c["entry_quality_score_n"]), 1)
        self.assertAlmostEqual(float(c["entry_quality_score_sum"]), 0.78, places=4)
        self.assertEqual(int(c["strong_breakout_count"]), 1)
        self.assertEqual(int(c["failed_breakout_count"]), 0)

    def test_pullback_score_not_always_zero_on_shallow_retrace(self) -> None:
        rtc = dict(_paper_trade_default_runtime_controls())
        intr = IntradaySignals(
            recent_5m_high=100.0,
            price_5min_ago=99.0,
            vwap=99.5,
            vwap_distance_pct=0.3,
            vol_3m_gt_prev_3m=True,
        )
        opens, highs, lows, closes, vols = [], [], [], [], []
        for _ in range(14):
            opens.append(99.5)
            highs.append(99.8)
            lows.append(99.2)
            closes.append(99.6)
            vols.append(1000.0)
        opens.append(100.1)
        highs.append(101.2)
        lows.append(100.0)
        closes.append(101.0)
        vols.append(2000.0)
        for _ in range(4):
            opens.append(100.8)
            highs.append(101.3)
            lows.append(100.6)
            closes.append(101.1)
            vols.append(1500.0)
        sc = _paper_trade_breakout_entry_quality_scores(
            {"opens_1m": opens, "highs_1m": highs, "lows_1m": lows, "closes_1m": closes, "vols_1m": vols},
            intr,
            price=101.0,
            entry_nf=100.0,
            rtc=rtc,
        )
        self.assertGreater(float(sc["breakout_pullback_quality_score"]), 0.2)
        self.assertGreater(float(sc["debug_post_peak"]), float(sc["debug_post_breakout_low"]))

    def test_freshness_and_extension_fields_present(self) -> None:
        rtc = dict(_paper_trade_default_runtime_controls())
        intr = IntradaySignals(
            recent_5m_high=1000.0,
            price_5min_ago=995.0,
            vwap=998.0,
            vwap_distance_pct=0.25,
            vol_3m_gt_prev_3m=True,
        )
        sc = _paper_trade_breakout_entry_quality_scores(
            self._synthetic_breakout_extras(),
            intr,
            price=1002.0,
            entry_nf=1000.0,
            rtc=rtc,
        )
        self.assertIn("breakout_freshness_score", sc)
        self.assertIn("breakout_extension_pct", sc)
        self.assertGreaterEqual(float(sc["breakout_freshness_score"]), 0.0)
        self.assertGreater(float(sc["breakout_extension_pct"]), 0.0)

    def test_structure_relaxed_gate_blocks_low_quality(self) -> None:
        rtc = dict(_paper_trade_default_runtime_controls())
        bad_eq = {
            "breakout_vwap_hold_score": 0.3,
            "breakout_failure_risk_score": 0.8,
            "breakout_pullback_quality_score": 0.2,
            "breakout_freshness_score": 0.2,
            "entry_quality_score": 0.25,
        }
        ok, reason = _paper_trade_structure_relaxed_gate(
            relaxed_ok=True,
            proximity_relaxed_ok=False,
            st_best_rr_val=0.9,
            entry_quality_scores=bad_eq,
            rtc=rtc,
        )
        self.assertFalse(ok)
        self.assertTrue(str(reason).startswith("RELAXED_QUALITY_"))

    def test_logic_version_constant(self) -> None:
        self.assertIn("continuation", PAPER_TRADE_LOGIC_VERSION)


class TestPaperTradeCsvHeader(unittest.TestCase):
    def test_extend_phase2_inserts_vwap_diag_after_vwap_under_duration(self) -> None:
        """旧 dry-run header（VWAP診断5列欠落）を fieldnames に揃える。"""
        legacy = [
            "vwap_under_duration_bars",
            "failure_upper_wick_penalty",
            "entry_quality_score",
        ]
        header = _paper_trade_csv_header_extend_phase2(legacy)
        for col in _PAPER_TRADE_VWAP_DIAG_CSV_FIELDS:
            self.assertIn(col, header)
        anchor = header.index("vwap_under_duration_bars")
        block = header[anchor + 1 : anchor + 1 + len(_PAPER_TRADE_VWAP_DIAG_CSV_FIELDS)]
        self.assertEqual(tuple(block), _PAPER_TRADE_VWAP_DIAG_CSV_FIELDS)

    def test_entry_quality_csv_columns_keys_match_fieldnames_tuple(self) -> None:
        cols = _paper_trade_entry_quality_csv_columns(None)
        self.assertEqual(tuple(cols.keys()), _PAPER_TRADE_ENTRY_QUALITY_CSV_FIELDNAMES)
        for col in _PAPER_TRADE_VWAP_DIAG_CSV_FIELDS:
            self.assertIn(col, cols)


if __name__ == "__main__":
    unittest.main()
