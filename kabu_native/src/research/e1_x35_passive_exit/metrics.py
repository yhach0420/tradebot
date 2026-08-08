"""EXIT economics, path aggregate, LODO/LOSO, path class diagnostics."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from . import MAX_SYMBOL_CONTRIB, OCCUPANCY_PROXY_600S
from .exits import run_spec


def dist_stats(xs: list[float]) -> dict[str, Any]:
    a = np.asarray([x for x in xs if x is not None and np.isfinite(x)], dtype=float)
    if a.size == 0:
        return {"n": 0, "mean": None, "median": None, "p75": None, "p90": None, "p95": None}
    return {
        "n": int(a.size),
        "mean": float(np.mean(a)),
        "median": float(np.median(a)),
        "p75": float(np.quantile(a, 0.75)),
        "p90": float(np.quantile(a, 0.90)),
        "p95": float(np.quantile(a, 0.95)),
    }


def evaluate_spec(eps: list[dict], spec: dict) -> dict[str, Any]:
    by_day: dict[str, list[float]] = defaultdict(list)
    by_ss: dict[tuple, list[float]] = defaultdict(list)
    rows = []
    for e in eps:
        r = run_spec(e, spec)
        if not r.get("ok"):
            continue
        rows.append((e, r))
    if not rows:
        return {"ok": False, "n": 0, "spec_id": spec.get("id")}
    rets = [float(r["exit_ret_bps"]) for _, r in rows]
    holds = [float(r["hold_sec"]) for _, r in rows]
    for e, r in rows:
        by_day[e["date"]].append(float(r["exit_ret_bps"]))
        by_ss[(e["date"], e["symbol"], e["session"])].append(float(r["exit_ret_bps"]))

    day_means = {d: float(np.mean(v)) for d, v in by_day.items()}
    ss_means = [float(np.mean(v)) for v in by_ss.values()]
    pos = sum(x for x in rets if x > 0)
    neg = sum(x for x in rets if x < 0)
    pf = float(pos / abs(neg)) if abs(neg) > 1e-12 else None
    hold_stats = dist_stats(holds)

    # concentration
    by_sym: dict[str, float] = defaultdict(float)
    tot_pos = 0.0
    for e, r in rows:
        v = float(r["exit_ret_bps"])
        if v > 0:
            by_sym[e["symbol"]] += v
            tot_pos += v
    top_share = float(max(by_sym.values()) / tot_pos) if tot_pos > 1e-12 and by_sym else None

    reason_counts = defaultdict(int)
    for _, r in rows:
        reason_counts[r["reason"]] += 1

    return {
        "ok": True,
        "spec_id": spec.get("id"),
        "family": spec.get("family"),
        "n": len(rets),
        "mean_ret_bps": float(np.mean(rets)),
        "median_ret_bps": float(np.median(rets)),
        "pf": pf,
        "positive_days": sum(1 for v in day_means.values() if v > 0),
        "negative_days": sum(1 for v in day_means.values() if v < 0),
        "n_days": len(day_means),
        "median_day": float(np.median(list(day_means.values()))) if day_means else None,
        "worst_day": float(min(day_means.values())) if day_means else None,
        "best_day": float(max(day_means.values())) if day_means else None,
        "day_means": day_means,
        "ss_balanced": float(np.mean(ss_means)) if ss_means else None,
        "day_balanced": float(np.mean(list(day_means.values()))) if day_means else None,
        "hold_sec": hold_stats,
        "hold_vs_proxy600": (
            None if hold_stats["median"] is None
            else float(OCCUPANCY_PROXY_600S - hold_stats["median"])
        ),
        "max_symbol_contrib_share": top_share,
        "severe_symbol_concentration": bool(top_share is not None and top_share > MAX_SYMBOL_CONTRIB),
        "reason_counts": dict(reason_counts),
    }


def aggregate_path_metrics(eps: list[dict]) -> dict[str, Any]:
    keys = [
        "mfe", "mae", "time_to_mfe", "time_to_mae", "max_giveback", "giveback_to_end",
        "first_positive_sec", "first_p10", "first_p20", "first_p30", "first_m10", "first_m20",
        "exec_60", "exec_180", "exec_300", "exec_600", "exec_900",
    ]
    out = {}
    for k in keys:
        xs = [float(e["metrics"][k]) for e in eps if e["metrics"].get(k) is not None and np.isfinite(e["metrics"][k])]
        out[k] = dist_stats(xs)
    return out


def answer_path_questions(agg: dict[str, Any], eps: list[dict]) -> dict[str, str]:
    t_mfe = (agg.get("time_to_mfe") or {}).get("median")
    mfe = (agg.get("mfe") or {}).get("mean")
    mae = (agg.get("mae") or {}).get("mean")
    gb = (agg.get("max_giveback") or {}).get("mean")
    e60 = (agg.get("exec_60") or {}).get("mean")
    e300 = (agg.get("exec_300") or {}).get("mean")
    e600 = (agg.get("exec_600") or {}).get("mean")
    fm20 = (agg.get("first_m20") or {}).get("median")

    # winner vs loser early behavior
    wins = [e for e in eps if (e["metrics"].get("exec_600") or 0) > 0]
    losses = [e for e in eps if (e["metrics"].get("exec_600") or 0) <= 0]
    win_early = float(np.mean([e["metrics"]["exec_60"] for e in wins if e["metrics"].get("exec_60") is not None])) if wins else None
    loss_early = float(np.mean([e["metrics"]["exec_60"] for e in losses if e["metrics"].get("exec_60") is not None])) if losses else None

    q1 = (
        f"Median time_to_MFE ~ {t_mfe:.1f}s; mean exec@60/300/600 = "
        f"{e60}/{e300}/{e600} bps. Profit typically accrues over minutes, peak ~median t_mfe."
        if t_mfe is not None else "insufficient path"
    )
    q2 = (
        f"Winners mean exec@60={win_early:.2f} vs losers {loss_early:.2f}. "
        + (
            "Winners often positive early (quick rise)."
            if win_early is not None and win_early > 0
            else "Winners not clearly positive early (pullback-then-rise possible)."
        )
    )
    q3 = (
        f"Median first -20bps touch ~ {fm20:.1f}s; mean MAE={mae:.1f}bps. "
        f"Losers often identifiable within ~{fm20:.0f}s if stop around 20bps."
        if fm20 is not None else f"mean MAE={mae}; first-touch -20 sparse"
    )
    q4 = f"Mean max giveback from MFE ~ {gb:.1f}bps (mean MFE={mfe:.1f})."
    q5 = (
        f"Fixed600 mean={e600:.2f}. If dynamic EXIT shortens hold below 600s while keeping/improving "
        f"return, capacity proxy improves; see EXIT CV vs FIXED controls."
    )
    return {"Q1": q1, "Q2": q2, "Q3": q3, "Q4": q4, "Q5": q5}


def classify_paths(eps: list[dict], *, train_eps: list[dict]) -> dict[str, Any]:
    """Train-derived classification thresholds (diagnostic only)."""
    t_mfe = [e["metrics"]["time_to_mfe"] for e in train_eps]
    gb = [e["metrics"]["max_giveback"] for e in train_eps]
    t_q50 = float(np.median(t_mfe)) if t_mfe else 120.0
    gb_q50 = float(np.median(gb)) if gb else 20.0
    counts = defaultdict(int)
    for e in eps:
        m = e["metrics"]
        r600 = m.get("exec_600") or 0.0
        if r600 > 0 and (m.get("time_to_mfe") or 999) <= t_q50 and (m.get("exec_60") or 0) > 0:
            counts["QUICK_WIN"] += 1
        elif r600 > 0 and (m.get("exec_60") or 0) <= 0:
            counts["SLOW_WIN"] += 1
        elif (m.get("mfe") or 0) > 10 and (m.get("max_giveback") or 0) >= gb_q50 and r600 <= 0:
            counts["GIVEBACK"] += 1
        elif (m.get("mfe") or 0) < 10 and (m.get("time_to_mfe") or 0) > 180:
            counts["NO_PROGRESS"] += 1
        elif (m.get("first_m20") or 9999) <= 60 and r600 <= 0:
            counts["FAST_LOSS"] += 1
        elif (m.get("mae") or 0) < -20 and r600 > 0:
            counts["RECOVERY"] += 1
        else:
            counts["OTHER"] += 1
    return {
        "train_t_mfe_q50": t_q50,
        "train_giveback_q50": gb_q50,
        "counts": dict(counts),
        "note": "diagnostic only - not used as ENTRY filter",
    }


def lodo_spec(eps: list[dict], spec: dict) -> dict[str, Any]:
    days = sorted({e["date"] for e in eps})
    folds = []
    for hold in days:
        rest = [e for e in eps if e["date"] != hold]
        hold_eps = [e for e in eps if e["date"] == hold]
        if len(hold_eps) < 1 or len(rest) < 5:
            continue
        sm = evaluate_spec(hold_eps, spec)
        folds.append({"holdout_day": hold, "mean_ret": sm.get("mean_ret_bps"), "n": sm.get("n")})
    pos = sum(1 for f in folds if (f.get("mean_ret") or 0) > 0)
    return {
        "n_folds": len(folds),
        "positive_holdout_days": pos,
        "majority_positive": pos > len(folds) / 2.0 if folds else False,
        "folds": folds,
    }


def loso_spec(eps: list[dict], spec: dict, *, max_symbols: int = 30) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for e in eps:
        counts[e["symbol"]] += 1
    top = [s for s, _ in sorted(counts.items(), key=lambda x: -x[1])[:max_symbols]]
    folds = []
    for hold in top:
        rest = [e for e in eps if e["symbol"] != hold]
        sm = evaluate_spec(rest, spec)
        folds.append({"holdout_symbol": hold, "mean_ret": sm.get("mean_ret_bps")})
    pos = sum(1 for f in folds if (f.get("mean_ret") or 0) > 0)
    return {
        "n_folds": len(folds),
        "positive_folds": pos,
        "majority_positive": pos > len(folds) / 2.0 if folds else False,
        "sample": folds[:10],
    }
