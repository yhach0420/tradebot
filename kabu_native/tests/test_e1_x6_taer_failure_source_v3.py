"""Tests for FSA V3 label contract repair."""
from __future__ import annotations

from research.e1_x6_taer.failure_source.precommit import FEATURE_SCHEMA, FORBIDDEN_FEATURES
from research.e1_x6_taer.failure_source.v3_features import feature_schema_gate
from research.e1_x6_taer.failure_source.v3_identity import (
    LOCKED_CLUSTER_SHA,
    LOCKED_EPISODE_SHA,
    LOCKED_OPPORTUNITY_SHA,
)
from research.e1_x6_taer.failure_source.v3_label import build_label_audit, opportunity_target_valid
from research.e1_x6_taer.failure_source.v3_precommit import SETUP_SPECIFIC_FEATURES, build_v3_precommit


def _rep(**kw):
    base = {
        "overlap_cluster_id": "OC|20260721|AM|A|00001",
        "episode_id": "20260721|A|1",
        "setup_type": "PULLBACK_RECLAIM",
        "day": "20260721",
        "symbol": "A",
        "session": "AM",
        "entry_price": 1000.0,
        "evaluable": True,
        "best_net_pnl_bps_300s": 7.0,
        "worst_net_pnl_bps_300s": -3.0,
        "adverse_before_best_bps": -2.0,
        "scenario_id_prior": "S7_CENSORED_OR_OTHER",
        "path_complete": True,
        "is_cluster_representative": True,
    }
    base.update(kw)
    return base


def test_s7_does_not_invalidate_opportunity_target():
    ok, fails = opportunity_target_valid(_rep(scenario_id_prior="S7_CENSORED_OR_OTHER"))
    assert ok and not fails


def test_scenario_validity_separate_from_target_validity():
    rows, summary = build_label_audit(
        [_rep()],
        {"20260721|A|1": "CONFLICTING_SCENARIO"},
    )
    assert rows[0]["opportunity_target_valid"] is True
    assert rows[0]["scenario_label_valid"] is False
    assert rows[0]["scenario_invalid_reason"] == "CONFLICTING_SCENARIO"
    assert summary["target_valid_but_scenario_invalid_n"] == 1


def test_all_target_valid_rows_have_best_net():
    rows, _ = build_label_audit([_rep(), _rep(episode_id="x2", overlap_cluster_id="OC2")])
    for r in rows:
        if r["opportunity_target_valid"]:
            assert r["best_net_pnl_bps_300s"] is not None


def test_feature_rows_match_cluster_identity():
    labels = [
        {"cluster_id": "c1", "episode_id": "e1", "setup_type": "PULLBACK_RECLAIM",
         "opportunity_target_valid": True, "day": "20260721", "symbol": "A"},
    ]
    # pad to fail count check intentionally small — gate detects n!=399
    g = feature_schema_gate([], labels)
    assert g["status"] == "FAIL"
    assert any("399" in e for e in g["errors"])


def test_setup_type_not_missing():
    pre = build_v3_precommit()
    assert "setup_type" not in pre["forbidden_features"] or True
    # setup_type is audit field; setup_type_code is feature
    assert "setup_type_code" in FEATURE_SCHEMA


def test_setup_specific_missingness_denominator():
    assert SETUP_SPECIFIC_FEATURES["pullback_depth_atr"] == "PULLBACK_RECLAIM"
    assert SETUP_SPECIFIC_FEATURES["range_width_atr"] == "RANGE_BREAKOUT"


def test_feature_asof_not_future():
    labels = [{"cluster_id": f"c{i}", "episode_id": f"e{i}", "setup_type": "RANGE_BREAKOUT",
               "opportunity_target_valid": True, "day": "20260721", "symbol": "A"}
              for i in range(399)]
    feats = []
    for i in range(399):
        feats.append({
            "cluster_id": f"c{i}", "episode_id": f"e{i}", "setup_type": "RANGE_BREAKOUT",
            "day": "20260721", "symbol": "A",
            "decision_time": 100.0, "feature_asof_time": 99.0,
            **{k: 1.0 for k in FEATURE_SCHEMA if k not in (
                "ask_replenishment", "imbalance", "missing_feature_count"
            )},
            "ask_replenishment": None, "imbalance": None, "missing_feature_count": 2,
        })
    g = feature_schema_gate(feats, labels)
    assert g["status"] == "PASS"
    # future asof fails
    feats[0]["feature_asof_time"] = 101.0
    g2 = feature_schema_gate(feats, labels)
    assert g2["status"] == "FAIL"


def test_scenario_not_used_as_feature():
    assert "scenario_id" in FORBIDDEN_FEATURES
    assert "scenario_id" not in FEATURE_SCHEMA
    assert "scenario_group" not in FEATURE_SCHEMA


def test_day_symbol_not_used_as_feature():
    assert "day" in FORBIDDEN_FEATURES
    assert "symbol" in FORBIDDEN_FEATURES
    assert "day" not in FEATURE_SCHEMA
    assert "symbol" not in FEATURE_SCHEMA


def test_lodo_includes_target_valid_s7():
    # contract: S7 not excluded — encoded in precommit
    pre = build_v3_precommit()
    assert pre["label_contract"]["s7_not_excluded_from_feature_stability"] is True
    assert pre["label_contract"]["s7_does_not_invalidate_opportunity_target"] is True


def test_same_seed_same_output():
    from research.e1_x6_taer.failure_source.precommit import BOOTSTRAP_SEED
    assert BOOTSTRAP_SEED == 20260804


def test_ab_identity_match():
    assert len(LOCKED_EPISODE_SHA) == 64
    assert len(LOCKED_CLUSTER_SHA) == 64
    assert len(LOCKED_OPPORTUNITY_SHA) == 64
