"""Recompute FIXED horizons under canonical contract; robustness gates."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.e1_x35_passive_exit.metrics import dist_stats, evaluate_spec

from . import HORIZONS, LODO_MIN_POS_DAYS, MAX_SYMBOL_CONTRIB
from .contracts import canonical_fixed_exit


def recompute_fixed(eps: list[dict]) -> dict[str, Any]:
    out = {}
    for H in HORIZONS:
        spec = {"id": f"E0_FIXED_{H}", "family": "E0_FIXED", "fixed_hold_sec": float(H)}
        sm = evaluate_spec(eps, spec)
        # exit reason counts already in sm
        # also confirm canonical function matches
        canon_rets = []
        for e in eps:
            r = canonical_fixed_exit(e["path"], float(H))
            if r.get("ok"):
                canon_rets.append(float(r["exit_ret_bps"]))
        sm["canonical_mean_ret_bps"] = float(np.mean(canon_rets)) if canon_rets else None
        sm["canonical_matches_evaluate"] = (
            sm.get("mean_ret_bps") is not None
            and sm["canonical_mean_ret_bps"] is not None
            and abs(sm["mean_ret_bps"] - sm["canonical_mean_ret_bps"]) < 1e-9
        )
        out[f"FIXED{H}"] = {k: v for k, v in sm.items() if k != "day_means"}
        out[f"FIXED{H}"]["day_means"] = sm.get("day_means")
    return out


def passes_robustness(sm: dict[str, Any]) -> dict[str, Any]:
    gates = {
        "ret_gt0": (sm.get("mean_ret_bps") or 0) > 0,
        "pf_gt1": sm.get("pf") is not None and sm["pf"] > 1.0,
        "positive_days_ge9": (sm.get("positive_days") or 0) >= LODO_MIN_POS_DAYS,
        "ss_balanced_gt0": (sm.get("ss_balanced") or 0) > 0,
        "lodo_majority": (sm.get("positive_days") or 0) > (sm.get("n_days") or 0) / 2.0,
        "n_days_14": (sm.get("n_days") or 0) >= 14,
        "no_severe_symbol_conc": not bool(sm.get("severe_symbol_concentration")),
    }
    return {
        "pass": all(gates.values()),
        "gates": gates,
        "failed": [k for k, v in gates.items() if not v],
    }


def best_robust_fixed(recomputed: dict[str, Any]) -> dict[str, Any]:
    """Among FIXED180/300/600/900 that pass robustness, pick best by mean_ret then PF."""
    candidates = []
    for H in HORIZONS:
        sm = recomputed[f"FIXED{H}"]
        rob = passes_robustness(sm)
        if rob["pass"]:
            candidates.append({
                "H": H,
                "mean_ret_bps": sm["mean_ret_bps"],
                "pf": sm["pf"],
                "positive_days": sm["positive_days"],
                "hold_median": (sm.get("hold_sec") or {}).get("median"),
            })
    candidates.sort(key=lambda x: (-float(x["mean_ret_bps"]), -(x["pf"] or 0)))
    return {
        "n_passing": len(candidates),
        "ranked": candidates,
        "best_H": candidates[0]["H"] if candidates else None,
    }
