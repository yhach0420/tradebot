import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research.phase346_board_failure_false_positive_guard import (
    COHORT_ROBUSTNESS,
    Phase346BoardFailureFalsePositiveGuardAggregator,
)
from small_paper.board_failure_false_positive_guard import (
    BASE_VARIANT_ID,
    BoardFailureGuardTuningPack,
    BoardFailureGuardVariant,
    default_phase346_variants,
    entry_cooldown_blocks,
    price_reclaim_cancels,
    reclaim_window_active,
)

JST = timezone(timedelta(hours=9))


class TestPhase346GuardHelpers(unittest.TestCase):
    def test_entry_cooldown_blocks(self) -> None:
        entry = datetime(2026, 5, 28, 9, 0, 0, tzinfo=JST)
        tick = entry + timedelta(seconds=20)
        self.assertTrue(entry_cooldown_blocks(entry_time=entry, tick_time=tick, cooldown_sec=30))
        self.assertFalse(entry_cooldown_blocks(entry_time=entry, tick_time=tick, cooldown_sec=0))

    def test_price_reclaim_cancels(self) -> None:
        self.assertTrue(price_reclaim_cancels(price=101.0, arm_low=100.0))
        self.assertFalse(price_reclaim_cancels(price=99.0, arm_low=100.0))

    def test_reclaim_window_active(self) -> None:
        self.assertTrue(reclaim_window_active(ticks_since_arm=3, reclaim_ticks=5))
        self.assertFalse(reclaim_window_active(ticks_since_arm=6, reclaim_ticks=5))

    def test_default_variants(self) -> None:
        variants = default_phase346_variants()
        ids = {v.variant_id for v in variants}
        self.assertIn(BASE_VARIANT_ID, ids)
        self.assertIn(f"{BASE_VARIANT_ID}_cd60", ids)
        self.assertIn(f"{BASE_VARIANT_ID}_rc5", ids)
        self.assertIn(f"{BASE_VARIANT_ID}_cd60_rc5", ids)
        self.assertEqual(len(variants), 8)


class TestPhase346GuardPack(unittest.TestCase):
    def test_cooldown_suppresses_early_arm(self) -> None:
        pack = BoardFailureGuardTuningPack(
            variants=(
                BoardFailureGuardVariant(
                    variant_id="cd30",
                    entry_cooldown_sec=30,
                ),
            )
        )
        entry = datetime(2026, 5, 28, 9, 0, 0, tzinfo=JST)
        pack.register_position(
            position_id="p1",
            symbol="9984.T",
            entry_time=entry,
            entry_price=1000.0,
            payload={
                "BidQty": 100,
                "AskQty": 100,
                "CurrentPriceTime": entry.isoformat(),
            },
            entry_shadow={},
        )
        tick_payload = {
            "BidQty": 50,
            "AskQty": 200,
            "CurrentPriceTime": (entry + timedelta(seconds=5)).isoformat(),
        }
        pack.record_holding_tick(
            symbol="9984.T",
            position_id="p1",
            entry_time=entry,
            payload=tick_payload,
            current_price=990.0,
            entry_price=1000.0,
            mfe_pct=0.0,
            entry_shadow={},
        )
        st = pack.positions["p1"].variant_states["cd30"]
        self.assertFalse(st.triggered)
        self.assertFalse(st.armed)

    def test_reclaim_disarms_exit_candidate(self) -> None:
        pack = BoardFailureGuardTuningPack(
            variants=(
                BoardFailureGuardVariant(
                    variant_id="rc3",
                    confirm_ticks=3,
                    reclaim_ticks=3,
                ),
            )
        )
        entry = datetime(2026, 5, 28, 9, 0, 0, tzinfo=JST)
        pack.register_position(
            position_id="p1",
            symbol="9984.T",
            entry_time=entry,
            entry_price=1000.0,
            payload={
                "BidQty": 100,
                "AskQty": 100,
                "CurrentPriceTime": entry.isoformat(),
            },
            entry_shadow={},
        )
        prices = [990.0, 985.0, 991.0, 992.0, 993.0]
        for i, price in enumerate(prices):
            pack.record_holding_tick(
                symbol="9984.T",
                position_id="p1",
                entry_time=entry,
                payload={
                    "BidQty": 50,
                    "AskQty": 200,
                    "CurrentPriceTime": (entry + timedelta(seconds=i + 1)).isoformat(),
                },
                current_price=price,
                entry_price=1000.0,
                mfe_pct=0.0,
                entry_shadow={},
            )
        st = pack.positions["p1"].variant_states["rc3"]
        self.assertFalse(st.triggered)


class TestPhase346Aggregator(unittest.TestCase):
    def test_ingest_and_guard_assessment(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            agg = Phase346BoardFailureFalsePositiveGuardAggregator(reports_dir=Path(tmp))
            rows = [
                {
                    "position_id": "p1",
                    "symbol": "9984.T",
                    "variant_id": BASE_VARIANT_ID,
                    "shadow_pnl_yen_100": 1000.0,
                    "actual_pnl_yen_100": 5000.0,
                    "actual_exit_reason": "trailing_mfe_exit",
                    "candidate_vs_actual_delta_yen": -4000.0,
                    "no_candidate_trigger": False,
                    "forensic_class": "B_false_positive",
                    "peak_mfe_pct": 0.1,
                },
                {
                    "position_id": "p1",
                    "symbol": "9984.T",
                    "variant_id": f"{BASE_VARIANT_ID}_cd60",
                    "shadow_pnl_yen_100": 5000.0,
                    "actual_pnl_yen_100": 5000.0,
                    "actual_exit_reason": "trailing_mfe_exit",
                    "candidate_vs_actual_delta_yen": 0.0,
                    "no_candidate_trigger": True,
                    "forensic_class": "N_no_shadow",
                    "peak_mfe_pct": 0.1,
                },
            ]
            agg.ingest_session(
                session_meta={
                    "session_id": "s1",
                    "day_key": "20260528",
                    "session_cohort": COHORT_ROBUSTNESS,
                },
                trade_rows=rows,
                push_rows=100,
                runtime_sec=1.0,
            )
            summary = agg.build_summary()
            robustness = summary["cohorts"][COHORT_ROBUSTNESS]
            self.assertIn(BASE_VARIANT_ID, robustness)
            self.assertEqual(robustness[BASE_VARIANT_ID]["false_positive_count"], 1)
            assess = summary["guard_pass_assessment"][f"{BASE_VARIANT_ID}_cd60"]
            self.assertIn("guard_pass", assess)


if __name__ == "__main__":
    unittest.main()
