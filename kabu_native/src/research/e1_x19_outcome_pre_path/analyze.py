"""Outcome class assignment + strata + discrimination."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from . import (
    ALL_FEATURES,
    CONFIRMATION,
    DISCOVERY,
    GATE_DAYS,
    GATE_MAX_DAY,
    GATE_MAX_SYM,
    GATE_SUPPORT,
    MATCHED_MIN_PER_CLASS,
    MECHANISM_MAP,
    NO_PROGRESS_BPS,
    STRESS_DAY,
    TWO_SIDED_BPS,
)


def assign_class(r: dict[str, Any]) -> str:
    mfe = r.get("MFE_300s")
    fr = r.get("forward_return_300s")
    mae = r.get("MAE_300s")
    p10 = r.get("plus10_before_minus10")

    if mfe is not None and fr is not None:
        if float(mfe) < NO_PROGRESS_BPS and abs(float(fr)) < NO_PROGRESS_BPS:
            return "NOPROGRESS"

    # WINNER: +10 before -10
    if p10 is not None and float(p10) == 1.0:
        return "WINNER"
    # STOP: -10 before +10 (encoded as plus10_before_minus10 == 0)
    if p10 is not None and float(p10) == 0.0:
        return "STOP"

    if mfe is not None and mae is not None:
        if float(mfe) >= TWO_SIDED_BPS and abs(float(mae)) >= TWO_SIDED_BPS:
            return "TWO_SIDED_VOLATILE"

    return "UNCLASSIFIED"


def classify_population(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        m = dict(r)
        m["outcome_class"] = assign_class(r)
        out.append(m)
    return out


def _mean(xs: list[float]) -> Optional[float]:
    return float(np.mean(xs)) if xs else None


def _std(xs: list[float]) -> Optional[float]:
    return float(np.std(xs, ddof=1)) if len(xs) >= 2 else None


def design_terciles(discovery_rows: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    """Fixed tercile cuts from Discovery only."""
    cuts = {}
    for key, field in (
        ("advancing_frac", "advancing_symbol_fraction"),
        ("cs_dispersion", "cs_return_dispersion_60s"),
        ("pre_anchor_vol", "range_width_180s"),
    ):
        xs = [float(r[field]) for r in discovery_rows if r.get(field) is not None]
        if len(xs) < 30:
            cuts[key] = (0.0, 0.0)
            continue
        q33, q66 = float(np.quantile(xs, 1 / 3)), float(np.quantile(xs, 2 / 3))
        cuts[key] = (q33, q66)
    return cuts


def assign_strata(rows: list[dict[str, Any]], cuts: dict[str, tuple[float, float]]) -> list[dict[str, Any]]:
    def terc(val, cut):
        if val is None or cut == (0.0, 0.0):
            return "UNK"
        lo, hi = cut
        if val <= lo:
            return "low"
        if val <= hi:
            return "middle"
        return "high"

    out = []
    for r in rows:
        m = dict(r)
        # gap positive / non-positive: use return_300s as pre-anchor path proxy for gap-like momentum
        r300 = r.get("return_300s")
        m["gap_strata"] = "positive" if (r300 is not None and float(r300) > 0) else "non_positive"
        m["advancing_tercile"] = terc(
            float(r["advancing_symbol_fraction"]) if r.get("advancing_symbol_fraction") is not None else None,
            cuts.get("advancing_frac", (0.0, 0.0)),
        )
        m["dispersion_tercile"] = terc(
            float(r["cs_return_dispersion_60s"]) if r.get("cs_return_dispersion_60s") is not None else None,
            cuts.get("cs_dispersion", (0.0, 0.0)),
        )
        m["vol_tercile"] = terc(
            float(r["range_width_180s"]) if r.get("range_width_180s") is not None else None,
            cuts.get("pre_anchor_vol", (0.0, 0.0)),
        )
        out.append(m)
    return out


def class_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    c = defaultdict(int)
    for r in rows:
        c[r["outcome_class"]] += 1
    return dict(c)


def class_profile(rows: list[dict[str, Any]], feature: str) -> dict[str, Any]:
    by = defaultdict(list)
    for r in rows:
        v = r.get(feature)
        if v is None:
            continue
        # market features require evaluable
        if feature in (
            "advancing_symbol_fraction", "declining_symbol_fraction",
            "universe_median_return_60s", "universe_median_return_180s", "universe_median_return_300s",
            "symbol_minus_median_return_60s", "symbol_minus_median_return_180s", "cs_return_dispersion_60s",
        ):
            if not r.get("market_state_evaluable"):
                continue
        by[r["outcome_class"]].append(float(v))
    out = {}
    for cls, xs in by.items():
        out[cls] = {
            "n": len(xs),
            "mean": _mean(xs),
            "median": float(np.median(xs)) if xs else None,
            "q20": float(np.quantile(xs, 0.2)) if xs else None,
            "q80": float(np.quantile(xs, 0.8)) if xs else None,
        }
    return out


def std_diff(a: list[float], b: list[float]) -> Optional[float]:
    if len(a) < 5 or len(b) < 5:
        return None
    sa, sb = _std(a), _std(b)
    pool = None
    if sa is not None and sb is not None:
        pool = np.sqrt(0.5 * (sa ** 2 + sb ** 2))
    if pool is None or pool < 1e-15:
        return None
    return float((np.mean(a) - np.mean(b)) / pool)


def point_biserial(xs: list[float], ys: list[int]) -> Optional[float]:
    if len(xs) < 20 or len(set(ys)) < 2:
        return None
    return float(np.corrcoef(xs, ys)[0, 1])


def spearman(xs: list[float], ys: list[float]) -> Optional[float]:
    if len(xs) < 20:
        return None
    rx = np.argsort(np.argsort(xs)).astype(float)
    ry = np.argsort(np.argsort(ys)).astype(float)
    return float(np.corrcoef(rx, ry)[0, 1])


def feature_values(rows: list[dict[str, Any]], feature: str, cls: str) -> list[float]:
    out = []
    for r in rows:
        if r.get("outcome_class") != cls:
            continue
        v = r.get(feature)
        if v is None:
            continue
        if feature.startswith("universe_") or feature.startswith("symbol_minus") or feature in (
            "advancing_symbol_fraction", "declining_symbol_fraction", "cs_return_dispersion_60s",
        ):
            if not r.get("market_state_evaluable"):
                continue
        out.append(float(v))
    return out


def discovery_direction(disc: list[dict[str, Any]], feature: str) -> Optional[str]:
    """Direction: higher feature → more WINNER vs STOP. Discovery only."""
    w = feature_values(disc, feature, "WINNER")
    s = feature_values(disc, feature, "STOP")
    d = std_diff(w, s)
    if d is None:
        return None
    if d > 0:
        return "higher_favors_WINNER"
    if d < 0:
        return "higher_favors_STOP"
    return "neutral"


def matched_parent_diff(rows: list[dict[str, Any]], feature: str) -> dict[str, Any]:
    """Parent groups: date × session × time_bucket × price_band × vol_tercile."""
    groups = defaultdict(lambda: defaultdict(list))
    excluded = 0
    used = 0
    for r in rows:
        key = (
            r.get("date"), r.get("session"), r.get("time_bucket"),
            r.get("price_band"), r.get("vol_tercile"),
        )
        v = r.get(feature)
        if v is None:
            continue
        groups[key][r["outcome_class"]].append(float(v))

    diffs_ws, diffs_wn, diffs_sn, diffs_wt = [], [], [], []
    for key, byc in groups.items():
        if len(byc.get("WINNER", [])) < MATCHED_MIN_PER_CLASS or len(byc.get("STOP", [])) < MATCHED_MIN_PER_CLASS:
            excluded += 1
            continue
        used += 1
        diffs_ws.append(float(np.mean(byc["WINNER"]) - np.mean(byc["STOP"])))
        if len(byc.get("NOPROGRESS", [])) >= MATCHED_MIN_PER_CLASS:
            diffs_wn.append(float(np.mean(byc["WINNER"]) - np.mean(byc["NOPROGRESS"])))
            diffs_sn.append(float(np.mean(byc["STOP"]) - np.mean(byc["NOPROGRESS"])))
        if len(byc.get("TWO_SIDED_VOLATILE", [])) >= MATCHED_MIN_PER_CLASS:
            diffs_wt.append(float(np.mean(byc["WINNER"]) - np.mean(byc["TWO_SIDED_VOLATILE"])))

    return {
        "groups_used": used,
        "groups_excluded_low_support": excluded,
        "mean_WINNER_minus_STOP": _mean(diffs_ws),
        "mean_WINNER_minus_NOPROGRESS": _mean(diffs_wn),
        "mean_STOP_minus_NOPROGRESS": _mean(diffs_sn),
        "mean_WINNER_minus_TWO_SIDED": _mean(diffs_wt),
    }


def day_balanced_effect(rows: list[dict[str, Any]], feature: str) -> Optional[float]:
    by_day = defaultdict(lambda: {"W": [], "S": []})
    for r in rows:
        v = r.get(feature)
        if v is None:
            continue
        if r["outcome_class"] == "WINNER":
            by_day[r["date"]]["W"].append(float(v))
        elif r["outcome_class"] == "STOP":
            by_day[r["date"]]["S"].append(float(v))
    diffs = []
    for d, g in by_day.items():
        if len(g["W"]) >= 5 and len(g["S"]) >= 5:
            diffs.append(float(np.mean(g["W"]) - np.mean(g["S"])))
    return _mean(diffs)


def symbol_balanced_effect(rows: list[dict[str, Any]], feature: str) -> Optional[float]:
    by_sym = defaultdict(lambda: {"W": [], "S": []})
    for r in rows:
        v = r.get(feature)
        if v is None:
            continue
        if r["outcome_class"] == "WINNER":
            by_sym[r["symbol"]]["W"].append(float(v))
        elif r["outcome_class"] == "STOP":
            by_sym[r["symbol"]]["S"].append(float(v))
    diffs = []
    for s, g in by_sym.items():
        if len(g["W"]) >= 3 and len(g["S"]) >= 3:
            diffs.append(float(np.mean(g["W"]) - np.mean(g["S"])))
    return _mean(diffs)


def lodo_flip(rows: list[dict[str, Any]], feature: str, base_dir: str) -> bool:
    days = sorted({r["date"] for r in rows})
    flips = 0
    for leave in days:
        sub = [r for r in rows if r["date"] != leave]
        w = feature_values(sub, feature, "WINNER")
        s = feature_values(sub, feature, "STOP")
        d = std_diff(w, s)
        if d is None:
            continue
        dir_ = "higher_favors_WINNER" if d > 0 else "higher_favors_STOP"
        if base_dir and dir_ != base_dir and abs(d) > 0.05:
            flips += 1
    return flips >= 2


def contribution_caps(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows) or 1
    by_day = defaultdict(int)
    by_sym = defaultdict(int)
    for r in rows:
        by_day[r["date"]] += 1
        by_sym[r["symbol"]] += 1
    max_day = max(by_day.values()) / n if by_day else 0
    max_sym = max(by_sym.values()) / n if by_sym else 0
    return {
        "max_day_frac": max_day,
        "max_sym_frac": max_sym,
        "max_day": max(by_day, key=by_day.get) if by_day else None,
        "max_sym": max(by_sym, key=by_sym.get) if by_sym else None,
        "pass_day": max_day <= GATE_MAX_DAY,
        "pass_sym": max_sym <= GATE_MAX_SYM,
    }


def analyze_feature(rows: list[dict[str, Any]], feature: str, disc_dir: Optional[str]) -> dict[str, Any]:
    disc = [r for r in rows if r["date"] in DISCOVERY]
    conf = [r for r in rows if r["date"] in CONFIRMATION]
    stress = [r for r in rows if r["date"] == STRESS_DAY]

    def effect(sub):
        w, s = feature_values(sub, feature, "WINNER"), feature_values(sub, feature, "STOP")
        return std_diff(w, s)

    e_d, e_c, e_s = effect(disc), effect(conf), effect(stress)
    dir_d = disc_dir or discovery_direction(disc, feature)

    def same_dir(e):
        if e is None or dir_d is None or dir_d == "neutral":
            return None
        if dir_d == "higher_favors_WINNER":
            return e > 0
        return e < 0

    # association on discovery WINNER vs STOP
    xs, ys = [], []
    for r in disc:
        if r["outcome_class"] not in ("WINNER", "STOP"):
            continue
        v = r.get(feature)
        if v is None:
            continue
        xs.append(float(v))
        ys.append(1 if r["outcome_class"] == "WINNER" else 0)

    matched = matched_parent_diff(disc + conf, feature)  # discovery+confirmation for matched; stress diagnostic
    matched_stress = matched_parent_diff(stress, feature)

    w_all = feature_values(disc + conf, feature, "WINNER")
    s_all = feature_values(disc + conf, feature, "STOP")
    support = len(w_all) + len(s_all)
    days = len({r["date"] for r in disc + conf if r["outcome_class"] in ("WINNER", "STOP")})

    caps = contribution_caps([r for r in disc + conf if r["outcome_class"] in ("WINNER", "STOP")])

    conf_ok = same_dir(e_c) is True
    stress_ok = same_dir(e_s) is not False  # None ok; False = major reversal
    if e_s is not None and dir_d and same_dir(e_s) is False and abs(e_s) > 0.15:
        stress_ok = False
    matched_ok = False
    md = matched.get("mean_WINNER_minus_STOP")
    if md is not None and dir_d:
        matched_ok = (md > 0 and dir_d == "higher_favors_WINNER") or (md < 0 and dir_d == "higher_favors_STOP")

    lodo_bad = lodo_flip(disc + conf, feature, dir_d or "")

    gate_pass = (
        support >= GATE_SUPPORT
        and days >= GATE_DAYS
        and conf_ok
        and stress_ok
        and matched_ok
        and not lodo_bad
        and caps["pass_day"]
        and caps["pass_sym"]
    )

    return {
        "feature": feature,
        "discovery_direction": dir_d,
        "profiles": class_profile(disc + conf, feature),
        "std_diff_discovery": e_d,
        "std_diff_confirmation": e_c,
        "std_diff_stress_20260803": e_s,
        "confirmation_same_direction": conf_ok,
        "stress_no_major_reversal": stress_ok,
        "point_biserial_discovery": point_biserial(xs, ys) if xs else None,
        "day_balanced_effect": day_balanced_effect(disc + conf, feature),
        "symbol_balanced_effect": symbol_balanced_effect(disc + conf, feature),
        "matched": matched,
        "matched_stress": matched_stress,
        "matched_direction_ok": matched_ok,
        "lodo_major_flip": lodo_bad,
        "support_winner_stop": support,
        "entry_days": days,
        "contribution": caps,
        "stability_gate_pass": gate_pass,
    }


def mechanism_dedup(feature_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Max one representative per mechanism among gate-passers; else best by |std_diff|."""
    reps = {}
    duplicates = []
    for mech, feats in MECHANISM_MAP.items():
        if not feats:
            continue
        cands = []
        for f in feats:
            fr = feature_results.get(f)
            if not fr:
                continue
            cands.append(fr)
        if not cands:
            continue
        # prefer gate pass
        passed = [c for c in cands if c.get("stability_gate_pass")]
        pool = passed or cands
        best = max(pool, key=lambda c: abs(c.get("std_diff_discovery") or 0))
        reps[mech] = best["feature"] if best.get("stability_gate_pass") or passed else (
            best["feature"] if best.get("stability_gate_pass") else None
        )
        # if multiple pass, others are duplicates
        for c in passed:
            if c["feature"] != reps.get(mech):
                duplicates.append({"mechanism": mech, "feature": c["feature"], "reason": "same_mechanism"})
        # correlation among mechanism features
        # store ranking overlap lightly
    # fix: only assign if gate pass
    final = {}
    for mech, feats in MECHANISM_MAP.items():
        if not feats:
            continue
        passed = [feature_results[f] for f in feats if f in feature_results and feature_results[f].get("stability_gate_pass")]
        if not passed:
            final[mech] = {"representative": None, "stable": False}
            continue
        best = max(passed, key=lambda c: abs(c.get("std_diff_discovery") or 0))
        final[mech] = {"representative": best["feature"], "stable": True, "detail": best}
        for c in passed:
            if c["feature"] != best["feature"]:
                duplicates.append({"mechanism": mech, "dropped": c["feature"], "kept": best["feature"]})
    return {"by_mechanism": final, "duplicates_dropped": duplicates}


def key_answers(rows: list[dict[str, Any]], feature_results: dict[str, dict]) -> dict[str, Any]:
    disc_conf = [r for r in rows if r["date"] != STRESS_DAY]
    stress = [r for r in rows if r["date"] == STRESS_DAY]

    def cls_mean(sub, cls, feat):
        xs = feature_values(sub, feat, cls)
        return _mean(xs)

    w_ret = cls_mean(disc_conf, "WINNER", "return_60s")
    s_ret = cls_mean(disc_conf, "STOP", "return_60s")
    w_dd = cls_mean(disc_conf, "WINNER", "drawdown_from_recent_high_bps")
    w_reb = cls_mean(disc_conf, "WINNER", "rebound_from_recent_low_bps")
    w_hi = cls_mean(disc_conf, "WINNER", "distance_from_session_high_bps")
    w_lo = cls_mean(disc_conf, "WINNER", "distance_from_session_low_bps")
    w_vol = cls_mean(disc_conf, "WINNER", "volume_rate_60s")
    n_vol = cls_mean(disc_conf, "NOPROGRESS", "volume_rate_60s")
    s_hi = cls_mean(disc_conf, "STOP", "distance_from_session_high_bps")
    s_adv = cls_mean(disc_conf, "STOP", "advancing_symbol_fraction")
    w_tw = feature_results.get("range_width_180s", {})

    stress_dirs = {
        f: feature_results[f].get("stress_no_major_reversal")
        for f in feature_results
    }

    return {
        "Winner_rising_pre_anchor": bool(w_ret is not None and s_ret is not None and w_ret > s_ret),
        "Winner_pullback_then_recover": bool(w_dd is not None and w_reb is not None and w_dd < 0 and w_reb > 0),
        "Winner_near_high_vs_low": (
            "near_high" if (w_hi is not None and w_lo is not None and abs(w_hi) < abs(w_lo)) else
            "near_low" if (w_hi is not None and w_lo is not None) else "unknown"
        ),
        "Winner_volume_expansion": bool(w_vol is not None and n_vol is not None and w_vol > n_vol),
        "STOP_high_chase": bool(s_hi is not None and w_hi is not None and abs(s_hi) < abs(w_hi)),  # closer to high
        "STOP_weak_market_bounce": bool(s_adv is not None and s_adv < 0.5),
        "NoProgress_low_activity": bool(n_vol is not None and w_vol is not None and n_vol < w_vol),
        "TwoSided_vs_Winner_pre_distinguishable": bool(
            (w_tw.get("std_diff_discovery") is not None)
            and any(
                feature_results[f].get("stability_gate_pass")
                for f in ("range_width_180s", "cs_return_dispersion_60s", "volume_rate_60s")
                if f in feature_results
            )
        ),
        "stress_20260803_same_direction_features": sum(1 for v in stress_dirs.values() if v is True),
        "stress_20260803_reversed_features": sum(1 for v in stress_dirs.values() if v is False),
        "means": {
            "winner_return_60s": w_ret, "stop_return_60s": s_ret,
            "winner_drawdown": w_dd, "winner_rebound": w_reb,
            "winner_dist_high": w_hi, "winner_dist_low": w_lo,
            "winner_volume_rate": w_vol, "noprogress_volume_rate": n_vol,
            "stop_dist_high": s_hi, "stop_advancing_frac": s_adv,
        },
    }
