"""Synthetic A–R suite. No Historical Capture. No PnL."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from . import (
    CHECKPOINT_STALE,
    EVALUABLE,
    GRID_SEC,
    NOT_EVALUABLE,
    SESSION_INVALID,
)
from .contract import (
    TrailMachine,
    entry_candidate,
    evaluate_trail,
    first_event_after,
    last_current_price_asof,
    ledger_rows,
    ols_log_trend_slope,
    snapshot_events_at_or_before,
    trail_checkpoints,
)

JST = ZoneInfo("Asia/Tokyo")
DAY = "20990106"  # synthetic calendar label only; not a Capture day


def _ep(h: int, m: int, s: int = 0) -> float:
    return datetime(2099, 1, 6, h, m, s, tzinfo=JST).timestamp()


def _px_at_g(symbol: str, g: float, prices: list[float], *, age: float = 0.0) -> list[dict[str, Any]]:
    marks = trail_checkpoints(g)
    assert len(prices) == len(marks)
    return [
        {"symbol": symbol, "event_time": c - float(age), "CurrentPrice": p}
        for c, p in zip(marks, prices)
    ]


def _rising() -> list[float]:
    return [100.0 + k for k in range(11)]


def _falling() -> list[float]:
    return [110.0 - k for k in range(11)]


CaseFn = Callable[[], dict[str, Any]]


def case_A() -> dict[str, Any]:
    g0 = _ep(10, 10, 0)
    g1 = g0 + GRID_SEC
    sm = TrailMachine("A")
    e0 = evaluate_trail(symbol="A", g=g0, day=DAY, events=_px_at_g("A", g0, _falling()))
    e1 = evaluate_trail(symbol="A", g=g1, day=DAY, events=_px_at_g("A", g1, _rising()))
    sm.on_eval(e0, day=DAY)
    a = sm.on_eval(e1, day=DAY)
    ok = (
        e0["status"] == EVALUABLE and e0["trail10_state"] is False
        and e1["status"] == EVALUABLE and e1["trail10_state"] is True
        and a is not None and abs(a.g - g1) < 1e-9
        and len(sm.history) == 1
    )
    return {"id": "A", "ok": ok, "detail": {"prev": e0["trail10_state"], "cur": e1["trail10_state"], "n": len(sm.history)}}


def case_B() -> dict[str, Any]:
    g = _ep(10, 10, 0)
    r = evaluate_trail(symbol="B", g=g, day=DAY, events=_px_at_g("B", g, _falling()))
    ok = r["status"] == EVALUABLE and r["trail10_state"] is False
    return {"id": "B", "ok": ok, "detail": r["trail10_state"]}


def case_C() -> dict[str, Any]:
    g = _ep(10, 10, 0)
    prices = [100.0, 90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 20.0, 10.0, 101.0]
    slope = ols_log_trend_slope(prices)
    r = evaluate_trail(symbol="C", g=g, day=DAY, events=_px_at_g("C", g, prices))
    ok = prices[-1] > prices[0] and slope <= 0 and r["trail10_state"] is False
    return {"id": "C", "ok": ok, "detail": f"slope={slope} state={r['trail10_state']}"}


def case_D() -> dict[str, Any]:
    g = _ep(10, 10, 0)
    prices = [100.0 + k for k in range(10)] + [100.0]
    slope = ols_log_trend_slope(prices)
    r = evaluate_trail(symbol="D", g=g, day=DAY, events=_px_at_g("D", g, prices))
    ok = slope > 0 and prices[-1] <= prices[0] and r["trail10_state"] is False
    return {"id": "D", "ok": ok, "detail": f"slope={slope} state={r['trail10_state']}"}


def case_E() -> dict[str, Any]:
    g = _ep(10, 10, 0)
    r = evaluate_trail(symbol="E", g=g, day=DAY, events=_px_at_g("E", g, [100.0] * 11))
    ok = r["trail10_state"] is False and abs(r["trend_slope"]) < 1e-15
    return {"id": "E", "ok": ok, "detail": r["trail10_state"]}


def case_F() -> dict[str, Any]:
    g0 = _ep(10, 10, 0)
    sm = TrailMachine("F")
    sm.on_eval(evaluate_trail(symbol="F", g=g0, day=DAY, events=_px_at_g("F", g0, _falling())), day=DAY)
    a1 = sm.on_eval(
        evaluate_trail(symbol="F", g=g0 + GRID_SEC, day=DAY, events=_px_at_g("F", g0 + GRID_SEC, _rising())),
        day=DAY,
    )
    a2 = sm.on_eval(
        evaluate_trail(symbol="F", g=g0 + 2 * GRID_SEC, day=DAY, events=_px_at_g("F", g0 + 2 * GRID_SEC, _rising())),
        day=DAY,
    )
    ok = a1 is not None and a2 is None and len(sm.history) == 1
    return {"id": "F", "ok": ok, "detail": len(sm.history)}


def case_G() -> dict[str, Any]:
    g0 = _ep(10, 10, 0)
    sm = TrailMachine("G")
    sm.on_eval(evaluate_trail(symbol="G", g=g0, day=DAY, events=_px_at_g("G", g0, _falling())), day=DAY)
    a = sm.on_eval(
        evaluate_trail(symbol="G", g=g0 + GRID_SEC, day=DAY, events=_px_at_g("G", g0 + GRID_SEC, _rising())),
        day=DAY,
    )
    ok = a is not None and len(sm.history) == 1
    return {"id": "G", "ok": ok, "detail": len(sm.history)}


def case_H() -> dict[str, Any]:
    g0 = _ep(10, 10, 0)
    sm = TrailMachine("H")
    sm.on_eval(evaluate_trail(symbol="H", g=g0, day=DAY, events=_px_at_g("H", g0, _falling())), day=DAY)
    a1 = sm.on_eval(
        evaluate_trail(symbol="H", g=g0 + GRID_SEC, day=DAY, events=_px_at_g("H", g0 + GRID_SEC, _rising())),
        day=DAY,
    )
    sm.on_eval(
        evaluate_trail(symbol="H", g=g0 + 2 * GRID_SEC, day=DAY, events=_px_at_g("H", g0 + 2 * GRID_SEC, _falling())),
        day=DAY,
    )
    a2 = sm.on_eval(
        evaluate_trail(symbol="H", g=g0 + 3 * GRID_SEC, day=DAY, events=_px_at_g("H", g0 + 3 * GRID_SEC, _rising())),
        day=DAY,
    )
    ok = a1 is not None and a2 is not None and a2.g != a1.g and len(sm.history) == 2
    return {"id": "H", "ok": ok, "detail": len(sm.history)}


def case_I() -> dict[str, Any]:
    g0 = _ep(10, 10, 0)
    sm = TrailMachine("I")
    sm.on_eval(evaluate_trail(symbol="I", g=g0, day=DAY, events=_px_at_g("I", g0, _falling())), day=DAY)
    sm.on_eval({"status": NOT_EVALUABLE, "g": g0 + GRID_SEC, "trail10_state": None}, day=DAY)
    a = sm.on_eval(
        evaluate_trail(symbol="I", g=g0 + 2 * GRID_SEC, day=DAY, events=_px_at_g("I", g0 + 2 * GRID_SEC, _rising())),
        day=DAY,
    )
    ok = a is None and len(sm.history) == 0
    return {"id": "I", "ok": ok, "detail": len(sm.history)}


def case_J() -> dict[str, Any]:
    g0 = _ep(10, 10, 0)
    sm = TrailMachine("J")
    sm.on_eval({"status": NOT_EVALUABLE, "g": g0, "trail10_state": None}, day=DAY)
    sm.on_eval(evaluate_trail(symbol="J", g=g0 + GRID_SEC, day=DAY, events=_px_at_g("J", g0 + GRID_SEC, _falling())), day=DAY)
    a = sm.on_eval(
        evaluate_trail(symbol="J", g=g0 + 2 * GRID_SEC, day=DAY, events=_px_at_g("J", g0 + 2 * GRID_SEC, _rising())),
        day=DAY,
    )
    ok = a is not None and len(sm.history) == 1
    return {"id": "J", "ok": ok, "detail": len(sm.history)}


def case_K() -> dict[str, Any]:
    g = _ep(10, 10, 0)
    events = _px_at_g("K", g, _rising(), age=61.0)
    r = evaluate_trail(symbol="K", g=g, day=DAY, events=events)
    ok = r["status"] == NOT_EVALUABLE and r["reason"] == CHECKPOINT_STALE
    return {"id": "K", "ok": ok, "detail": r.get("reason")}


def case_L() -> dict[str, Any]:
    g = _ep(10, 10, 0)
    marks = trail_checkpoints(g)
    c = marks[3]
    events = [
        {"symbol": "L", "event_time": c - 1.0, "CurrentPrice": 100.0},
        {"symbol": "L", "event_time": c + 0.001, "CurrentPrice": 9999.0},
    ]
    hit = last_current_price_asof(events, symbol="L", checkpoint=c)
    ok = hit["ok"] is True and hit["price"] == 100.0
    return {"id": "L", "ok": ok, "detail": hit}


def case_M() -> dict[str, Any]:
    g0 = _ep(10, 10, 0)
    sm_a = TrailMachine("A")
    false_a = evaluate_trail(symbol="A", g=g0, day=DAY, events=_px_at_g("A", g0, _falling()))
    sm_a.on_eval(false_a, day=DAY)
    # peer B fires; A stays FALSE / no fire
    sm_b = TrailMachine("B")
    sm_b.on_eval(evaluate_trail(symbol="B", g=g0, day=DAY, events=_px_at_g("B", g0, _falling())), day=DAY)
    a_b = sm_b.on_eval(
        evaluate_trail(symbol="B", g=g0 + GRID_SEC, day=DAY, events=_px_at_g("B", g0 + GRID_SEC, _rising())),
        day=DAY,
    )
    a_a = sm_a.on_eval(
        evaluate_trail(symbol="A", g=g0 + GRID_SEC, day=DAY, events=_px_at_g("A", g0 + GRID_SEC, _falling())),
        day=DAY,
    )
    ok = a_b is not None and a_a is None and len(sm_a.history) == 0
    return {"id": "M", "ok": ok, "detail": {"B": len(sm_b.history), "A": len(sm_a.history)}}


def case_N() -> dict[str, Any]:
    g0 = _ep(10, 10, 0)
    sm = TrailMachine("A")
    sm.on_eval(evaluate_trail(symbol="A", g=g0, day=DAY, events=_px_at_g("A", g0, _falling())), day=DAY)
    a = sm.on_eval(
        evaluate_trail(symbol="A", g=g0 + GRID_SEC, day=DAY, events=_px_at_g("A", g0 + GRID_SEC, _rising())),
        day=DAY,
    )
    cand = entry_candidate(a) if a else {}
    ok = a is not None and cand["symbol"] == "A" and cand["rerank_universe_forbidden"] is True
    return {"id": "N", "ok": ok, "detail": cand}


def case_O() -> dict[str, Any]:
    g = _ep(12, 35, 0)  # window start 12:25 — lunch, not PM (PM starts 12:30)
    r = evaluate_trail(symbol="O", g=g, day=DAY, events=_px_at_g("O", g, _rising()))
    ok = r["status"] == NOT_EVALUABLE and r["reason"] == SESSION_INVALID
    return {"id": "O", "ok": ok, "detail": r.get("reason")}


def case_P() -> dict[str, Any]:
    g = _ep(10, 10, 0)
    r = evaluate_trail(symbol="P", g=g, day=DAY, events=_px_at_g("P", g, _rising(), age=60.0))
    ok = r["status"] == EVALUABLE and r["trail10_state"] is True
    return {"id": "P", "ok": ok, "detail": r["status"]}


def case_Q() -> dict[str, Any]:
    g = _ep(10, 10, 0)
    events = [
        {"symbol": "Q", "event_time": g - 1.0, "CurrentPrice": 100.0},
        {"symbol": "Q", "event_time": g, "CurrentPrice": 101.0},
        {"symbol": "Q", "event_time": g + 0.4, "CurrentPrice": 9999.0},
    ]
    wake = first_event_after(g, [e["event_time"] for e in events])
    snap = snapshot_events_at_or_before(events, g)
    ok = wake == g + 0.4 and all(float(e["event_time"]) <= g + 1e-12 for e in snap) and len(snap) == 2
    return {"id": "Q", "ok": ok, "detail": {"wake": wake, "snap_n": len(snap)}}


def _run_ledger() -> list[dict[str, Any]]:
    g0 = _ep(10, 10, 0)
    sm = TrailMachine("R")
    sm.on_eval(evaluate_trail(symbol="R", g=g0, day=DAY, events=_px_at_g("R", g0, _falling())), day=DAY)
    sm.on_eval(
        evaluate_trail(symbol="R", g=g0 + GRID_SEC, day=DAY, events=_px_at_g("R", g0 + GRID_SEC, _rising())),
        day=DAY,
    )
    return ledger_rows(sm.history)


def _sha(rows: list[dict[str, Any]]) -> str:
    blob = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def case_R() -> dict[str, Any]:
    s1 = _sha(_run_ledger())
    s2 = _sha(_run_ledger())
    ok = s1 == s2 and len(s1) == 64
    return {"id": "R", "ok": ok, "detail": s1}


CASES: list[CaseFn] = [
    case_A, case_B, case_C, case_D, case_E, case_F, case_G, case_H,
    case_I, case_J, case_K, case_L, case_M, case_N, case_O, case_P,
    case_Q, case_R,
]


def run_suite() -> dict[str, Any]:
    results = []
    for fn in CASES:
        try:
            r = fn()
        except Exception as exc:  # noqa: BLE001
            r = {"id": fn.__name__, "ok": False, "detail": repr(exc)}
        results.append(r)
    passed = sum(1 for r in results if r["ok"])
    failed = sum(1 for r in results if not r["ok"])
    return {
        "passed": passed,
        "failed": failed,
        "n": len(results),
        "results": results,
    }
