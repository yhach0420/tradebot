"""Phase672 — Pre-entry microsequence feature discovery tests."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase672_pre_entry_microsequence import (  # noqa: E402
    TickSnap,
    _compute_microsequence_features,
    _is_early_stop,
    _pressure_proxies,
    _price_direction_stats,
    run_audit,
)

JST = timezone(timedelta(hours=9))


class TestMicrosequenceHelpers(unittest.TestCase):
    def test_price_direction_stats_down_ticks(self) -> None:
        stats = _price_direction_stats([100.0, 99.0, 98.0, 97.5, 97.0])
        self.assertEqual(stats["consecutive_down_ticks"], 4.0)
        self.assertGreater(float(stats["down_tick_ratio"] or 0), 0.5)

    def test_microsequence_features_on_synthetic_ticks(self) -> None:
        base_dt = datetime(2026, 6, 1, 9, 10, tzinfo=JST)
        base = base_dt.timestamp()
        price_series = [
            (base_dt - timedelta(seconds=120), 100.0),
            (base_dt - timedelta(seconds=60), 99.5),
            (base_dt - timedelta(seconds=30), 99.0),
            (base_dt - timedelta(seconds=10), 98.5),
            (base_dt, 98.0),
        ]
        ticks = [
            TickSnap(ts=base - 60, price=99.5, bid_qty=4800, ask_qty=4200, imb=0.53, spread_bps=12.0),
            TickSnap(ts=base - 30, price=99.0, bid_qty=4500, ask_qty=4500, imb=0.50, spread_bps=14.0),
            TickSnap(ts=base - 10, price=98.5, bid_qty=4200, ask_qty=4800, imb=0.47, spread_bps=16.0),
            TickSnap(ts=base, price=98.0, bid_qty=4000, ask_qty=5000, imb=0.44, spread_bps=18.0),
        ]
        feats = _compute_microsequence_features(
            ticks,
            price_series=price_series,
            entry_dt=base_dt,
            entry_ts=base,
            entry_px=98.0,
            signal_ts=base - 5,
            signal_px=98.3,
        )
        self.assertTrue(feats.get("microsequence_ok"))
        self.assertLess(float(feats.get("pre30_price_return") or 0), 0)
        self.assertGreater(float(feats.get("board_imbalance_drop") or 0), 0)

    def test_pressure_proxies_price_down_board_weak(self) -> None:
        base = datetime(2026, 6, 1, 9, 10, tzinfo=JST).timestamp()
        ticks = [
            TickSnap(ts=base - 60, price=100.0, bid_qty=5000, ask_qty=4000, imb=0.60, spread_bps=10.0),
            TickSnap(ts=base - 30, price=99.0, bid_qty=4500, ask_qty=4500, imb=0.52, spread_bps=12.0),
            TickSnap(ts=base, price=98.0, bid_qty=4000, ask_qty=5000, imb=0.44, spread_bps=14.0),
        ]
        board = {"board_imbalance_change": -0.05, "best_ask_size_increase": 1000, "board_imbalance_drop": 0.03}
        out = _pressure_proxies(ticks, entry_ts=base, entry_px=98.0, board=board)
        self.assertEqual(out["price_down_with_board_weakening"], 1.0)


def test_phase672_audit_on_canonical_dataset():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper").is_dir():
        pytest.skip("small_paper results missing")
    if not (root / "data" / "push_jsonl").is_dir():
        pytest.skip("push_jsonl missing")
    report = run_audit()
    assert report["entry_count"] == 3192
    assert report["verdict"] in {
        "FOUND_SIGNAL",
        "FOUND_WEAK_SIGNAL",
        "REJECT",
        "DATA_GAP",
    }
    out_dir = root / "results" / "reports" / "phase672_pre_entry_microsequence"
    assert (out_dir / "phase672_microsequence_report.json").is_file()
    assert (out_dir / "phase672_microsequence_feature_rank.csv").is_file()
    assert (out_dir / "phase672_microsequence_tree_rules.csv").is_file()
    assert (out_dir / "phase672_microsequence_threshold_sweep.csv").is_file()
    assert (out_dir / "phase672_microsequence_counterfactual.csv").is_file()
    assert (out_dir / "phase672_early_stop_examples.csv").is_file()
    assert (out_dir / "phase672_decision.md").is_file()


if __name__ == "__main__":
    unittest.main()
