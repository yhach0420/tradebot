"""Decision and mechanism helpers. No threshold search."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.canonical_entry_performance_rebase.analyze import _f
from research.passive_wait_policy_reassessment.analyze import _median, _rate


def coverage_improved(*, new_rate: Optional[float], prior_rate: float, new_fill_n: int, prior_fill_n: int, new_trade_n: int, prior_trade_n: int) -> bool:
    rate_up = new_rate is not None and float(new_rate) > float(prior_rate)
    fill_up = int(new_fill_n) > int(prior_fill_n)
    trade_up = int(new_trade_n) > int(prior_trade_n)
    return bool(rate_up or fill_up or trade_up)


def preservation_pass(pres: dict[str, Any]) -> bool:
    keys = (
        "CURRENT_ADMISSION_LOST_N",
        "CURRENT_FILL_LOST_N",
        "CURRENT_EXIT_MISMATCH_N",
        "CURRENT_PNL_MISMATCH_N",
        "CURRENT_CAP_INTERFERENCE_N",
        "CURRENT_SAME_SYMBOL_INTERFERENCE_N",
    )
    return all(int(pres.get(k) or 0) == 0 for k in keys)


def cohort_key(c: dict[str, Any]) -> tuple:
    return (str(c.get("date") or ""), float(c.get("t0") or 0.0))


def set_stats(cands: list[dict[str, Any]]) -> dict[str, Any]:
    pfill = [_f(c.get("P_FILL5")) for c in cands]
    pfill = [float(v) for v in pfill if v is not None]
    aug = [_f(c.get("AUG_SCORE")) for c in cands]
    aug = [float(v) for v in aug if v is not None]
    utils = [_f(c.get("utility")) for c in cands]
    utils = [float(v) for v in utils if v is not None]
    fill_n = sum(1 for c in cands if int(c.get("Y_FILL5") or 0) == 1)
    fill_utils = [float(_f(c.get("utility")) or 0.0) for c in cands if int(c.get("Y_FILL5") or 0) == 1]
    return {
        "n": len(cands),
        "P_FILL5_MEDIAN": _median(pfill),
        "AUG_SCORE_MEDIAN": _median(aug),
        "FILL_RATE": _rate(fill_n, len(cands)) if cands else None,
        "UTILITY_MEDIAN": _median(utils),
        "COND_PNL": float(np.mean(fill_utils)) if fill_utils else None,
    }


def rank_change_table(old_cands: list[dict[str, Any]], new_cands: list[dict[str, Any]]) -> dict[str, Any]:
    old_by = {cohort_key(c): c for c in old_cands}
    new_by = {cohort_key(c): c for c in new_cands}
    keys = sorted(set(old_by) | set(new_by), key=lambda k: (k[0], k[1]))
    rows = []
    changed = 0
    old_only: list[dict[str, Any]] = []
    new_only: list[dict[str, Any]] = []
    for k in keys:
        oc = old_by.get(k)
        nc = new_by.get(k)
        old_rk = oc.get("row_key") if oc else None
        new_rk = nc.get("row_key") if nc else None
        is_changed = old_rk != new_rk
        if is_changed:
            changed += 1
            if oc is not None:
                old_only.append(oc)
            if nc is not None:
                new_only.append(nc)
        rows.append(
            {
                "date": k[0],
                "t0": k[1],
                "RANK_CHANGED": bool(is_changed),
                "OLD_SYMBOL": oc.get("symbol") if oc else None,
                "NEW_SYMBOL": nc.get("symbol") if nc else None,
                "OLD_ROW_KEY": old_rk,
                "NEW_ROW_KEY": new_rk,
                "OLD_P_FILL5": oc.get("P_FILL5") if oc else None,
                "NEW_P_FILL5": nc.get("P_FILL5") if nc else None,
                "OLD_AUG_SCORE": oc.get("AUG_SCORE") if oc else None,
                "NEW_AUG_SCORE": nc.get("AUG_SCORE") if oc else None,
            }
        )
    n = len(keys)
    return {
        "RANK_CHANGED_COHORT_N": changed,
        "RANK_CHANGED_RATE": (float(changed) / float(n)) if n else None,
        "COHORT_N": n,
        "rows": rows,
        "OLD_ONLY": old_only,
        "NEW_ONLY": new_only,
        "OLD_ONLY_STATS": set_stats(old_only),
        "NEW_ONLY_STATS": set_stats(new_only),
    }


def decide_case(
    *,
    integrity_ok: bool,
    preservation_ok: bool,
    gate_pass: bool,
    coverage_up: bool,
    augment_net: float,
    overlay_net: float,
    current_net: float,
) -> dict[str, Any]:
    if not integrity_ok or not preservation_ok:
        return {
            "CASE": "E",
            "VERDICT": "AM_UTILITY_FILLABILITY_AUGMENT_INTEGRITY_FAILED",
            "NEXT": "STOP",
            "PRIMARY_MECHANISM": "INTEGRITY_OR_CURRENT_PRESERVATION_FAILURE",
        }
    if gate_pass:
        return {
            "CASE": "A",
            "VERDICT": "AM_CURRENT_PRESERVING_UTILITY_FILLABILITY_AUGMENT_SUPPORTED",
            "NEXT": "AM_CURRENT_PRESERVING_UTILITY_FILLABILITY_AUGMENT_FREEZE_REVIEW",
            "PRIMARY_MECHANISM": "P_FILL_ORDERING_PASSES_FULL_ECONOMIC_GATE",
        }
    if coverage_up and float(augment_net) > 0 and float(overlay_net) > float(current_net):
        return {
            "CASE": "B",
            "VERDICT": "AM_UTILITY_FILLABILITY_AUGMENT_PARTIAL_ECONOMIC_EDGE",
            "NEXT": "AM_AUGMENT_PORTFOLIO_RISK_ARCHITECTURE_REASSESSMENT",
            "PRIMARY_MECHANISM": "P_FILL_IMPROVES_COVERAGE_AND_NET_WITHOUT_ROBUSTNESS_GATES",
        }
    if coverage_up:
        return {
            "CASE": "C",
            "VERDICT": "AM_FILLABILITY_INTEGRATION_DESTROYS_AUGMENT_QUALITY",
            "NEXT": "AM_ENTRY_INFORMATION_EXPANSION_REASSESSMENT",
            "PRIMARY_MECHANISM": "P_FILL_IMPROVES_COVERAGE_BUT_SLEEVE_OR_OVERLAY_NOT_PROFITABLE",
        }
    return {
        "CASE": "D",
        "VERDICT": "AM_UTILITY_AUGMENT_PASSIVE_ACCESS_REMAINS_INSUFFICIENT",
        "NEXT": "AM_ENTRY_INFORMATION_EXPANSION_REASSESSMENT",
        "PRIMARY_MECHANISM": "P_FILL_ORDERING_DOES_NOT_IMPROVE_PASSIVE_ACCESS",
    }
