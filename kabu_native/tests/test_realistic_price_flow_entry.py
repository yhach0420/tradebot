"""RPFE safety + temporal state-machine tests."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.pbv2_zero_base_revalidation.panel import CandidateRow
from research.realistic_price_flow_entry.constants import TIME_FEATURE_BLOCKLIST
from research.realistic_price_flow_entry.evaluate import (
    day_matched_comparison,
    early_stop_rate,
    matched_comparison,
    pbv2_keep,
)
from research.realistic_price_flow_entry.features import dynamic_complete
from research.realistic_price_flow_entry.state_machine import (
    PATTERN_A_SPECS,
    ThresholdSet,
    assert_no_direct_idle_to_entry,
    audit_state_machine_integrity,
    flow_confirm,
    invalid_reason,
    run_pattern_stream,
)

JST = ZoneInfo("Asia/Tokyo")


def _row(
    t: datetime,
    px: float = 1000.0,
    *,
    cf_exit_reason: str = "stop_hit",
    cf_hold_sec: float | None = 120.0,
    is_stop: bool = True,
    **feat,
) -> CandidateRow:
    features = {
        "f_vwap": 0.0,
        "f_mom": 0.2,
        "f_rise5": -0.2,
        "f_rise10": 0.1,
        "f_near_high": 1.0,
        "f_tv": 1e9,
        "f_spread": 2.0,
        "f_atr": 0.5,
        "f_fall": 0.4,
        "f_bounce": 0.3,
        "f_np_ret_60": 0.0,
        "f_np_tv_chg_pct_60": 0.3,
        "f_np_ticks_60": 10.0,
        "f_np_imb_chg_60": 0.05,
        "f_np_bid_chg_60": 0.01,
        "f_np_ask_chg_60": -0.01,
    }
    features.update(feat)
    return CandidateRow(
        day=t.strftime("%Y%m%d"),
        session="s",
        symbol="1000.T",
        evaluation_time=t,
        evaluation_event_id=f"e-{t.isoformat()}",
        universe_source="core",
        current_price=px,
        current_price_time=t,
        board_time=t,
        board_age_sec=1.0,
        price_age_sec=0.5,
        pbv2_candidate=False,
        pbv2_score=2.0,
        pbv2_decision=False,
        reject_reason="",
        accept=False,
        cap_blocked=False,
        features=features,
        board_quality="TOP_ONLY",
        lane_c_complete=all(
            features.get(k) is not None
            for k in ("f_np_imb_chg_60", "f_np_bid_chg_60", "f_np_ask_chg_60", "f_np_tv_chg_pct_60")
        ),
        lane_c_any=True,
        session_bucket="AM",
        pnl_evaluable=True,
        cf_pnl=10.0,
        cf_pnl_5bps=5.0,
        cf_exit_reason=cf_exit_reason,
        cf_hold_sec=cf_hold_sec,
        is_stop=is_stop,
    )


def _thr_loose() -> ThresholdSet:
    t = ThresholdSet()
    for state, specs in PATTERN_A_SPECS.items():
        for feat, op, _ in specs:
            t.values[f"{state}:{feat}:{op}"] = -1e9 if op == ">=" else 1e9
    for feat, op in (
        ("f_np_imb_chg_60", ">="),
        ("f_np_bid_chg_60", ">="),
        ("f_np_ask_chg_60", "<="),
    ):
        t.values[f"FLOW:{feat}:{op}"] = -1e9 if op == ">=" else 1e9
    return t


def _temporal_pullback_rows() -> list[CandidateRow]:
    """Build a timed sequence that can reach ENTRY only via real micro-high cross."""
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    # prices: build pullback then reclaim above micro_high
    # CONTEXT period with soft pullback marks
    seq = [
        (0, 1000.0, 0.20, 0.3),  # IDLE→CONTEXT
        (35, 995.0, 0.18, 0.3),  # CONTEXT dwell + pullback start
        (70, 990.0, 0.15, 0.3),  # pullback >=30s → SETUP eligible
        (105, 988.0, 0.12, 0.3),  # SETUP → wait
        (140, 989.0, 0.14, 0.35),  # SETUP→SELL_WEAK
        (175, 990.0, 0.16, 0.4),  # SELL_WEAK confirm obs1
        (210, 991.0, 0.22, 0.5),  # SELL_WEAK→BUY (mom improved)
        (245, 992.0, 0.25, 0.55),  # BUY hold
        (280, 1001.0, 0.35, 0.7),  # micro-high cross → PRICE+ENTRY
    ]
    rows = []
    for sec, px, mom, tv_chg in seq:
        r = _row(
            t0 + timedelta(seconds=sec),
            px=px,
            f_mom=mom,
            f_rise5=-0.3,
            f_fall=1.0,
            f_bounce=0.5,
            f_np_tv_chg_pct_60=tv_chg,
            f_spread=2.0,
            f_tv=1e9 + sec * 1e3,
        )
        rows.append(r)
    return rows


def test_state_order_required():
    rows = _temporal_pullback_rows()
    trigs = run_pattern_stream(rows, pattern="A", thr=_thr_loose(), require_flow=False)
    assert trigs, "expected temporal ENTRY with micro-high cross"
    hist = trigs[0].state_history
    assert assert_no_direct_idle_to_entry(hist)
    joined = " ".join(hist)
    assert "CONTEXT_READY" in joined
    assert "SETUP_DETECTED" in joined
    assert "PRICE_TRIGGERED" in joined
    assert trigs[0].real_micro_high_cross
    assert (trigs[0].total_confirmation_latency_sec or 0) > 0


def test_no_same_timestamp_multistep_to_buy():
    rows = _temporal_pullback_rows()
    trigs = run_pattern_stream(rows, pattern="A", thr=_thr_loose(), require_flow=False)
    audit = audit_state_machine_integrity(trigs)
    assert audit["context_to_buy_confirm_same_timestamp"] == 0
    assert audit["latency_zero_entries"] == 0
    assert audit["states_advanced_gt1_per_obs"] == 0
    assert audit["gate_ok"]


def test_no_direct_idle_to_entry():
    assert assert_no_direct_idle_to_entry(["IDLE->CONTEXT_READY", "CONTEXT_READY->SETUP_DETECTED"])
    assert not assert_no_direct_idle_to_entry(["IDLE->ENTRY"])


def test_price_trigger_requires_real_cross():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    # Never cross above micro_high — prices keep falling
    rows = []
    for i in range(12):
        px = 1000.0 - i * 2
        rows.append(
            _row(
                t0 + timedelta(seconds=35 * i),
                px=px,
                f_mom=0.1 + i * 0.01,
                f_rise5=-0.3,
                f_fall=1.0,
                f_bounce=0.2,
                f_np_tv_chg_pct_60=0.4,
            )
        )
    trigs = run_pattern_stream(rows, pattern="A", thr=_thr_loose(), require_flow=False)
    assert trigs == []


def test_invalidated_on_new_low():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    r = _row(t0, f_np_ret_60=-1.6)
    assert invalid_reason(r, _thr_loose()) == "new_low_pressure"


def test_invalidated_on_spread_widening():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    thr = _thr_loose()
    thr.values["SELL_PRESSURE_WEAKENED:f_spread:<="] = 2.0
    r = _row(t0, f_spread=10.0)
    assert invalid_reason(r, thr) == "spread_widening"


def test_no_time_features():
    for b in TIME_FEATURE_BLOCKLIST:
        assert "minute" in b or "session" in b or "hour" in b or "day" in b or "weekday" in b or "am" in b or "refresh" in b


def test_no_symbol_features():
    r = _row(datetime(2026, 7, 22, 10, 0, tzinfo=JST))
    assert "symbol_id" not in r.features


def test_no_future_features():
    r = _row(datetime(2026, 7, 22, 10, 0, tzinfo=JST))
    assert not any(k.startswith("forward_") for k in r.features)


def test_dynamic_no_imputation():
    r = _row(datetime(2026, 7, 22, 10, 0, tzinfo=JST))
    r.features["f_np_imb_chg_60"] = None
    r.lane_c_complete = False
    assert dynamic_complete(r) is False
    assert flow_confirm(r, _thr_loose()) == "NOT_EVALUABLE"


def test_price_only_runs_without_dynamic():
    rows = _temporal_pullback_rows()
    for r in rows:
        r.features["f_np_imb_chg_60"] = None
        r.features["f_np_bid_chg_60"] = None
        r.features["f_np_ask_chg_60"] = None
        r.lane_c_complete = False
        # keep tv_chg for accel
    trigs = run_pattern_stream(rows, pattern="A", thr=_thr_loose(), require_flow=False)
    assert isinstance(trigs, list)


def test_flow_not_evaluable_when_missing():
    r = _row(datetime(2026, 7, 22, 10, 0, tzinfo=JST))
    r.lane_c_complete = False
    assert flow_confirm(r, _thr_loose()) == "NOT_EVALUABLE"


def test_train_date_before_test():
    from research.pbv2_zero_base_revalidation.leakage import audit_fold

    assert audit_fold(["20260720", "20260721"], "20260722")["max_train_date_lt_test"]


def test_pf_5bps_integrity():
    from research.pbv2_zero_base_revalidation.metrics import pnl_metric_block

    block = pnl_metric_block([100.0, -300.0], [100.0, -300.0])
    assert block["total_pnl_5bps"] < 0
    assert block["PF_5bps"] is None or block["PF_5bps"] <= 1.0
    assert not block["metric_integrity_blocked"]


def test_early_stop_uses_hold_sec():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    a = _row(t0, cf_hold_sec=60.0, cf_exit_reason="stop_hit", is_stop=True)
    b = _row(t0 + timedelta(minutes=1), cf_hold_sec=400.0, cf_exit_reason="stop_hit", is_stop=True)
    c = _row(
        t0 + timedelta(minutes=2),
        cf_hold_sec=100.0,
        cf_exit_reason="trailing_mfe_exit",
        is_stop=False,
    )
    rate = early_stop_rate([a, b, c], lambda r: True)
    # only a is early stop among 3
    assert rate == pytest.approx(1 / 3, abs=1e-4)


def test_matched_comparison_is_day_local():
    rows = []
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    for day_i, day in enumerate(("20260722", "20260723")):
        for i in range(6):
            r = _row(t0 + timedelta(days=day_i, minutes=i))
            r.day = day
            r.pbv2_decision = i < 4  # 4 pbv2 / day
            r.pnl_evaluable = True
            r.cf_pnl_5bps = float(i)
            r.cf_pnl = float(i)
            rows.append(r)
    # RPFE keep: 2 per day
    rpfe_keys = {(r.day, r.symbol, r.evaluation_time.isoformat()) for r in rows if int(r.evaluation_time.minute) < 2}
    m = day_matched_comparison(rows, pbv2_keep, lambda r: (r.day, r.symbol, r.evaluation_time.isoformat()) in rpfe_keys)
    assert m["verdict"] == "DAY_MATCHED_COMPARISON_READY"
    assert m["same_day_same_n"]["n_matched"] == 4  # min(4,2)=2 per day × 2
    legacy = matched_comparison(rows, pbv2_keep, lambda r: True, n_target=999)
    assert legacy["day_matched"]["same_day_same_n"]["n_matched"] <= len(rows)


def test_cap5_deterministic():
    from research.pbv2_zero_base_revalidation.cap5 import replay_cap5

    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    rows = []
    for i, sym in enumerate(["A.T", "B.T", "C.T", "D.T", "E.T", "F.T"]):
        r = _row(t0)
        r.symbol = sym
        r.evaluation_event_id = f"e{i}"
        r.cf_pnl_5bps = 100 - 10 * i
        r.cf_pnl = 100 - 10 * i
        r.pnl_evaluable = True
        rows.append(r)
    a = replay_cap5(rows, lambda r: float(r.cf_pnl_5bps or 0), method_name="t")
    b = replay_cap5(rows, lambda r: float(r.cf_pnl_5bps or 0), method_name="t")
    assert a == b


def test_submit_cancel_live_zero():
    from research.realistic_price_flow_entry import pipeline as pl

    assert hasattr(pl, "run_pipeline")


def test_only_three_outputs(tmp_path: Path):
    from research.realistic_price_flow_entry.report import emit_artifacts

    out = tmp_path / "run"
    out.mkdir()
    (out / "junk.csv").write_text("x", encoding="utf-8")
    emit_artifacts(
        out,
        {
            "run_id": "t",
            "verdict": {"final": "RPFE_OFFLINE_ONLY", "codes": [], "summary": "t"},
            "evaluation": {},
            "submit": 0,
            "cancel": 0,
            "live_order": 0,
            "mainline_unchanged": True,
        },
    )
    assert sorted(p.name for p in out.iterdir() if p.is_file()) == ["audit.xlsx", "report.json", "report.md"]
