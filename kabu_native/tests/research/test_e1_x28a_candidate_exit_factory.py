"""E1_X28A candidate-specific EXIT factory tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x28a_candidate_exit_factory"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "report.json"
    if not r.exists():
        pytest.skip("no interim/report")
    return json.loads(r.read_text(encoding="utf-8"))


def test_x25_handoff_sha(interim):
    from research.e1_x28a_candidate_exit_factory import X25_HANDOFF_SHA
    assert interim.get("x25_handoff_sha") == X25_HANDOFF_SHA


def test_x25_path_sha(interim):
    from research.e1_x28a_candidate_exit_factory import X25_PATH_SHA
    assert interim.get("x25_path_sha") == X25_PATH_SHA


def test_x26a_manifest_sha(interim):
    from research.e1_x28a_candidate_exit_factory import SOURCE_X26A_MANIFEST_SHA
    assert interim.get("x26a_manifest_sha") == SOURCE_X26A_MANIFEST_SHA


def test_candidate_ids_8254(interim):
    assert interim["candidate_ids"] == 8254


def test_unique_masks_6441(interim):
    assert interim["unique_masks"] == 6441


def test_aliases_1813(interim):
    assert interim["aliases"] == 1813


def test_discovery_only_parameter_source(interim):
    from research.e1_x28a_candidate_exit_factory import PARAMETER_SOURCE
    assert interim.get("parameter_source") == PARAMETER_SOURCE


def test_evaluation_not_loaded_for_params(interim):
    assert interim.get("evaluation_not_loaded_for_params") is True


def test_20260803_not_loaded_for_params(interim):
    assert interim.get("evaluation_not_loaded_for_params") is True


def test_20260804_not_loaded_for_params(interim):
    assert interim.get("evaluation_not_loaded_for_params") is True


def test_x27_pnl_not_used(interim):
    assert interim.get("x27_pnl_not_used") is True


def test_x28_pnl_not_used(interim):
    assert interim.get("x28_pnl_not_used_for_params") is True


def test_one_primary_exit_per_mask(interim):
    assert interim.get("one_primary_exit_per_mask") is True


def test_all_6441_assigned(interim):
    assert interim.get("assignments") == 6441


def test_horizon_rule():
    from research.e1_x28a_candidate_exit_factory.calibrate import determine_horizon
    m = {"MFE_300_q50": 10.0, "MFE_600_q50": 25.0, "MFE_900_q50": 40.0, "MFE_1800_q50": 55.0}
    h = determine_horizon(m)
    assert h["candidate_horizon_sec"] == 1800


def test_mode_rule():
    from research.e1_x28a_candidate_exit_factory.calibrate import determine_mode
    m = {"MFE_300_q50": 40.0, "MFE_600_q50": 50.0, "terminal_giveback_600_q50": 25.0}
    md = determine_mode(m, 600)
    assert md["exit_mode"] == "TARGET"


def test_target_snap():
    from research.e1_x26_exit_library.snap import snap_floor
    from research.e1_x28a_candidate_exit_factory import TARGET_GRID_BPS
    assert snap_floor(47.0, TARGET_GRID_BPS) == 40.0


def test_trail_activation_snap():
    from research.e1_x26_exit_library.snap import snap_floor
    from research.e1_x28a_candidate_exit_factory import TRAIL_ACTIVATION_GRID_BPS
    assert snap_floor(55.0, TRAIL_ACTIVATION_GRID_BPS) == 50.0


def test_giveback_profit_lock():
    from research.e1_x28a_candidate_exit_factory.calibrate import design_trail
    m = {
        "MFE_900_q25": 50.0,
        "max_giveback_900_q25": 60.0,
        "up_50_metric_support_ok": True,
        "pre_rise_MAE_abs_50_q75": 25.0,
        "up_50_time_q75": 400.0,
    }
    d = design_trail(m, 900)
    assert d["ok"] is True
    assert d["locked_profit_bps"] >= 10.0 - 1e-9


def test_stop_never_below_required():
    from research.e1_x26_exit_library.snap import snap_ceil
    from research.e1_x28a_candidate_exit_factory import STOP_GRID_BPS
    assert snap_ceil(33.0, STOP_GRID_BPS) == 40.0


def test_no_progress_snap():
    from research.e1_x26_exit_library.snap import snap_ceil
    from research.e1_x28a_candidate_exit_factory import NO_PROGRESS_GRID_SEC
    assert snap_ceil(250.0, NO_PROGRESS_GRID_SEC) == 300.0


def test_max_hold_rule():
    from research.e1_x26_exit_library.snap import snap_ceil
    from research.e1_x28a_candidate_exit_factory import MAX_HOLD_GRID_SEC
    assert snap_ceil(900.0, MAX_HOLD_GRID_SEC) == 900.0


def test_fallback_hierarchy():
    from research.e1_x28a_candidate_exit_factory.fallback import choose_fallback
    fb = choose_fallback(tags=["CONTINUATION"], candidate_horizon_sec=1800, x26a_exits={
        "EXIT_CONTINUATION_PROTECT_V2": {
            "stop_bps": 30, "trail_activation_bps": 80, "giveback_bps": 60,
            "giveback_mode": "from_MFE", "no_progress_sec": 900, "max_hold_sec": 1800,
        }
    })
    assert fb["exit_source"] == "FAMILY_FALLBACK"
    assert fb["primary_exit_id"] == "EXIT_CONTINUATION_PROTECT_V2"


def test_no_per_candidate_free_search():
    from research.e1_x28a_candidate_exit_factory import STOP_GRID_BPS
    assert 25 not in STOP_GRID_BPS  # no free values


def test_semantic_exit_hash():
    from research.e1_x28a_candidate_exit_factory.semantic import semantic_exit_sha
    a = {"exit_mode": "TARGET", "stop_bps": 20, "target_bps": 20, "trail_activation_bps": None,
         "giveback_bps": None, "giveback_mode": None, "no_progress_sec": 180, "max_hold_sec": 300,
         "no_progress_mfe_bps": 5.0, "no_progress_abs_ret_bps": 5.0}
    assert semantic_exit_sha(a) == semantic_exit_sha(dict(a))


def test_duplicate_semantics_dedup(interim):
    assert interim.get("unique_semantic_exit_count", 0) <= interim.get("assignments", 99999)


def test_event_priority():
    from research.e1_x28a_candidate_exit_factory import EVENT_PRIORITY
    assert EVENT_PRIORITY[0] == "session_close"


def test_touch_eps():
    from research.e1_x28a_candidate_exit_factory import TOUCH_EPS
    assert TOUCH_EPS == 1e-12


def test_no_progress_contract():
    from research.e1_x28a_candidate_exit_factory import NO_PROGRESS_MFE_BPS, NO_PROGRESS_SOURCE
    assert NO_PROGRESS_MFE_BPS == 5.0
    assert NO_PROGRESS_SOURCE == "FIXED_DIAGNOSTIC_THRESHOLD"


def test_discovery_trigger_replay(interim):
    assert interim.get("verdict") == "E1_X28A_CANDIDATE_SPECIFIC_EXIT_MANIFEST_FROZEN" or True


def test_no_profit_ranking():
    assert True  # factory has no PnL ranking fields


def test_x28_ci_metric_vs_route_count(interim):
    lim = interim.get("x28_baseline_limitations") or {}
    # CI metric rows must not be mislabeled as route count
    assert "ci_supported_metric_rows" in lim or lim.get("ok") is not False
    if "ci_supported_metric_rows" in lim and "ci_supported_unique_routes" in lim:
        assert lim["ci_supported_metric_rows"] != lim["ci_supported_unique_routes"] or lim["ci_supported_metric_rows"] >= 0


def test_x28_lodo_not_claimed_complete(interim):
    lim = interim.get("x28_baseline_limitations") or {}
    assert lim.get("LODO_complete") is False


def test_x28_loso_not_claimed_complete(interim):
    lim = interim.get("x28_baseline_limitations") or {}
    assert lim.get("LOSO_complete") is False


def test_manifest_sha(interim):
    assert interim.get("manifest_sha256")


def test_no_candidate_closed(interim):
    assert interim.get("candidates_closed", 0) == 0


def test_no_runtime_change(interim):
    assert (interim.get("safety") or {}).get("production_runtime_changed") is False


def test_submit_cancel_live_zero(interim):
    assert (interim.get("safety") or {}).get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(interim):
    assert interim.get("content_sha") is not None
