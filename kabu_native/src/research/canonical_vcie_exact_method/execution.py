"""Execution-grade E0–E5 + 1tick adverse for VCIE entries."""
from __future__ import annotations

from typing import Any, Sequence

from research.canonical_vcie_exact_method.constants import COST_BPS, LOT
from research.canonical_vcie_exact_method.loader import Tick
from research.canonical_vcie_exact_method.opportunity import Candidate, first_valid_ask, path_metrics

E_DELAYS = {"E0": 0.0, "E1": 0.001, "E2": 0.10, "E3": 0.25, "E4": 0.50, "E5": 1.00}


def _yen(entry: float, exit_: float) -> float:
    raw = (exit_ - entry) * LOT
    cost = entry * LOT * COST_BPS / 10000.0 + exit_ * LOT * COST_BPS / 10000.0
    return raw - cost


def evaluate_execution(cands: Sequence[Candidate], streams: dict[str, list[Tick]], *, hold_sec: float = 120.0) -> dict[str, Any]:
    if not cands:
        return {"n": 0, "E0_E5": {}, "one_tick_adverse": None, "EXECUTION_RESOLUTION_BLOCKED": True, "resolution": "NO_ENTRIES"}

    intervals = []
    for c in cands[:80]:
        ticks = streams[c.stream_key]
        i = c.entry_idx
        if i + 1 < len(ticks):
            intervals.append((ticks[i + 1].ts - ticks[i].ts).total_seconds())
    med = sorted(intervals)[len(intervals) // 2] if intervals else None
    blocked = bool(med is not None and med > 0.5)

    out_e = {}
    for name, delay in E_DELAYS.items():
        pnls = []
        delays = []
        nevers = []
        mfes = []
        maes = []
        winners = 0
        cover = 0
        for c in cands:
            ticks = streams[c.stream_key]
            # decision = cross ~ use entry_idx as decision proxy; for E0 use same idx ask
            dec = c.entry_idx
            if name == "E0":
                ask = ticks[dec].board.canonical_best_ask
                aq = ticks[dec].board.canonical_ask_qty
                if not ask or ask <= 0 or (aq is not None and aq < LOT):
                    continue
                fill_i, fill_ask, dly = dec, float(ask), 0.0
            else:
                fill = first_valid_ask(ticks, dec, min_delay=delay)
                if fill is None:
                    continue
                fill_i, fill_ask, dly = fill
            cover += 1
            m = path_metrics(ticks, fill_i, fill_ask, max_sec=hold_sec)
            if not m.get("evaluable"):
                continue
            pnls.append(float(m["terminal_pnl_yen"]))
            delays.append(dly)
            nevers.append(1 if m["never_profitable"] else 0)
            mfes.append(m["mfe"])
            maes.append(m["mae"])
            if m["winner"]:
                winners += 1
        wins = sum(p for p in pnls if p > 0)
        losses = -sum(p for p in pnls if p < 0)
        pf = (wins / losses) if losses > 0 else (float("inf") if wins > 0 else None)
        ds = sorted(delays)
        out_e[name] = {
            "n": len(pnls),
            "coverage": cover / len(cands) if cands else 0,
            "pnl": sum(pnls) if pnls else 0.0,
            "PF": round(pf, 4) if isinstance(pf, float) and pf != float("inf") else pf,
            "delay_median": ds[len(ds) // 2] if ds else None,
            "delay_p90": ds[int((len(ds) - 1) * 0.9)] if ds else None,
            "never_profitable": (sum(nevers) / len(nevers)) if nevers else None,
            "avg_mfe": (sum(mfes) / len(mfes)) if mfes else None,
            "avg_mae": (sum(maes) / len(maes)) if maes else None,
            "winner_rate": winners / len(pnls) if pnls else None,
        }

    # 1tick adverse
    adverse = []
    for c in cands:
        ticks = streams[c.stream_key]
        ask = c.entry_ask
        tick = 0.5 if ask >= 1000 else 0.1
        m = path_metrics(ticks, c.entry_idx, ask + tick, max_sec=hold_sec)
        if m.get("evaluable"):
            adverse.append(float(m["terminal_pnl_yen"]))
    aw = sum(p for p in adverse if p > 0)
    al = -sum(p for p in adverse if p < 0)
    apf = (aw / al) if al > 0 else (float("inf") if aw > 0 else None)

    return {
        "n": len(cands),
        "median_push_interval_sec": med,
        "EXECUTION_RESOLUTION_BLOCKED": blocked,
        "resolution": "EXECUTION_RESOLUTION_BLOCKED" if blocked else "OK",
        "E0_E5": out_e,
        "one_tick_adverse": {
            "n": len(adverse),
            "pnl": sum(adverse) if adverse else 0.0,
            "PF": round(apf, 4) if isinstance(apf, float) and apf != float("inf") else apf,
        },
        "resilience": "EXECUTION_FRAGILE" if blocked or (out_e.get("E2") or {}).get("PF") in (None, 0) else (
            "EXECUTION_RESILIENT" if isinstance((out_e.get("E2") or {}).get("PF"), (int, float)) and (out_e["E2"]["PF"] or 0) > 1 else "EXECUTION_FRAGILE"
        ),
    }
