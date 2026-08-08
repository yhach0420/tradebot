"""Mask-level ENTRY-only metrics for absolute-rise evaluation."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np


def summarize_mask(
    *,
    mask: np.ndarray,
    labels: dict[str, np.ndarray],
    dates: np.ndarray,
    symbols: np.ndarray,
    complement_base: Optional[np.ndarray] = None,
) -> dict[str, Any]:
    """Aggregate absolute-rise ENTRY metrics on selected episodes."""
    valid = labels["valid"]
    sel = mask & valid
    idx = np.where(sel)[0]
    n = int(idx.size)
    empty = {
        "episode_count": 0,
        "symbol_count": 0,
        "day_count": 0,
        "primary_win_rate": None,
        "primary_edge": None,
        "ft_plus30_count": 0,
        "ft_minus20_count": 0,
        "return_300": None,
        "return_600": None,
        "positive_return_rate_300": None,
        "positive_return_rate_600": None,
        "mfe": None,
        "mae": None,
        "selected_minus_complement_300": None,
        "selected_minus_complement_600": None,
        "positive_day_majority": False,
        "positive_days": 0,
        "eval_days": 0,
    }
    if n == 0:
        return empty

    prim = labels["primary"][idx]
    primary_wr = float(prim.mean())
    # first-touch edge vs 0.5 null; also vs complement if provided
    primary_edge = primary_wr - 0.5

    # +30 first / -20 first counts within primary horizon using times
    t_p30 = labels["time_to_p30"][idx]
    t_m20 = labels["time_to_m20"][idx]
    plus30 = 0
    minus20 = 0
    for a, b in zip(t_p30, t_m20):
        a_ok = np.isfinite(a) and a <= 600.0
        b_ok = np.isfinite(b) and b <= 600.0
        if a_ok and (not b_ok or a <= b):
            plus30 += 1
        elif b_ok and (not a_ok or b < a):
            minus20 += 1

    v300 = labels["return_300_valid"][idx]
    v600 = labels["return_600_valid"][idx]
    r300 = labels["return_300"][idx]
    r600 = labels["return_600"][idx]
    mean_300 = float(np.nanmean(r300[v300])) if v300.any() else None
    mean_600 = float(np.nanmean(r600[v600])) if v600.any() else None
    pos_300 = float(np.mean(r300[v300] > 0)) if v300.any() else None
    pos_600 = float(np.mean(r600[v600] > 0)) if v600.any() else None

    mfe = float(np.nanmean(labels["mfe"][idx]))
    mae = float(np.nanmean(labels["mae"][idx]))

    # day-level majority on return_300
    day_pos = 0
    day_eval = 0
    for d in sorted(set(dates[idx].tolist())):
        dm = dates[idx] == d
        vv = v300 & dm
        if int(vv.sum()) < 3:
            continue
        day_eval += 1
        if float(np.nanmean(r300[vv])) > 0:
            day_pos += 1

    delta_300 = delta_600 = None
    if complement_base is not None:
        comp = complement_base & valid & (~mask)
        cidx = np.where(comp)[0]
        if cidx.size >= 10:
            cv300 = labels["return_300_valid"][cidx]
            cv600 = labels["return_600_valid"][cidx]
            if mean_300 is not None and cv300.any():
                delta_300 = mean_300 - float(np.nanmean(labels["return_300"][cidx][cv300]))
            if mean_600 is not None and cv600.any():
                delta_600 = mean_600 - float(np.nanmean(labels["return_600"][cidx][cv600]))
            # primary edge vs complement rate
            c_prim = float(labels["primary"][cidx].mean())
            primary_edge = primary_wr - c_prim

    return {
        "episode_count": n,
        "symbol_count": int(len(set(symbols[idx].tolist()))),
        "day_count": int(len(set(dates[idx].tolist()))),
        "primary_win_rate": primary_wr,
        "primary_edge": float(primary_edge),
        "ft_plus30_count": int(plus30),
        "ft_minus20_count": int(minus20),
        "return_300": mean_300,
        "return_600": mean_600,
        "positive_return_rate_300": pos_300,
        "positive_return_rate_600": pos_600,
        "mfe": mfe,
        "mae": mae,
        "selected_minus_complement_300": delta_300,
        "selected_minus_complement_600": delta_600,
        "positive_day_majority": day_eval > 0 and day_pos > day_eval / 2.0,
        "positive_days": day_pos,
        "eval_days": day_eval,
    }


def day_symbol_concentration(
    *,
    mask: np.ndarray,
    labels: dict[str, np.ndarray],
    dates: np.ndarray,
    symbols: np.ndarray,
    ret_key: str = "return_300",
) -> dict[str, Any]:
    valid = labels["valid"] & labels[f"{ret_key}_valid"]
    sel = mask & valid
    idx = np.where(sel)[0]
    if idx.size == 0:
        return {
            "max_day_contribution_share": None,
            "max_symbol_contribution_share": None,
            "worst_day": None,
            "best_day": None,
            "worst_symbol": None,
            "best_symbol": None,
        }
    rets = labels[ret_key][idx]
    # positive-mass concentration: share of total positive return
    pos = np.clip(rets, 0, None)
    tot = float(pos.sum())
    day_shares = []
    day_means = {}
    for d in set(dates[idx].tolist()):
        dm = dates[idx] == d
        day_means[d] = float(np.nanmean(rets[dm]))
        share = float(pos[dm].sum()) / tot if tot > 1e-12 else 0.0
        day_shares.append((d, share))
    sym_shares = []
    sym_means = {}
    for s in set(symbols[idx].tolist()):
        sm = symbols[idx] == s
        sym_means[s] = float(np.nanmean(rets[sm]))
        share = float(pos[sm].sum()) / tot if tot > 1e-12 else 0.0
        sym_shares.append((s, share))
    day_shares.sort(key=lambda x: -x[1])
    sym_shares.sort(key=lambda x: -x[1])
    worst_day = min(day_means, key=day_means.get) if day_means else None
    best_day = max(day_means, key=day_means.get) if day_means else None
    worst_sym = min(sym_means, key=sym_means.get) if sym_means else None
    best_sym = max(sym_means, key=sym_means.get) if sym_means else None
    return {
        "max_day_contribution_share": day_shares[0][1] if day_shares else None,
        "max_symbol_contribution_share": sym_shares[0][1] if sym_shares else None,
        "worst_day": worst_day,
        "best_day": best_day,
        "worst_symbol": worst_sym,
        "best_symbol": best_sym,
        "median_daily_return": float(np.median(list(day_means.values()))) if day_means else None,
    }


def passes_inner(summary: dict[str, Any], *, min_ep: int, min_sym: int) -> bool:
    if (summary.get("episode_count") or 0) < min_ep:
        return False
    if (summary.get("symbol_count") or 0) < min_sym:
        return False
    if (summary.get("primary_edge") or 0) <= 0:
        return False
    if (summary.get("return_300") or 0) <= 0:
        return False
    if (summary.get("return_600") or 0) <= 0:
        return False
    # absolute rise required: primary win rate must exceed complement-adjusted edge path
    # selected-minus-complement alone is NOT enough — already enforced by absolute returns
    if not summary.get("positive_day_majority"):
        return False
    if (summary.get("eval_days") or 0) < 2:
        return False
    return True
