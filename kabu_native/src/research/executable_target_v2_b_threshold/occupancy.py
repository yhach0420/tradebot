"""CAP occupancy with frozen current-ENTRY scores. Percentile is the only free gate."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x36_joint_allocator.replay import simulate_joint
from research.executable_target_v2_b_threshold import MIN_COHORT_FOR_PERCENTILE
from small_paper.v1r_primary_runtime import POSITION_CAP

JST = ZoneInfo("Asia/Tokyo")
HIGHER_SCORE_PREFERRED = True


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def percentile_keep(cohort: list[dict[str, Any]], p: Optional[int]) -> list[dict[str, Any]]:
    """Keep score >= within-anchor percentile. p=None is NO_THRESHOLD. higher=preferred."""
    if p is None:
        return list(cohort)
    scores = [_f(c.get("score")) for c in cohort]
    scores = [s for s in scores if s is not None]
    if len(scores) < int(MIN_COHORT_FOR_PERCENTILE):
        return list(cohort)
    thr = float(np.percentile(np.asarray(scores, dtype=float), float(p)))
    kept = []
    for c in cohort:
        s = _f(c.get("score"))
        if s is not None and s + 1e-15 >= thr:
            kept.append(c)
    return kept


def occupancy_day(candidates: list[dict[str, Any]], percentile: Optional[int]) -> list[dict[str, Any]]:
    """Admit until POSITION_CAP after optional CS percentile gate. Pending reserves 1s."""
    by_clock: dict[float, list[dict[str, Any]]] = {}
    for c in candidates:
        if _f(c.get("score")) is None:
            continue
        rec = dict(c)
        rec["filled"] = bool(c.get("filled"))
        rec["fill_time"] = c.get("fill_time")
        rec["fill_price"] = c.get("fill_price")
        rec["canonical_exit_time"] = c.get("exit_time")
        rec["canonical_exit_ret_bps"] = c.get("canonical_exit_ret_bps")
        rec["limit_price"] = c.get("limit_price")
        rec["bid0"] = c.get("limit_price")
        rec["signal_time"] = float(c["signal_time"])
        rec["date"] = str(c["date"])
        rec["symbol"] = str(c["symbol"])
        by_clock.setdefault(float(c["signal_time"]), []).append(rec)

    events: list[dict[str, Any]] = []
    for t0, grp in by_clock.items():
        kept = percentile_keep(grp, percentile)
        events.extend(kept)
    if not events:
        return []

    def _sfn(e: dict[str, Any]) -> float:
        s = _f(e.get("score"))
        return float(s) if s is not None else float("-inf")

    sim = simulate_joint(events, score_fn=_sfn)
    trades: list[dict[str, Any]] = []
    for e in sim["events"]:
        if not e.get("accepted"):
            continue
        trades.append(
            {
                "date": e.get("date"),
                "session": e.get("session"),
                "symbol": e.get("symbol"),
                "anchor_time": e.get("anchor"),
                "score": e.get("score"),
                "limit": e.get("limit_price"),
                "fill_time": e.get("fill_time"),
                "fill_time_iso": (
                    datetime.fromtimestamp(float(e["fill_time"]), JST).isoformat(timespec="milliseconds")
                    if e.get("fill_time") is not None
                    else None
                ),
                "fill_price": e.get("fill_price"),
                "exit_time": e.get("exit_time") or e.get("canonical_exit_time"),
                "exit_price": e.get("exit_price"),
                "exit_reason": e.get("exit_reason"),
                "pnl_yen_100": e.get("pnl_yen_100"),
                "mfe_yen_100": e.get("mfe_yen_100_floor0"),
                "mfe_yen_100_raw": e.get("mfe_yen_100_raw"),
                "mae_yen_100": e.get("mae_yen_100"),
                "mfe_bps": e.get("mfe_bps_floor0"),
                "mae_bps": e.get("mae_bps"),
                "percentile": percentile,
                "position_cap": POSITION_CAP,
            }
        )
    return trades


def occupancy_all_percentiles(
    candidates: list[dict[str, Any]],
    percentiles: tuple,
) -> dict[Any, list[dict[str, Any]]]:
    return {p: occupancy_day(candidates, p) for p in percentiles}
