"""Phase668 — existing shadow adoption review tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from research.phase658_full_period_shadow_revalidation import ShadowEval
from research.phase668_existing_shadow_adoption_review import (
    PHASE668_VERDICT,
    _daily_consistency,
    decide_shadow,
)


def _ev(**kwargs: object) -> ShadowEval:
    ev = ShadowEval(shadow_id=str(kwargs.get("shadow_id", "test_shadow")))
    for k, v in kwargs.items():
        setattr(ev, k, v)
    return ev


def test_decide_remove_on_negative_pnl():
    ev = _ev(shadow_id="vwap_shadow_reject", evaluable=True, delta_pnl_yen=-50000, recent_5d_delta_yen=-1000)
    decision, _ = decide_shadow(ev, shadow_def=None, daily_consistency={"improved_day_rate": 0.4}, all_evals={})
    assert decision == "REMOVE"


def test_decide_rise5_remove_when_flat_band_better():
    rise5 = _ev(shadow_id="pbv2_rise5_shadow", evaluable=True, delta_pnl_yen=30000)
    flat = _ev(shadow_id="pbv2_flat_band_shadow", evaluable=True, delta_pnl_yen=80000)
    decision, _ = decide_shadow(
        rise5,
        shadow_def=None,
        daily_consistency={"improved_day_rate": 0.6},
        all_evals={"pbv2_flat_band_shadow": flat, "pbv2_rise5_shadow": rise5},
    )
    assert decision == "REMOVE"


def test_daily_consistency_rate():
    rows = [{"delta_pnl_yen": 100}, {"delta_pnl_yen": -50}, {"delta_pnl_yen": 0}]
    out = _daily_consistency(rows)
    assert out["improved_days"] == 2
    assert out["improved_day_rate"] == pytest.approx(0.6667, rel=1e-3)


def test_phase668_audit_on_canonical_dataset():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper").is_dir():
        pytest.skip("small_paper results missing")
    from research.phase668_existing_shadow_adoption_review import run_audit

    report = run_audit(skip_slow=True)
    assert report["verdict"] == PHASE668_VERDICT
    assert report["entry_count"] == 3192
    assert "pbv2_flat_band_shadow" in {r["shadow_id"] for r in report["review_rows"]}
    assert report["new_shadow_blocked"] is True
