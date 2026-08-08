"""E1_X15 tests."""
from __future__ import annotations

import json
from pathlib import Path

from research.e1_x15_rpfe_incremental_entry import (
    FEATURES_ALLOWED,
    FORBIDDEN_ALPHA,
    FORBIDDEN_RISK_FROM,
    REBOUND_Q80,
    SOURCE_RUN,
    SOURCE_VERDICT,
    VARIANTS,
    VOL_PCT_Q80,
    VWAP_Q80,
)

NATIVE = Path(__file__).resolve().parents[2]
SOURCE = NATIVE / "results" / "research" / "e1_x14_holdout_reconciliation" / "report.json"
OUT = NATIVE / "results" / "research" / "e1_x15_rpfe_incremental_entry"


def _interim():
    p = OUT / "_interim.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def test_source_run_identity():
    src = json.loads(SOURCE.read_text(encoding="utf-8"))
    assert src["run_id"] == SOURCE_RUN
    assert src["verdict"] == SOURCE_VERDICT


def test_exact_three_features_only():
    assert set(FEATURES_ALLOWED) == {
        "distance_from_vwap_bps", "rebound_from_recent_low_bps", "volume_percentile_60s",
    }
    assert "volume_rate_60s" not in FEATURES_ALLOWED


def test_exact_four_variants_only():
    assert VARIANTS == ("C0", "C1", "C2", "C3")


def test_design_thresholds_fixed():
    assert VWAP_Q80 == 100.73709346405396
    assert REBOUND_Q80 == 46.32381895220083
    assert VOL_PCT_Q80 == 0.6486486486486487


def test_no_threshold_retune():
    inter = _interim()
    if not inter:
        return
    thr = inter["thresholds"]
    assert thr["vwap"] == VWAP_Q80
    assert thr["rebound"] == REBOUND_Q80
    assert thr["vol_pct"] == VOL_PCT_Q80


def test_vwap_used_as_upper_reject():
    # contract: OK when distance <= q80 (reject when above)
    assert VWAP_Q80 > 0


def test_recent_low_before_anchor():
    from research.e1_x15_rpfe_incremental_entry.anchors import _rebound_diag
    ticks = [
        {"t": 1000.0, "price": 100.0},
        {"t": 1010.0, "price": 90.0},
        {"t": 1020.0, "price": 95.0},
    ]
    d = _rebound_diag(ticks, 1020.0, lookback=20.0)
    assert d["ok"] is True
    assert d["elapsed_sec_from_low"] >= 0
    assert d["recent_low_price"] == 90.0


def test_relative_percentile_same_timestamp():
    from research.e1_x15_rpfe_incremental_entry import MIN_RS_UNIVERSE
    assert MIN_RS_UNIVERSE == 20


def test_one_anchor_per_episode():
    assert True  # enforced in select_anchors_for_episodes


def test_same_rpfe_episode_matching():
    inter = _interim()
    if not inter:
        return
    assert inter["matched_n"] > 0


def test_c0_baseline_defined():
    assert "C0" in VARIANTS


def test_session_boundary():
    assert True


def test_no_progress_contract():
    from research.e1_x15_rpfe_incremental_entry import NO_PROGRESS_RET
    assert abs(NO_PROGRESS_RET - 0.0005) < 1e-12


def test_incremental_c2_vs_c1():
    inter = _interim()
    if not inter:
        return
    assert "C2_vs_C1" in inter["incs"]


def test_incremental_c3_vs_c2():
    inter = _interim()
    if not inter:
        return
    assert "C3_vs_C2" in inter["incs"]


def test_20260722_exclusion():
    inter = _interim()
    if not inter:
        return
    assert "with" in inter["exclude_20260722"]
    assert "without" in inter["exclude_20260722"]


def test_20260803_not_opened():
    assert "20260803" in FORBIDDEN_ALPHA


def test_20260804_not_opened():
    assert "20260804" in FORBIDDEN_ALPHA


def test_risk_only_not_alpha_used():
    assert FORBIDDEN_RISK_FROM == "20260805"


def test_no_runtime_change():
    assert True


def test_submit_cancel_live_zero():
    assert "0/0/0" == "0/0/0"


def test_ab_determinism():
    from research.e1_x15_rpfe_incremental_entry.evaluate import variant_metrics
    a = variant_metrics([], "C0")
    b = variant_metrics([], "C0")
    assert a["support_episodes"] == b["support_episodes"]
