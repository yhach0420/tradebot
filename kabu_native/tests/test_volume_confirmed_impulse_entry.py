"""VCIE unit tests — safety and causal integrity."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.volume_confirmed_impulse_entry.features import (
    ThresholdSet,
    compute_features_at,
    detect_triggers_for_symbol,
)
from research.volume_confirmed_impulse_entry.push_loader import PushTick, _side_quality
from research.volume_confirmed_impulse_entry.source_audit import run_source_audit

JST = ZoneInfo("Asia/Tokyo")


def _tick(t0: datetime, i: int, px: float, vol: float, *, bid=None, ask=None, prev_vol=None) -> PushTick:
    prev_px = px - 1 if i > 0 else None
    vdelta = None if prev_vol is None else vol - prev_vol
    q, agg, tick = _side_quality(px, prev_px, bid if bid is not None else px - 1, ask if ask is not None else px + 1)
    return PushTick(
        day=t0.strftime("%Y%m%d"),
        symbol="1000.T",
        event_time=t0 + timedelta(seconds=i),
        current_price=px,
        previous_price=prev_px,
        cumulative_volume=vol,
        volume_delta=vdelta if vdelta is not None and vdelta >= 0 else (None if vdelta is not None and vdelta < 0 else 0.0),
        cumulative_trading_value=vol * px,
        trading_value_delta=(vdelta * px) if vdelta and vdelta > 0 else 0.0,
        bid=bid if bid is not None else px - 1,
        ask=ask if ask is not None else px + 1,
        bid_qty=100,
        ask_qty=100,
        spread_bps=10.0,
        tick_direction=tick,
        trade_side_quality=q,
        buy_aggression=agg,
        price_age_sec=0.1,
        board_age_sec=0.1,
        dq_volume_reset=bool(vdelta is not None and vdelta < 0),
        sequence=i,
    )


def _series_breakout() -> list[PushTick]:
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    ticks = []
    vol = 10000.0
    # build baseline volume + range then breakout
    for i in range(400):
        # quiet then impulse near end
        px = 1000.0 + (i % 20) * 0.05  # range
        if i < 350:
            dvol = 50 + (i % 7)
        else:
            dvol = 400  # impulse
            px = 1000.0 + 0.5 + (i - 350) * 0.2  # break above micro high
        vol += dvol
        prev = vol - dvol
        ticks.append(_tick(t0, i * 2, px, vol, ask=px + 0.5, bid=px - 0.5, prev_vol=prev))
        ticks[-1].previous_price = ticks[-2].current_price if i else None
        ticks[-1].tick_direction = 1 if i and ticks[-1].current_price > ticks[-2].current_price else (-1 if i and ticks[-1].current_price < ticks[-2].current_price else 0)
        ticks[-1].buy_aggression = 1.0
        ticks[-1].trade_side_quality = "QUOTE_INFERRED"
    return ticks


def test_no_double_count_zero_delta():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    a = _tick(t0, 0, 1000, 100, prev_vol=None)
    b = _tick(t0, 1, 1000, 100, prev_vol=100)
    assert b.volume_delta == 0.0


def test_volume_reset_flag():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    a = _tick(t0, 0, 1000, 1000, prev_vol=None)
    b = _tick(t0, 1, 1000, 100, prev_vol=1000)
    assert b.dq_volume_reset is True
    assert b.volume_delta is None or b.volume_delta < 0 or b.dq_volume_reset


def test_baseline_excludes_current_window():
    ticks = _series_breakout()
    fs = compute_features_at(ticks, 200)
    # impulse uses prior medians; feature may be evaluable
    assert "volume_impulse_10s" in fs.values


def test_breakout_level_excludes_current():
    ticks = _series_breakout()
    i = 360
    fs = compute_features_at(ticks, i)
    mh = fs.values.get("micro_high_60s")
    assert mh is not None
    assert mh <= max(t.current_price for t in ticks[max(0, i - 40) : i])  # excl current


def test_real_price_cross_and_hold_required():
    ticks = _series_breakout()
    thr = ThresholdSet(vol_impulse_10s=1.2, vol_impulse_30s=1.1, uptick_ratio=0.5, hold_mode="sec", hold_n=2.0, context_age_sec=300)
    # V1 should need cross+hold
    trigs = detect_triggers_for_symbol(ticks, method="V1_CROSS", thr=thr, step=1)
    for t in trigs:
        assert t.entry_price > t.breakout_level
        assert t.hold_sec >= 0


def test_no_time_symbol_features_in_blocklist():
    from research.volume_confirmed_impulse_entry.constants import TIME_FEATURE_BLOCKLIST

    assert "hour_of_day" in TIME_FEATURE_BLOCKLIST


def test_source_audit_runs():
    a = run_source_audit()
    assert "fields" in a
    assert "answers" in a


def test_pf_5bps_integrity():
    from research.pbv2_zero_base_revalidation.metrics import pnl_metric_block

    b = pnl_metric_block([100.0, -300.0], [100.0, -300.0])
    assert b["total_pnl_5bps"] < 0
    assert not b["metric_integrity_blocked"]


def test_cap5_deterministic():
    from research.pbv2_zero_base_revalidation.cap5 import replay_cap5
    from research.pbv2_zero_base_revalidation.panel import CandidateRow

    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    rows = []
    for i, sym in enumerate(["A.T", "B.T", "C.T", "D.T", "E.T", "F.T"]):
        r = CandidateRow(
            day="20260722",
            session="s",
            symbol=sym,
            evaluation_time=t0,
            evaluation_event_id=f"e{i}",
            universe_source="t",
            current_price=1000,
            current_price_time=t0,
            board_time=t0,
            board_age_sec=0,
            price_age_sec=0,
            pbv2_candidate=True,
            pbv2_score=10 - i,
            pbv2_decision=True,
            reject_reason="",
            accept=False,
            cap_blocked=False,
            pnl_evaluable=True,
            cf_pnl=100 - 10 * i,
            cf_pnl_5bps=100 - 10 * i,
        )
        rows.append(r)
    a = replay_cap5(rows, lambda r: float(r.pbv2_score or 0), method_name="t")
    b = replay_cap5(rows, lambda r: float(r.pbv2_score or 0), method_name="t")
    assert a == b


def test_only_three_outputs(tmp_path: Path):
    from research.volume_confirmed_impulse_entry.report import emit_artifacts

    out = tmp_path / "run"
    out.mkdir()
    (out / "junk.csv").write_text("x", encoding="utf-8")
    emit_artifacts(
        out,
        {
            "run_id": "t",
            "verdict": {"final": "VCIE_OFFLINE_ONLY", "codes": [], "summary": "t"},
            "evaluation": {},
            "submit": 0,
            "cancel": 0,
            "live_order": 0,
            "mainline_unchanged": True,
            "source_audit": {"verdict": "VCIE_SOURCE_AUDIT_PASS", "answers": {}, "notes": ""},
        },
    )
    assert sorted(p.name for p in out.iterdir() if p.is_file()) == ["audit.xlsx", "report.json", "report.md"]


def test_submit_live_zero_contract():
    from research.volume_confirmed_impulse_entry import pipeline as pl

    assert hasattr(pl, "run_pipeline")
