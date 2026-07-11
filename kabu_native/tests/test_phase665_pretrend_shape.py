"""Phase665 — pre-trend shape analysis tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.phase665_pretrend_shape_analysis import (
    PHASE665_VERDICT,
    _enrich_trade,
    classify_pretrend_shape,
    compute_pretrend_features,
    decide_phase665,
)

JST = ZoneInfo("Asia/Tokyo")


def _series_uptrend() -> list[tuple[datetime, float]]:
    base = datetime(2026, 7, 1, 9, 0, tzinfo=JST)
    out: list[tuple[datetime, float]] = []
    px = 1000.0
    for i in range(20):
        px += 2.0
        out.append((base.replace(minute=i), px))
    return out


def test_classify_uptrend_continuation():
    feat = {
        "computed": True,
        "r300_sec": 0.6,
        "r600_sec": 1.2,
        "r900_sec": 1.5,
        "r60_sec": 0.1,
        "r120_sec": 0.05,
        "high_update_5min": 2,
        "high_update_10min": 3,
        "low_update_5min": 0,
        "low_update_10min": 0,
        "vwap_dev_pct": 0.5,
    }
    assert classify_pretrend_shape(feat) == "A"


def test_classify_down_bounce():
    feat = {
        "computed": True,
        "r300_sec": -0.2,
        "r600_sec": -0.8,
        "r900_sec": -1.0,
        "r60_sec": 0.3,
        "r120_sec": 0.25,
        "high_update_5min": 0,
        "high_update_10min": 0,
        "low_update_5min": 1,
        "low_update_10min": 2,
        "vwap_dev_pct": 0.0,
    }
    assert classify_pretrend_shape(feat) == "C"


def test_compute_features_on_series():
    ent = datetime(2026, 7, 1, 9, 19, tzinfo=JST)
    feat = compute_pretrend_features(_series_uptrend(), entry_ts=ent, entry_px=1040.0)
    assert feat.get("computed") is True
    assert feat.get("r300_sec") is not None


def test_enrich_trade_resolves_entry_price_from_series():
    ent = datetime(2026, 7, 1, 9, 19, tzinfo=JST)
    series = _series_uptrend()
    trade = {
        "symbol": "1234.T",
        "day": "2026-07-01",
        "entry_time": ent.isoformat(),
        "pnl_yen_100": 100.0,
    }
    enriched = _enrich_trade(trade, price_idx={("1234.T", "20260701"): series})
    assert enriched.get("computed") is True
    assert enriched.get("entry_price") == 1040.0
    assert enriched.get("pretrend_shape") in ("A", "B", "E", "F")


def test_decide_hold_on_weak_improvement():
    decision, _ = decide_phase665(
        trades=[{"pretrend_shape": "C", "pnl_yen_100": -100}],
        counterfactual=[
            {
                "scenario_id": "exclude_C_D",
                "pool": "all",
                "delta_pnl_yen_100": 500,
                "delta_profit_factor": 0.01,
                "delta_max_dd_yen_100": 0,
                "blocked_winners": 10,
                "blocked_losers": 20,
            }
        ],
        pool_metrics={"all": {"C": {"entry_count": 30}}, "PBV2": {"shape_C_share": 0.05, "C": {"entry_count": 25}}},
    )
    assert decision in ("HOLD", "REJECT")


def test_phase665_audit_on_canonical_dataset():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper").is_dir():
        pytest.skip("small_paper results missing")
    from research.phase665_pretrend_shape_analysis import run_audit

    report = run_audit(max_workers=2)
    assert report["verdict"] == PHASE665_VERDICT
    assert report["entry_count"] == 3192
    assert report["decision"] in ("ADOPT_CANDIDATE", "HOLD", "REJECT")
