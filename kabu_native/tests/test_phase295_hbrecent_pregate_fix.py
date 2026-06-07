"""Phase295: entry_high_break_recent must be set before gate score on all paths."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from research.exposure_gate import REJECT_ENTRY_SCORE_V2_BELOW, GateDecision
from small_paper.entry_expectancy_score_shadow import (
    SCORE_POINTS_V2,
    _feature_token,
    compute_entry_expectancy_score_fields,
)
from small_paper.extended_entry_shadow import (
    compute_entry_high_break_recent_field,
    compute_entry_shadow_fields,
)
from small_paper.pilot_runner import _event_from_gate

JST = ZoneInfo("Asia/Tokyo")


def _hb_no_ring(entry_ts: float = 1300.0) -> list[tuple[float, float]]:
    return [
        (entry_ts - 500.0, 100.0),
        (entry_ts - 400.0, 100.5),
        (entry_ts - 250.0, 101.0),
        (entry_ts - 200.0, 101.2),
        (entry_ts - 60.0, 102.0),
        (entry_ts, 102.0),
    ]


class TestPhase295HBRecentPregateFix(unittest.TestCase):
    def test_pregate_field_is_bool_not_none(self) -> None:
        out = compute_entry_high_break_recent_field(
            trade={"current_price": 102.0},
            payload={"CurrentPrice": 102.0},
            price_ring=_hb_no_ring(),
            entry_ts=1300.0,
        )
        self.assertIn("entry_high_break_recent", out)
        self.assertIsInstance(out["entry_high_break_recent"], bool)
        self.assertIsNotNone(out["entry_high_break_recent"])

    def test_pregate_matches_full_shadow_hb(self) -> None:
        trade = {"current_price": 102.0, "rolling_mfe_pct": 0.005, "momentum_continuation_score": 0.25}
        payload = {"CurrentPrice": 102.0, "VWAP": 100.0, "HighPrice": 103.0}
        ring = _hb_no_ring()
        pre = compute_entry_high_break_recent_field(
            trade=trade, payload=payload, price_ring=ring, entry_ts=1300.0
        )
        full = compute_entry_shadow_fields(
            trade=trade,
            payload=payload,
            price_ring=ring,
            entry_ts=1300.0,
            session_momentum_samples=[],
        )
        self.assertEqual(pre["entry_high_break_recent"], full["entry_high_break_recent"])

    def test_reject_event_persists_entry_high_break_recent(self) -> None:
        trade = {
            "profile": "momentum_volume_v13_combined",
            "symbol": "9984.T",
            "entry_time": datetime(2026, 6, 4, 9, 30, tzinfo=JST).isoformat(),
            "exit_time": datetime(2026, 6, 4, 10, 0, tzinfo=JST).isoformat(),
            "continuation_quality_score": 0.5,
            "entry_high_break_recent": False,
            "max_continuation_duration": 10,
            "momentum_continuation_score": 0.2,
            "current_price": 5000.0,
            "trading_value": 1e10,
            "rolling_mae_pct": 0.0,
        }
        trade.update(compute_entry_expectancy_score_fields(trade=trade))
        decision = GateDecision(
            accept=False,
            reason=REJECT_ENTRY_SCORE_V2_BELOW,
            continuation_quality_score=0.5,
            quality_tier="",
            entry_expectancy_score_v2=trade.get("entry_expectancy_score_v2"),
            entry_score_v2_threshold=5,
            entry_score_v2_gate_pass=False,
        )
        ev = _event_from_gate(
            event_type="rejected",
            trade=trade,
            decision=decision,
            source="live",
            message_index=1,
            current_price=5000.0,
        )
        self.assertIn("entry_high_break_recent", ev)
        self.assertIsNotNone(ev["entry_high_break_recent"])
        self.assertFalse(ev["entry_high_break_recent"])

    def test_hbrecent_no_contributes_when_false_pre_gate(self) -> None:
        trade = {
            "entry_high_break_recent": False,
            "max_continuation_duration": 10,
            "momentum_continuation_score": 0.20,
            "current_price": 5000.0,
            "trading_value": 3e10,
            "rolling_mae_pct": -0.0003,
            "entry_order_book_imbalance": 0.50,
        }
        fields = compute_entry_expectancy_score_fields(trade=trade)
        self.assertGreaterEqual(int(fields["entry_expectancy_score_v2"]), 2)
        tok = _feature_token("HBRecent", trade)
        self.assertEqual(tok, "HBRecent:no")
        self.assertNotIn("HBRecent:no", SCORE_POINTS_V2)

    def test_hbrecent_none_does_not_fire_token(self) -> None:
        trade = {
            "entry_high_break_recent": None,
            "max_continuation_duration": 10,
            "momentum_continuation_score": 0.20,
            "current_price": 5000.0,
            "trading_value": 3e10,
            "rolling_mae_pct": -0.0003,
        }
        self.assertIsNone(_feature_token("HBRecent", trade))
        with_hb = {**trade, "entry_high_break_recent": False}
        score_none = int(compute_entry_expectancy_score_fields(trade=trade)["entry_expectancy_score_v2"])
        score_hb = int(compute_entry_expectancy_score_fields(trade=with_hb)["entry_expectancy_score_v2"])
        self.assertEqual(score_hb, score_none)


if __name__ == "__main__":
    unittest.main()
