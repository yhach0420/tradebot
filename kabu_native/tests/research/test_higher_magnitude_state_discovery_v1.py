"""Higher-magnitude episode tests. No future in episode bounds. CS1–CS3 not rescued. Frozen Validation closed."""
from __future__ import annotations

import inspect

from research.higher_magnitude_state_discovery_v1 import (
    CS1_CS3_RESCUED,
    EPISODE_GAP_MIN,
    EPISODE_LENGTH_GRID_SEARCHED,
    FROZEN_VALIDATION_OPENED,
    KABU_50_APPLIED,
    MAX_CANDIDATES,
    PARENT_VERDICT,
    RANK_DIAGNOSTIC_K,
    REJECTED_CS,
)
from research.higher_magnitude_state_discovery_v1.analyze import decide
from research.higher_magnitude_state_discovery_v1.episodes import cluster_symbol_day, freeze_episode_ids
from research.higher_magnitude_state_discovery_v1.isolation import CAUSAL_PATH_OUT, OUT, write_overlap_n
from research.higher_magnitude_state_discovery_v1.publish import SHEET_ORDER


def test_constants():
    assert PARENT_VERDICT == "CAUSAL_STRUCTURE_FOUND_EDGE_INSUFFICIENT_V1"
    assert CS1_CS3_RESCUED is False
    assert REJECTED_CS[0].startswith("CS1")
    assert EPISODE_GAP_MIN == 15
    assert EPISODE_LENGTH_GRID_SEARCHED is False
    assert RANK_DIAGNOSTIC_K == (1, 3)
    assert MAX_CANDIDATES == 2
    assert FROZEN_VALIDATION_OPENED is False
    assert KABU_50_APPLIED is False
    assert OUT.name == "higher_magnitude_state_discovery_v1"
    assert CAUSAL_PATH_OUT.name == "causal_path_to_complete_strategy_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert "Rank_Response" in SHEET_ORDER
    assert "Frozen_Validation_Status" in SHEET_ORDER


def test_episode_cluster_timestamp_only():
    ev = [
        {"event_time": "09:30", "event_family": "PULLBACK_START", "x0_h15_bps": 50},
        {"event_time": "09:40", "event_family": "VWAP_RECLAIM", "x0_h15_bps": -20},
        {"event_time": "10:20", "event_family": "BREAKOUT20", "x0_h15_bps": 80},
    ]
    clusters = cluster_symbol_day(ev, gap_min=15)
    assert len(clusters) == 2
    assert [x["event_family"] for x in clusters[0]] == ["PULLBACK_START", "VWAP_RECLAIM"]
    assert [x["event_family"] for x in clusters[1]] == ["BREAKOUT20"]
    sha1 = freeze_episode_ids([{"episode_id": "a"}, {"episode_id": "b"}])
    sha2 = freeze_episode_ids([{"episode_id": "b", "x0_h15_bps": 99}, {"episode_id": "a", "mfe_bps": 1}])
    assert sha1 == sha2


def test_decide_and_forbidden():
    bind_fail = decide(bind_ok=False, promoted_n=1, ranking_provided=True, rank_ordered=True, secondary_ran=True, secondary_any_pass=True)
    assert "BIND" in bind_fail["VERDICT"]
    ranked = decide(bind_ok=True, promoted_n=1, ranking_provided=True, rank_ordered=True, secondary_ran=True, secondary_any_pass=True)
    assert ranked["VERDICT"] == "CROSS_SECTIONAL_OPPORTUNITY_SELECTION_FOUND_V1"
    found = decide(bind_ok=True, promoted_n=1, ranking_provided=False, rank_ordered=False, secondary_ran=True, secondary_any_pass=True)
    assert found["VERDICT"] == "HIGH_MAGNITUDE_COMPLETE_STRATEGY_CANDIDATE_FOUND_V1"
    limit = decide(bind_ok=True, promoted_n=0, ranking_provided=False, rank_ordered=True, secondary_ran=False, secondary_any_pass=None)
    assert limit["VERDICT"] == "STOCK_PANEL_MAGNITUDE_LIMIT_CONFIRMED_V1"
    import research.higher_magnitude_state_discovery_v1.analyze as a
    import research.higher_magnitude_state_discovery_v1.candidates as c

    src = inspect.getsource(a) + inspect.getsource(c)
    assert "DESIGN_KABU_50" not in src
    assert "copied_cs1_cs3" in inspect.getsource(c)
    assert c.OCCUPANCY == 3
    assert MAX_CANDIDATES == 2
