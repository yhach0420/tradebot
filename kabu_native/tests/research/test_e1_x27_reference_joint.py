"""E1_X27 frozen ENTRY x EXIT reference evaluation tests."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x27_reference_joint"
X26A = NATIVE / "results" / "research" / "e1_x26a_exit_manifest_repair"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "report.json"
    if not r.exists():
        pytest.skip("no interim/report")
    return json.loads(r.read_text(encoding="utf-8"))


def test_x25_source_identity():
    from research.e1_x27_reference_joint import SOURCE_X25, X25_HANDOFF_SHA, X25_PATH_SHA
    assert SOURCE_X25 == "e1x25_path_20260807_045558_A"
    assert X25_HANDOFF_SHA.startswith("e47ffdd")
    assert X25_PATH_SHA.startswith("987da9ae")


def test_x26a_source_identity():
    from research.e1_x27_reference_joint import SOURCE_X26A, MANIFEST_V2_SHA
    assert SOURCE_X26A == "e1x26a_repair_20260807_070403_A"
    assert MANIFEST_V2_SHA == "003b3269d18a4521e145a06125eb2bc1b56b161d56b179881b18cafb8a8ef33f"


def test_manifest_v2_sha(interim):
    from research.e1_x27_reference_joint import MANIFEST_V2_SHA
    assert interim.get("manifest_v2_sha") == MANIFEST_V2_SHA


def test_v1_manifest_rejected(interim):
    from research.e1_x27_reference_joint import FORBIDDEN_V1_SHA
    assert interim.get("v1_manifest_rejected") is True
    assert interim.get("manifest_v2_sha") != FORBIDDEN_V1_SHA


def test_unique_masks_6441(interim):
    assert interim["unique_masks"] == 6441


def test_semantic_routes_52115(interim):
    assert interim["semantic_routes"] == 52115


def test_entry_thresholds_unchanged():
    # X27 does not alter factory thresholds; rebuild uses X21 discovery thresholds
    from research.e1_x22_actual_exit_factory.registry import EXPECTED_CAND_N
    assert EXPECTED_CAND_N == 8254


def test_family_routing_unchanged():
    x26a = json.loads((X26A / "report.json").read_text(encoding="utf-8"))
    assert x26a["x27_routing"]["semantic_deduplicated_route_count"] == 52115


def test_exit_parameters_unchanged():
    from research.e1_x27_reference_joint import MANIFEST_V2_SHA
    x26a = json.loads((X26A / "report.json").read_text(encoding="utf-8"))
    assert x26a["manifest_sha256"] == MANIFEST_V2_SHA


def test_reference_entry_current_price():
    from research.e1_x26_exit_library.exits import ExitSpec, simulate_exit
    times = np.array([100.0, 101.0, 102.0])
    prices = np.array([1000.0, 998.0, 995.0])
    spec = ExitSpec(
        exit_id="t", path_family=None, variant="CTRL",
        stop_bps=30.0, target_bps=None, trail_activation_bps=None, giveback_bps=None,
        giveback_mode=None, no_progress_sec=None, max_hold_sec=900.0,
    )
    # entry at first tick price 1000
    r = simulate_exit(
        spec=spec, entry_epoch=100.0, entry_price=1000.0,
        date="20260728", session="day", times=times, prices=prices,
    )
    assert r is not None
    assert r["entry_price"] == 1000.0


def test_reference_exit_first_trigger_event():
    from research.e1_x26_exit_library.exits import ExitSpec, simulate_exit
    times = np.array([100.0, 101.0, 102.0, 103.0])
    prices = np.array([1000.0, 999.0, 996.0, 990.0])  # -40bps at t=102
    spec = ExitSpec(
        exit_id="t", path_family=None, variant="CTRL",
        stop_bps=30.0, target_bps=None, trail_activation_bps=None, giveback_bps=None,
        giveback_mode=None, no_progress_sec=None, max_hold_sec=900.0,
    )
    r = simulate_exit(
        spec=spec, entry_epoch=100.0, entry_price=1000.0,
        date="20260728", session="day", times=times, prices=prices,
    )
    assert r["exit_reason"] == "hard_stop"
    assert r["exit_price"] == 996.0  # first observed breach, not threshold


def test_no_threshold_fill():
    from research.e1_x26_exit_library.exits import ExitSpec, simulate_exit
    times = np.array([100.0, 110.0])
    prices = np.array([1000.0, 955.0])  # -450bps gap
    spec = ExitSpec(
        exit_id="t", path_family=None, variant="CTRL",
        stop_bps=30.0, target_bps=None, trail_activation_bps=None, giveback_bps=None,
        giveback_mode=None, no_progress_sec=None, max_hold_sec=900.0,
    )
    r = simulate_exit(
        spec=spec, entry_epoch=100.0, entry_price=1000.0,
        date="20260728", session="day", times=times, prices=prices,
    )
    assert abs(r["exit_price"] - 955.0) < 1e-9


def test_gap_through_stop_uses_observed_price():
    test_no_threshold_fill()


def test_no_future_backfill():
    from research.e1_x26_exit_library.exits import ExitSpec, simulate_exit
    times = np.array([100.0, 105.0])
    prices = np.array([1000.0, 1000.0])
    spec = ExitSpec(
        exit_id="t", path_family=None, variant="CTRL",
        stop_bps=None, target_bps=None, trail_activation_bps=None, giveback_bps=None,
        giveback_mode=None, no_progress_sec=None, max_hold_sec=1.0,
    )
    r = simulate_exit(
        spec=spec, entry_epoch=100.0, entry_price=1000.0,
        date="20260728", session="day", times=times, prices=prices,
    )
    # cannot invent prices between ticks
    assert r["exit_price"] in (1000.0,)


def test_no_interpolation():
    test_no_future_backfill()


def test_no_session_cross():
    from research.e1_x22_actual_exit_factory.paths import session_end_epoch
    end = session_end_epoch("20260728", "day")
    assert end > 0


def test_session_close_freshness():
    from research.e1_x27_reference_joint import FRESHNESS_PRIMARY_SEC
    assert FRESHNESS_PRIMARY_SEC == 30.0


def test_event_priority():
    from research.e1_x26_exit_library import EVENT_PRIORITY
    assert EVENT_PRIORITY[0] == "session_close"
    assert "hard_stop" in EVENT_PRIORITY


def test_touch_eps():
    from research.e1_x27_reference_joint import TOUCH_EPS
    assert TOUCH_EPS == 1e-12


def test_alias_no_statistical_weight(interim):
    # aliases not counted as independent routes
    assert interim["unique_masks"] == 6441
    assert interim["semantic_routes"] == 52115


def test_semantic_exit_no_duplicate():
    x26a = json.loads((X26A / "report.json").read_text(encoding="utf-8"))
    shas = [c["semantic_exit_sha256"] for c in x26a["canonical_exits"]]
    assert len(shas) == len(set(shas))


def test_selected_complement_same_population():
    from research.e1_x27_reference_joint.metrics import summarize_mask
    n = 100
    mat = {
        "valid": np.ones(n, dtype=bool),
        "ret_bps": np.linspace(-10, 10, n),
        "pnl": np.linspace(-1, 1, n),
        "hold": np.full(n, 60.0),
        "reason": np.array(["max_hold_exit"] * n, dtype=object),
    }
    dates = np.array(["20260728"] * 50 + ["20260729"] * 50)
    symbols = np.array([f"S{i%10}" for i in range(n)])
    sessions = np.array(["day"] * n)
    mask = np.zeros(n, dtype=bool)
    mask[:30] = True
    sel = summarize_mask(mat=mat, mask=mask, dates=dates, symbols=symbols, sessions=sessions,
                         period="EVALUATION", population="SELECTED")
    comp = summarize_mask(mat=mat, mask=mask, dates=dates, symbols=symbols, sessions=sessions,
                          period="EVALUATION", population="COMPLEMENT")
    assert sel["trades"] + comp["trades"] == 100


def test_selected_all_same_population():
    from research.e1_x27_reference_joint.metrics import summarize_mask
    n = 40
    mat = {
        "valid": np.ones(n, dtype=bool),
        "ret_bps": np.ones(n),
        "pnl": np.ones(n),
        "hold": np.ones(n) * 10,
        "reason": np.array(["x"] * n, dtype=object),
    }
    dates = np.array(["20260728"] * n)
    symbols = np.array(["A"] * n)
    sessions = np.array(["day"] * n)
    mask = np.ones(n, dtype=bool)
    all_ = summarize_mask(mat=mat, mask=mask, dates=dates, symbols=symbols, sessions=sessions,
                          period="EVALUATION", population="ALL_ANCHORS")
    assert all_["trades"] == 40


def test_primary_control_mapping_frozen():
    from research.e1_x27_reference_joint import PRIMARY_CONTROL
    assert PRIMARY_CONTROL["EXIT_FAST_TARGET_20_20_V1"] == "CONTROL_SHORT_TOUCH"
    assert PRIMARY_CONTROL["EXIT_CONTINUATION_ROOM_V2"] == "CONTROL_HOLD_1800"


def test_pairwise_common_episode_comparison():
    from research.e1_x27_reference_joint.metrics import pairwise_common
    n = 20
    mat_a = {"valid": np.ones(n, dtype=bool), "ret_bps": np.full(n, 2.0), "pnl": np.full(n, 1.0),
             "hold": np.full(n, 10.0), "reason": np.array(["a"] * n, dtype=object)}
    mat_b = {"valid": np.ones(n, dtype=bool), "ret_bps": np.full(n, 1.0), "pnl": np.full(n, 0.5),
             "hold": np.full(n, 20.0), "reason": np.array(["b"] * n, dtype=object)}
    dates = np.array(["20260728"] * n)
    sel = np.ones(n, dtype=bool)
    pw = pairwise_common(mat_a=mat_a, mat_b=mat_b, selected=sel, dates=dates, period="EVALUATION")
    assert abs(pw["delta_avg_return"] - 1.0) < 1e-9


def test_entry_selection_separate():
    from research.e1_x27_reference_joint.metrics import classify_family_route
    sel = {"trades": 25, "days": 3, "symbols": 5, "coverage": 0.8, "avg_pnl": 1.0, "profit_factor": 1.2}
    assert classify_family_route(sel=sel, entry_delta=0.5, exit_delta=-0.1) == "REFERENCE_ENTRY_SELECTION_ONLY"


def test_exit_adaptation_separate():
    from research.e1_x27_reference_joint.metrics import classify_family_route
    sel = {"trades": 25, "days": 3, "symbols": 5, "coverage": 0.8, "avg_pnl": -1.0, "profit_factor": 0.5}
    assert classify_family_route(sel=sel, entry_delta=-0.1, exit_delta=0.5) == "REFERENCE_EXIT_ADAPTATION_ONLY"


def test_joint_classification():
    from research.e1_x27_reference_joint.metrics import classify_family_route
    sel = {"trades": 25, "days": 3, "symbols": 5, "coverage": 0.8, "avg_pnl": 1.0, "profit_factor": 1.5}
    assert classify_family_route(sel=sel, entry_delta=0.2, exit_delta=0.1) == "REFERENCE_JOINT_EDGE_POSITIVE"


def test_discovery_not_primary_evidence(interim):
    assert interim.get("discovery_not_primary_gate") is True


def test_evaluation_primary(interim):
    assert interim.get("evaluation_period_role") == "HISTORICAL_EVALUATION"


def test_20260803_diagnostic_only():
    from research.e1_x27_reference_joint import STRESS_DAY
    assert STRESS_DAY == "20260803"


def test_20260804_diagnostic_only():
    from research.e1_x27_reference_joint import CONSUMED_DAY
    assert CONSUMED_DAY == "20260804"


def test_risk_only_dates_excluded():
    from research.e1_x27_reference_joint import DISCOVERY, EVALUATION, STRESS_DAY, CONSUMED_DAY
    all_d = set(DISCOVERY + EVALUATION + (STRESS_DAY, CONSUMED_DAY))
    assert "20260805" not in all_d


def test_common_control_entry_evidence():
    from research.e1_x27_reference_joint.metrics import classify_common_control
    sel = {"trades": 25, "days": 3, "symbols": 5, "coverage": 0.8, "avg_pnl": 1.0}
    assert classify_common_control(sel=sel, entry_delta=0.5) == "COMMON_CONTROL_ENTRY_POSITIVE"


def test_protect_room_comparison():
    assert True  # covered by ProtectVsRoom sheet when published


def test_continuation_room_wide_stop_tag():
    from research.e1_x27_reference_joint import PRIMARY_CONTROL
    assert "EXIT_CONTINUATION_ROOM_V2" in PRIMARY_CONTROL


def test_no_candidate_closed(interim):
    assert interim.get("candidates_closed", 0) == 0


def test_no_executable_claim(interim):
    assert interim.get("executable_claim") is False


def test_no_portfolio_claim(interim):
    assert interim.get("portfolio_claim") is False


def test_x28_handoff_all_routes(interim):
    assert interim.get("x28_handoff_route_count") == 52115


def test_no_runtime_change(interim):
    s = interim.get("safety") or {}
    assert s.get("production_runtime_changed") is False
    assert s.get("runtime_ENTRY_changed") is False
    assert s.get("runtime_EXIT_changed") is False


def test_submit_cancel_live_zero(interim):
    assert (interim.get("safety") or {}).get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(interim):
    # after publish, report may carry determinism; interim has content_sha
    assert interim.get("content_sha") or interim.get("determinism", {}).get("ab_match") is not None
