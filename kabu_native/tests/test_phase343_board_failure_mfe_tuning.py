import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from research.phase343_board_failure_mfe_tuning import (
    Phase343BoardFailureMfeAggregator,
    phase343_adoption_assessment,
    trade_in_mfe_cohort,
)
from small_paper.board_failure_exit_tuning import (
    BoardFailureTuningPack,
    BoardFailureTuningVariant,
    default_phase343_variants,
    export_board_failure_tuning_trade_rows,
    mfe_filter_allows,
    make_position_id,
)

JST = ZoneInfo("Asia/Tokyo")


def _payload(
    *,
    bid_qty: float = 2000.0,
    ask_qty: float = 8000.0,
    price: float = 990.0,
    ts: str = "2026-06-05T10:00:00+09:00",
) -> dict:
    return {
        "BidPrice": price - 0.5,
        "AskPrice": price + 0.5,
        "BidQty": bid_qty,
        "AskQty": ask_qty,
        "CurrentPrice": price,
        "CurrentPriceTime": ts,
    }


class TestPhase343BoardFailureMfeTuning(unittest.TestCase):
    def test_default_variants_grid(self) -> None:
        variants = default_phase343_variants()
        self.assertEqual(len(variants), 8)
        ids = {v.variant_id for v in variants}
        self.assertIn("mfe_lt_0p3_confirm3", ids)
        self.assertIn("mfe_lt_0p3_confirm5", ids)

    def test_mfe_filter_blocks_high_mfe(self) -> None:
        self.assertTrue(mfe_filter_allows(peak_mfe_pct=0.15, max_mfe_pct=0.2))
        self.assertFalse(mfe_filter_allows(peak_mfe_pct=0.35, max_mfe_pct=0.3))

    def test_mfe_filter_blocks_trigger_for_high_mfe_variant(self) -> None:
        variant = BoardFailureTuningVariant("mfe_lt_0p2_confirm3", max_mfe_pct=0.2, confirm_ticks=3)
        pack = BoardFailureTuningPack(variants=(variant,))
        ent = datetime(2026, 6, 5, 10, 0, 0, tzinfo=JST)
        pid = make_position_id("9984.T", ent)
        entry_price = 1000.0
        pack.register_position(
            position_id=pid,
            symbol="9984.T",
            entry_time=ent,
            entry_price=entry_price,
            payload=_payload(bid_qty=6000, ask_qty=4000, price=entry_price),
            entry_shadow={},
        )
        prices = [995.0, 994.0, 993.0]
        for i, px in enumerate(prices):
            pack.record_holding_tick(
                symbol="9984.T",
                position_id=pid,
                entry_time=ent,
                payload=_payload(price=px, ts=f"2026-06-05T10:0{i}+09:00"),
                current_price=px,
                entry_price=entry_price,
                mfe_pct=0.25,
                entry_shadow={},
            )
        pack.finalize_position(
            position_id=pid,
            actual_exit_reason="stop_hit",
            actual_exit_time=datetime(2026, 6, 5, 10, 30, 0, tzinfo=JST),
            actual_exit_price=980.0,
            entry_price=entry_price,
        )
        rows = export_board_failure_tuning_trade_rows(pack)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["no_candidate_trigger"])

    def test_small_mfe_triggers(self) -> None:
        variant = BoardFailureTuningVariant("mfe_lt_0p3_confirm3", max_mfe_pct=0.3, confirm_ticks=3)
        pack = BoardFailureTuningPack(variants=(variant,))
        ent = datetime(2026, 6, 5, 10, 0, 0, tzinfo=JST)
        pid = make_position_id("9984.T", ent)
        entry_price = 1000.0
        pack.register_position(
            position_id=pid,
            symbol="9984.T",
            entry_time=ent,
            entry_price=entry_price,
            payload=_payload(bid_qty=6000, ask_qty=4000, price=entry_price),
            entry_shadow={},
        )
        for i, px in enumerate([995.0, 994.0, 993.0]):
            pack.record_holding_tick(
                symbol="9984.T",
                position_id=pid,
                entry_time=ent,
                payload=_payload(price=px, ts=f"2026-06-05T10:0{i}+09:00"),
                current_price=px,
                entry_price=entry_price,
                mfe_pct=0.1,
                entry_shadow={},
            )
        pack.finalize_position(
            position_id=pid,
            actual_exit_reason="stop_hit",
            actual_exit_time=datetime(2026, 6, 5, 10, 30, 0, tzinfo=JST),
            actual_exit_price=980.0,
            entry_price=entry_price,
        )
        rows = export_board_failure_tuning_trade_rows(pack)
        self.assertFalse(rows[0]["no_candidate_trigger"])

    def test_adoption_vs_phase342(self) -> None:
        metrics = {
            "delta_yen": 15000.0,
            "profit_factor": 0.25,
            "profit_take_miss_yen_100": -3000.0,
            "stop_hit_reduction_count": 2,
            "symbol_concentration": False,
            "improved_session_count": 2,
            "worsened_session_count": 1,
        }
        out = phase343_adoption_assessment(
            metrics,
            actual_total=-10000.0,
            actual_pf=0.21,
            phase342_baseline={"profit_take_miss_yen_100": -9520.0},
        )
        self.assertTrue(out["checks"]["profit_take_miss_vs_phase342"])
        self.assertTrue(out["checks"]["pf_not_worse_than_actual"])

    def test_aggregator_ingest(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            agg = Phase343BoardFailureMfeAggregator(reports_dir=Path(tmp))
            rows = []
            for vid in ("mfe_lt_0p2_confirm3", "mfe_lt_0p3_confirm3"):
                rows.append(
                    {
                        "position_id": "p1",
                        "symbol": "9984.T",
                        "variant_id": vid,
                        "max_mfe_pct": 0.2 if "0p2" in vid else 0.3,
                        "confirm_ticks": 3,
                        "peak_mfe_pct": 0.15,
                        "mfe_bucket": "mfe_lt_0p3",
                        "shadow_pnl_yen_100": -200.0,
                        "actual_pnl_yen_100": -500.0,
                        "actual_exit_reason": "stop_hit",
                        "candidate_vs_actual_delta_yen": 300.0,
                        "no_candidate_trigger": False,
                    }
                )
            agg.ingest_session(
                session_meta={"session_id": "s1", "day_key": "20260518"},
                trade_rows=rows,
                push_rows=1000,
                runtime_sec=1.0,
            )
            summary = agg.build_summary()
            self.assertEqual(summary["positions_evaluated"], 1)
            self.assertIn("mfe_lt_0p2_confirm3", summary["variants"])

    def test_mfe_cohort_filter(self) -> None:
        self.assertTrue(trade_in_mfe_cohort({"peak_mfe_pct": 0.15}, "mfe_lt_0p2"))
        self.assertFalse(trade_in_mfe_cohort({"peak_mfe_pct": 0.25}, "mfe_lt_0p2"))


if __name__ == "__main__":
    unittest.main()
