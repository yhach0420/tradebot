"""Phase635: PBv2-only rise5 shadow guard tests."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from small_paper.or_overlay_cap import ENTRY_TYPE_OR
from small_paper.pbv2_rise5_shadow import (
    APPLY_POOL_PBV2_ONLY,
    PbV2Rise5ShadowCounters,
    compute_pbv2_rise5_shadow_fields,
    enrich_exit_pbv2_rise5_shadow_fields,
    rise5_shadow_enabled,
    shadow_applies_to_trade,
    would_block_pbv2_rise5_shadow,
)


def _cfg(**kwargs) -> SimpleNamespace:
    base = {
        "pbv2_rise5_shadow_enabled": True,
        "pbv2_rise5_shadow_threshold_pct": 1.84,
        "pbv2_rise5_shadow_apply_pool": APPLY_POOL_PBV2_ONLY,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestPbV2Rise5ShadowFields(unittest.TestCase):
    def test_pbv2_blocks_when_rise5_above_threshold(self) -> None:
        fields = compute_pbv2_rise5_shadow_fields(
            _cfg(),
            {"entry_type": "PBV2", "entry_rise_5min_pct": 2.5},
        )
        self.assertTrue(fields["pbv2_rise5_shadow_block"])
        self.assertEqual(fields["pbv2_rise5_shadow_reason"], "entry_rise_5min_pct_above_threshold")
        self.assertEqual(fields["pbv2_rise5_threshold"], 1.84)

    def test_or_not_shadow_target(self) -> None:
        fields = compute_pbv2_rise5_shadow_fields(
            _cfg(),
            {"entry_type": ENTRY_TYPE_OR, "entry_rise_5min_pct": 5.0},
        )
        self.assertFalse(fields["pbv2_rise5_shadow_block"])
        self.assertFalse(
            would_block_pbv2_rise5_shadow(fields, threshold=1.84, apply_pool=APPLY_POOL_PBV2_ONLY)
        )
        self.assertFalse(shadow_applies_to_trade(fields, apply_pool=APPLY_POOL_PBV2_ONLY))

    def test_missing_rise5_fail_open(self) -> None:
        fields = compute_pbv2_rise5_shadow_fields(_cfg(), {"entry_type": "PBV2"})
        self.assertFalse(fields["pbv2_rise5_shadow_block"])
        self.assertEqual(fields["pbv2_rise5_shadow_reason"], "rise5_missing_fail_open")

    def test_keeps_when_rise5_at_threshold(self) -> None:
        fields = compute_pbv2_rise5_shadow_fields(
            _cfg(),
            {"entry_type": "PBV2", "entry_rise_5min_pct": 1.84},
        )
        self.assertFalse(fields["pbv2_rise5_shadow_block"])

    def test_disabled_config(self) -> None:
        self.assertFalse(rise5_shadow_enabled(_cfg(pbv2_rise5_shadow_enabled=False)))
        fields = compute_pbv2_rise5_shadow_fields(
            _cfg(pbv2_rise5_shadow_enabled=False),
            {"entry_type": "PBV2", "entry_rise_5min_pct": 9.0},
        )
        self.assertFalse(fields["pbv2_rise5_shadow_block"])

    def test_exit_shadow_removes_blocked_pnl(self) -> None:
        entry_shadow = compute_pbv2_rise5_shadow_fields(
            _cfg(),
            {"entry_type": "PBV2", "entry_rise_5min_pct": 3.0},
        )
        exit_fields = enrich_exit_pbv2_rise5_shadow_fields(
            entry_shadow,
            entry_price=1000.0,
            exit_price=1050.0,
            exit_reason="trailing_mfe_exit",
            peak_mfe_pct=1.2,
            peak_mae_pct=-0.5,
        )
        self.assertEqual(exit_fields["pbv2_rise5_shadow_pnl_yen_100"], 0.0)
        self.assertEqual(exit_fields["shadow_blocked_pnl_yen_100"], 5000.0)
        self.assertEqual(exit_fields["shadow_blocked_mfe"], 1.2)
        self.assertLess(exit_fields["pbv2_rise5_shadow_delta_yen"], 0.0)

    def test_counters_aggregate_delta(self) -> None:
        counters = PbV2Rise5ShadowCounters(threshold_pct=1.84)
        blocked = compute_pbv2_rise5_shadow_fields(
            _cfg(),
            {"entry_type": "PBV2", "entry_rise_5min_pct": 3.0, "minutes_from_open": 30},
        )
        kept = compute_pbv2_rise5_shadow_fields(
            _cfg(),
            {"entry_type": "PBV2", "entry_rise_5min_pct": 0.5, "minutes_from_open": 30},
        )
        or_row = compute_pbv2_rise5_shadow_fields(
            _cfg(),
            {"entry_type": ENTRY_TYPE_OR, "entry_rise_5min_pct": 9.0},
        )
        counters.record_accept(blocked)
        counters.record_accept(kept)
        counters.record_accept(or_row)
        counters.record_exit(
            enrich_exit_pbv2_rise5_shadow_fields(
                blocked,
                entry_price=1000.0,
                exit_price=990.0,
                exit_reason="stop_hit",
            )
        )
        counters.record_exit(
            enrich_exit_pbv2_rise5_shadow_fields(
                kept,
                entry_price=1000.0,
                exit_price=1010.0,
                exit_reason="trailing_mfe_exit",
            )
        )
        summary = counters.summary_fields()
        self.assertEqual(summary["pbv2_rise5_shadow_target_count"], 2)
        self.assertEqual(summary["pbv2_rise5_shadow_block_count"], 1)
        self.assertEqual(summary["pbv2_rise5_shadow_blocked_losers"], 1)
        self.assertGreater(summary["pbv2_rise5_shadow_delta_yen"], 0.0)


class TestExecuteAcceptedEntryDoesNotBlock(unittest.TestCase):
    def test_shadow_true_entry_still_proceeds(self) -> None:
        from small_paper.pilot_runner import _execute_accepted_entry

        gate = MagicMock()
        gate.record_accepted = MagicMock()
        state = MagicMock()
        state.events = []
        state.accepted_rows = []
        state.session_momentum_samples = []
        state.low_liquidity_shadow_reject_count = 0
        state.peak_open_slots = 0
        state.pbv2_rise5_shadow = PbV2Rise5ShadowCounters(threshold_pct=1.84)
        state.or_overlay = None
        writer = MagicMock()
        config = _cfg()
        config.position_cap_mode = False
        config.low_liquidity_shadow_enabled = False
        ctx = SimpleNamespace(
            config=config,
            gate=gate,
            state=state,
            source="test",
            writer=writer,
            observer=None,
            discord=None,
            symbol_price_ring={"7203.T": []},
            extension_bus=None,
            pos_fields=["symbol", "entry_time", "exit_time", "open_slots_after"],
        )
        trade = {
            "symbol": "7203.T",
            "entry_type": "PBV2",
            "entry_rise_5min_pct": 3.5,
            "trading_value": 2e8,
            "entry_time": "2026-07-05T09:30:00+09:00",
            "exit_time": "2026-07-05T09:35:00+09:00",
        }
        decision = SimpleNamespace(accept=True, reason="")
        with patch("small_paper.pilot_runner._should_enrich_accept_audit", return_value=False):
            with patch("small_paper.pilot_runner._should_record_entry_shadows", return_value=False):
                with patch("small_paper.pilot_runner._maybe_reject_same_symbol_open_overlap", return_value=False):
                    with patch("small_paper.pilot_runner._event_from_gate", return_value={"event_type": "accepted"}):
                        with patch("small_paper.pilot_runner._record_bucket"):
                            _execute_accepted_entry(
                                ctx,
                                sym="7203.T",
                                trade=trade,
                                decision=decision,
                                payload={"CurrentPrice": 1000},
                                enriched={},
                                msg_i=1,
                                bucket="AM",
                                score5_ord=None,
                            )
        self.assertTrue(trade["pbv2_rise5_shadow_block"])
        gate.record_accepted.assert_called_once()
        writer.append_event.assert_called_once()
        self.assertEqual(state.pbv2_rise5_shadow.pbv2_rise5_shadow_block_count, 1)


if __name__ == "__main__":
    unittest.main()
