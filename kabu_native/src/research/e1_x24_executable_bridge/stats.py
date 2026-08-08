"""Bootstrap CI + Benjamini-Hochberg FDR (descriptive tags only)."""
from __future__ import annotations

from typing import Any

import numpy as np

from . import BOOTSTRAP_ITERS, BOOTSTRAP_SEED


def _bh_qvalues(pvals: list[float]) -> list[float]:
    n = len(pvals)
    if n == 0:
        return []
    order = np.argsort(pvals)
    ranked = np.array(pvals, dtype=float)[order]
    q = np.empty(n, dtype=float)
    prev = 1.0
    for i in range(n - 1, -1, -1):
        rank = i + 1
        val = ranked[i] * n / rank
        prev = min(prev, val)
        q[i] = min(prev, 1.0)
    out = np.empty(n, dtype=float)
    out[order] = q
    return out.tolist()


def bootstrap_pair(
    trade_rets: np.ndarray,
    baseline_rets: np.ndarray,
    *,
    iters: int = BOOTSTRAP_ITERS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    n = int(trade_rets.size)
    if n == 0:
        return {
            "avg_return_bps_ci95": [None, None],
            "delta_vs_baseline_ci95": [None, None],
            "raw_p_value": None,
            "obs_avg": None,
            "obs_delta": None,
            "tag": "DESCRIPTIVE_ONLY",
        }
    base_mean = float(np.mean(baseline_rets)) if baseline_rets.size else 0.0
    obs = float(np.mean(trade_rets))
    obs_delta = obs - base_mean

    boots = np.empty(iters)
    deltas = np.empty(iters)
    for i in range(iters):
        sample = trade_rets[rng.integers(0, n, size=n)]
        boots[i] = float(np.mean(sample))
        deltas[i] = boots[i] - base_mean

    lo, hi = np.percentile(boots, [2.5, 97.5])
    dlo, dhi = np.percentile(deltas, [2.5, 97.5])
    # one-sided p: fraction of bootstrap means <= 0 (for positive edge claim)
    raw_p = float(np.mean(boots <= 0.0))
    if lo > 0 and dlo > 0:
        tag = "CI_POSITIVE"
    else:
        tag = "DESCRIPTIVE_ONLY"
    return {
        "avg_return_bps_ci95": [float(lo), float(hi)],
        "delta_vs_baseline_ci95": [float(dlo), float(dhi)],
        "raw_p_value": raw_p,
        "obs_avg": obs,
        "obs_delta": obs_delta,
        "tag": tag,
        "n_trades": n,
    }


def attach_bootstrap_and_fdr(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    boot_rows = []
    pvals = []
    for i, r in enumerate(rows):
        # deterministic per-pair seed derived from global seed + index
        b = bootstrap_pair(
            np.asarray(r["trade_rets_bps"], dtype=float),
            np.asarray(r["baseline_rets_bps"], dtype=float),
            seed=BOOTSTRAP_SEED + i,
        )
        r["bootstrap"] = b
        pvals.append(b["raw_p_value"] if b["raw_p_value"] is not None else 1.0)
        boot_rows.append(r)
    qvals = _bh_qvalues(pvals)
    for r, q in zip(boot_rows, qvals):
        r["bootstrap"]["bh_q_value"] = q
        if r["bootstrap"]["tag"] == "CI_POSITIVE" and q < 0.10:
            r["bootstrap"]["tag"] = "FDR_SUPPORTED"
        r["stat_tag"] = r["bootstrap"]["tag"]
    return boot_rows
