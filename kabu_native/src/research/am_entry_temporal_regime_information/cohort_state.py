"""Causal temporal / market-regime / CURRENT core-state features. No date. No future."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.am_current_utility_augment.overlay import overlay_replay
from research.am_entry_temporal_regime_information import (
    AM_CLOCK,
    CORE_STATE_FEATURES,
    REGIME_FEATURES,
    TEMPORAL_FEATURES,
)
from research.canonical_entry_performance_rebase.analyze import _f


def _anchor_hm(anchor: str) -> tuple[int, int] | None:
    parts = str(anchor or "").strip().split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None


def temporal_for_anchor(anchor: str) -> dict[str, Any]:
    hm = _anchor_hm(anchor)
    if hm is None or hm not in AM_CLOCK:
        return {"AM_SLOT_INDEX": None, "AM_MINUTES_FROM_OPEN": None}
    return {
        "AM_SLOT_INDEX": int(AM_CLOCK.index(hm)),
        "AM_MINUTES_FROM_OPEN": float(hm[0] * 60 + hm[1] - 9 * 60),
    }


def _finite(xs: list[Any]) -> list[float]:
    out = []
    for v in xs:
        x = _f(v)
        if x is not None:
            out.append(float(x))
    return out


def _median(xs: list[float]) -> Optional[float]:
    return float(np.median(xs)) if xs else None


def _iqr(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(np.percentile(xs, 75) - np.percentile(xs, 25))


def _breadth(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(sum(1 for v in xs if v > 0.0) / len(xs))


def regime_for_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    ret60 = _finite([r.get("mid_ret_60s") for r in group])
    ret180 = _finite([r.get("mid_ret_180s") for r in group])
    return {
        "MARKET_MEDIAN_RET_60S": _median(ret60),
        "MARKET_MEDIAN_RET_180S": _median(ret180),
        "MARKET_BREADTH_UP_60S": _breadth(ret60),
        "MARKET_BREADTH_UP_180S": _breadth(ret180),
        "MARKET_IQR_RET_60S": _iqr(ret60),
        "MARKET_IQR_RET_180S": _iqr(ret180),
        "MARKET_MEDIAN_SPREAD_BPS": _median(_finite([r.get("spread_bps") for r in group])),
        "MARKET_MEDIAN_IMBALANCE": _median(_finite([r.get("imbalance") for r in group])),
        "MARKET_MEDIAN_EVENT_RATE_60S": _median(_finite([r.get("event_rate_60s") for r in group])),
        "MARKET_MEDIAN_VOLUME_RATE_60S": _median(_finite([r.get("volume_rate_60s") for r in group])),
        "MARKET_MEDIAN_VOLUME_PERCENTILE_60S": _median(
            _finite([r.get("volume_percentile_60s") for r in group])
        ),
        "MARKET_MEDIAN_TRADING_VALUE_PERCENTILE_180S": _median(
            _finite([r.get("trading_value_percentile_180s") for r in group])
        ),
        "MARKET_MEDIAN_DISTANCE_VWAP_BPS": _median(
            _finite([r.get("distance_from_vwap_bps") for r in group])
        ),
        "MARKET_MEDIAN_REBOUND_LOW_BPS": _median(
            _finite([r.get("rebound_from_recent_low_bps") for r in group])
        ),
    }


def attach_temporal_regime(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    by: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
    unknown_slot = 0
    for r in rows:
        t0 = _f(r.get("t0"))
        if t0 is None:
            continue
        by[(str(r.get("date") or ""), float(t0))].append(r)
    regime_by = {k: regime_for_group(g) for k, g in by.items()}
    out = []
    for r in rows:
        rec = dict(r)
        tmp = temporal_for_anchor(str(r.get("anchor") or ""))
        if tmp.get("AM_SLOT_INDEX") is None:
            unknown_slot += 1
        rec.update(tmp)
        t0 = _f(r.get("t0"))
        if t0 is None:
            for k in REGIME_FEATURES:
                rec[k] = None
        else:
            rec.update(regime_by.get((str(r.get("date") or ""), float(t0))) or {})
        out.append(rec)
    return out, unknown_slot


def attach_core_state(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snap: dict[tuple[str, float], dict[str, Any]] = {}
    overlay_replay(rows, include_augment=False, core_state_out=snap)
    out = []
    for r in rows:
        rec = dict(r)
        t0 = _f(r.get("t0"))
        st = snap.get((str(r.get("date") or ""), float(t0))) if t0 is not None else None
        if st:
            rec.update(st)
        else:
            for k in CORE_STATE_FEATURES:
                rec.setdefault(k, None)
        out.append(rec)
    return out


def attach_all_cohort_state(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tagged, unknown_slot = attach_temporal_regime(rows)
    tagged = attach_core_state(tagged)
    return tagged, {
        "AM_SLOT_UNKNOWN_N": int(unknown_slot),
        "TEMPORAL_FEATURES": list(TEMPORAL_FEATURES),
        "REGIME_FEATURES": list(REGIME_FEATURES),
        "CORE_STATE_FEATURES": list(CORE_STATE_FEATURES),
    }


def unique_cohorts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, float], dict[str, Any]] = {}
    for r in rows:
        t0 = _f(r.get("t0"))
        if t0 is None:
            continue
        k = (str(r.get("date") or ""), float(t0))
        if k not in seen:
            seen[k] = r
    return list(seen.values())


def extra_medians(rows: list[dict[str, Any]], extra_feats: list[str]) -> dict[str, Optional[float]]:
    cohorts = unique_cohorts(rows)
    out: dict[str, Optional[float]] = {}
    for f in extra_feats:
        xs = _finite([c.get(f) for c in cohorts])
        out[f] = float(np.median(xs)) if xs else None
    return out


def missingness_lodo(
    rows: list[dict[str, Any]], extra_feats: list[str], days: list[str]
) -> list[dict[str, Any]]:
    if not extra_feats:
        return []
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in unique_cohorts(rows):
        by_day[str(r.get("date") or "")].append(r)
    stats = {f: {"VALID_COHORT_N": 0, "IMPUTED_COHORT_N": 0} for f in extra_feats}
    for outer in days:
        train = []
        for d in days:
            if d != outer:
                train.extend(by_day.get(d) or [])
        med = extra_medians(train, extra_feats)
        for c in by_day.get(str(outer)) or []:
            for f in extra_feats:
                if _f(c.get(f)) is not None:
                    stats[f]["VALID_COHORT_N"] += 1
                elif med.get(f) is not None:
                    stats[f]["IMPUTED_COHORT_N"] += 1
                else:
                    stats[f]["IMPUTED_COHORT_N"] += 1
    return [{"feature": f, **stats[f]} for f in extra_feats]
