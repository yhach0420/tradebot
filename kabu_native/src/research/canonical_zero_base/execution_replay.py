"""Execution-grade fill scenarios E0–E5 / S0–S5 on canonical Ask/Bid."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from research.canonical_zero_base.canonical_loader import Tick
from research.canonical_zero_base.constants import LOT


def _find_ask(ticks: Sequence[Tick], start_idx: int, *, min_delay_sec: float) -> tuple[Optional[float], Optional[int], str]:
    t0 = ticks[start_idx].ts
    for j in range(start_idx, min(len(ticks), start_idx + 200)):
        t = ticks[j]
        dt = (t.ts - t0).total_seconds()
        if dt < min_delay_sec and j != start_idx:
            continue
        if min_delay_sec == 0 and j != start_idx and dt > 0:
            # E0 same payload only
            if j != start_idx:
                break
        ask = t.board.canonical_best_ask
        aq = t.board.canonical_ask_qty
        if ask is None or ask <= 0:
            continue
        if aq is not None and aq < LOT:
            # walk sell ladder if present via depth — only Sell1 available in board; mark partial
            return ask, j, "NOT_FULLY_EVALUABLE_QTY"
        if min_delay_sec == 0:
            return ask, j, "OK"  # E0
        if dt >= min_delay_sec or (min_delay_sec > 0 and j > start_idx):
            return ask, j, "OK"
    return None, None, "NOT_EVALUABLE"


def _find_bid(ticks: Sequence[Tick], start_idx: int, *, min_delay_sec: float) -> tuple[Optional[float], Optional[int], str]:
    t0 = ticks[start_idx].ts
    for j in range(start_idx, min(len(ticks), start_idx + 200)):
        t = ticks[j]
        dt = (t.ts - t0).total_seconds()
        if j == start_idx and min_delay_sec > 0:
            continue
        if dt < min_delay_sec:
            continue
        bid = t.board.canonical_best_bid
        bq = t.board.canonical_bid_qty
        if bid is None or bid <= 0:
            continue
        if bq is not None and bq < LOT:
            return bid, j, "NOT_FULLY_EVALUABLE_QTY"
        return bid, j, "OK"
    return None, None, "NOT_EVALUABLE"


SCENARIOS = {
    "E0": 0.0,
    "E1": 0.001,  # first later tick (~immediate next)
    "E2": 0.10,
    "E3": 0.25,
    "E4": 0.50,
    "E5": 1.00,
}


def entry_fill(ticks: Sequence[Tick], decision_idx: int, scenario: str) -> dict[str, Any]:
    delay = SCENARIOS[scenario]
    if scenario == "E0":
        ask = ticks[decision_idx].board.canonical_best_ask
        aq = ticks[decision_idx].board.canonical_ask_qty
        ok = "OK" if ask and ask > 0 else "NOT_EVALUABLE"
        if aq is not None and aq < LOT:
            ok = "NOT_FULLY_EVALUABLE_QTY"
        return {"price": ask, "idx": decision_idx, "status": ok}
    # E1 = first valid after decision
    if scenario == "E1":
        for j in range(decision_idx + 1, min(len(ticks), decision_idx + 50)):
            ask = ticks[j].board.canonical_best_ask
            aq = ticks[j].board.canonical_ask_qty
            if ask and ask > 0:
                st = "NOT_FULLY_EVALUABLE_QTY" if (aq is not None and aq < LOT) else "OK"
                return {"price": ask, "idx": j, "status": st}
        return {"price": None, "idx": None, "status": "NOT_EVALUABLE"}
    ask, idx, st = _find_ask(ticks, decision_idx, min_delay_sec=delay)
    return {"price": ask, "idx": idx, "status": st}


def exit_fill(ticks: Sequence[Tick], decision_idx: int, scenario: str) -> dict[str, Any]:
    mapping = {"S0": 0.0, "S1": 0.001, "S2": 0.10, "S3": 0.25, "S4": 0.50, "S5": 1.00}
    delay = mapping[scenario]
    if scenario == "S0":
        bid = ticks[decision_idx].board.canonical_best_bid
        bq = ticks[decision_idx].board.canonical_bid_qty
        ok = "OK" if bid and bid > 0 else "NOT_EVALUABLE"
        if bq is not None and bq < LOT:
            ok = "NOT_FULLY_EVALUABLE_QTY"
        return {"price": bid, "idx": decision_idx, "status": ok}
    if scenario == "S1":
        for j in range(decision_idx + 1, min(len(ticks), decision_idx + 50)):
            bid = ticks[j].board.canonical_best_bid
            bq = ticks[j].board.canonical_bid_qty
            if bid and bid > 0:
                st = "NOT_FULLY_EVALUABLE_QTY" if (bq is not None and bq < LOT) else "OK"
                return {"price": bid, "idx": j, "status": st}
        return {"price": None, "idx": None, "status": "NOT_EVALUABLE"}
    bid, idx, st = _find_bid(ticks, decision_idx, min_delay_sec=delay)
    return {"price": bid, "idx": idx, "status": st}
