"""Build ENTRY-time feature matrix for Winner Feature Filter (causal only)."""
from __future__ import annotations

import math
from datetime import datetime, time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.cost_aware_v2.dataset import NATIVE, TradeRow, _parse_ts
from research.winner_feature_filter.labels import LabeledTrade

JST = ZoneInfo("Asia/Tokyo")
OPEN_AM = time(9, 0)
REFRESH_AM = time(11, 30)
OPEN_PM = time(12, 30)
REFRESH_PM = time(15, 0)

WINDOWS = {
    "30s": 30,
    "60s": 60,
    "120s": 120,
    "5m": 300,
    "10m": 600,
    "15m": 900,
}


def _minutes_from_open(entry_ts: Optional[datetime], session: str) -> Optional[float]:
    if entry_ts is None:
        return None
    t = entry_ts.astimezone(JST)
    open_t = OPEN_AM if session == "AM" else OPEN_PM
    base = datetime.combine(t.date(), open_t, tzinfo=JST)
    return round((t - base).total_seconds() / 60.0, 4)


def _minutes_to_refresh(entry_ts: Optional[datetime], session: str) -> Optional[float]:
    if entry_ts is None:
        return None
    t = entry_ts.astimezone(JST)
    ref = REFRESH_AM if session == "AM" else REFRESH_PM
    target = datetime.combine(t.date(), ref, tzinfo=JST)
    return round((target - t).total_seconds() / 60.0, 4)


def _near_refresh(mins: Optional[float], *, band: float = 15.0) -> Optional[float]:
    if mins is None:
        return None
    return 1.0 if 0.0 <= mins <= band else 0.0


def _window_alias(w_name: str) -> list[int]:
    sec = WINDOWS[w_name]
    if sec <= 30:
        return [30, 10]
    if sec <= 60:
        return [60, 30]
    if sec <= 120:
        return [120, 60]
    if sec <= 300:
        return [300, 120]
    return [300]


def _first_feat(feats: Mapping[str, Optional[float]], keys: Sequence[str]) -> Optional[float]:
    for k in keys:
        v = feats.get(k)
        if v is not None:
            return float(v)
    return None


def build_feature_dict(trade: TradeRow) -> dict[str, Optional[float]]:
    f: dict[str, Optional[float]] = dict(trade.features)

    # Time-of-day features intentionally NOT attached (excluded from all research stages).
    f["mkt_scan_rank"] = f.get("f_scan_rank")
    f["mkt_cap_usage"] = f.get("f_cap_usage")
    f["mkt_cap_mode"] = f.get("f_cap_mode")

    for w_name, sec in WINDOWS.items():
        aliases = _window_alias(w_name)
        ret_keys = [f"f_np_ret_{a}" for a in aliases]
        if w_name == "5m":
            ret_keys = ["f_rise5", "f_np_ret_300"] + ret_keys
        elif w_name == "10m":
            ret_keys = ["f_rise10", "f_np_ret_300"]
        elif w_name == "15m":
            ret_keys = ["f_rise15", "f_rise10", "f_np_ret_300"]
        elif w_name == "30s":
            ret_keys = ["f_r30", "f_np_ret_30", "f_np_ret_10"]
        elif w_name == "60s":
            ret_keys = ["f_r60", "f_np_ret_60", "f_np_ret_30"]
        elif w_name == "120s":
            ret_keys = ["f_r120", "f_np_ret_120", "f_np_ret_60"]
        f[f"w_{w_name}_ret"] = _first_feat(f, ret_keys)
        f[f"w_{w_name}_slope"] = _first_feat(
            f, [f"f_np_slope_{a}" for a in aliases] + (["f_slope5"] if sec >= 300 else [])
        )
        f[f"w_{w_name}_accel"] = _first_feat(f, [f"f_np_accel_{a}" for a in aliases])
        f[f"w_{w_name}_decel"] = (
            -float(f[f"w_{w_name}_accel"]) if f.get(f"w_{w_name}_accel") is not None else None
        )
        f[f"w_{w_name}_imb_chg"] = _first_feat(f, [f"f_np_imb_chg_{a}" for a in aliases])
        f[f"w_{w_name}_imb_persist"] = _first_feat(f, [f"f_np_imb_persist_{a}" for a in aliases])
        f[f"w_{w_name}_bid_chg"] = _first_feat(f, [f"f_np_bid_chg_{a}" for a in aliases])
        f[f"w_{w_name}_ask_chg"] = _first_feat(f, [f"f_np_ask_chg_{a}" for a in aliases])
        f[f"w_{w_name}_tv_chg"] = _first_feat(f, [f"f_np_tv_chg_pct_{a}" for a in aliases])
        f[f"w_{w_name}_vol_price_sync"] = _first_feat(f, [f"f_np_vol_price_sync_{a}" for a in aliases])
        f[f"w_{w_name}_ticks"] = _first_feat(f, [f"f_np_ticks_{a}" for a in aliases])
        ticks = f.get(f"w_{w_name}_ticks")
        f[f"w_{w_name}_exec_speed"] = (
            round(float(ticks) / float(sec), 6) if ticks is not None and sec > 0 else None
        )

    f["px_near_high"] = f.get("f_near_high")
    f["px_fall_from_high"] = f.get("f_fall")
    f["px_bounce_from_low"] = f.get("f_bounce")
    f["px_vwap_dev"] = f.get("f_vwap")
    f["px_atr"] = f.get("f_atr")
    f["px_tick_ratio"] = f.get("f_tick_ratio")
    msh = f.get("f_minutes_since_day_high")
    f["px_high_update_fresh"] = round(1.0 / (1.0 + float(msh)), 6) if msh is not None else None
    bounce = f.get("f_bounce")
    fall = f.get("f_fall")
    near = f.get("f_near_high")
    f["mom_pullback_rate"] = round(abs(float(fall)), 6) if fall is not None else None
    f["mom_rebound_rate"] = round(float(bounce), 6) if bounce is not None else None
    f["mom_recovery_vs_near"] = (
        round(float(bounce) - float(near), 6) if bounce is not None and near is not None else None
    )

    f["board_imb"] = f.get("f_imb")
    f["board_imb_pct"] = f.get("f_imb_pct")
    f["board_spread"] = f.get("f_spread")
    f["board_age"] = f.get("f_board_age")
    f["board_div_price_up_board_down"] = f.get("f_div_price_up_board_down")

    f["vol_tv"] = f.get("f_tv")
    f["vol_surge_60s"] = f.get("w_60s_tv_chg")
    f["vol_surge_5m"] = f.get("w_5m_tv_chg")

    f["tech_rsi14"] = f.get("f_rsi")
    f["tech_pbv2"] = f.get("f_pbv2")
    f["tech_mom"] = f.get("f_mom") if f.get("f_mom") is not None else f.get("f_mom_alt")
    f["tech_pure_mom"] = f.get("f_pure_mom")
    f["tech_chase"] = f.get("f_chase")
    f["tech_w54_stop_risk"] = f.get("f_w54_stop_risk")
    f["tech_rolling_mfe"] = f.get("f_rolling_mfe")
    f["tech_rolling_mae"] = f.get("f_rolling_mae")

    vwap = f.get("f_vwap")
    r60 = f.get("w_60s_ret")
    f["px_vwap_breakout_flag"] = (
        1.0
        if (vwap is not None and r60 is not None and float(vwap) > 0 and float(r60) > 0)
        else (0.0 if (vwap is not None and r60 is not None) else None)
    )
    f["px_ma_proxy_5m"] = f.get("f_rise5")
    f["px_ma_proxy_10m"] = f.get("f_rise10")
    f["px_ma_proxy_15m"] = f.get("f_rise15")
    return f


FEATURE_PREFIXES_KEEP = (
    "w_",
    "px_",
    "mom_",
    "board_",
    "vol_",
    "tech_",
    "mkt_",
    "f_scan_rank",
    "f_cap_",
    "f_shape_",
    "f_rsi",
    "f_pbv2",
    "f_near_high",
    "f_imb",
    "f_vwap",
    "f_atr",
    "f_spread",
    "f_mom",
    "f_rise",
    "f_r30",
    "f_r60",
    "f_r120",
    "f_bounce",
    "f_fall",
    "f_slope5",
    "f_tv",
    "f_chase",
    "f_w54",
    "f_div",
    "f_np_",
    "f_tick",
    "f_minutes",
    "f_day_high",
    "f_entry_mom",
)

# Rolling MFE/MAE at accept can be near-tautological / weakly post-setup; exclude from Winner model.
FEATURE_EXCLUDE_SUBSTRINGS = (
    "rolling_mfe",
    "rolling_mae",
)


def select_model_features(feat: Mapping[str, Optional[float]]) -> dict[str, Optional[float]]:
    from research.winner_feature_filter.lanes import is_time_feature

    out: dict[str, Optional[float]] = {}
    for k, v in feat.items():
        if is_time_feature(k):
            continue
        if any(s in k.lower() for s in FEATURE_EXCLUDE_SUBSTRINGS):
            continue
        # Exclude market-state time proxies even if prefix matches
        if k.startswith("mkt_"):
            continue
        if any(k.startswith(p) or k == p for p in FEATURE_PREFIXES_KEEP):
            if v is None:
                out[k] = None
                continue
            try:
                x = float(v)
            except (TypeError, ValueError):
                continue
            if math.isnan(x) or math.isinf(x):
                out[k] = None
            else:
                out[k] = x
    return out


def build_matrix(
    labeled: Sequence[LabeledTrade],
    *,
    native: Path = NATIVE,
) -> tuple[list[str], list[dict[str, Optional[float]]], dict[str, Any]]:
    _ = native
    rows: list[dict[str, Optional[float]]] = []
    for lt in labeled:
        full = build_feature_dict(lt.trade)
        rows.append(select_model_features(full))

    key_counts: dict[str, int] = {}
    for r in rows:
        for k, v in r.items():
            if v is not None:
                key_counts[k] = key_counts.get(k, 0) + 1
    feature_names = sorted(k for k, n in key_counts.items() if n >= max(20, int(0.01 * len(rows))))
    meta = {
        "n_rows": len(rows),
        "n_feature_candidates": len(key_counts),
        "n_features_used": len(feature_names),
        "min_non_null": max(20, int(0.01 * len(rows))),
        "fill_rates": {k: round(key_counts.get(k, 0) / max(len(rows), 1), 4) for k in feature_names},
        "windows": list(WINDOWS.keys()),
        "note": (
            "Classical MACD/Stoch/ADX/CCI/ROC/Williams/BB/EMA-cross require 1m bars; "
            "full-history bars unavailable for most formal days. "
            "Used accept-time RSI/ATR/VWAP/momentum + NP multi-window (30s–5m) + rise10/15 proxies for 10m/15m. "
            "market_capture L2 available only for recent capture days."
        ),
    }
    return feature_names, rows, meta


def matrix_to_xy(
    feature_names: Sequence[str],
    rows: Sequence[Mapping[str, Optional[float]]],
    y: Sequence[int],
) -> tuple[Any, Any, list[str], dict[str, Any]]:
    """Build model matrix.

    Missing-value policy (importance path):
      - per-column **median imputation** for NaN
      - if a column is entirely NaN → 0.0 fill (should not occur after feature filter)
      - NOT null-row exclusion; NOT mean imputation; NOT blanket zero-fill
    """
    import numpy as np

    X = np.full((len(rows), len(feature_names)), np.nan, dtype=float)
    for i, r in enumerate(rows):
        for j, k in enumerate(feature_names):
            v = r.get(k)
            if v is not None:
                X[i, j] = float(v)
    n_median = 0
    n_zero_col = 0
    for j in range(X.shape[1]):
        col = X[:, j]
        mask = ~np.isnan(col)
        if mask.any():
            med = float(np.nanmedian(col))
            n_miss = int((~mask).sum())
            if n_miss:
                n_median += n_miss
            col[~mask] = med
            X[:, j] = col
        else:
            X[:, j] = 0.0
            n_zero_col += 1
    meta = {
        "missing_value_policy": "median_imputation",
        "null_exclusion": False,
        "zero_fill": False,
        "mean_imputation": False,
        "n_cells_median_imputed": n_median,
        "n_all_nan_columns_zero_filled": n_zero_col,
        "n_rows_used_for_importance": len(rows),
        "note": (
            "Importance (LGBM/SHAP/Permutation/IG/MI) runs on all formal rows after "
            "per-column median imputation. Sparse features (e.g. NP board windows) are "
            "therefore evaluated on the full 1900-row matrix with imputed placeholders."
        ),
    }
    return X, np.asarray(y, dtype=int), list(feature_names), meta


def availability_table(
    feature_names: Sequence[str],
    rows: Sequence[Mapping[str, Optional[float]]],
    fill_rates: Mapping[str, float],
) -> list[dict[str, Any]]:
    n = len(rows)
    out = []
    for name in feature_names:
        n_ok = sum(1 for r in rows if r.get(name) is not None)
        out.append(
            {
                "feature": name,
                "n_available": n_ok,
                "n_total": n,
                "fill_rate": round(n_ok / n, 4) if n else 0.0,
                "fill_rate_meta": fill_rates.get(name),
            }
        )
    out.sort(key=lambda r: (-r["n_available"], r["feature"]))
    return out
