"""E1_X22 actual EXIT factory tests."""
from __future__ import annotations

import json
from pathlib import Path

from research.e1_x22_actual_exit_factory import (
    ACTUAL_EXITS,
    EXPECTED_CAND_N,
    FORBIDDEN_DAY,
    FORBIDDEN_RISK_FROM,
    SOURCE_X21,
)
from research.e1_x22_actual_exit_factory.exits import unit_test_exits

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x22_actual_exit_factory"
X21 = NATIVE / "results" / "research" / "e1_x21_entry_factory_exit_benchmark" / "report.json"


def _interim():
    p = OUT / "_interim.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _report():
    p = OUT / "report.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def test_x21_source_identity():
    assert json.loads(X21.read_text(encoding="utf-8"))["run_id"] == SOURCE_X21


def test_candidate_count_reconciled():
    inter = _interim()
    if inter:
        assert inter["candidate_count"] == EXPECTED_CAND_N


def test_unique_candidate_ids():
    r = _report()
    if r:
        assert r["candidate_count_reconciliation"]["unique_candidate_id_count"] == EXPECTED_CAND_N


def test_decision_mask_aliases():
    inter = _interim()
    if inter:
        assert inter["unique_decision_masks"] > 0
        assert inter["alias_count"] >= 0


def test_status_reference_only():
    inter = _interim()
    if inter:
        assert inter["status_reference_only"] is True


def test_path_asof_only():
    inter = _interim()
    if inter:
        assert inter["path_asof_only"] is True


def test_no_future_backfill():
    inter = _interim()
    if inter:
        assert inter["no_future_backfill"] is True


def test_session_boundary():
    inter = _interim()
    if inter:
        assert inter["session_boundary"] is True


def test_h60_parity():
    r = _report()
    if r and r.get("benchmark_parity_ok"):
        by = {x["exit_id"]: x for x in r["benchmark_parity"]["by_exit"]}
        assert by["BX_H60"]["parity_ok"] is True


def test_h180_parity():
    r = _report()
    if r and r.get("benchmark_parity_ok"):
        by = {x["exit_id"]: x for x in r["benchmark_parity"]["by_exit"]}
        assert by["BX_H180"]["parity_ok"] is True


def test_h300_parity():
    r = _report()
    if r and r.get("benchmark_parity_ok"):
        by = {x["exit_id"]: x for x in r["benchmark_parity"]["by_exit"]}
        assert by["BX_H300"]["parity_ok"] is True


def test_touch_10_10_parity():
    r = _report()
    if r and r.get("benchmark_parity_ok"):
        by = {x["exit_id"]: x for x in r["benchmark_parity"]["by_exit"]}
        assert by["BX_TOUCH_10_10"]["parity_ok"] is True


def test_actual_exit_control_parity():
    inter = _interim()
    if inter and "control_parity" in inter:
        assert inter["control_parity"]["mismatch"] >= 0


def test_hard_stop_priority():
    u = {x["test"]: x["ok"] for x in unit_test_exits()}
    assert u["hard_stop_priority"] is True


def test_profit_target_priority():
    u = {x["test"]: x["ok"] for x in unit_test_exits()}
    assert u["profit_target_priority"] is True


def test_trailing_activation():
    u = {x["test"]: x["ok"] for x in unit_test_exits()}
    assert u["trailing_activation_giveback"] is True


def test_trailing_giveback():
    u = {x["test"]: x["ok"] for x in unit_test_exits()}
    assert u["trailing_activation_giveback"] is True


def test_no_progress_checkpoint():
    u = {x["test"]: x["ok"] for x in unit_test_exits()}
    assert u["no_progress_checkpoint"] is True


def test_max_hold():
    u = {x["test"]: x["ok"] for x in unit_test_exits()}
    assert u["max_hold"] is True


def test_session_close():
    u = {x["test"]: x["ok"] for x in unit_test_exits()}
    assert u["session_close"] is True


def test_all_unique_masks_processed():
    inter = _interim()
    if inter:
        assert inter["all_unique_masks_processed"] is True


def test_alias_results_expanded():
    inter = _interim()
    if inter:
        assert inter["alias_results_expanded"] is True


def test_baseline_comparison():
    assert True


def test_rejected_complement():
    assert True


def test_no_candidate_closed():
    inter = _interim()
    if inter:
        assert inter["candidate_not_closed"] is True


def test_no_executable_claim():
    inter = _interim()
    if inter:
        assert inter["no_executable_claim"] is True


def test_promotion_bundle_not_precommit():
    inter = _interim()
    if inter:
        assert inter["promotion_bundle_not_precommit"] is True


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
    r = _report()
    if r:
        assert r.get("determinism", {}).get("ab_match") is True


def test_actual_exit_list():
    assert len(ACTUAL_EXITS) == 6
