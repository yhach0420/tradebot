"""Phase535: OR CAP reality validation unit tests."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase382_capital_constrained_backtest import _position_key  # noqa: E402
from research.phase535_or_cap_reality_validation import (  # noqa: E402
    PHASE535_VERDICT,
    CapScenario,
    _cap_scenarios,
    _simulate_cap_audited,
    _substitution_metrics,
)


def _trade(
    *,
    sym: str,
    day: str,
    entry_min: int,
    exit_min: int,
    pnl: float,
    pbv2: bool,
    overlay: bool,
) -> dict:
    base = datetime(2026, 6, 2, 9, 0)
    ent = base + timedelta(minutes=entry_min)
    ex = base + timedelta(minutes=exit_min)
    row = {
        "symbol": f"{sym}.T",
        "day": day,
        "entry_time": ent.isoformat(),
        "exit_time": ex.isoformat(),
        "entry_price": 1000.0,
        "exit_price": 1100.0,
        "pnl_yen_100": pnl,
        "exit_reason": "test",
        "_pbv2": pbv2,
        "_overlay": overlay,
    }
    row["position_key"] = _position_key(row)
    return row


class TestPhase535CapSim(unittest.TestCase):
    def test_scenario_count(self) -> None:
        self.assertEqual(len(_cap_scenarios()), 8)

    def test_split_or_pool_blocks_overlay_only(self) -> None:
        scenario = CapScenario("CAP_SPLIT_4_1", "split", 5, 4, 1, "split_pools")
        trades = [
            _trade(sym="1001", day="20260602", entry_min=0, exit_min=30, pnl=100, pbv2=True, overlay=False),
            _trade(sym="1002", day="20260602", entry_min=1, exit_min=30, pnl=100, pbv2=True, overlay=False),
            _trade(sym="1003", day="20260602", entry_min=2, exit_min=30, pnl=100, pbv2=True, overlay=False),
            _trade(sym="1004", day="20260602", entry_min=3, exit_min=30, pnl=100, pbv2=True, overlay=False),
            _trade(sym="2001", day="20260602", entry_min=4, exit_min=30, pnl=200, pbv2=False, overlay=True),
            _trade(sym="2002", day="20260602", entry_min=5, exit_min=30, pnl=300, pbv2=False, overlay=True),
        ]
        result = _simulate_cap_audited(trades, scenario=scenario)
        accepted = [r for r in result.entry_audit if r.accepted]
        blocked_or = [
            r for r in result.entry_audit if not r.accepted and r.overlay and not r.pbv2
        ]
        self.assertEqual(len(accepted), 5)
        self.assertEqual(len(blocked_or), 1)
        self.assertEqual(blocked_or[0].reject_reason, "or_pool_full")

    def test_pbv2_priority_beats_or_at_same_time(self) -> None:
        scenario = CapScenario("CAP_PBv2_PRIORITY_5", "shared", 5, 5, 5, "pbv2_first")
        trades = [
            _trade(sym="1001", day="20260602", entry_min=0, exit_min=30, pnl=50, pbv2=True, overlay=False),
            _trade(sym="1002", day="20260602", entry_min=0, exit_min=30, pnl=50, pbv2=True, overlay=False),
            _trade(sym="1003", day="20260602", entry_min=0, exit_min=30, pnl=50, pbv2=True, overlay=False),
            _trade(sym="1004", day="20260602", entry_min=0, exit_min=30, pnl=50, pbv2=True, overlay=False),
            _trade(sym="1005", day="20260602", entry_min=0, exit_min=30, pnl=50, pbv2=True, overlay=False),
            _trade(sym="2001", day="20260602", entry_min=0, exit_min=30, pnl=500, pbv2=False, overlay=True),
        ]
        result = _simulate_cap_audited(trades, scenario=scenario)
        accepted_or = [r for r in result.entry_audit if r.accepted and r.overlay and not r.pbv2]
        blocked_or = [r for r in result.entry_audit if not r.accepted and r.overlay and not r.pbv2]
        self.assertEqual(len(accepted_or), 0)
        self.assertEqual(len(blocked_or), 1)

    def test_substitution_metrics(self) -> None:
        base = [_trade(sym="1001", day="20260602", entry_min=0, exit_min=30, pnl=100, pbv2=True, overlay=False)]
        scen = [
            _trade(sym="1001", day="20260602", entry_min=0, exit_min=30, pnl=100, pbv2=True, overlay=False),
            _trade(sym="2001", day="20260602", entry_min=1, exit_min=30, pnl=50, pbv2=False, overlay=True),
        ]
        scen[0]["accepted_by_pbv2"] = True
        scen[0]["accepted_by_overlay"] = False
        scen[1]["accepted_by_pbv2"] = False
        scen[1]["accepted_by_overlay"] = True
        base[0]["accepted_by_pbv2"] = True
        base[0]["accepted_by_overlay"] = False
        sub = _substitution_metrics(baseline_trades=base, scenario_trades=scen, audit=[])
        self.assertEqual(sub["or_added_count"], 1)
        self.assertEqual(sub["or_added_pnl"], 50.0)

    def test_verdict(self) -> None:
        self.assertEqual(PHASE535_VERDICT, "phase535_or_cap_reality_validation_done")


if __name__ == "__main__":
    unittest.main()
