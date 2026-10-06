"""Corrected baseline aggregates, period recheck, supersede record."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from research.anchor_10min_opportunity.grids import tod_family
from research.anchor_timing_robustness.metrics import maxdd, trade_stats
from research.edge_decay_rca.analyze import exact_bridge, tag_entry_kind
from research.passive_fill_corrected_rebase import (
    DEVELOPMENT_DAYS,
    LATE_DAY,
    POST_DAYS,
    SUPERSEDE_STATUS,
    SUPERSEDED_ITEMS,
    SUPERSEDED_PATHS,
    VERDICT_DECAY,
    VERDICT_NO_DECAY,
)
from research.passive_fill_itayose_reconciliation.analyze import concentration, family_block, headline

ROBUST_TOD = {
    "OPEN_EARLY": {"09:05", "09:15"},
    "AM_MID": {"09:25", "09:40", "10:00", "10:20"},
    "AM_LATE": {"10:40", "11:00"},
    "PM_OPEN": {"12:40"},
    "PM_MID": {"13:00", "13:20", "13:40", "14:00", "14:20"},
    "PM_LATE": {"14:40", "15:00"},
}


def _pnl(t: dict[str, Any]) -> float:
    return float(t.get("pnl_yen_100") or 0.0)


def subset_days(trades: list[dict[str, Any]], days) -> list[dict[str, Any]]:
    want = set(days)
    return [t for t in trades if str(t.get("date")) in want]


def period_of(day: str) -> str:
    if day in DEVELOPMENT_DAYS:
        return "DEV10"
    if day in POST_DAYS:
        return "POST7"
    if day == LATE_DAY:
        return "LATE_0827"
    return "OTHER"


def robust_tod(anchor: str) -> str:
    lab = str(anchor)
    for name, labels in ROBUST_TOD.items():
        if lab in labels:
            return name
    return "OTHER"


def group_stats(trades: list[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
    by: dict[str, list] = defaultdict(list)
    for t in trades:
        by[str(key_fn(t))].append(t)
    rows = []
    for k, xs in by.items():
        st = headline(xs)
        rows.append({"key": k, "n": len(xs), "pnl": st.get("pnl"), "PF": st.get("PF"), "maxDD": st.get("maxDD")})
    rows.sort(key=lambda r: float(r.get("pnl") or 0.0), reverse=True)
    return rows


def entry_kind_block(trades: list[dict[str, Any]]) -> dict[str, Any]:
    tagged = tag_entry_kind(trades)
    first = [t for t in tagged if t.get("entry_kind") == "FIRST_ENTRY"]
    reent = [t for t in tagged if t.get("entry_kind") == "REENTRY"]
    return {
        "first_entry": headline(first),
        "re_entry": headline(reent),
        "tagged": tagged,
    }


def session_block(trades: list[dict[str, Any]]) -> dict[str, Any]:
    fam = family_block(trades)
    robust = {}
    for name in ROBUST_TOD:
        xs = [t for t in trades if robust_tod(str(t.get("anchor_time") or "")) == name]
        robust[name] = headline(xs)
    return {**fam, "robust_tod": robust}


def period_pack(trades: list[dict[str, Any]], days, *, include_0827: bool) -> dict[str, Any]:
    xs = subset_days(trades, days)
    ek = entry_kind_block(xs)
    st = headline(xs)
    n = len(xs)
    return {
        "days": list(days),
        "trades": st.get("trades") or n,
        "pnl": st.get("pnl"),
        "PF": st.get("PF"),
        "maxDD": st.get("maxDD"),
        "pnl_per_trade": (float(st.get("pnl") or 0.0) / n) if n else None,
        "win": st.get("win"),
        "loss": st.get("loss"),
        "draw": st.get("draw"),
        "AM": headline([t for t in xs if str(t.get("session")) == "AM"]),
        "PM": headline([t for t in xs if str(t.get("session")) == "PM"]),
        "OPEN_EARLY": headline(
            [t for t in xs if tod_family(str(t.get("anchor_time") or "")) == "OPEN_EARLY"]
        ),
        "first_entry": ek["first_entry"],
        "re_entry": ek["re_entry"],
        "include_0827": include_0827,
    }


def period_recheck(trades: list[dict[str, Any]], *, include_0827: bool) -> dict[str, Any]:
    post_days = POST_DAYS + ((LATE_DAY,) if include_0827 else ())
    dev = period_pack(trades, DEVELOPMENT_DAYS, include_0827=False)
    post = period_pack(trades, post_days, include_0827=include_0827)
    post7 = period_pack(trades, POST_DAYS, include_0827=False)
    bridge = exact_bridge(subset_days(trades, DEVELOPMENT_DAYS), subset_days(trades, POST_DAYS))
    d_pnl = float(dev.get("pnl") or 0.0)
    p_pnl = float(post.get("pnl") or 0.0)
    d_pt = dev.get("pnl_per_trade")
    p_pt = post.get("pnl_per_trade")
    d_pf = dev.get("PF")
    p_pf = post.get("PF")

    def _pf(v: Any) -> Optional[float]:
        if v is None or v == "Infinity":
            return None if v is None else 1e9
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    decay = False
    reasons = []
    if d_pnl > 50_000 and p_pnl < 0:
        decay = True
        reasons.append("DEV_PROFITABLE_POST_NEGATIVE")
    if d_pnl > 0 and p_pnl < 0.5 * d_pnl:
        dpf = _pf(d_pf)
        ppf = _pf(p_pf)
        if dpf is not None and (ppf is None or ppf < 0.7 * dpf):
            decay = True
            reasons.append("POST_PNL_AND_PF_COLLAPSE_VS_DEV")
    if d_pt is not None and p_pt is not None and d_pt > 0 and p_pt < 0.5 * d_pt and d_pnl > 0:
        decay = True
        reasons.append("POST_PNL_PER_TRADE_HALF_OF_DEV")
    label = VERDICT_DECAY if decay else VERDICT_NO_DECAY
    return {
        "DEV10": dev,
        "POST7": post7,
        "POST7_PLUS_LATE" if include_0827 else "POST7_ONLY": post,
        "exact_bridge_DEV10_vs_POST7": bridge,
        "NEW_PERIOD_DECAY": decay,
        "period_decay_status": label,
        "reasons": reasons,
        "RCA_STARTED": False,
    }


def baseline_bundle(trades: list[dict[str, Any]]) -> dict[str, Any]:
    ek = entry_kind_block(trades)
    sess = session_block(trades)
    tagged = ek.pop("tagged")
    return {
        "headline": headline(trades),
        "sessions": sess,
        "entry_kind": ek,
        "concentration": concentration(trades),
        "by_day": group_stats(trades, lambda t: t.get("date")),
        "by_symbol": group_stats(trades, lambda t: t.get("symbol")),
        "by_anchor": group_stats(trades, lambda t: t.get("anchor_time")),
        "tagged_n": len(tagged),
    }


def supersede_record() -> dict[str, Any]:
    return {
        "OLD_RESULTS_STATUS": SUPERSEDE_STATUS,
        "items": list(SUPERSEDED_ITEMS),
        "kept_as_historical_reference": list(SUPERSEDED_PATHS),
        "deleted": False,
        "decision_source": False,
    }
