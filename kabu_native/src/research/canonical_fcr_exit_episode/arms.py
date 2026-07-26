"""X0–X5 EXIT arms — incremental OR of exit conditions; ENTRY fixed."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence

from research.canonical_fcr_exact_method.loader import Tick
from research.canonical_fcr_exit_episode.constants import COST_BPS, LOT
from research.canonical_fcr_exit_episode.exit_states import ExitEpisode


@dataclass
class ExitTrade:
    arm: str
    day: str
    symbol: str
    episode_id: str
    impulse_id: str
    entry_time: datetime
    exit_time: datetime
    entry_ask: float
    exit_bid: float
    exit_idx: int
    exit_reason: str
    pnl_yen: float
    mfe_pct: float
    mae_pct: float
    hold_sec: float
    winner: bool
    stop_path: bool
    terminal_class: str
    stream_key: str


def _pnl(entry_ask: float, exit_bid: float) -> float:
    return (exit_bid - entry_ask) * LOT - (
        entry_ask * LOT * COST_BPS / 10000.0 + exit_bid * LOT * COST_BPS / 10000.0
    )


def _bid_at(ticks: Sequence[Tick], idx: int, fallback_ask: float) -> float:
    for k in range(idx, max(-1, idx - 5), -1):
        if k < 0:
            break
        b = ticks[k].board.canonical_best_bid
        if b is not None and b > 0:
            return float(b)
    for k in range(idx, min(len(ticks), idx + 5)):
        b = ticks[k].board.canonical_best_bid
        if b is not None and b > 0:
            return float(b)
    return fallback_ask


def resolve_exit(ep: ExitEpisode, arm: str) -> tuple[int, str]:
    """Return (exit_idx, reason). Cumulative arms X1⊂X2⊂… ; X5 = full union."""
    h = ep.idx_horizon
    assert h is not None
    if arm == "X0":
        return h, "FIXED_HORIZON"
    # collect candidate exits by arm policy
    cands: list[tuple[int, str]] = []
    if arm in ("X1", "X2", "X3", "X4", "X5"):
        if ep.idx_false_reclaim is not None:
            cands.append((ep.idx_false_reclaim, "FALSE_RECLAIM"))
    if arm in ("X2", "X3", "X4", "X5"):
        if ep.idx_structure is not None:
            cands.append((ep.idx_structure, "STRUCTURE"))
    if arm in ("X3", "X4", "X5"):
        if ep.idx_noprogress is not None:
            cands.append((ep.idx_noprogress, "NO_PROGRESS"))
    if arm in ("X4", "X5"):
        if ep.idx_giveback is not None:
            cands.append((ep.idx_giveback, "WINNER_GIVEBACK"))
    # X5 same signal set as X4 in this design (full integration = OR of X1–X4)
    if not cands:
        return h, "FIXED_HORIZON_FALLBACK"
    cands.sort(key=lambda x: x[0])
    return cands[0][0], cands[0][1]


def materialize_arm(episodes: Sequence[ExitEpisode], streams: dict[str, list[Tick]], arm: str) -> list[ExitTrade]:
    out: list[ExitTrade] = []
    for ep in episodes:
        ticks = streams[ep.entry.stream_key]
        idx, reason = resolve_exit(ep, arm)
        bid = _bid_at(ticks, idx, ep.entry.entry_ask)
        pnl = _pnl(ep.entry.entry_ask, bid)
        hold = (ticks[idx].ts - ep.entry.entry_time).total_seconds()
        out.append(ExitTrade(
            arm=arm,
            day=ep.entry.day,
            symbol=ep.entry.symbol,
            episode_id=ep.entry.episode_id,
            impulse_id=ep.entry.impulse_id,
            entry_time=ep.entry.entry_time,
            exit_time=ticks[idx].ts,
            entry_ask=ep.entry.entry_ask,
            exit_bid=bid,
            exit_idx=idx,
            exit_reason=reason,
            pnl_yen=pnl,
            mfe_pct=ep.mfe_pct,
            mae_pct=ep.mae_pct,
            hold_sec=hold,
            winner=pnl > 0 and ep.mfe_pct >= 0.35,
            stop_path=ep.mae_pct <= -0.8,
            terminal_class=ep.terminal_class,
            stream_key=ep.entry.stream_key,
        ))
    return out


def summarize(trades: Sequence[ExitTrade]) -> dict[str, Any]:
    n = len(trades)
    if not n:
        return {
            "n": 0, "pnl": 0.0, "pf": None, "mean": None,
            "winner_rate": None, "stop_rate": None, "noprogress_exit_rate": None,
            "avg_mfe": None, "avg_mae": None, "avg_hold": None,
            "mfe_capture": None, "reasons": {},
        }
    pnls = [t.pnl_yen for t in trades]
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    pf = (wins / losses) if losses > 0 else (float("inf") if wins > 0 else None)
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    # MFE capture: terminal ret vs MFE when MFE>0
    caps = []
    for t in trades:
        if t.mfe_pct > 0:
            term = (t.exit_bid - t.entry_ask) / t.entry_ask * 100.0
            caps.append(max(0.0, min(1.0, term / t.mfe_pct)))
    return {
        "n": n,
        "pnl": sum(pnls),
        "pf": round(pf, 4) if isinstance(pf, float) and pf != float("inf") else pf,
        "mean": sum(pnls) / n,
        "winner_rate": sum(1 for t in trades if t.winner) / n,
        "stop_rate": sum(1 for t in trades if t.stop_path) / n,
        "noprogress_exit_rate": sum(1 for t in trades if t.exit_reason == "NO_PROGRESS") / n,
        "avg_mfe": sum(t.mfe_pct for t in trades) / n,
        "avg_mae": sum(t.mae_pct for t in trades) / n,
        "avg_hold": sum(t.hold_sec for t in trades) / n,
        "mfe_capture": (sum(caps) / len(caps)) if caps else None,
        "reasons": reasons,
    }


def increment_exit(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    def d(k):
        if a.get(k) is None or b.get(k) is None:
            return None
        return b[k] - a[k]

    pf_imp = isinstance(a.get("pf"), (int, float)) and isinstance(b.get("pf"), (int, float)) and (b["pf"] or 0) > (a["pf"] or 0)
    mean_imp = d("mean") is not None and d("mean") > 0
    stop_imp = d("stop_rate") is not None and d("stop_rate") < 0
    winner_ok = not (d("winner_rate") is not None and d("winner_rate") < -0.08)
    mfe_ok = not (d("mfe_capture") is not None and d("mfe_capture") < -0.08)
    if (b.get("n") or 0) == 0:
        label = "INCREMENT_NOT_EVALUABLE"
    elif pf_imp and mean_imp and (stop_imp or winner_ok) and mfe_ok:
        label = "INCREMENT_POSITIVE"
    elif pf_imp or mean_imp or stop_imp:
        label = "INCREMENT_MIXED"
    else:
        label = "INCREMENT_NEGATIVE"
    return {
        "label": label,
        "pf_delta": d("pf") if isinstance(a.get("pf"), (int, float)) and isinstance(b.get("pf"), (int, float)) else None,
        "mean_delta": d("mean"),
        "stop_delta": d("stop_rate"),
        "winner_delta": d("winner_rate"),
        "mfe_capture_delta": d("mfe_capture"),
        "mae_delta": d("avg_mae"),
        "pnl_delta": d("pnl"),
    }
