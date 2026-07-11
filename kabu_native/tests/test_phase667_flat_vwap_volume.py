"""Phase667 — flat VWAP / volume refinement tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from research.phase667_flat_vwap_volume_refinement import (
    PHASE667_VERDICT,
    _flat_weak_refined,
    _refined_vwap_breakout,
    _refined_volume_spike,
    decide_phase667,
)


def test_refined_vwap_requires_cross_and_near_band():
    trade = {
        "vwap_cross_up": True,
        "vwap_dev_pct": 0.15,
        "vwap_slope": 0.02,
        "vwap_above": True,
        "vwap_hold_above": True,
    }
    assert _refined_vwap_breakout(trade) is True
    trade["vwap_dev_pct"] = 0.5
    assert _refined_vwap_breakout(trade) is False


def test_refined_volume_requires_price_up():
    trade = {
        "volume_ratio_1min": 1.5,
        "volume_ratio_3min": 1.0,
        "volume_ratio_5min": 1.0,
        "r60_sec": 0.1,
        "r120_sec": 0.0,
    }
    assert _refined_volume_spike(trade, threshold=1.2) is True
    trade["r60_sec"] = -0.05
    trade["r120_sec"] = -0.02
    assert _refined_volume_spike(trade, threshold=1.2) is False


def test_flat_weak_refined_only_on_flat():
    trade = {"pretrend_shape": "A", "recent_low_break": True}
    assert _flat_weak_refined(trade) is False
    trade["pretrend_shape"] = "E"
    trade["vwap_dev_pct"] = -0.2
    trade["board_improvement"] = False
    assert _flat_weak_refined(trade) is True


def test_decide_hold_on_moderate_gain():
    decision, _ = decide_phase667(
        counterfactual=[
            {
                "scenario_id": "exclude_flat_weak_and_range",
                "pool": "PBV2",
                "delta_pnl_yen_100": 150000,
                "delta_profit_factor": 0.05,
                "delta_max_dd_yen_100": 10000,
                "blocked_winners": 100,
                "blocked_losers": 120,
            }
        ],
        daily_rows=[],
        threshold_rows=[{"variant_id": "vwap_refined_strict", "profit_factor": 1.2, "total_pnl_yen_100": 5000}],
        flat_count=852,
    )
    assert decision in ("HOLD", "ADOPT_CANDIDATE")


def test_phase667_audit_on_canonical_dataset():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper").is_dir():
        pytest.skip("small_paper results missing")
    from research.phase667_flat_vwap_volume_refinement import run_audit

    report = run_audit(max_workers=2)
    assert report["verdict"] == PHASE667_VERDICT
    assert report["entry_count"] == 3192
    assert report["flat_entry_count"] >= 800
    assert report["decision"] in ("ADOPT_CANDIDATE", "HOLD", "REJECT")
