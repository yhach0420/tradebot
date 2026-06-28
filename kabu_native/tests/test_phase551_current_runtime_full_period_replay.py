"""Phase551 runtime full-period replay tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = REPO / "kabu_native"
for p in (REPO, KABU / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase546_entry_cluster_shadow_replay import VARIANTS  # noqa: E402
from research.phase551_current_runtime_full_period_replay import (  # noqa: E402
    PHASE551_VERDICT,
    V6_SPEC,
    _combine_metrics,
    _evaluate_live_trades,
    _is_or_trade,
)


def _trade(**kwargs: object) -> dict[str, object]:
    base: dict[str, object] = {
        "symbol": "5074.T",
        "day": "20260620",
        "entry_time": "2026-06-20T10:00:00+09:00",
        "exit_time": "2026-06-20T10:30:00+09:00",
        "pnl_yen_100": 1000.0,
        "cluster_id": 1,
        "new_subcluster_id": 1,
        "liquidity_burst": 0.01,
    }
    base.update(kwargs)
    return base


class TestPhase551RuntimeReplay(unittest.TestCase):
    def test_verdict(self) -> None:
        self.assertEqual(PHASE551_VERDICT, "phase551_current_runtime_full_period_replay_done")

    def test_v6_spec(self) -> None:
        self.assertEqual(V6_SPEC.variant_id, "V6")

    def test_legacy_blocks_or(self) -> None:
        trades = [_trade(entry_type="OR_OVERLAY"), _trade(entry_type="PBV2")]
        ev = _evaluate_live_trades(
            trades,
            include_or=False,
            reentry_rsi=False,
            entry_quality=False,
            cluster_guard=False,
            cluster_exception=False,
            bar_cache={},
            thresholds={"liquidity_burst_p75": 0.052267},
        )
        self.assertEqual(ev["trades"], 1)

    def test_cluster_exception_rescues(self) -> None:
        trades = [
            _trade(cluster_id=5, new_subcluster_id=3, liquidity_burst=0.08, pnl_yen_100=5000.0)
        ]
        ev = _evaluate_live_trades(
            trades,
            include_or=True,
            reentry_rsi=False,
            entry_quality=False,
            cluster_guard=True,
            cluster_exception=True,
            bar_cache={},
            thresholds={"liquidity_burst_p75": 0.052267},
        )
        self.assertEqual(ev["trades"], 1)
        self.assertEqual(ev["cluster_guard_exception_count"], 1)
        self.assertEqual(ev["cluster_guard_reject_count"], 0)

    def test_cluster_reject_without_exception(self) -> None:
        trades = [
            _trade(cluster_id=5, new_subcluster_id=3, liquidity_burst=0.01, pnl_yen_100=-500.0)
        ]
        ev = _evaluate_live_trades(
            trades,
            include_or=True,
            reentry_rsi=False,
            entry_quality=False,
            cluster_guard=True,
            cluster_exception=False,
            bar_cache={},
            thresholds={"liquidity_burst_p75": 0.052267},
        )
        self.assertEqual(ev["trades"], 0)
        self.assertEqual(ev["cluster_guard_reject_count"], 1)

    def test_combined_metrics_not_overwritten_by_live(self) -> None:
        live = {"pnl_yen_100": -100.0, "profit_factor": 0.5, "trades": 2, "_accepted": [{"pnl_yen_100": -50}]}
        cap = {"pnl_yen_100": 500.0, "_trades": [{"pnl_yen_100": 500}]}
        comb = _combine_metrics(live, cap)
        merged = {**{k: live.get(k) for k in live if not str(k).startswith("_")}, **comb}
        self.assertEqual(merged["pnl_yen_100"], comb["pnl_yen_100"])
        self.assertEqual(merged["profit_factor"], comb["profit_factor"])
        self.assertEqual(merged["trades"], comb["trades"])


if __name__ == "__main__":
    unittest.main()
