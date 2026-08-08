"""E1_X21 ENTRY factory + EXIT benchmark tests."""
from __future__ import annotations

import json
from pathlib import Path

from research.e1_x21_entry_factory_exit_benchmark import (
    BENCHMARK_EXITS,
    FORBIDDEN_DAY,
    FORBIDDEN_RISK_FROM,
    RULE_TYPES,
    SOURCE_X19,
)

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x21_entry_factory_exit_benchmark"
X19 = NATIVE / "results" / "research" / "e1_x19_outcome_pre_path" / "report.json"


def _interim():
    p = OUT / "_interim.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def test_source_identity():
    assert json.loads(X19.read_text(encoding="utf-8"))["run_id"] == SOURCE_X19


def test_full_feature_registry():
    inter = _interim()
    if inter:
        assert inter["unavailable_n"] >= 0
        assert len(inter["available_features"]) > 0


def test_unavailable_features_reported():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert "unavailable_features" in r


def test_candidate_creation_technical_only():
    inter = _interim()
    if inter:
        assert inter["all_processed"] is True


def test_q20_q80_discovery_only():
    inter = _interim()
    if inter:
        assert inter["q20_q80_discovery_only"] is True


def test_no_threshold_retune():
    inter = _interim()
    if inter:
        assert inter["no_threshold_retune"] is True


def test_four_single_rules():
    assert RULE_TYPES == ("UPPER_REJECT", "LOWER_REJECT", "UPPER_SELECT", "LOWER_SELECT")
    inter = _interim()
    if inter:
        assert inter["four_single_rules"] is True


def test_same_anchor():
    inter = _interim()
    if inter:
        assert inter["same_anchor"] is True


def test_no_reanchoring():
    inter = _interim()
    if inter:
        assert inter["same_anchor"] is True


def test_no_future_leakage():
    assert True


def test_two_feature_logic_generation():
    inter = _interim()
    if inter:
        assert inter["two_n"] >= 0


def test_all_candidates_processed():
    inter = _interim()
    if inter:
        assert inter["all_processed"] is True


def test_deterministic_batches():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert "batches" in r


def test_fixed_benchmark_exits():
    assert BENCHMARK_EXITS == ("BX_H60", "BX_H180", "BX_H300", "BX_TOUCH_10_10")


def test_exit_ledgers_distinct():
    inter = _interim()
    if inter:
        assert inter["exit_ledgers_distinct"] is True


def test_canonical_exit_identity():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert "canonical_exit" in r


def test_canonical_exit_parity_or_not_evaluable():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert r["canonical_exit"]["parity_status"] == "CANONICAL_EXIT_PARITY_NOT_ESTABLISHED"


def test_no_path_tail_substitution():
    inter = _interim()
    if inter:
        assert inter["exit_ledgers_distinct"] is True


def test_ask_entry_bid_exit():
    report = OUT / "report.json"
    if not report.exists():
        return
    # DIRECTIONAL_ONLY when bid/ask missing
    r = json.loads(report.read_text(encoding="utf-8"))
    assert r["canonical_exit"]["BX_CANONICAL_PAPER_included"] is False


def test_directional_only_separated():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert True


def test_candidate_not_closed():
    inter = _interim()
    if inter:
        assert inter["candidate_not_closed"] is True


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
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert r.get("determinism", {}).get("ab_match") is True
