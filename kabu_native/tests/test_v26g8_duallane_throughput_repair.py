"""V26G8 DualLane throughput repair: same-tick share + exact incremental cache.

Does not change ENTRY/EXIT/ArchE/FIXED600/750 semantics. Compares repaired
runtime decisions to the full SoT rebuild (Candidate-7 compute path).
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"

from research.e1_x35_passive_exit.paths import build_path
from small_paper.v1r_live_dual_lane import (
    V1RLiveDualLane,
    reset_dual_lane_for_tests,
    session_end_for_position,
)

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260824"
T0 = datetime(2026, 8, 24, 9, 25, 0, tzinfo=JST).timestamp()


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


def test_a_same_tick_does_not_double_rebuild(dual, monkeypatch):
    import research.e1_x35_passive_exit.paths as paths_mod
    import research.v1r_exit_v2_asymmetric.states as states_mod

    n_path = {"n": 0}
    n_bundle = {"n": 0}
    real_path = paths_mod.build_path
    real_bundle = states_mod.build_trade_bundle

    def wrap_path(*a, **k):
        n_path["n"] += 1
        return real_path(*a, **k)

    def wrap_bundle(*a, **k):
        n_bundle["n"] += 1
        return real_bundle(*a, **k)

    monkeypatch.setattr(paths_mod, "build_path", wrap_path)
    monkeypatch.setattr(states_mod, "build_trade_bundle", wrap_bundle)

    out = _admit(dual, "6098", 1000.0, T0)
    assert out["primary_admitted"] and out["control_admitted"]
    n_path["n"] = 0
    n_bundle["n"] = 0
    for i in range(1, 80):
        t = T0 + i * 0.5
        dual.on_tick(
            symbol="6098",
            payload=_snap(t, 1000.0 + i * 0.1, 1001.0 + i * 0.1),
            event_t=t,
            push_sequence=i,
        )
    # Stage B fast-path before 600s / guard hit: SoT rebuild must not run.
    assert n_path["n"] == 0
    assert n_bundle["n"] == 0
    assert dual.stats.tick_matches >= 2 * 79


def test_b_c_primary_and_control_path_matches_sot(dual):
    _admit(dual, "6098", 5000.0, T0)
    for i in range(1, 40):
        t = T0 + i * 1.0
        bid = 5000.0 - i * 0.5
        dual.on_tick(
            symbol="6098",
            payload=_snap(t, bid, bid + 2.0, 300.0, 300.0),
            event_t=t,
            push_sequence=i,
        )
    for lane, book in (("primary", dual.primary), ("control", dual.control)):
        pos = book["6098"]
        cache = dual._sync_exact_cache(pos)
        got = dual._path_from_cache(cache)
        board = dual._board_dict(pos)
        ref = build_path(
            board,
            entry_price=float(pos.fill_price),
            entry_t=float(pos.fill_time),
            sess_end=session_end_for_position(
                date=pos.date, session=pos.session, fill_time=pos.fill_time
            ),
        )
        assert got.get("ok") and ref.get("ok")
        assert got["offs"].size == ref["offs"].size
        assert np.allclose(got["offs"], ref["offs"], rtol=0.0, atol=1e-9)
        assert np.allclose(got["rets"], ref["rets"], rtol=0.0, atol=1e-9)
        fast = dual._decision_context(pos)
        full = dual.debug_rebuild_decision_context(pos)
        # Pre-horizon SoT may mark pol.ok via 600 fallback; evaluate gates on off_now.
        assert dual._evaluate(pos, fast) == dual._evaluate(pos, full)
        assert lane in ("primary", "control")


def test_d_e_f_guard_fixed600_750_policy_matches_full_rebuild(dual):
    """Drive long enough that SoT policy is invoked; compare vs full rebuild."""
    _admit(dual, "6098", 10000.0, T0)
    # Falling imb to exercise imbalance guard window, then recover.
    for i in range(1, 250):
        t = T0 + i * 0.5  # 125s
        imb_down = i < 20
        bq, aq = (100.0, 900.0) if imb_down else (500.0, 200.0)
        bid = 10000.0 + (0.0 if imb_down else i * 0.2)
        dual.on_tick(
            symbol="6098",
            payload=_snap(t, bid, bid + 5.0, bq, aq),
            event_t=t,
            push_sequence=i,
        )
    pos_p = dual.primary["6098"]
    pos_c = dual.control["6098"]
    for pos in (pos_p, pos_c):
        fast = dual._decision_context(pos)
        full = dual.debug_rebuild_decision_context(pos)
        assert dual._evaluate(pos, fast) == dual._evaluate(pos, full)
        assert bool((fast.get("pol") or {}).get("triggered_guard")) == bool(
            (full.get("pol") or {}).get("triggered_guard")
        )
    assert dual.stats.exact_cache_fallback == 0


def test_fixed600_horizon_policy_matches_full_rebuild(dual):
    """Once off>=600, repaired pol must match full SoT rebuild exactly."""
    _admit(dual, "6098", 8000.0, T0)
    for i in range(1, 610):
        t = T0 + float(i)
        bid = 8000.0 + i * 0.05
        dual.on_tick(
            symbol="6098",
            payload=_snap(t, bid, bid + 2.0, 400.0, 200.0),
            event_t=t,
            push_sequence=i,
        )
    for pos in (dual.primary["6098"], dual.control["6098"]):
        assert float(pos.t[-1] - pos.fill_time) >= 600.0
        fast = dual._decision_context(pos)
        full = dual.debug_rebuild_decision_context(pos)
        assert _pol_key(fast.get("pol") or {}) == _pol_key(full.get("pol") or {})
        assert dual._evaluate(pos, fast) == dual._evaluate(pos, full)
    assert dual.stats.exact_cache_fallback == 0


def test_g_h_i_slot_multi_open_same_symbol_both_lanes(dual):
    a = _admit(dual, "285A", 53330.0, T0)
    b = _admit(dual, "5803", 5070.0, T0 + 0.2)
    assert a["primary_admitted"] and b["primary_admitted"]
    assert dual.open_n("primary") == 2
    assert dual.open_n("control") == 2
    t = T0 + 1.0
    dual.on_tick(symbol="285A", payload=_snap(t, 53320.0, 53340.0), event_t=t, push_sequence=1)
    dual.on_tick(symbol="5803", payload=_snap(t, 5068.0, 5072.0), event_t=t, push_sequence=2)
    assert "285A" in dual.primary and "5803" in dual.primary
    assert dual.primary["285A"].t and dual.control["285A"].t
    assert dual.stats.lookup_miss_with_open == 0


def test_j_am_pm_session_end_identity(dual):
    _admit(dual, "6098", 1000.0, T0, session="AM")
    se_am = session_end_for_position(date=DAY, session="AM", fill_time=T0)
    t_pm = datetime(2026, 8, 24, 12, 35, 0, tzinfo=JST).timestamp()
    dual2_dir = Path(dual.trace_dir) / "pm" if dual.trace_dir else None
    reset_dual_lane_for_tests()
    dual_pm = V1RLiveDualLane(trace_dir=dual2_dir)
    _admit(dual_pm, "6098", 1000.0, t_pm, session="PM")
    se_pm = session_end_for_position(date=DAY, session="PM", fill_time=t_pm)
    assert se_pm > se_am
    assert dual_pm.primary["6098"].session == "PM"
    reset_dual_lane_for_tests()


def test_k_sequence_preserved_no_drop(dual):
    _admit(dual, "6098", 1000.0, T0)
    seqs = []
    for i in range(1, 30):
        t = T0 + i
        dual.on_push_meta(sequence=i, push_at=str(t))
        dual.on_tick(symbol="6098", payload=_snap(t, 1000.0, 1001.0), event_t=t, push_sequence=i)
        seqs.append(dual.stats.last_seq)
    assert seqs == list(range(1, 30))
    hb = dual.heartbeat_fields()
    assert "event_lag_sec" in hb
    assert "seq_lag" in hb
    assert "backlog_direction" in hb


def test_trace_uses_shared_context_not_strategy_recompute(dual, monkeypatch):
    import research.v1r_exit_v2_asymmetric.policy as policy_mod

    n_apply = {"n": 0}
    real = policy_mod.apply_architecture

    def wrap(*a, **k):
        n_apply["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(policy_mod, "apply_architecture", wrap)
    _admit(dual, "6098", 1000.0, T0)
    n_apply["n"] = 0
    t = T0 + 1.0
    dual.on_tick(symbol="6098", payload=_snap(t, 1000.0, 1001.0), event_t=t, push_sequence=1)
    # Fast path must not call apply_architecture at all (trace+eval share skip).
    assert n_apply["n"] == 0


def test_duplicate_timestamp_attach_matches_sot(dual):
    """Same event_time, later board row must win attach (searchsorted right-1)."""
    from research.v1r_exit_v2_asymmetric.states import attach_board_series

    _admit(dual, "6098", 1000.0, T0)
    t = T0 + 1.0
    dual.on_tick(
        symbol="6098",
        payload=_snap(t, 1000.0, 1001.0, 900.0, 100.0),
        event_t=t,
        push_sequence=1,
    )
    dual.on_tick(
        symbol="6098",
        payload=_snap(t, 1000.0, 1001.0, 100.0, 900.0),
        event_t=t,
        push_sequence=2,
    )
    pos = dual.primary["6098"]
    cache = dual._sync_exact_cache(pos)
    got = dual._path_from_cache(cache)
    board = dual._board_dict(pos)
    path = build_path(
        board,
        entry_price=float(pos.fill_price),
        entry_t=float(pos.fill_time),
        sess_end=session_end_for_position(
            date=pos.date, session=pos.session, fill_time=pos.fill_time
        ),
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
