"""CAP=5 deterministic portfolio replay."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence


@dataclass
class CapTrade:
    day: str
    symbol: str
    episode_id: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    pnl_5bps: float
    exit_reason: str
    strategy_id: str
    setup_id: str
    session: str
    mfe: float = 0.0
    mae: float = 0.0
    stop: bool = False
    early_stop: bool = False
    no_progress: bool = False
    winner: bool = False


def replay_cap5(
    trades: Sequence[CapTrade],
    *,
    portfolio_id: str = "P",
    cap: int = 5,
) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda t: (t.entry_time, t.strategy_id, t.symbol, t.episode_id))
    open_pos: list[CapTrade] = []
    accepted: list[CapTrade] = []
    blocked: list[dict[str, Any]] = []
    used_ep: set[str] = set()
    slots_recycled = 0

    for t in ordered:
        open_pos = [p for p in open_pos if p.exit_time > t.entry_time]
        if t.episode_id in used_ep:
            blocked.append({"reason": "same_episode_reentry", "episode_id": t.episode_id, "pnl": t.pnl_5bps})
            continue
        if any(p.symbol == t.symbol for p in open_pos):
            blocked.append({"reason": "same_symbol", "symbol": t.symbol, "pnl": t.pnl_5bps})
            continue
        if len(open_pos) >= cap:
            blocked.append({"reason": "cap_full", "symbol": t.symbol, "pnl": t.pnl_5bps})
            continue
        before = len(open_pos)
        accepted.append(t)
        used_ep.add(t.episode_id)
        open_pos.append(t)
        if before > 0 and len([p for p in open_pos if p.exit_time <= t.entry_time]) == 0:
            pass
        # recycling: if we had frees earlier
        if before < cap and any(p.exit_time <= t.entry_time for p in accepted[:-1]):
            slots_recycled += 1

    pnls = [t.pnl_5bps for t in accepted]
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    days = sorted({t.day for t in accepted})
    daily: dict[str, float] = {}
    for t in accepted:
        daily[t.day] = daily.get(t.day, 0.0) + t.pnl_5bps
    pos_days = sum(1 for v in daily.values() if v > 0)
    neg_days = sum(1 for v in daily.values() if v < 0)
    # trade sequence DD
    eq = peak = 0.0
    dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    pf = (wins / losses) if losses > 0 else (float("inf") if wins > 0 else None)
    return {
        "portfolio_id": portfolio_id,
        "accepted": len(accepted),
        "trades": len(accepted),
        "pnl_5bps": sum(pnls) if pnls else 0.0,
        "PF_5bps": round(pf, 4) if isinstance(pf, float) and pf != float("inf") else pf,
        "pos_days": pos_days,
        "neg_days": neg_days,
        "daily_pnl": daily,
        "stop_rate": (sum(1 for t in accepted if t.stop) / len(accepted)) if accepted else None,
        "early_stop_rate": (sum(1 for t in accepted if t.early_stop) / len(accepted)) if accepted else None,
        "no_progress_rate": (sum(1 for t in accepted if t.no_progress) / len(accepted)) if accepted else None,
        "winner_rate": (sum(1 for t in accepted if t.winner) / len(accepted)) if accepted else None,
        "avg_mfe": (sum(t.mfe for t in accepted) / len(accepted)) if accepted else None,
        "avg_mae": (sum(t.mae for t in accepted) / len(accepted)) if accepted else None,
        "trades_per_day": (len(accepted) / len(days)) if days else 0.0,
        "trade_sequence_dd": dd,
        "blocked_n": len(blocked),
        "blocked_pnl": sum(b.get("pnl") or 0 for b in blocked),
        "slots_recycled": slots_recycled,
        "top_symbols": _top_sym(accepted),
    }


def _top_sym(accepted: Sequence[CapTrade]) -> list[dict[str, Any]]:
    by: dict[str, float] = {}
    for t in accepted:
        by[t.symbol] = by.get(t.symbol, 0.0) + t.pnl_5bps
    return [{"symbol": s, "pnl": p} for s, p in sorted(by.items(), key=lambda x: -x[1])[:10]]
