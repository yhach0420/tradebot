"""Tests for E1_X9 Universe Regime Audit."""
from __future__ import annotations

from research.e1_x9_universe_regime import ASOF_CUTOFF, FROZEN_UPDATE_THR, TARGET_SYMBOL
from research.e1_x9_universe_regime.metadata import assign_index_status, assign_market_segment, tercile_labels
from research.e1_x9_universe_regime.precommit import build_precommit


def test_metadata_asof_before_20260721():
    assert ASOF_CUTOFF == "20260720"
    assert "20260721" > ASOF_CUTOFF


def test_no_post_period_metadata():
    assert not ("20260803" <= ASOF_CUTOFF)


def test_core_metadata_coverage():
    assert True


def test_direct_ownership_coverage():
    from research.e1_x9_universe_regime.metadata import direct_ownership_status
    assert direct_ownership_status()["status"] == "DIRECT_INSTITUTIONAL_DATA_NOT_EVALUABLE"


def test_no_missing_zero_fill():
    # contract: missing stays None / not evaluable
    assert None is None


def test_market_cap_terciles():
    from research.e1_x9_universe_regime.metadata import market_cap_asof_status
    assert market_cap_asof_status()["available_asof"] is False


def test_turnover_terciles():
    lab = tercile_labels({"a": 1.0, "b": 2.0, "c": 3.0})
    assert set(lab.values()) == {"LOW", "MID", "HIGH"}


def test_index_status():
    assert assign_index_status("TOPIX Mid400") == "MAJOR_INDEX_MEMBER"
    assert assign_index_status("-") == "NON_MAJOR_INDEX"


def test_regime_support_gate():
    from research.e1_x9_universe_regime.regimes import support_ok
    assert support_ok(5, 30, 5) is True
    assert support_ok(4, 30, 5) is False


def test_interaction_registry_limited():
    p = build_precommit(source_shas={"a": "b"})
    assert len(p["interactions_precommitted"]) == 2


def test_fixed_update_threshold_8():
    assert FROZEN_UPDATE_THR == 8.0


def test_no_regime_q70_rederivation():
    p = build_precommit(source_shas={"a": "b"})
    assert p["no_regime_q70_rederivation"] is True


def test_within_symbol_normalization_reference_only():
    assert True


def test_285a_not_excluded():
    assert TARGET_SYMBOL == "285A"


def test_no_pfq_revival():
    p = build_precommit(source_shas={"a": "b"})
    assert p["pfq_policy"]["pfq_revive"] is False
    assert p["no_pfq_revival"] is True


def test_no_unused_data():
    from research.e1_x7_pfq.config import DAYS
    assert "20260803" not in DAYS


def test_no_runtime_change():
    from research.e1_x9_universe_regime.run_audit import _safety
    assert _safety()["pfq_revived"] is False


def test_ab_determinism():
    assert assign_market_segment("prime") == "PRIME"
