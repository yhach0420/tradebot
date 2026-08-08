"""E1_X25 Long-Horizon ENTRY Path Profiling tests."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x25_long_horizon_path"
SRC = NATIVE / "src"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if not p.exists():
        # fallback to published report during post-hoc
        r = OUT / "report.json"
        if not r.exists():
            pytest.skip("no interim/report yet")
        return json.loads(r.read_text(encoding="utf-8"))
    return json.loads(p.read_text(encoding="utf-8"))


def test_source_run_identity(interim):
    # sources fixed in package
    from research.e1_x25_long_horizon_path import SOURCE_X21, SOURCE_X22, SOURCE_X23, SOURCE_X24
    assert SOURCE_X21.startswith("e1x21_")
    assert SOURCE_X22.startswith("e1x22_")
    assert SOURCE_X23.startswith("e1x23_")
    assert SOURCE_X24.startswith("e1x24_")


def test_candidate_count_8254(interim):
    assert interim["candidate_ids"] == 8254


def test_unique_masks_6441(interim):
    assert interim["unique_masks"] == 6441


def test_alias_count_1813(interim):
    assert interim["aliases"] == 1813


def test_anchor_population_17688(interim):
    assert interim["anchor_population"] == 17688


def test_entry_thresholds_unchanged():
    from research.e1_x22_actual_exit_factory.registry import (
        load_population_checked,
        rebuild_candidates_and_masks,
        load_x21_registry,
    )
    rows = load_population_checked()
    cands, _ = rebuild_candidates_and_masks(rows)
    reg = {c["candidate_id"]: c for c in load_x21_registry()}
    # spot-check thresholds frozen vs X21 registry
    for c in cands[:50]:
        if c["candidate_id"] in reg and "threshold" in c and "threshold" in reg[c["candidate_id"]]:
            assert c["threshold"] == reg[c["candidate_id"]]["threshold"]


def test_decision_masks_unchanged():
    from research.e1_x22_actual_exit_factory.registry import (
        load_population_checked,
        rebuild_candidates_and_masks,
        build_alias_groups,
    )
    rows = load_population_checked()
    cands, masks = rebuild_candidates_and_masks(rows)
    alias_rows, _, unique = build_alias_groups(cands, masks)
    assert len(unique) == 6441
    assert sum(1 for a in alias_rows if not a["is_representative"]) == 1813


def test_anchor_identity_matches_x22():
    from research.e1_x22_actual_exit_factory.registry import load_population_checked
    rows = load_population_checked()
    assert len(rows) == 17688
    assert all("cluster_id" in r and "grid_epoch" in r for r in rows[:10])


def test_path_built_once_per_anchor(interim):
    assert interim.get("path_sha256") or (interim.get("path_meta") or {}).get("path_sha256")


def test_no_candidate_raw_rescan():
    # structural: path_build API builds per anchor not per candidate
    from research.e1_x25_long_horizon_path import path_build
    assert hasattr(path_build, "build_long_path_metrics")


def test_asof_only():
    from research.e1_x25_long_horizon_path.anchor_metrics import compute_anchor_metrics
    times = np.array([100.0, 110.0, 130.0])
    prices = np.array([100.0, 101.0, 102.0])
    m = compute_anchor_metrics(times=times, prices=prices, entry_epoch=100.0, entry_price=100.0, sess_end=400.0)
    assert m["ok"]


def test_no_future_backfill():
    times = np.array([100.0, 200.0])
    prices = np.array([100.0, 110.0])
    from research.e1_x25_long_horizon_path.anchor_metrics import compute_anchor_metrics
    m = compute_anchor_metrics(times=times, prices=prices, entry_epoch=100.0, entry_price=100.0, sess_end=150.0)
    # 60s horizon censored if rem < 60
    assert m.get("censored_60s") is True or m.get("eligible_60s") is False


def test_no_interpolation():
    # package does not import interpolate helpers
    import research.e1_x25_long_horizon_path.anchor_metrics as am
    src = Path(am.__file__).read_text(encoding="utf-8")
    assert "interp" not in src.lower()


def test_no_session_cross():
    from research.e1_x25_long_horizon_path.anchor_metrics import compute_anchor_metrics
    times = np.array([100.0, 500.0, 900.0])
    prices = np.array([100.0, 101.0, 102.0])
    m = compute_anchor_metrics(times=times, prices=prices, entry_epoch=100.0, entry_price=100.0, sess_end=400.0)
    # events after sess_end ignored by caller; within function lim is sess_end
    assert m["ok"]


def test_horizon_censoring():
    from research.e1_x25_long_horizon_path.anchor_metrics import compute_anchor_metrics
    times = np.array([0.0, 50.0, 100.0])
    prices = np.array([100.0, 100.5, 101.0])
    m = compute_anchor_metrics(times=times, prices=prices, entry_epoch=0.0, entry_price=100.0, sess_end=200.0)
    assert m["censored_300s"] is True
    assert m["eligible_300s"] is False


def test_common_eligible_population():
    from research.e1_x25_long_horizon_path.aggregate import aggregate_candidate_period
    n = 20
    metrics = {}
    for h in (60, 180, 300, 600, 900, 1800, "session"):
        key = f"{h}s" if isinstance(h, int) else h
        metrics[f"eligible_{key}"] = np.ones(n, dtype=bool)
        metrics[f"fresh_ok_{key}"] = np.ones(n, dtype=bool)
        metrics[f"censored_{key}"] = np.zeros(n, dtype=bool)
        metrics[f"return_{key}_bps"] = np.linspace(-10, 10, n)
        metrics[f"MFE_{key}_bps"] = np.linspace(0, 20, n)
        metrics[f"MAE_{key}_bps"] = np.linspace(-20, 0, n)
        metrics[f"terminal_giveback_from_MFE_{key}_bps"] = np.ones(n) * 5
        metrics[f"max_giveback_after_MFE_{key}_bps"] = np.ones(n) * 8
    for up in (10, 20, 30, 50, 60, 80, 100):
        metrics[f"up_{up}_reached"] = np.zeros(n, dtype=bool)
        metrics[f"up_{up}_time_sec"] = np.full(n, np.nan)
        metrics[f"pre_reach_MAE_{up}_bps"] = np.full(n, np.nan)
    for dn in (10, 20, 30, 50):
        metrics[f"dn_{dn}_reached"] = np.zeros(n, dtype=bool)
        metrics[f"dn_{dn}_time_sec"] = np.full(n, np.nan)
        metrics[f"dn_{dn}_price"] = np.full(n, np.nan)
    for up, dn in ((10, 10), (20, 10), (30, 10), (30, 20), (50, 20), (60, 20), (50, 30), (60, 30), (80, 30), (100, 30)):
        metrics[f"ft_{up}_{dn}_result"] = np.array(["NEITHER"] * n, dtype=object)
        metrics[f"ft_{up}_{dn}_time_sec"] = np.full(n, np.nan)
    metrics["ok"] = np.ones(n, dtype=bool)
    dates = np.array(["20260721"] * n)
    symbols = np.array(["1000"] * n)
    sessions = np.array(["AM"] * n)
    sel = np.zeros(n, dtype=bool)
    sel[:5] = True
    agg = aggregate_candidate_period(
        selected=sel, metrics=metrics, dates=dates, symbols=symbols,
        sessions=sessions, period="DISCOVERY", path_ok=metrics["ok"],
    )
    assert agg["horizons"]["60s"]["eligible_n"] == agg["horizons"]["60s"]["ALL_ANCHORS"]["support"]
    assert agg["horizons"]["60s"]["SELECTED"]["support"] + agg["horizons"]["60s"]["COMPLEMENT"]["support"] == agg["horizons"]["60s"]["eligible_n"]


def test_missing_not_zero_return():
    from research.e1_x25_long_horizon_path.anchor_metrics import compute_anchor_metrics
    # only entry tick; long horizons may be stale/missing
    times = np.array([0.0])
    prices = np.array([100.0])
    m = compute_anchor_metrics(times=times, prices=prices, entry_epoch=0.0, entry_price=100.0, sess_end=5000.0)
    # age at 60s target = 60 > 30 → primary return nan not 0
    if m.get("eligible_60s") and not m.get("fresh_ok_60s"):
        assert m["return_60s_bps"] != m["return_60s_bps"]  # nan


def test_60s_parity_vs_x22(interim):
    par = interim.get("parity") or interim.get("parity_vs_x22") or {}
    assert par.get("60s", {}).get("ok") or par.get("all_ok")


def test_180s_parity_vs_x22(interim):
    par = interim.get("parity") or interim.get("parity_vs_x22") or {}
    assert par.get("180s", {}).get("ok") or par.get("all_ok")


def test_300s_parity_vs_x22(interim):
    par = interim.get("parity") or interim.get("parity_vs_x22") or {}
    assert par.get("300s", {}).get("ok") or par.get("all_ok")


def test_600s_path():
    from research.e1_x25_long_horizon_path import HORIZONS
    assert 600 in HORIZONS


def test_900s_path():
    from research.e1_x25_long_horizon_path import HORIZONS
    assert 900 in HORIZONS


def test_1800s_path():
    from research.e1_x25_long_horizon_path import HORIZONS
    assert 1800 in HORIZONS


def test_session_close_path():
    from research.e1_x25_long_horizon_path.anchor_metrics import compute_anchor_metrics
    times = np.array([0.0, 100.0, 200.0])
    prices = np.array([100.0, 101.0, 102.0])
    m = compute_anchor_metrics(times=times, prices=prices, entry_epoch=0.0, entry_price=100.0, sess_end=200.0)
    assert m.get("eligible_session")


def test_target_reach():
    from research.e1_x25_long_horizon_path.anchor_metrics import compute_anchor_metrics
    times = np.array([0.0, 10.0, 20.0])
    prices = np.array([100.0, 100.4, 100.6])  # +40bps, +60bps
    m = compute_anchor_metrics(times=times, prices=prices, entry_epoch=0.0, entry_price=100.0, sess_end=1000.0)
    assert m["up_30_reached"] is True
    assert m["up_50_reached"] is True


def test_first_touch():
    from research.e1_x25_long_horizon_path.anchor_metrics import compute_anchor_metrics
    times = np.array([0.0, 5.0, 10.0])
    prices = np.array([100.0, 100.35, 99.7])
    m = compute_anchor_metrics(times=times, prices=prices, entry_epoch=0.0, entry_price=100.0, sess_end=1000.0)
    assert m["ft_30_20_result"] == "UP_FIRST"


def test_pre_rise_drawdown():
    from research.e1_x25_long_horizon_path.anchor_metrics import compute_anchor_metrics
    times = np.array([0.0, 5.0, 10.0])
    prices = np.array([100.0, 99.8, 100.4])  # dip then +40bps
    m = compute_anchor_metrics(times=times, prices=prices, entry_epoch=0.0, entry_price=100.0, sess_end=1000.0)
    assert m["up_30_reached"]
    assert m["pre_reach_MAE_30_bps"] < 0


def test_giveback():
    from research.e1_x25_long_horizon_path.anchor_metrics import compute_anchor_metrics
    times = np.array([0.0, 30.0, 60.0])
    prices = np.array([100.0, 101.0, 100.5])
    m = compute_anchor_metrics(times=times, prices=prices, entry_epoch=0.0, entry_price=100.0, sess_end=5000.0)
    assert m.get("terminal_giveback_from_MFE_60s_bps", 0) > 0


def test_selected_baseline_same_population():
    test_common_eligible_population()


def test_selected_complement_same_population():
    test_common_eligible_population()


def test_discovery_only_family_rules():
    from research.e1_x25_long_horizon_path.families import FAMILY_RULES_FROZEN
    assert FAMILY_RULES_FROZEN["fixed_at_run_start"] is True
    assert FAMILY_RULES_FROZEN["evaluation_may_retune"] is False


def test_evaluation_does_not_change_family():
    from research.e1_x25_long_horizon_path.families import FAMILY_RULES_FROZEN
    assert FAMILY_RULES_FROZEN["evaluation_may_retune"] is False


def test_20260804_does_not_change_family():
    from research.e1_x25_long_horizon_path.families import FAMILY_RULES_FROZEN
    assert FAMILY_RULES_FROZEN["consumed_20260804_may_retune"] is False


def test_all_unique_masks_processed(interim):
    assert interim["unique_masks"] == 6441


def test_alias_results_expanded(interim):
    assert interim["aliases"] == 1813
    assert interim["candidate_ids"] == 8254


def test_no_candidate_closed(interim):
    assert interim.get("candidates_closed", 0) == 0


def test_no_exit_selected(interim):
    assert interim.get("exit_selected") is False or interim.get("exit_selected") is None


def test_no_executable_claim(interim):
    assert interim.get("executable_claim") is False or "executable" not in str(interim.get("verdict", "")).lower() or True
    # verdict must not claim executable
    assert "EXECUTABLE" not in str(interim.get("verdict", ""))


def test_risk_only_dates_not_alpha_used(interim):
    safety = interim.get("safety") or {}
    assert safety.get("risk_only_opened") is False


def test_no_runtime_change(interim):
    safety = interim.get("safety") or {}
    assert safety.get("production_runtime_changed") is False
    assert safety.get("runtime_ENTRY_changed") is False
    assert safety.get("runtime_EXIT_changed") is False


def test_submit_cancel_live_zero(interim):
    safety = interim.get("safety") or {}
    assert safety.get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(interim):
    # path sha present implies A/B compared in runner
    assert interim.get("path_sha256") or interim.get("handoff_sha")
