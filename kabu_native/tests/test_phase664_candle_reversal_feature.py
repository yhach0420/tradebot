"""Phase664 — candle reversal feature study unit tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.phase664_candle_reversal_feature_study import (
    PHASE664_VERDICT,
    aggregate_1m_to_5m,
    compute_candle_features,
    decide_phase664,
    _candle_shape,
)
from research.phase507_classic_indicators import Bar1m

JST = ZoneInfo("Asia/Tokyo")


def _bar(ts: str, o: float, h: float, l: float, c: float, v: float = 10.0) -> Bar1m:
    return Bar1m(
        ts=datetime.fromisoformat(ts),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=v,
        vwap=c,
    )


def test_candle_shape_hammer_like():
    bar = _bar("2026-07-01T09:05:00+09:00", 100, 101, 95, 100.5, 20)
    shape = _candle_shape(bar)
    assert shape is not None
    assert shape["is_bullish"] is True
    assert shape["is_hammer_like"] is True
    assert shape["lower_shadow_ratio"] > 0.5


def test_aggregate_1m_to_5m():
    bars = [
        _bar("2026-07-01T09:00:00+09:00", 100, 101, 99, 100, 1),
        _bar("2026-07-01T09:01:00+09:00", 100, 102, 99, 101, 1),
        _bar("2026-07-01T09:02:00+09:00", 101, 103, 100, 102, 1),
        _bar("2026-07-01T09:03:00+09:00", 102, 103, 101, 102, 1),
        _bar("2026-07-01T09:04:00+09:00", 102, 104, 101, 103, 1),
    ]
    out = aggregate_1m_to_5m(bars)
    assert len(out) == 1
    assert out[0].open == 100
    assert out[0].close == 103
    assert out[0].volume >= 5


def test_compute_candle_features_insufficient_history():
    bars = [_bar(f"2026-07-01T09:0{i}:00+09:00", 100, 101, 99, 100, 1) for i in range(5)]
    ent = datetime.fromisoformat("2026-07-01T09:04:30+09:00")
    feat = compute_candle_features(bars_1m=bars, entry_ts=ent, entry_px=100.0)
    assert feat.get("computed") is False


def test_decide_hold_on_low_coverage():
    decision, _ = decide_phase664(
        trades=[{"pnl_yen_100": 1}],
        pool_metrics={"all": {"pattern_yes": {"entry_count": 2}, "pattern_no": {"entry_count": 10}}},
        counterfactual=[],
        pattern_id="hammer_only",
    )
    assert decision == "HOLD"


def test_phase664_audit_on_canonical_dataset():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper").is_dir():
        pytest.skip("small_paper results missing")
    from research.phase664_candle_reversal_feature_study import run_audit

    report = run_audit(max_workers=2)
    assert report["verdict"] == PHASE664_VERDICT
    assert report["entry_count"] == 3192
    assert report["decision"] in ("ADOPT_CANDIDATE", "HOLD", "REJECT")
    assert report["decision"] == "REJECT"
