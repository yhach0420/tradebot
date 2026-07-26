"""A0–A5 integrated IOAR arms — same ENTRY universe."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from research.integrated_order_flow_absorption_reversal.constants import COST_BPS, LOT
from research.integrated_order_flow_absorption_reversal.loader import Tick, bid_at
from research.integrated_order_flow_absorption_reversal.state_machine import Episode


@dataclass
class Trade:
    arm: str
    day: str
    symbol: str
    episode_id: str
    entry_time: datetime
    exit_time: datetime
    entry_ask: float
    exit_bid: float
    exit_reason: str
    pnl_yen: float
    mfe_pct: float
    mae_pct: float
    hold_sec: float
    winner: bool
    stop_5m: bool
    stream_key: str
    outcome: str


def _pnl(ask: float, bid: float) -> float:
    return (bid - ask) * LOT - (ask * LOT * COST_BPS / 10000.0 + bid * LOT * COST_BPS / 10000.0)


def resolve_exit(ep: Episode, arm: str) -> tuple[int, str]:
    h = ep.idx_horizon or ep.entry_idx or 0
    cands: list[tuple[int, str]] = []
    if ep.idx_hard is not None:
        cands.append((ep.idx_hard, "HARD_EXIT"))
    if arm == "A0":
        if ep.idx_diag is not None:
            cands.append((ep.idx_diag, "FIXED_DIAG"))
        cands.append((h, "HORIZON"))
    if arm in ("A1", "A2", "A3", "A4", "A5"):
        if ep.idx_abs_fail is not None:
            cands.append((ep.idx_abs_fail, "ABSORPTION_FAILURE"))
    if arm in ("A2", "A3", "A4", "A5"):
        if ep.idx_no_demand is not None:
            cands.append((ep.idx_no_demand, "NO_DEMAND_FOLLOW_THROUGH"))
    if arm in ("A3", "A4", "A5"):
        if ep.idx_demand_exh is not None:
            cands.append((ep.idx_demand_exh, "DEMAND_EXHAUSTION"))
    if arm in ("A4", "A5"):
        if ep.idx_giveback is not None:
            cands.append((ep.idx_giveback, "PROFIT_GIVEBACK"))
    if not cands:
        return h, "HORIZON"
    cands.sort(key=lambda x: x[0])
    return cands[0][0], cands[0][1]


def _outcome(ep: Episode, reason: str, mfe: float) -> str:
    if reason == "ABSORPTION_FAILURE":
        return "ABSORPTION_FALSE_POSITIVE"
    if reason == "NO_DEMAND_FOLLOW_THROUGH":
        return "NO_DEMAND"
    if mfe >= 0.50 and ep.saw_s6:
        return "STRONG_WINNER"
    if mfe >= COST_BPS / 100.0 and ep.saw_s6:
        return "SUCCESS_CONTINUATION"
    if mfe > 0:
        return "TEMPORARY_ADVANCE"
    return "NO_DEMAND"


def materialize(episodes: Sequence[Episode], streams: dict[str, list[Tick]], arm: str) -> list[Trade]:
    need = (
        "S0_MARKET_BALANCE", "S1_SELL_PRESSURE", "S2_ABSORPTION_ACTIVE",
        "S3_SELL_EXHAUSTION", "S4_BUY_FLOW_REVERSAL", "S5_ACCEPTANCE_CONFIRM", "ENTRY",
    )
    out: list[Trade] = []
    used: set[str] = set()
    for ep in episodes:
        if ep.entry_idx is None or ep.entry_ask is None or ep.entry_time is None:
            continue
        if any(s not in ep.states for s in need):
            continue
        if ep.episode_id in used:
            continue
        ticks = streams[ep.stream_key]
        idx, reason = resolve_exit(ep, arm)
        bid = bid_at(ticks, idx, ep.entry_ask)
        pnl = _pnl(ep.entry_ask, bid)
        hold = (ticks[idx].ts - ep.entry_time).total_seconds()
        out.append(Trade(
            arm=arm, day=ep.day, symbol=ep.symbol, episode_id=ep.episode_id,
            entry_time=ep.entry_time, exit_time=ticks[idx].ts,
            entry_ask=ep.entry_ask, exit_bid=bid, exit_reason=reason,
            pnl_yen=pnl, mfe_pct=ep.mfe_pct, mae_pct=ep.mae_pct, hold_sec=hold,
            winner=pnl > 0 and ep.mfe_pct >= 0.30,
            stop_5m=ep.mae_pct <= -0.8 and hold <= 300,
            stream_key=ep.stream_key,
            outcome=_outcome(ep, reason, ep.mfe_pct),
        ))
        used.add(ep.episode_id)
    return out


def summarize(trades: Sequence[Trade]) -> dict[str, Any]:
    n = len(trades)
    if not n:
        return {
            "n": 0, "pnl": 0.0, "pf": None, "mean": None, "win_rate": None,
            "avg_win": None, "avg_loss": None, "avg_mfe": None, "avg_mae": None,
            "mfe_capture": None, "avg_hold": None, "stop_5m_rate": None,
            "absorption_failure_rate": None, "no_demand_rate": None,
            "demand_exhaustion_rate": None, "winner_rate": None,
            "reasons": {}, "outcomes": {}, "by_day": {}, "top1_symbol_share": None,
            "top3_symbol_share": None,
        }
    pnls = [t.pnl_yen for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p < 0]
    pf = (sum(wins) / sum(losses)) if losses else (float("inf") if wins else None)
    reasons: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        outcomes[t.outcome] = outcomes.get(t.outcome, 0) + 1
    caps = []
    for t in trades:
        if t.mfe_pct > 0:
            term = (t.exit_bid - t.entry_ask) / t.entry_ask * 100.0
            caps.append(max(0.0, min(1.0, term / t.mfe_pct)))
    by_sym: dict[str, float] = {}
    by_day: dict[str, float] = {}
    for t in trades:
        by_sym[t.symbol] = by_sym.get(t.symbol, 0.0) + t.pnl_yen
        by_day[t.day] = by_day.get(t.day, 0.0) + t.pnl_yen
    pos = sorted([v for v in by_sym.values() if v > 0], reverse=True)
    tot = sum(abs(v) for v in by_sym.values()) or 1.0
    top_abs = sorted(by_sym.items(), key=lambda x: abs(x[1]), reverse=True)
    top1 = abs(top_abs[0][1]) / tot if top_abs else 0.0
    top3 = sum(abs(v) for _, v in top_abs[:3]) / tot if top_abs else 0.0
    return {
        "n": n,
        "pnl": sum(pnls),
        "pf": round(pf, 4) if isinstance(pf, float) and pf != float("inf") else pf,
        "mean": sum(pnls) / n,
        "win_rate": len(wins) / n,
        "avg_win": (sum(wins) / len(wins)) if wins else None,
        "avg_loss": (sum(losses) / len(losses)) if losses else None,
        "avg_mfe": sum(t.mfe_pct for t in trades) / n,
        "avg_mae": sum(t.mae_pct for t in trades) / n,
        "mfe_capture": (sum(caps) / len(caps)) if caps else None,
        "avg_hold": sum(t.hold_sec for t in trades) / n,
        "stop_5m_rate": sum(1 for t in trades if t.stop_5m) / n,
        "absorption_failure_rate": reasons.get("ABSORPTION_FAILURE", 0) / n,
        "no_demand_rate": reasons.get("NO_DEMAND_FOLLOW_THROUGH", 0) / n,
        "demand_exhaustion_rate": reasons.get("DEMAND_EXHAUSTION", 0) / n,
        "winner_rate": sum(1 for t in trades if t.winner) / n,
        "reasons": reasons,
        "outcomes": outcomes,
        "by_day": by_day,
        "by_symbol_top": top_abs[:10],
        "top1_symbol_share": top1,
        "top3_symbol_share": top3,
        "pos_days": sum(1 for v in by_day.values() if v > 0),
        "neg_days": sum(1 for v in by_day.values() if v <= 0),
    }


def increment(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    def d(k):
        if a.get(k) is None or b.get(k) is None:
            return None
        return b[k] - a[k]

    pf_imp = isinstance(a.get("pf"), (int, float)) and isinstance(b.get("pf"), (int, float)) and (b["pf"] or 0) > (a["pf"] or 0)
    mean_imp = d("mean") is not None and d("mean") > 0
    if (b.get("n") or 0) == 0:
        label = "INCREMENT_NOT_EVALUABLE"
    elif pf_imp and mean_imp:
        label = "INCREMENT_POSITIVE"
    elif pf_imp or mean_imp:
        label = "INCREMENT_MIXED"
    else:
        label = "INCREMENT_NEGATIVE"
    return {
        "label": label, "pf_delta": d("pf") if isinstance(a.get("pf"), (int, float)) and isinstance(b.get("pf"), (int, float)) else None,
        "mean_delta": d("mean"), "pnl_delta": d("pnl"),
        "stop_5m_delta": d("stop_5m_rate"), "winner_delta": d("winner_rate"),
        "mfe_capture_delta": d("mfe_capture"),
    }
