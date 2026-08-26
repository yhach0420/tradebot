"""V26G10 DualLane true-incremental guard/path: exact vs full-history, O(1) matching tick."""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"

from research.e1_x35_passive_exit.paths import build_path
from small_paper.v1r_exit_v2_activation_gate import STRATEGY_SHA
from small_paper.v1r_exit_v2_contract import EXIT_V2_CANDIDATE_SHA
from small_paper.v1r_live_dual_lane import (
    V1RLiveDualLane,
    reset_dual_lane_for_tests,
    session_end_for_position,
)

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260825"
T0 = datetime(2026, 8, 25, 9, 5, 0, tzinfo=JST).timestamp()
C9_STRATEGY = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
C9_EXIT = "6cc3b8aade76e323682ec39dfd06878aab0ff1a99dd42922744b0054a7ea3255"


def _snap(t: float, bid: float, ask: float, bq: float = 400.0, aq: float = 200.0):
    return {
        "event_time": t,
        "CurrentPriceTime": t,
        "Buy1": {"Price": bid, "Qty": bq},
        "Sell1": {"Price": ask, "Qty": aq},
        "CurrentPrice": (bid + ask) / 2.0,
        "board_age_sec": 0.0,
        "SpecialQuote": False,
    }


def _admit(dual: V1RLiveDualLane, symbol: str, px: float, t: float, session: str = "AM"):
    return dual.try_admit_fill(
        symbol=symbol,
        fill_price=px,
        fill_time=t,
        payload=_snap(t, px - 10.0, px, 400.0, 200.0),
        session=session,
        date=DAY,
        source="v1r_native",
    )


def _pol_key(pol: dict) -> tuple:
    if not pol or not pol.get("ok"):
        return ("not_ok",)
    return (
        bool(pol.get("ok")),
        bool(pol.get("triggered_guard")),
        bool(pol.get("extended")),
        str(pol.get("reason") or ""),
        round(float(pol.get("exit_off") or 0.0), 6),
        round(float(pol.get("exit_time") or 0.0), 6),
        round(float(pol.get("exit_ret_bps") or 0.0), 6),
    )


@pytest.fixture
def dual(tmp_path: Path):
    reset_dual_lane_for_tests()
    d = V1RLiveDualLane(trace_dir=tmp_path)
    yield d
    reset_dual_lane_for_tests()


def test_strategy_sha_unchanged():
    assert STRATEGY_SHA == C9_STRATEGY
    assert EXIT_V2_CANDIDATE_SHA == C9_EXIT


def test_incremental_guard_matches_full_history_every_tick(dual):
    _admit(dual, "6098", 5000.0, T0)
    for i in range(1, 180):
        t = T0 + i * 0.5
        imb_down = 10 <= i <= 40
        bq, aq = (100.0, 900.0) if imb_down else (500.0, 200.0)
        dual.on_tick(
            symbol="6098",
            payload=_snap(t, 5000.0 - (2.0 if imb_down else 0.0), 5002.0, bq, aq),
            event_t=t,
            push_sequence=i,
        )
        pos = dual.primary["6098"]
        cache = dual._sync_exact_cache(pos)
        inc = (
            bool(cache.get("guard_hit")),
            bool(cache.get("guard_frozen")),
            int(cache.get("guard_hit_index", -1)),
            bool(cache.get("guard_monitor_passed")),
        )
        dual._exact_recompute_guard(cache)
        ref = (
            bool(cache.get("guard_hit")),
            bool(cache.get("guard_frozen")),
            int(cache.get("guard_hit_index", -1)),
            bool(cache.get("guard_monitor_passed")),
        )
        assert inc == ref, f"tick={i} inc={inc} ref={ref}"


def test_duplicate_timestamp_guard_matches_recompute_and_sot(dual):
    from research.v1r_exit_v2_asymmetric.states import attach_board_series

    _admit(dual, "6098", 1000.0, T0)
    t = T0 + 8.0
    dual.on_tick(symbol="6098", payload=_snap(t, 1000.0, 1001.0, 900.0, 100.0), event_t=t, push_sequence=1)
    dual.on_tick(symbol="6098", payload=_snap(t, 1000.0, 1001.0, 100.0, 900.0), event_t=t, push_sequence=2)
    t2 = T0 + 12.0
    dual.on_tick(symbol="6098", payload=_snap(t2, 1000.0, 1001.0, 100.0, 900.0), event_t=t2, push_sequence=3)
    pos = dual.primary["6098"]
    cache = dual._sync_exact_cache(pos)
    inc = (bool(cache.get("guard_hit")), int(cache.get("guard_hit_index", -1)))
    dual._exact_recompute_guard(cache)
    ref = (bool(cache.get("guard_hit")), int(cache.get("guard_hit_index", -1)))
    assert inc == ref
    got = dual._path_from_cache(cache)
    board = dual._board_dict(pos)
    path = build_path(
        board,
        entry_price=float(pos.fill_price),
        entry_t=float(pos.fill_time),
        sess_end=session_end_for_position(date=pos.date, session=pos.session, fill_time=pos.fill_time),
    )
    path = attach_board_series(path, board)
    gi = np.asarray(got["imb"])
    pi = np.asarray(path["imb"])
    both = np.isfinite(gi) & np.isfinite(pi)
    assert both.any()
    assert float(np.max(np.abs(gi[both] - pi[both]))) < 1e-12
    fast = dual._decision_context(pos)
    full = dual.debug_rebuild_decision_context(pos)
    assert dual._evaluate(pos, fast) == dual._evaluate(pos, full)


def test_path_not_materialized_before_need_full(dual, monkeypatch):
    n_mat = {"n": 0}
    real = dual._path_from_cache

    def wrap(cache):
        n_mat["n"] += 1
        return real(cache)

    monkeypatch.setattr(dual, "_path_from_cache", wrap)
    _admit(dual, "6098", 1000.0, T0)
    n_mat["n"] = 0
    for i in range(1, 80):
        t = T0 + i * 0.5
        dual.on_tick(symbol="6098", payload=_snap(t, 1000.0, 1001.0), event_t=t, push_sequence=i)
    assert n_mat["n"] == 0
    assert dual.stats.path_materialization == 0
    assert dual.stats.guard_incremental_update > 0


def test_seq_lag_is_publisher_minus_consumer_ack(dual):
    dual.note_ingress_cursors(publisher_last_sequence=70000, consumer_ack_sequence=50)
    hb = dual.heartbeat_fields()
    assert hb["seq_lag"] == 69950
    assert hb["paper_consumer_seq_lag"] == 69950
    assert "event_lag_sec" in hb
    assert "processed_event_time" in hb
    assert "backlog_direction" in hb
    assert hb["stats"]["cache_hit"] >= 0
    assert "guard_incremental_update" in hb["stats"]
    assert "path_materialization" in hb["stats"]


def test_matching_tick_cost_does_not_scale_with_board_n(dual):
    _admit(dual, "5803", 5011.0, T0)
    checkpoints = {2000: None, 5000: None}
    target = 5000
    times: list[float] = []
    for i in range(1, target + 1):
        t = T0 + i * 0.05
        t0 = time.perf_counter()
        dual.on_tick(
            symbol="5803",
            payload=_snap(t, 5010.0, 5012.0, 400.0, 200.0),
            event_t=t,
            push_sequence=i,
        )
        dt = (time.perf_counter() - t0) * 1000.0
        if i in checkpoints:
            checkpoints[i] = dt
        if i > target - 200:
            times.append(dt)
    pos = dual.primary["5803"]
    assert len(pos.t) >= 5000
    mean_tail = float(np.mean(times))
    # Linear O(n) at 5k vs 2k would be ~2.5x; true incremental stays near-flat.
    ms_2k = float(checkpoints[2000] or 0.0)
    ms_5k = float(checkpoints[5000] or 0.0)
    assert mean_tail < 8.0
    if ms_2k > 0.05:
        assert ms_5k / ms_2k < 4.0
    assert dual.stats.exact_cache_fallback == 0


def test_truncated_clock_does_not_fail_closed(dual):
    _admit(dual, "5803", 5011.0, T0)
    dual.on_tick(
        symbol="5803",
        payload=_snap(T0 - 0.3, 5010.0, 5012.0),
        event_t=T0 - 0.3,
        push_sequence=1,
    )
    assert dual.fail_closed is False
    assert dual.stats.exceptions == 0
    pos = dual.primary["5803"]
    assert len(pos.t) >= 2
    assert pos.t[-1] + 1e-15 >= pos.t[-2]


def test_force_full_policy_match_at_horizons(dual):
    _admit(dual, "6098", 8000.0, T0)
    for i in range(1, 610):
        t = T0 + float(i)
        dual.on_tick(
            symbol="6098",
            payload=_snap(t, 8000.0 + i * 0.05, 8002.0 + i * 0.05, 400.0, 200.0),
            event_t=t,
            push_sequence=i,
        )
    for pos in (dual.primary["6098"], dual.control["6098"]):
        fast = dual._decision_context(pos)
        full = dual.debug_rebuild_decision_context(pos)
        assert _pol_key(fast.get("pol") or {}) == _pol_key(full.get("pol") or {})
        assert dual._evaluate(pos, fast) == dual._evaluate(pos, full)
    assert dual.stats.exact_cache_fallback == 0
