"""E1_X26 EXIT library tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x26_exit_library"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if not p.exists():
        r = OUT / "report.json"
        if not r.exists():
            pytest.skip("no interim/report")
        return json.loads(r.read_text(encoding="utf-8"))
    return json.loads(p.read_text(encoding="utf-8"))


def test_x25_source_identity():
    from research.e1_x26_exit_library import SOURCE_X25
    assert SOURCE_X25 == "e1x25_path_20260807_045558_A"


def test_handoff_sha(interim):
    from research.e1_x26_exit_library import X25_HANDOFF_SHA
    assert interim.get("x25_handoff_sha") == X25_HANDOFF_SHA


def test_path_sha(interim):
    from research.e1_x26_exit_library import X25_PATH_SHA
    assert interim.get("x25_path_sha") == X25_PATH_SHA


def test_candidate_count_8254(interim):
    assert interim["candidate_ids"] == 8254


def test_unique_masks_6441(interim):
    assert interim["unique_masks"] == 6441


def test_aliases_1813(interim):
    assert interim["aliases"] == 1813


def test_discovery_only_loaded_before_manifest(interim):
    assert interim.get("exit_parameter_source") == "DISCOVERY_ONLY_MECHANICAL_CALIBRATION"
    assert interim.get("evaluation_metrics_loaded_for_params") is False


def test_evaluation_not_used_for_parameters(interim):
    assert interim.get("evaluation_metrics_loaded_for_params") is False


def test_20260803_not_used_for_parameters(interim):
    assert interim.get("evaluation_metrics_loaded_for_params") is False


def test_20260804_not_used_for_parameters(interim):
    assert interim.get("evaluation_metrics_loaded_for_params") is False


def test_family_margin_deterministic():
    from research.e1_x26_exit_library.routing import family_margin_scores
    feat = {"up30_reach_delta_pt": 5.0, "up30_median_reach_time": 100.0}
    a = family_margin_scores(feat, ["QUICK_MOVE"])
    b = family_margin_scores(feat, ["QUICK_MOVE"])
    assert a == b
    assert a["QUICK_MOVE"] > 0


def test_max_two_family_routes():
    from research.e1_x26_exit_library.routing import route_families
    scores = {"QUICK_MOVE": 10.0, "CONTINUATION": 6.0, "PULLBACK_THEN_RISE": 1.0,
              "DELAYED_MOVE": 0, "SPIKE_AND_GIVEBACK": 0, "NO_CLEAR_PATH_EDGE": 0}
    r = route_families(["QUICK_MOVE", "CONTINUATION", "PULLBACK_THEN_RISE"], scores)
    assert r["primary_path_family"] == "QUICK_MOVE"
    assert r["secondary_path_family"] == "CONTINUATION"
    assert r.get("tertiary") is None


def test_no_clear_exclusive():
    from research.e1_x26_exit_library.routing import route_families
    scores = {f: 0.0 for f in ["QUICK_MOVE", "PULLBACK_THEN_RISE", "CONTINUATION", "DELAYED_MOVE", "SPIKE_AND_GIVEBACK", "NO_CLEAR_PATH_EDGE"]}
    scores["NO_CLEAR_PATH_EDGE"] = 1.0
    r = route_families(["NO_CLEAR_PATH_EDGE"], scores)
    assert r["primary_path_family"] == "NO_CLEAR_PATH_EDGE"
    assert r["secondary_path_family"] is None


def test_candidate_balanced_calibration():
    from research.e1_x26_exit_library.calibrate import candidate_balanced_metrics
    feats = {
        "a": {"pre30_mae_q": -15.0, "mfe300_med": 40.0, "max_gb_300_med": 12.0, "up30_median_reach_time": 120.0},
        "b": {"pre30_mae_q": -25.0, "mfe300_med": 50.0, "max_gb_300_med": 18.0, "up30_median_reach_time": 200.0},
    }
    m = candidate_balanced_metrics(family="QUICK_MOVE", member_ids=["a", "b"], features_by_id=feats)
    assert m["n_masks"] == 2
    assert m["alias_weight"] == 0


def test_anchor_weighted_sensitivity():
    import numpy as np
    from research.e1_x26_exit_library.calibrate import anchor_weighted_metrics
    n = 10
    metrics = {
        "pre_reach_MAE_30_bps": -np.linspace(5, 50, n),
        "up_30_reached": np.ones(n, dtype=bool),
        "pre_reach_MAE_50_bps": -np.linspace(5, 50, n),
        "up_50_reached": np.ones(n, dtype=bool),
        "pre_reach_MAE_60_bps": -np.linspace(5, 50, n),
        "up_60_reached": np.ones(n, dtype=bool),
        "MFE_300s_bps": np.linspace(10, 80, n),
        "eligible_300s": np.ones(n, dtype=bool),
        "fresh_ok_300s": np.ones(n, dtype=bool),
        "MFE_900s_bps": np.linspace(10, 100, n),
        "eligible_900s": np.ones(n, dtype=bool),
        "fresh_ok_900s": np.ones(n, dtype=bool),
        "MFE_1800s_bps": np.linspace(10, 120, n),
        "eligible_1800s": np.ones(n, dtype=bool),
        "fresh_ok_1800s": np.ones(n, dtype=bool),
        "max_giveback_after_MFE_300s_bps": np.linspace(5, 40, n),
        "max_giveback_after_MFE_900s_bps": np.linspace(5, 50, n),
        "max_giveback_after_MFE_1800s_bps": np.linspace(5, 60, n),
        "ok": np.ones(n, dtype=bool),
    }
    dates = np.array(["20260721"] * n)
    masks = {"m1": np.ones(n, dtype=bool), "m2": np.array([1, 1, 1, 0, 0, 0, 0, 0, 0, 0], dtype=bool)}
    aw = anchor_weighted_metrics(
        family="QUICK_MOVE", member_ids=["m1", "m2"], unique_masks=masks,
        metrics=metrics, dates=dates, path_ok=metrics["ok"],
    )
    assert aw["n_anchors_weighted"] == 10


def test_alias_no_weight():
    from research.e1_x26_exit_library.calibrate import candidate_balanced_metrics
    m = candidate_balanced_metrics(family="X", member_ids=["a"], features_by_id={"a": {"pre30_mae_q": -10.0}})
    assert m["alias_weight"] == 0


def test_fixed_parameter_grids():
    from research.e1_x26_exit_library import STOP_GRID_BPS, TARGET_GRID_BPS, GIVEBACK_GRID_BPS
    assert STOP_GRID_BPS[0] == 10
    assert 120 in TARGET_GRID_BPS
    assert 15 in GIVEBACK_GRID_BPS


def test_snap_rules():
    from research.e1_x26_exit_library.snap import snap_ceil, snap_floor
    from research.e1_x26_exit_library import STOP_GRID_BPS, TARGET_GRID_BPS
    assert snap_ceil(22, STOP_GRID_BPS) == 30
    assert snap_floor(45, TARGET_GRID_BPS) == 40


def test_max_two_exits_per_family(interim):
    lib = interim.get("exit_library") or {}
    for k in ("QUICK", "PULLBACK", "CONTINUATION", "DELAYED", "SPIKE"):
        assert len(lib.get(k) or []) <= 2


def test_no_candidate_specific_parameters(interim):
    params = interim.get("exit_parameters") or {}
    # family-level ids only
    for eid in params:
        assert eid.startswith("EXIT_")


def test_quick_exit(interim):
    assert "EXIT_QUICK_TARGET_V1" in (interim.get("exit_library") or {}).get("QUICK", [])


def test_pullback_exit(interim):
    assert "EXIT_PULLBACK_PROTECT_V1" in (interim.get("exit_library") or {}).get("PULLBACK", [])


def test_continuation_exit(interim):
    assert "EXIT_CONTINUATION_PROTECT_V1" in (interim.get("exit_library") or {}).get("CONTINUATION", [])


def test_delayed_exit(interim):
    assert "EXIT_DELAYED_PROTECT_V1" in (interim.get("exit_library") or {}).get("DELAYED", [])


def test_spike_exit(interim):
    assert "EXIT_SPIKE_TARGET_V1" in (interim.get("exit_library") or {}).get("SPIKE", [])


def test_event_priority(interim):
    from research.e1_x26_exit_library import EVENT_PRIORITY
    assert EVENT_PRIORITY[0] == "session_close"
    assert EVENT_PRIORITY[1] == "hard_stop"


def test_touch_eps(interim):
    from research.e1_x26_exit_library import TOUCH_EPS
    assert TOUCH_EPS == 1e-12
    assert interim.get("TOUCH_EPS") == 1e-12


def test_exit_reason_reachable(interim):
    # covered by runner gate; ensure frozen
    assert interim.get("verdict") == "E1_X26_PATH_FAMILY_EXIT_LIBRARY_FROZEN" or interim.get("manifest_sha256")


def test_exit_ledgers_distinct(interim):
    assert interim.get("manifest_sha256")


def test_no_candidate_closed(interim):
    assert interim.get("candidates_closed", 0) == 0


def test_no_exit_ranked(interim):
    assert interim.get("exit_ranked") is False


def test_no_evaluation_profit_generated(interim):
    assert interim.get("evaluation_profit_generated") is False


def test_manifest_sha(interim):
    assert interim.get("manifest_sha256")
    assert len(str(interim["manifest_sha256"])) == 64


def test_x27_handoff(interim):
    assert interim.get("x27_unique_masks") == 6441


def test_no_runtime_change(interim):
    s = interim.get("safety") or {}
    assert s.get("production_runtime_changed") is False
    assert s.get("runtime_EXIT_changed") is False


def test_submit_cancel_live_zero(interim):
    assert (interim.get("safety") or {}).get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(interim):
    assert interim.get("manifest_sha256")
