"""CAP5 time-series replay with IDEES ranking."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from research.integrated_directional_entry_exit_strategy.constants import CAP
from research.integrated_directional_entry_exit_strategy.exits import TradeResult


def replay_cap5_ranked(trades: Sequence[TradeResult], *, cap: int = CAP) -> dict[str, Any]:
    """Independent strategy CAP5: same-symbol ban, cap slots, ranked simultaneous entries."""
    ordered = sorted(
        trades,
        key=lambda t: (
            t.entry_time,
            -t.signal_score,
            t.entry_spread_bps,
            t.signal_time,
            t.sample_id,
        ),
    )
    open_pos: list[TradeResult] = []
    accepted: list[TradeResult] = []
    blocked: list[dict[str, Any]] = []
    used_ep: set[str] = set()

    for t in ordered:
        open_pos = [p for p in open_pos if p.exit_time > t.entry_time]
        if t.episode_id in used_ep:
            blocked.append({"reason": "same_episode", "sample_id": t.sample_id, "pnl": t.net_pnl_yen_100})
            continue
        if any(p.symbol == t.symbol for p in open_pos):
            blocked.append({"reason": "same_symbol", "symbol": t.symbol, "pnl": t.net_pnl_yen_100})
            continue
        if len(open_pos) >= cap:
            blocked.append({"reason": "cap_full", "symbol": t.symbol, "pnl": t.net_pnl_yen_100})
            continue
        accepted.append(t)
        used_ep.add(t.episode_id)
        open_pos.append(t)

    return summarize_portfolio(accepted, blocked)


def summarize_portfolio(accepted: Sequence[TradeResult], blocked: Sequence[dict] | None = None) -> dict[str, Any]:
    blocked = list(blocked or [])
    if not accepted:
        return {
            "trades": 0, "total_pnl_yen_100": 0.0, "avg_pnl_yen_100": None,
            "profit_factor_yen_100": None, "win_rate": None, "max_drawdown_yen": 0.0,
            "mfe_mae": None, "avg_hold_sec": None, "cap_blocked": sum(1 for b in blocked if b.get("reason") == "cap_full"),
            "blocked_n": len(blocked), "accepted": [], "daily": {}, "symbols": {},
            "exit_reasons": {}, "top1_trade_share": None, "top1_symbol_share": None,
            "top3_symbol_share": None, "avg_entry_spread": None, "avg_confirm_wait": None,
            "avg_pnl_5s": None, "avg_pnl_30s": None, "avg_pnl_180s": None,
            "avg_mfe": None, "avg_mae": None, "cap_utilization": 0.0,
        }
    pnls = [t.net_pnl_yen_100 for t in accepted]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    eq = peak = 0.0
    max_dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    mfes = [t.mfe_bps for t in accepted]
    maes = [abs(t.mae_bps) for t in accepted]
    mfe_mae = (sum(mfes) / len(mfes)) / (sum(maes) / len(maes)) if maes and sum(maes) else None
    by_day: dict[str, float] = defaultdict(float)
    by_sym: dict[str, float] = defaultdict(float)
    reasons: dict[str, int] = defaultdict(int)
    for t in accepted:
        by_day[t.day] += t.net_pnl_yen_100
        by_sym[t.symbol] += t.net_pnl_yen_100
        reasons[t.exit_reason] += 1
    abs_pnls = sorted([abs(p) for p in pnls], reverse=True)
    tot_abs = sum(abs_pnls) or 1.0
    s_ranked = sorted(by_sym.values(), key=abs, reverse=True)
    stot = sum(abs(x) for x in s_ranked) or 1.0
    # rough CAP utilization: fraction of time with open slots used — proxy = min(1, trades*avg_hold / (days*session*cap))
    days_n = len(by_day) or 1
    avg_hold = sum(t.hold_sec for t in accepted) / len(accepted)
    # AM+PM ~ 5.5h = 19800s per day
    util = min(1.0, (len(accepted) * avg_hold) / (days_n * 19800.0 * CAP))
    def _avg(xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else None
    return {
        "trades": len(accepted),
        "total_pnl_yen_100": sum(pnls),
        "avg_pnl_yen_100": sum(pnls) / len(pnls),
        "profit_factor_yen_100": (sum(wins) / abs(sum(losses))) if losses else None,
        "win_rate": len(wins) / len(pnls),
        "max_drawdown_yen": max_dd,
        "mfe_mae": mfe_mae,
        "avg_hold_sec": avg_hold,
        "cap_blocked": sum(1 for b in blocked if b.get("reason") == "cap_full"),
        "same_symbol_blocked": sum(1 for b in blocked if b.get("reason") == "same_symbol"),
        "blocked_n": len(blocked),
        "accepted": list(accepted),
        "daily": dict(by_day),
        "symbols": dict(sorted(by_sym.items(), key=lambda x: -abs(x[1]))),
        "exit_reasons": dict(reasons),
        "top1_trade_share": abs_pnls[0] / tot_abs,
        "top1_symbol_share": abs(s_ranked[0]) / stot if s_ranked else None,
        "top3_symbol_share": sum(abs(x) for x in s_ranked[:3]) / stot if s_ranked else None,
        "avg_entry_spread": _avg([t.entry_spread_bps for t in accepted]),
        "avg_confirm_wait": _avg([t.confirm_wait_sec for t in accepted]),
        "avg_pnl_5s": _avg([t.pnl_5s for t in accepted]),
        "avg_pnl_30s": _avg([t.pnl_30s for t in accepted]),
        "avg_pnl_180s": _avg([t.pnl_180s for t in accepted]),
        "avg_mfe": _avg(mfes),
        "avg_mae": _avg([t.mae_bps for t in accepted]),
        "cap_utilization": util,
    }


def train_passes(m: dict, train_days: list[str]) -> tuple[bool, list[str]]:
    reasons = []
    ok = True
    if (m.get("total_pnl_yen_100") or 0) <= 0:
        ok = False
        reasons.append("pnl<=0")
    if (m.get("profit_factor_yen_100") or 0) <= 1.10:
        ok = False
        reasons.append("PF<=1.10")
    if (m.get("avg_pnl_yen_100") or 0) <= 0:
        ok = False
        reasons.append("avg<=0")
    if (m.get("trades") or 0) < 50:
        ok = False
        reasons.append("trades<50")
    for d in train_days:
        if (m.get("daily") or {}).get(d, 0) <= 0:
            ok = False
            reasons.append(f"day_{d}_nonpos")
    tot = m.get("total_pnl_yen_100") or 0
    dd = m.get("max_drawdown_yen") or 0
    if tot > 0 and abs(dd) > tot:
        ok = False
        reasons.append("dd>total")
    if (m.get("top1_trade_share") or 0) >= 0.30:
        ok = False
        reasons.append("top1_trade>=30%")
    if (m.get("top1_symbol_share") or 0) >= 0.30:
        ok = False
        reasons.append("top1_symbol>=30%")
    if (m.get("top3_symbol_share") or 0) >= 0.60:
        ok = False
        reasons.append("top3_symbol>=60%")
    return ok, reasons


def val_passes(m: dict) -> tuple[bool, list[str]]:
    reasons = []
    ok = True
    if (m.get("total_pnl_yen_100") or 0) <= 0:
        ok = False
        reasons.append("pnl<=0")
    if (m.get("profit_factor_yen_100") or 0) <= 1.05:
        ok = False
        reasons.append("PF<=1.05")
    if (m.get("avg_pnl_yen_100") or 0) <= 0:
        ok = False
        reasons.append("avg<=0")
    if (m.get("trades") or 0) < 20:
        ok = False
        reasons.append("trades<20")
    if (m.get("top1_symbol_share") or 0) >= 0.50:
        ok = False
        reasons.append("extreme_symbol")
    tot = m.get("total_pnl_yen_100") or 0
    dd = abs(m.get("max_drawdown_yen") or 0)
    if tot > 0 and dd > tot * 1.5:
        ok = False
        reasons.append("dd_too_large")
    return ok, reasons


def hold_passes(m: dict) -> tuple[bool, list[str]]:
    reasons = []
    ok = True
    if (m.get("total_pnl_yen_100") or 0) <= 0:
        ok = False
        reasons.append("pnl<=0")
    if (m.get("profit_factor_yen_100") or 0) <= 1.0:
        ok = False
        reasons.append("PF<=1")
    if (m.get("avg_pnl_yen_100") or 0) <= 0:
        ok = False
        reasons.append("avg<=0")
    if (m.get("trades") or 0) < 10:
        ok = False
        reasons.append("trades<10")
    if (m.get("top1_symbol_share") or 0) >= 0.40:
        ok = False
        reasons.append("top1_symbol>=40%")
    return ok, reasons
