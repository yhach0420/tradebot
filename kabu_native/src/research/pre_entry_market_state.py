"""Pre-entry market state features + multi-day research store (no strategy wiring).

1 ENTRY = 1 sample. Leak-safe: only recv_epoch <= entry_epoch for features.
"""

from __future__ import annotations

import csv
import json
import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from research.board_entry_features import (
    MAX_WORKERS,
    fnum,
    load_accepted_entries,
    nearest_backward,
    parse_ts,
    stream_slim_board,
    sym_code,
    window_dynamics,
)
from research.board_entry_dataset_append import detect_session_meta, is_eligible, native_root_from

log = logging.getLogger(__name__)

from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

SCHEMA_VERSION = "pre_entry_market_state_v1_w43"
PRE_WINDOWS = (30, 60, 120, 300, 600)
POST_WINDOWS = (30, 60, 120, 300, 600)
REANALYSIS_GATES = {
    5: "pilot_cluster_recompute",
    10: "cluster_stability_eval",
    20: "candidate_rule_review",
}
MAX_INTERPRETABLE_STATES = 8


def dataset_root(native_root: Path) -> Path:
    return native_root / "results" / "research" / "pre_entry_market_state"


def partition_dir(root: Path, trading_date: str) -> Path:
    return root / f"trading_date={trading_date}"


def manifest_path(root: Path) -> Path:
    return root / "market_state_manifest.json"


def summary_csv_path(root: Path) -> Path:
    return root / "market_state_summary.csv"


def stability_csv_path(root: Path) -> Path:
    return root / "cluster_stability_history.csv"


def load_manifest(root: Path) -> dict[str, Any]:
    p = manifest_path(root)
    if not p.is_file():
        return {
            "schema_version": SCHEMA_VERSION,
            "sessions": {},
            "trading_dates": [],
            "n_trading_days": 0,
            "created_at": datetime.now(JST).isoformat(),
            "reanalysis_gates": REANALYSIS_GATES,
            "adoption_blocked_until_days": 5,
        }
    return json.loads(p.read_text(encoding="utf-8"))


def save_manifest(root: Path, man: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    man["updated_at"] = datetime.now(JST).isoformat()
    man["schema_version"] = SCHEMA_VERSION
    man["n_trading_days"] = len(man.get("trading_dates") or [])
    tmp = root / "market_state_manifest.json.tmp"
    tmp.write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(manifest_path(root))


def load_np_pre_entry(session_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    path = session_dir / "np_pre_entry_features.jsonl"
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            o = json.loads(line)
            key = (str(o.get("symbol") or ""), str(o.get("entry_time") or ""))
            out[key] = o
    return out


def _finite(x: Any) -> bool:
    try:
        v = float(x)
        return v == v and np.isfinite(v)
    except (TypeError, ValueError):
        return False


def price_volume_window_features(
    df: pd.DataFrame,
    *,
    entry_epoch: float,
    entry_price: float,
    window_sec: float,
) -> dict[str, Any]:
    """Leak-safe pre-entry price/volume features for one window."""
    prefix = f"pre_{int(window_sec)}s"
    empty = {
        f"{prefix}_coverage_ok": False,
        f"{prefix}_n_ticks": 0,
        f"{prefix}_span_sec": 0.0,
        f"{prefix}_volume_source": "capture_trading_volume",
    }
    if df is None or len(df) == 0 or not _finite(entry_epoch) or not _finite(entry_price) or entry_price <= 0:
        return empty
    wdf = df[(df["recv_epoch"] > entry_epoch - window_sec) & (df["recv_epoch"] <= entry_epoch)].sort_values(
        "recv_epoch"
    )
    if len(wdf) < 2:
        return empty

    px = wdf["current_price"].astype(float).to_numpy()
    ts = wdf["recv_epoch"].astype(float).to_numpy()
    vol = wdf["trading_volume"].astype(float).to_numpy() if "trading_volume" in wdf.columns else np.full(len(wdf), np.nan)
    tval = wdf["trading_value"].astype(float).to_numpy() if "trading_value" in wdf.columns else np.full(len(wdf), np.nan)

    valid_px = px[np.isfinite(px) & (px > 0)]
    if len(valid_px) < 2:
        return empty

    p0, p1 = float(valid_px[0]), float(valid_px[-1])
    ret = (p1 / p0 - 1.0) if p0 > 0 else float("nan")
    # slope via simple OLS on finite points
    mask = np.isfinite(px) & (px > 0)
    tsm, pxm = ts[mask], px[mask]
    if len(pxm) >= 2 and (tsm[-1] - tsm[0]) > 0:
        x = (tsm - tsm[0]) / max(1.0, window_sec)
        slope = float(np.polyfit(x, pxm / p0 - 1.0, 1)[0])
    else:
        slope = float("nan")

    run_max = np.maximum.accumulate(valid_px)
    run_min = np.minimum.accumulate(valid_px)
    max_rise = float(np.nanmax(valid_px / run_min - 1.0)) if len(valid_px) else float("nan")
    max_dd = float(np.nanmin(valid_px / run_max - 1.0)) if len(valid_px) else float("nan")
    recent_low = float(np.nanmin(valid_px))
    recent_high = float(np.nanmax(valid_px))
    bounce = (p1 - recent_low) / recent_low if recent_low > 0 else float("nan")
    fall = (recent_high - p1) / recent_high if recent_high > 0 else float("nan")
    range_width = (recent_high - recent_low) / p0 if p0 > 0 else float("nan")

    log_rets = np.diff(np.log(valid_px))
    rvol = float(np.std(log_rets) * np.sqrt(max(len(log_rets), 1))) if len(log_rets) else float("nan")

    high_updates = low_updates = new_high = new_low = 0
    cur_hi, cur_lo = valid_px[0], valid_px[0]
    for i in range(1, len(valid_px)):
        if valid_px[i] > valid_px[i - 1]:
            high_updates += 1
        elif valid_px[i] < valid_px[i - 1]:
            low_updates += 1
        if valid_px[i] > cur_hi:
            new_high += 1
            cur_hi = valid_px[i]
        if valid_px[i] < cur_lo:
            new_low += 1
            cur_lo = valid_px[i]

    # breakout success/failure: attempt above early high then hold vs fail
    early_n = max(2, len(valid_px) // 3)
    early_hi = float(np.nanmax(valid_px[:early_n]))
    broke = p1 > early_hi * 1.001
    failed_bo = (float(np.nanmax(valid_px)) > early_hi * 1.001) and (p1 < early_hi)
    breakout_success = 1.0 if broke and not failed_bo else 0.0
    breakout_failure = 1.0 if failed_bo else 0.0

    # same-price duration (seconds of longest flat run)
    same_dur = 0.0
    run = 0.0
    for i in range(1, len(px)):
        if np.isfinite(px[i]) and np.isfinite(px[i - 1]) and abs(px[i] - px[i - 1]) < 1e-9:
            run += float(ts[i] - ts[i - 1])
            same_dur = max(same_dur, run)
        else:
            run = 0.0
    price_updates = sum(
        1 for i in range(1, len(px)) if np.isfinite(px[i]) and np.isfinite(px[i - 1]) and abs(px[i] - px[i - 1]) > 1e-9
    )

    # VWAP proxy from cumulative value/volume when both rise
    vwap_dev = float("nan")
    vwap_cross = 0
    if np.isfinite(vol).sum() >= 2 and np.isfinite(tval).sum() >= 2:
        v0, v1 = float(vol[np.isfinite(vol)][0]), float(vol[np.isfinite(vol)][-1])
        tv0, tv1 = float(tval[np.isfinite(tval)][0]), float(tval[np.isfinite(tval)][-1])
        dvol = v1 - v0
        dval = tv1 - tv0
        if dvol > 0 and dval > 0:
            vwap = dval / dvol
            vwap_dev = (p1 / vwap - 1.0) if vwap > 0 else float("nan")
            # crossing count vs running vwap
            prev_side = 0
            for i in range(1, len(px)):
                if not (np.isfinite(vol[i]) and np.isfinite(tval[i]) and np.isfinite(px[i])):
                    continue
                dv = vol[i] - vol[0]
                dvv = tval[i] - tval[0]
                if dv <= 0:
                    continue
                vw = dvv / dv
                side = 1 if px[i] > vw else (-1 if px[i] < vw else 0)
                if prev_side and side and side != prev_side:
                    vwap_cross += 1
                if side:
                    prev_side = side

    vol_finite = vol[np.isfinite(vol)]
    tv_finite = tval[np.isfinite(tval)]
    vol_delta = float(vol_finite[-1] - vol_finite[0]) if len(vol_finite) >= 2 else float("nan")
    tv_delta = float(tv_finite[-1] - tv_finite[0]) if len(tv_finite) >= 2 else float("nan")
    # acceleration: second half delta vs first half
    mid = len(wdf) // 2
    vol_acc = float("nan")
    tv_acc = float("nan")
    if mid >= 1 and len(vol_finite) >= 4:
        v_a = float(vol[mid] - vol[0]) if np.isfinite(vol[mid]) and np.isfinite(vol[0]) else float("nan")
        v_b = float(vol[-1] - vol[mid]) if np.isfinite(vol[-1]) and np.isfinite(vol[mid]) else float("nan")
        if _finite(v_a) and _finite(v_b):
            vol_acc = v_b - v_a
    if mid >= 1 and len(tv_finite) >= 4:
        t_a = float(tval[mid] - tval[0]) if np.isfinite(tval[mid]) and np.isfinite(tval[0]) else float("nan")
        t_b = float(tval[-1] - tval[mid]) if np.isfinite(tval[-1]) and np.isfinite(tval[mid]) else float("nan")
        if _finite(t_a) and _finite(t_b):
            tv_acc = t_b - t_a

    span = float(ts[-1] - ts[0])
    vol_burst = 1.0 if _finite(vol_delta) and vol_delta > 0 and span > 0 and (vol_delta / span) > 0 else 0.0
    # persistence: positive volume increments across thirds
    persist = 0.0
    if len(vol_finite) >= 6:
        cuts = np.array_split(np.arange(len(vol)), 3)
        pos = 0
        for c in cuts:
            if len(c) < 2:
                continue
            a, b = vol[c[0]], vol[c[-1]]
            if np.isfinite(a) and np.isfinite(b) and b > a:
                pos += 1
        persist = pos / 3.0

    vol_price_ratio = (vol_delta / max(1.0, float(price_updates))) if _finite(vol_delta) else float("nan")
    px_move = abs(ret) if _finite(ret) else 0.0
    price_wo_vol = 1.0 if px_move >= 0.001 and (not _finite(vol_delta) or vol_delta <= 0) else 0.0
    vol_wo_progress = 1.0 if _finite(vol_delta) and vol_delta > 0 and px_move < 0.0005 else 0.0
    vol_dry = 1.0 if _finite(vol_delta) and vol_delta <= 0 else 0.0

    coverage_ok = span >= 0.5 * window_sec
    return {
        f"{prefix}_coverage_ok": bool(coverage_ok),
        f"{prefix}_n_ticks": int(len(wdf)),
        f"{prefix}_span_sec": round(span, 3),
        f"{prefix}_return": round(ret, 6) if _finite(ret) else float("nan"),
        f"{prefix}_slope": round(slope, 6) if _finite(slope) else float("nan"),
        f"{prefix}_max_rise": round(max_rise, 6) if _finite(max_rise) else float("nan"),
        f"{prefix}_max_drawdown": round(max_dd, 6) if _finite(max_dd) else float("nan"),
        f"{prefix}_bounce_from_recent_low": round(bounce, 6) if _finite(bounce) else float("nan"),
        f"{prefix}_fall_from_recent_high": round(fall, 6) if _finite(fall) else float("nan"),
        f"{prefix}_high_update_count": high_updates,
        f"{prefix}_low_update_count": low_updates,
        f"{prefix}_new_high_count": new_high,
        f"{prefix}_new_low_count": new_low,
        f"{prefix}_breakout_success": breakout_success,
        f"{prefix}_breakout_failure": breakout_failure,
        f"{prefix}_range_width": round(range_width, 6) if _finite(range_width) else float("nan"),
        f"{prefix}_realized_vol": round(rvol, 6) if _finite(rvol) else float("nan"),
        f"{prefix}_vwap_deviation": round(vwap_dev, 6) if _finite(vwap_dev) else float("nan"),
        f"{prefix}_vwap_crossing_count": vwap_cross,
        f"{prefix}_same_price_duration_sec": round(same_dur, 3),
        f"{prefix}_current_price_update_count": price_updates,
        f"{prefix}_volume_delta": vol_delta,
        f"{prefix}_trading_value_delta": tv_delta,
        f"{prefix}_volume_acceleration": vol_acc,
        f"{prefix}_trading_value_acceleration": tv_acc,
        f"{prefix}_volume_burst": vol_burst,
        f"{prefix}_volume_persistence": persist,
        f"{prefix}_volume_price_update_ratio": vol_price_ratio,
        f"{prefix}_price_without_volume": price_wo_vol,
        f"{prefix}_volume_without_price_progress": vol_wo_progress,
        f"{prefix}_volume_dry_up": vol_dry,
        f"{prefix}_volume_source": "capture_trading_volume",
        f"{prefix}_volume_missing": int(not np.isfinite(vol).any()),
    }


def classify_price_state(row: Mapping[str, Any]) -> str:
    ret = fnum(row.get("pre_300s_return"), 0.0)
    bounce = fnum(row.get("pre_300s_bounce_from_recent_low"), 0.0)
    fall = fnum(row.get("pre_300s_fall_from_recent_high"), 0.0)
    failed = fnum(row.get("pre_300s_breakout_failure"), 0.0) >= 1.0
    range_w = fnum(row.get("pre_300s_range_width"), 0.0)
    same = fnum(row.get("pre_300s_same_price_duration_sec"), 0.0)
    price_age = fnum(row.get("price_age_sec"), 0.0)
    board_churn = fnum(row.get("board_60s_same_price_board_churn") or row.get("board_300s_same_price_board_churn"), 0.0)
    if price_age >= 60 and board_churn >= 5:
        return "QUOTE_ONLY_STALE"
    if failed:
        return "FAILED_BREAKOUT"
    if ret >= 0.004 and bounce < 0.003:
        return "LATE_CHASE"
    if bounce >= 0.003 and ret >= -0.001:
        return "PULLBACK_RECOVERY"
    if ret >= 0.002:
        return "RISE"
    if ret <= -0.002 or fall >= 0.003:
        return "FALL"
    if range_w < 0.002 or same >= 60:
        return "FLAT"
    return "FLAT"


def classify_volume_state(row: Mapping[str, Any]) -> str:
    vd = fnum(row.get("pre_300s_volume_delta"))
    acc = fnum(row.get("pre_300s_volume_acceleration"), 0.0)
    persist = fnum(row.get("pre_300s_volume_persistence"), 0.0)
    dry = fnum(row.get("pre_300s_volume_dry_up"), 0.0) >= 1.0
    no_conf = fnum(row.get("pre_300s_price_without_volume"), 0.0) >= 1.0
    np_tv = fnum(row.get("np_tv_chg_pct_300s"))
    if dry or ( _finite(vd) and vd <= 0 and (not _finite(np_tv) or np_tv <= 0)):
        return "DRY"
    if no_conf:
        return "NO_CONFIRMATION"
    # surge heuristic: strong positive delta + acceleration
    if (_finite(vd) and vd > 0 and acc > 0 and persist >= 0.66) or (_finite(np_tv) and np_tv >= 0.02):
        return "SURGE"
    if (_finite(vd) and vd > 0) or (_finite(np_tv) and np_tv > 0):
        return "RISING"
    return "NORMAL"


def classify_board_state(row: Mapping[str, Any]) -> str:
    imb = fnum(row.get("board_at_entry_imbalance_l5") or row.get("board_60s_imbalance_l5"))
    ofi = fnum(row.get("board_60s_ofi_proxy") or row.get("board_300s_ofi_proxy"), 0.0)
    ask_dep = fnum(row.get("board_60s_ask_depletion_bid_replenish") or row.get("board_300s_ask_depletion_bid_replenish"), 0.0)
    churn = fnum(row.get("board_60s_same_price_board_churn") or row.get("board_300s_same_price_board_churn"), 0.0)
    px_upd = fnum(row.get("board_60s_price_update_count") or row.get("pre_60s_current_price_update_count"), 0.0)
    ask_wall = fnum(row.get("board_at_entry_ask_wall_qty"), 0.0)
    bid_wall = fnum(row.get("board_at_entry_bid_wall_qty"), 0.0)
    if churn >= 8 and px_upd <= 1:
        return "QUOTE_ONLY_CHURN"
    if ask_wall > 0 and bid_wall > 0 and ask_wall >= 3 * max(bid_wall, 1.0) and (not _finite(imb) or imb < 0):
        return "WALL_BLOCKED"
    if ask_dep >= 1.0 or (_finite(ofi) and ofi > 0 and _finite(imb) and imb > 0.15):
        return "ASK_DEPLETION"
    if _finite(imb) and imb > 0.2:
        return "BID_PRESSURE"
    if _finite(imb) and imb < -0.2:
        return "ASK_PRESSURE"
    return "BALANCED"


def classify_activity_state(row: Mapping[str, Any]) -> str:
    board_ups = fnum(row.get("board_60s_updates_per_sec") or row.get("board_300s_updates_per_sec"), 0.0)
    px_ups = fnum(row.get("pre_60s_current_price_update_count"), 0.0)
    ratio = fnum(row.get("board_60s_board_price_update_ratio"), float("nan"))
    burst = fnum(row.get("pre_60s_volume_burst"), 0.0) >= 1.0
    if burst and board_ups >= 0.5 and px_ups >= 2:
        return "BURST"
    if board_ups >= 0.3 and px_ups >= 2:
        return "ACTIVE_PRICE_AND_BOARD"
    if board_ups >= 0.3 and px_ups <= 1:
        return "BOARD_ONLY"
    if px_ups >= 2 and board_ups < 0.15:
        return "PRICE_ONLY"
    if _finite(ratio) and ratio >= 10 and px_ups <= 1:
        return "BOARD_ONLY"
    return "LOW_ACTIVITY"


def classify_pbv2_state(row: Mapping[str, Any]) -> str:
    score = fnum(row.get("score_v2"))
    mom = fnum(row.get("momentum"))
    imb_p = fnum(row.get("entry_imbalance_percentile"))
    parts = []
    if _finite(score):
        parts.append("SCORE4+" if score >= 4 else ("SCORE3" if score >= 3 else "SCORE_LT3"))
    if _finite(imb_p):
        if imb_p >= 0.7:
            parts.append("BOARD_HIGH")
        elif imb_p >= 0.4:
            parts.append("BOARD_MID")
        else:
            parts.append("BOARD_LOW")
    if _finite(mom):
        parts.append("MOMENTUM_MID" if mom >= 0.5 else "MOMENTUM_LOW")
    return "|".join(parts) if parts else "PBV2_UNKNOWN"


def coarse_combo_id(price: str, volume: str, board: str, activity: str, pbv2: str) -> str:
    """Coarse combination to avoid 1-sample clusters."""
    p = {
        "RISE": "RISE",
        "PULLBACK_RECOVERY": "RISE",
        "LATE_CHASE": "CHASE",
        "FAILED_BREAKOUT": "FAIL",
        "FALL": "FALL",
        "FLAT": "FLAT",
        "QUOTE_ONLY_STALE": "STALE",
    }.get(price, "FLAT")
    v = {
        "SURGE": "SURGE",
        "RISING": "RISING",
        "NORMAL": "NORMAL",
        "DRY": "DRY",
        "NO_CONFIRMATION": "NOCONF",
    }.get(volume, "NORMAL")
    b = {
        "BID_PRESSURE": "BID",
        "ASK_DEPLETION": "BID",
        "ASK_PRESSURE": "ASK",
        "WALL_BLOCKED": "WALL",
        "QUOTE_ONLY_CHURN": "CHURN",
        "BALANCED": "BAL",
    }.get(board, "BAL")
    a = {
        "ACTIVE_PRICE_AND_BOARD": "ACTIVE",
        "BURST": "ACTIVE",
        "BOARD_ONLY": "BOARD_ONLY",
        "PRICE_ONLY": "PRICE_ONLY",
        "LOW_ACTIVITY": "LOW",
    }.get(activity, "LOW")
    s = "S4" if "SCORE4" in pbv2 else ("S3" if "SCORE3" in pbv2 else "S0")
    return f"P={p}|V={v}|B={b}|A={a}|S={s}"


def interpretable_state_label(price: str, volume: str, board: str, activity: str) -> str:
    """Map to <=8 human-readable market states."""
    if price == "QUOTE_ONLY_STALE" or (activity == "BOARD_ONLY" and price in ("FLAT", "QUOTE_ONLY_STALE")):
        return "STALE_PRICE_QUOTE_CHURN"
    if price == "FAILED_BREAKOUT" and board in ("ASK_PRESSURE", "WALL_BLOCKED"):
        return "FAILED_HIGH_ASK_PRESSURE"
    if price in ("RISE", "PULLBACK_RECOVERY") and volume in ("SURGE", "RISING") and board in (
        "BID_PRESSURE",
        "ASK_DEPLETION",
        "BALANCED",
    ):
        return "RISE_VOLUME_BOARD_CONFIRM"
    if price == "PULLBACK_RECOVERY":
        return "PULLBACK_RECOVERY_COMPRESS"
    if price == "LATE_CHASE":
        return "LATE_CHASE"
    if price == "FALL" or board == "ASK_PRESSURE":
        return "FALL_OR_ASK_PRESSURE"
    if activity == "BOARD_ONLY" or board == "QUOTE_ONLY_CHURN":
        return "FLAT_BOARD_ONLY_ACTIVE"
    return "MIXED_OR_LOW_SIGNAL"


def build_market_state_row(
    base: Mapping[str, Any],
    board_df: pd.DataFrame,
    np_row: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    ee = float(base.get("entry_epoch") or float("nan"))
    ep = float(base.get("entry_price") or float("nan"))
    row: dict[str, Any] = dict(base)
    np_row = np_row or {}
    for k, v in np_row.items():
        if str(k).startswith("np_"):
            row[k] = v

    # board window dynamics already on board_entry dataset for <=300s; add 600s if board present
    if board_df is not None and len(board_df):
        for w in PRE_WINDOWS:
            pv = price_volume_window_features(board_df, entry_epoch=ee, entry_price=ep, window_sec=float(w))
            row.update(pv)
            if w == 600:
                wdf = board_df[(board_df["recv_epoch"] > ee - w) & (board_df["recv_epoch"] <= ee)]
                dyn = window_dynamics(wdf) if len(wdf) else {}
                for k, v in dyn.items():
                    row[f"board_{w}s_{k}"] = v
        # post outcomes from capture (overwrite if present)
        from research.board_entry_features import mfe_mae_horizon, post_return

        for w in POST_WINDOWS:
            row[f"post_return_{w}s"] = post_return(board_df, ee, ep, float(w))
        mfe5, mae5 = mfe_mae_horizon(board_df, ee, ep, 300.0)
        row["MFE_5m"] = mfe5
        row["MAE_5m"] = mae5
    else:
        for w in PRE_WINDOWS:
            row[f"pre_{w}s_coverage_ok"] = False
            row[f"pre_{w}s_volume_source"] = "missing_capture"

    # Prefer board_entry precomputed board cols already merged into base
    price_s = classify_price_state(row)
    vol_s = classify_volume_state(row)
    board_s = classify_board_state(row)
    act_s = classify_activity_state(row)
    pbv2_s = classify_pbv2_state(row)
    row["PRICE_STATE"] = price_s
    row["VOLUME_STATE"] = vol_s
    row["BOARD_STATE"] = board_s
    row["ACTIVITY_STATE"] = act_s
    row["PBV2_STATE"] = pbv2_s
    row["STATE_COMBO_ID"] = coarse_combo_id(price_s, vol_s, board_s, act_s, pbv2_s)
    row["INTERPRETABLE_STATE"] = interpretable_state_label(price_s, vol_s, board_s, act_s)

    # coverage flags
    row["price_window_sync_ok"] = bool(row.get("pre_300s_coverage_ok"))
    row["volume_window_sync_ok"] = bool(
        row.get("pre_300s_coverage_ok") and fnum(row.get("pre_300s_volume_missing"), 1.0) < 0.5
    )
    row["board_window_sync_ok"] = bool(row.get("board_sync_ok"))
    row["all_feature_sync_ok"] = bool(
        row["price_window_sync_ok"] and row["volume_window_sync_ok"] and row["board_window_sync_ok"]
    )
    row["open_window_shortfall_600s"] = not bool(row.get("pre_600s_coverage_ok"))
    row["future_leak_feature"] = False
    row["volume_source"] = "capture_trading_volume+np_tv_chg_pct"
    return row


SAME_PRICE_REL_EPS = 1e-6  # relative price equality vs prev exit
SAME_PRICE_REENTRY_MAX_GAP_SEC = 10.0


def annotate_prev_exit_gaps(df: pd.DataFrame) -> pd.DataFrame:
    """Add prev-EXIT based reENTRY gap fields (per symbol, chronological).

    Also keeps gap_sec_from_prev_entry (ENTRY-to-ENTRY) for comparison.
    Flags same_price_reentry_after_exit when gap_sec_from_prev_exit <= 10s
    and entry price matches prev exit price.
    """
    if df is None or len(df) == 0:
        return df
    out = df.copy()
    out["prev_exit_time"] = pd.NA
    out["gap_sec_from_prev_exit"] = np.nan
    out["prev_exit_price"] = np.nan
    out["same_price_vs_prev_exit"] = False
    out["prev_exit_reason"] = pd.NA
    out["gap_sec_from_prev_entry"] = np.nan
    out["same_price_reentry_after_exit"] = False
    out["same_price_reentry_note"] = ""

    def _ts(v: Any) -> Optional[datetime]:
        return parse_ts(v)

    for sym, idx in out.groupby(out["symbol"].astype(str), sort=False).groups.items():
        sub = out.loc[list(idx)].sort_values("entry_time")
        prev_exit_t = None
        prev_exit_px = None
        prev_exit_reason = None
        prev_entry_t = None
        for i in sub.index:
            et = _ts(out.at[i, "entry_time"])
            ep = fnum(out.at[i, "entry_price"])
            if prev_entry_t is not None and et is not None:
                out.at[i, "gap_sec_from_prev_entry"] = (et - prev_entry_t).total_seconds()
            if prev_exit_t is not None and et is not None:
                gap = (et - prev_exit_t).total_seconds()
                out.at[i, "prev_exit_time"] = prev_exit_t.isoformat()
                out.at[i, "gap_sec_from_prev_exit"] = gap
                out.at[i, "prev_exit_price"] = prev_exit_px
                out.at[i, "prev_exit_reason"] = prev_exit_reason
                same = False
                if _finite(ep) and _finite(prev_exit_px) and prev_exit_px and prev_exit_px > 0:
                    same = abs(ep - float(prev_exit_px)) / float(prev_exit_px) <= SAME_PRICE_REL_EPS or abs(
                        ep - float(prev_exit_px)
                    ) < 1e-9
                out.at[i, "same_price_vs_prev_exit"] = bool(same)
                if same and gap <= SAME_PRICE_REENTRY_MAX_GAP_SEC:
                    out.at[i, "same_price_reentry_after_exit"] = True
                    out.at[i, "same_price_reentry_note"] = (
                        f"reENTRY {gap:.0f}s after prev EXIT @ same price "
                        f"(prev_exit_reason={prev_exit_reason})"
                    )
            # advance prev exit/entry from this row's own exit
            xt = _ts(out.at[i, "exit_time"])
            xp = fnum(out.at[i, "exit_price"])
            if not _finite(xp):
                # fall back: structural may leave exit_price; try pnl-implied skip
                xp = ep
            prev_exit_t = xt
            prev_exit_px = xp if _finite(xp) else prev_exit_px
            prev_exit_reason = str(out.at[i, "exit_reason"] or "")
            prev_entry_t = et
    return out


def stream_slim_board_extended(
    *,
    capture_dir: Path,
    entries: list[dict[str, Any]],
    cache_dir: Path,
    pre_lookback_sec: float = 620.0,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Like stream_slim_board but with longer pre-lookback for 600s windows."""
    if not entries:
        return pd.DataFrame(), {"rows": 0}
    # Temporarily patch epochs via wrapper: reuse stream by adjusting entry_epoch copies
    adj = []
    for e in entries:
        d = dict(e)
        # stream_slim uses min(entry)-320; we shift entry_epoch earlier conceptually by
        # increasing lookback via fake earlier entries is hacky — instead call extract with custom t0.
        adj.append(d)
    symbols = sorted({e["symbol_code"] for e in entries})
    t0 = min(e["entry_epoch"] for e in entries) - pre_lookback_sec
    t1 = max(max(e["exit_epoch"], e["entry_epoch"] + 620) for e in entries) + 5
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from research.board_entry_features import extract_part

    parts = [p for p in sorted(capture_dir.glob("push_part_*.jsonl")) if p.stat().st_size > 0]
    cache_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(str(p), symbols, t0, t1, str(cache_dir / f"slim_{i:02d}.parquet")) for i, p in enumerate(parts)]
    stats: list[dict[str, Any]] = []
    if jobs:
        with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = [ex.submit(extract_part, j) for j in jobs]
            for fut in as_completed(futs):
                stats.append(fut.result())
    frames = []
    for s in stats:
        op = s.get("out")
        if op and Path(op).is_file():
            df = pd.read_parquet(op)
            if len(df):
                frames.append(df)
    board = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(board):
        board = board.sort_values(["symbol", "recv_epoch", "sequence"]).drop_duplicates(
            ["symbol", "payload_hash", "recv_epoch"], keep="first"
        )
    return board, {
        "rows": int(len(board)),
        "parts_processed": len(stats),
        "dups_skipped": sum(int(s.get("dups_skipped") or 0) for s in stats),
        "t0": t0,
        "t1": t1,
        "pre_lookback_sec": pre_lookback_sec,
    }


def build_session_market_state_df(
    *,
    session_dir: Path,
    capture_dir: Path,
    trading_date: str,
    session_kind: str,
    session_id: str,
    board_entry_df: Optional[pd.DataFrame] = None,
    cache_dir: Optional[Path] = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    entries = load_accepted_entries(session_dir)
    np_map = load_np_pre_entry(session_dir)
    quality: dict[str, Any] = {
        "trading_date": trading_date,
        "session_id": session_id,
        "n_entries": len(entries),
        "volume_source": "capture_trading_volume + np_tv_chg_pct_*",
        "max_workers": MAX_WORKERS,
        "capture_copied": False,
    }
    if not entries:
        return pd.DataFrame(), {**quality, "status": "NO_ENTRIES"}

    # Merge board_entry dataset columns if provided
    be_map: dict[tuple[str, str], dict[str, Any]] = {}
    if board_entry_df is not None and len(board_entry_df):
        for _, r in board_entry_df.iterrows():
            be_map[(str(r.get("symbol")), str(r.get("entry_time")))] = r.to_dict()

    own_cache = cache_dir is None
    if cache_dir is None:
        cache_dir = Path(tempfile.mkdtemp(prefix="ms_slim_"))
    try:
        board, slim_stats = stream_slim_board_extended(
            capture_dir=capture_dir, entries=entries, cache_dir=cache_dir, pre_lookback_sec=620.0
        )
        quality["slim"] = {k: slim_stats.get(k) for k in ("rows", "parts_processed", "dups_skipped", "t0", "t1")}
        by_sym = (
            {s: g.sort_values("recv_epoch").reset_index(drop=True) for s, g in board.groupby("symbol")}
            if len(board)
            else {}
        )
        rows = []
        for e in entries:
            key = (str(e["symbol"]), str(e["entry_time"]))
            base = {**e}
            if key in be_map:
                # board features from dataset; do not overwrite identity/outcome keys lightly
                for k, v in be_map[key].items():
                    if k not in base or base.get(k) is None or (isinstance(base.get(k), float) and base.get(k) != base.get(k)):
                        base[k] = v
                    elif str(k).startswith("board_") or str(k).startswith("label_") or str(k).startswith("return_"):
                        base[k] = v
            base["trading_date"] = trading_date
            base["session_kind"] = session_kind
            base["session_id"] = session_id
            sdf = by_sym.get(e["symbol_code"], pd.DataFrame())
            rows.append(build_market_state_row(base, sdf, np_map.get(key)))
        df = annotate_prev_exit_gaps(pd.DataFrame(rows))
        quality.update(
            {
                "status": "OK",
                "price_sync_ok": int(df["price_window_sync_ok"].sum()) if len(df) else 0,
                "volume_sync_ok": int(df["volume_window_sync_ok"].sum()) if len(df) else 0,
                "board_sync_ok": int(df["board_window_sync_ok"].sum()) if len(df) else 0,
                "all_sync_ok": int(df["all_feature_sync_ok"].sum()) if len(df) else 0,
                "open_shortfall_600s": int(df["open_window_shortfall_600s"].sum()) if len(df) else 0,
                "volume_missing_rate": float(df["pre_300s_volume_missing"].mean()) if "pre_300s_volume_missing" in df else 1.0,
                "same_price_reentry_after_exit_n": int(df["same_price_reentry_after_exit"].astype(bool).sum())
                if len(df)
                else 0,
            }
        )
        return df, quality
    finally:
        if own_cache and cache_dir is not None and Path(cache_dir).exists():
            shutil.rmtree(cache_dir, ignore_errors=True)


def append_session_market_state(
    *,
    native_root: Path,
    session_dir: Path,
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail-open post-seal append into results/research/pre_entry_market_state/."""
    session_dir = Path(session_dir)
    meta = detect_session_meta(session_dir, summary)
    eligible, why = is_eligible(meta)
    if not eligible:
        return {"status": "SKIP", "reason": why}
    trading_date = str(meta["trading_date"])
    session_id = str(meta["session_id"])
    sk = str(meta["session_kind"])
    root = dataset_root(native_root)
    root.mkdir(parents=True, exist_ok=True)
    man = load_manifest(root)
    sess_key = f"{trading_date}|{sk}|{session_id}"
    if sess_key in (man.get("sessions") or {}):
        return {"status": "ALREADY_PRESENT", "session_key": sess_key}

    capture_dir = native_root / "data" / "market_capture" / trading_date
    be_path = (
        native_root
        / "results"
        / "research"
        / "board_entry_dataset"
        / f"trading_date={trading_date}"
        / "entries.parquet"
    )
    be_df = pd.read_parquet(be_path) if be_path.is_file() else None
    if be_df is not None and "session_id" in be_df.columns:
        be_df = be_df[be_df["session_id"].astype(str) == session_id]

    df, quality = build_session_market_state_df(
        session_dir=session_dir,
        capture_dir=capture_dir,
        trading_date=trading_date,
        session_kind=sk,
        session_id=session_id,
        board_entry_df=be_df,
    )
    part = partition_dir(root, trading_date)
    part.mkdir(parents=True, exist_ok=True)
    out_pq = part / "market_state_entries.parquet"
    # append-safe: concat if exists for other sessions same day
    if out_pq.is_file():
        prev = pd.read_parquet(out_pq)
        if "session_id" in prev.columns:
            prev = prev[prev["session_id"].astype(str) != session_id]
        df = pd.concat([prev, df], ignore_index=True) if len(df) else prev
    if len(df):
        df.to_parquet(out_pq, index=False)
    (part / "state_quality.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )

    man.setdefault("sessions", {})[sess_key] = {
        "trading_date": trading_date,
        "session_id": session_id,
        "n_entries": int(quality.get("n_entries") or 0),
        "status": quality.get("status"),
        "ingested_at": datetime.now(JST).isoformat(),
    }
    dates = sorted(set(man.get("trading_dates") or []) | {trading_date})
    man["trading_dates"] = dates
    man["n_trading_days"] = len(dates)
    gate = None
    for d, name in sorted(REANALYSIS_GATES.items()):
        if len(dates) >= d:
            gate = {"days": d, "action": name}
    man["latest_reanalysis_gate"] = gate
    man["adoption_allowed"] = False  # never before 5 days; still research-only
    save_manifest(root, man)

    # summary csv row
    _append_summary_row(
        root,
        {
            "trading_date": trading_date,
            "session_id": session_id,
            "n_entries": quality.get("n_entries"),
            "price_sync_ok": quality.get("price_sync_ok"),
            "volume_sync_ok": quality.get("volume_sync_ok"),
            "board_sync_ok": quality.get("board_sync_ok"),
            "all_sync_ok": quality.get("all_sync_ok"),
            "volume_missing_rate": quality.get("volume_missing_rate"),
            "status": quality.get("status"),
        },
    )
    return {"status": "INGESTED", "session_key": sess_key, "quality": quality, "path": str(out_pq)}


def _append_summary_row(root: Path, row: dict[str, Any]) -> None:
    path = summary_csv_path(root)
    rows = []
    if path.is_file():
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    rows = [r for r in rows if not (r.get("trading_date") == row.get("trading_date") and r.get("session_id") == row.get("session_id"))]
    rows.append({k: ("" if v is None else v) for k, v in row.items()})
    cols = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def maybe_append_session_market_state(
    *,
    native_root: Path,
    session_dir: Path,
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        return append_session_market_state(
            native_root=Path(native_root), session_dir=Path(session_dir), summary=summary
        )
    except Exception as exc:
        log.warning("pre_entry_market_state append failed: %s", exc)
        return {"status": "ERROR", "error": str(exc)}


def feature_dictionary() -> list[dict[str, str]]:
    rows = []
    for w in PRE_WINDOWS:
        for name, desc in [
            ("return", "pre-entry return over window"),
            ("slope", "normalized price slope"),
            ("max_rise", "max rise from running low"),
            ("max_drawdown", "max drawdown from running high"),
            ("bounce_from_recent_low", "entry vs window low"),
            ("fall_from_recent_high", "window high vs entry"),
            ("volume_delta", "TradingVolume end-start"),
            ("trading_value_delta", "TradingValue end-start"),
            ("realized_vol", "log-return realized vol"),
            ("vwap_deviation", "price vs window VWAP proxy"),
            ("same_price_duration_sec", "longest flat price run"),
            ("current_price_update_count", "CurrentPrice change count"),
            ("coverage_ok", "span >= 50% of window"),
        ]:
            rows.append(
                {
                    "feature": f"pre_{w}s_{name}",
                    "family": "price_volume",
                    "window_sec": str(w),
                    "leak_safe": "true",
                    "description": desc,
                }
            )
    for fam, feats in [
        ("board", ["imbalance_l5", "ofi_proxy", "same_price_board_churn", "updates_per_sec", "board_price_update_ratio"]),
        ("state", ["PRICE_STATE", "VOLUME_STATE", "BOARD_STATE", "ACTIVITY_STATE", "PBV2_STATE", "STATE_COMBO_ID", "INTERPRETABLE_STATE"]),
        ("pbv2", ["score_v2", "momentum", "entry_imbalance_percentile", "route", "quality", "spread_bps", "update_count"]),
        ("outcome", ["pnl_pct", "exit_reason", "MFE_5m", "MAE_5m", "post_return_30s", "label_winner", "label_stop", "label_no_progress"]),
    ]:
        for f in feats:
            rows.append({"feature": f, "family": fam, "window_sec": "", "leak_safe": "true" if fam != "outcome" else "outcome_only", "description": f})
    return rows
