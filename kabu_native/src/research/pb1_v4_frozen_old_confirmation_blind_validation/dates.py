"""Split lookback vs Old Confirmation vs sealed holdouts. Date lists only; no bars."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_frozen_old_confirmation_blind_validation import (
    EVAL_FIRST,
    EVAL_LAST,
    FV_FIRST,
    FV_LAST,
    LOOKBACK_TAIL_N,
    PROSPECTIVE_FROM,
)


def partition_dates(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    disc = sorted(str(d) for d in list(split.get("discovery_dates") or []))
    conf_raw = sorted(str(d) for d in list(split.get("confirmation_dates") or []))
    fv_raw = sorted(str(d) for d in list(split.get("frozen_validation_dates") or []))
    conf = [d for d in conf_raw if EVAL_FIRST <= d <= EVAL_LAST]
    fv = [d for d in fv_raw if FV_FIRST <= d <= FV_LAST]
    disc_before = [d for d in disc if d < EVAL_FIRST]
    lookback = disc_before[-int(LOOKBACK_TAIL_N) :] if len(disc_before) > int(LOOKBACK_TAIL_N) else list(disc_before)
    leak_conf = [d for d in conf_raw if d < EVAL_FIRST or d > EVAL_LAST]
    leak_fv_in_conf = [d for d in conf if FV_FIRST <= d <= FV_LAST]
    walk_dates = sorted(set(lookback) | set(conf))
    forbidden = set(fv) | {d for d in disc + conf_raw + fv_raw if d >= PROSPECTIVE_FROM}
    overlap_walk_fv = sorted(set(walk_dates) & set(fv))
    overlap_walk_prospective = sorted(d for d in walk_dates if d >= PROSPECTIVE_FROM)
    ok = (
        bool(conf)
        and conf[0] == EVAL_FIRST
        and conf[-1] == EVAL_LAST
        and (not lookback or lookback[-1] < EVAL_FIRST)
        and not leak_conf
        and not leak_fv_in_conf
        and not overlap_walk_fv
        and not overlap_walk_prospective
        and not (set(lookback) & set(conf))
    )
    return {
        "ok": ok,
        "lookback_dates": lookback,
        "confirmation_dates": conf,
        "frozen_validation_dates": fv,
        "walk_dates": walk_dates,
        "forbidden_dates": sorted(forbidden),
        "lookback_n": len(lookback),
        "confirmation_n": len(conf),
        "frozen_validation_n": len(fv),
        "lookback_first": lookback[0] if lookback else None,
        "lookback_last": lookback[-1] if lookback else None,
        "confirmation_first": conf[0] if conf else None,
        "confirmation_last": conf[-1] if conf else None,
        "LOOKBACK_TAIL_N": int(LOOKBACK_TAIL_N),
        "lookback_role": "FEATURE_HISTORY_ONLY",
        "confirmation_role": "BLIND_EVALUATION",
        "frozen_validation_role": "SEALED",
        "prospective_role": "SEALED",
        "leak_confirmation_outside_range": leak_conf,
        "overlap_walk_fv": overlap_walk_fv,
        "overlap_walk_prospective": overlap_walk_prospective,
        "reason": None if ok else "confirmation_date_partition_invalid",
    }
