"""Discovery-only family margin scores and primary/secondary routing."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from . import DISCOVERY, PATH_FAMILIES


def _nanmedian(x: np.ndarray) -> Optional[float]:
    x = x[np.isfinite(x)]
    if x.size == 0:
        return None
    return float(np.median(x))


def _rate(m: np.ndarray) -> Optional[float]:
    if m.size == 0:
        return None
    return float(np.mean(m.astype(np.float64)))


def discovery_mask_features(
    *,
    selected: np.ndarray,
    metrics: dict[str, np.ndarray],
    dates: np.ndarray,
    path_ok: np.ndarray,
) -> dict[str, Any]:
    """Per-mask Discovery path features for margin + calibration (no Evaluation)."""
    disc = path_ok & np.isin(dates, list(DISCOVERY))
    base = disc
    sel = base & selected
    all_m = base

    def reach_delta(up: int) -> Optional[float]:
        reached = metrics[f"up_{up}_reached"]
        rs = _rate(reached[sel]) if sel.any() else None
        ra = _rate(reached[all_m]) if all_m.any() else None
        if rs is None or ra is None:
            return None
        return (rs - ra) * 100.0

    def within_rate(up: int, h: int, mask: np.ndarray) -> Optional[float]:
        reached = metrics[f"up_{up}_reached"]
        t = metrics[f"up_{up}_time_sec"]
        within = reached & np.isfinite(t) & (t <= h)
        return _rate(within[mask]) if mask.any() else None

    def horizon_mean(key: str, mask: np.ndarray) -> Optional[float]:
        elig = mask & metrics[f"eligible_{key}"] & metrics[f"fresh_ok_{key}"]
        v = metrics[f"return_{key}_bps"][elig]
        v = v[np.isfinite(v)]
        return float(np.mean(v)) if v.size else None

    def horizon_mfe(key: str, mask: np.ndarray) -> Optional[float]:
        elig = mask & metrics[f"eligible_{key}"] & metrics[f"fresh_ok_{key}"]
        v = metrics[f"MFE_{key}_bps"][elig]
        v = v[np.isfinite(v)]
        return float(np.mean(v)) if v.size else None

    def giveback_med(key: str, mask: np.ndarray) -> Optional[float]:
        elig = mask & metrics[f"eligible_{key}"] & metrics[f"fresh_ok_{key}"]
        v = metrics[f"terminal_giveback_from_MFE_{key}_bps"][elig]
        return _nanmedian(v)

    def max_gb_med(key: str, mask: np.ndarray) -> Optional[float]:
        elig = mask & metrics[f"eligible_{key}"] & metrics[f"fresh_ok_{key}"]
        v = metrics[f"max_giveback_after_MFE_{key}_bps"][elig]
        return _nanmedian(v)

    r30_time = metrics["up_30_time_sec"][sel & metrics["up_30_reached"]]
    r50_time = metrics["up_50_time_sec"][sel & metrics["up_50_reached"]]

    feat = {
        "selected_n": int(sel.sum()),
        "up30_reach_delta_pt": reach_delta(30),
        "up50_reach_delta_pt": reach_delta(50),
        "up30_median_reach_time": _nanmedian(r30_time),
        "up50_median_reach_time": _nanmedian(r50_time),
        "pre30_mae_med": _nanmedian(metrics["pre_reach_MAE_30_bps"][sel & metrics["up_30_reached"]]),
        "pre50_mae_med": _nanmedian(metrics["pre_reach_MAE_50_bps"][sel & metrics["up_50_reached"]]),
        "pre60_mae_med": _nanmedian(metrics["pre_reach_MAE_60_bps"][sel & metrics["up_60_reached"]]),
        "mfe300_mean": horizon_mfe("300s", sel),
        "mfe900_mean": horizon_mfe("900s", sel),
        "mfe1800_mean": horizon_mfe("1800s", sel),
        "mfe300_med": _nanmedian(metrics["MFE_300s_bps"][sel & metrics["eligible_300s"] & metrics["fresh_ok_300s"]]),
        "mfe900_med": _nanmedian(metrics["MFE_900s_bps"][sel & metrics["eligible_900s"] & metrics["fresh_ok_900s"]]),
        "mfe1800_med": _nanmedian(metrics["MFE_1800s_bps"][sel & metrics["eligible_1800s"] & metrics["fresh_ok_1800s"]]),
        "ret300_delta": None,
        "ret900_delta": None,
        "ret1800_delta": None,
        "ret_session_delta": None,
        "mfe300_delta": None,
        "up60_within_300": within_rate(60, 300, sel),
        "up60_within_900": within_rate(60, 900, sel),
        "up30_within_300_delta_pt": None,
        "up30_within_900_delta_pt": None,
        "up50_within_900_delta_pt": None,
        "term_gb_900_med": giveback_med("900s", sel),
        "term_gb_300_med": giveback_med("300s", sel),
        "max_gb_300_med": max_gb_med("300s", sel),
        "max_gb_900_med": max_gb_med("900s", sel),
        "max_gb_1800_med": max_gb_med("1800s", sel),
        # raw arrays for calibration votes (mask-level summaries)
        "pre30_mae_q": _nanmedian(metrics["pre_reach_MAE_30_bps"][sel & metrics["up_30_reached"]]),
        "pre50_mae_q": _nanmedian(metrics["pre_reach_MAE_50_bps"][sel & metrics["up_50_reached"]]),
        "pre60_mae_q": _nanmedian(metrics["pre_reach_MAE_60_bps"][sel & metrics["up_60_reached"]]),
    }

    # deltas vs ALL
    for key, outk in (("300s", "ret300_delta"), ("900s", "ret900_delta"), ("1800s", "ret1800_delta"), ("session", "ret_session_delta")):
        s = horizon_mean(key, sel)
        a = horizon_mean(key, all_m)
        feat[outk] = None if s is None or a is None else s - a
    s = horizon_mfe("300s", sel)
    a = horizon_mfe("300s", all_m)
    feat["mfe300_delta"] = None if s is None or a is None else s - a

    for up, h, outk in ((30, 300, "up30_within_300_delta_pt"), (30, 900, "up30_within_900_delta_pt"), (50, 900, "up50_within_900_delta_pt")):
        rs = within_rate(up, h, sel)
        ra = within_rate(up, h, all_m)
        feat[outk] = None if rs is None or ra is None else (rs - ra) * 100.0

    return feat


def _pos(x: Optional[float]) -> float:
    if x is None or x != x:
        return 0.0
    return max(0.0, float(x))


def family_margin_scores(feat: dict[str, Any], tags: list[str]) -> dict[str, float]:
    """
    Excess margin vs original family rule thresholds.
    Only tagged families get positive scores; others 0.
    """
    scores = {f: 0.0 for f in PATH_FAMILIES}

    if "QUICK_MOVE" in tags:
        # rule: delta>=2 AND time<=300; margin = excess over thresholds
        d = feat.get("up30_reach_delta_pt")
        t = feat.get("up30_median_reach_time")
        scores["QUICK_MOVE"] = _pos(None if d is None else d - 2.0) + _pos(
            None if t is None else (300.0 - t) / 300.0 * 2.0
        )

    if "PULLBACK_THEN_RISE" in tags:
        d30 = feat.get("up30_reach_delta_pt")
        d50 = feat.get("up50_reach_delta_pt")
        d = max(_pos(None if d30 is None else d30 - 2.0), _pos(None if d50 is None else d50 - 2.0))
        pre = feat.get("pre50_mae_med")
        if pre is None:
            pre = feat.get("pre30_mae_med")
        pull = _pos(None if pre is None else (-10.0 - pre))  # more negative = more margin
        t = feat.get("up50_median_reach_time")
        if t is None:
            t = feat.get("up30_median_reach_time")
        t_m = _pos(None if t is None else (900.0 - t) / 900.0 * 2.0)
        scores["PULLBACK_THEN_RISE"] = d + pull + t_m

    if "CONTINUATION" in tags:
        mfe_ext = None
        if feat.get("mfe900_mean") is not None and feat.get("mfe300_mean") is not None:
            mfe_ext = feat["mfe900_mean"] - feat["mfe300_mean"] - 20.0
        late = None
        if feat.get("up60_within_900") is not None and feat.get("up60_within_300") is not None:
            late = (feat["up60_within_900"] - feat["up60_within_300"]) * 100.0 - 5.0
        scores["CONTINUATION"] = (
            _pos(mfe_ext) + _pos(late) + _pos(feat.get("ret900_delta"))
        )

    if "DELAYED_MOVE" in tags:
        early_gap = 0.0
        if feat.get("ret300_delta") is not None:
            early_gap += _pos(-feat["ret300_delta"])
        late = max(_pos(feat.get("ret900_delta")), _pos(feat.get("ret1800_delta")))
        late_r = max(
            _pos(None if feat.get("up30_within_900_delta_pt") is None else feat["up30_within_900_delta_pt"] - 3.0),
            _pos(None if feat.get("up50_within_900_delta_pt") is None else feat["up50_within_900_delta_pt"] - 3.0),
        )
        scores["DELAYED_MOVE"] = early_gap + late + late_r

    if "SPIKE_AND_GIVEBACK" in tags:
        scores["SPIKE_AND_GIVEBACK"] = (
            _pos(feat.get("mfe300_delta"))
            + _pos(None if feat.get("ret900_delta") is None else -feat["ret900_delta"])
            + _pos(None if feat.get("term_gb_900_med") is None else feat["term_gb_900_med"] - 20.0)
        )

    if "NO_CLEAR_PATH_EDGE" in tags and len(tags) == 1:
        scores["NO_CLEAR_PATH_EDGE"] = 1.0

    return scores


def route_families(tags: list[str], scores: dict[str, float]) -> dict[str, Any]:
    """Max 2 families; NO_CLEAR exclusive."""
    tags = list(tags)
    if "NO_CLEAR_PATH_EDGE" in tags:
        # exclusive
        return {
            "primary_path_family": "NO_CLEAR_PATH_EDGE",
            "secondary_path_family": None,
            "routing_reason": "NO_CLEAR_exclusive",
            "family_margin_scores": scores,
        }

    ranked = sorted(
        [(f, scores.get(f, 0.0)) for f in PATH_FAMILIES if f != "NO_CLEAR_PATH_EDGE" and f in tags],
        key=lambda x: (-x[1], x[0]),
    )
    if not ranked:
        return {
            "primary_path_family": "NO_CLEAR_PATH_EDGE",
            "secondary_path_family": None,
            "routing_reason": "no_positive_family_tag",
            "family_margin_scores": scores,
        }
    primary, pscore = ranked[0]
    secondary = None
    reason = f"primary={primary}_score={pscore:.4f}"
    if len(ranked) >= 2 and pscore > 0 and ranked[1][1] >= 0.5 * pscore:
        secondary = ranked[1][0]
        reason += f";secondary={secondary}_score={ranked[1][1]:.4f}_ge_50pct_primary"
    elif len(ranked) >= 2:
        reason += f";secondary_skipped_score={ranked[1][1]:.4f}_lt_50pct_primary"
    return {
        "primary_path_family": primary,
        "secondary_path_family": secondary,
        "routing_reason": reason,
        "family_margin_scores": scores,
    }
