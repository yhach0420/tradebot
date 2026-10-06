"""Join 9 Ridge OOF preds into median AUG_SCORE. No representation selection."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.am_current_utility_augment import AUG_SCORE_THRESHOLD, AVAILABLE_REP_MIN
from research.canonical_entry_performance_rebase.analyze import _f, row_key
from research.wait5_session_target_learnability import POS_REP_MIN


def ensemble_row(preds: list[Optional[float]]) -> dict[str, Any]:
    xs = [float(v) for v in preds if v is not None and v == v]
    n = len(xs)
    pos = sum(1 for v in xs if v > float(AUG_SCORE_THRESHOLD))
    med = float(np.median(xs)) if xs else None
    eligible = (
        n >= int(AVAILABLE_REP_MIN)
        and med is not None
        and float(med) > float(AUG_SCORE_THRESHOLD)
        and pos >= int(POS_REP_MIN)
    )
    return {
        "AUG_SCORE": med,
        "POSITIVE_REP_N": pos,
        "AVAILABLE_REP_N": n,
        "AUGMENT_ELIGIBLE": bool(eligible),
    }


def attach_ensemble(rows: list[dict[str, Any]], scores_by_rep: dict[str, dict[str, Any]], rep_ids: list[str]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        k = row_key(r)
        preds = []
        per: dict[str, Any] = {}
        for rid in rep_ids:
            v = _f((scores_by_rep.get(rid) or {}).get(k))
            per[rid] = v
            preds.append(v)
        rec = dict(r)
        rec.update(ensemble_row(preds))
        rec["rep_preds"] = per
        rec["_current_score"] = _f(r.get("current_score"))
        rec["_aug_score"] = rec.get("AUG_SCORE")
        rec["_positive_rep_n"] = int(rec.get("POSITIVE_REP_N") or 0)
        rec["_available_rep_n"] = int(rec.get("AVAILABLE_REP_N") or 0)
        rec["_aug_eligible"] = bool(rec.get("AUGMENT_ELIGIBLE"))
        out.append(rec)
    return out
