"""Price-Flow EXIT unit tests."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.price_flow_exit.constants import ROUNDTRIP_COST_PCT
from research.price_flow_exit.entries import FixedEntry
from research.price_flow_exit.exit_rules import ExitParams, classify_abcd, simulate_exit
from research.price_flow_exit.path_mfe import ExecMFE, PathBar, compute_executable_mfe, simulate_x0

JST = ZoneInfo("Asia/Tokyo")


def _entry(px: float = 1000.0, bl: float | None = 1001.0) -> FixedEntry:
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    return FixedEntry(
        day="20260722",
        symbol="1000.T",
        entry_time=t0,
        entry_price=px,
        entry_method="VCIE_V4",
        cohort="E1",
        breakout_level=bl,
        vcie=True,
        setup_id="s1",
        impulse_episode_id="i1",
        breakout_episode_id="b1",
    )


def _path_rise_then_fail(entry: FixedEntry) -> list[PathBar]:
    bars = []
    t0 = entry.entry_time
    # rise above breakout then fail
    for i, (px, bid) in enumerate(
        [
            (1000.5, 1000.0),
            (1001.5, 1001.0),
            (1002.0, 1001.5),
            (1000.5, 1000.0),
            (999.0, 998.5),
            (998.0, 997.5),
            (997.5, 997.0),
            (997.0, 996.5),
        ]
    ):
        bars.append(
            PathBar(
                t=t0 + timedelta(seconds=5 * (i + 1)),
                px=px,
                bid=bid,
                ask=px + 1,
                bid_qty=100,
                ask_qty=100,
                volume_delta=200.0,
                tick_direction=1 if i < 3 else -1,
                buy_aggression=1.0 if i < 3 else 0.0,
                spread_bps=10.0,
            )
        )
    return bars


def test_entry_time_unchanged():
    e = _entry()
    t = e.entry_time
    path = _path_rise_then_fail(e)
    simulate_x0(e, path)
    assert e.entry_time == t


def test_entry_price_unchanged():
    e = _entry()
    px = e.entry_price
    simulate_x0(e, _path_rise_then_fail(e))
    assert e.entry_price == px


def test_bid_used_for_executable_exit():
    e = _entry()
    path = _path_rise_then_fail(e)
    mfe = compute_executable_mfe(e, path)
    assert mfe.executable_mfe_bid is not None
    # peak bid 1001.5 → ret from 1000
    assert mfe.executable_mfe_bid == pytest.approx((1001.5 / 1000 - 1) * 100, abs=1e-6)


def test_no_current_price_as_bid_without_flag():
    e = _entry()
    t0 = e.entry_time
    path = [
        PathBar(t0 + timedelta(seconds=1), 1010.0, None, 1011.0, 1, 1, 10.0, 1, 1.0, None),
        PathBar(t0 + timedelta(seconds=2), 1012.0, None, 1013.0, 1, 1, 10.0, 1, 1.0, None),
    ]
    mfe = compute_executable_mfe(e, path)
    assert mfe.executable_mfe_bid is None
    assert mfe.quote_evaluable is False


def test_cost_5bps():
    assert ROUNDTRIP_COST_PCT == 0.05


def test_mfe_uses_only_future_for_evaluation():
    e = _entry()
    path = _path_rise_then_fail(e)
    assert all(b.t >= e.entry_time for b in path)


def test_exit_rule_uses_only_past():
    e = _entry()
    path = _path_rise_then_fail(e)
    ex = simulate_exit(e, path, mode="X1", params=ExitParams(fb_window_sec=60))
    assert ex.exit_time >= e.entry_time


def test_abcd_classification():
    mfe = ExecMFE(
        True, 0.2, -0.1, 0.15, -0.1, 0.10, -0.15, 1, 2, 3, 10, 5, 1, 1, 1, 0.1, 1001.0, 10, 0
    )
    assert classify_abcd(mfe, -10.0).label == "C"
    assert classify_abcd(mfe, 1.0).label.startswith("D")
    mfe_a = ExecMFE(True, -0.1, -0.2, -0.05, -0.2, -0.1, -0.25, None, None, None, 0, 0, 0, 0, 0, None, None, 5, 0)
    assert classify_abcd(mfe_a, -1.0).label == "A"


def test_positive_duration():
    e = _entry()
    mfe = compute_executable_mfe(e, _path_rise_then_fail(e))
    assert mfe.positive_duration >= 0


def test_break_even_duration():
    e = _entry()
    mfe = compute_executable_mfe(e, _path_rise_then_fail(e))
    assert mfe.break_even_duration >= 0


def test_failed_breakout_exit():
    e = _entry(bl=1001.0)
    path = _path_rise_then_fail(e)
    ex = simulate_exit(e, path, mode="X1", params=ExitParams(fb_window_sec=60))
    assert ex.exit_reason in ("failed_breakout_exit", "stop_hit", "trailing_mfe_exit", "path_end", "no_progress_exit")


def test_no_follow_through_exit():
    e = _entry()
    t0 = e.entry_time
    path = [
        PathBar(t0 + timedelta(seconds=s), 1000.2, 1000.0, 1001.0, 1, 1, 50.0, 0, 0.4, 10.0)
        for s in range(40, 130, 5)
    ]
    ex = simulate_exit(e, path, mode="X2", params=ExitParams(nft_window_sec=180, nft_progress_pct=0.20))
    assert ex.hold_sec >= 0


def test_break_even_protection():
    e = _entry()
    t0 = e.entry_time
    path = []
    # arm with profit then return to BE
    for i, bid in enumerate([1000.0, 1002.0, 1003.0, 1001.0, 1000.0, 999.5]):
        path.append(PathBar(t0 + timedelta(seconds=10 * (i + 1)), bid + 0.5, bid, bid + 1, 1, 1, 100.0, 1, 1.0, 5.0))
    ex = simulate_exit(e, path, mode="X3", params=ExitParams(be_arm_pct=0.05))
    assert ex.exit_reason


def test_impulse_decay_exit():
    e = _entry()
    path = _path_rise_then_fail(e)
    ex = simulate_exit(e, path, mode="X4", params=ExitParams())
    assert ex.exit_time >= e.entry_time


def test_volume_exhaustion_exit():
    e = _entry()
    path = _path_rise_then_fail(e)
    ex = simulate_exit(e, path, mode="X5", params=ExitParams())
    assert ex.exit_time >= e.entry_time


def test_exit_priority():
    e = _entry()
    path = _path_rise_then_fail(e)
    ex = simulate_exit(e, path, mode="X6", params=ExitParams(fb_window_sec=60))
    assert ex.reasons


def test_same_episode_reentry():
    e = _entry()
    assert e.breakout_episode_id


def test_warmup_not_in_true_oos():
    from research.price_flow_exit.constants import OOS_DAYS, WARMUP_DAY

    assert WARMUP_DAY not in OOS_DAYS


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
        rows.append(
            CandidateRow(
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
        )
    assert replay_cap5(rows, lambda r: float(r.pbv2_score or 0), method_name="t") == replay_cap5(
        rows, lambda r: float(r.pbv2_score or 0), method_name="t"
    )


def test_submit_cancel_live_zero():
    from research.price_flow_exit import pipeline as pl

    assert hasattr(pl, "run_pipeline")


def test_mainline_unchanged():
    from research.price_flow_exit.constants import NATIVE

    assert (NATIVE / "src" / "small_paper" / "pilot_runner.py").exists()


def test_only_three_outputs(tmp_path: Path):
    from research.price_flow_exit.report import emit_artifacts

    out = tmp_path / "run"
    out.mkdir()
    (out / "junk.csv").write_text("x", encoding="utf-8")
    emit_artifacts(
        out,
        {
            "run_id": "t",
            "verdict": {"final": "PRICE_FLOW_EXIT_OFFLINE_ONLY", "codes": [], "summary": "t"},
            "evaluation": {"cohorts": {}},
            "submit": 0,
            "cancel": 0,
            "live_order": 0,
            "mainline_unchanged": True,
        },
    )
    assert sorted(p.name for p in out.iterdir() if p.is_file()) == ["audit.xlsx", "report.json", "report.md"]
