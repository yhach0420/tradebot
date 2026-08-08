"""Design-fixed thresholds applied to Validation and Holdout."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x14_board_independent_signal import FEATURE_HYPOTHESIS
from research.e1_x14_board_independent_signal.evaluate import (
    PRICE_FEATURES,
    RS_FEATURES,
    VOLUME_FEATURES,
    _hyp_sign,
)

from . import (
    DESIGN,
    HOLDOUT,
    HOLDOUT_SUPPORT_MIN,
    KNOWN_MAINTAINED,
    KNOWN_REVERSALS,
    LABEL,
    TOUCH,
    VALIDATION,
)

ALL_FEATURES = list(dict.fromkeys(PRICE_FEATURES + VOLUME_FEATURES + RS_FEATURES))


def _effect(
    rows: list[dict[str, Any]],
    name: str,
    q20: float,
    q80: float,
    sign: float,
) -> dict[str, Any]:
    xs = [float(r[name]) for r in rows]
    ys = [float(r[LABEL]) for r in rows]
    low = [ys[i] for i, x in enumerate(xs) if x <= q20]
    high = [ys[i] for i, x in enumerate(xs) if x >= q80]
    raw = (float(np.mean(high)) - float(np.mean(low))) if low and high else None
    directed = (raw * sign) if (raw is not None and sign != 0) else raw
    touch_low = [float(r[TOUCH]) for r in rows if r.get(TOUCH) is not None and float(r[name]) <= q20]
    touch_high = [float(r[TOUCH]) for r in rows if r.get(TOUCH) is not None and float(r[name]) >= q80]
    ft_low = float(np.mean(touch_low)) if touch_low else None
    ft_high = float(np.mean(touch_high)) if touch_high else None
    # first-touch directed: for positive hyp, high bucket should have higher touch rate
    ft_raw = (ft_high - ft_low) if (ft_high is not None and ft_low is not None) else None
    ft_dir = (ft_raw * sign) if (ft_raw is not None and sign != 0) else ft_raw
    return {
        "support": len(rows),
        "n_low": len(low),
        "n_high": len(high),
        "raw_gap": raw,
        "directed_effect": directed,
        "q20_forward_return": float(np.mean(low)) if low else None,
        "q80_forward_return": float(np.mean(high)) if high else None,
        "q20_first_touch": ft_low,
        "q80_first_touch": ft_high,
        "first_touch_directed": ft_dir,
    }


def reconcile_feature(
    name: str,
    clusters: list[dict[str, Any]],
    *,
    source_stable: bool,
) -> dict[str, Any]:
    sign = _hyp_sign(name)
    hyp = FEATURE_HYPOTHESIS.get(name)

    def subset(days: tuple[str, ...]) -> list[dict[str, Any]]:
        return [
            c for c in clusters
            if c.get("date") in days and c.get(name) is not None and c.get(LABEL) is not None
        ]

    design = subset(DESIGN)
    valid = subset(VALIDATION)
    hold = subset(HOLDOUT)

    # Thresholds ONLY from DESIGN
    if len(design) < 20:
        return {
            "feature": name,
            "hypothesis": hyp,
            "hypothesis_sign": sign,
            "candidate_status": "PRE_HOLDOUT_UNSTABLE_REJECT",
            "stages": [],
            "note": "insufficient design support for thresholds",
            "source_stable_candidate": source_stable,
        }
    dx = [float(r[name]) for r in design]
    q20 = float(np.quantile(dx, 0.20))
    q80 = float(np.quantile(dx, 0.80))

    provenance = {
        "feature": name,
        "hypothesis_sign": sign,
        "hypothesis": hyp,
        "q20_threshold": q20,
        "q80_threshold": q80,
        "threshold_construction_dates": list(DESIGN),
        "threshold_application_dates": {
            "DESIGN": list(DESIGN),
            "VALIDATION": list(VALIDATION),
            "HISTORICAL_HOLDOUT": list(HOLDOUT),
        },
        "holdout_thresholds_recomputed": False,
        "source_used_design_plus_validation_for_thresholds": True,
        "source_note": (
            "E1_X14 source evaluate_feature computed q20/q80 on the evaluation set itself; "
            "pre-holdout stable_candidate used DESIGN+VALIDATION combined; "
            "source holdout_directed_gap also re-derived holdout quantiles (non-compliant). "
            "This reconciliation fixes: thresholds from DESIGN only."
        ),
    }

    d_eff = _effect(design, name, q20, q80, sign)
    v_eff = _effect(valid, name, q20, q80, sign) if len(valid) >= 20 else None
    h_eff = _effect(hold, name, q20, q80, sign) if len(hold) >= 20 else None

    stages = []
    # DESIGN_CANDIDATE
    design_ok = d_eff["directed_effect"] is not None and d_eff["directed_effect"] > 0 and d_eff["support"] >= 100
    if design_ok:
        stages.append("DESIGN_CANDIDATE")

    # VALIDATION_SUPPORTED
    val_ok = (
        design_ok and v_eff is not None and v_eff["directed_effect"] is not None
        and v_eff["directed_effect"] > 0 and v_eff["support"] >= 50
    )
    if val_ok:
        stages.append("VALIDATION_SUPPORTED")

    # Holdout sign
    holdout_status = None
    if h_eff is not None and h_eff["directed_effect"] is not None and h_eff["support"] >= HOLDOUT_SUPPORT_MIN:
        if h_eff["directed_effect"] > 0:
            holdout_status = "HOLDOUT_SIGN_MAINTAINED"
            stages.append("HOLDOUT_SIGN_MAINTAINED")
        else:
            holdout_status = "HOLDOUT_SIGN_REVERSED"
            stages.append("HOLDOUT_SIGN_REVERSED")

    # first-touch major contradiction: directed first-touch strongly opposite when holdout maintained/reversed check
    ft_ok = True
    if h_eff and h_eff.get("first_touch_directed") is not None and holdout_status == "HOLDOUT_SIGN_MAINTAINED":
        # major contradiction if FT directed < -0.05 absolute while effect positive
        if h_eff["first_touch_directed"] < -0.05:
            ft_ok = False

    pre_holdout_ok = design_ok and val_ok  # both required for gate
    # Also allow design-only if validation thin but source marked stable? Spec: pre-holdout directed > 0
    # Use design+validation combined directed with FIXED design thresholds as pre-holdout
    pre_rows = design + valid
    pre_eff = _effect(pre_rows, name, q20, q80, sign) if len(pre_rows) >= 20 else d_eff
    pre_pos = pre_eff["directed_effect"] is not None and pre_eff["directed_effect"] > 0

    if not source_stable and not pre_pos:
        candidate_status = "PRE_HOLDOUT_UNSTABLE_REJECT"
    elif not pre_pos:
        candidate_status = "PRE_HOLDOUT_UNSTABLE_REJECT"
    elif holdout_status == "HOLDOUT_SIGN_REVERSED":
        candidate_status = "HOLDOUT_REVERSED_REJECT"
    elif (
        holdout_status == "HOLDOUT_SIGN_MAINTAINED"
        and h_eff is not None
        and h_eff["directed_effect"] is not None
        and h_eff["directed_effect"] > 0
        and h_eff["support"] >= HOLDOUT_SUPPORT_MIN
        and ft_ok
    ):
        candidate_status = "HOLDOUT_MAINTAINED_CANDIDATE"
    else:
        # pre-holdout ok but holdout insufficient or FT fail
        if holdout_status is None:
            candidate_status = "PRE_HOLDOUT_UNSTABLE_REJECT" if not source_stable else "HOLDOUT_REVERSED_REJECT"
            # insufficient holdout support → reject for holdout gate
            if h_eff is None or (h_eff.get("support") or 0) < HOLDOUT_SUPPORT_MIN:
                candidate_status = "HOLDOUT_REVERSED_REJECT" if pre_pos else "PRE_HOLDOUT_UNSTABLE_REJECT"
                # actually insufficient ≠ reversed; use reject without maintained
                candidate_status = "PRE_HOLDOUT_UNSTABLE_REJECT" if not pre_pos else "HOLDOUT_REVERSED_REJECT"
        else:
            candidate_status = "HOLDOUT_REVERSED_REJECT"

    if not ft_ok and candidate_status == "HOLDOUT_MAINTAINED_CANDIDATE":
        candidate_status = "HOLDOUT_REVERSED_REJECT"

    return {
        "feature": name,
        "hypothesis": hyp,
        "hypothesis_sign": sign,
        "source_stable_candidate": source_stable,
        "threshold_provenance": provenance,
        "design_effect": d_eff,
        "validation_effect": v_eff,
        "pre_holdout_effect": pre_eff,
        "holdout_effect": h_eff,
        "stages": stages,
        "holdout_status": holdout_status,
        "candidate_status": candidate_status,
        "first_touch_ok": ft_ok,
        "stable_not_equal_holdout_pass": True,
    }


def reconcile_all(
    clusters: list[dict[str, Any]],
    source_stable: set[str],
) -> list[dict[str, Any]]:
    return [reconcile_feature(n, clusters, source_stable=(n in source_stable)) for n in ALL_FEATURES]
