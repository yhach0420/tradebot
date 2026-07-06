"""Phase650: PBv2 flat-band guard shadow tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from small_paper.or_overlay_cap import ENTRY_TYPE_OR  # noqa: E402
from small_paper.pbv2_flat_band_guard_shadow import (  # noqa: E402
    APPLY_POOL_PBV2_ONLY,
    REASON_FLAT_BAND_NARROW,
    REASON_OVERHEAT_RISE5,
    PbV2FlatBandShadowCounters,
    build_pbv2_flat_band_shadow_counters,
    compute_pbv2_flat_band_shadow_fields,
    enrich_exit_pbv2_flat_band_shadow_fields,
    evaluate_flat_plus_overheat,
    flat_band_shadow_enabled,
    is_flat_band_narrow,
    is_overheat_rise5,
)


def _cfg(**overrides: object) -> SimpleNamespace:
    base = {
        "pbv2_flat_band_shadow_enabled": True,
        "pbv2_flat_band_shadow_apply_pool": APPLY_POOL_PBV2_ONLY,
        "pbv2_flat_band_shadow_rise5_flat_min_pct": 0.0,
        "pbv2_flat_band_shadow_rise5_flat_max_pct": 0.5,
        "pbv2_flat_band_shadow_rise10_flat_min_pct": -0.5,
        "pbv2_flat_band_shadow_rise10_flat_max_pct": 0.5,
        "pbv2_flat_band_shadow_overheat_rise5_pct": 2.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestFlatBandLogic(unittest.TestCase):
    def test_flat_band_narrow(self) -> None:
        self.assertTrue(is_flat_band_narrow(0.2, 0.1, rise5_min=0.0, rise5_max=0.5, rise10_min=-0.5, rise10_max=0.5))
        self.assertFalse(is_flat_band_narrow(-0.2, 0.1, rise5_min=0.0, rise5_max=0.5, rise10_min=-0.5, rise10_max=0.5))

    def test_overheat(self) -> None:
        self.assertTrue(is_overheat_rise5(2.5, threshold=2.0))
        self.assertFalse(is_overheat_rise5(1.5, threshold=2.0))

    def test_rise10_missing_no_flat_block(self) -> None:
        blocked, reason, flat_hit, overheat_hit = evaluate_flat_plus_overheat(
            {"entry_rise_5min_pct": 0.2},
            rise5_min=0.0,
            rise5_max=0.5,
            rise10_min=-0.5,
            rise10_max=0.5,
            overheat_threshold=2.0,
        )
        self.assertFalse(blocked)
        self.assertFalse(flat_hit)
        self.assertFalse(overheat_hit)

    def test_rise5_missing_no_block(self) -> None:
        fields = compute_pbv2_flat_band_shadow_fields(_cfg(), {"entry_type": "PBV2"})
        self.assertFalse(fields["pbv2_flat_band_shadow_block"])

    def test_or_excluded(self) -> None:
        fields = compute_pbv2_flat_band_shadow_fields(
            _cfg(),
            {"entry_type": ENTRY_TYPE_OR, "entry_rise_5min_pct": 0.2, "entry_rise_10min_pct": 0.0},
        )
        self.assertFalse(fields["pbv2_flat_band_shadow_block"])

    def test_flat_plus_overheat_flat(self) -> None:
        fields = compute_pbv2_flat_band_shadow_fields(
            _cfg(),
            {"entry_type": "PBV2", "entry_rise_5min_pct": 0.2, "entry_rise_10min_pct": 0.0},
        )
        self.assertTrue(fields["pbv2_flat_band_shadow_block"])
        self.assertEqual(fields["pbv2_flat_band_shadow_reason"], REASON_FLAT_BAND_NARROW)

    def test_flat_plus_overheat_overheat(self) -> None:
        fields = compute_pbv2_flat_band_shadow_fields(
            _cfg(),
            {"entry_type": "PBV2", "entry_rise_5min_pct": 2.5, "entry_rise_10min_pct": 1.0},
        )
        self.assertTrue(fields["pbv2_flat_band_shadow_block"])
        self.assertEqual(fields["pbv2_flat_band_shadow_reason"], REASON_OVERHEAT_RISE5)

    def test_overlap_with_rise5_shadow(self) -> None:
        fields = compute_pbv2_flat_band_shadow_fields(
            _cfg(),
            {
                "entry_type": "PBV2",
                "entry_rise_5min_pct": 2.5,
                "entry_rise_10min_pct": 0.0,
                "pbv2_rise5_shadow_block": True,
            },
            rise5_shadow_block=True,
        )
        self.assertTrue(fields["flat_band_and_rise5_shadow_block"])

    def test_exit_enrichment_blocked(self) -> None:
        entry = compute_pbv2_flat_band_shadow_fields(
            _cfg(),
            {"entry_type": "PBV2", "entry_rise_5min_pct": 0.2, "entry_rise_10min_pct": 0.0},
        )
        exit_fields = enrich_exit_pbv2_flat_band_shadow_fields(
            entry,
            entry_price=1000.0,
            exit_price=990.0,
            exit_reason="stop_hit",
            peak_mfe_pct=0.5,
            peak_mae_pct=-0.3,
        )
        self.assertEqual(exit_fields["pbv2_flat_band_shadow_pnl_yen_100"], 0.0)
        self.assertEqual(exit_fields["pbv2_flat_band_shadow_blocked_pnl_yen_100"], -1000.0)
        self.assertGreater(exit_fields["pbv2_flat_band_shadow_delta_yen"], 0.0)

    def test_counters_with_rise5_coexist(self) -> None:
        counters = build_pbv2_flat_band_shadow_counters(_cfg())
        blocked = compute_pbv2_flat_band_shadow_fields(
            _cfg(),
            {"entry_type": "PBV2", "entry_rise_5min_pct": 0.2, "entry_rise_10min_pct": 0.0},
        )
        kept = compute_pbv2_flat_band_shadow_fields(
            _cfg(),
            {"entry_type": "PBV2", "entry_rise_5min_pct": 1.0, "entry_rise_10min_pct": 1.0},
        )
        counters.record_accept(blocked)
        counters.record_accept(kept)
        counters.record_exit(
            enrich_exit_pbv2_flat_band_shadow_fields(
                blocked,
                entry_price=1000.0,
                exit_price=990.0,
                exit_reason="stop_hit",
            )
        )
        counters.record_exit(
            enrich_exit_pbv2_flat_band_shadow_fields(
                kept,
                entry_price=1000.0,
                exit_price=1010.0,
                exit_reason="trailing_mfe_exit",
            )
        )
        summary = counters.summary_fields()
        self.assertEqual(summary["pbv2_flat_band_shadow_target_count"], 2)
        self.assertEqual(summary["pbv2_flat_band_shadow_block_count"], 1)
        self.assertGreater(summary["pbv2_flat_band_shadow_net_effect_yen"], 0.0)

    def test_disabled(self) -> None:
        self.assertFalse(flat_band_shadow_enabled(_cfg(pbv2_flat_band_shadow_enabled=False)))


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
        state.pbv2_rise5_shadow = MagicMock()
        state.pbv2_flat_band_shadow = PbV2FlatBandShadowCounters()
        state.or_overlay = None
        writer = MagicMock()
        config = _cfg()
        config.position_cap_mode = False
        config.low_liquidity_shadow_enabled = False
        config.pbv2_rise5_shadow_enabled = False
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
            "entry_rise_5min_pct": 0.2,
            "entry_rise_10min_pct": 0.0,
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
        self.assertTrue(trade["pbv2_flat_band_shadow_block"])
        gate.record_accepted.assert_called_once()
        writer.append_event.assert_called_once()
        self.assertEqual(state.pbv2_flat_band_shadow.pbv2_flat_band_shadow_block_count, 1)


if __name__ == "__main__":
    unittest.main()
