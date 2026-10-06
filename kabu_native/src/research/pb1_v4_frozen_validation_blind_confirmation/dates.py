"""Split lookback vs Frozen Validation vs sealed prospective. Date lists only; no bars."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_frozen_validation_blind_confirmation import (
    EVAL_FIRST,
    EVAL_LAST,
    LOOKBACK_TAIL_N,
    OC_FIRST,
    OC_LAST,
    PROSPECTIVE_FROM,
)


def partition_dates(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    disc = [str(d) for d in list(split.get("discovery_dates") or [])]
    conf = [str(d) for d in list(split.get("confirmation_dates") or [])]
    fv_raw = sorted(str(d) for d in list(split.get("frozen_validation_dates") or []))
    fv = [d for d in fv_raw if EVAL_FIRST <= d <= EVAL_LAST]
    prior = sorted(d for d in set(disc) | set(conf) if d < EVAL_FIRST)
    lookback = prior[-int(LOOKBACK_TAIL_N) :] if len(prior) > int(LOOKBACK_TAIL_N) else list(prior)
    leak_fv = [d for d in fv_raw if d < EVAL_FIRST or d > EVAL_LAST]
    walk_dates = sorted(set(lookback) | set(fv))
    forbidden = sorted({d for d in disc + conf + fv_raw if d >= PROSPECTIVE_FROM} | {d for d in walk_dates if d >= PROSPECTIVE_FROM})
    overlap_prospective = sorted(d for d in walk_dates if d >= PROSPECTIVE_FROM)
    oc_in_eval = [d for d in fv if OC_FIRST <= d <= OC_LAST]
    ok = (
        bool(fv)
        and fv[0] == EVAL_FIRST
        and fv[-1] == EVAL_LAST
        and (not lookback or lookback[-1] < EVAL_FIRST)
        and not leak_fv
        and not overlap_prospective
        and not oc_in_eval
        and not (set(lookback) & set(fv))
    )
    return {
        "ok": ok,
        "lookback_dates": lookback,
        "frozen_validation_dates": fv,
        "walk_dates": walk_dates,
        "forbidden_dates": forbidden,
        "lookback_n": len(lookback),
        "frozen_validation_n": len(fv),
        "lookback_first": lookback[0] if lookback else None,
        "lookback_last": lookback[-1] if lookback else None,
        "eval_first": fv[0] if fv else None,
        "eval_last": fv[-1] if fv else None,
        "LOOKBACK_TAIL_N": int(LOOKBACK_TAIL_N),
        "lookback_role": "FEATURE_HISTORY_ONLY",
        "lookback_source": "Discovery + Old Confirmation already development-exposed",
        "eval_role": "BLIND_EVALUATION",
        "old_confirmation_role": "DEVELOPMENT_EXPOSED_AFTER_REVIEW_NOT_REUSED_AS_BLIND",
        "prospective_role": "SEALED",
        "leak_fv_outside_range": leak_fv,
        "overlap_walk_prospective": overlap_prospective,
        "old_confirmation_dates_in_eval": oc_in_eval,
        "reason": None if ok else "frozen_validation_date_partition_invalid",
    }
