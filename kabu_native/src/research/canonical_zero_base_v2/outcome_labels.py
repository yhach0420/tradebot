"""ENTRY outcome labels from future canonical Bid path (TRAIN-defined class bounds)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from research.canonical_zero_base_v2.constants import COST_BPS, HORIZONS_SEC, LOT
from research.canonical_zero_base_v2.loader import Tick


@dataclass
class OutcomeLabel:
    anchor_id: str
    entry_ask: float
    evaluable: bool
    class_name: str
    metrics: dict[str, Any] = field(default_factory=dict)
    horizon_metrics: dict[int, dict[str, float]] = field(default_factory=dict)


def _tick_size(px: float) -> float:
    if px < 1000:
        return 0.1
    if px < 3000:
        return 0.5
    if px < 5000:
        return 1.0
    if px < 10000:
        return 1.0
    if px < 30000:
        return 5.0
    return 10.0


def path_metrics(ticks: Sequence[Tick], entry_idx: int, entry_ask: float, *, max_sec: float = 300.0) -> dict[str, Any]:
    if entry_ask <= 0 or entry_idx >= len(ticks) - 1:
        return {"evaluable": False}
    t0 = ticks[entry_idx].ts
    mfe = mae = 0.0
    t_mfe = t_mae = None
    first_pos = first_adv = None
    pos_dur = neg_dur = 0.0
    last_ts = t0
    terminal = None
    path_auc = 0.0
    never = True
    cost = COST_BPS / 10000.0
    cost_recovery = None
    collapse = 0.0
    large_rise = 0.0
    prev_ret = 0.0
    pos_to_neg = neg_to_pos = False
    was_pos = was_neg = False
    for j in range(entry_idx + 1, len(ticks)):
        t = ticks[j]
        dt = (t.ts - t0).total_seconds()
        if dt > max_sec:
            break
        bid = t.board.canonical_best_bid
        if bid is None or bid <= 0:
            continue
        ret = (bid - entry_ask) / entry_ask * 100.0
        net = ret - COST_BPS / 100.0  # approx pct points of 5bps RT half-ish; use yen later
        terminal = ret
        large_rise = max(large_rise, ret)
        collapse = min(collapse, ret)
        if ret > mfe:
            mfe, t_mfe = ret, dt
        if ret < mae:
            mae, t_mae = ret, dt
        if never and bid > entry_ask * (1 + cost):
            never = False
            if cost_recovery is None:
                cost_recovery = dt
        if first_pos is None and ret > 0:
            first_pos = dt
        if first_adv is None and ret < 0:
            first_adv = dt
        step = (t.ts - last_ts).total_seconds()
        if ret > 0:
            pos_dur += step
            if was_neg:
                neg_to_pos = True
            was_pos = True
            was_neg = False
        elif ret < 0:
            neg_dur += step
            if was_pos:
                pos_to_neg = True
            was_neg = True
            was_pos = False
        path_auc += (ret + prev_ret) / 2.0 * step
        prev_ret = ret
        last_ts = t.ts
    if terminal is None:
        return {"evaluable": False}
    net_mfe = mfe - COST_BPS / 100.0
    # yen terminal
    # find last bid within horizon
    last_bid = None
    for j in range(entry_idx + 1, len(ticks)):
        if (ticks[j].ts - t0).total_seconds() > max_sec:
            break
        b = ticks[j].board.canonical_best_bid
        if b is not None and b > 0:
            last_bid = b
    if last_bid is None:
        return {"evaluable": False}
    raw = (last_bid - entry_ask) * LOT
    c = entry_ask * LOT * COST_BPS / 10000.0 + last_bid * LOT * COST_BPS / 10000.0
    return {
        "evaluable": True,
        "mfe": mfe,
        "mae": mae,
        "net_mfe_after_5bps": net_mfe,
        "net_terminal_return_pct": terminal,
        "net_terminal_yen": raw - c,
        "time_to_mfe": t_mfe,
        "time_to_mae": t_mae,
        "path_auc": path_auc,
        "positive_duration": pos_dur,
        "negative_duration": neg_dur,
        "first_positive_time": first_pos,
        "first_adverse_time": first_adv,
        "positive_to_negative_reversal": pos_to_neg,
        "negative_to_positive_recovery": neg_to_pos,
        "never_profitable": never,
        "cost_recovery_time": cost_recovery,
        "large_rise_magnitude": large_rise,
        "collapse_magnitude": collapse,
        "tick_size": _tick_size(entry_ask),
        "spread_at_entry": ticks[entry_idx].board.canonical_spread,
    }


def classify_outcome(m: dict[str, Any], *, bounds: dict[str, float]) -> str:
    if not m.get("evaluable"):
        return "UNKNOWN"
    if m.get("never_profitable"):
        return "NEVER_PROFITABLE"
    mfe = float(m.get("mfe") or 0)
    mae = float(m.get("mae") or 0)
    t_mfe = float(m.get("time_to_mfe") or 999)
    term = float(m.get("net_terminal_return_pct") or 0)
    win_fast = bounds.get("winner_fast_mfe", 0.8)
    win_slow_t = bounds.get("winner_slow_t", 60)
    noprogress_mfe = bounds.get("noprogress_mfe", 0.25)
    stop_mae = bounds.get("stop_mae", -0.8)
    if mfe >= win_fast and t_mfe <= 30 and term > 0:
        return "WINNER_FAST"
    if mfe >= win_fast and t_mfe > win_slow_t and term > 0:
        return "WINNER_SLOW"
    if mfe >= win_fast and m.get("positive_to_negative_reversal") and term <= 0:
        return "WINNER_REVERSAL"
    if 0.2 <= mfe < win_fast and term > 0:
        return "SMALL_WIN"
    if mae <= stop_mae and (m.get("time_to_mae") or 999) <= 30:
        return "EARLY_STOP_PATH"
    if mae <= stop_mae:
        return "LATE_STOP_PATH"
    if mfe < noprogress_mfe and abs(term) < 0.15:
        return "NOPROGRESS"
    if mfe >= 0.3 and term < -0.2:
        return "FALSE_BREAK"
    if mfe >= win_fast and term > 0:
        return "WINNER_SLOW"
    return "UNKNOWN"


def fit_class_bounds(train_metrics: Sequence[dict[str, Any]]) -> dict[str, float]:
    mfes = sorted(float(m["mfe"]) for m in train_metrics if m.get("evaluable") and m.get("mfe") is not None)
    maes = sorted(float(m["mae"]) for m in train_metrics if m.get("evaluable") and m.get("mae") is not None)
    if not mfes:
        return {"winner_fast_mfe": 0.8, "winner_slow_t": 60.0, "noprogress_mfe": 0.25, "stop_mae": -0.8}
    # coarse TRAIN quantiles only
    def q(xs, p):
        return xs[int(max(0, min(len(xs) - 1, round((len(xs) - 1) * p))))]

    return {
        "winner_fast_mfe": max(0.4, q(mfes, 0.70)),
        "winner_slow_t": 60.0,
        "noprogress_mfe": max(0.1, q(mfes, 0.35)),
        "stop_mae": min(-0.4, q(maes, 0.20)),
    }


def label_anchor(
    ticks: Sequence[Tick],
    entry_idx: int,
    entry_ask: float,
    anchor_id: str,
    *,
    bounds: dict[str, float],
) -> OutcomeLabel:
    base = path_metrics(ticks, entry_idx, entry_ask, max_sec=300)
    hz = {}
    for h in HORIZONS_SEC:
        mh = path_metrics(ticks, entry_idx, entry_ask, max_sec=float(h))
        if mh.get("evaluable"):
            hz[h] = {
                "mfe": float(mh["mfe"]),
                "mae": float(mh["mae"]),
                "terminal": float(mh["net_terminal_return_pct"]),
                "never_profitable": 1.0 if mh["never_profitable"] else 0.0,
            }
    cls = classify_outcome(base, bounds=bounds)
    return OutcomeLabel(
        anchor_id=anchor_id,
        entry_ask=entry_ask,
        evaluable=bool(base.get("evaluable")),
        class_name=cls,
        metrics=base,
        horizon_metrics=hz,
    )
