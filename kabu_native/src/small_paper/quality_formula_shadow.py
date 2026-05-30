"""
Phase204: Shadow quality formula (persistence + trading_value) — logging only.

Parallel to continuation_quality_score; does NOT affect entry decisions.
Fixed normalization priors from Phase203 (not tuned per session).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

# Fixed priors — do not tune per day
TV_LOG_MIN = 9.0
TV_LOG_MAX = 10.5
DURATION_SCALE = 14.0

SHADOW_FIELD_KEYS = (
    "shadow_quality_score",
    "shadow_quality_rank",
    "current_quality_rank",
)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))


def persistence_component(trade: Mapping[str, Any]) -> float:
    dur = _float(trade.get("max_continuation_duration"))
    if dur is None:
        raw = trade.get("quality_components_json")
        if raw:
            try:
                import json

                qc = json.loads(raw) if isinstance(raw, str) else dict(raw)
                p = _float(qc.get("continuation_persistence"))
                if p is not None:
                    return float(p)
            except (json.JSONDecodeError, TypeError):
                pass
        dur = 0.0
    return _clamp01(float(dur or 0) / DURATION_SCALE)


def trading_value_norm(trade: Mapping[str, Any]) -> float:
    tv = _float(trade.get("trading_value"))
    if tv is None or tv <= 0:
        return 0.0
    logv = math.log10(tv)
    return _clamp01((logv - TV_LOG_MIN) / (TV_LOG_MAX - TV_LOG_MIN))


def compute_shadow_quality_score(trade: Mapping[str, Any]) -> float:
    """Phase203 variant D: 0.5 * persistence + 0.5 * trading_value_norm."""
    p = persistence_component(trade)
    tv = trading_value_norm(trade)
    return round(0.5 * p + 0.5 * tv, 4)


def compute_shadow_quality_fields(trade: Mapping[str, Any]) -> dict[str, Any]:
    return {"shadow_quality_score": compute_shadow_quality_score(trade)}


def _rank_by_score(rows: Sequence[Mapping[str, Any]], score_key: str) -> dict[tuple[str, str], int]:
    keyed = [
        (
            (str(r.get("symbol") or ""), str(r.get("entry_time") or "")),
            _float(r.get(score_key)) or 0.0,
        )
        for r in rows
    ]
    keyed.sort(key=lambda x: x[1], reverse=True)
    out: dict[tuple[str, str], int] = {}
    rank = 0
    prev_score: Optional[float] = None
    for i, (key, score) in enumerate(keyed, start=1):
        if prev_score is None or score != prev_score:
            rank = i
            prev_score = score
        out[key] = rank
    return out


def assign_session_quality_ranks(
    accepted_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> None:
    """Assign current_quality_rank and shadow_quality_rank within session (logging only)."""
    current_ranks = _rank_by_score(accepted_rows, "continuation_quality_score")
    shadow_ranks = _rank_by_score(accepted_rows, "shadow_quality_score")
    for row in accepted_rows:
        key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
        row["current_quality_rank"] = current_ranks.get(key)
        row["shadow_quality_rank"] = shadow_ranks.get(key)
    for ev in events:
        if ev.get("event_type") != "accepted":
            continue
        key = (str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))
        if key in current_ranks:
            ev["current_quality_rank"] = current_ranks[key]
        if key in shadow_ranks:
            ev["shadow_quality_rank"] = shadow_ranks[key]


def _pf(pnls: Sequence[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return wins / gl


def _quantile_threshold(values: Sequence[float], q: float) -> float:
    ys = sorted(values)
    if not ys:
        return 0.0
    idx = (len(ys) - 1) * q
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return ys[lo]
    w = idx - lo
    return ys[lo] * (1 - w) + ys[hi] * w


def _rankdata(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    rx = _rankdata(xs)
    ry = _rankdata(ys)
    mx = sum(rx) / len(rx)
    my = sum(ry) / len(ry)
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(len(xs)))
    dx = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(len(xs))))
    dy = math.sqrt(sum((ry[i] - my) ** 2 for i in range(len(ys))))
    if dx <= 0 or dy <= 0:
        return None
    return num / (dx * dy)


@dataclass
class ScoredTrade:
    symbol: str
    entry_time: str
    session_id: str
    pnl_pct: float
    current_score: float
    shadow_score: float
    current_rank: int
    shadow_rank: int


def top20_tier_metrics(
    trades: Sequence[ScoredTrade],
    *,
    score_attr: str,
) -> dict[str, Any]:
    scores = [getattr(t, score_attr) for t in trades]
    p80 = _quantile_threshold(scores, 0.80)
    subset = [t for t, s in zip(trades, scores) if s >= p80]
    pnls = [t.pnl_pct for t in subset]
    return {
        "count": len(subset),
        "threshold": round(p80, 4),
        "profit_factor": round(_pf(pnls), 4) if _pf(pnls) is not None else None,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 4) if pnls else None,
    }


def session_top20_summary_from_rows(
    accepted_rows: Sequence[Mapping[str, Any]],
    pnl_by_key: Mapping[tuple[str, str], float],
) -> dict[str, Any]:
    rows: list[ScoredTrade] = []
    for r in accepted_rows:
        key = (str(r.get("symbol") or ""), str(r.get("entry_time") or ""))
        pnl = pnl_by_key.get(key)
        if pnl is None:
            continue
        cur = _float(r.get("continuation_quality_score")) or 0.0
        sh = _float(r.get("shadow_quality_score"))
        if sh is None:
            sh = compute_shadow_quality_score(r)
        rows.append(
            ScoredTrade(
                symbol=key[0],
                entry_time=key[1],
                session_id="",
                pnl_pct=float(pnl),
                current_score=float(cur),
                shadow_score=float(sh),
                current_rank=int(r.get("current_quality_rank") or 0),
                shadow_rank=int(r.get("shadow_quality_rank") or 0),
            )
        )
    if not rows:
        return {
            "current_quality_top20_pf": None,
            "shadow_quality_top20_pf": None,
            "current_quality_top20_total_pnl_pct": None,
            "shadow_quality_top20_total_pnl_pct": None,
        }
    cur_top = top20_tier_metrics(rows, score_attr="current_score")
    sh_top = top20_tier_metrics(rows, score_attr="shadow_score")
    return {
        "current_quality_top20_pf": cur_top["profit_factor"],
        "shadow_quality_top20_pf": sh_top["profit_factor"],
        "current_quality_top20_total_pnl_pct": cur_top["total_pnl_pct"],
        "shadow_quality_top20_total_pnl_pct": sh_top["total_pnl_pct"],
        "current_quality_top20_count": cur_top["count"],
        "shadow_quality_top20_count": sh_top["count"],
    }


def pnl_map_from_events(events: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for ev in events:
        if ev.get("event_type") != "observer_exit":
            continue
        pnl = _float(ev.get("pnl_pct"))
        key = (str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))
        if pnl is not None and key[1]:
            out[key] = pnl
    return out


def finalize_session_quality_shadow(
    accepted_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assign ranks and return summary fields for small_paper_summary.json."""
    for row in accepted_rows:
        row.update(compute_shadow_quality_fields(row))
    assign_session_quality_ranks(accepted_rows, events)
    pnl_by_key = pnl_map_from_events(events)
    summary = session_top20_summary_from_rows(accepted_rows, pnl_by_key)
    summary["quality_formula_shadow_enabled"] = True
    return summary
