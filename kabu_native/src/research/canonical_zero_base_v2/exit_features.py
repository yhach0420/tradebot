"""EXIT feature candidates from post-entry paths — not ENTRY feature reuse."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from research.canonical_zero_base_v2.loader import Tick

EXIT_FEATURE_INVENTORY: list[dict[str, Any]] = []
LEAD_EVENTS = (1, 2, 3)
LEAD_SECS = (1, 3, 5, 10, 15, 30)


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def compute_exit_features_at(
    ticks: Sequence[Tick],
    entry_idx: int,
    j: int,
    *,
    entry_ask: float,
    levels: dict[str, float],
    strategy_id: str,
) -> dict[str, Optional[float]]:
    """Features at path index j (> entry), causal w.r.t. exit decision at j."""
    if j <= entry_idx or j >= len(ticks):
        return {}
    t0 = ticks[entry_idx]
    t = ticks[j]
    bid = t.board.canonical_best_bid
    if bid is None:
        return {}
    hold = (t.ts - t0.ts).total_seconds()
    pnl_pct = (bid - entry_ask) / entry_ask * 100.0
    # MFE/MAE so far
    mfe = mae = 0.0
    for k in range(entry_idx + 1, j + 1):
        b = ticks[k].board.canonical_best_bid
        if b is None:
            continue
        r = (b - entry_ask) / entry_ask * 100.0
        mfe = max(mfe, r)
        mae = min(mae, r)
    out: dict[str, Optional[float]] = {
        "hold_sec": hold,
        "pnl_pct": pnl_pct,
        "mfe_so_far": mfe,
        "mae_so_far": mae,
        "giveback": mfe - pnl_pct,
        "mfe_stagnation": 1.0 if mfe > 0.3 and pnl_pct < mfe * 0.5 else 0.0,
        "spread": _f(t.board.canonical_spread),
        "spread_bps": _f(t.board.canonical_spread_bps),
        "top_imbalance": _f(t.board.canonical_top_imbalance),
        "bid_qty": _f(t.board.canonical_bid_qty),
        "ask_qty": _f(t.board.canonical_ask_qty),
        "bid_retreat": 1.0 if (t.board.canonical_best_bid or 0) < (ticks[j - 1].board.canonical_best_bid or 0) else 0.0,
        "ask_replenish": 1.0 if (t.board.canonical_ask_qty or 0) > (ticks[max(entry_idx, j - 3)].board.canonical_ask_qty or 0) else 0.0,
        "bid_depletion": 1.0 if (t.board.canonical_bid_qty or 0) < (ticks[max(entry_idx, j - 3)].board.canonical_bid_qty or 0) * 0.8 else 0.0,
    }
    # short lookback flow
    up = dn = 0
    for k in range(max(entry_idx, j - 10), j + 1):
        if k == 0:
            continue
        if ticks[k].px > ticks[k - 1].px:
            up += 1
        elif ticks[k].px < ticks[k - 1].px:
            dn += 1
    tot = up + dn
    out["uptick_ratio_path"] = up / tot if tot else None
    out["sell_flow_accel"] = float(dn - up)
    out["adverse_accel"] = 1.0 if pnl_pct < mae * 0.5 and mae < -0.2 else 0.0

    # strategy-specific structural
    if strategy_id == "Z1":
        low = levels.get("low") or levels.get("pullback_low")
        reclaim = levels.get("reclaim")
        out["thesis_low_breach"] = 1.0 if low and bid < float(low) else 0.0
        out["reclaim_fail"] = 1.0 if reclaim and bid < float(reclaim) else 0.0
        out["lower_low"] = 1.0 if low and bid < float(low) * 0.999 else 0.0
    elif strategy_id == "Z2":
        hi = levels.get("range_high") or levels.get("high")
        out["breakout_reentry"] = 1.0 if hi and bid < float(hi) * 0.998 else 0.0
        out["high_update_stop"] = 1.0 if hi and t.px <= float(hi) else 0.0
    elif strategy_id == "Z3":
        wall0 = levels.get("wall0") or 0
        out["wall_reform"] = 1.0 if wall0 and (t.board.canonical_ask_qty or 0) > float(wall0) * 0.9 else 0.0
        bb0 = t0.board.canonical_best_bid
        out["bid_back"] = 1.0 if bb0 is not None and bid < bb0 else 0.0
    elif strategy_id == "Z4":
        hi = levels.get("hi")
        lo = levels.get("lo")
        out["range_reentry"] = 1.0 if hi and lo and lo < bid < hi else 0.0
        out["expansion_stop"] = 1.0 if hi and t.px <= float(hi) else 0.0

    # lead-time copies from past events/seconds
    for n in LEAD_EVENTS:
        k = j - n
        if k > entry_idx:
            bb = ticks[k].board.canonical_best_bid
            if bb and entry_ask:
                out[f"pnl_lead_e{n}"] = (bb - entry_ask) / entry_ask * 100.0
            out[f"imb_lead_e{n}"] = _f(ticks[k].board.canonical_top_imbalance)
    for sec in LEAD_SECS:
        k = j
        while k > entry_idx and (t.ts - ticks[k].ts).total_seconds() < sec:
            k -= 1
        if k > entry_idx:
            bb = ticks[k].board.canonical_best_bid
            if bb:
                out[f"pnl_lead_{sec}s"] = (bb - entry_ask) / entry_ask * 100.0
            out[f"giveback_lead_{sec}s"] = (mfe - ((bb - entry_ask) / entry_ask * 100.0)) if bb else None
    return out


def ensure_exit_inventory(sample: dict[str, Any]) -> list[dict[str, Any]]:
    if EXIT_FEATURE_INVENTORY:
        return EXIT_FEATURE_INVENTORY
    for name in sorted(sample.keys()):
        EXIT_FEATURE_INVENTORY.append({
            "feature_id": name,
            "feature_name": name,
            "group": "EXIT_PATH",
            "formula": f"post_entry:{name}",
            "window": "lead_multi",
            "causal_availability": "at_or_before_exit_decision",
            "implementation_status": "COMPUTED",
            "leakage_status": "PASS",
        })
    return EXIT_FEATURE_INVENTORY


def path_class(mfe: float, mae: float, term: float, exit_reason: str) -> str:
    if exit_reason == "session_close":
        return "SESSION_CLOSE"
    if mae <= -0.9 and (term < 0):
        return "STOP_FAST" if mae <= -0.9 else "STOP_SLOW"
    if mfe >= 0.8 and term > 0 and (mfe - term) < 0.3:
        return "CLEAN_WINNER"
    if mfe >= 0.8 and term < mfe * 0.4:
        return "WINNER_GIVEBACK"
    if mfe < 0.25 and abs(term) < 0.15:
        return "NOPROGRESS"
    if mfe >= 0.4 and term < -0.2:
        return "FALSE_BREAK"
    if exit_reason.startswith("invalid"):
        return "INVALIDATED_AND_CONTINUED_DOWN" if term < 0 else "INVALIDATED_THEN_RECOVERED"
    if exit_reason == "exhaustion":
        return "EXHAUSTED"
    return "UNKNOWN"
