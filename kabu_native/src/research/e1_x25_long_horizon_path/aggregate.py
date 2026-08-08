"""Candidate-level aggregation on shared eligible populations."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from . import (
    CONSUMED_DAY,
    DISCOVERY,
    EVALUATION,
    FIRST_TOUCH,
    HORIZONS,
    STRESS_DAY,
    UPSIDE_BPS,
)


def period_mask(dates: np.ndarray, name: str) -> np.ndarray:
    if name == "DISCOVERY":
        return np.isin(dates, list(DISCOVERY))
    if name == "EVALUATION":
        return np.isin(dates, list(EVALUATION))
    if name == "20260803":
        return dates == STRESS_DAY
    if name == "20260804":
        return dates == CONSUMED_DAY
    if name == "ALL":
        # primary population ALL = disc+eval+stress (no risk-only; 20260804 separate)
        return np.isin(dates, list(DISCOVERY + EVALUATION + (STRESS_DAY,)))
    raise ValueError(name)


def _nanmean(x: np.ndarray) -> Optional[float]:
    x = x[np.isfinite(x)]
    if x.size == 0:
        return None
    return float(np.mean(x))


def _nanmedian(x: np.ndarray) -> Optional[float]:
    x = x[np.isfinite(x)]
    if x.size == 0:
        return None
    return float(np.median(x))


def _nanq(x: np.ndarray, q: float) -> Optional[float]:
    x = x[np.isfinite(x)]
    if x.size == 0:
        return None
    return float(np.quantile(x, q))


def _balanced_mean(values: np.ndarray, groups: np.ndarray) -> Optional[float]:
    ok = np.isfinite(values)
    if not np.any(ok):
        return None
    v = values[ok]
    g = groups[ok]
    uniq, inv = np.unique(g, return_inverse=True)
    sums = np.bincount(inv, weights=v)
    cnts = np.bincount(inv)
    means = sums / np.maximum(cnts, 1)
    return float(np.mean(means))


def summarize_group(
    values: np.ndarray,
    dates: np.ndarray,
    symbols: np.ndarray,
    sessions: np.ndarray,
) -> dict[str, Any]:
    ok = np.isfinite(values)
    n = int(ok.sum())
    if n == 0:
        return {
            "n": 0, "days": 0, "symbols": 0, "sessions": 0,
            "mean": None, "median": None, "q25": None, "q75": None,
            "positive_rate": None, "negative_rate": None,
            "day_balanced": None, "symbol_balanced": None,
        }
    v = values[ok]
    return {
        "n": n,
        "days": int(np.unique(dates[ok]).size),
        "symbols": int(np.unique(symbols[ok]).size),
        "sessions": int(np.unique(sessions[ok]).size),
        "mean": float(np.mean(v)),
        "median": float(np.median(v)),
        "q25": float(np.quantile(v, 0.25)),
        "q75": float(np.quantile(v, 0.75)),
        "positive_rate": float(np.mean(v > 0)),
        "negative_rate": float(np.mean(v < 0)),
        "day_balanced": _balanced_mean(values, dates),
        "symbol_balanced": _balanced_mean(values, symbols),
    }


def rate_bool(mask: np.ndarray) -> Optional[float]:
    if mask.size == 0:
        return None
    return float(np.mean(mask.astype(np.float64)))


def aggregate_candidate_period(
    *,
    selected: np.ndarray,
    metrics: dict[str, np.ndarray],
    dates: np.ndarray,
    symbols: np.ndarray,
    sessions: np.ndarray,
    period: str,
    path_ok: np.ndarray,
) -> dict[str, Any]:
    """SELECTED / COMPLEMENT / ALL_ANCHORS on common eligible population per metric."""
    pm = period_mask(dates, period) & path_ok
    out: dict[str, Any] = {
        "period": period,
        "period_anchors": int(pm.sum()),
        "selected_anchors": int((selected & pm).sum()),
        "complement_anchors": int((~selected & pm).sum()),
        "horizons": {},
        "reach": {},
        "first_touch": {},
        "pre_rise": {},
        "giveback": {},
    }

    for h in list(HORIZONS) + ["session"]:
        key = f"{h}s" if isinstance(h, int) else h
        elig = pm & metrics[f"eligible_{key}"] & metrics.get(f"fresh_ok_{key}", np.ones(len(pm), dtype=bool))
        # common eligible population
        all_m = elig
        sel_m = elig & selected
        comp_m = elig & ~selected
        ret = metrics[f"return_{key}_bps"]
        mfe = metrics[f"MFE_{key}_bps"]
        mae = metrics[f"MAE_{key}_bps"]
        gb = metrics.get(f"terminal_giveback_from_MFE_{key}_bps", np.full(len(pm), np.nan))
        max_gb = metrics.get(f"max_giveback_after_MFE_{key}_bps", np.full(len(pm), np.nan))

        def pack(mask: np.ndarray) -> dict[str, Any]:
            s_ret = summarize_group(ret[mask], dates[mask], symbols[mask], sessions[mask])
            s_mfe = summarize_group(mfe[mask], dates[mask], symbols[mask], sessions[mask])
            s_mae = summarize_group(mae[mask], dates[mask], symbols[mask], sessions[mask])
            return {
                "support": int(mask.sum()),
                "days": s_ret["days"], "symbols": s_ret["symbols"], "sessions": s_ret["sessions"],
                "return": s_ret, "MFE": s_mfe, "MAE": s_mae,
                "median_terminal_giveback": _nanmedian(gb[mask]),
                "median_max_giveback": _nanmedian(max_gb[mask]),
            }

        all_p = pack(all_m)
        sel_p = pack(sel_m)
        comp_p = pack(comp_m)

        def delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
            if a is None or b is None:
                return None
            return float(a - b)

        out["horizons"][key] = {
            "eligible_n": int(all_m.sum()),
            "censored_in_period": int((pm & metrics[f"censored_{key}"]).sum()) if f"censored_{key}" in metrics else None,
            "SELECTED": sel_p,
            "COMPLEMENT": comp_p,
            "ALL_ANCHORS": all_p,
            "delta_vs_ALL": {
                "mean_return": delta(sel_p["return"]["mean"], all_p["return"]["mean"]),
                "median_return": delta(sel_p["return"]["median"], all_p["return"]["median"]),
                "mean_MFE": delta(sel_p["MFE"]["mean"], all_p["MFE"]["mean"]),
                "mean_MAE": delta(sel_p["MAE"]["mean"], all_p["MAE"]["mean"]),
            },
            "delta_vs_COMPLEMENT": {
                "mean_return": delta(sel_p["return"]["mean"], comp_p["return"]["mean"]),
                "median_return": delta(sel_p["return"]["median"], comp_p["return"]["median"]),
                "mean_MFE": delta(sel_p["MFE"]["mean"], comp_p["MFE"]["mean"]),
                "mean_MAE": delta(sel_p["MAE"]["mean"], comp_p["MAE"]["mean"]),
            },
            "retention_rate": (sel_p["support"] / all_p["support"]) if all_p["support"] else None,
        }

    # reach metrics (session path; eligibility = path_ok in period)
    base = pm
    for up in UPSIDE_BPS:
        reached = metrics[f"up_{up}_reached"]
        tsec = metrics[f"up_{up}_time_sec"]
        for label, mask in (
            ("SELECTED", base & selected),
            ("COMPLEMENT", base & ~selected),
            ("ALL_ANCHORS", base),
        ):
            m = mask
            rr = rate_bool(reached[m]) if m.any() else None
            times = tsec[m & reached]
            out["reach"].setdefault(f"up_{up}", {})[label] = {
                "support": int(m.sum()),
                "reach_rate": rr,
                "median_reach_time": _nanmedian(times),
                "q25_reach_time": _nanq(times, 0.25),
                "q75_reach_time": _nanq(times, 0.75),
            }
        # also horizon-gated reach proxies: reached within H using time <= H
        for h in (300, 900, 1800):
            within = reached & np.isfinite(tsec) & (tsec <= h)
            sel_r = rate_bool(within[base & selected]) if (base & selected).any() else None
            all_r = rate_bool(within[base]) if base.any() else None
            out["reach"][f"up_{up}"][f"within_{h}s"] = {
                "SELECTED_rate": sel_r,
                "ALL_rate": all_r,
                "delta_vs_ALL_pt": None if sel_r is None or all_r is None else (sel_r - all_r) * 100.0,
            }

        pre = metrics[f"pre_reach_MAE_{up}_bps"]
        out["pre_rise"][f"up_{up}"] = {
            "SELECTED_median_pre_MAE": _nanmedian(pre[base & selected & reached]),
            "ALL_median_pre_MAE": _nanmedian(pre[base & reached]),
        }

    for up, dn in FIRST_TOUCH:
        key = f"ft_{up}_{dn}"
        res = metrics[f"{key}_result"]
        block = {}
        for label, mask in (
            ("SELECTED", base & selected),
            ("COMPLEMENT", base & ~selected),
            ("ALL_ANCHORS", base),
        ):
            m = mask
            if not m.any():
                block[label] = {"up_first_rate": None, "down_first_rate": None, "neither_rate": None, "n": 0}
                continue
            rr = res[m]
            block[label] = {
                "n": int(m.sum()),
                "up_first_rate": float(np.mean(rr == "UP_FIRST")),
                "down_first_rate": float(np.mean(rr == "DOWN_FIRST")),
                "neither_rate": float(np.mean(rr == "NEITHER")),
            }
        out["first_touch"][key] = block

    return out
