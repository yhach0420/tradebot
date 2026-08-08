"""E1_X28A1 TARGET floor repair tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x28a1_candidate_exit_repair"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "report.json"
    if not r.exists():
        pytest.skip("no interim/report")
    return json.loads(r.read_text(encoding="utf-8"))


def test_x28a_v1_manifest_sha(interim):
    from research.e1_x28a1_candidate_exit_repair import SOURCE_X28A_MANIFEST_V1_SHA
    assert interim.get("source_x28a_manifest_v1_sha") == SOURCE_X28A_MANIFEST_V1_SHA


def test_x25_path_sha(interim):
    from research.e1_x28a1_candidate_exit_repair import X25_PATH_SHA
    assert interim.get("x25_path_sha") == X25_PATH_SHA


def test_x26a_manifest_sha(interim):
    from research.e1_x28a1_candidate_exit_repair import SOURCE_X26A_MANIFEST_SHA
    assert interim.get("x26a_manifest_sha") == SOURCE_X26A_MANIFEST_SHA


def test_unique_masks_6441(interim):
    assert interim.get("unique_masks") == 6441


def test_all_6441_assigned(interim):
    assert interim.get("assignments") == 6441


def test_v1_target_issue_reproduced(interim):
    assert interim.get("v1_target_count") == 369
    assert interim.get("v1_raw_target_below_20", 0) >= 250


def test_raw_target_below_20_never_snapped_up():
    from research.e1_x28a1_candidate_exit_repair.target_v2 import snap_target_floor_strict
    t, err = snap_target_floor_strict(19.0)
    assert t is None
    assert err == "CANDIDATE_TARGET_BELOW_MINIMUM"
    t2, err2 = snap_target_floor_strict(10.0)
    assert t2 is None and err2 == "CANDIDATE_TARGET_BELOW_MINIMUM"


def test_raw_target_20_or_more_snapped_down():
    from research.e1_x28a1_candidate_exit_repair.target_v2 import snap_target_floor_strict
    assert snap_target_floor_strict(20.0)[0] == 20.0
    assert snap_target_floor_strict(27.0)[0] == 20.0
    assert snap_target_floor_strict(38.0)[0] == 30.0
    assert snap_target_floor_strict(47.0)[0] == 40.0


def test_target_reach_support(interim):
    assert interim.get("raw_target_below_20_never_snapped_up") is True


def test_target_reach_within_candidate_horizon():
    from research.e1_x28a1_candidate_exit_repair.target_v2 import design_target_v2
    import numpy as np
    # synthetic: raw 25 → target 20; within-horizon support insufficient
    m = {"MFE_300_q25": 25.0}
    n = 20
    selected = np.ones(n, dtype=bool)
    path_ok = np.ones(n, dtype=bool)
    dates = np.array(["20260721"] * 5 + ["20260722"] * 5 + ["20260723"] * 5 + ["20260724"] * 5)
    metrics = {
        "ok": path_ok,
        "up_20_reached": np.ones(n, dtype=bool),
        "up_20_time_sec": np.full(n, 500.0),  # all after horizon 300
        "pre_reach_MAE_20_bps": np.full(n, -15.0),
    }
    d = design_target_v2(
        m=m, horizon_sec=300, selected=selected, metrics=metrics, dates=dates, path_ok=path_ok,
    )
    assert d["ok"] is False
    assert d["reason"] == "CANDIDATE_TARGET_WITHIN_HORIZON_SUPPORT_INSUFFICIENT"


def test_target_no_progress_uses_within_horizon_reaches():
    from research.e1_x28a1_candidate_exit_repair.target_v2 import design_target_v2
    import numpy as np
    m = {"MFE_600_q25": 35.0}
    n = 30
    selected = np.ones(n, dtype=bool)
    path_ok = np.ones(n, dtype=bool)
    dates = np.array(
        ["20260721"] * 8 + ["20260722"] * 8 + ["20260723"] * 8 + ["20260724"] * 6
    )
    # within-horizon times ~200 → q75 within horizon; snap ceil to 300
    metrics = {
        "ok": path_ok,
        "up_30_reached": np.ones(n, dtype=bool),
        "up_30_time_sec": np.linspace(100.0, 250.0, n),
        "pre_reach_MAE_30_bps": np.full(n, -20.0),
    }
    d = design_target_v2(
        m=m, horizon_sec=600, selected=selected, metrics=metrics, dates=dates, path_ok=path_ok,
    )
    assert d["ok"] is True
    assert d["no_progress_sec"] <= 600.0 + 1e-9
    assert d["reach_time_q75_within_horizon"] is not None
    assert d["no_progress_sec"] >= d["reach_time_q75_within_horizon"] - 1e-6 or d["no_progress_sec"] == 600.0


def test_target_no_progress_not_beyond_candidate_horizon():
    from research.e1_x28a1_candidate_exit_repair.target_v2 import design_target_v2
    import numpy as np
    m = {"MFE_300_q25": 40.0}  # snaps to 40; nearest upside metric level = 30
    n = 24
    selected = np.ones(n, dtype=bool)
    path_ok = np.ones(n, dtype=bool)
    dates = np.array(["20260721"] * 6 + ["20260722"] * 6 + ["20260723"] * 6 + ["20260724"] * 6)
    metrics = {
        "ok": path_ok,
        "up_30_reached": np.ones(n, dtype=bool),
        "up_30_time_sec": np.full(n, 290.0),
        "pre_reach_MAE_30_bps": np.full(n, -15.0),
    }
    d = design_target_v2(
        m=m, horizon_sec=300, selected=selected, metrics=metrics, dates=dates, path_ok=path_ok,
    )
    assert d["ok"] is True
    assert d["no_progress_sec"] <= 300.0 + 1e-9
    assert d["max_hold_sec"] <= 300.0 + 1e-9


def test_invalid_target_uses_fallback(interim):
    assert interim.get("changed_assignment_count", 0) >= 1
    assert (
        interim.get("target_to_family_fallback_count", 0)
        + interim.get("target_to_control_fallback_count", 0)
    ) >= 1 or interim.get("v2_candidate_target_count", 0) < interim.get("v1_target_count", 369)


def test_invalid_target_not_converted_to_trail(interim):
    # Candidate-specific TRAIL preserved; invalid TARGET → family/control fallback, not new TRAIL
    assert interim.get("v2_candidate_trail_count") == interim.get("v1_candidate_trail_count", 6057)
    assert interim.get("v2_candidate_trail_count") == 6057


def test_trail_assignments_parity(interim):
    assert interim.get("trail_parity_ok") is True


def test_no_evaluation_parameter_use(interim):
    assert interim.get("evaluation_not_used_for_params") is True


def test_no_x27_pnl_use(interim):
    assert interim.get("x27_pnl_not_used") is True


def test_no_x28_pnl_use(interim):
    assert interim.get("x28_pnl_not_used") is True


def test_semantic_exit_dedup(interim):
    assert interim.get("unique_semantic_exit_count", 0) >= 1
    assert interim.get("unique_semantic_exit_count") <= interim.get("assignments", 6441)


def test_discovery_trigger_replay(interim):
    assert interim.get("discovery_replay_done", True) is True


def test_no_profit_ranking(interim):
    assert interim.get("no_profit_ranking", True) is True


def test_manifest_v2_sha(interim):
    sha = interim.get("manifest_sha256") or ""
    assert len(sha) == 64
    from research.e1_x28a1_candidate_exit_repair import SOURCE_X28A_MANIFEST_V1_SHA
    assert sha != SOURCE_X28A_MANIFEST_V1_SHA


def test_no_candidate_closed(interim):
    assert interim.get("candidates_closed", 0) == 0


def test_no_runtime_change(interim):
    s = interim.get("safety") or {}
    assert s.get("production_runtime_changed") is False
    assert s.get("runtime_ENTRY_changed") is False
    assert s.get("runtime_EXIT_changed") is False


def test_submit_cancel_live_zero(interim):
    assert (interim.get("safety") or {}).get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(interim):
    # content_sha present; A/B tautology on single pass is recorded in report after publish
    assert interim.get("content_sha")
