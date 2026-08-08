"""Aggregates: opportunity-weighted, missed winners, adverse, day, LOSO/LODO."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from . import ARM_AGGRESSIVE, ARM_INSIDE, ARM_PASSIVE, HORIZONS_PRIMARY


def dist_stats(xs: list[float]) -> dict[str, Any]:
    a = np.asarray([x for x in xs if x is not None and np.isfinite(x)], dtype=float)
    if a.size == 0:
        return {"n": 0, "mean": None, "median": None, "p10": None, "p50": None, "p90": None}
    return {
        "n": int(a.size),
        "mean": float(np.mean(a)),
        "median": float(np.median(a)),
        "p10": float(np.quantile(a, 0.10)),
        "p50": float(np.quantile(a, 0.50)),
        "p90": float(np.quantile(a, 0.90)),
    }


def _arm_blob(row: dict, arm: str) -> dict:
    if arm == ARM_AGGRESSIVE:
        return row["aggressive"]
    if arm == ARM_PASSIVE:
        return row["passive"]
    if arm == ARM_INSIDE:
        return row["inside"]
    raise ValueError(arm)


def opportunity_return(blob: dict, H: int) -> float:
    """Filled → ret_H (or 0 if invalid); unfilled → 0."""
    if not blob.get("filled"):
        return 0.0
    if blob.get(f"ret_{H}_valid") and blob.get(f"ret_{H}") is not None:
        return float(blob[f"ret_{H}"])
    return 0.0


def filled_return(blob: dict, H: int) -> float | None:
    if not blob.get("filled"):
        return None
    if blob.get(f"ret_{H}_valid") and blob.get(f"ret_{H}") is not None:
        return float(blob[f"ret_{H}"])
    return None


def ss_key(r: dict) -> tuple:
    return (r["date"], r["symbol"], r["session"])


def summarize_arm(rows: list[dict], arm: str) -> dict[str, Any]:
    n = len(rows)
    fills = 0
    saved = []
    filled_rets = {H: [] for H in HORIZONS_PRIMARY}
    opp = {H: [] for H in HORIZONS_PRIMARY}
    pos_contrib = {H: 0.0 for H in HORIZONS_PRIMARY}
    neg_contrib = {H: 0.0 for H in HORIZONS_PRIMARY}

    for r in rows:
        b = _arm_blob(r, arm)
        for H in HORIZONS_PRIMARY:
            o = opportunity_return(b, H)
            opp[H].append(o)
            if o > 0:
                pos_contrib[H] += o
            elif o < 0:
                neg_contrib[H] += o
        if b.get("filled"):
            fills += 1
            if b.get("entry_spread_saved_bps") is not None:
                saved.append(float(b["entry_spread_saved_bps"]))
            for H in HORIZONS_PRIMARY:
                fr = filled_return(b, H)
                if fr is not None:
                    filled_rets[H].append(fr)

    out: dict[str, Any] = {
        "arm": arm,
        "signals": n,
        "fills": fills,
        "fill_rate": float(fills / n) if n else None,
        "entry_spread_saved_bps": dist_stats(saved),
    }
    for H in HORIZONS_PRIMARY:
        out[f"ret{H}_filled_mean"] = float(np.mean(filled_rets[H])) if filled_rets[H] else None
        out[f"opp_w_ret{H}"] = float(np.mean(opp[H])) if opp[H] else None
        out[f"pf_equiv_pos{H}"] = float(pos_contrib[H])
        out[f"pf_equiv_neg{H}"] = float(neg_contrib[H])
        denom = abs(neg_contrib[H])
        out[f"pf_equiv{H}"] = (
            float(pos_contrib[H] / denom) if denom > 1e-12 else None
        )

    # weightings for opp_w ret600
    def _bal(group_fn):
        by: dict[Any, list[float]] = defaultdict(list)
        for r in rows:
            by[group_fn(r)].append(opportunity_return(_arm_blob(r, arm), 600))
        means = [float(np.mean(v)) for v in by.values() if v]
        return float(np.mean(means)) if means else None

    out["opp_w_ret600_episode"] = out.get("opp_w_ret600")
    out["opp_w_ret600_symbol_session"] = _bal(ss_key)
    out["opp_w_ret600_day"] = _bal(lambda r: r["date"])
    for H in (300, 900):
        out[f"opp_w_ret{H}_symbol_session"] = None
        by: dict[Any, list[float]] = defaultdict(list)
        for r in rows:
            by[ss_key(r)].append(opportunity_return(_arm_blob(r, arm), H))
        means = [float(np.mean(v)) for v in by.values() if v]
        out[f"opp_w_ret{H}_symbol_session"] = float(np.mean(means)) if means else None
    return out


def missed_winners(rows: list[dict], arm: str) -> dict[str, Any]:
    """Aggressive positive but passive/inside unfilled."""
    out = {}
    for thr_name, thr in (("gt0", 0.0), ("gt10", 10.0), ("gt20", 20.0)):
        agg_pos = 0
        missed = 0
        for r in rows:
            a = r["aggressive"]
            if not a.get("filled") or not a.get("ret_600_valid"):
                continue
            ret = float(a["ret_600"])
            if ret <= thr:
                continue
            agg_pos += 1
            b = _arm_blob(r, arm)
            if not b.get("filled"):
                missed += 1
        out[thr_name] = {
            "aggressive_positive_count": agg_pos,
            "passive_missed_positive_count": missed,
            "missed_winner_rate": float(missed / agg_pos) if agg_pos else None,
        }
    return out


def adverse_selection(rows: list[dict], arm: str) -> dict[str, Any]:
    filled_mid300, filled_mid600 = [], []
    unfilled_mid300, unfilled_mid600 = [], []
    filled_mfe, filled_mae = [], []
    unfilled_mfe, unfilled_mae = [], []

    for r in rows:
        b = _arm_blob(r, arm)
        # mid path from aggressive entry mid (same snap) using aggressive mid_* if available
        # Prefer arm's mid if filled; else aggressive mid as market direction from t0
        a = r["aggressive"]
        m300 = a.get("mid_300") if a.get("mid_300_valid") else None
        m600 = a.get("mid_600") if a.get("mid_600_valid") else None
        # MFE/MAE: for filled use arm path; for unfilled use aggressive path as counterfactual market
        if b.get("filled"):
            if m300 is not None:
                filled_mid300.append(float(m300))
            if m600 is not None:
                filled_mid600.append(float(m600))
            if b.get("mfe") is not None and np.isfinite(b["mfe"]):
                filled_mfe.append(float(b["mfe"]))
            if b.get("mae") is not None and np.isfinite(b["mae"]):
                filled_mae.append(float(b["mae"]))
        else:
            if m300 is not None:
                unfilled_mid300.append(float(m300))
            if m600 is not None:
                unfilled_mid600.append(float(m600))
            if a.get("mfe") is not None and np.isfinite(a["mfe"]):
                unfilled_mfe.append(float(a["mfe"]))
            if a.get("mae") is not None and np.isfinite(a["mae"]):
                unfilled_mae.append(float(a["mae"]))

    def _m(xs):
        return float(np.mean(xs)) if xs else None

    f_mid600 = _m(filled_mid600)
    u_mid600 = _m(unfilled_mid600)
    flag = False
    if f_mid600 is not None and u_mid600 is not None and f_mid600 < u_mid600 - 1.0:
        # filled mid worse by >1bps
        flag = True
    if f_mid600 is not None and f_mid600 < -2.0 and (u_mid600 is None or f_mid600 < u_mid600):
        flag = True

    return {
        "filled": {
            "n": sum(1 for r in rows if _arm_blob(r, arm).get("filled")),
            "mid300": _m(filled_mid300),
            "mid600": f_mid600,
            "mfe": _m(filled_mfe),
            "mae": _m(filled_mae),
        },
        "unfilled": {
            "n": sum(1 for r in rows if not _arm_blob(r, arm).get("filled")),
            "mid300": _m(unfilled_mid300),
            "mid600": u_mid600,
            "mfe": _m(unfilled_mfe),
            "mae": _m(unfilled_mae),
        },
        "delta_mid600_filled_minus_unfilled": (
            None if f_mid600 is None or u_mid600 is None else float(f_mid600 - u_mid600)
        ),
        "PASSIVE_ADVERSE_SELECTION": flag,
    }


def day_level(rows: list[dict], arm: str) -> list[dict]:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["date"]].append(r)
    out = []
    for day in sorted(by):
        g = by[day]
        s_arm = summarize_arm(g, arm)
        s_agg = summarize_arm(g, ARM_AGGRESSIVE)
        out.append({
            "date": day,
            "n": len(g),
            "fill_rate": s_arm["fill_rate"],
            "spread_saved_mean": (s_arm["entry_spread_saved_bps"] or {}).get("mean"),
            "opp_w_ret600": s_arm["opp_w_ret600"],
            "aggressive_opp_w_ret600": s_agg["opp_w_ret600"],
            "delta600_vs_aggressive": (
                None
                if s_arm["opp_w_ret600"] is None or s_agg["opp_w_ret600"] is None
                else float(s_arm["opp_w_ret600"] - s_agg["opp_w_ret600"])
            ),
        })
    return out


def day_majority_not_worse(days: list[dict], *, tol_bps: float = 0.0) -> dict[str, Any]:
    deltas = [d["delta600_vs_aggressive"] for d in days if d.get("delta600_vs_aggressive") is not None]
    if not deltas:
        return {"ok": False, "n": 0}
    better_or_eq = sum(1 for x in deltas if x >= -tol_bps)
    worse = sum(1 for x in deltas if x < -tol_bps)
    return {
        "ok": better_or_eq >= worse,  # majority not worse
        "n_days": len(deltas),
        "better_or_eq": better_or_eq,
        "worse": worse,
        "mean_delta": float(np.mean(deltas)),
        "neg_days": worse,
    }


def lodo_advantage(rows: list[dict], arm: str) -> dict[str, Any]:
    """Leave-one-day-out mean opp_w advantage vs aggressive."""
    days = sorted({r["date"] for r in rows})
    folds = []
    for hold in days:
        train = [r for r in rows if r["date"] != hold]
        if len(train) < 50:
            continue
        s_a = summarize_arm(train, arm)
        s_g = summarize_arm(train, ARM_AGGRESSIVE)
        if s_a["opp_w_ret600"] is None or s_g["opp_w_ret600"] is None:
            continue
        folds.append({
            "holdout_day": hold,
            "advantage600": float(s_a["opp_w_ret600"] - s_g["opp_w_ret600"]),
            "arm_opp": s_a["opp_w_ret600"],
            "agg_opp": s_g["opp_w_ret600"],
        })
    adv = [f["advantage600"] for f in folds]
    return {
        "folds": folds,
        "mean_advantage600": float(np.mean(adv)) if adv else None,
        "positive_folds": sum(1 for a in adv if a > 0),
        "n_folds": len(adv),
        "all_nonneg": all(a >= -0.5 for a in adv) if adv else False,
    }


def loso_advantage(rows: list[dict], arm: str, *, max_symbols: int = 40) -> dict[str, Any]:
    """Leave-one-symbol-out on top symbols by count (cap for cost)."""
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r["symbol"]] += 1
    top = [s for s, _ in sorted(counts.items(), key=lambda x: -x[1])[:max_symbols]]
    folds = []
    for hold in top:
        train = [r for r in rows if r["symbol"] != hold]
        s_a = summarize_arm(train, arm)
        s_g = summarize_arm(train, ARM_AGGRESSIVE)
        if s_a["opp_w_ret600"] is None or s_g["opp_w_ret600"] is None:
            continue
        folds.append({
            "holdout_symbol": hold,
            "advantage600": float(s_a["opp_w_ret600"] - s_g["opp_w_ret600"]),
        })
    adv = [f["advantage600"] for f in folds]
    return {
        "n_folds": len(adv),
        "mean_advantage600": float(np.mean(adv)) if adv else None,
        "positive_folds": sum(1 for a in adv if a > 0),
        "min_advantage600": float(np.min(adv)) if adv else None,
        "sample": folds[:15],
        "note": f"top-{max_symbols} symbols by episode count; 285A treated normally",
    }


def concentration_audit(rows: list[dict], arm: str) -> dict[str, Any]:
    """Check if advantage comes from few days/symbols."""
    by_day: dict[str, list[float]] = defaultdict(list)
    by_sym: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        dlt = opportunity_return(_arm_blob(r, arm), 600) - opportunity_return(r["aggressive"], 600)
        by_day[r["date"]].append(dlt)
        by_sym[r["symbol"]].append(dlt)
    day_means = sorted(
        ((d, float(np.mean(v))) for d, v in by_day.items()),
        key=lambda x: -x[1],
    )
    sym_means = sorted(
        ((s, float(np.mean(v))) for s, v in by_sym.items()),
        key=lambda x: -x[1],
    )
    total_pos = sum(max(0.0, float(np.sum(v))) for v in by_day.values())
    top2 = sum(max(0.0, m * len(by_day[d])) for d, m in day_means[:2]) if day_means else 0.0
    # simpler: share of positive delta mass in top 2 days
    pos_by_day = [(d, sum(x for x in v if x > 0)) for d, v in by_day.items()]
    pos_by_day.sort(key=lambda x: -x[1])
    pos_total = sum(x for _, x in pos_by_day)
    top2_share = (
        float((pos_by_day[0][1] + pos_by_day[1][1]) / pos_total)
        if len(pos_by_day) >= 2 and pos_total > 1e-12
        else None
    )
    severe = bool(top2_share is not None and top2_share > 0.70)
    return {
        "top_days": day_means[:5],
        "top_symbols": sym_means[:10],
        "top2_positive_delta_share": top2_share,
        "severe_concentration": severe,
    }
