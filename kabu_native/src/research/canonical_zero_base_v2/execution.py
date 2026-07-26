"""Execution-grade E0–E5 / S0–S5 evaluation — must produce numeric results."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from research.canonical_zero_base_v2.constants import COST_BPS, LOT
from research.canonical_zero_base_v2.loader import Tick

E_DELAYS = {"E0": 0.0, "E1": 0.001, "E2": 0.10, "E3": 0.25, "E4": 0.50, "E5": 1.00}
S_DELAYS = {"S0": 0.0, "S1": 0.001, "S2": 0.10, "S3": 0.25, "S4": 0.50, "S5": 1.00}


def _find_ask(ticks: Sequence[Tick], idx: int, *, delay: float, e0: bool = False) -> dict[str, Any]:
    t0 = ticks[idx].ts
    if e0:
        ask = ticks[idx].board.canonical_best_ask
        aq = ticks[idx].board.canonical_ask_qty
        st = "OK" if ask and ask > 0 else "NOT_EVALUABLE"
        if aq is not None and aq < LOT:
            st = "NOT_FULLY_EVALUABLE_QTY"
        return {"price": ask, "idx": idx, "status": st, "delay": 0.0}
    start = idx if delay <= 0 else idx + 1
    for j in range(start, min(len(ticks), idx + 80)):
        dt = (ticks[j].ts - t0).total_seconds()
        if delay > 0 and dt < delay:
            continue
        if delay <= 0.001 and j == idx:
            continue
        ask = ticks[j].board.canonical_best_ask
        aq = ticks[j].board.canonical_ask_qty
        if ask is None or ask <= 0:
            continue
        st = "NOT_FULLY_EVALUABLE_QTY" if (aq is not None and aq < LOT) else "OK"
        return {"price": ask, "idx": j, "status": st, "delay": dt}
    return {"price": None, "idx": None, "status": "NOT_EVALUABLE", "delay": None}


def _find_bid(ticks: Sequence[Tick], idx: int, *, delay: float, s0: bool = False) -> dict[str, Any]:
    t0 = ticks[idx].ts
    if s0:
        bid = ticks[idx].board.canonical_best_bid
        bq = ticks[idx].board.canonical_bid_qty
        st = "OK" if bid and bid > 0 else "NOT_EVALUABLE"
        if bq is not None and bq < LOT:
            st = "NOT_FULLY_EVALUABLE_QTY"
        return {"price": bid, "idx": idx, "status": st, "delay": 0.0}
    for j in range(idx + 1, min(len(ticks), idx + 80)):
        dt = (ticks[j].ts - t0).total_seconds()
        if delay > 0.001 and dt < delay:
            continue
        bid = ticks[j].board.canonical_best_bid
        bq = ticks[j].board.canonical_bid_qty
        if bid is None or bid <= 0:
            continue
        st = "NOT_FULLY_EVALUABLE_QTY" if (bq is not None and bq < LOT) else "OK"
        return {"price": bid, "idx": j, "status": st, "delay": dt}
    return {"price": None, "idx": None, "status": "NOT_EVALUABLE", "delay": None}


def _yen(entry: float, exit_: float) -> float:
    raw = (exit_ - entry) * LOT
    cost = entry * LOT * COST_BPS / 10000.0 + exit_ * LOT * COST_BPS / 10000.0
    return raw - cost


def evaluate_latency_pairs(
    entries: Sequence[dict[str, Any]],
    streams: dict[str, list[Tick]],
    *,
    hold_sec: float = 60.0,
) -> dict[str, Any]:
    """For each entry, score E*/S* roundtrips over a fixed hold (execution sensitivity)."""
    if not entries:
        return {
            "n": 0,
            "resolution": "NO_ENTRIES",
            "E0_E5": {},
            "S0_S5": {},
            "pairs": {},
            "one_tick_adverse": None,
            "EXECUTION_RESOLUTION_BLOCKED": True,
        }
    # estimate median push interval
    intervals = []
    for e in entries[:50]:
        ticks = streams.get(e["stream_key"]) or []
        i = e["entry_idx"]
        if i + 1 < len(ticks):
            intervals.append((ticks[i + 1].ts - ticks[i].ts).total_seconds())
    med_int = sorted(intervals)[len(intervals) // 2] if intervals else None
    resolution_blocked = bool(med_int is not None and med_int > 0.5)

    pair_stats: dict[str, dict[str, Any]] = {}
    for ek, ed in E_DELAYS.items():
        for sk, sd in S_DELAYS.items():
            if (ek, sk) not in (("E1", "S1"), ("E2", "S2"), ("E4", "S4"), ("E0", "S0"), ("E5", "S5")):
                continue
            pnls = []
            cover = 0
            n_ok = 0
            delays = []
            for e in entries:
                ticks = streams[e["stream_key"]]
                i = e["entry_idx"]
                ef = _find_ask(ticks, i, delay=ed, e0=(ek == "E0"))
                if ef["price"] is None:
                    continue
                cover += 1
                # exit decision at hold_sec after entry fill idx
                fill_i = ef["idx"] if ef["idx"] is not None else i
                # find exit decision time
                t_fill = ticks[fill_i].ts
                exit_dec = fill_i
                for j in range(fill_i, len(ticks)):
                    if (ticks[j].ts - t_fill).total_seconds() >= hold_sec:
                        exit_dec = j
                        break
                sf = _find_bid(ticks, exit_dec, delay=sd, s0=(sk == "S0"))
                if sf["price"] is None:
                    continue
                n_ok += 1
                pnls.append(_yen(float(ef["price"]), float(sf["price"])))
                if ef.get("delay") is not None:
                    delays.append(float(ef["delay"]))
            wins = sum(p for p in pnls if p > 0)
            losses = -sum(p for p in pnls if p < 0)
            pf = (wins / losses) if losses > 0 else (float("inf") if wins > 0 else None)
            pair_stats[f"{ek}/{sk}"] = {
                "n": n_ok,
                "coverage": cover / len(entries) if entries else 0,
                "pnl": sum(pnls) if pnls else 0.0,
                "PF": round(pf, 4) if isinstance(pf, float) and pf != float("inf") else pf,
                "mean_entry_delay": (sum(delays) / len(delays)) if delays else None,
            }

    # 1tick adverse: entry ask + 1 tick, exit bid - 0
    adverse = []
    for e in entries:
        ticks = streams[e["stream_key"]]
        i = e["entry_idx"]
        ask = ticks[i].board.canonical_best_ask
        if ask is None:
            continue
        # rough tick
        tick = 0.5 if ask >= 1000 else 0.1
        ef = ask + tick
        # exit at +60s bid
        t0 = ticks[i].ts
        bid = None
        for j in range(i + 1, len(ticks)):
            if (ticks[j].ts - t0).total_seconds() >= hold_sec:
                bid = ticks[j].board.canonical_best_bid
                break
        if bid:
            adverse.append(_yen(ef, float(bid)))
    aw = sum(p for p in adverse if p > 0)
    al = -sum(p for p in adverse if p < 0)
    apf = (aw / al) if al > 0 else (float("inf") if aw > 0 else None)

    return {
        "n": len(entries),
        "median_push_interval_sec": med_int,
        "resolution": "EXECUTION_RESOLUTION_BLOCKED" if resolution_blocked else "OK",
        "EXECUTION_RESOLUTION_BLOCKED": resolution_blocked,
        "E0_E5": {k: pair_stats.get(f"{k}/S1") for k in E_DELAYS},
        "S0_S5": {k: pair_stats.get(f"E1/{k}") for k in S_DELAYS},
        "pairs": pair_stats,
        "one_tick_adverse": {
            "n": len(adverse),
            "pnl": sum(adverse) if adverse else 0.0,
            "PF": round(apf, 4) if isinstance(apf, float) and apf != float("inf") else apf,
        },
        "fill_coverage_E1": (pair_stats.get("E1/S1") or {}).get("coverage"),
    }
