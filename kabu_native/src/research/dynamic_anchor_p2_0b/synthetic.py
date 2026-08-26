"""Synthetic A–O suite. No Historical Capture. No PnL."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from . import (
    CONFIRMATION_NOT_EVALUABLE,
    CONFIRMED,
    GRID_SEC,
    REJECTED,
    SESSION_INCOMPLETE,
    VOLUME_PERCENTILE_MIN,
)
from .contract import (
    DynamicAnchor,
    SymbolMachine,
    checkpoint_epochs,
    confirmation_window,
    entry_candidate,
    evaluate_confirmation,
    false_to_true_edges,
    first_event_after,
    last_current_price_asof,
    ols_log_trend_slope,
    preentry_snapshot_events,
    t1_raw,
)

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260803"  # calendar labels only; not a Capture read


def _ep(h: int, m: int, s: int = 0) -> float:
    return datetime(2026, 8, 3, h, m, s, tzinfo=JST).timestamp()


def _px_events(symbol: str, t0: float, prices: list[float]) -> list[dict[str, Any]]:
    marks = checkpoint_epochs(t0)
    assert len(prices) == len(marks)
    return [
        {"symbol": symbol, "event_time": c, "CurrentPrice": p}
        for c, p in zip(marks, prices)
    ]


def _grid_row(symbol: str, epoch: float, raw_true: bool, **extra: Any) -> dict[str, Any]:
    volp = VOLUME_PERCENTILE_MIN if raw_true else 0.1
    row = {
        "symbol": symbol,
        "grid_epoch": epoch,
        "date": DAY,
        "session": "AM" if datetime.fromtimestamp(epoch, JST).hour < 12 else "PM",
        "feature_status": "OK",
        "relative_status": "OK",
        "rs_universe_n": 20,
        "volume_percentile_60s": volp,
    }
    row.update(extra)
    return row


CaseFn = Callable[[], dict[str, Any]]


def case_A_rising() -> dict[str, Any]:
    t0 = _ep(10, 0)
    t1 = t0 + 600
    prices = [100.0 + k for k in range(11)]
    r = evaluate_confirmation(symbol="A", t0=t0, t1=t1, events=_px_events("A", t0, prices))
    ok = r["status"] == CONFIRMED and r["trend_slope"] > 0 and r["p10_gt_p0"]
    return {"id": "A", "ok": ok, "detail": r["status"]}


def case_B_falling() -> dict[str, Any]:
    t0 = _ep(10, 0)
    t1 = t0 + 600
    prices = [110.0 - k for k in range(11)]
    r = evaluate_confirmation(symbol="B", t0=t0, t1=t1, events=_px_events("B", t0, prices))
    ok = r["status"] == REJECTED and r["p10_gt_p0"] is False
    return {"id": "B", "ok": ok, "detail": r["status"]}


def case_C_fall_then_spike() -> dict[str, Any]:
    t0 = _ep(10, 0)
    t1 = t0 + 600
    prices = [100.0, 90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 20.0, 10.0, 101.0]
    slope = ols_log_trend_slope(prices)
    r = evaluate_confirmation(symbol="C", t0=t0, t1=t1, events=_px_events("C", t0, prices))
    ok = (
        prices[-1] > prices[0]
        and slope <= 0
        and r["status"] == REJECTED
    )
    return {"id": "C", "ok": ok, "detail": f"status={r['status']} slope={slope}"}


def case_D_rise_then_collapse() -> dict[str, Any]:
    t0 = _ep(10, 0)
    t1 = t0 + 600
    prices = [100.0 + k for k in range(10)] + [100.0]
    slope = ols_log_trend_slope(prices)
    r = evaluate_confirmation(symbol="D", t0=t0, t1=t1, events=_px_events("D", t0, prices))
    ok = slope > 0 and prices[-1] <= prices[0] and r["status"] == REJECTED
    return {"id": "D", "ok": ok, "detail": f"status={r['status']} slope={slope}"}


def case_E_flat() -> dict[str, Any]:
    t0 = _ep(10, 0)
    t1 = t0 + 600
    prices = [100.0] * 11
    r = evaluate_confirmation(symbol="E", t0=t0, t1=t1, events=_px_events("E", t0, prices))
    ok = r["status"] == REJECTED and abs(r["trend_slope"]) < 1e-15 and r["p10_gt_p0"] is False
    return {"id": "E", "ok": ok, "detail": r["status"]}


def case_F_stale() -> dict[str, Any]:
    t0 = _ep(10, 0)
    t1 = t0 + 600
    marks = checkpoint_epochs(t0)
    events = _px_events("F", t0, [100.0 + k for k in range(11)])
    # Last print at-or-before checkpoint 5 must be >60s old (age==60 is still allowed).
    del events[5]
    events[4]["event_time"] = marks[5] - 61.0
    r = evaluate_confirmation(symbol="F", t0=t0, t1=t1, events=events)
    ok = r["status"] == CONFIRMATION_NOT_EVALUABLE and r["reason"] == "CHECKPOINT_STALE"
    return {"id": "F", "ok": ok, "detail": r.get("reason")}


def case_G_future_ignored() -> dict[str, Any]:
    t0 = _ep(10, 0)
    marks = checkpoint_epochs(t0)
    c = marks[3]
    events = [
        {"symbol": "G", "event_time": c - 1.0, "CurrentPrice": 100.0},
        {"symbol": "G", "event_time": c + 0.001, "CurrentPrice": 9999.0},
    ]
    hit = last_current_price_asof(events, symbol="G", checkpoint=c, t1=t0 + 600)
    ok = hit["ok"] is True and hit["price"] == 100.0
    return {"id": "G", "ok": ok, "detail": hit}


def case_H_1120_complete() -> dict[str, Any]:
    t0 = _ep(11, 20)
    w = confirmation_window(DAY, t0)
    ok = w["status"] == "WINDOW_OK" and abs(w["t1"] - _ep(11, 30)) < 1e-6
    return {"id": "H", "ok": ok, "detail": w}


def case_I_1121_incomplete() -> dict[str, Any]:
    t0 = _ep(11, 21)
    w = confirmation_window(DAY, t0)
    ok = w["status"] == SESSION_INCOMPLETE
    return {"id": "I", "ok": ok, "detail": w["status"]}


def case_J_1450_complete() -> dict[str, Any]:
    t0 = _ep(14, 50)
    w = confirmation_window(DAY, t0)
    ok = w["status"] == "WINDOW_OK" and abs(w["t1"] - _ep(15, 0)) < 1e-6
    return {"id": "J", "ok": ok, "detail": w}


def case_K_1451_incomplete() -> dict[str, Any]:
    t0 = _ep(14, 51)
    w = confirmation_window(DAY, t0)
    ok = w["status"] == SESSION_INCOMPLETE
    return {"id": "K", "ok": ok, "detail": w["status"]}


def case_L_persist_true_one_edge() -> dict[str, Any]:
    g0 = _ep(10, 0, 0)
    rows = [
        _grid_row("L", g0, False),
        _grid_row("L", g0 + GRID_SEC, True),
        _grid_row("L", g0 + 2 * GRID_SEC, True),
        _grid_row("L", g0 + 3 * GRID_SEC, True),
    ]
    edges = false_to_true_edges(rows)
    ok = len(edges) == 1 and abs(edges[0]["t0"] - (g0 + GRID_SEC)) < 1e-9
    return {"id": "L", "ok": ok, "detail": len(edges)}


def case_M_rearm_true_false_true() -> dict[str, Any]:
    sm = SymbolMachine("M")
    g0 = _ep(10, 0)
    # FALSE → ARMED
    assert sm.on_grid(raw=False, grid_epoch=g0, day=DAY) is None
    # TRUE → first anchor
    a1 = sm.on_grid(raw=True, grid_epoch=g0 + GRID_SEC, day=DAY)
    assert a1 is not None and sm.state == "ANCHOR_ACTIVE"
    sm.close_active(CONFIRMED)
    # still TRUE after DISARM: must not fire
    a_skip = sm.on_grid(raw=True, grid_epoch=g0 + 2 * GRID_SEC, day=DAY)
    # FALSE → ARMED
    sm.on_grid(raw=False, grid_epoch=g0 + 3 * GRID_SEC, day=DAY)
    a2 = sm.on_grid(raw=True, grid_epoch=g0 + 4 * GRID_SEC, day=DAY)
    ok = a_skip is None and a2 is not None and a2.t0 != a1.t0
    return {"id": "M", "ok": ok, "detail": {"a1": a1.t0 if a1 else None, "a2": a2.t0 if a2 else None}}


def case_N_peer_no_edge_on_A() -> dict[str, Any]:
    g0 = _ep(10, 0)
    rows = [
        _grid_row("A", g0, False),
        _grid_row("A", g0 + GRID_SEC, False),
        _grid_row("B", g0, False),
        _grid_row("B", g0 + GRID_SEC, True),
    ]
    edges = false_to_true_edges(rows)
    a_edges = [e for e in edges if e["symbol"] == "A"]
    b_edges = [e for e in edges if e["symbol"] == "B"]
    ok = len(a_edges) == 0 and len(b_edges) == 1
    return {"id": "N", "ok": ok, "detail": {"nA": len(a_edges), "nB": len(b_edges)}}


def case_O_ownership_stays_A() -> dict[str, Any]:
    t0 = _ep(10, 0)
    anc = DynamicAnchor(symbol="A", t0=t0, t1=t0 + 600, date=DAY, session="AM", status=CONFIRMED)
    cand = entry_candidate(anc)
    # simulated illegal rerank target
    other = "B"
    ok = cand["symbol"] == "A" and cand["symbol"] != other and cand["rerank_universe_forbidden"] is True
    return {"id": "O", "ok": ok, "detail": cand}


CASES: list[CaseFn] = [
    case_A_rising,
    case_B_falling,
    case_C_fall_then_spike,
    case_D_rise_then_collapse,
    case_E_flat,
    case_F_stale,
    case_G_future_ignored,
    case_H_1120_complete,
    case_I_1121_incomplete,
    case_J_1450_complete,
    case_K_1451_incomplete,
    case_L_persist_true_one_edge,
    case_M_rearm_true_false_true,
    case_N_peer_no_edge_on_A,
    case_O_ownership_stays_A,
]


def run_suite() -> dict[str, Any]:
    results = []
    for fn in CASES:
        try:
            r = fn()
        except Exception as exc:  # noqa: BLE001 — surface in report
            r = {"id": fn.__name__, "ok": False, "detail": repr(exc)}
        results.append(r)
    passed = sum(1 for r in results if r["ok"])
    failed = sum(1 for r in results if not r["ok"])
    return {
        "passed": passed,
        "failed": failed,
        "n": len(results),
        "results": results,
        "grid_sec": GRID_SEC,
        "volume_percentile_min": VOLUME_PERCENTILE_MIN,
    }


def extra_contract_checks() -> list[dict[str, Any]]:
    """Additional leak / cadence / snapshot checks (not A–O)."""
    out = []
    # missing → FALSE
    out.append({
        "id": "missing_false",
        "ok": t1_raw({"feature_status": "OK", "relative_status": "OK", "rs_universe_n": 20}) is False,
    })
    # no imputation of NaN
    out.append({
        "id": "nan_false",
        "ok": t1_raw({
            "feature_status": "OK", "relative_status": "OK", "rs_universe_n": 20,
            "volume_percentile_60s": float("nan"),
        }) is False,
    })
    # first event > t1, equal t1 does not fire
    t1 = _ep(10, 10)
    out.append({
        "id": "decision_fire_strict_gt",
        "ok": first_event_after(t1, [t1, t1 + 0.5]) == t1 + 0.5,
    })
    # snapshot strips t>t1
    ev = [
        {"event_time": t1 - 1, "CurrentPrice": 1, "symbol": "A"},
        {"event_time": t1 + 1, "CurrentPrice": 9, "symbol": "A"},
    ]
    snap = preentry_snapshot_events(ev, t1)
    out.append({
        "id": "snapshot_no_future",
        "ok": len(snap) == 1 and snap[0]["CurrentPrice"] == 1,
    })
    # GRID cadence is X14 10s
    from research.e1_x14_board_independent_signal import GRID_SEC as X14_GRID
    out.append({"id": "cadence_is_x14_10s", "ok": GRID_SEC == 10 and GRID_SEC == X14_GRID})
    return out
