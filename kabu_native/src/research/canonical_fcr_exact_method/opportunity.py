"""FCR opportunity labels — E1 Ask / future Bid."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from research.canonical_fcr_exact_method.constants import COST_BPS, LOT
from research.canonical_fcr_exact_method.loader import Tick


@dataclass
class Candidate:
    arm: str
    day: str
    symbol: str
    episode_id: str
    impulse_id: str
    entry_idx: int
    entry_time: Any
    entry_ask: float
    stream_key: str
    features: dict


def first_valid_ask(ticks: Sequence[Tick], decision_idx: int, *, min_delay: float = 0.0) -> Optional[tuple[int, float, float]]:
    t0 = ticks[decision_idx].ts
    start = decision_idx if min_delay <= 0 else decision_idx + 1
    for j in range(start, min(len(ticks), decision_idx + 80)):
        dt = (ticks[j].ts - t0).total_seconds()
        if min_delay > 0 and dt < min_delay:
            continue
        if min_delay == 0 and j != decision_idx:
            # E1-style: prefer next if same not ok
            pass
        ask = ticks[j].board.canonical_best_ask
        aq = ticks[j].board.canonical_ask_qty
        if ask and ask > 0 and (aq is None or aq >= LOT):
            if min_delay == 0 and j == decision_idx:
                return j, float(ask), 0.0
            if j > decision_idx:
                return j, float(ask), dt
    return None


def path_metrics(ticks: Sequence[Tick], entry_idx: int, entry_ask: float, *, max_sec: float = 300.0) -> dict[str, Any]:
    if entry_ask <= 0 or entry_idx >= len(ticks) - 1:
        return {"evaluable": False}
    t0 = ticks[entry_idx].ts
    mfe = mae = 0.0
    never = True
    cost = COST_BPS / 10000.0
    cost_rec = first_adv = None
    last_bid = None
    stop5 = False
    for j in range(entry_idx + 1, len(ticks)):
        dt = (ticks[j].ts - t0).total_seconds()
        if dt > max_sec:
            break
        bid = ticks[j].board.canonical_best_bid
        if bid is None or bid <= 0:
            continue
        last_bid = bid
        ret = (bid - entry_ask) / entry_ask * 100.0
        mfe = max(mfe, ret)
        mae = min(mae, ret)
        if never and bid > entry_ask * (1 + cost):
            never = False
            cost_rec = dt
        if first_adv is None and ret < 0:
            first_adv = dt
        if dt <= 300 and ret <= -0.8:
            stop5 = True
    if last_bid is None:
        return {"evaluable": False}
    yen = (last_bid - entry_ask) * LOT - (
        entry_ask * LOT * COST_BPS / 10000.0 + last_bid * LOT * COST_BPS / 10000.0
    )
    return {
        "evaluable": True,
        "mfe": mfe,
        "mae": mae,
        "terminal_pnl_yen": yen,
        "never_profitable": never,
        "early_adverse": bool(first_adv is not None and first_adv <= 15 and mae <= -0.3),
        "stop_path": mae <= -0.8,
        "stop_5m_path": stop5,
        "no_progress": mfe < 0.25 and abs((last_bid - entry_ask) / entry_ask * 100) < 0.15,
        "winner": yen > 0 and mfe >= 0.4,
        "cost_recovery_time": cost_rec,
    }


def evaluate_candidates(cands: Sequence[Candidate], streams: dict[str, list[Tick]], *, horizon: float = 180.0) -> dict[str, Any]:
    rows = []
    for c in cands:
        m = path_metrics(streams[c.stream_key], c.entry_idx, c.entry_ask, max_sec=horizon)
        if m.get("evaluable"):
            rows.append({**m, "day": c.day, "symbol": c.symbol, "episode_id": c.episode_id})
    n = len(rows)
    if not n:
        return {"n": 0, "pnl": 0.0, "pf": None, "mean": None, "never_rate": None, "early_adverse_rate": None,
                "stop_rate": None, "stop_5m_rate": None, "noprogress_rate": None, "winner_rate": None,
                "avg_mfe": None, "avg_mae": None, "top1_symbol_share": None}
    pnls = [r["terminal_pnl_yen"] for r in rows]
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    pf = (wins / losses) if losses > 0 else (float("inf") if wins > 0 else None)
    by_sym: dict[str, float] = {}
    for r in rows:
        by_sym[r["symbol"]] = by_sym.get(r["symbol"], 0.0) + r["terminal_pnl_yen"]
    pos = sorted([v for v in by_sym.values() if v > 0], reverse=True)
    tot = sum(pos) or 1.0
    return {
        "n": n,
        "pnl": sum(pnls),
        "pf": round(pf, 4) if isinstance(pf, float) and pf != float("inf") else pf,
        "mean": sum(pnls) / n,
        "never_rate": sum(1 for r in rows if r["never_profitable"]) / n,
        "early_adverse_rate": sum(1 for r in rows if r["early_adverse"]) / n,
        "stop_rate": sum(1 for r in rows if r["stop_path"]) / n,
        "stop_5m_rate": sum(1 for r in rows if r["stop_5m_path"]) / n,
        "noprogress_rate": sum(1 for r in rows if r["no_progress"]) / n,
        "winner_rate": sum(1 for r in rows if r["winner"]) / n,
        "avg_mfe": sum(r["mfe"] for r in rows) / n,
        "avg_mae": sum(r["mae"] for r in rows) / n,
        "top1_symbol_share": (pos[0] / tot) if pos else 0.0,
    }


def increment_effect(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    def d(k):
        if a.get(k) is None or b.get(k) is None:
            return None
        return b[k] - a[k]

    pf_imp = isinstance(a.get("pf"), (int, float)) and isinstance(b.get("pf"), (int, float)) and (b["pf"] or 0) > (a["pf"] or 0)
    mean_imp = d("mean") is not None and d("mean") > 0
    quality = (d("never_rate") is not None and d("never_rate") < 0) or (d("early_adverse_rate") is not None and d("early_adverse_rate") < 0)
    winner_ok = (b.get("winner_rate") or 0) > 0 and not ((d("winner_rate") is not None) and d("winner_rate") < -0.05)
    if (b.get("n") or 0) == 0:
        label = "INCREMENT_NEGATIVE"
    elif pf_imp and mean_imp and quality and winner_ok:
        label = "INCREMENT_POSITIVE"
    elif pf_imp or mean_imp or quality:
        label = "INCREMENT_MIXED"
    else:
        label = "INCREMENT_NEGATIVE"
    return {
        "label": label,
        "candidate_delta": (b.get("n") or 0) - (a.get("n") or 0),
        "pnl_delta": d("pnl"),
        "pf_delta": d("pf") if isinstance(a.get("pf"), (int, float)) and isinstance(b.get("pf"), (int, float)) else None,
        "mean_delta": d("mean"),
        "never_delta": d("never_rate"),
        "early_adverse_delta": d("early_adverse_rate"),
        "stop_delta": d("stop_rate"),
        "winner_delta": d("winner_rate"),
        "mfe_delta": d("avg_mfe"),
        "mae_delta": d("avg_mae"),
    }
