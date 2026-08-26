"""Pure accounting helpers. No threshold / WAIT / C1 changes."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np


ENTRY_ORDER = (
    "NOT_SELECTED",
    "BLOCKED_OPEN",
    "BLOCKED_PENDING",
    "BLOCKED_CAP",
    "OTHER_REJECT",
    "ADMITTED",
)


def exclusive_entry_terminal(
    *,
    live_admitted: bool,
    pending_before: bool,
    open_before: bool,
    in_elig: bool,
    feature_evaluable: bool,
    score_evaluable: bool,
    joint_admitted: bool,
) -> str:
    """Canonical ENTRY-stage exclusive terminal for one confirmed Dynamic anchor.

    Order matches P2-2 if/elif (admitted → pending → open → feature-fail → residual),
    with the P2-2 residual `blocked_cap` split:

    - BLOCKED_CAP: simulate_joint admitted, live did not (live exposure).
    - NOT_SELECTED: evaluable and in cohort, simulate_joint did not admit
      (joint CAPACITY_BLOCKED / rank beyond POSITION_CAP on empty joint occupancy).
    """
    if live_admitted:
        return "ADMITTED"
    if pending_before:
        return "BLOCKED_PENDING"
    if open_before:
        return "BLOCKED_OPEN"
    if (not in_elig) or (not feature_evaluable) or (not score_evaluable):
        return "OTHER_REJECT"
    if joint_admitted:
        return "BLOCKED_CAP"
    return "NOT_SELECTED"


def _count(d: dict[str, int], *keys: str) -> int:
    for k in keys:
        if k in d and d[k] is not None:
            return int(d[k])
    return 0


def funnel_integrity(counts: dict[str, int]) -> dict[str, Any]:
    confirmed = _count(counts, "confirmed")
    parts = [_count(counts, k) for k in ENTRY_ORDER]
    admitted = _count(counts, "ADMITTED", "admitted")
    fills = _count(counts, "FILLED", "fills")
    expired = _count(counts, "EXPIRED", "expired")
    entry_sum = sum(parts)
    fill_sum = fills + expired
    entry_ok = entry_sum == confirmed
    fill_ok = fill_sum == admitted
    return {
        "confirmed": confirmed,
        "entry_sum": entry_sum,
        "entry_ok": entry_ok,
        "admitted": admitted,
        "fills": fills,
        "expired": expired,
        "fill_sum": fill_sum,
        "fill_ok": fill_ok,
        "pass": bool(entry_ok and fill_ok),
        "equation_entry": "confirmed = NOT_SELECTED + BLOCKED_OPEN + BLOCKED_PENDING + BLOCKED_CAP + OTHER_REJECT + ADMITTED",
        "equation_fill": "ADMITTED = FILLED + EXPIRED",
    }


def pct_block(values: list[Any]) -> dict[str, Any]:
    xs = []
    for v in values:
        if v is None:
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if x == x and np.isfinite(x):
            xs.append(x)
    if not xs:
        return {
            "count": 0,
            "median": None,
            "p25": None,
            "p75": None,
            "p90": None,
        }
    a = np.asarray(xs, dtype=float)
    return {
        "count": int(a.size),
        "median": float(np.median(a)),
        "p25": float(np.percentile(a, 25)),
        "p75": float(np.percentile(a, 75)),
        "p90": float(np.percentile(a, 90)),
    }


def pnl_stats(trades: list[dict[str, Any]], *, pnl_key: str = "pnl_yen_100") -> dict[str, Any]:
    pnls = [float(t.get(pnl_key) or 0.0) for t in trades]
    w = sum(1 for p in pnls if p > 1e-9)
    l = sum(1 for p in pnls if p < -1e-9)
    d = len(pnls) - w - l
    gp = sum(p for p in pnls if p > 0)
    gl = sum(-p for p in pnls if p < 0)
    if gl <= 1e-12:
        pf: Optional[Any] = None if gp <= 1e-12 else float("inf")
    else:
        pf = gp / gl
    return {
        "count": len(trades),
        "win": w,
        "loss": l,
        "draw": d,
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "pnl": round(sum(pnls), 2),
        "PF": pf,
    }


def pf_out(v: Any) -> Any:
    if v is None:
        return None
    if v == float("inf"):
        return "Infinity"
    return v


def rate(num: int, den: int) -> Optional[float]:
    if den <= 0:
        return None
    return float(num) / float(den)


def trade_match_key(t: dict[str, Any]) -> tuple:
    return (
        str(t.get("date")),
        str(t.get("symbol")),
        round(float(t.get("fill_time") or 0.0), 6),
        round(float(t.get("fill_price") or 0.0), 4),
        round(float(t.get("pnl_yen_100") or 0.0), 4),
    )
