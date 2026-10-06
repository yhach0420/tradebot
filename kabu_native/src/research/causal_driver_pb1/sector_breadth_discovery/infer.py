"""Frozen-index date-block bootstrap using V1.1 CI and sign-tail p. No new RNG draws."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.causal_driver_pb1.phase2_discovery.infer import _w_ols_fe
from research.causal_driver_pb1.sector_breadth_discovery import BOOTSTRAP_N
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.inference import (
    bootstrap_two_sided_sign_tail_p,
    ci_excludes_zero,
    percentile_ci_95,
)


def fit_frozen(
    y: np.ndarray,
    fx: np.ndarray,
    controls: list[np.ndarray],
    date_ids: np.ndarray,
    minute_ids: np.ndarray,
    n_dates: int,
    boot_index: np.ndarray,
) -> dict[str, Any]:
    cols = [fx] + list(controls)
    x = np.column_stack(cols)
    mask = np.isfinite(y) & np.isfinite(x).all(axis=1)
    n = int(mask.sum())
    if n < 40 or boot_index.shape != (int(BOOTSTRAP_N), int(n_dates)):
        return {"ok": False, "n": n, "reason": "mask_or_index"}
    yy = y[mask]
    xx = x[mask]
    dd = date_ids[mask]
    mm = minute_ids[mask]
    n_min = int(mm.max()) + 1 if mm.size else 1
    ones = np.ones(yy.shape[0], dtype=np.float64)
    b0 = _w_ols_fe(yy, xx, mm, ones, n_min)
    if b0 is None:
        return {"ok": False, "n": n, "reason": "point_fail"}
    boots = np.full(int(BOOTSTRAP_N), np.nan, dtype=np.float64)
    for i in range(int(BOOTSTRAP_N)):
        w = np.bincount(boot_index[i], minlength=int(n_dates)).astype(np.float64)
        ww = w[dd]
        bb = _w_ols_fe(yy, xx, mm, ww, n_min)
        if bb is not None:
            boots[i] = float(bb[0])
    if not np.all(np.isfinite(boots)):
        return {
            "ok": False,
            "n": n,
            "beta": b0,
            "b_fx": float(b0[0]),
            "boot_n": int(np.isfinite(boots).sum()),
            "reason": "nonfinite_replicate",
        }
    lo, hi = percentile_ci_95(boots)
    p = bootstrap_two_sided_sign_tail_p(boots)
    return {
        "ok": True,
        "n": n,
        "beta": b0,
        "b_fx": float(b0[0]),
        "ci_lo": lo,
        "ci_hi": hi,
        "p_boot": p,
        "p_value_method": "BOOTSTRAP_TWO_SIDED_SIGN_TAIL_PLUS_ONE",
        "ci_method": "BOOTSTRAP_PERCENTILE_CI",
        "boot_n": int(BOOTSTRAP_N),
        "ci_excludes_0": bool(ci_excludes_zero(lo, hi)),
    }
