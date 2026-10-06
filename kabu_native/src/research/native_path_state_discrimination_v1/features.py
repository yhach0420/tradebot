"""Compact causal ENTRY-time state. No future path. No 105-symbol one-hot. No indicator grids."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.clock import parse_hhmm
from research.native_path_state_discrimination_v1 import GROUP_NAMES, IMPULSE_SIGMA, LOOKBACK_BARS, MIN_SECTOR_N
from research.one_minute_native_playbook_discovery_v1.states import features_at, prior_std, to_min

LEAK_TOKENS = (
    "mfe",
    "mae",
    "path_type",
    "path_class",
    "x0_bps",
    "x1_bps",
    "x20",
    "y",
    "fwd",
    "ret_p5",
    "ret_p10",
    "outcome",
)

NUM_CORE = (
    "ret_1m",
    "ret_3m",
    "ret_5m",
    "dist_20high",
    "dist_20low",
    "opening_gap",
    "mins_from_open",
    "mins_since_impulse",
    "pullback_depth",
)
NUM_GROUP = tuple(f"g_{g}" for g in GROUP_NAMES)
NUM_SECTOR = (
    "sector_rel_sess",
    "market_rel_sess",
    "sector_rank_pct",
    "leading",
    "lagging",
    "sector_breadth_pos",
)
NUM_ACTIVITY = (
    "vol_rel20",
    "va_rel20",
    "rng_rel20",
    "rvol_1m",
    "vol_expand",
    "va_expand",
    "range_expand",
    "compression",
)
NUM_VWAP = (
    "dist_vwap",
    "vwap_reclaim",
    "above_vwap",
    "pullback",
)

FEATURE_SETS = {
    "F_BASE": NUM_CORE + ("pullback",),
    "F_GROUP": NUM_CORE + ("pullback",) + NUM_GROUP,
    "F_SECTOR": NUM_CORE + ("pullback",) + NUM_GROUP + NUM_SECTOR,
    "F_ACTIVITY": NUM_CORE + ("pullback",) + NUM_GROUP + NUM_SECTOR + NUM_ACTIVITY,
    "F_VWAP": NUM_CORE + ("pullback",) + NUM_GROUP + NUM_SECTOR + NUM_ACTIVITY + NUM_VWAP,
}

PRIMARY_FEATURES = FEATURE_SETS["F_VWAP"]


def spec_sha() -> str:
    payload = {
        "primary": list(PRIMARY_FEATURES),
        "sets": {k: list(v) for k, v in FEATURE_SETS.items()},
        "no_symbol_onehot": True,
        "no_macd_rsi_ema_grid": True,
    }
    return hashlib.sha256(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()


def leakage_tokens_in_features(names: tuple[str, ...] = PRIMARY_FEATURES) -> list[str]:
    hit = []
    for n in names:
        low = n.lower()
        for tok in LEAK_TOKENS:
            if tok in low:
                hit.append(n)
                break
    return hit


def _b(x: Any) -> float:
    return 1.0 if bool(x) else 0.0


def _f(x: Any) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v


def mins_from_open(hhmm: str) -> float:
    p = parse_hhmm(hhmm)
    if p is None:
        return float("nan")
    return float(p[0] * 60 + p[1] - 9 * 60)


def last_impulse_mins(rec: dict[str, Any], i: int) -> float:
    tm = to_min(rec["t"][i])
    if tm is None:
        return float("nan")
    found = None
    for j in range(i, max(-1, i - 30), -1):
        r1 = rec["r1"][j]
        sd = prior_std(rec["r1"], j, LOOKBACK_BARS)
        if np.isfinite(r1) and np.isfinite(sd) and sd > 0 and r1 > 0 and r1 > IMPULSE_SIGMA * sd:
            found = to_min(rec["t"][j])
            break
    if found is None or tm is None:
        return float("nan")
    return float(max(0, tm - found))


def compact_state(
    *,
    feat: dict[str, Any],
    snap: dict[str, Any],
    opening_gap: float | None,
    group: str,
    rec: dict[str, Any],
    i: int,
) -> dict[str, float]:
    sec = str(feat.get("sector") or "")
    st = (snap.get("sectors") or {}).get(sec) or {}
    rk = feat.get("sector_rank")
    nsec = feat.get("sector_n")
    rank_pct = float("nan")
    if rk is not None and nsec and int(nsec) >= MIN_SECTOR_N:
        rank_pct = float(rk) / float(nsec)
    pull = bool(feat.get("pullback"))
    r5 = _f(feat.get("ret_5m"))
    row: dict[str, float] = {
        "ret_1m": _f(feat.get("ret_1m")),
        "ret_3m": _f(feat.get("ret_3m")),
        "ret_5m": r5,
        "dist_20high": _f(feat.get("dist_20high")),
        "dist_20low": _f(feat.get("dist_20low")),
        "opening_gap": _f(opening_gap) if opening_gap is not None else float("nan"),
        "mins_from_open": mins_from_open(str(feat.get("feature_bar") or "")),
        "mins_since_impulse": last_impulse_mins(rec, i),
        "pullback_depth": float(-r5) if pull and r5 == r5 else 0.0,
        "pullback": _b(pull),
        "sector_rel_sess": _f(feat.get("sess_ret")) - _f(st.get("sess_med")),
        "market_rel_sess": _f(feat.get("sess_ret")) - _f(snap.get("market_sess_med")),
        "sector_rank_pct": rank_pct,
        "leading": _b(feat.get("leading")),
        "lagging": _b(feat.get("lagging")),
        "sector_breadth_pos": _f(st.get("breadth_pos")),
        "vol_rel20": _f(feat.get("vol_rel20")),
        "va_rel20": _f(feat.get("va_rel20")),
        "rng_rel20": _f(feat.get("rng_rel20")),
        "rvol_1m": _f(feat.get("rvol_1m")),
        "vol_expand": _b(feat.get("vol_expand")),
        "va_expand": _b(feat.get("va_expand")),
        "range_expand": _b(feat.get("range_expand")),
        "compression": _b(feat.get("compression")),
        "dist_vwap": _f(feat.get("dist_vwap")),
        "vwap_reclaim": _b(feat.get("vwap_reclaim")),
        "above_vwap": _b(feat.get("above_vwap")),
    }
    for g in GROUP_NAMES:
        row[f"g_{g}"] = 1.0 if group == g else 0.0
    return row


def availability_ok(ep: dict[str, Any]) -> dict[str, Any]:
    feat_bar = str(ep.get("feature_bar") or "")
    avail = str(ep.get("available_at") or "")
    decision = str(ep.get("event_time") or "")
    leak = leakage_tokens_in_features()
    return {
        "feature_bar": feat_bar,
        "available_at": avail,
        "decision_time": decision,
        "available_at_le_decision": bool(avail and decision and avail <= decision),
        "feature_bar_lt_decision": bool(feat_bar and decision and feat_bar < decision),
        "leakage_feature_names": leak,
        "any_feature_leakage": bool(leak),
    }
