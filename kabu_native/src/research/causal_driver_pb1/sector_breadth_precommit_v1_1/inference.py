"""Frozen date-block bootstrap CI, sign-tail p-value, and BH q-values. No outcomes."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.causal_driver_pb1.identity.ids import sha256_bytes, sha256_obj
from research.causal_driver_pb1.sector_breadth_precommit import BOOTSTRAP_N, BOOTSTRAP_SEED, FAMILY_N, FDR_FAMILY_N, FDR_Q
from research.causal_driver_pb1.sector_breadth_precommit_v1_1 import (
    BH_METHOD,
    CI_METHOD,
    P_VALUE_METHOD,
    QUANTILE_INTERPOLATION,
    RNG_ALGORITHM,
    RNG_LIBRARY,
)


def rng_version() -> str:
    return str(np.__version__)


def draw_date_block_indices(
    *,
    n_day: int,
    n_boot: int = BOOTSTRAP_N,
    seed: int = BOOTSTRAP_SEED,
) -> np.ndarray:
    """Sample n_day dates with replacement, independently per replicate. MT19937 RandomState."""
    if int(n_day) < 1:
        raise ValueError("n_day_ge_1")
    rng = np.random.RandomState(int(seed))
    out = np.empty((int(n_boot), int(n_day)), dtype=np.int64)
    for i in range(int(n_boot)):
        out[i] = rng.randint(0, int(n_day), size=int(n_day))
    return out


def date_block_weights(index_row: np.ndarray, n_day: int) -> np.ndarray:
    """Times a date is sampled. Weighting the date's full intraday row-set is equivalent to repeating the block."""
    return np.bincount(np.asarray(index_row, dtype=np.int64), minlength=int(n_day)).astype(np.float64)


def percentile_ci_95(beta_boot: np.ndarray) -> tuple[float, float]:
    arr = np.asarray(beta_boot, dtype=np.float64)
    if arr.size != int(BOOTSTRAP_N) or not np.all(np.isfinite(arr)):
        raise ValueError("ci_requires_exactly_2000_finite_beta_boot")
    try:
        lo, hi = np.percentile(arr, [2.5, 97.5], method=QUANTILE_INTERPOLATION)
    except TypeError:
        lo, hi = np.percentile(arr, [2.5, 97.5], interpolation=QUANTILE_INTERPOLATION)
    return float(lo), float(hi)


def ci_excludes_zero(lower: float, upper: float) -> bool:
    if lower == 0.0 or upper == 0.0:
        return False
    return (lower > 0.0) or (upper < 0.0)


def bootstrap_two_sided_sign_tail_p(beta_boot: np.ndarray) -> float:
    arr = np.asarray(beta_boot, dtype=np.float64)
    b = int(arr.size)
    if b != int(BOOTSTRAP_N) or not np.all(np.isfinite(arr)):
        raise ValueError("p_requires_exactly_2000_finite_beta_boot")
    n_nonpositive = int(np.sum(arr <= 0.0))
    n_nonnegative = int(np.sum(arr >= 0.0))
    p_lower = (1.0 + n_nonpositive) / (b + 1.0)
    p_upper = (1.0 + n_nonnegative) / (b + 1.0)
    return float(min(1.0, 2.0 * min(p_lower, p_upper)))


def require_family_p(p: np.ndarray) -> np.ndarray:
    arr = np.asarray(p, dtype=np.float64)
    if int(arr.size) != int(FAMILY_N):
        raise ValueError("BH_FAMILY_N_NOT_384")
    if not np.all(np.isfinite(arr)):
        raise ValueError("BH_P_NONFINITE")
    return arr


def bh_qvalues_canonical(p: np.ndarray) -> np.ndarray:
    """Monotone BH q-values. p is already in canonical test order (metric, scope, lookback, horizon)."""
    arr = require_family_p(p)
    m = int(arr.size)
    if m != int(FDR_FAMILY_N):
        raise ValueError("BH_FAMILY_N_NOT_384")
    order = np.lexsort((np.arange(m, dtype=np.int64), arr))
    q = np.empty(m, dtype=np.float64)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = int(order[rank - 1])
        val = float(arr[i]) * m / rank
        prev = min(prev, val)
        q[i] = prev
    return np.clip(q, 0.0, 1.0)


def d3_pass(q_value: float) -> bool:
    return float(q_value) <= float(FDR_Q)


def freeze_bootstrap_indices(*, development_dates: list[str], c1_dates: list[str]) -> dict[str, Any]:
    periods = (
        ("DEVELOPMENT", list(development_dates)),
        ("C1", list(c1_dates)),
    )
    packed = []
    for name, dates in periods:
        n_day = len(dates)
        idx = draw_date_block_indices(n_day=n_day)
        index_bytes = np.ascontiguousarray(idx, dtype="<i8").tobytes()
        packed.append(
            {
                "period": name,
                "n_day": n_day,
                "dates": dates,
                "index_sha256": sha256_bytes(index_bytes),
            }
        )
    payload = {
        "bootstrap_method": "DATE_BLOCK_BOOTSTRAP",
        "sampling_unit": "eligible trading date",
        "with_replacement": True,
        "duplicate_date_repeats_entire_intraday_block": True,
        "no_independent_minute_resample": True,
        "refit_MODEL1_each_replicate": True,
        "no_row_iid_se_shortcut": True,
        "rng_algorithm": RNG_ALGORITHM,
        "rng_library": RNG_LIBRARY,
        "bootstrap_n": int(BOOTSTRAP_N),
        "bootstrap_seed": int(BOOTSTRAP_SEED),
        "draw": "RandomState.randint(low=0, high=n_day, size=n_day) once per replicate 0..n_boot-1",
        "index_dtype": "int64_little_endian",
        "periods": packed,
    }
    sha = sha256_obj(payload)
    return {
        "bootstrap_method": payload["bootstrap_method"],
        "sampling_unit": payload["sampling_unit"],
        "with_replacement": True,
        "duplicate_date_repeats_entire_intraday_block": True,
        "no_independent_minute_resample": True,
        "refit_MODEL1_each_replicate": True,
        "no_row_iid_se_shortcut": True,
        "rng_algorithm": RNG_ALGORITHM,
        "rng_library": RNG_LIBRARY,
        "rng_version": rng_version(),
        "bootstrap_n": int(BOOTSTRAP_N),
        "bootstrap_seed": int(BOOTSTRAP_SEED),
        "draw": payload["draw"],
        "index_dtype": "int64_little_endian",
        "bootstrap_index_sha256": sha,
        "periods": [
            {
                "period": p["period"],
                "n_day": p["n_day"],
                "date_n": len(p["dates"]),
                "first_date": p["dates"][0] if p["dates"] else None,
                "last_date": p["dates"][-1] if p["dates"] else None,
                "index_sha256": p["index_sha256"],
            }
            for p in packed
        ],
    }


def inference_spec(*, bootstrap_index_sha256: str, development_n: int, c1_n: int) -> dict[str, Any]:
    return {
        "bootstrap_method": "DATE_BLOCK_BOOTSTRAP",
        "bootstrap_n": int(BOOTSTRAP_N),
        "bootstrap_seed": int(BOOTSTRAP_SEED),
        "sampling_unit": "eligible trading date",
        "n_day_development": int(development_n),
        "n_day_c1": int(c1_n),
        "with_replacement": True,
        "duplicate_date_repeats_entire_intraday_block": True,
        "no_independent_minute_resample": True,
        "refit_each_replicate": "exact same frozen MODEL1 + controls + minute-of-day FE + scope/metric/lookback/horizon/freshness",
        "extract": "beta_driver_boot[b]",
        "no_row_iid_se_shortcut": True,
        "rng_algorithm": RNG_ALGORITHM,
        "rng_library": RNG_LIBRARY,
        "rng_version": rng_version(),
        "bootstrap_index_sha256": bootstrap_index_sha256,
        "ci_method": CI_METHOD,
        "ci_percentiles": [2.5, 97.5],
        "ci_quantile_interpolation": QUANTILE_INTERPOLATION,
        "ci_quantile_library": "numpy.percentile",
        "ci_not": ("basic bootstrap", "BCa", "normal approximation", "OLS iid CI"),
        "ci_boundary_policy": "PASS iff lower > 0 OR upper < 0; FAIL if lower == 0 OR upper == 0 OR interval contains 0",
        "p_value_method": P_VALUE_METHOD,
        "p_value_formula": "min(1, 2 * min((1+n_nonpositive)/(B+1), (1+n_nonnegative)/(B+1))) with B=2000",
        "p_value_not": ("OLS p-value", "iid t-test", "cluster-robust asymptotic p-value", "normal-approximation p-value"),
        "bh_method": BH_METHOD,
        "bh_m": int(FDR_FAMILY_N),
        "bh_q": float(FDR_Q),
        "bh_no_subgroup": True,
        "bh_canonical_order": ("metric", "scope_id", "lookback ascending", "horizon ascending"),
        "bh_q_formula": "q_(i) = min_{j>=i} (m/j * p_(j)) capped at 1; D3 PASS iff q_value <= 0.05 including equality",
        "d2_and_d3_both_required": True,
        "d7_ci_same_method": True,
        "d7_no_bh_retest": True,
        "c1_no_bh_rerun": True,
        "c2_ci_same_method": True,
        "c7_ci_same_method": True,
        "family_size_remains_384": True,
    }


def run_validation_tests(*, development_dates: list[str], c1_dates: list[str], bootstrap_index_sha256: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []

    a = freeze_bootstrap_indices(development_dates=development_dates, c1_dates=c1_dates)
    b = freeze_bootstrap_indices(development_dates=development_dates, c1_dates=c1_dates)
    t1 = a["bootstrap_index_sha256"] == b["bootstrap_index_sha256"] == bootstrap_index_sha256
    rows.append({"id": "T1", "name": "determinism", "pass": t1, "sha": a["bootstrap_index_sha256"]})

    pos = np.full(int(BOOTSTRAP_N), 1.0, dtype=np.float64)
    lo, hi = percentile_ci_95(pos)
    t2 = ci_excludes_zero(lo, hi) and lo > 0.0
    rows.append({"id": "T2", "name": "always_positive_ci", "pass": t2, "lower": lo, "upper": hi})

    mix = np.array([-1.0] * 1000 + [1.0] * 1000, dtype=np.float64)
    lo3, hi3 = percentile_ci_95(mix)
    t3 = (not ci_excludes_zero(lo3, hi3)) and lo3 < 0.0 and hi3 > 0.0
    rows.append({"id": "T3", "name": "zero_crossing_ci", "pass": t3, "lower": lo3, "upper": hi3})

    p4 = bootstrap_two_sided_sign_tail_p(mix)
    t4 = p4 >= 0.5
    rows.append({"id": "T4", "name": "symmetric_p_large", "pass": t4, "p": p4})

    strong = np.array([1.0] * 1999 + [-1.0], dtype=np.float64)
    p5 = bootstrap_two_sided_sign_tail_p(strong)
    t5 = p5 <= 0.01
    rows.append({"id": "T5", "name": "strong_positive_p_small", "pass": t5, "p": p5})

    p_small = np.array([0.01, 0.04, 0.05], dtype=np.float64)
    # algorithm check on a 384-vector: first three as known, remainder = 1
    p_vec = np.ones(int(FAMILY_N), dtype=np.float64)
    p_vec[:3] = p_small
    q_vec = bh_qvalues_canonical(p_vec)
    # hand BH on the three small p among 384: q depends on m=384
    # Isolated 3-vector check:
    def _bh_any(p: np.ndarray) -> np.ndarray:
        m = int(p.size)
        order = np.lexsort((np.arange(m, dtype=np.int64), p))
        q = np.empty(m, dtype=np.float64)
        prev = 1.0
        for rank in range(m, 0, -1):
            i = int(order[rank - 1])
            val = float(p[i]) * m / rank
            prev = min(prev, val)
            q[i] = prev
        return np.clip(q, 0.0, 1.0)

    q3 = _bh_any(p_small)
    expected3 = np.array([0.03, 0.05, 0.05], dtype=np.float64)
    t6 = bool(np.allclose(q3, expected3, atol=1e-12)) and q_vec.size == int(FAMILY_N) and d3_pass(0.05) and (not d3_pass(0.0500000001))
    rows.append({"id": "T6", "name": "bh_known_vector", "pass": t6, "q3": [float(x) for x in q3]})

    t7 = True
    detail7 = "family_n=384"
    try:
        require_family_p(np.ones(383))
        t7 = False
        detail7 = "383_did_not_fail"
    except ValueError:
        pass
    try:
        require_family_p(np.ones(385))
        t7 = False
        detail7 = "385_did_not_fail"
    except ValueError:
        pass
    try:
        require_family_p(np.ones(384))
    except ValueError:
        t7 = False
        detail7 = "384_failed"
    rows.append({"id": "T7", "name": "family_n_384_fail_closed", "pass": t7, "detail": detail7})

    # boundary equality on 0
    lo0, hi0 = 0.0, 1.0
    t_bound = (not ci_excludes_zero(lo0, hi0)) and (not ci_excludes_zero(-1.0, 0.0)) and ci_excludes_zero(0.1, 1.0)
    rows.append({"id": "T_CI_EQ0", "name": "ci_zero_boundary_fail", "pass": t_bound})

    ok = all(bool(r.get("pass")) for r in rows)
    return {"pass": ok, "rows": rows, "n_pass": sum(1 for r in rows if r.get("pass")), "n": len(rows)}
