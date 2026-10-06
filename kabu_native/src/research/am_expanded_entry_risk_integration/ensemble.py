"""WIN_DOMINANT ensemble from OOF class probabilities. No cutoff search."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.am_entry_information_expansion import AVAILABLE_REP_MIN
from research.canonical_entry_performance_rebase.analyze import _f, row_key
from research.wait5_session_target_learnability import POS_REP_MIN


def _cell(proba_by_rep: dict[str, dict[str, Any]], rid: str, key: str) -> dict[str, Any]:
    rec = (proba_by_rep.get(rid) or {}).get(key)
    if isinstance(rec, dict):
        return rec
    return {}


def attach_win_dominant(
    rows: list[dict[str, Any]],
    proba_by_rep: dict[str, dict[str, Any]],
    rep_ids: list[str],
) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        k = row_key(r)
        margins: list[float] = []
        joints: list[float] = []
        dom = 0
        for rid in rep_ids:
            rec = _cell(proba_by_rep, rid, k)
            pw = _f(rec.get("P_WIN"))
            pl = _f(rec.get("P_LOSS"))
            pn = _f(rec.get("P_NEUTRAL"))
            if pw is None or pl is None or pn is None:
                continue
            margins.append(float(pw) - max(float(pl), float(pn)))
            joints.append(float(pw) - float(pl))
            if float(pw) > float(pl) and float(pw) > float(pn):
                dom += 1
        n = len(margins)
        med_m = float(np.median(margins)) if margins else None
        med_j = float(np.median(joints)) if joints else None
        eligible = (
            n >= int(AVAILABLE_REP_MIN)
            and int(dom) >= int(POS_REP_MIN)
            and med_m is not None
            and float(med_m) > 0.0
        )
        rec = dict(r)
        rec["AVAILABLE_REP_N"] = n
        rec["WIN_DOMINANT_REP_N"] = int(dom)
        rec["MEDIAN_WIN_MARGIN"] = med_m
        rec["ENSEMBLE_JOINT_SCORE"] = med_j
        rec["AUG_SCORE"] = med_m
        rec["POSITIVE_REP_N"] = int(dom)
        rec["AUGMENT_ELIGIBLE"] = bool(eligible)
        rec["_current_score"] = _f(r.get("current_score"))
        rec["_aug_score"] = med_m
        rec["_win_margin"] = med_m
        rec["_joint_med"] = med_j
        rec["_win_dom_n"] = int(dom)
        rec["_positive_rep_n"] = int(dom)
        rec["_available_rep_n"] = n
        rec["_aug_eligible"] = bool(eligible)
        out.append(rec)
    return out


def joint_map(proba_by_rep: dict[str, dict[str, Any]]) -> dict[str, dict[str, Optional[float]]]:
    out: dict[str, dict[str, Optional[float]]] = {}
    for rid, body in proba_by_rep.items():
        mp: dict[str, Optional[float]] = {}
        for k, rec in (body or {}).items():
            if isinstance(rec, dict):
                j = _f(rec.get("JOINT_SCORE"))
                if j is None:
                    pw = _f(rec.get("P_WIN"))
                    pl = _f(rec.get("P_LOSS"))
                    if pw is not None and pl is not None:
                        j = float(pw) - float(pl)
                mp[k] = j
            else:
                mp[k] = _f(rec)
        out[rid] = mp
    return out
