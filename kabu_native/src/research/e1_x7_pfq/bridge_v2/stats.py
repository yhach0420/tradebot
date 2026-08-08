"""Matched parents, rates, bootstrap, verdict helpers."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Optional

import numpy as np

from . import BOOTSTRAP_REPS, BOOTSTRAP_SEED


FT_KEYS = (
    "plus5_vs_minus10",
    "plus5_vs_minus15",
    "plus10_vs_minus10",
    "plus10_vs_minus15",
)


def rate_plus_first(rows: list[dict[str, Any]], key: str, mode: str) -> Optional[float]:
    vals = []
    for r in rows:
        ft = r.get(f"{mode}_ft") or {}
        v = ft.get(key)
        if v in ("NOT_EVALUABLE", None):
            continue
        vals.append(1.0 if v == "PLUS_FIRST" else 0.0)
    if not vals:
        return None
    return float(np.mean(vals))


def rate_net_reached(rows: list[dict[str, Any]], mode: str, thr: float) -> Optional[float]:
    vals = []
    for r in rows:
        p = r.get(mode) or {}
        if not p.get("evaluable"):
            continue
        best = p.get("best_net_pnl_bps_300s")
        if best is None:
            continue
        vals.append(1.0 if float(best) >= thr else 0.0)
    if not vals:
        return None
    return float(np.mean(vals))


def compare_sets(
    cand: list[dict[str, Any]],
    parent: list[dict[str, Any]],
    *,
    mode: str,
) -> dict[str, Any]:
    out: dict[str, Any] = {"mode": mode, "metrics": {}}
    for thr, name in [(5.0, "net_plus5_rate"), (10.0, "net_plus10_rate")]:
        cr = rate_net_reached(cand, mode, thr)
        pr = rate_net_reached(parent, mode, thr)
        out["metrics"][name] = {
            "candidate_rate": cr,
            "matched_parent_rate": pr,
            "difference": None if cr is None or pr is None else cr - pr,
        }
    for k in FT_KEYS:
        cr = rate_plus_first(cand, k, mode)
        pr = rate_plus_first(parent, k, mode)
        out["metrics"][f"{k}_rate"] = {
            "candidate_rate": cr,
            "matched_parent_rate": pr,
            "difference": None if cr is None or pr is None else cr - pr,
        }
    # day differences for first-touch metrics
    days = sorted({r["day"] for r in cand} | {r["day"] for r in parent})
    day_diff: dict[str, dict[str, Optional[float]]] = {}
    for k in FT_KEYS:
        day_diff[k] = {}
        pos = neg = 0
        for d in days:
            c_d = [r for r in cand if r["day"] == d]
            p_d = [r for r in parent if r["day"] == d]
            cr = rate_plus_first(c_d, k, mode)
            pr = rate_plus_first(p_d, k, mode)
            diff = None if cr is None or pr is None else cr - pr
            day_diff[k][d] = diff
            if diff is not None:
                if diff > 0:
                    pos += 1
                elif diff < 0:
                    neg += 1
        out["metrics"][f"{k}_rate"]["day_difference"] = day_diff[k]
        out["metrics"][f"{k}_rate"]["positive_difference_days"] = pos
        out["metrics"][f"{k}_rate"]["negative_difference_days"] = neg
        out["metrics"][f"{k}_rate"]["n_days_scored"] = pos + neg
    return out


def bootstrap_difference(
    cand: list[dict[str, Any]],
    parent: list[dict[str, Any]],
    *,
    mode: str,
    metric_key: str,
    rate_fn: Callable[[list[dict[str, Any]]], Optional[float]],
    reps: int = BOOTSTRAP_REPS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Cluster bootstrap on day×symbol units present in union."""
    units = sorted({(r["day"], r["symbol"]) for r in cand} | {(r["day"], r["symbol"]) for r in parent})
    if not units:
        return {"difference_median": None, "difference_ci95": [None, None], "crosses_zero": None, "positive_fraction": None}

    by_unit_c: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_unit_p: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in cand:
        by_unit_c[(r["day"], r["symbol"])].append(r)
    for r in parent:
        by_unit_p[(r["day"], r["symbol"])].append(r)

    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(reps):
        draw = [units[i] for i in rng.integers(0, len(units), size=len(units))]
        c_rows = [x for u in draw for x in by_unit_c.get(u, [])]
        p_rows = [x for u in draw for x in by_unit_p.get(u, [])]
        cr = rate_fn(c_rows)
        pr = rate_fn(p_rows)
        if cr is None or pr is None:
            continue
        diffs.append(cr - pr)
    if not diffs:
        return {"difference_median": None, "difference_ci95": [None, None], "crosses_zero": None, "positive_fraction": None}
    arr = np.asarray(diffs, dtype=float)
    lo, hi = np.quantile(arr, [0.025, 0.975])
    return {
        "metric": metric_key,
        "mode": mode,
        "n_units": len(units),
        "n_valid_reps": len(diffs),
        "difference_median": float(np.median(arr)),
        "difference_ci95": [float(lo), float(hi)],
        "crosses_zero": bool(lo <= 0 <= hi),
        "positive_fraction": float(np.mean(arr > 0)),
    }


def entry_path_supported(enrichment: dict[str, Any]) -> tuple[bool, list[str]]:
    """fixed-grid first-touch: ci lower>0 and day diff positive >=7/9."""
    reasons = []
    fg = enrichment.get("fixed_grid") or {}
    metrics = fg.get("metrics") or {}
    ok_any = False
    for k in FT_KEYS:
        m = metrics.get(f"{k}_rate") or {}
        boot = m.get("bootstrap") or {}
        ci = boot.get("difference_ci95") or [None, None]
        lo = ci[0]
        pos_days = int(m.get("positive_difference_days") or 0)
        n_scored = int(m.get("n_days_scored") or 0)
        day_ok = pos_days >= 7 and n_scored >= 7
        if lo is not None and lo > 0 and day_ok:
            ok_any = True
            reasons.append(f"{k}: ci_lo={lo:.4f} pos_days={pos_days}/{n_scored}")
    return ok_any, reasons


def observation_density_proxy(event_enr: dict[str, Any], fixed_enr: dict[str, Any]) -> bool:
    """Event-time improves first-touch; fixed-grid improvement disappears."""
    e_metrics = (event_enr.get("metrics") or {})
    f_metrics = (fixed_enr.get("metrics") or {})
    event_ok = False
    fixed_ok = False
    for k in ("plus5_vs_minus10_rate", "plus5_vs_minus15_rate"):
        ed = (e_metrics.get(k) or {}).get("difference")
        fd = (f_metrics.get(k) or {}).get("difference")
        e_boot = ((e_metrics.get(k) or {}).get("bootstrap") or {})
        f_boot = ((f_metrics.get(k) or {}).get("bootstrap") or {})
        e_lo = (e_boot.get("difference_ci95") or [None, None])[0]
        f_lo = (f_boot.get("difference_ci95") or [None, None])[0]
        if ed is not None and ed > 0 and e_lo is not None and e_lo > 0:
            event_ok = True
        if fd is not None and fd > 0 and f_lo is not None and f_lo > 0:
            fixed_ok = True
    return event_ok and not fixed_ok


def volatility_proxy_only(enrichment_fg: dict[str, Any], enrichment_ev: dict[str, Any]) -> bool:
    """best/net_plus rates improve but plus5_before_minus10/15 do not."""
    improved_oracle = False
    ft_improved = False
    for enr in (enrichment_fg, enrichment_ev):
        m = enr.get("metrics") or {}
        for name in ("net_plus5_rate", "net_plus10_rate"):
            d = (m.get(name) or {}).get("difference")
            if d is not None and d > 0:
                improved_oracle = True
        for name in ("plus5_vs_minus10_rate", "plus5_vs_minus15_rate"):
            boot = ((m.get(name) or {}).get("bootstrap") or {})
            lo = (boot.get("difference_ci95") or [None, None])[0]
            if lo is not None and lo > 0:
                ft_improved = True
    # Also check best_net mean difference if present
    return improved_oracle and not ft_improved
