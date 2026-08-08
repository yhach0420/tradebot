"""Verdict + PASSIVE_FILL_EXIT_V1 freeze."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import (
    ENTRY_SHA,
    LODO_MIN_POS_DAYS,
    PRIORITY,
    VERDICT_DYNAMIC,
    VERDICT_FIXED,
    VERDICT_NONE,
)


def _gates_from(sm: dict[str, Any], *, lodo_majority: bool) -> dict[str, bool]:
    return {
        "ret_gt0": (sm.get("mean_ret_bps") or 0) > 0,
        "pf_gt1": sm.get("pf") is not None and sm["pf"] > 1.0,
        "positive_days_ge9": (sm.get("positive_days") or 0) >= LODO_MIN_POS_DAYS,
        "ss_balanced_gt0": (sm.get("ss_balanced") or 0) > 0,
        "lodo_majority": bool(lodo_majority),
        "n_days_14": (sm.get("n_days") or 0) >= 14,
        "no_severe_symbol_conc": not bool(sm.get("severe_symbol_concentration")),
    }


def decide_verdict(
    *,
    cross: dict[str, Any],
    fixed_controls: dict[str, Any],
    selected_per_fold: dict[str, Any],
    lodo: dict[str, Any],
) -> dict[str, Any]:
    f600_sm = fixed_controls.get("E0_FIXED_600") or {}
    # LODO majority for fixed600 ≈ day positivity majority (same day means)
    f600_lodo_maj = (f600_sm.get("positive_days") or 0) > (f600_sm.get("n_days") or 0) / 2.0
    dyn_gates = _gates_from(cross, lodo_majority=bool(lodo.get("majority_positive")))
    fixed_gates = _gates_from(f600_sm, lodo_majority=f600_lodo_maj)
    dyn_failed = [k for k, v in dyn_gates.items() if not v]
    fixed_failed = [k for k, v in fixed_gates.items() if not v]

    any_dynamic = any(
        v is not None and not str((v or {}).get("family") or "").startswith("E0")
        for v in selected_per_fold.values()
        if v is not None
    )
    all_fixed = all(
        v is None or str(v.get("family") or "").startswith("E0")
        for v in selected_per_fold.values()
    )

    f600 = f600_sm.get("mean_ret_bps")
    dyn = cross.get("mean_ret_bps")

    # Dynamic PASS only if cross-fitted gates clear AND dynamic meaningfully preferred
    if not dyn_failed and any_dynamic and not all_fixed:
        if f600 is None or dyn is None or dyn > f600 + 0.5 or (
            (cross.get("hold_sec") or {}).get("median") or 9999
        ) < 0.7 * 600:
            # prefer dynamic if better ret OR substantially shorter hold with gates already passed
            return {
                "verdict": VERDICT_DYNAMIC,
                "freeze": True,
                "gates": dyn_gates,
                "fixed_gates": fixed_gates,
                "reason": "dynamic EXIT supported on cross-fitted gates",
                "freeze_as": "SELECTED_PER_OUTER_FOLD",
            }

    # Fixed horizon remains baseline when FIXED600 clears gates (even if dynamic fails days)
    if not fixed_failed:
        return {
            "verdict": VERDICT_FIXED,
            "freeze": False,  # PASSIVE_FILL_EXIT_V1 only on DYNAMIC pass
            "gates": dyn_gates,
            "fixed_gates": fixed_gates,
            "reason": (
                "fixed horizon remains best robust baseline"
                + (f"; dynamic failed: {dyn_failed}" if dyn_failed else "")
            ),
            "freeze_as": "E0_FIXED_600",
        }

    return {
        "verdict": VERDICT_NONE,
        "freeze": False,
        "gates": dyn_gates,
        "fixed_gates": fixed_gates,
        "reason": f"no robust EXIT: dynamic failed {dyn_failed}; fixed600 failed {fixed_failed}",
    }


def freeze_exit_manifest(
    *,
    decision: dict,
    selected_per_fold: dict,
    cross: dict,
) -> dict[str, Any]:
    freeze_as = decision.get("freeze_as")
    if freeze_as == "E0_FIXED_600":
        family = "E0_FIXED"
        thresholds = {"fixed_hold_sec": 600.0}
        spec_id = "E0_FIXED_600"
    else:
        family = "OUTER_FOLD_SELECTED"
        thresholds = {"selected_per_fold": selected_per_fold}
        spec_id = "CROSS_FITTED_SELECTED"

    body = {
        "manifest_id": "PASSIVE_FILL_EXIT_V1",
        "entry_sha": ENTRY_SHA,
        "exit_family": family,
        "spec_id": spec_id,
        "thresholds": thresholds,
        "condition_priority": list(PRIORITY),
        "executable_price_rule": "Buy1.Price qty>=100 freshness<=5s not special same session",
        "session_rule": "no session cross; SESSION_CLOSE force",
        "entry_origin": "fill_time / fill_price",
        "cross_fitted_mean_ret_bps": cross.get("mean_ret_bps"),
        "cross_fitted_hold_median_sec": (cross.get("hold_sec") or {}).get("median"),
        "runtime_reflect": False,
        "research_paper_only": True,
        "not_deployable_performance_claim": True,
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["sha256"] = hashlib.sha256(raw).hexdigest()
    return body
