"""Phase A analysis: deltas, day/LOSO/TOD, late-chase, interpretation."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from . import (
    DAY_SUPPORT_MIN,
    DELTA_P2_BPS,
    HISTORICAL_DAYS,
    HORIZONS_SEC,
    MAX_SYMBOL_CONTRIB,
    STRESS_DAYS_285A,
    TOD_BUCKETS,
)

JST = ZoneInfo("Asia/Tokyo")


def candidate_horizon_summary(labels: dict[str, np.ndarray]) -> dict[str, Any]:
    out = {}
    v = labels["valid"]
    for H in HORIZONS_SEC:
        vv = v & labels[f"return_{H}_valid"]
        rs = labels[f"return_{H}"][vv]
        out[H] = {
            "n": int(vv.sum()),
            "mean": float(np.mean(rs)) if vv.any() else None,
            "median": float(np.median(rs)) if vv.any() else None,
            "positive_rate": float(np.mean(rs > 0)) if vv.any() else None,
        }
    # first touches
    out["ft"] = {
        "plus20_rate": float(np.isfinite(labels["time_to_p20"][v]).mean()) if v.any() else None,
        "plus30_rate": float(np.isfinite(labels["time_to_p30"][v]).mean()) if v.any() else None,
        "plus50_rate": float(np.isfinite(labels["time_to_p50"][v]).mean()) if v.any() else None,
        "minus20_rate": float(np.isfinite(labels["time_to_m20"][v]).mean()) if v.any() else None,
        "minus30_rate": float(np.isfinite(labels["time_to_m30"][v]).mean()) if v.any() else None,
        "minus50_rate": float(np.isfinite(labels["time_to_m50"][v]).mean()) if v.any() else None,
        # first-touch win: +30 before -20 within 600
        "primary_long_ft_rate": float(labels["primary"][v].mean()) if v.any() else None,
    }
    out["mfe"] = float(np.nanmean(labels["mfe"][v])) if v.any() else None
    out["mae"] = float(np.nanmean(labels["mae"][v])) if v.any() else None
    return out


def _mean_by_day(rows, labels, H: int) -> dict[str, float]:
    dates = np.array([r["date"] for r in rows])
    v = labels["valid"] & labels[f"return_{H}_valid"]
    out = {}
    for d in HISTORICAL_DAYS:
        m = v & (dates == d)
        if m.sum() < 5:
            continue
        out[d] = float(np.mean(labels[f"return_{H}"][m]))
    return out


def _control_mean_by_day(ctrl_rows: list[dict[str, Any]]) -> dict[str, float]:
    by = defaultdict(list)
    for x in ctrl_rows:
        by[x["date"]].append(x["ret"])
    return {d: float(np.mean(vs)) for d, vs in by.items() if len(vs) >= 3}


def day_level_audit(
    rows: list[dict[str, Any]],
    labels: dict[str, np.ndarray],
    controls: dict[str, Any],
) -> dict[str, Any]:
    cand300 = _mean_by_day(rows, labels, 300)
    cand600 = _mean_by_day(rows, labels, 600)
    ctrl300 = _control_mean_by_day(controls["same_symbol_rows"][300])
    ctrl600 = _control_mean_by_day(controls["same_symbol_rows"][600])
    days = []
    neg300 = neg600 = 0
    for d in HISTORICAL_DAYS:
        c3, k3 = cand300.get(d), ctrl300.get(d)
        c6, k6 = cand600.get(d), ctrl600.get(d)
        d3 = (c3 - k3) if (c3 is not None and k3 is not None) else None
        d6 = (c6 - k6) if (c6 is not None and k6 is not None) else None
        if d3 is not None and d3 < 0:
            neg300 += 1
        if d6 is not None and d6 < 0:
            neg600 += 1
        days.append({
            "date": d,
            "cand_ret300": c3, "ctrl_ret300": k3, "delta300": d3,
            "cand_ret600": c6, "ctrl_ret600": k6, "delta600": d6,
        })
    return {
        "days": days,
        "negative_delta300_days": neg300,
        "negative_delta600_days": neg600,
        "day_support_ok": neg300 >= DAY_SUPPORT_MIN and neg600 >= DAY_SUPPORT_MIN,
    }


def loso_delta(
    rows: list[dict[str, Any]],
    labels: dict[str, np.ndarray],
    controls: dict[str, Any],
) -> dict[str, Any]:
    """LOSO on candidate-control downward edge at ret300."""
    symbols = np.array([r["symbol"] for r in rows])
    dates = np.array([r["date"] for r in rows])
    v = labels["valid"] & labels["return_300_valid"]

    # map control rets by cand_i
    ctrl_by_cand: dict[int, list[float]] = defaultdict(list)
    for x in controls["same_symbol_rows"][300]:
        ctrl_by_cand[x["cand_i"]].append(x["ret"])

    def edge_omit(omit: str | None) -> tuple[float | None, int]:
        deltas = []
        for i in np.where(v)[0]:
            if omit is not None and symbols[i] == omit:
                continue
            if i not in ctrl_by_cand:
                continue
            c = float(labels["return_300"][i])
            k = float(np.mean(ctrl_by_cand[i]))
            deltas.append(c - k)
        if len(deltas) < 30:
            return None, len(deltas)
        return float(np.mean(deltas)), len(deltas)

    full, n_full = edge_omit(None)
    uniq = sorted(set(symbols[v].tolist()))
    rows_out = []
    for sym in uniq:
        e, n = edge_omit(sym)
        if e is None or full is None:
            continue
        # contribution: how much omitting improves (less negative) edge
        contrib = float(full - e)  # if full more negative, contrib > 0 means sym worsened edge
        rows_out.append({
            "omitted_symbol": sym,
            "delta300": e,
            "n": n,
            "symbol_contribution": contrib,
        })
    if not rows_out or full is None:
        return {"full_delta300": full, "n": n_full, "severe": False}
    worst = max(rows_out, key=lambda x: x["symbol_contribution"])
    best = min(rows_out, key=lambda x: x["symbol_contribution"])
    # concentration: share of negative mass by symbol on candidate absolute returns
    rets = labels["return_300"][v]
    neg = np.clip(-rets, 0, None)
    tot = float(neg.sum()) + 1e-12
    max_share = 0.0
    for sym in uniq:
        sm = v & (symbols == sym)
        share = float(np.clip(-labels["return_300"][sm], 0, None).sum()) / tot
        max_share = max(max_share, share)
    pos_loso = sum(1 for r in rows_out if (r["delta300"] or 0) < 0)
    neg_loso = sum(1 for r in rows_out if (r["delta300"] or 0) >= 0)
    return {
        "full_delta300": full,
        "n": n_full,
        "positive_directional_loso": pos_loso,  # still negative delta when omitted
        "negative_directional_loso": neg_loso,
        "max_symbol_contribution_share": max_share,
        "worst_omitted_symbol": worst["omitted_symbol"],
        "best_omitted_symbol": best["omitted_symbol"],
        "severe_symbol_concentration": max_share > MAX_SYMBOL_CONTRIB,
        "check_285A_stress": _check_285a(rows, labels),
    }


def _check_285a(rows, labels) -> dict[str, Any]:
    dates = np.array([r["date"] for r in rows])
    symbols = np.array([r["symbol"] for r in rows])
    out = {}
    for d in STRESS_DAYS_285A:
        present = bool(((dates == d) & (symbols == "285A")).any())
        out[d] = "PRESENT" if present else "NOT_PRESENT"
    return out


def tod_bucket_name(epoch: float) -> str | None:
    dt = datetime.fromtimestamp(float(epoch), tz=JST)
    hm = (dt.hour, dt.minute)
    for name, (h0, m0), (h1, m1) in TOD_BUCKETS:
        a = h0 * 60 + m0
        b = h1 * 60 + m1
        cur = hm[0] * 60 + hm[1]
        if a <= cur < b:
            return name
    return None


def time_of_day_audit(
    rows: list[dict[str, Any]],
    labels: dict[str, np.ndarray],
    controls: dict[str, Any],
) -> dict[str, Any]:
    buckets: dict[str, dict[str, list]] = {
        name: {"cand300": [], "ctrl300": [], "cand600": [], "ctrl600": []}
        for name, *_ in TOD_BUCKETS
    }
    ctrl_by_cand300 = defaultdict(list)
    ctrl_by_cand600 = defaultdict(list)
    for x in controls["same_symbol_rows"][300]:
        ctrl_by_cand300[x["cand_i"]].append(x["ret"])
    for x in controls["same_symbol_rows"][600]:
        ctrl_by_cand600[x["cand_i"]].append(x["ret"])

    for i, r in enumerate(rows):
        if not labels["valid"][i]:
            continue
        name = tod_bucket_name(float(r["grid_epoch"]))
        if name is None or name not in buckets:
            continue
        if labels["return_300_valid"][i]:
            buckets[name]["cand300"].append(float(labels["return_300"][i]))
            if i in ctrl_by_cand300:
                buckets[name]["ctrl300"].append(float(np.mean(ctrl_by_cand300[i])))
        if labels["return_600_valid"][i]:
            buckets[name]["cand600"].append(float(labels["return_600"][i]))
            if i in ctrl_by_cand600:
                buckets[name]["ctrl600"].append(float(np.mean(ctrl_by_cand600[i])))

    rows_out = []
    neg_buckets = 0
    usable = 0
    for name, *_ in TOD_BUCKETS:
        b = buckets[name]
        c3 = float(np.mean(b["cand300"])) if b["cand300"] else None
        k3 = float(np.mean(b["ctrl300"])) if b["ctrl300"] else None
        c6 = float(np.mean(b["cand600"])) if b["cand600"] else None
        k6 = float(np.mean(b["ctrl600"])) if b["ctrl600"] else None
        d3 = (c3 - k3) if c3 is not None and k3 is not None else None
        d6 = (c6 - k6) if c6 is not None and k6 is not None else None
        if d3 is not None:
            usable += 1
            if d3 < 0:
                neg_buckets += 1
        rows_out.append({
            "bucket": name, "n_cand300": len(b["cand300"]),
            "cand_ret300": c3, "ctrl_ret300": k3, "delta300": d3,
            "cand_ret600": c6, "ctrl_ret600": k6, "delta600": d6,
        })
    # TIME_CONCENTRATED if only 1 bucket drives most of negative delta
    deltas = [(r["bucket"], r["delta300"]) for r in rows_out if r["delta300"] is not None and r["delta300"] < 0]
    tag = None
    if deltas:
        # if one bucket has |delta| > 2x median of others
        mags = sorted([abs(d) for _, d in deltas], reverse=True)
        if len(mags) >= 2 and mags[0] > 2.5 * (np.median(mags[1:]) + 1e-9):
            tag = "TIME_CONCENTRATED"
        elif usable > 0 and neg_buckets <= 1:
            tag = "TIME_CONCENTRATED"
    return {"buckets": rows_out, "tag": tag, "negative_buckets": neg_buckets}


def late_chase_diagnostic(
    rows: list[dict[str, Any]],
    labels: dict[str, np.ndarray],
) -> dict[str, Any]:
    """pre-entry rise vs post-entry reversal (diagnostic only)."""
    v = labels["valid"] & labels["return_300_valid"]
    pre_feats = [
        "return_30s", "return_60s", "return_180s", "return_300s",
        "drawdown_from_recent_high_bps", "rebound_from_recent_low_bps",
        "volume_delta_60s", "trading_value_delta_60s",
    ]
    post = labels["return_300"][v]
    # define pre-rise: return_180s high tercile among valid
    pre180 = np.array([
        float(r["return_180s"]) if r.get("return_180s") is not None else np.nan
        for r in rows
    ], dtype=float)
    dd = np.array([
        float(r["drawdown_from_recent_high_bps"]) if r.get("drawdown_from_recent_high_bps") is not None else np.nan
        for r in rows
    ], dtype=float)

    m = v & np.isfinite(pre180)
    if m.sum() < 100:
        return {"status": "INSUFFICIENT", "late_chase_reversal": False}

    q70 = float(np.nanquantile(pre180[m], 0.70))
    q30_dd = float(np.nanquantile(dd[m & np.isfinite(dd)], 0.30)) if np.isfinite(dd[m]).sum() > 50 else np.nan
    # near high: drawdown close to 0 (high quantile of drawdown which is typically negative)
    # drawdown_from_recent_high is usually <=0; near ceiling => drawdown near 0 => high quantile
    near_high = m & np.isfinite(dd) & (dd >= float(np.nanquantile(dd[m & np.isfinite(dd)], 0.70)))
    strong_rise = m & (pre180 >= q70)
    late = strong_rise & near_high
    late_post = float(np.mean(labels["return_300"][late & labels["return_300_valid"]])) if (late & labels["return_300_valid"]).any() else None
    other_post = float(np.mean(labels["return_300"][m & (~late) & labels["return_300_valid"]])) if (m & (~late) & labels["return_300_valid"]).any() else None

    # correlation pre180 vs post300
    x = pre180[m]
    y = labels["return_300"][m]
    if np.std(x) > 1e-9 and np.std(y) > 1e-9:
        corr = float(np.corrcoef(x, y)[0, 1])
    else:
        corr = None

    late_chase = (
        late_post is not None
        and other_post is not None
        and late_post < other_post - 3.0
        and (corr is not None and corr < -0.02)
    )
    return {
        "status": "OK",
        "pre180_q70": q70,
        "n_late_chase_proxy": int(late.sum()),
        "late_chase_ret300": late_post,
        "complement_ret300": other_post,
        "corr_pre180_post300": corr,
        "late_chase_reversal": bool(late_chase),
        "tag": "LATE_CHASE_REVERSAL" if late_chase else None,
        "features_inspected": pre_feats,
    }


def interpret_population(
    cand: dict[str, Any],
    controls: dict[str, Any],
    day_audit: dict[str, Any],
    loso: dict[str, Any],
) -> dict[str, Any]:
    c300 = cand[300]["mean"]
    c600 = cand[600]["mean"]
    k300 = controls["same_symbol_control"][300]["mean"]
    k600 = controls["same_symbol_control"][600]["mean"]
    m300 = controls["market_time_control"][300]["mean"]
    m600 = controls["market_time_control"][600]["mean"]
    d300 = (c300 - k300) if c300 is not None and k300 is not None else None
    d600 = (c600 - k600) if c600 is not None and k600 is not None else None

    case = "NO_STABLE_POPULATION_DIRECTION"
    if d300 is not None and d600 is not None:
        # P1: both cand and control similarly down, small gap
        if d300 > DELTA_P2_BPS and d600 > DELTA_P2_BPS:
            case = "MARKET_DOWNWARD_BACKGROUND"
        elif d300 <= DELTA_P2_BPS and d600 <= DELTA_P2_BPS:
            case = "CANDIDATE_GENERATOR_NEGATIVE_DIRECTIONAL_EDGE"
        else:
            case = "NO_STABLE_POPULATION_DIRECTION"

    phase_b = (
        case == "CANDIDATE_GENERATOR_NEGATIVE_DIRECTIONAL_EDGE"
        and day_audit.get("day_support_ok")
        and not loso.get("severe_symbol_concentration")
    )
    return {
        "case": case,
        "candidate_ret300": c300,
        "candidate_ret600": c600,
        "same_symbol_control_ret300": k300,
        "same_symbol_control_ret600": k600,
        "market_time_control_ret300": m300,
        "market_time_control_ret600": m600,
        "candidate_minus_control_300": d300,
        "candidate_minus_control_600": d600,
        "phase_b_eligible": bool(phase_b),
        "phase_b_reason": (
            "ok" if phase_b else
            f"case={case}; day_support={day_audit.get('day_support_ok')}; "
            f"severe_sym={loso.get('severe_symbol_concentration')}"
        ),
    }
