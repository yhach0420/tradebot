import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from small_paper.exit_candidate_shadow import (
    EXIT_CANDIDATE_IDS,
    EXTEND_CANDIDATE_ID,
    ExitCandidateShadowPack,
    evaluate_board_collapse_profit_exit,
    evaluate_high_update_failure_exit,
    evaluate_loss_acceleration_exit,
    evaluate_profit_protect_exit,
    evaluate_strength_hold_extend,
    evaluate_vwap_assisted_loss_exit,
    export_exit_candidate_trade_rows,
    make_position_id,
)

JST = ZoneInfo("Asia/Tokyo")


def _payload(*, bid_qty: float = 6000.0, ask_qty: float = 4000.0, vwap: float | None = None) -> dict:
    out = {
        "BidPrice": 1000.0,
        "AskPrice": 1001.0,
        "BidQty": bid_qty,
        "AskQty": ask_qty,
        "CurrentPrice": 1000.5,
        "CurrentPriceTime": "2026-06-05T10:00:00+09:00",
    }
    if vwap is not None:
        out["VWAP"] = vwap
    return out


class TestPhase337ExitCandidateShadow(unittest.TestCase):
    def test_candidate_evaluators(self) -> None:
        base = {
            "current_pnl_pct": -0.5,
            "mfe_pct": 0.2,
            "board_imbalance_delta": -0.06,
            "price_action_deteriorated": True,
        }
        self.assertTrue(evaluate_loss_acceleration_exit(base))
        self.assertFalse(
            evaluate_loss_acceleration_exit({**base, "current_pnl_pct": -0.2})
        )

        profit = {
            "current_pnl_pct": 0.3,
            "mfe_pct": 0.8,
            "board_imbalance_delta": -0.06,
            "high_update_stalled_or_slow": True,
        }
        self.assertTrue(evaluate_profit_protect_exit(profit))

        collapse = {
            "current_pnl_pct": 0.4,
            "board_imbalance_delta": -0.10,
            "spread_or_bid_weakness": True,
            "collapse_consecutive": 2,
        }
        self.assertTrue(evaluate_board_collapse_profit_exit(collapse))
        self.assertFalse(
            evaluate_board_collapse_profit_exit({**collapse, "collapse_consecutive": 1})
        )

        high_fail = {
            "current_pnl_pct": 0.2,
            "mfe_pct": 0.7,
            "ticks_since_high_update": 3,
            "board_imbalance_delta": -0.06,
        }
        self.assertTrue(evaluate_high_update_failure_exit(high_fail))

        vwap_loss = {
            "current_pnl_pct": -0.3,
            "board_imbalance_delta": -0.06,
            "below_vwap": True,
            "vwap_available": True,
        }
        self.assertTrue(evaluate_vwap_assisted_loss_exit(vwap_loss))
        self.assertFalse(
            evaluate_vwap_assisted_loss_exit({**vwap_loss, "vwap_available": False})
        )

        extend = {
            "mfe_pct": 1.2,
            "board_imbalance_delta": 0.02,
            "board_maintained": True,
            "near_high": True,
        }
        self.assertTrue(evaluate_strength_hold_extend(extend))

    def test_one_trigger_per_candidate_per_position(self) -> None:
        pack = ExitCandidateShadowPack()
        ent = datetime(2026, 6, 5, 10, 0, 0, tzinfo=JST)
        pid = make_position_id("9984.T", ent)
        pack.register_position(
            position_id=pid,
            symbol="9984.T",
            entry_time=ent,
            entry_price=1000.0,
            payload=_payload(),
            entry_shadow={},
        )
        bad = _payload(bid_qty=1500.0, ask_qty=8500.0)
        for px in (996.0, 995.0, 994.0):
            pack.record_holding_tick(
                symbol="9984.T",
                position_id=pid,
                entry_time=ent,
                payload=bad,
                current_price=px,
                entry_price=1000.0,
                mfe_pct=0.1,
                entry_shadow={},
            )
        st = pack.positions[pid].candidate_states["loss_acceleration_exit"]
        self.assertTrue(st.triggered)
        pack.finalize_position(
            position_id=pid,
            actual_exit_reason="stop_hit",
            actual_exit_time=datetime(2026, 6, 5, 10, 5, 0, tzinfo=JST),
            actual_exit_price=988.0,
            entry_price=1000.0,
        )
        rows = export_exit_candidate_trade_rows(pack)
        self.assertEqual(len(rows), len(EXIT_CANDIDATE_IDS))
        loss_rows = [r for r in rows if r["candidate_id"] == "loss_acceleration_exit"]
        self.assertEqual(len(loss_rows), 1)
        self.assertFalse(loss_rows[0]["no_candidate_trigger"])

    def test_no_trigger_matches_actual_pnl(self) -> None:
        pack = ExitCandidateShadowPack()
        ent = datetime(2026, 6, 5, 10, 0, 0, tzinfo=JST)
        pid = make_position_id("7203.T", ent)
        pack.register_position(
            position_id=pid,
            symbol="7203.T",
            entry_time=ent,
            entry_price=2000.0,
            payload=_payload(),
            entry_shadow={},
        )
        pack.record_holding_tick(
            symbol="7203.T",
            position_id=pid,
            entry_time=ent,
            payload=_payload(),
            current_price=2005.0,
            entry_price=2000.0,
            mfe_pct=0.25,
            entry_shadow={},
        )
        pack.finalize_position(
            position_id=pid,
            actual_exit_reason="trailing_mfe",
            actual_exit_time=datetime(2026, 6, 5, 10, 10, 0, tzinfo=JST),
            actual_exit_price=2010.0,
            entry_price=2000.0,
        )
        rows = export_exit_candidate_trade_rows(pack)
        for row in rows:
            self.assertTrue(row["no_candidate_trigger"])
            self.assertEqual(row["shadow_pnl_yen_100"], row["actual_pnl_yen_100"])
            self.assertEqual(row["candidate_vs_actual_delta_yen"], 0.0)

    def test_extend_candidate_no_virtual_exit(self) -> None:
        self.assertNotIn(EXTEND_CANDIDATE_ID, EXIT_CANDIDATE_IDS)


if __name__ == "__main__":
    unittest.main()
