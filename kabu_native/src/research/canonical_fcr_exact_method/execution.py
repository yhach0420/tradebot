"""E0–E5 + 1tick adverse for FCR."""
from __future__ import annotations

from typing import Any, Sequence

from research.canonical_fcr_exact_method.constants import LOT
from research.canonical_fcr_exact_method.loader import Tick
from research.canonical_fcr_exact_method.opportunity import Candidate, first_valid_ask, path_metrics

E_DELAYS = {"E0": 0.0, "E1": 0.001, "E2": 0.10, "E3": 0.25, "E4": 0.50, "E5": 1.00}


def evaluate_execution(cands: Sequence[Candidate], streams: dict[str, list[Tick]], *, hold_sec: float = 180.0) -> dict[str, Any]:
    if not cands:
        return {"n": 0, "E0_E5": {}, "one_tick_adverse": None, "EXECUTION_RESOLUTION_BLOCKED": True, "resilience": "EXECUTION_FRAGILE"}
    intervals = []
    for c in cands[:60]:
        ticks = streams[c.stream_key]
        if c.entry_idx + 1 < len(ticks):
            intervals.append((ticks[c.entry_idx + 1].ts - ticks[c.entry_idx].ts).total_seconds())
    med = sorted(intervals)[len(intervals) // 2] if intervals else None
    blocked = bool(med is not None and med > 0.5)
    out = {}
    for name, delay in E_DELAYS.items():
        pnls, delays, nevers, mfes, maes = [], [], [], [], []
        winners = cover = 0
        for c in cands:
            ticks = streams[c.stream_key]
            dec = c.entry_idx
            if name == "E0":
                ask = ticks[dec].board.canonical_best_ask
                aq = ticks[dec].board.canonical_ask_qty
                if not ask or (aq is not None and aq < LOT):
                    continue
                fi, fa, dly = dec, float(ask), 0.0
            else:
                fill = first_valid_ask(ticks, dec, min_delay=delay)
                if fill is None:
                    continue
                fi, fa, dly = fill
            cover += 1
            m = path_metrics(ticks, fi, fa, max_sec=hold_sec)
            if not m.get("evaluable"):
                continue
            pnls.append(float(m["terminal_pnl_yen"]))
            delays.append(dly)
            nevers.append(1 if m["never_profitable"] else 0)
            mfes.append(m["mfe"])
            maes.append(m["mae"])
            winners += 1 if m["winner"] else 0
        w = sum(p for p in pnls if p > 0)
        l = -sum(p for p in pnls if p < 0)
        pf = (w / l) if l > 0 else (float("inf") if w > 0 else None)
        ds = sorted(delays)
        out[name] = {
            "n": len(pnls),
            "coverage": cover / len(cands),
            "pnl": sum(pnls) if pnls else 0.0,
            "PF": round(pf, 4) if isinstance(pf, float) and pf != float("inf") else pf,
            "mean": (sum(pnls) / len(pnls)) if pnls else None,
            "delay_median": ds[len(ds) // 2] if ds else None,
            "delay_p90": ds[int((len(ds) - 1) * 0.9)] if ds else None,
            "never_profitable": (sum(nevers) / len(nevers)) if nevers else None,
            "avg_mfe": (sum(mfes) / len(mfes)) if mfes else None,
            "avg_mae": (sum(maes) / len(maes)) if maes else None,
            "winner_rate": winners / len(pnls) if pnls else None,
        }
    adverse = []
    for c in cands:
        tick = 0.5 if c.entry_ask >= 1000 else 0.1
        m = path_metrics(streams[c.stream_key], c.entry_idx, c.entry_ask + tick, max_sec=hold_sec)
        if m.get("evaluable"):
            adverse.append(float(m["terminal_pnl_yen"]))
    aw, al = sum(p for p in adverse if p > 0), -sum(p for p in adverse if p < 0)
    apf = (aw / al) if al > 0 else (float("inf") if aw > 0 else None)
    e2pf = (out.get("E2") or {}).get("PF")
    resilient = (not blocked) and isinstance(e2pf, (int, float)) and e2pf > 1
    return {
        "n": len(cands),
        "median_push_interval_sec": med,
        "EXECUTION_RESOLUTION_BLOCKED": blocked,
        "E0_E5": out,
        "one_tick_adverse": {"n": len(adverse), "pnl": sum(adverse) if adverse else 0.0,
                             "PF": round(apf, 4) if isinstance(apf, float) and apf != float("inf") else apf},
        "resilience": "EXECUTION_RESILIENT" if resilient else "EXECUTION_FRAGILE",
    }
