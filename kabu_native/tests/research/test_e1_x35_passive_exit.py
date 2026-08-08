"""E1_X35 PASSIVE fill EXIT architecture tests."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from research.e1_x35_passive_exit import (
    ENTRY_SHA,
    EXPECTED_FILLS,
    FORBIDDEN_FROM,
    PRIORITY,
)
from research.e1_x35_passive_exit.exits import build_catalog, simulate_exit
from research.e1_x35_passive_exit.paths import path_metrics

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x35_passive_exit"
X34C = NATIVE / "results" / "research" / "e1_x34c_passive_deployability"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "report.json"
    if r.exists():
        return json.loads(r.read_text(encoding="utf-8"))
    pytest.skip("no interim/report yet")


@pytest.fixture(scope="module")
def report():
    r = OUT / "report.json"
    if r.exists():
        return json.loads(r.read_text(encoding="utf-8"))
    pytest.skip("no report")


def _toy_path(*, offs, rets):
    return {
        "ok": True,
        "offs": np.asarray(offs, dtype=float),
        "rets": np.asarray(rets, dtype=float),
        "mids": np.asarray(rets, dtype=float),
        "times": np.asarray([1e9 + o for o in offs], dtype=float),
        "sess_end": 1e9 + 10000,
        "entry_t": 1e9,
        "entry_price": 1000.0,
    }


def test_entry_sha_identity(interim):
    assert interim.get("entry_sha") == ENTRY_SHA
    body = json.loads((X34C / "PASSIVE_FILL_ENTRY_V1.json").read_text(encoding="utf-8"))
    assert body.get("sha256") == ENTRY_SHA


def test_fill_time_origin(interim):
    assert interim.get("entry_origin_fill_time") is True


def test_actual_bid_exit(interim):
    assert interim.get("executable_bid_exit") is True


def test_no_future_price_use():
    path = _toy_path(offs=[0, 10, 20], rets=[0.0, 5.0, -5.0])
    m = path_metrics(path)
    assert m["ok"]
    assert m["exec_10"] == 5.0
    # mark at 30 uses last available <=30 (20s)
    assert m["exec_30"] == -5.0


def test_path_metrics():
    path = _toy_path(offs=[0, 5, 15, 40], rets=[0.0, -10.0, 25.0, 5.0])
    m = path_metrics(path)
    assert m["mfe"] == 25.0
    assert m["mae"] == -10.0
    assert m["time_to_mfe"] == 15.0
    assert m["max_giveback"] == 20.0


def test_nested_outer_blind(interim):
    assert interim.get("selected_per_fold") is not None
    assert set(interim["selected_per_fold"].keys()) == {"A", "B", "C", "D"}


def test_inner_lodo(interim):
    assert "lodo" in interim


def test_threshold_train_only(interim):
    # catalog built per outer train inside CV; freeze notes train-only
    assert interim.get("no_allocator_tuning") is True


def test_fixed_horizon_controls(interim):
    fc = interim.get("fixed_controls_summary") or {}
    for h in ("E0_FIXED_180", "E0_FIXED_300", "E0_FIXED_600", "E0_FIXED_900"):
        assert h in fc


def test_hard_stop_priority():
    path = _toy_path(offs=[0, 1, 2], rets=[0.0, -25.0, 50.0])
    r = simulate_exit(path, hard_stop_bps=20.0, profit_target_bps=40.0, fixed_hold_sec=10.0)
    assert r["reason"] == "HARD_STOP"
    assert r["exit_ret_bps"] == -25.0


def test_profit_target():
    path = _toy_path(offs=[0, 1, 2], rets=[0.0, 15.0, 40.0])
    r = simulate_exit(path, profit_target_bps=30.0, max_hold_sec=900.0)
    assert r["reason"] == "PROFIT_TARGET"
    assert r["exit_ret_bps"] == 40.0


def test_trailing():
    path = _toy_path(offs=[0, 1, 2, 3], rets=[0.0, 30.0, 40.0, 20.0])
    r = simulate_exit(
        path,
        trail_activate_bps=25.0,
        trail_giveback_frac=0.5,
        max_hold_sec=900.0,
    )
    assert r["reason"] == "ACTIVATED_TRAILING"
    assert r["exit_ret_bps"] == 20.0


def test_no_progress():
    path = _toy_path(offs=[0, 50, 120], rets=[0.0, 2.0, 3.0])
    r = simulate_exit(path, no_progress_sec=100.0, no_progress_min_mfe=10.0, max_hold_sec=900.0)
    assert r["reason"] == "NO_PROGRESS"


def test_hybrid_priority():
    assert PRIORITY[0] == "HARD_STOP"
    path = _toy_path(offs=[0, 1], rets=[0.0, -30.0])
    r = simulate_exit(
        path,
        hard_stop_bps=20.0,
        trail_activate_bps=15.0,
        trail_giveback_frac=0.5,
        no_progress_sec=60.0,
        max_hold_sec=900.0,
    )
    assert r["reason"] == "HARD_STOP"


def test_session_close():
    path = _toy_path(offs=[0, 10, 20], rets=[0.0, 1.0, 2.0])
    r = simulate_exit(path)  # no conditions
    assert r["reason"] == "SESSION_CLOSE"
    assert r["exit_ret_bps"] == 2.0


def test_hold_duration(interim):
    cf = interim.get("cross_fitted") or {}
    hs = cf.get("hold_sec") or {}
    for k in ("mean", "median", "p90", "p95"):
        assert k in hs


def test_no_allocator_tuning(interim):
    assert interim.get("no_allocator_tuning") is True


def test_no_runtime_change(interim):
    assert interim.get("no_runtime_change") is True


def test_no_short(interim):
    assert interim.get("no_short") is True


def test_20260810_unopened(interim):
    assert interim.get("opened_20260810") is False
    assert FORBIDDEN_FROM == "20260810"


def test_submit_cancel_live(interim):
    assert interim.get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(interim):
    ab = interim.get("ab_determinism") or {}
    assert ab.get("ok") is True


def test_n_fills(interim):
    assert interim.get("n_fills") == EXPECTED_FILLS


def test_catalog_has_families():
    toy = []
    rng = np.random.default_rng(0)
    for i in range(40):
        toy.append({
            "metrics": {
                "ok": True,
                "mfe": float(rng.uniform(5, 80)),
                "mae": float(rng.uniform(-60, -5)),
                "time_to_mfe": float(rng.uniform(30, 500)),
            }
        })
    cat = build_catalog(toy)
    fams = {c["family"] for c in cat}
    assert "E0_FIXED" in fams
    assert "E1_HARD_STOP" in fams
    assert "E2_PROFIT_TARGET" in fams
    assert "E3_ACTIVATED_TRAILING" in fams
    assert "E4_NO_PROGRESS" in fams
    assert "E5_HYBRID" in fams


def test_artifacts_exist(report):
    assert (OUT / "report.json").exists()
    assert (OUT / "report.md").exists()
    assert (OUT / "audit.xlsx").exists()
    if report.get("manifest_created"):
        assert (OUT / "PASSIVE_FILL_EXIT_V1.json").exists()
        assert report.get("verdict") == "E1_X35_PASSIVE_FILL_EXIT_SUPPORTED"
    else:
        assert report.get("verdict") in (
            "E1_X35_FIXED_HORIZON_EXIT_REMAINS_BASELINE",
            "E1_X35_NO_ROBUST_EXIT_ARCHITECTURE",
        )
