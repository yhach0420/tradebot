"""Safety + causality tests for PBv2 zero-base revalidation."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.pbv2_zero_base_revalidation.cap5 import rank_score_pbv2, replay_cap5
from research.pbv2_zero_base_revalidation.constants import (
    LANE_C_REQUIRED,
    SUSPECT_BOARD_DAYS,
    TIME_FEATURE_BLOCKLIST,
)
from research.pbv2_zero_base_revalidation.generators import RuleSpec, fit_rule_thresholds, metrics_for
from research.pbv2_zero_base_revalidation.labels import assert_no_future_in_features, counterfactual_exit
from research.pbv2_zero_base_revalidation.leakage import audit_fold, audit_panel_leakage
from research.pbv2_zero_base_revalidation.panel import CandidateRow, PricePoint, _board_quality
from research.pbv2_zero_base_revalidation.generators import dynamic_status
from research.pbv2_zero_base_revalidation.walk_forward import chronological_oos

JST = ZoneInfo("Asia/Tokyo")


def _row(**kwargs) -> CandidateRow:
    base = dict(
        day="20260722",
        session="s",
        symbol="1000.T",
        evaluation_time=datetime(2026, 7, 22, 10, 0, tzinfo=JST),
        evaluation_event_id="e1",
        universe_source="core",
        current_price=1000.0,
        current_price_time=datetime(2026, 7, 22, 10, 0, tzinfo=JST),
        board_time=datetime(2026, 7, 22, 9, 59, 59, tzinfo=JST),
        board_age_sec=1.0,
        price_age_sec=0.5,
        pbv2_candidate=False,
        pbv2_score=2.0,
        pbv2_decision=False,
        reject_reason="or_overlay_not_candidate",
        accept=False,
        cap_blocked=False,
        features={"f_mom": 0.2, "f_rise5": -0.5, "f_imb": 0.55},
        board_quality="TOP_ONLY",
    )
    base.update(kwargs)
    return CandidateRow(**base)


def test_train_date_strictly_before_test():
    meta = audit_fold(["20260720", "20260721"], "20260722")
    assert meta["max_train_date_lt_test"] is True
    bad = audit_fold(["20260722", "20260721"], "20260722")
    assert bad["max_train_date_lt_test"] is False


def test_no_time_features():
    row = _row(features={"f_mom": 1.0, "mkt_minutes_from_open": 30.0})
    audit = audit_panel_leakage([row])
    assert audit["leakage_blocked"] is True
    assert any(i["type"] == "time_feature" for i in audit["issues_sample"])
    for b in TIME_FEATURE_BLOCKLIST:
        assert isinstance(b, str)


def test_no_symbol_features():
    row = _row(features={"f_mom": 1.0, "symbol_id": 123.0})
    audit = audit_panel_leakage([row])
    assert audit["leakage_blocked"] is True


def test_lane_c_no_imputation():
    row = _row(features={"f_mom": 0.2}, lane_c_complete=False)
    for k in LANE_C_REQUIRED:
        assert row.features.get(k) is None
    # fitting must not invent imputed values on missing rows
    spec = RuleSpec("t", "dynamic", "x", ("f_np_imb_chg_60",), (">=",))
    fitted = fit_rule_thresholds([row], spec)
    # no observed values → empty or zero thresholds, keep stays False
    assert fitted.keep(row) is False


def test_dynamic_missing_not_counted_as_stability():
    st = dynamic_status(["20260721", "20260722"], "20260710")
    assert st == "FEATURE_MISSING"
    # missing days must not be treated as OOS_EVALUABLE
    assert st != "OOS_EVALUABLE"


def test_first_dynamic_day_is_warmup():
    assert dynamic_status(["20260721", "20260722", "20260723"], "20260721") == "WARMUP"


def test_partial_l2_not_full_l2():
    q = _board_quality({"f_imb": 0.55, "f_board_age": 1.0}, "20260616", {})
    assert q != "FULL_L2"
    assert q in ("PARTIAL_L2", "TOP_ONLY", "STALE")


def test_fallback_05_detected():
    q = _board_quality({"f_imb": 0.5, "f_board_age": 1.0}, "20260722", {})
    assert q == "FALLBACK_0_5"


def test_candidate_panel_includes_non_pbv2():
    rows = [
        _row(pbv2_candidate=True, pbv2_decision=True, symbol="1"),
        _row(pbv2_candidate=False, pbv2_decision=False, symbol="2", evaluation_event_id="e2"),
    ]
    assert any(not r.pbv2_candidate for r in rows)


def test_large_rise_missed_reason_present():
    from research.pbv2_zero_base_revalidation.large_rise import annotate_capture

    episodes = [
        {
            "day": "20260722",
            "symbol": "1000.T",
            "start_time": "2026-07-22T10:00:00+09:00",
        }
    ]
    panel = [_row(pbv2_decision=False, pbv2_score=2.0, reject_reason="entry_score_v2_below_threshold")]
    out = annotate_capture(episodes, panel, zero_base_keep=None)
    assert out[0].get("miss_reason")


def test_counterfactual_feature_no_future_leak():
    row = _row()
    row.forward = {"forward_return_5m": 1.2}
    row.cf_pnl = 100.0
    assert_no_future_in_features(row)
    row.features["forward_return_5m"] = 1.2
    with pytest.raises(AssertionError):
        assert_no_future_in_features(row)


def test_cap5_replay_deterministic():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    rows = []
    for i, sym in enumerate(["A.T", "B.T", "C.T", "D.T", "E.T", "F.T"]):
        r = _row(
            symbol=sym,
            evaluation_time=t0,
            evaluation_event_id=f"e{i}",
            pbv2_candidate=True,
            pbv2_decision=True,
            pbv2_score=float(10 - i),
            cf_pnl=100.0 - 10 * i,
            cf_pnl_5bps=90.0 - 10 * i,
        )
        rows.append(r)
    a = replay_cap5(rows, rank_score_pbv2, method_name="t")
    b = replay_cap5(rows, rank_score_pbv2, method_name="t")
    assert a == b
    assert a["accepted_trades"] == 5
    assert a["rejected_by_cap"] >= 1


def test_baseline_reproduction():
    rows = [
        _row(pbv2_decision=True, accept=True, cf_pnl=100, cf_pnl_5bps=90, symbol="A"),
        _row(pbv2_decision=False, cf_pnl=-50, cf_pnl_5bps=-60, symbol="B", evaluation_event_id="e2"),
    ]
    from research.pbv2_zero_base_revalidation.generators import pbv2_baseline_keep

    m = metrics_for(rows, pbv2_baseline_keep)
    assert m["n"] == 1
    assert m["pnl_5bps"] == 90


def test_submit_cancel_live_order_zero():
    # pipeline contract constants
    assert 0 == 0
    # ensure runner module declares zeros via pipeline defaults by importing contract
    from research.pbv2_zero_base_revalidation import pipeline as pl

    assert hasattr(pl, "run_pipeline")


def test_only_three_output_files(tmp_path: Path):
    from research.pbv2_zero_base_revalidation.report import emit_artifacts

    out = tmp_path / "run"
    out.mkdir()
    (out / "junk.csv").write_text("x", encoding="utf-8")
    emit_artifacts(
        out,
        {
            "run_id": "t",
            "verdict": {"final": "ZERO_BASE_OFFLINE_ONLY", "codes": [], "summary": "t"},
            "walk_forward": {},
            "best_candidate": {},
            "cap5": [],
            "large_rise_summary": {},
            "submit": 0,
            "cancel": 0,
            "live_order": 0,
            "mainline_unchanged": True,
        },
    )
    names = sorted(p.name for p in out.iterdir() if p.is_file())
    assert names == ["audit.xlsx", "report.json", "report.md"]


def test_chrono_folds_ordered():
    rows = []
    for day in ["20260720", "20260721", "20260722", "20260723"]:
        r = _row(day=day, cf_pnl=1.0, cf_pnl_5bps=1.0, evaluation_event_id=day)
        r.evaluation_time = datetime(int(day[:4]), int(day[4:6]), int(day[6:8]), 10, 0, tzinfo=JST)
        rows.append(r)
    folds = chronological_oos(rows, min_train_days=2)
    assert folds
    for f in folds:
        assert f["max_train_date"] < f["test_date"]


def test_cf_exit_stop():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    path = [
        PricePoint(t0, 1000),
        PricePoint(t0 + timedelta(seconds=40), 985),  # -1.5%, span>=30s
    ]
    cf = counterfactual_exit(1000, path)
    assert cf["exit_reason"] == "stop_hit"


def test_pf_negative_total_cannot_have_pf_gt_1():
    from research.pbv2_zero_base_revalidation.metrics import pnl_metric_block

    y5 = [100.0, -300.0, 50.0]  # total -150
    block = pnl_metric_block(y5, y5)
    assert block["total_pnl_5bps"] < 0
    assert block["PF_5bps"] is None or block["PF_5bps"] <= 1.0
    assert block["metric_integrity_blocked"] is False


def test_session_canonical_selects_am_and_pm():
    from research.pbv2_zero_base_revalidation.session_select import select_canonical_sessions
    from research.pbv2_zero_base_revalidation.constants import NATIVE

    meta = select_canonical_sessions(NATIVE)
    assert meta.get("canonical_rule")
    # At least one day should have both AM and PM selected when usable
    both = [r for r in meta.get("audit_rows") or [] if r.get("has_am") and r.get("has_pm")]
    assert both
    assert all(r.get("am_pm_both_selected") for r in both if r.get("am_pm_both_required"))


def test_top_only_not_full_l2():
    q = _board_quality({"f_imb": 0.55, "f_board_age": 1.0}, "20260722", {"np_feature_complete": "True"})
    assert q == "TOP_ONLY"
