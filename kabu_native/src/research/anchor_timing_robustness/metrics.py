"""Rank / overlap / economics helpers. Thresholds are precommitted in __init__."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness import (
    ENTRY_OVERLAP_STABLE,
    ENTRY_OVERLAP_UNSTABLE,
    MIN_SPEARMAN_N,
    NORMAL_SESSION_BUCKETS,
    SPEARMAN_ROBUST,
    SPEARMAN_SENSITIVE,
    TOP5_ROBUST,
    TOP5_SENSITIVE,
    TRADE_COUNT_RATIO_HI,
    TRADE_COUNT_RATIO_LO,
    VERDICT_MIXED,
    VERDICT_ROBUST,
    VERDICT_SENSITIVE,
)


def pf_of(pnls: list[float]) -> Optional[float]:
    wins = sum(x for x in pnls if x > 0)
    losses = sum(-x for x in pnls if x < 0)
    if losses <= 1e-12:
        return None if wins <= 1e-12 else float("inf")
    return wins / losses


def pf_out(v: Any) -> Any:
    if v is None:
        return None
    if v == float("inf"):
        return "Infinity"
    return v


def mean_finite(xs: list[Any]) -> Optional[float]:
    vals = []
    for x in xs:
        if x is None:
            continue
        try:
            f = float(x)
        except (TypeError, ValueError):
            continue
        if f != f:
            continue
        vals.append(f)
    if not vals:
        return None
    return float(np.mean(vals))


def _rankdata(a: np.ndarray) -> np.ndarray:
    n = int(a.size)
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.arange(1, n + 1, dtype=float)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and a[order[j + 1]] == a[order[i]]:
            j += 1
        if j > i:
            avg = 0.5 * (ranks[order[i]] + ranks[order[j]])
            for k in range(i, j + 1):
                ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(x: list[float], y: list[float]) -> Optional[float]:
    if len(x) != len(y) or len(x) < MIN_SPEARMAN_N:
        return None
    ax = np.asarray(x, dtype=float)
    ay = np.asarray(y, dtype=float)
    ok = np.isfinite(ax) & np.isfinite(ay)
    if int(ok.sum()) < MIN_SPEARMAN_N:
        return None
    rx = _rankdata(ax[ok])
    ry = _rankdata(ay[ok])
    if float(np.std(rx)) < 1e-12 or float(np.std(ry)) < 1e-12:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def overlap_at_k(a: list[str], b: list[str], k: int) -> dict[str, Any]:
    sa = set(a[:k])
    sb = set(b[:k])
    inter = sa & sb
    union = sa | sb
    return {
        f"top{k}_overlap": (len(inter) / float(k)) if k else None,
        f"top{k}_jaccard": (len(inter) / float(len(union))) if union else None,
        f"top{k}_a": sorted(sa),
        f"top{k}_b": sorted(sb),
        f"top{k}_inter": sorted(inter),
    }


def trade_stats(trades: list[dict[str, Any]], pnl_key: str = "pnl_yen_100") -> dict[str, Any]:
    pnls = [float(t.get(pnl_key) or 0.0) for t in trades]
    w = sum(1 for p in pnls if p > 1e-9)
    l = sum(1 for p in pnls if p < -1e-9)
    d = len(pnls) - w - l
    gp = sum(p for p in pnls if p > 0)
    gl = sum(-p for p in pnls if p < 0)
    pnl = round(sum(pnls), 2)
    mfes = [float(t["mfe_yen_100"]) for t in trades if t.get("mfe_yen_100") is not None]
    maes = [float(t["mae_yen_100"]) for t in trades if t.get("mae_yen_100") is not None]
    return {
        "trades": len(trades),
        "win": w,
        "loss": l,
        "draw": d,
        "win_rate": (w / len(pnls)) if pnls else None,
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "pnl": pnl,
        "PF": pf_out(pf_of(pnls)),
        "avg_trade": round(pnl / len(trades), 4) if trades else 0.0,
        "median_trade": float(np.median(pnls)) if pnls else None,
        "MFE": float(np.mean(mfes)) if mfes else None,
        "MAE": float(np.mean(maes)) if maes else None,
    }


def maxdd(trades: list[dict[str, Any]], *, time_key: str = "exit_time", pnl_key: str = "pnl_yen_100") -> float:
    ordered = sorted(
        trades,
        key=lambda t: (float(t.get(time_key) or t.get("fill_time") or 0.0), str(t.get("symbol") or "")),
    )
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for t in ordered:
        eq += float(t.get(pnl_key) or 0.0)
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return round(dd, 2)


def _bucket_status(spearman_v: Optional[float], top5_v: Optional[float]) -> str:
    if spearman_v is None or top5_v is None:
        return "INSUFFICIENT"
    if spearman_v >= SPEARMAN_ROBUST and top5_v >= TOP5_ROBUST:
        return "ROBUST"
    if spearman_v < SPEARMAN_SENSITIVE or top5_v < TOP5_SENSITIVE:
        return "SENSITIVE"
    return "MIXED"


def interpret(
    *,
    spearman_pm: dict[str, Optional[float]],
    top5_pm: dict[str, Optional[float]],
    entry_pm: dict[str, Optional[float]],
    isolated_stats: dict[str, dict[str, Any]],
    tod_status: dict[str, str],
) -> dict[str, Any]:
    sp5 = mean_finite([spearman_pm.get("M5"), spearman_pm.get("P5")])
    t5 = mean_finite([top5_pm.get("M5"), top5_pm.get("P5")])
    e5 = mean_finite([entry_pm.get("M5"), entry_pm.get("P5")])
    sp10 = mean_finite([spearman_pm.get("M10"), spearman_pm.get("P10")])
    t10 = mean_finite([top5_pm.get("M10"), top5_pm.get("P10")])
    e10 = mean_finite([entry_pm.get("M10"), entry_pm.get("P10")])

    selection_stable = bool(
        e5 is not None
        and e5 >= ENTRY_OVERLAP_STABLE
        and t5 is not None
        and t5 >= TOP5_ROBUST
        and sp5 is not None
        and sp5 >= SPEARMAN_ROBUST
    )
    selection_unstable = bool(
        (e5 is not None and e5 < ENTRY_OVERLAP_UNSTABLE)
        or (t5 is not None and t5 < TOP5_SENSITIVE)
        or (sp5 is not None and sp5 < SPEARMAN_SENSITIVE)
    )

    ref = isolated_stats.get("REFERENCE") or {}
    m5 = isolated_stats.get("M5") or {}
    p5 = isolated_stats.get("P5") or {}
    ref_n = int(ref.get("trades") or 0)
    economics_stable = True
    econ_notes: list[str] = []
    ref_pnl = float(ref.get("pnl") or 0.0)
    for lab, st in (("M5", m5), ("P5", p5)):
        n = int(st.get("trades") or 0)
        if ref_n <= 0 or n <= 0:
            economics_stable = False
            econ_notes.append(f"{lab}_empty_or_ref_empty")
            continue
        ratio = n / float(ref_n)
        if ratio < TRADE_COUNT_RATIO_LO or ratio > TRADE_COUNT_RATIO_HI:
            economics_stable = False
            econ_notes.append(f"{lab}_trade_count_ratio={ratio:.3f}")
        pnl = float(st.get("pnl") or 0.0)
        if abs(ref_pnl) > 1.0 and (ref_pnl > 0) != (pnl > 0):
            economics_stable = False
            econ_notes.append(f"{lab}_pnl_sign_flip")
        rpf = ref.get("PF")
        spf = st.get("PF")
        def _pf_num(v: Any) -> Optional[float]:
            if v is None:
                return None
            if v == "Infinity" or v == float("inf"):
                return float("inf")
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        rp, spv = _pf_num(rpf), _pf_num(spf)
        if rp is not None and spv is not None:
            r_ok = rp >= 1.0
            s_ok = spv >= 1.0
            if r_ok != s_ok:
                economics_stable = False
                econ_notes.append(f"{lab}_PF_regime_change")

    if sp5 is not None and t5 is not None:
        if sp5 >= SPEARMAN_ROBUST and t5 >= TOP5_ROBUST:
            exact = "LOW"
        elif sp5 < SPEARMAN_SENSITIVE or t5 < TOP5_SENSITIVE:
            exact = "HIGH"
        else:
            exact = "MEDIUM"
    else:
        exact = "HIGH" if selection_unstable else "MEDIUM"

    open_s = tod_status.get("OPEN_EARLY") or "INSUFFICIENT"
    pm_open_s = tod_status.get("PM_OPEN") or "INSUFFICIENT"
    normal_s = tod_status.get("NORMAL_SESSION") or "INSUFFICIENT"
    mixed_tod = (
        (open_s == "SENSITIVE" or pm_open_s == "SENSITIVE")
        and normal_s == "ROBUST"
    )

    if mixed_tod:
        verdict = VERDICT_MIXED
    elif exact == "LOW" and selection_stable:
        verdict = VERDICT_ROBUST
    elif exact == "HIGH" or selection_unstable:
        verdict = VERDICT_SENSITIVE
    elif normal_s == "ROBUST" and (open_s in {"SENSITIVE", "MIXED"} or pm_open_s in {"SENSITIVE", "MIXED"}):
        verdict = VERDICT_MIXED
    elif exact == "LOW":
        verdict = VERDICT_ROBUST
    else:
        verdict = VERDICT_SENSITIVE if not selection_stable else VERDICT_MIXED

    if exact == "HIGH" and not mixed_tod:
        overfit = "HIGH"
    elif mixed_tod or exact == "MEDIUM":
        overfit = "MEDIUM"
    else:
        overfit = "LOW"

    return {
        "RANK_SPEARMAN_M5": spearman_pm.get("M5"),
        "RANK_SPEARMAN_P5": spearman_pm.get("P5"),
        "RANK_SPEARMAN_M10": spearman_pm.get("M10"),
        "RANK_SPEARMAN_P10": spearman_pm.get("P10"),
        "TOP5_OVERLAP_M5": top5_pm.get("M5"),
        "TOP5_OVERLAP_P5": top5_pm.get("P5"),
        "TOP5_OVERLAP_M10": top5_pm.get("M10"),
        "TOP5_OVERLAP_P10": top5_pm.get("P10"),
        "ENTRY_OVERLAP_M5": entry_pm.get("M5"),
        "ENTRY_OVERLAP_P5": entry_pm.get("P5"),
        "ENTRY_OVERLAP_M10": entry_pm.get("M10"),
        "ENTRY_OVERLAP_P10": entry_pm.get("P10"),
        "SELECTION_STABLE": bool(selection_stable),
        "ECONOMICS_STABLE": bool(economics_stable),
        "EXACT_TIME_DEPENDENCE": exact,
        "OVERFIT_CONCERN": overfit,
        "verdict": verdict,
        "mean_spearman_pm5": sp5,
        "mean_top5_pm5": t5,
        "mean_entry_pm5": e5,
        "mean_spearman_pm10": sp10,
        "mean_top5_pm10": t10,
        "mean_entry_pm10": e10,
        "economics_notes": econ_notes,
        "OPEN_EARLY_RESULT": open_s,
        "NORMAL_SESSION_RESULT": normal_s,
        "PM_OPEN_RESULT": pm_open_s,
        "tod_status": tod_status,
        "thresholds": {
            "SPEARMAN_ROBUST": SPEARMAN_ROBUST,
            "SPEARMAN_SENSITIVE": SPEARMAN_SENSITIVE,
            "TOP5_ROBUST": TOP5_ROBUST,
            "TOP5_SENSITIVE": TOP5_SENSITIVE,
            "ENTRY_OVERLAP_STABLE": ENTRY_OVERLAP_STABLE,
            "ENTRY_OVERLAP_UNSTABLE": ENTRY_OVERLAP_UNSTABLE,
            "TRADE_COUNT_RATIO_LO": TRADE_COUNT_RATIO_LO,
            "TRADE_COUNT_RATIO_HI": TRADE_COUNT_RATIO_HI,
            "NORMAL_SESSION_BUCKETS": list(NORMAL_SESSION_BUCKETS),
        },
    }


def classify_tod_status(rows: list[dict[str, Any]]) -> dict[str, str]:
    """rows: per (day,anchor,shift) with spearman + top5_overlap + tod_bucket + shift_key."""
    from collections import defaultdict

    by: dict[str, dict[str, list]] = defaultdict(lambda: {"spearman": [], "top5": []})
    for r in rows:
        if not r.get("valid"):
            continue
        sk = str(r.get("shift_key") or "")
        if sk not in {"M5", "P5"}:
            continue
        b = str(r.get("tod_bucket") or "OTHER")
        if r.get("spearman") is not None:
            by[b]["spearman"].append(r["spearman"])
            by["ALL"]["spearman"].append(r["spearman"])
        if r.get("top5_overlap") is not None:
            by[b]["top5"].append(r["top5_overlap"])
            by["ALL"]["top5"].append(r["top5_overlap"])
        if b in NORMAL_SESSION_BUCKETS:
            if r.get("spearman") is not None:
                by["NORMAL_SESSION"]["spearman"].append(r["spearman"])
            if r.get("top5_overlap") is not None:
                by["NORMAL_SESSION"]["top5"].append(r["top5_overlap"])
    out: dict[str, str] = {}
    for name in list(by.keys()) + ["OPEN_EARLY", "AM_MID", "AM_LATE", "PM_OPEN", "PM_MID", "PM_LATE", "NORMAL_SESSION"]:
        sp = mean_finite(by.get(name, {}).get("spearman") or [])
        t5 = mean_finite(by.get(name, {}).get("top5") or [])
        out[name] = _bucket_status(sp, t5)
    return out
