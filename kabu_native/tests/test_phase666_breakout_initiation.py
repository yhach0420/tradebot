"""Phase666 — breakout initiation analysis tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.phase666_breakout_initiation_analysis import (
    PHASE666_VERDICT,
    classify_breakout_initiation,
    compute_breakout_features,
    decide_phase666,
)

JST = ZoneInfo("Asia/Tokyo")


def _flat_series() -> list[tuple[datetime, float]]:
    base = datetime(2026, 7, 1, 9, 0, tzinfo=JST)
    out: list[tuple[datetime, float]] = []
    px = 1000.0
    for i in range(30):
        px += 0.05 if i % 2 == 0 else -0.03
        out.append((base + timedelta(minutes=i), px))
    return out


def test_classify_flat_volume_spike():
    feat = {
        "computed": True,
        "recent_high_break": False,
        "recent_low_break": False,
        "vwap_cross_up": False,
        "vwap_cross_down": False,
        "volume_spike": True,
        "board_improvement": False,
        "r60_sec": 0.0,
        "r120_sec": 0.0,
    }
    assert classify_breakout_initiation(feat, pretrend_shape="E") == "C"


def test_classify_flat_no_signal():
    feat = {
        "computed": True,
        "recent_high_break": False,
        "recent_low_break": False,
        "vwap_cross_up": False,
        "vwap_cross_down": False,
        "volume_spike": False,
        "board_improvement": False,
        "r60_sec": 0.0,
        "r120_sec": 0.0,
    }
    assert classify_breakout_initiation(feat, pretrend_shape="E") == "E"


def test_classify_non_flat_is_na():
    feat = {"volume_spike": True}
    assert classify_breakout_initiation(feat, pretrend_shape="A") == "NA"


def test_compute_breakout_features():
    ent = datetime(2026, 7, 1, 9, 29, tzinfo=JST)
    series = _flat_series()
    feat = compute_breakout_features(series, entry_ts=ent, entry_px=1001.0, accept={})
    assert feat.get("computed") is True
    assert feat.get("range_5min_pct") is not None


def test_decide_reject_on_weak_improvement():
    decision, _ = decide_phase666(
        counterfactual=[
            {
                "scenario_id": "exclude_flat_E_F",
                "pool": "all",
                "delta_pnl_yen_100": 1000,
                "delta_profit_factor": 0.01,
                "delta_max_dd_yen_100": 0,
                "blocked_winners": 200,
                "blocked_losers": 150,
            }
        ],
        pool_metrics={"all": {"E": {"total_pnl_yen_100": -300000}}},
        feature_compare={},
    )
    assert decision in ("HOLD", "REJECT")


def test_phase666_audit_on_canonical_dataset():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper").is_dir():
        pytest.skip("small_paper results missing")
    from research.phase666_breakout_initiation_analysis import run_audit

    report = run_audit(max_workers=2)
    assert report["verdict"] == PHASE666_VERDICT
    assert report["entry_count"] == 3192
    assert report["flat_entry_count"] >= 800
    assert report["decision"] in ("ADOPT_CANDIDATE", "HOLD", "REJECT")
