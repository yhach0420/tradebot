"""P2-0B synthetic precommit tests. No Capture. No PnL. No Runtime change."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from research.dynamic_anchor_p2_0b import (
    CONFIRMATION_NOT_EVALUABLE,
    GRID_SEC,
    VOLUME_PERCENTILE_MIN,
)
from research.dynamic_anchor_p2_0b.contract import (
    confirmation_window,
    evaluate_confirmation,
    t1_raw,
)
from research.dynamic_anchor_p2_0b.publish import write_artifacts
from research.dynamic_anchor_p2_0b.synthetic import extra_contract_checks, run_suite
from research.e1_x14_board_independent_signal import GRID_SEC as X14_GRID

JST = ZoneInfo("Asia/Tokyo")


def test_x14_grid_cadence_not_tick_driven():
    assert GRID_SEC == 10
    assert GRID_SEC == X14_GRID
    assert VOLUME_PERCENTILE_MIN == 0.6486486486486487


def test_missing_is_false_no_impute():
    assert t1_raw({}) is False
    assert t1_raw({
        "feature_status": "FEATURE_NOT_EVALUABLE",
        "relative_status": "OK",
        "rs_universe_n": 20,
        "volume_percentile_60s": 0.99,
    }) is False


def test_synthetic_A_to_O():
    suite = run_suite()
    failed = [r for r in suite["results"] if not r["ok"]]
    assert suite["failed"] == 0, failed
    assert suite["passed"] == 15


def test_extra_leak_and_snapshot():
    extra = extra_contract_checks()
    bad = [r for r in extra if not r["ok"]]
    assert not bad, bad


def test_p0_stale_not_evaluable():
    t0 = datetime(2026, 8, 3, 10, 0, tzinfo=JST).timestamp()
    t1 = t0 + 600
    events = []
    for i in range(11):
        c = t0 + i * 60
        et = c - 61.0 if i == 0 else c
        events.append({"symbol": "P0", "event_time": et, "CurrentPrice": 100.0 + i})
    r = evaluate_confirmation(symbol="P0", t0=t0, t1=t1, events=events)
    assert r["status"] == CONFIRMATION_NOT_EVALUABLE
    assert r["reason"] == "CHECKPOINT_STALE"


def test_lunch_not_a_window():
    lunch = datetime(2026, 8, 3, 12, 0, tzinfo=JST).timestamp()
    w = confirmation_window("20260803", lunch)
    assert w["status"] == "SESSION_INCOMPLETE"


def test_write_precommit_artifacts():
    paths = write_artifacts()
    assert paths["report_json"].is_file()
    assert paths["report_md"].is_file()
    assert paths["audit_xlsx"].is_file()
    # only those three names in the output dir
    names = {p.name for p in paths["report_json"].parent.iterdir() if p.is_file()}
    assert names == {"report.json", "report.md", "audit.xlsx"}
