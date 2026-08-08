"""Failure-source analytics."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from . import VWAP_UPPER_LIMIT_BPS

LABEL = "forward_return_180s"
TOUCH = "plus5_before_minus5"


def _mean(xs: list[float]) -> Optional[float]:
    return float(np.mean(xs)) if xs else None


def _q(xs: list[float], q: float) -> Optional[float]:
    return float(np.quantile(xs, q)) if xs else None


def _cohort(rows: list[dict[str, Any]], flag: str) -> list[dict[str, Any]]:
    return [r for r in rows if r.get(flag)]


def effect_block(a2: list[dict], c0: list[dict]) -> dict[str, Any]:
    def m(rows, key):
        return _mean([float(r[key]) for r in rows if r.get(key) is not None])

    def d(key):
        va, vb = m(a2, key), m(c0, key)
        if va is None or vb is None:
            return None
        return va - vb

    return {
        "FR_30": d("forward_return_30s"),
        "FR_60": d("forward_return_60s"),
        "FR_180": d("forward_return_180s"),
        "FR_300": d("forward_return_300s"),
        "MFE_180": d("MFE_180s"),
        "MAE_180": d("MAE_180s"),
        "touch": d(TOUCH),
        "NoProgress": (
            None if m(a2, "NO_PROGRESS_300S") is None or m(c0, "NO_PROGRESS_300S") is None
            else float(sum(1 for r in a2 if r.get("NO_PROGRESS_300S")) / len(a2) if a2 else 0)
            - float(sum(1 for r in c0 if r.get("NO_PROGRESS_300S")) / len(c0) if c0 else 0)
        ),
        "a2_fr180": m(a2, LABEL),
        "c0_fr180": m(c0, LABEL),
    }


def historical_daily(hist: list[dict[str, Any]]) -> dict[str, Any]:
    days = sorted({r["date"] for r in hist})
    rows = []
    for d in days:
        day = [r for r in hist if r["date"] == d]
        c0 = _cohort(day, "in_A0")  # all C0
        a1 = _cohort(day, "in_A1")
        a2 = _cohort(day, "in_A2")
        rej = _cohort(day, "in_A2_Rejected")
        # A2 vs A1 (A1≡availability); also A2-minus-C0 metrics as required
        eff_a1 = effect_block(a2, a1)
        eff_c0 = effect_block(a2, c0)
        rows.append({
            "date": d,
            "C0_support": len(c0),
            "A1_support": len(a1),
            "A2_support": len(a2),
            "Rejected_support": len(rej),
            "A2_minus_A1_FR_180": eff_a1["FR_180"],
            "A2_minus_C0": eff_c0,
        })
    frs = [r["A2_minus_C0"]["FR_180"] for r in rows if r["A2_minus_C0"]["FR_180"] is not None]
    pos = [r for r in rows if (r["A2_minus_C0"]["FR_180"] or 0) > 0]
    neg = [r for r in rows if (r["A2_minus_C0"]["FR_180"] or 0) < 0]
    largest_pos = max(rows, key=lambda r: r["A2_minus_C0"]["FR_180"] or -1e9)
    largest_neg = min(rows, key=lambda r: r["A2_minus_C0"]["FR_180"] or 1e9)
    return {
        "daily": rows,
        "positive_effect_days": [r["date"] for r in pos],
        "negative_effect_days": [r["date"] for r in neg],
        "n_positive": len(pos),
        "n_negative": len(neg),
        "largest_positive_day": {"date": largest_pos["date"], "FR_180_delta": largest_pos["A2_minus_C0"]["FR_180"]},
        "largest_negative_day": {"date": largest_neg["date"], "FR_180_delta": largest_neg["A2_minus_C0"]["FR_180"]},
        "median_daily_effect_FR_180": float(np.median(frs)) if frs else None,
        "all_days_positive": len(neg) == 0 and len(pos) > 0,
        "has_aug3_like_reversal": any((r["A2_minus_C0"]["FR_180"] or 0) < 0 for r in rows),
    }


def vwap_distribution(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    by_day: dict[str, list] = defaultdict(list)
    for r in rows:
        if r.get("distance_from_vwap_bps") is None:
            continue
        by_day[r["date"]].append(float(r["distance_from_vwap_bps"]))
    out = []
    for d, xs in sorted(by_day.items()):
        xs = sorted(xs)
        # threshold percentile rank
        rank = float(sum(1 for x in xs if x <= VWAP_UPPER_LIMIT_BPS) / len(xs))
        n = len([r for r in rows if r["date"] == d])
        n_a2 = len([r for r in rows if r["date"] == d and r.get("in_A2")])
        n_rej = len([r for r in rows if r["date"] == d and r.get("in_A2_Rejected")])
        out.append({
            "date": d,
            "panel": label,
            "n": len(xs),
            "mean": float(np.mean(xs)),
            "median": float(np.median(xs)),
            "p10": _q(xs, 0.10),
            "p20": _q(xs, 0.20),
            "p50": _q(xs, 0.50),
            "p80": _q(xs, 0.80),
            "p90": _q(xs, 0.90),
            "p95": _q(xs, 0.95),
            "max": float(np.max(xs)),
            "threshold_percentile_rank": rank,  # fraction <= threshold ≈ empirical CDF at thr
            "A2_pass_fraction": n_a2 / n if n else None,
            "Rejected_fraction": n_rej / n if n else None,
        })
    return {"by_day": out, "panel": label}


def threshold_transport(hist_dist: dict, prosp_dist: dict) -> dict[str, Any]:
    hist_ranks = [r["threshold_percentile_rank"] for r in hist_dist["by_day"] if r.get("threshold_percentile_rank") is not None]
    prosp_ranks = [r["threshold_percentile_rank"] for r in prosp_dist["by_day"] if r.get("threshold_percentile_rank") is not None]
    hist_med = float(np.median(hist_ranks)) if hist_ranks else None
    prosp_med = float(np.median(prosp_ranks)) if prosp_ranks else None
    shift = None if hist_med is None or prosp_med is None else prosp_med - hist_med
    # large shift if |delta| > 0.15
    flag = bool(shift is not None and abs(shift) > 0.15)
    return {
        "hist_median_threshold_percentile_rank": hist_med,
        "prosp_threshold_percentile_rank": prosp_med,
        "shift": shift,
        "THRESHOLD_TRANSPORT_FAILURE": flag,
        "note": "threshold not retuned",
    }


def cohort_composition(rows: list[dict[str, Any]], flag: str = "in_A0") -> dict[str, Any]:
    sub = _cohort(rows, flag)
    am = sum(1 for r in sub if r.get("session") == "AM")
    pm = sum(1 for r in sub if r.get("session") == "PM")
    prices = [float(r["c0_price"]) for r in sub if r.get("c0_price") is not None]
    vols = [float(r["ctx_volume"]) for r in sub if r.get("ctx_volume") is not None]
    gaps = [float(r["ctx_gap_vs_pre_open"]) for r in sub if r.get("ctx_gap_vs_pre_open") is not None]
    ret_open = [float(r["ctx_return_from_session_open"]) for r in sub if r.get("ctx_return_from_session_open") is not None]
    r60 = [float(r["ctx_return_60s"]) for r in sub if r.get("ctx_return_60s") is not None]
    buckets = defaultdict(int)
    for r in sub:
        buckets[r.get("time_bucket") or "OTHER"] += 1
    return {
        "flag": flag,
        "support": len(sub),
        "symbols_n": len({r["symbol"] for r in sub}),
        "AM_n": am,
        "PM_n": pm,
        "AM_frac": am / len(sub) if sub else None,
        "price_mean": _mean(prices),
        "price_median": float(np.median(prices)) if prices else None,
        "volume_mean": _mean(vols),
        "gap_mean": _mean(gaps),
        "return_from_open_mean": _mean(ret_open),
        "return_60s_mean": _mean(r60),
        "vol_60s_mean": _mean([float(r["ctx_vol_60s"]) for r in sub if r.get("ctx_vol_60s") is not None]),
        "vol_300s_mean": _mean([float(r["ctx_vol_300s"]) for r in sub if r.get("ctx_vol_300s") is not None]),
        "dist_session_high_bps_mean": _mean([float(r["ctx_dist_from_session_high_bps"]) for r in sub if r.get("ctx_dist_from_session_high_bps") is not None]),
        "dist_session_low_bps_mean": _mean([float(r["ctx_dist_from_session_low_bps"]) for r in sub if r.get("ctx_dist_from_session_low_bps") is not None]),
        "time_bucket_counts": dict(buckets),
    }


def cohort_shift(hist: list, prosp: list) -> dict[str, Any]:
    out = {}
    for flag in ("in_A0", "in_A2", "in_A2_Rejected"):
        h = cohort_composition(hist, flag)
        p = cohort_composition(prosp, flag)
        out[flag] = {"historical": h, "prospective": p}
    # composition shift heuristics
    h0, p0 = out["in_A0"]["historical"], out["in_A0"]["prospective"]
    reasons = []
    if h0.get("AM_frac") is not None and p0.get("AM_frac") is not None:
        if abs(h0["AM_frac"] - p0["AM_frac"]) > 0.2:
            reasons.append("AM_PM_mix")
    if h0.get("return_60s_mean") is not None and p0.get("return_60s_mean") is not None:
        if (h0["return_60s_mean"] > 0) != (p0["return_60s_mean"] > 0) or abs(h0["return_60s_mean"] - p0["return_60s_mean"]) > 0.002:
            reasons.append("pre_anchor_momentum")
    if h0.get("dist_session_high_bps_mean") is not None and p0.get("dist_session_high_bps_mean") is not None:
        if abs(h0["dist_session_high_bps_mean"] - p0["dist_session_high_bps_mean"]) > 50:
            reasons.append("session_high_distance")
    return {
        "by_flag": out,
        "COHORT_COMPOSITION_SHIFT": len(reasons) > 0,
        "reasons": reasons,
    }


def common_symbol_analysis(hist: list, prosp: list) -> dict[str, Any]:
    hs = {r["symbol"] for r in hist}
    ps = {r["symbol"] for r in prosp}
    common = sorted(hs & ps)
    hist_only = sorted(hs - ps)
    prosp_only = sorted(ps - hs)
    h_c = [r for r in hist if r["symbol"] in common]
    p_c = [r for r in prosp if r["symbol"] in common]
    h_eff = effect_block(_cohort(h_c, "in_A2"), _cohort(h_c, "in_A0"))
    p_eff = effect_block(_cohort(p_c, "in_A2"), _cohort(p_c, "in_A0"))
    h_rej = effect_block(_cohort(h_c, "in_A2_Rejected"), _cohort(h_c, "in_A2"))
    p_rej = effect_block(_cohort(p_c, "in_A2_Rejected"), _cohort(p_c, "in_A2"))
    # overall prosp effect
    p_all = effect_block(_cohort(prosp, "in_A2"), _cohort(prosp, "in_A0"))
    within_rev = (p_eff.get("FR_180") or 0) < 0
    overall_rev = (p_all.get("FR_180") or 0) < 0
    hist_pos = (h_eff.get("FR_180") or 0) > 0
    symbol_mix = bool(hist_pos and within_rev is False and overall_rev)
    within = bool(hist_pos and within_rev)
    # if common also reverses
    if within_rev and overall_rev:
        within = True
        symbol_mix = False
    return {
        "common_symbols": common,
        "common_n": len(common),
        "historical_only_symbols": hist_only,
        "prospective_only_symbols": prosp_only,
        "common_historical_A2_vs_C0": h_eff,
        "common_prospective_A2_vs_C0": p_eff,
        "common_historical_Rejected_vs_A2": h_rej,
        "common_prospective_Rejected_vs_A2": p_rej,
        "SYMBOL_MIX_SHIFT": symbol_mix,
        "WITHIN_SYMBOL_REGIME_REVERSAL": within,
    }


def time_of_day(hist: list, prosp: list) -> dict[str, Any]:
    def bucket_metrics(rows: list, bucket: str) -> dict[str, Any]:
        sub = [r for r in rows if r.get("time_bucket") == bucket]
        a2 = _cohort(sub, "in_A2")
        rej = _cohort(sub, "in_A2_Rejected")
        c0 = _cohort(sub, "in_A0")
        return {
            "bucket": bucket,
            "C0_support": len(c0),
            "A2_support": len(a2),
            "Rejected_support": len(rej),
            "A2_FR": _mean([float(r[LABEL]) for r in a2 if r.get(LABEL) is not None]),
            "A2_MFE": _mean([float(r["MFE_180s"]) for r in a2 if r.get("MFE_180s") is not None]),
            "A2_MAE": _mean([float(r["MAE_180s"]) for r in a2 if r.get("MAE_180s") is not None]),
            "A2_touch": _mean([float(r[TOUCH]) for r in a2 if r.get(TOUCH) is not None]),
            "A2_NoProgress": float(sum(1 for r in a2 if r.get("NO_PROGRESS_300S")) / len(a2)) if a2 else None,
            "A2_minus_C0_FR": effect_block(a2, c0).get("FR_180"),
            "Rejected_minus_A2_FR": effect_block(rej, a2).get("FR_180"),
        }

    buckets = ("AM_OPEN", "AM_MID", "PM_OPEN", "PM_MID")
    hist_b = {b: bucket_metrics(hist, b) for b in buckets}
    prosp_b = {b: bucket_metrics(prosp, b) for b in buckets}
    # interaction: hist positive effect in bucket, prosp negative
    interaction = False
    for b in buckets:
        he = hist_b[b].get("A2_minus_C0_FR")
        pe = prosp_b[b].get("A2_minus_C0_FR")
        if he is not None and pe is not None and he > 0 and pe < 0:
            interaction = True
        if he is not None and pe is not None and he < 0 and pe > 0:
            interaction = True
    return {
        "historical": hist_b,
        "prospective": prosp_b,
        "TIME_OF_DAY_INTERACTION": interaction,
        "note": "fixed buckets only — not a candidate gate",
    }


def market_state(hist: list, prosp: list) -> dict[str, Any]:
    def summarize(rows, flag):
        sub = _cohort(rows, flag)
        return {
            "advancing_frac": _mean([float(r["ctx_advancing_frac"]) for r in sub if r.get("ctx_advancing_frac") is not None]),
            "declining_frac": _mean([float(r["ctx_declining_frac"]) for r in sub if r.get("ctx_declining_frac") is not None]),
            "univ_med_ret_60s": _mean([float(r["ctx_univ_median_return_60s"]) for r in sub if r.get("ctx_univ_median_return_60s") is not None]),
            "univ_med_ret_180s": _mean([float(r["ctx_univ_median_return_180s"]) for r in sub if r.get("ctx_univ_median_return_180s") is not None]),
            "univ_med_ret_300s": _mean([float(r["ctx_univ_median_return_300s"]) for r in sub if r.get("ctx_univ_median_return_300s") is not None]),
            "return_dispersion": _mean([float(r["ctx_return_dispersion_60s"]) for r in sub if r.get("ctx_return_dispersion_60s") is not None]),
            "volume_dispersion": _mean([float(r["ctx_volume_dispersion"]) for r in sub if r.get("ctx_volume_dispersion") is not None]),
            "own_return_60s": _mean([float(r["ctx_return_60s"]) for r in sub if r.get("ctx_return_60s") is not None]),
        }

    h_a2, h_rej = summarize(hist, "in_A2"), summarize(hist, "in_A2_Rejected")
    p_a2, p_rej = summarize(prosp, "in_A2"), summarize(prosp, "in_A2_Rejected")
    # High VWAP (rejected) leading market on prosp?
    # If rejected own_return >> univ and positive while A2 weak
    regime = False
    if (p_rej.get("own_return_60s") or 0) > (p_a2.get("own_return_60s") or 0) + 0.001:
        if (p_rej.get("univ_med_ret_60s") or 0) > 0 and (p_a2.get("own_return_60s") or 0) < (p_rej.get("own_return_60s") or 0):
            regime = True
    if (h_a2.get("univ_med_ret_60s") or 0) * (p_a2.get("univ_med_ret_60s") or 0) < 0:
        regime = True
    return {
        "historical_A2": h_a2,
        "historical_Rejected": h_rej,
        "prospective_A2": p_a2,
        "prospective_Rejected": p_rej,
        "MARKET_REGIME_INTERACTION": regime,
        "interpretation": {
            "prosp_rejected_stronger_pre_anchor_momentum": (p_rej.get("own_return_60s") or 0) > (p_a2.get("own_return_60s") or 0),
            "prosp_a2_weaker_than_univ": (
                p_a2.get("own_return_60s") is not None and p_a2.get("univ_med_ret_60s") is not None
                and p_a2["own_return_60s"] < p_a2["univ_med_ret_60s"]
            ),
        },
        "asof_only": True,
        "no_future_regime_feature": True,
    }


def gap_trend(hist: list, prosp: list) -> dict[str, Any]:
    def block(rows, flag):
        sub = _cohort(rows, flag)
        return {
            "gap_mean": _mean([float(r["ctx_gap_vs_pre_open"]) for r in sub if r.get("ctx_gap_vs_pre_open") is not None]),
            "session_open_return": _mean([float(r["ctx_return_from_session_open"]) for r in sub if r.get("ctx_return_from_session_open") is not None]),
            "return_60s": _mean([float(r["ctx_return_60s"]) for r in sub if r.get("ctx_return_60s") is not None]),
            "return_180s": _mean([float(r["ctx_return_180s"]) for r in sub if r.get("ctx_return_180s") is not None]),
            "return_300s": _mean([float(r["ctx_return_300s"]) for r in sub if r.get("ctx_return_300s") is not None]),
            "dist_high_bps": _mean([float(r["ctx_dist_from_session_high_bps"]) for r in sub if r.get("ctx_dist_from_session_high_bps") is not None]),
            "rebound_bps": _mean([float(r["ctx_rebound_bps"]) for r in sub if r.get("ctx_rebound_bps") is not None]),
            "range_width_bps": _mean([float(r["ctx_range_width_bps"]) for r in sub if r.get("ctx_range_width_bps") is not None]),
            "vol_60s": _mean([float(r["ctx_vol_60s"]) for r in sub if r.get("ctx_vol_60s") is not None]),
        }

    out = {
        "historical_A2": block(hist, "in_A2"),
        "historical_Rejected": block(hist, "in_A2_Rejected"),
        "prospective_A2": block(prosp, "in_A2"),
        "prospective_Rejected": block(prosp, "in_A2_Rejected"),
    }
    # late chase hist: rejected closer to high / stronger recent up move but worse forward
    # trend continuation prosp: rejected stronger momentum and better forward
    p_rej_r = out["prospective_Rejected"].get("return_60s") or 0
    p_a2_r = out["prospective_A2"].get("return_60s") or 0
    h_rej_r = out["historical_Rejected"].get("return_60s") or 0
    h_a2_r = out["historical_A2"].get("return_60s") or 0
    interaction = bool(
        (h_rej_r > h_a2_r)  # rejected had more chase historically
        and (p_rej_r > p_a2_r)  # also on prosp
    )
    # stronger: prosp rejected also near highs with positive continuation signal
    if (out["prospective_Rejected"].get("dist_high_bps") or 0) > (out["prospective_A2"].get("dist_high_bps") or -1e9):
        # rejected closer to high (less negative dist) → continuation
        if p_rej_r > p_a2_r:
            interaction = True
    return {
        **out,
        "LATE_CHASE_VS_TREND_CONTINUATION_INTERACTION": interaction,
        "note": "no new regime gate created",
    }


def no_progress_decomposition(prosp: list) -> dict[str, Any]:
    a2 = _cohort(prosp, "in_A2")
    c0 = _cohort(prosp, "in_A0")

    def classify(rows):
        n = len(rows) or 1
        np_rows = [r for r in rows if r.get("NO_PROGRESS_300S")]
        adverse = [r for r in rows if r.get(LABEL) is not None and float(r[LABEL]) < -0.0005]
        favor = [r for r in rows if r.get(LABEL) is not None and float(r[LABEL]) > 0.0005]
        twosided = []
        for r in rows:
            if r.get("MFE_180s") is None or r.get("MAE_180s") is None:
                continue
            if float(r["MFE_180s"]) > 0.0005 and float(r["MAE_180s"]) < -0.0005:
                twosided.append(r)
        # NoProgress that are adverse directional at 180s (shouldn't be many if NP defined on 300s)
        np_adverse = [r for r in np_rows if r.get(LABEL) is not None and float(r[LABEL]) < 0]
        np_favor = [r for r in np_rows if r.get(LABEL) is not None and float(r[LABEL]) > 0]
        return {
            "n": len(rows),
            "no_progress_n": len(np_rows),
            "no_progress_rate": len(np_rows) / n,
            "adverse_directional_n": len(adverse),
            "adverse_directional_rate": len(adverse) / n,
            "favorable_directional_n": len(favor),
            "favorable_directional_rate": len(favor) / n,
            "high_vol_two_sided_n": len(twosided),
            "high_vol_two_sided_rate": len(twosided) / n,
            "no_progress_with_negative_FR180_n": len(np_adverse),
            "no_progress_with_positive_FR180_n": len(np_favor),
            "mean_FR180": _mean([float(r[LABEL]) for r in rows if r.get(LABEL) is not None]),
            "mean_MAE180": _mean([float(r["MAE_180s"]) for r in rows if r.get("MAE_180s") is not None]),
        }

    ca2, cc0 = classify(a2), classify(c0)
    # NP improved but FR/MAE worse → adverse direction interpretation
    np_improved = (ca2["no_progress_rate"] or 1) < (cc0["no_progress_rate"] or 0)
    fr_worse = (ca2["mean_FR180"] or 0) < (cc0["mean_FR180"] or 0)
    mae_worse = (ca2["mean_MAE180"] or 0) < (cc0["mean_MAE180"] or 0)
    flag = bool(np_improved and (fr_worse or mae_worse))
    return {
        "A2": ca2,
        "C0": cc0,
        "NO_PROGRESS_IMPROVEMENT_ADVERSE_DIRECTION": flag,
        "note": "NoProgress is not used as sole ENTRY quality gate",
    }


def classify_failures(
    parity: dict,
    transport: dict,
    cohort: dict,
    common: dict,
    tod: dict,
    market: dict,
    gap: dict,
    npdec: dict,
    hist_daily: dict,
) -> dict[str, Any]:
    tags = []
    if not parity.get("ok"):
        tags.append("CONSTRUCTION_MISMATCH")
    if transport.get("THRESHOLD_TRANSPORT_FAILURE"):
        tags.append("THRESHOLD_TRANSPORT_FAILURE")
    if cohort.get("COHORT_COMPOSITION_SHIFT"):
        tags.append("COHORT_COMPOSITION_SHIFT")
    if common.get("SYMBOL_MIX_SHIFT"):
        tags.append("SYMBOL_MIX_SHIFT")
    if common.get("WITHIN_SYMBOL_REGIME_REVERSAL"):
        tags.append("WITHIN_SYMBOL_REGIME_REVERSAL")
    if tod.get("TIME_OF_DAY_INTERACTION"):
        tags.append("TIME_OF_DAY_INTERACTION")
    if market.get("MARKET_REGIME_INTERACTION"):
        tags.append("MARKET_REGIME_INTERACTION")
    if gap.get("LATE_CHASE_VS_TREND_CONTINUATION_INTERACTION"):
        tags.append("LATE_CHASE_VS_TREND_CONTINUATION_INTERACTION")
    if npdec.get("NO_PROGRESS_IMPROVEMENT_ADVERSE_DIRECTION"):
        tags.append("NO_PROGRESS_IMPROVEMENT_ADVERSE_DIRECTION")

    # primary selection priority
    priority = [
        "CONSTRUCTION_MISMATCH",
        "WITHIN_SYMBOL_REGIME_REVERSAL",
        "LATE_CHASE_VS_TREND_CONTINUATION_INTERACTION",
        "MARKET_REGIME_INTERACTION",
        "THRESHOLD_TRANSPORT_FAILURE",
        "COHORT_COMPOSITION_SHIFT",
        "SYMBOL_MIX_SHIFT",
        "TIME_OF_DAY_INTERACTION",
        "NO_PROGRESS_IMPROVEMENT_ADVERSE_DIRECTION",
    ]
    primary = None
    for p in priority:
        if p in tags:
            primary = p
            break
    if primary is None:
        primary = "NO_STABLE_VWAP_REJECT_MECHANISM"
        tags.append(primary)
    elif common.get("WITHIN_SYMBOL_REGIME_REVERSAL") and "NO_STABLE_VWAP_REJECT_MECHANISM" not in tags:
        # within-symbol reversal implies no stable mechanism
        tags.append("NO_STABLE_VWAP_REJECT_MECHANISM")

    secondary = [t for t in tags if t != primary]

    # verdict
    if "CONSTRUCTION_MISMATCH" in tags:
        from . import VERDICT_MISMATCH
        verdict = VERDICT_MISMATCH
    elif primary in (
        "MARKET_REGIME_INTERACTION",
        "LATE_CHASE_VS_TREND_CONTINUATION_INTERACTION",
        "TIME_OF_DAY_INTERACTION",
    ) and not common.get("WITHIN_SYMBOL_REGIME_REVERSAL"):
        from . import VERDICT_REGIME
        verdict = VERDICT_REGIME
    else:
        from . import VERDICT_NO_STABLE
        verdict = VERDICT_NO_STABLE

    return {
        "tags": tags,
        "primary_failure_source": primary,
        "secondary_failure_sources": secondary,
        "verdict": verdict,
        "hist_had_reversal_days": hist_daily.get("has_aug3_like_reversal"),
        "hist_positive_days": hist_daily.get("positive_effect_days"),
        "hist_negative_days": hist_daily.get("negative_effect_days"),
    }
