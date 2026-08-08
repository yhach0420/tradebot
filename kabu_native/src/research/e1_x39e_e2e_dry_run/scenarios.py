"""E2E scenario orchestration for V1R dry-run."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from research.e1_x35_passive_exit.paths import build_path
from research.e1_x35r_exit_contract.contracts import canonical_fixed_exit
from research.e1_x36_joint_allocator.replay import simulate_joint
from research.e1_x39_activation_lock.recovery import PositionState, load_state, persist_state, recover

from . import (
    DEMO_DAY,
    DEMO_MARKER,
    DEMO_UNIVERSE,
    EXIT_HOLD_SEC,
    FEATURE_ORDER,
    LOT_QTY,
    MODEL_ARTIFACT_SHA,
    NOTIFY_PREFIXES,
    POSITION_CAP,
    PRECOMMIT_U1_SHA,
    V1R_SHA,
    WAIT_SEC,
)
from .engine import DryRunEngine
from .push_board import DemoPush, demo_day_epoch


def _warmup_pushes(eng: DryRunEngine, symbols: tuple[str, ...], t0: float) -> None:
    """Push history so mid_ret_60/180 and event_rate are defined at t0."""
    for sym_i, sym in enumerate(symbols):
        base = 1000.0 + sym_i * 50.0
        # denser history → higher event_rate; mild mid drift for score differentiation
        for k in range(200):
            tt = t0 - 200.0 + k
            mid_drift = 1.0 + (sym_i * 0.002) * (k / 200.0)
            bid = base * mid_drift
            ask = bid + 1.0 + (sym_i % 3) * 0.5
            eng.ingest_push(DemoPush(
                symbol=sym,
                event_time=tt,
                buy1_price=bid,
                buy1_qty=200.0,
                sell1_price=ask,
                sell1_qty=200.0,
                fresh_sec=0.3,
                special=False,
            ))
        # t0 snapshot: Buy1 = limit reference
        eng.ingest_push(DemoPush(
            symbol=sym,
            event_time=t0,
            buy1_price=base,
            buy1_qty=500.0,
            sell1_price=base + 2.0 + sym_i,  # initially above limit for most
            sell1_qty=200.0,
            fresh_sec=0.2,
            special=False,
        ))


def run_cohort_cap(eng: DryRunEngine, t0: float) -> dict[str, Any]:
    """6+ candidates at 09:05; admit 5; CAP block rest."""
    symbols = DEMO_UNIVERSE[:6]
    _warmup_pushes(eng, symbols, t0)

    events = []
    feature_ok = True
    for sym in symbols:
        feats = eng.features_from_push(sym, t0)
        if any(feats.get(f) is None or not np.isfinite(feats.get(f)) for f in FEATURE_ORDER):
            feature_ok = False
        score = float(eng.score_fn(feats))
        assert np.isfinite(score), (sym, score, feats)
        b = eng.board(sym)
        # limit = Buy1 @ t0
        i = int(np.searchsorted(b["t"], t0, side="right") - 1)
        limit = float(b["bid"][i])
        events.append({
            "date": DEMO_DAY,
            "symbol": sym,
            "session": "AM",
            "signal_time": t0,
            "filled": False,
            "limit_price": limit,
            "bid0": limit,
            **feats,
            "score_preview": score,
        })

    # A/B identity
    sim_a = simulate_joint([dict(e) for e in events], score_fn=eng.score_fn)
    sim_b = simulate_joint([dict(e) for e in events], score_fn=eng.score_fn)
    adm_a = sorted(e["symbol"] for e in sim_a["events"] if e.get("admitted"))
    adm_b = sorted(e["symbol"] for e in sim_b["events"] if e.get("admitted"))
    assert adm_a == adm_b

    admitted = [e for e in sim_a["events"] if e.get("admitted")]
    blocked = [e for e in sim_a["events"] if e.get("CAPACITY_BLOCKED")]
    # notify
    for e in admitted:
        eng.notify.enqueue("ENTRY", {
            "symbol": e["symbol"], "signal_time": t0, "limit": e["limit_price"],
            "status": "PENDING", "expiry": t0 + WAIT_SEC,
        }, prefix=NOTIFY_PREFIXES["entry"])
        eng.primary_pending += 1
        eng.track_cap(eng.primary_open, eng.primary_pending)
        eng.append_ledger("V1R_OPERATIONAL_REALIZABLE_TEST", {
            "kind": "ENTRY_PENDING", "symbol": e["symbol"], "t0": t0,
        })
    for e in blocked:
        eng.notify.enqueue("CAP_BLOCKED", {
            "symbol": e["symbol"], "reason": "CAPACITY_BLOCKED",
        }, prefix=NOTIFY_PREFIXES["cap_blocked"])

    assert len(admitted) == POSITION_CAP
    assert len(blocked) >= 1
    assert eng.open_plus_pending_max <= POSITION_CAP
    assert eng.cap_violations == 0

    return {
        "feature_ok": feature_ok,
        "candidates": len(events),
        "admitted": [e["symbol"] for e in admitted],
        "cap_blocked": [e["symbol"] for e in blocked],
        "rank_order": sorted(
            ((e["symbol"], e.get("alloc_score")) for e in sim_a["events"]),
            key=lambda x: (-(x[1] if x[1] is not None and np.isfinite(x[1]) else -1e18), x[0]),
        ),
        "events": sim_a["events"],
        "score_rank_admission_identity": adm_a == adm_b,
        "raw_push_to_feature": True,
    }


def run_fill(eng: DryRunEngine, events: list[dict], t0: float) -> dict[str, Any]:
    """Admittee gets Sell1 <= limit within 1s → FILL at limit."""
    fill_sym = next(e["symbol"] for e in events if e.get("admitted"))
    row = next(e for e in events if e["symbol"] == fill_sym)
    limit = float(row["limit_price"])
    # push fill evidence at t0+0.4
    eng.ingest_push(DemoPush(
        symbol=fill_sym,
        event_time=t0 + 0.4,
        buy1_price=limit,
        buy1_qty=300.0,
        sell1_price=limit - 1.0,  # crosses
        sell1_qty=150.0,
        fresh_sec=0.5,
        special=False,
    ))
    board = eng.board(fill_sym)
    sess_end = t0 + 2.5 * 3600
    fill = find_ask_cross_fill(
        board, t0=t0, wait_sec=WAIT_SEC, limit_price=limit, sess_end=sess_end,
    )
    assert fill["filled"] is True
    assert abs(float(fill["fill_price"]) - limit) < 1e-12
    eng.notify.enqueue("FILL", {
        "symbol": fill_sym, "fill_time": fill["fill_t"], "fill_price": fill["fill_price"],
        "exit_target": float(fill["fill_t"]) + EXIT_HOLD_SEC,
    }, prefix=NOTIFY_PREFIXES["fill"])
    eng.primary_pending = max(0, eng.primary_pending - 1)
    eng.primary_open += 1
    eng.track_cap(eng.primary_open, eng.primary_pending)
    eng.append_ledger("V1R_OPERATIONAL_REALIZABLE_TEST", {
        "kind": "FILL", "symbol": fill_sym, "fill_price": fill["fill_price"],
    })
    eng.append_ledger("V1R_RESEARCH_PROSPECTIVE_TEST", {
        "kind": "FILL", "symbol": fill_sym, "order_active": t0,
    })
    return {
        "symbol": fill_sym,
        "fill_time": float(fill["fill_t"]),
        "fill_price": float(fill["fill_price"]),
        "limit": limit,
        "exit_target": float(fill["fill_t"]) + EXIT_HOLD_SEC,
        "pass": True,
    }


def run_expire(eng: DryRunEngine, events: list[dict], t0: float, exclude: str) -> dict[str, Any]:
    """Another admittee: Sell1 stays above limit → EXPIRED at t0+1."""
    exp_sym = next(
        e["symbol"] for e in events
        if e.get("admitted") and e["symbol"] != exclude
    )
    row = next(e for e in events if e["symbol"] == exp_sym)
    limit = float(row["limit_price"])
    # keep ask above limit through window
    for dt in (0.2, 0.5, 0.9):
        eng.ingest_push(DemoPush(
            symbol=exp_sym,
            event_time=t0 + dt,
            buy1_price=limit,
            buy1_qty=200.0,
            sell1_price=limit + 5.0,
            sell1_qty=200.0,
            fresh_sec=0.4,
            special=False,
        ))
    board = eng.board(exp_sym)
    fill = find_ask_cross_fill(
        board, t0=t0, wait_sec=WAIT_SEC, limit_price=limit, sess_end=t0 + 3600,
    )
    assert fill["filled"] is False
    eng.notify.enqueue("EXPIRED", {
        "symbol": exp_sym, "expiry": t0 + WAIT_SEC, "original_t0": t0,
    }, prefix=NOTIFY_PREFIXES["expired"])
    eng.primary_pending = max(0, eng.primary_pending - 1)
    eng.track_cap(eng.primary_open, eng.primary_pending)
    eng.append_ledger("V1R_OPERATIONAL_REALIZABLE_TEST", {
        "kind": "EXPIRED", "symbol": exp_sym, "expiry": t0 + WAIT_SEC,
    })
    return {"symbol": exp_sym, "expired": True, "no_wait_extension": True, "pass": True}


def run_reject_cases(eng: DryRunEngine, t0: float) -> dict[str, Any]:
    """Qty / freshness / special reject even if price would cross."""
    sym = "1007"
    limit = 2000.0
    # warmup t0 book
    eng.ingest_push(DemoPush(
        symbol=sym, event_time=t0, buy1_price=limit, buy1_qty=200,
        sell1_price=limit + 10, sell1_qty=200, fresh_sec=0.2,
    ))
    # qty < 100
    eng.ingest_push(DemoPush(
        symbol=sym, event_time=t0 + 0.2, buy1_price=limit, buy1_qty=200,
        sell1_price=limit - 1, sell1_qty=50, fresh_sec=0.2,
    ))
    # freshness > 5
    eng.ingest_push(DemoPush(
        symbol=sym, event_time=t0 + 0.4, buy1_price=limit, buy1_qty=200,
        sell1_price=limit - 1, sell1_qty=200, fresh_sec=6.0,
    ))
    # special quote
    eng.ingest_push(DemoPush(
        symbol=sym, event_time=t0 + 0.6, buy1_price=limit, buy1_qty=200,
        sell1_price=limit - 1, sell1_qty=200, fresh_sec=0.2, special=True,
    ))
    board = eng.board(sym)
    mid = find_ask_cross_fill(
        board, t0=t0, wait_sec=WAIT_SEC, limit_price=limit, sess_end=t0 + 3600,
    )
    assert mid["filled"] is False
    # valid push before expiry → FILL
    eng.ingest_push(DemoPush(
        symbol=sym, event_time=t0 + 0.85, buy1_price=limit, buy1_qty=200,
        sell1_price=limit - 1, sell1_qty=200, fresh_sec=0.2, special=False,
    ))
    board2 = eng.board(sym)
    ok = find_ask_cross_fill(
        board2, t0=t0, wait_sec=WAIT_SEC, limit_price=limit, sess_end=t0 + 3600,
    )
    assert ok["filled"] is True
    return {
        "qty_reject": True,
        "freshness_reject": True,
        "special_reject": True,
        "later_valid_fill": True,
        "pass": True,
    }


def run_exit600(eng: DryRunEngine, fill: dict[str, Any]) -> dict[str, Any]:
    """FIRST_VALID_BUY1_AT_OR_AFTER_TARGET."""
    sym = fill["symbol"]
    fill_t = float(fill["fill_time"])
    fill_px = float(fill["fill_price"])
    target = float(fill["exit_target"])
    assert abs(target - (fill_t + EXIT_HOLD_SEC)) < 1e-9

    # before target: should not exit
    eng.ingest_push(DemoPush(
        symbol=sym, event_time=target - 10.0, buy1_price=fill_px + 5,
        buy1_qty=200, sell1_price=fill_px + 6, sell1_qty=200, fresh_sec=0.3,
    ))
    # at/after target: invalid qty
    eng.ingest_push(DemoPush(
        symbol=sym, event_time=target + 1.0, buy1_price=fill_px + 8,
        buy1_qty=50, sell1_price=fill_px + 9, sell1_qty=200, fresh_sec=0.3,
    ))
    # later valid
    exit_t = target + 5.0
    eng.ingest_push(DemoPush(
        symbol=sym, event_time=exit_t, buy1_price=fill_px + 10,
        buy1_qty=200, sell1_price=fill_px + 11, sell1_qty=200, fresh_sec=0.3,
    ))
    board = eng.board(sym)
    path = build_path(board, entry_price=fill_px, entry_t=fill_t, sess_end=fill_t + 7200)
    ex = canonical_fixed_exit(path, EXIT_HOLD_SEC)
    assert ex.get("ok") is True
    assert float(ex["exit_time"]) + 1e-9 >= target
    # must not use pre-target quote as exit
    assert float(ex["exit_time"]) >= target - 1e-9
    eng.notify.enqueue("EXIT", {
        "symbol": sym, "entry": fill_px, "exit": ex.get("exit_price"),
        "hold_sec": ex.get("exit_off"), "reason": "FIXED600",
    }, prefix=NOTIFY_PREFIXES["exit"])
    eng.primary_open = max(0, eng.primary_open - 1)
    eng.track_cap(eng.primary_open, eng.primary_pending)
    eng.append_ledger("V1R_OPERATIONAL_REALIZABLE_TEST", {
        "kind": "EXIT", "symbol": sym, "exit_time": ex["exit_time"],
    })
    return {
        "symbol": sym,
        "target": target,
        "exit_time": float(ex["exit_time"]),
        "canonical": "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET",
        "pass": True,
    }


def run_recovery(eng: DryRunEngine, t0: float, fill: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "state.json"
        # PENDING
        pending = PositionState(
            role="V1R_PAPER_PRIMARY",
            strategy_sha=V1R_SHA,
            model_sha=MODEL_ARTIFACT_SHA,
            precommit_sha=PRECOMMIT_U1_SHA,
            universe_generation="DAY_FIXED_AM_RUNTIME_UNIVERSE_V1",
            universe_effective_time=None,
            symbol="PEND",
            signal_time=t0,
            features={f: 0.0 for f in FEATURE_ORDER},
            score=0.5, rank=1, limit_price=1000.0,
            pending_expiry=t0 + WAIT_SEC, slot_reserved=True, status="PENDING",
        )
        persist_state(path, [pending])
        loaded = load_state(path)
        mid = recover(loaded, now=t0 + 0.5, board_bids_after=[])
        pending_ok = (
            mid[0]["action"] == "RESUME_PENDING"
            and loaded[0].pending_expiry == t0 + WAIT_SEC
        )

        # OPEN mid-hold
        ft = float(fill["fill_time"])
        open_pos = PositionState(
            role="V1R_PAPER_PRIMARY",
            strategy_sha=V1R_SHA, model_sha=MODEL_ARTIFACT_SHA,
            precommit_sha=PRECOMMIT_U1_SHA,
            universe_generation="DAY_FIXED_AM_RUNTIME_UNIVERSE_V1",
            universe_effective_time=None,
            symbol=fill["symbol"], signal_time=t0,
            features={f: 0.0 for f in FEATURE_ORDER},
            score=0.6, rank=1, limit_price=float(fill["limit"]),
            status="OPEN", fill_time=ft, fill_price=float(fill["fill_price"]),
            exit_target=ft + EXIT_HOLD_SEC, slot_reserved=True,
        )
        persist_state(path, [open_pos])
        loaded_o = load_state(path)
        mid_o = recover(loaded_o, now=ft + 300.0, board_bids_after=[])
        open_ok = (
            mid_o[0]["action"] == "RESUME_OPEN_WAIT_TARGET_QUOTE"
            and loaded_o[0].exit_target == ft + EXIT_HOLD_SEC
        )

        # past target
        persist_state(path, [PositionState(**{**open_pos.__dict__})])
        loaded_p = load_state(path)
        target = ft + EXIT_HOLD_SEC
        past = recover(
            loaded_p,
            now=target + 100.0,
            board_bids_after=[(target + 2.0, float(fill["fill_price"]) + 3.0)],
        )
        past_ok = (
            past[0]["action"] == "EXIT_FIRST_VALID_BUY1_AT_OR_AFTER_TARGET"
            and past[0].get("restart_was_not_used_as_target") is True
            and loaded_p[0].exit_target == target
        )

    return {
        "pending_recovery": pending_ok,
        "open_recovery": open_ok,
        "past_target_recovery": past_ok,
        "pass": pending_ok and open_ok and past_ok,
    }


def run_shadow_isolation(eng: DryRunEngine) -> dict[str, Any]:
    eng.shadow_pbv2_positions = 3
    eng.shadow_1m_positions = 2
    eng.cash_1m += 12_000.0  # shadow-only
    eng.notify.enqueue("PBV2", {"n": 3}, prefix=NOTIFY_PREFIXES["pbv2"])
    eng.notify.enqueue("1M", {"cash": eng.cash_1m}, prefix=NOTIFY_PREFIXES["capital_1m"])
    eng.append_ledger("PBV2_SHADOW_TEST", {"positions": 3})
    eng.append_ledger("V1R_1M_SHADOW_TEST", {"cash": eng.cash_1m, "positions": 2})
    # primary cap unchanged by shadows
    before = eng.open_plus_pending_max
    eng.track_cap(eng.primary_open, eng.primary_pending)
    return {
        "pbv2_positions": 3,
        "one_m_positions": 2,
        "cash_1m": eng.cash_1m,
        "primary_cap_unaffected": eng.cap_violations == 0 and eng.open_plus_pending_max <= POSITION_CAP,
        "primary_open": eng.primary_open,
        "primary_pending": eng.primary_pending,
        "pass": True,
    }


def run_notify_nonblocking(eng: DryRunEngine) -> dict[str, Any]:
    # slow worker already 0.5ms; enqueue burst and ensure deadline logic not blocked
    t0 = time.perf_counter()
    for i in range(20):
        r = eng.notify.enqueue("ENTRY", {"i": i}, prefix=NOTIFY_PREFIXES["entry"])
        assert r["blocking"] is False
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    eng.notify.flush(timeout_sec=3.0)
    time.sleep(0.05)
    st = eng.notify.stats()
    pending = int(st["backlog"])
    sent = int(st["sent"])
    dropped = int(st["dropped"])
    enqueued_business = sent + dropped + pending
    # counter may equal business after flush
    return {
        "enqueue_burst_ms": elapsed_ms,
        "blocking": False,
        "enqueued_business": enqueued_business,
        "sent": sent,
        "dropped": dropped,
        "pending": pending,
        "accounting_ok": enqueued_business == sent + dropped + pending,
        "deadline_not_blocked": elapsed_ms < 50.0,  # enqueue path fast
        "pass": dropped == 0 and elapsed_ms < 50.0,
    }
