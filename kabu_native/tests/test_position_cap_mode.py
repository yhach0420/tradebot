"""Phase396: Runtime Position-CAP mode tests."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PARENT = ROOT.parent
for p in (SRC, PARENT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.exposure_gate import (  # noqa: E402
    REJECT_MAX_CONCURRENT,
    ExposureGate,
    ExposureGateConfig,
)
from small_paper.config import SmallPaperPilotConfig, load_pilot_config  # noqa: E402
from small_paper.position_cap_mode import (  # noqa: E402
    LegacyVirtualHoldShadow,
    PositionCapSessionStats,
    make_position_cap_stats,
    position_cap_summary_fields,
)

JST = ZoneInfo("Asia/Tokyo")


def _trade(sym: str, offset_min: int = 0, hold_sec: float = 300.0) -> dict:
    ent = datetime(2026, 6, 15, 12, 30, tzinfo=JST) + timedelta(minutes=offset_min)
    ex = ent + timedelta(seconds=hold_sec)
    return {
        "profile": "momentum_volume_v13_combined",
        "symbol": sym,
        "entry_time": ent.isoformat(),
        "exit_time": ex.isoformat(),
        "trade_date": ent.date().isoformat(),
        "pnl_pct": 0.0,
    }


def _gate_cfg(**kwargs) -> ExposureGateConfig:
    base = dict(
        profile="momentum_volume_v13_combined",
        max_concurrent_positions=2,
        reject_below_quality=False,
        min_continuation_quality=0.0,
    )
    base.update(kwargs)
    return ExposureGateConfig(**base)


class TestExposureGatePositionCapMode(unittest.TestCase):
    def test_legacy_virtual_hold_cap(self) -> None:
        gate = ExposureGate(_gate_cfg(position_cap_mode=False))
        t1 = _trade("1111.T", 0)
        t2 = _trade("2222.T", 1)
        t3 = _trade("3333.T", 2)
        self.assertTrue(gate.evaluate_entry(t1).accept)
        gate.record_accepted(t1)
        self.assertTrue(gate.evaluate_entry(t2).accept)
        gate.record_accepted(t2)
        d3 = gate.evaluate_entry(t3)
        self.assertFalse(d3.accept)
        self.assertEqual(d3.reason, REJECT_MAX_CONCURRENT)
        # VH slot frees after 5min even without structural exit
        t4 = _trade("4444.T", 6)
        self.assertTrue(gate.evaluate_entry(t4).accept)

    def test_position_cap_uses_observer_count(self) -> None:
        gate = ExposureGate(_gate_cfg(position_cap_mode=True))
        trade = _trade("1111.T")
        d_block = gate.evaluate_entry(trade, observer_open_count=2, observer_symbol_open=False)
        self.assertFalse(d_block.accept)
        self.assertEqual(d_block.reason, REJECT_MAX_CONCURRENT)
        d_allow = gate.evaluate_entry(trade, observer_open_count=1, observer_symbol_open=False)
        self.assertTrue(d_allow.accept)
        gate.record_accepted(trade)
        self.assertEqual(len(gate.state.open_slots), 0)

    def test_position_cap_allows_same_symbol_overlap(self) -> None:
        gate = ExposureGate(_gate_cfg(position_cap_mode=True))
        trade = _trade("1111.T")
        d = gate.evaluate_entry(trade, observer_open_count=2, observer_symbol_open=True)
        self.assertTrue(d.accept)

    def test_vh_expiry_does_not_free_position_cap_slot(self) -> None:
        gate = ExposureGate(_gate_cfg(position_cap_mode=True))
        t1 = _trade("1111.T", 0, hold_sec=300)
        gate.record_accepted(t1)
        self.assertEqual(len(gate.state.open_slots), 0)
        # Observer still holds 2 positions after VH would have expired
        t_new = _trade("4444.T", 6)
        d = gate.evaluate_entry(t_new, observer_open_count=2, observer_symbol_open=False)
        self.assertFalse(d.accept)


class TestLegacyVirtualHoldShadow(unittest.TestCase):
    def test_shadow_tracks_vh_accept_delta(self) -> None:
        shadow = LegacyVirtualHoldShadow(cap=2)
        trades = [_trade("1111.T", 0), _trade("2222.T", 1), _trade("3333.T", 2)]
        for t in trades:
            shadow.simulate(t, runtime_decision="accept")
        self.assertEqual(shadow.accepted_count, 2)
        self.assertEqual(shadow.rejected_cap_count, 1)


class TestConfigLoader(unittest.TestCase):
    def test_yaml_position_cap_fields(self) -> None:
        cfg_path = (
            ROOT
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        if not cfg_path.is_file():
            self.skipTest("production yaml missing")
        cfg = load_pilot_config(cfg_path)
        self.assertTrue(cfg.position_cap_mode)
        self.assertEqual(cfg.position_cap_release, "structural_exit")
        self.assertEqual(cfg.same_symbol_open_policy, "no_overlap_replace")
        self.assertFalse(cfg.order_enabled)
        self.assertTrue(cfg.paper_only)
        gate_cfg = cfg.exposure_gate_config()
        self.assertTrue(gate_cfg.position_cap_mode)

    def test_default_same_symbol_open_policy_is_replace(self) -> None:
        cfg = SmallPaperPilotConfig()
        self.assertEqual(cfg.same_symbol_open_policy, "replace")


class TestSummaryFields(unittest.TestCase):
    def test_position_cap_summary_payload(self) -> None:
        config = SmallPaperPilotConfig(
            position_cap_mode=True,
            position_cap_release="structural_exit",
            max_concurrent_positions=3,
        )
        state = MagicMock()
        state.accepted_rows = [1, 2]
        state.peak_open_slots = 0
        state.peak_observer_open = 3
        stats = PositionCapSessionStats(
            legacy_vh_shadow=LegacyVirtualHoldShadow(cap=3),
            rejected_by_position_cap=5,
            position_cap_max_open=3,
            observer_open_max_positions=3,
        )
        stats.legacy_vh_shadow.accepted_count = 10
        state.position_cap_stats = stats
        gate = ExposureGate(ExposureGateConfig(position_cap_mode=True))
        out = position_cap_summary_fields(config, state, gate, events=[])
        self.assertTrue(out["position_cap_mode"])
        self.assertEqual(out["accepted_count_position_cap"], 2)
        self.assertEqual(out["rejected_by_position_cap"], 5)
        self.assertEqual(out["legacy_virtual_hold_delta_accept_count"], 8)


if __name__ == "__main__":
    unittest.main()
