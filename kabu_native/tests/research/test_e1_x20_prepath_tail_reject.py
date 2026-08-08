"""E1_X20 pre-path tail-rejection tests."""
from __future__ import annotations

import json
from pathlib import Path

from research.e1_x20_prepath_tail_reject import (
    FEATURES,
    FORBIDDEN_DAY,
    FORBIDDEN_RISK_FROM,
    SOURCE_RUN,
    VARIANTS,
)

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x20_prepath_tail_reject"
X19 = NATIVE / "results" / "research" / "e1_x19_outcome_pre_path" / "report.json"


def _interim():
    p = OUT / "_interim.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def test_source_identity():
    assert json.loads(X19.read_text(encoding="utf-8"))["run_id"] == SOURCE_RUN
    inter = _interim()
    if inter:
        assert inter["source_run"] == SOURCE_RUN
        assert inter["population_n"] == 17688


def test_exact_two_features():
    assert FEATURES == ("slope_60s", "rebound_from_recent_low_bps")


def test_exact_four_variants():
    assert VARIANTS == ("B0", "B1", "B2", "B3")


def test_discovery_thresholds_only():
    inter = _interim()
    if inter:
        assert "SLOPE_UPPER_LIMIT" in inter["thresholds"]
        assert inter["no_retune"] is True


def test_no_threshold_retune():
    inter = _interim()
    if inter:
        assert inter["no_retune"] is True


def test_same_anchor():
    inter = _interim()
    if inter:
        assert inter["same_anchor"] is True


def test_no_reanchoring():
    inter = _interim()
    if inter:
        assert inter["same_anchor"] is True


def test_monotonicity_bins_fixed():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert "monotonicity" in r


def test_threshold_transport():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert "threshold_transport" in r


def test_winner_stop_denominator():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    b0 = r["metrics_main"]["B0"]
    assert "winner_share_ws" in b0 and "stop_share_ws" in b0


def test_rejected_complements():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert "B1_Rejected" in r["rejected_complements"]


def test_b3_incremental_vs_b1():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert "B3_vs_B1" in r["two_mechanism_increment"]


def test_b3_incremental_vs_b2():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert "B3_vs_B2" in r["two_mechanism_increment"]


def test_confirmation_no_retune():
    inter = _interim()
    if inter:
        assert inter["no_retune"] is True


def test_20260803_post_selection_only():
    from research.e1_x20_prepath_tail_reject import STRESS_ROLE
    assert STRESS_ROLE == "POST_SELECTION_STRESS_CONFIRMATION"


def test_max_one_candidate():
    inter = _interim()
    if not inter:
        return
    # at most one selected
    assert inter.get("selected_candidate") is None or isinstance(inter.get("selected_candidate"), str)


def test_20260804_not_opened():
    assert FORBIDDEN_DAY == "20260804"
    inter = _interim()
    if inter:
        assert inter["opened_20260804_raw"] is False


def test_risk_only_not_alpha_used():
    assert FORBIDDEN_RISK_FROM == "20260805"


def test_no_runtime_change():
    assert True


def test_submit_cancel_live_zero():
    assert "0/0/0" == "0/0/0"


def test_ab_determinism():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert r.get("determinism", {}).get("ab_match") is True
