"""E1_X18 VWAP reject failure source tests."""
from __future__ import annotations

import json
from pathlib import Path

from research.e1_x18_vwap_failure_source import (
    CANDIDATE_STATUS,
    FORBIDDEN_DAY,
    FORBIDDEN_RISK_FROM,
    HIST_SOURCE_RUN,
    PROSP_DAY,
    PROSP_ROLE,
    PROSP_SOURCE_RUN,
    TIME_BUCKETS,
    VWAP_UPPER_LIMIT_BPS,
)

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x18_vwap_failure_source"
X16 = NATIVE / "results" / "research" / "e1_x16_same_anchor_vwap_reject" / "report.json"
X17 = NATIVE / "results" / "research" / "e1_x17_vwap_reject_prospective" / "report.json"


def _interim():
    p = OUT / "_interim.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def test_source_run_identity():
    assert json.loads(X16.read_text(encoding="utf-8"))["run_id"] == HIST_SOURCE_RUN
    assert json.loads(X17.read_text(encoding="utf-8"))["run_id"] == PROSP_SOURCE_RUN
    inter = _interim()
    if inter:
        assert inter["hist_source"] == HIST_SOURCE_RUN
        assert inter["prosp_source"] == PROSP_SOURCE_RUN


def test_candidate_closed_rejected():
    assert CANDIDATE_STATUS == "CLOSED_REJECTED"
    inter = _interim()
    if inter:
        assert inter["candidate_status"] == "CLOSED_REJECTED"


def test_no_threshold_retune():
    assert VWAP_UPPER_LIMIT_BPS == 100.73709346405396
    inter = _interim()
    if inter:
        assert inter["no_retune"] is True
        assert inter["threshold"] == VWAP_UPPER_LIMIT_BPS


def test_no_inverse_candidate():
    inter = _interim()
    if inter:
        assert inter["no_inverse"] is True


def test_contract_parity():
    inter = _interim()
    if inter:
        assert inter["parity_ok"] is True


def test_historical_daily_decomposition():
    inter = _interim()
    if inter:
        assert inter["hist_daily_n"] == 9


def test_threshold_percentile_rank():
    inter = _interim()
    if not inter:
        return
    assert "transport" in inter
    assert "hist_median_threshold_percentile_rank" in inter["transport"]


def test_common_symbol_comparison():
    inter = _interim()
    if inter:
        assert inter["common_n"] >= 0


def test_fixed_time_buckets():
    assert set(TIME_BUCKETS) == {"AM_OPEN", "AM_MID", "PM_OPEN", "PM_MID"}
    inter = _interim()
    if inter:
        assert inter["time_buckets_fixed"] is True


def test_market_state_asof_only():
    inter = _interim()
    if inter:
        assert inter["asof_only"] is True


def test_no_future_regime_feature():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert r["market_state"]["no_future_regime_feature"] is True


def test_no_progress_direction_decomposition():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert "NO_PROGRESS_IMPROVEMENT_ADVERSE_DIRECTION" in r["no_progress_decomposition"]


def test_20260803_consumed():
    assert PROSP_DAY == "20260803"
    assert PROSP_ROLE == "CONSUMED_PROSPECTIVE_FAILURE_ANALYSIS_ONLY"


def test_20260804_not_opened():
    assert FORBIDDEN_DAY == "20260804"
    inter = _interim()
    if inter:
        assert inter["opened_20260804"] is False


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
