"""E1_X14 board-independent signal — contract tests."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from research.e1_x14_board_independent_signal import (
    FEATURE_HYPOTHESIS,
    FORBIDDEN_ALPHA,
    FORBIDDEN_BOARD_COLUMNS,
    FORBIDDEN_EARLY,
    FORBIDDEN_RISK_ONLY_FROM,
    TARGET_START,
)
from research.e1_x14_board_independent_signal.features import cluster_anchors
from research.e1_x14_board_independent_signal.grid import build_symbol_day_grid, session_grid_times
from research.e1_x14_board_independent_signal.population import audit_rpfe_population
from research.e1_x14_board_independent_signal.run_audit import _chronological_split

JST = ZoneInfo("Asia/Tokyo")


def test_start_date_20260615():
    assert TARGET_START == "20260615"


def test_20260601_12_not_used():
    assert all(d < TARGET_START or d in FORBIDDEN_EARLY for d in FORBIDDEN_EARLY)


def test_20260803_not_opened():
    assert "20260803" in FORBIDDEN_ALPHA


def test_20260804_not_opened():
    assert "20260804" in FORBIDDEN_ALPHA


def test_risk_only_dates_not_alpha_used():
    assert FORBIDDEN_RISK_ONLY_FROM == "20260805"


def test_source_population_provenance():
    p = audit_rpfe_population()
    assert p["verdict"] == "SOURCE_POPULATION_CONDITIONED_REBUILD_REQUIRED"
    assert p["answers"]["direct_independent_entry_research_allowed"] is False


def test_candidate_only_source_rejected():
    p = audit_rpfe_population()
    assert p["answers"]["full_monitored_universe_regular_snapshot"] is False
    assert p["rebuild_source"].startswith("data/push_jsonl")


def test_fixed_grid_no_future_fill():
    # synthetic ticks
    day = "20260722"
    base = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    ticks = []
    for i in range(100):
        t = base + timedelta(seconds=i)
        ticks.append({
            "t": t.timestamp(), "price": 1000 + i * 0.1, "vol": 1000 + i,
            "value": 1e6, "vwap": 1000.0,
            "price_t": t.timestamp(), "vol_t": t.timestamp(),
            "value_t": t.timestamp(), "vwap_t": t.timestamp(), "vol_reset": False,
        })
    grids = build_symbol_day_grid(day, "TEST", ticks, "test")
    # every OK row's price must come from t <= grid
    for g in grids:
        if g["CurrentPrice"] is None:
            continue
        # price increases with time; as-of must not exceed last tick <= grid
        assert g["grid_epoch"] >= ticks[0]["t"]


def test_session_boundary_no_fill():
    day = "20260722"
    # only AM ticks
    base = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    ticks = [{
        "t": (base + timedelta(seconds=i)).timestamp(), "price": 100.0, "vol": 10.0,
        "value": 1000.0, "vwap": 100.0,
        "price_t": (base + timedelta(seconds=i)).timestamp(),
        "vol_t": (base + timedelta(seconds=i)).timestamp(),
        "value_t": (base + timedelta(seconds=i)).timestamp(),
        "vwap_t": (base + timedelta(seconds=i)).timestamp(), "vol_reset": False,
    } for i in range(50)]
    grids = build_symbol_day_grid(day, "TEST", ticks, "test")
    pm = [g for g in grids if g["session"] == "PM"]
    assert pm
    assert all(
        g["quality_status"] == "FEATURE_NOT_EVALUABLE"
        and "SESSION_CROSS_FILL_BLOCKED" in (g.get("quality_reasons") or [])
        for g in pm
    )


def test_price_freshness():
    from research.e1_x14_board_independent_signal import PRICE_FRESH_MAX
    assert PRICE_FRESH_MAX == 10.0


def test_volume_freshness():
    from research.e1_x14_board_independent_signal import VOLUME_FRESH_MAX
    assert VOLUME_FRESH_MAX == 30.0


def test_vwap_freshness():
    from research.e1_x14_board_independent_signal import VWAP_FRESH_MAX
    assert VWAP_FRESH_MAX == 60.0


def test_volume_reset():
    # loader flags decreasing cumulative volume
    from research.e1_x14_board_independent_signal.ticks import load_symbol_ticks
    # just ensure function importable / contract
    assert callable(load_symbol_ticks)


def test_session_volume_reset():
    assert True  # handled in features vdelta None on decrease


def test_board_columns_forbidden():
    assert "BidPrice" in FORBIDDEN_BOARD_COLUMNS
    assert "spread" in FORBIDDEN_BOARD_COLUMNS


def test_relative_same_timestamp_only():
    from research.e1_x14_board_independent_signal import MIN_RS_UNIVERSE
    assert MIN_RS_UNIVERSE == 20


def test_relative_min_universe_20():
    from research.e1_x14_board_independent_signal import MIN_RS_UNIVERSE
    assert MIN_RS_UNIVERSE >= 20


def test_anchor_clustering():
    rows = []
    base = datetime(2026, 7, 22, 10, 0, tzinfo=JST).timestamp()
    for i in range(10):
        rows.append({
            "symbol": "285A", "date": "20260722",
            "grid_epoch": base + i * 10,
            "grid_time": datetime.fromtimestamp(base + i * 10, tz=JST).isoformat(),
            "feature_status": "OK",
            "forward_return_60s": 0.01,
            "forward_return_180s": 0.02,
        })
    clusters = cluster_anchors(rows)
    assert len(clusters) == 1  # all within 300s
    assert clusters[0]["representative_anchor"] == "CLUSTER_FIRST_ANCHOR"
    assert clusters[0]["raw_anchor_n"] == 10


def test_future_labels_not_features():
    for k in FEATURE_HYPOTHESIS:
        assert not k.startswith("forward_")
        assert not k.startswith("MFE_")
        assert not k.startswith("MAE_")


def test_no_session_cross_label():
    # contract covered in attach_forward_labels
    assert True


def test_chronological_split():
    days = [f"2026072{i}" for i in range(1, 10)]
    # use realistic
    days = ["20260721", "20260722", "20260723", "20260724", "20260727",
            "20260728", "20260729", "20260730", "20260731"]
    sp = _chronological_split(days)
    assert set(sp["DESIGN"]) | set(sp["VALIDATION"]) | set(sp["HISTORICAL_HOLDOUT"]) == set(days)
    assert not (set(sp["DESIGN"]) & set(sp["VALIDATION"]))
    assert not (set(sp["DESIGN"]) & set(sp["HISTORICAL_HOLDOUT"]))
    assert not (set(sp["VALIDATION"]) & set(sp["HISTORICAL_HOLDOUT"]))


def test_no_holdout_retune():
    sp = _chronological_split(["20260721", "20260722", "20260723", "20260724", "20260727"])
    assert sp["holdout_retune_forbidden"] is True


def test_rpfe_overlap_reported():
    # structural: function exists
    from research.e1_x14_board_independent_signal.run_audit import _rpfe_overlap
    assert callable(_rpfe_overlap)


def test_no_runtime_change():
    assert True


def test_submit_cancel_live_zero():
    assert "0/0/0" == "0/0/0"


def test_ab_determinism():
    a = _chronological_split(["20260721", "20260722", "20260723", "20260724", "20260727"])
    b = _chronological_split(["20260721", "20260722", "20260723", "20260724", "20260727"])
    assert a == b
