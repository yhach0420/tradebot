import csv
import json
import tempfile
import unittest
from pathlib import Path

from small_paper.observer_position_tracker import OBSERVER_EXIT, ObserverJudgmentEvent
from small_paper.phase180_symbol_diagnostics import aggregate_symbol_diagnostics
from small_paper.pilot_runner import (
    EVENT_FIELDS,
    _enrich_accept_audit_fields,
    _event_from_gate,
    _observer_exit_event_row,
)


class TestPhase180Logging(unittest.TestCase):
    def test_event_fields_include_phase180_columns(self) -> None:
        required = (
            "tick_size",
            "hold_sec",
            "entry_price",
            "exit_price",
            "structural_exit_reason",
            "peak_mfe_pct",
            "trailing_mfe_activated",
            "stop_hit",
            "session_close",
            "overlap_replaced_review",
        )
        for key in required:
            self.assertIn(key, EVENT_FIELDS)

    def test_observer_exit_row_maps_context(self) -> None:
        ev = ObserverJudgmentEvent(
            kind=OBSERVER_EXIT,
            symbol="3905.T",
            context={
                "exit_reason": "stop_hit",
                "is_structural_exit": True,
                "entry_time": "2026-05-28T10:00:00+09:00",
                "exit_time": "2026-05-28T10:02:00+09:00",
                "hold_sec": 120,
                "entry_price": 500,
                "current_price": 490,
                "unrealized_pnl_pct": -2.0,
                "peak_mfe_pct": 0.5,
                "rolling_mae_pct": -2.0,
                "stop_hit": True,
            },
        )
        row = _observer_exit_event_row(ev, source="live", message_index=9, profile="q070")
        self.assertEqual(row["event_type"], "observer_exit")
        self.assertEqual(row["symbol"], "3905.T")
        self.assertTrue(row["stop_hit"])
        self.assertEqual(row["exit_reason"], "stop_hit")

    def test_accepted_event_includes_suitability_from_trade(self) -> None:
        from research.exposure_gate import GateDecision

        trade = {
            "symbol": "9984.T",
            "profile": "q070",
            "entry_time": "2026-05-28T09:00:00+09:00",
            "daytrade_suitability_score": 0.72,
            "daytrade_suitability_threshold": 0.55,
            "atr_pct": 1.2,
            "intraday_range_pct": 2.0,
            "trading_value": 2e9,
            "turnover_proxy": 0.01,
            "tick_size": 1.0,
            "tick_ratio_pct": 0.1,
            "current_price": 8000,
            "low_liquidity_shadow_rejected": False,
            "low_liquidity_shadow_reason": "",
            "low_liquidity_shadow_trading_value": 2e9,
            "low_liquidity_shadow_turnover_proxy": 0.01,
        }
        decision = GateDecision(accept=True, reason="", continuation_quality_score=0.7, quality_tier="top")
        ev = _event_from_gate(
            event_type="accepted",
            trade=trade,
            decision=decision,
            source="live",
            message_index=1,
            current_price=8000,
        )
        self.assertEqual(ev.get("daytrade_suitability_score"), 0.72)
        self.assertEqual(ev.get("trading_value"), 2e9)
        self.assertEqual(ev.get("tick_size"), 1.0)

    def test_aggregate_tolerates_legacy_minimal_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session = Path(td) / "live_session_test"
            session.mkdir()
            path = session / "small_paper_events.csv"
            with path.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(
                    f,
                    fieldnames=["event_time", "event_type", "symbol", "gate_accept"],
                )
                w.writeheader()
                w.writerow(
                    {
                        "event_time": "2026-05-28T09:00:00+09:00",
                        "event_type": "accepted",
                        "symbol": "7203.T",
                        "gate_accept": "True",
                    }
                )
            agg = aggregate_symbol_diagnostics([session])
            self.assertEqual(agg["symbol_count"], 1)
            row = agg["symbols"][0]
            self.assertEqual(row["symbol"], "7203.T")
            self.assertEqual(row["observer_exit_count"], 0)
            self.assertIn(row["verdict"], ("keep", "watch", "exclude_candidate"))


if __name__ == "__main__":
    unittest.main()
