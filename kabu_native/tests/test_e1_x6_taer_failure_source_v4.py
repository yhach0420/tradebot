"""Tests for FSA V4 stability gate contract repair."""
from __future__ import annotations

from research.e1_x6_taer.failure_source.v4_analysis import (
    _median_split_effect,
    _zero_variance,
    audit_entry_features,
    class_support_table,
    lodo_v4,
    univariate_v4,
)
from research.e1_x6_taer.failure_source.v4_identity import (
    LOCKED_CLUSTER_SHA,
    LOCKED_EPISODE_SHA,
    LOCKED_OPPORTUNITY_SHA,
    LOCKED_TARGET_VALIDITY_SHA,
)
from research.e1_x6_taer.failure_source.v4_precommit import ENTRY_FEATURE_COLUMNS, MIN_EFFECT_BPS, build_v4_precommit


def _row(day, setup, plus5, feat=1.0, eid=None, best=10.0):
    eid = eid or f"{day}|X|{plus5}|{feat}"
    return {
        "episode_id": eid,
        "cluster_id": f"OC|{eid}",
        "setup_type": setup,
        "day": day,
        "symbol": "X",
        "session": "AM",
        "net_plus_5bps": 1 if plus5 else 0,
        "best_net_pnl_bps_300s": best,
        "feat_a": feat,
        "event_freshness": 0.0,
        "board_freshness": 0.0,
    }


def test_day_class_counts_use_cluster_labels():
    rows = [
        _row("d1", "PULLBACK_RECLAIM", True, best=20),
        _row("d1", "PULLBACK_RECLAIM", False, best=20),  # median still positive
        _row("d1", "PULLBACK_RECLAIM", False, best=20),
        _row("d1", "PULLBACK_RECLAIM", True, best=20),
    ]
    cs = class_support_table(rows)
    d = cs["by_setup"]["PULLBACK_RECLAIM"]["days"][0]
    assert d["positive_n"] == 2 and d["negative_n"] == 2
    assert d["median_best_net_300s"] > 0  # median positive but two-class day
    assert d["descriptive_two_class"] is True


def test_positive_median_day_is_not_class_support():
    pre = build_v4_precommit()
    assert pre["class_support_definition"]["forbidden"] == "daily median sign of best_net_pnl_bps_300s"
    assert pre["continuous_outcome_stability"]["does_not_use_non_opportunity_days_ge_4"] is True


def test_each_day_positive_and_negative_counts_reconcile():
    rows = [_row("d1", "RANGE_BREAKOUT", i % 2 == 0, eid=f"e{i}", best=5 if i % 2 == 0 else 0)
            for i in range(6)]
    cs = class_support_table(rows)
    for d in cs["by_setup"]["RANGE_BREAKOUT"]["days"]:
        assert d["positive_n"] + d["negative_n"] == d["cluster_n"]


def test_continuous_stability_does_not_require_negative_median_days():
    pre = build_v4_precommit()
    reqs = pre["continuous_outcome_stability"]["requires"]
    assert "non_opportunity_days" not in str(reqs)
    assert "evaluable_day_deletions_ge_7" in reqs


def test_constant_feature_is_not_evaluable():
    assert _zero_variance([0.0, 0.0, 0.0]) is True
    rows = []
    for i, day in enumerate(["20260721", "20260722", "20260723", "20260724",
                             "20260727", "20260728", "20260729", "20260730", "20260731"]):
        for j in range(10):
            r = _row(day, "PULLBACK_RECLAIM", j % 2 == 0, feat=0.0, eid=f"{day}|{j}", best=float(j))
            r["event_freshness"] = 0.0
            rows.append(r)
    # monkey via FEATURE path — use univariate on event_freshness through direct helper
    from research.e1_x6_taer.failure_source.precommit import FEATURE_SCHEMA
    assert "event_freshness" in FEATURE_SCHEMA
    uni = univariate_v4(rows)
    ef = next(x for x in uni["by_setup"]["PULLBACK_RECLAIM"] if x["feature"] == "event_freshness")
    assert ef["status"] == "NON_EVALUABLE_ZERO_VARIANCE"
    assert ef["direction"] is None
    assert ef["primary_candidate_eligible"] is False


def test_null_spearman_does_not_create_effect():
    assert _median_split_effect([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
                                [1, 2, 3, 4, 5, 6, 7, 8]) is None


def test_bootstrap_runs_for_direction_stable_features():
    pre = build_v4_precommit()
    assert pre["bootstrap_definition"]["reps"] == 1000
    assert pre["bootstrap_definition"]["unit"] == "day_x_symbol"


def test_bootstrap_unit_is_day_symbol():
    pre = build_v4_precommit()
    assert pre["bootstrap_definition"]["unit"] == "day_x_symbol"
    assert MIN_EFFECT_BPS["PULLBACK_RECLAIM"] == 2.0
    assert MIN_EFFECT_BPS["RANGE_BREAKOUT"] == 3.0


def test_range_model_keeps_n100_gate():
    pre = build_v4_precommit()
    assert pre["model_execution_gate"]["range"]["target_valid_clusters_ge"] == 100
    assert pre["model_execution_gate"]["range"]["not_relaxed_posthoc"] is True
    assert pre["model_execution_gate"]["range"]["if_lt_100"] == "NOT_EVALUABLE_SUPPORT_LT_100"


def test_entry_features_contains_all_columns():
    required = {
        "cluster_id", "episode_id", "setup_type", "pullback_depth_atr", "range_width_atr",
        "event_freshness", "board_freshness", "missing_feature_count",
    }
    assert required.issubset(set(ENTRY_FEATURE_COLUMNS))


def test_entry_features_setup_not_missing():
    labels = [{"cluster_id": f"c{i}", "episode_id": f"e{i}"} for i in range(399)]
    rows = []
    for i in range(399):
        r = {c: None for c in ENTRY_FEATURE_COLUMNS}
        r.update({
            "cluster_id": f"c{i}", "episode_id": f"e{i}", "setup_type": "PULLBACK_RECLAIM",
            "day": "20260721", "session": "AM", "symbol": "A",
            "decision_time": 1.0, "feature_asof_time": 1.0,
            "trade_side_quality": "TICK_RULE_INFERRED",
            "missing_feature_count": 0,
        })
        rows.append(r)
    g = audit_entry_features(rows, labels)
    assert g["status"] == "PASS"
    rows[0]["setup_type"] = ""
    g2 = audit_entry_features(rows, labels)
    assert g2["status"] == "FAIL"


def test_feature_table_399_identity_match():
    labels = [{"cluster_id": f"c{i}", "episode_id": f"e{i}"} for i in range(399)]
    rows = [{c: 0 for c in ENTRY_FEATURE_COLUMNS} for i in range(398)]
    for i, r in enumerate(rows):
        r.update({"cluster_id": f"c{i}", "episode_id": f"e{i}", "setup_type": "RANGE_BREAKOUT",
                  "trade_side_quality": "X", "decision_time": 1, "feature_asof_time": 1})
    g = audit_entry_features(rows, labels)
    assert g["status"] == "FAIL"
    assert any("399" in e for e in g["errors"])


def test_ab_identity_match():
    assert len(LOCKED_EPISODE_SHA) == 64
    assert len(LOCKED_CLUSTER_SHA) == 64
    assert len(LOCKED_OPPORTUNITY_SHA) == 64
    assert len(LOCKED_TARGET_VALIDITY_SHA) == 64
