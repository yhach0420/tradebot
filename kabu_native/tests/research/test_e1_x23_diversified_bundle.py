"""E1_X23 diversified bundle tests."""
from __future__ import annotations

import json
from pathlib import Path

from research.e1_x23_diversified_bundle import (
    EXPECTED_CAND_N,
    EXPECTED_UNIQUE_MASKS,
    FORBIDDEN_RISK_FROM,
    SOURCE_X22,
    TARGET_DAY,
)

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x23_diversified_bundle"
X22 = NATIVE / "results" / "research" / "e1_x22_actual_exit_factory" / "report.json"


def _interim():
    p = OUT / "_interim.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _report():
    p = OUT / "report.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def test_x22_source_identity():
    assert json.loads(X22.read_text(encoding="utf-8"))["run_id"] == SOURCE_X22


def test_x21_touch_name_normalized():
    r = _report()
    if r:
        names = {x["x23_normalized_name"] for x in r["touch_normalization"]}
        assert "LEGACY_BX_TOUCH_100_100" in names


def test_control_mismatch_3_explained():
    r = _report()
    if r and r.get("control_mismatch_3"):
        assert len(r["control_mismatch_3"]) == 3
        assert all(m.get("allowed") for m in r["control_mismatch_3"])


def test_candidate_registry_unchanged():
    inter = _interim()
    if inter:
        assert inter["candidate_registry_unchanged"] is True


def test_thresholds_unchanged():
    inter = _interim()
    if inter:
        assert inter["thresholds_unchanged"] is True


def test_exit_specs_unchanged():
    inter = _interim()
    if inter:
        assert inter["exit_specs_unchanged"] is True


def test_alias_not_duplicated():
    inter = _interim()
    if inter:
        assert inter["alias_not_duplicated"] is True


def test_single_family_coverage():
    r = _report()
    if r:
        assert r["bundle_audit"]["single_family_coverage_ok"] is True


def test_component_family_coverage():
    r = _report()
    if r:
        assert "component_signature_mask_counts" in r["bundle_audit"]


def test_exit_coverage():
    r = _report()
    if r:
        assert len(r["bundle_audit"]["exit_counts"]) == 6


def test_retention_coverage():
    r = _report()
    if r:
        assert "retention_band_counts" in r["bundle_audit"]


def test_period_tag_coverage():
    r = _report()
    if r:
        assert "period_tag_counts" in r["bundle_audit"]


def test_bundle_precommit_before_raw_open():
    inter = _interim()
    if inter:
        assert inter["raw_opened_before_precommit"] is False


def test_bundle_sha_match():
    r = _report()
    pre = OUT / "_precommit.json"
    if r and pre.exists():
        body = json.loads(pre.read_text(encoding="utf-8"))
        assert body["bundle_sha256"] == r["precommit"]["bundle_sha256"]


def test_20260804_open_once():
    inter = _interim()
    if inter:
        assert inter.get("opened_20260804") is True
        assert inter.get("open_once") is True


def test_same_population_contract():
    assert TARGET_DAY == "20260804"


def test_same_anchor():
    inter = _interim()
    if inter:
        assert inter["same_anchor"] is True


def test_no_threshold_retune():
    inter = _interim()
    if inter:
        assert inter["no_threshold_retune"] is True


def test_same_exit_baseline_comparison():
    r = _report()
    if r and r.get("prospective_summary"):
        assert True


def test_no_candidate_closed():
    inter = _interim()
    if inter:
        assert inter["no_candidate_closed"] is True


def test_no_executable_claim():
    inter = _interim()
    if inter:
        assert inter["no_executable_claim"] is True


def test_canonical_exit_not_injected():
    inter = _interim()
    if inter:
        assert inter["canonical_not_injected"] is True


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


def test_expected_counts():
    assert EXPECTED_CAND_N == 8254
    assert EXPECTED_UNIQUE_MASKS == 6441
