"""Bootstrap / FDR / LODO / LOSO diagnostics (DESCRIPTIVE_ONLY; no gate)."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from . import BOOTSTRAP_ITERS, BOOTSTRAP_SEED, DISCOVERY


def bh_qvalues(pvals: np.ndarray) -> np.ndarray:
    n = len(pvals)
    q = np.full(n, np.nan)
    if n == 0:
        return q
    order = np.argsort(pvals)
    ranked = pvals[order]
    prev = 1.0
    out = np.empty(n)
    for i in range(n - 1, -1, -1):
        rank = i + 1
        val = ranked[i] * n / rank
        prev = min(prev, val)
        out[i] = min(prev, 1.0)
    q[order] = out
    return q


def day_cluster_bootstrap_delta(
    *,
    values: np.ndarray,
    selected: np.ndarray,
    dates: np.ndarray,
    eligible: np.ndarray,
    iters: int = BOOTSTRAP_ITERS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Day-cluster bootstrap of selected_mean - all_mean on eligible population."""
    elig = eligible & np.isfinite(values)
    if elig.sum() < 10 or (selected & elig).sum() < 5:
        return {
            "delta": None, "ci95": [None, None], "raw_p": None,
            "tag": "DESCRIPTIVE_ONLY", "iters": iters, "seed": seed,
        }
    days = np.unique(dates[elig])
    # per-day means
    day_sel = []
    day_all = []
    valid_days = []
    for d in days:
        m = elig & (dates == d)
        if not m.any():
            continue
        day_all.append(float(np.mean(values[m])))
        ms = m & selected
        if ms.any():
            day_sel.append(float(np.mean(values[ms])))
            valid_days.append(d)
        else:
            day_sel.append(np.nan)
            valid_days.append(d)
    day_sel_a = np.asarray(day_sel, dtype=np.float64)
    day_all_a = np.asarray(day_all, dtype=np.float64)
    ok = np.isfinite(day_sel_a) & np.isfinite(day_all_a)
    if ok.sum() < 2:
        return {
            "delta": None, "ci95": [None, None], "raw_p": None,
            "tag": "DESCRIPTIVE_ONLY", "iters": iters, "seed": seed,
        }
    obs = float(np.mean(day_sel_a[ok]) - np.mean(day_all_a[ok]))
    rng = np.random.default_rng(seed)
    idx = np.where(ok)[0]
    boots = np.empty(iters)
    for i in range(iters):
        samp = rng.choice(idx, size=idx.size, replace=True)
        boots[i] = float(np.mean(day_sel_a[samp]) - np.mean(day_all_a[samp]))
    lo, hi = np.quantile(boots, [0.025, 0.975])
    # two-sided raw p vs 0
    raw_p = float(np.mean(np.abs(boots) >= abs(obs))) if iters else None
    tag = "DESCRIPTIVE_ONLY"
    if lo > 0 or hi < 0:
        tag = "CI_SUPPORTED"
    return {
        "delta": obs,
        "ci95": [float(lo), float(hi)],
        "raw_p": raw_p,
        "tag": tag,
        "iters": iters,
        "seed": seed,
        "n_days": int(ok.sum()),
    }


def stability_diagnostics(
    *,
    values: np.ndarray,
    selected: np.ndarray,
    dates: np.ndarray,
    symbols: np.ndarray,
    eligible: np.ndarray,
    light: bool = False,
) -> dict[str, Any]:
    elig = eligible & np.isfinite(values)
    if not elig.any():
        return {"lodo": [], "loso": [], "max_day_contribution": None, "max_symbol_contribution": None}

    def mean_delta(mask: np.ndarray) -> Optional[float]:
        m = elig & mask
        if not m.any() or not (selected & m).any():
            return None
        return float(np.mean(values[selected & m]) - np.mean(values[m]))

    base = mean_delta(np.ones(len(dates), dtype=bool))
    lodo = []
    loso = []
    if not light:
        for d in sorted(set(dates[elig].tolist())):
            delta = mean_delta(dates != d)
            lodo.append({"left_out_day": d, "delta": delta, "delta_vs_full": None if delta is None or base is None else delta - base})
        uniq, cnts = np.unique(symbols[elig & selected], return_counts=True)
        top = uniq[np.argsort(-cnts)[:15]]
        for s in top:
            delta = mean_delta(symbols != s)
            loso.append({"left_out_symbol": s, "delta": delta, "delta_vs_full": None if delta is None or base is None else delta - base})

    # contribution: abs day means of selected returns
    day_contrib = {}
    for d in set(dates[elig & selected].tolist()):
        m = elig & selected & (dates == d)
        if m.any():
            day_contrib[d] = float(np.mean(values[m]))
    sym_contrib = {}
    for s in set(symbols[elig & selected].tolist()):
        m = elig & selected & (symbols == s)
        if m.any():
            sym_contrib[s] = float(np.mean(values[m]))

    max_day = max(day_contrib.items(), key=lambda x: abs(x[1])) if day_contrib else None
    max_sym = max(sym_contrib.items(), key=lambda x: abs(x[1])) if sym_contrib else None

    without_0722 = mean_delta(dates != "20260722")
    without_2354 = mean_delta(symbols != "2354")
    without_285A = mean_delta(symbols != "285A")

    return {
        "lodo": lodo,
        "loso": loso,
        "max_day_contribution": {"day": max_day[0], "mean": max_day[1]} if max_day else None,
        "max_symbol_contribution": {"symbol": max_sym[0], "mean": max_sym[1]} if max_sym else None,
        "without_20260722": without_0722,
        "without_2354": without_2354,
        "without_285A": without_285A,
        "full_delta": base,
    }


def apply_fdr_tags(boot_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pvals = []
    idxs = []
    for i, r in enumerate(boot_rows):
        if r.get("raw_p") is not None:
            pvals.append(r["raw_p"])
            idxs.append(i)
    if not pvals:
        return boot_rows
    q = bh_qvalues(np.asarray(pvals, dtype=np.float64))
    for j, i in enumerate(idxs):
        boot_rows[i]["bh_q"] = float(q[j])
        if q[j] <= 0.05 and boot_rows[i].get("tag") == "CI_SUPPORTED":
            boot_rows[i]["tag"] = "FDR_SUPPORTED"
        elif boot_rows[i].get("tag") is None:
            boot_rows[i]["tag"] = "DESCRIPTIVE_ONLY"
    return boot_rows
