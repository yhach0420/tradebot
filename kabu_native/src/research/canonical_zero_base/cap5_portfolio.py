"""CAP=5 event-driven portfolio with one episode one entry."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from research.canonical_zero_base.constants import CAP, COST_BPS, LOT


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
    mfe: float
    mae: float
    stop: bool
    early_stop: bool
    no_progress: bool
    winner: bool


def _session(ts: datetime) -> str:
    return "AM" if ts.hour < 12 else "PM"


def replay_cap5(trades: Sequence[CapTrade], *, portfolio_id: str, cap: int = CAP) -> dict[str, Any]:
    events: list[tuple[datetime, int, str, CapTrade]] = []
    for t in trades:
        events.append((t.entry_time, 1, "ENTRY", t))
        events.append((t.exit_time, 0, "EXIT", t))
    events.sort(key=lambda e: (e[0], e[1], e[3].symbol, e[3].setup_id))

    open_pos: dict[str, CapTrade] = {}
    open_sym: set[tuple[str, str]] = set()
    used_ep: set[str] = set()
    accepted: list[CapTrade] = []
    cap_blocked = sym_blocked = ep_blocked = 0

    for ts, _o, kind, t in events:
        if kind == "EXIT":
            if t.setup_id in open_pos:
                open_pos.pop(t.setup_id)
                open_sym.discard((t.day, t.symbol))
                accepted.append(t)
            continue
        if t.episode_id in used_ep:
            ep_blocked += 1
            continue
        if (t.day, t.symbol) in open_sym:
            sym_blocked += 1
            continue
        if len(open_pos) >= cap:
            cap_blocked += 1
            continue
        open_pos[t.setup_id] = t
        open_sym.add((t.day, t.symbol))
        used_ep.add(t.episode_id)

    pnls = [t.pnl_5bps for t in accepted]
    gp = sum(p for p in pnls if p > 0)
    gl = sum(p for p in pnls if p < 0)
    pf = (gp / abs(gl)) if gl < 0 else (None if not pnls else float("inf") if gp > 0 else None)
    days = sorted({t.day for t in accepted})
    by_day: dict[str, float] = defaultdict(float)
    by_sym: dict[str, float] = defaultdict(float)
    for t in accepted:
        by_day[t.day] += t.pnl_5bps
        by_sym[t.symbol] += t.pnl_5bps
    eq = peak = max_dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    return {
        "portfolio_id": portfolio_id,
        "candidates": len(trades),
        "accepted": len(accepted),
        "trades": len(accepted),
        "cap_blocked": cap_blocked,
        "same_symbol_blocked": sym_blocked,
        "episode_blocked": ep_blocked,
        "pnl_5bps": round(sum(pnls), 2),
        "PF_5bps": round(pf, 4) if isinstance(pf, float) and pf != float("inf") else pf,
        "win_rate": (sum(1 for p in pnls if p > 0) / len(pnls)) if pnls else None,
        "stop_rate": (sum(1 for t in accepted if t.stop) / len(accepted)) if accepted else None,
        "early_stop_rate": (sum(1 for t in accepted if t.early_stop) / len(accepted)) if accepted else None,
        "no_progress_rate": (sum(1 for t in accepted if t.no_progress) / len(accepted)) if accepted else None,
        "winner_rate": (sum(1 for t in accepted if t.winner) / len(accepted)) if accepted else None,
        "avg_mfe": (sum(t.mfe for t in accepted) / len(accepted)) if accepted else None,
        "avg_mae": (sum(t.mae for t in accepted) / len(accepted)) if accepted else None,
        "trades_per_day": (len(accepted) / len(days)) if days else 0.0,
        "pos_days": sum(1 for v in by_day.values() if v > 0),
        "neg_days": sum(1 for v in by_day.values() if v <= 0),
        "trade_sequence_dd": round(max_dd, 2),
        "daily_pnl": dict(by_day),
        "top_symbols": sorted(by_sym.items(), key=lambda x: -x[1])[:5],
        "one_episode_one_entry": True,
    }
