"""ENTRY opportunity labels — E1 Ask entry, future Bid path."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from research.canonical_vcie_exact_method.constants import COST_BPS, LOT
from research.canonical_vcie_exact_method.loader import Tick


HORIZONS = (5, 10, 15, 30, 60, 120, 180, 300)


@dataclass
class Candidate:
    arm: str
    day: str
    symbol: str
    episode_id: str
    entry_idx: int
    entry_time: Any
    entry_ask: float
    stream_key: str
    breakout_level: float
    features: dict


def first_valid_ask(ticks: Sequence[Tick], decision_idx: int, *, min_delay: float = 0.0) -> Optional[tuple[int, float, float]]:
    t0 = ticks[decision_idx].ts
    start = decision_idx if min_delay <= 0 else decision_idx + 1
    for j in range(start, min(len(ticks), decision_idx + 80)):
        dt = (ticks[j].ts - t0).total_seconds()
        if min_delay > 0 and dt < min_delay:
            continue
        if min_delay == 0 and j == decision_idx:
            ask = ticks[j].board.canonical_best_ask
            aq = ticks[j].board.canonical_ask_qty
            if ask and ask > 0 and (aq is None or aq >= LOT):
                return j, float(ask), 0.0
            continue
        if j == decision_idx:
            continue
        ask = ticks[j].board.canonical_best_ask
        aq = ticks[j].board.canonical_ask_qty
        if ask and ask > 0 and (aq is None or aq >= LOT):
            return j, float(ask), dt
    return None


def path_metrics(ticks: Sequence[Tick], entry_idx: int, entry_ask: float, *, max_sec: float = 300.0) -> dict[str, Any]:
    if entry_ask <= 0 or entry_idx >= len(ticks) - 1:
        return {"evaluable": False}
    t0 = ticks[entry_idx].ts
    mfe = mae = 0.0
    t_mfe = t_mae = None
    never = True
    cost = COST_BPS / 10000.0
    cost_rec = None
    first_adv = None
    last_bid = None
    pos_dur = neg_dur = 0.0
    last_ts = t0
    high_updates = 0
    peak_px = entry_ask
    for j in range(entry_idx + 1, len(ticks)):
        t = ticks[j]
        dt = (t.ts - t0).total_seconds()
        if dt > max_sec:
            break
        bid = t.board.canonical_best_bid
        if bid is None or bid <= 0:
            continue
        last_bid = bid
        ret = (bid - entry_ask) / entry_ask * 100.0
        if ret > mfe:
            mfe, t_mfe = ret, dt
        if ret < mae:
            mae, t_mae = ret, dt
        if never and bid > entry_ask * (1 + cost):
            never = False
            cost_rec = dt
        if first_adv is None and ret < 0:
            first_adv = dt
        step = (t.ts - last_ts).total_seconds()
        if ret > 0:
            pos_dur += step
        elif ret < 0:
            neg_dur += step
        if t.px and t.px > peak_px:
            peak_px = t.px
            high_updates += 1
        last_ts = t.ts
    if last_bid is None:
        return {"evaluable": False}
    raw = (last_bid - entry_ask) * LOT
    c = entry_ask * LOT * COST_BPS / 10000.0 + last_bid * LOT * COST_BPS / 10000.0
    yen = raw - c
    return {
        "evaluable": True,
        "mfe": mfe,
        "mae": mae,
        "net_mfe_after_cost": mfe - COST_BPS / 100.0,
        "terminal_pnl_yen": yen,
        "time_to_mfe": t_mfe,
        "time_to_mae": t_mae,
        "never_profitable": never,
        "early_adverse": bool(first_adv is not None and first_adv <= 15 and mae <= -0.3),
        "cost_recovery_time": cost_rec,
        "winner": yen > 0 and mfe >= 0.4,
        "no_progress": mfe < 0.25 and abs((last_bid - entry_ask) / entry_ask * 100) < 0.15,
        "positive_duration": pos_dur,
        "negative_duration": neg_dur,
        "post_cross_high_update_count": high_updates,
        "breakout_failure": mae < -0.2 and mfe < 0.3,
    }


def evaluate_candidates(cands: Sequence[Candidate], streams: dict[str, list[Tick]], *, horizon: float = 120.0) -> dict[str, Any]:
    rows = []
    for c in cands:
        ticks = streams[c.stream_key]
        m = path_metrics(ticks, c.entry_idx, c.entry_ask, max_sec=horizon)
        if not m.get("evaluable"):
            continue
        rows.append({**m, "day": c.day, "symbol": c.symbol, "episode_id": c.episode_id, "arm": c.arm})
    n = len(rows)
    if n == 0:
        return {"n": 0, "pnl": 0.0, "pf": None, "never_rate": None, "early_adverse_rate": None, "winner_rate": None, "avg_mfe": None, "avg_mae": None, "mean": None}
    pnls = [r["terminal_pnl_yen"] for r in rows]
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    pf = (wins / losses) if losses > 0 else (float("inf") if wins > 0 else None)
    by_sym: dict[str, float] = {}
    for r in rows:
        by_sym[r["symbol"]] = by_sym.get(r["symbol"], 0.0) + r["terminal_pnl_yen"]
    pos = sorted([v for v in by_sym.values() if v > 0], reverse=True)
    tot_pos = sum(pos) or 1.0
    return {
        "n": n,
        "pnl": sum(pnls),
        "pf": round(pf, 4) if isinstance(pf, float) and pf != float("inf") else pf,
        "never_rate": sum(1 for r in rows if r["never_profitable"]) / n,
        "early_adverse_rate": sum(1 for r in rows if r["early_adverse"]) / n,
        "winner_rate": sum(1 for r in rows if r["winner"]) / n,
        "avg_mfe": sum(r["mfe"] for r in rows) / n,
        "avg_mae": sum(r["mae"] for r in rows) / n,
        "mean": sum(pnls) / n,
        "top1_symbol_share": (pos[0] / tot_pos) if pos else 0.0,
        "rows": rows,
    }


def incremental(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """b relative to a (adding a condition)."""
    def d(key):
        va, vb = a.get(key), b.get(key)
        if va is None or vb is None:
            return None
        return vb - va

    return {
        "candidate_delta": (b.get("n") or 0) - (a.get("n") or 0),
        "pnl_delta": d("pnl"),
        "pf_delta": d("pf") if isinstance(a.get("pf"), (int, float)) and isinstance(b.get("pf"), (int, float)) else None,
        "never_delta": d("never_rate"),
        "early_adverse_delta": d("early_adverse_rate"),
        "mfe_delta": d("avg_mfe"),
        "mae_delta": d("avg_mae"),
        "winner_rate_delta": d("winner_rate"),
        "positive_effect": bool(
            (b.get("n") or 0) > 0
            and (d("never_rate") is not None and d("never_rate") < 0 or d("pf") is not None and (b.get("pf") or 0) > (a.get("pf") or 0))
        ),
    }
