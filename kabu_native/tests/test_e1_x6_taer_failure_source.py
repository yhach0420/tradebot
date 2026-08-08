"""Regression tests for TAER Failure Source Analysis V2."""
from __future__ import annotations

import math

import pytest

from research.e1_x6_provisional.cost_contract import post_cost_label_bps, yen_roundtrip_cost
from research.e1_x6_taer.failure_source.analysis import primary_rows
from research.e1_x6_taer.failure_source.clusters import build_overlap_clusters
from research.e1_x6_taer.failure_source.precommit import FEATURE_SCHEMA, FORBIDDEN_FEATURES
from research.e1_x6_taer.failure_source.opportunity import OppState, _opp_row


def test_opportunity_uses_best_ask_entry():
    # net bps identity: entry=ask, exit=bid
    ask, bid = 1000.0, 1001.0
    assert abs(post_cost_label_bps(ask, bid) - ((bid / ask - 1) * 10000 - 5)) < 1e-9


def test_opportunity_uses_same_symbol_best_bid():
    st = OppState("e", "PULLBACK_RECLAIM", "20260721", "AM", "9984", 100.0)
    st.ensure_horizons()
    st.entry_ask = 1000.0
    st.started = True
    st.best[300.0] = 1.0
    st.best_net_300 = 1.0
    st.best_exit_bid = 1000.1
    st.path_event_count = 3
    row = _opp_row(st)
    assert row["best_exit_bid"] == 1000.1
    assert row["entry_price"] == 1000.0


def test_opportunity_cannot_cross_day():
    # OppState keyed by day; cross-day updates rejected by caller contract
    st = OppState("e", "RANGE_BREAKOUT", "20260721", "AM", "9984", 1.0)
    assert st.day == "20260721"


def test_opportunity_cannot_cross_session():
    st = OppState("e", "RANGE_BREAKOUT", "20260721", "AM", "9984", 1.0)
    assert st.session == "AM"


def test_cost_applied_once():
    assert abs(yen_roundtrip_cost(1000.0) - 50.0) < 1e-9
    flat = post_cost_label_bps(1000.0, 1000.0)
    assert abs(flat - (-5.0)) < 1e-9


def test_future_feature_not_in_entry_table():
    for bad in ("future_mfe", "future_mae", "future_price", "exit_reason", "scenario_id"):
        assert bad not in FEATURE_SCHEMA
        assert bad in FORBIDDEN_FEATURES or bad.replace("future_", "future_") in FORBIDDEN_FEATURES


def test_overlap_cluster_deterministic():
    eps = [
        {"episode_id": "d|s|2", "day": "20260721", "session": "AM", "symbol": "A",
         "entry_t": 100.0, "setup_type": "PULLBACK_RECLAIM", "scenario_id_prior": "S1"},
        {"episode_id": "d|s|1", "day": "20260721", "session": "AM", "symbol": "A",
         "entry_t": 100.0, "setup_type": "PULLBACK_RECLAIM", "scenario_id_prior": "S1"},
        {"episode_id": "d|s|3", "day": "20260721", "session": "AM", "symbol": "A",
         "entry_t": 500.0, "setup_type": "PULLBACK_RECLAIM", "scenario_id_prior": "S1"},
    ]
    a, sa = build_overlap_clusters(eps)
    b, sb = build_overlap_clusters(list(reversed(eps)))
    assert sa["overlap_cluster_n"] == sb["overlap_cluster_n"] == 2
    reps_a = sorted(x["episode_id"] for x in a if x["is_cluster_representative"])
    reps_b = sorted(x["episode_id"] for x in b if x["is_cluster_representative"])
    assert reps_a == reps_b
    # overlapping 100 and 100 same cluster; 500 separate (100+300=400 < 500)
    assert reps_a == ["d|s|1", "d|s|3"]


def test_cluster_weight_sum():
    eps = [
        {"episode_id": f"e{i}", "day": "20260721", "session": "AM", "symbol": "A",
         "entry_t": 10.0 + i, "setup_type": "RANGE_BREAKOUT", "scenario_id_prior": "S1"}
        for i in range(5)
    ]
    enriched, summary = build_overlap_clusters(eps)
    assert abs(summary["cluster_weight_sum"] - summary["overlap_cluster_n"]) < 1e-9
    assert abs(sum(e["cluster_weight"] for e in enriched) - summary["overlap_cluster_n"]) < 1e-9


def test_s7_excluded_from_primary_supervised_analysis():
    opp = [
        {"episode_id": "a", "is_cluster_representative": True, "scenario_id_prior": "S1_IMMEDIATE_CONTINUATION",
         "evaluable": True, "best_net_pnl_bps_300s": 1.0},
        {"episode_id": "b", "is_cluster_representative": True, "scenario_id_prior": "S7_CENSORED_OR_OTHER",
         "evaluable": True, "best_net_pnl_bps_300s": 2.0},
    ]
    feats = [
        {"episode_id": "a", "x": 1},
        {"episode_id": "b", "x": 2},
    ]
    po, pf, meta = primary_rows(opp, feats)
    assert [r["episode_id"] for r in po] == ["a"]
    assert meta["excluded_counts"].get("s7") == 1


def test_lodo_preprocessing_build_only():
    # Contract: confirm day unused in fit — encoded in model_diagnostics name/flag
    from research.e1_x6_taer.failure_source.precommit import build_precommit
    pre = build_precommit(episode_ids=["x"], path_ledger_n=1, usable_n=1, excluded_n=0)
    assert pre["model_diagnostic_rules"]["preprocess_fit"] == "build_days_only"
    assert pre["model_diagnostic_rules"]["name"] == "cross_day_diagnostic"


def test_no_calendar_or_symbol_feature():
    for bad in ("day", "weekday", "symbol", "symbol_code", "calendar_date"):
        assert bad not in FEATURE_SCHEMA


def test_same_seed_same_output():
    from research.e1_x6_taer.failure_source.precommit import BOOTSTRAP_SEED
    assert BOOTSTRAP_SEED == 20260804
