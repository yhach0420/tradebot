"""Forward labels + counterfactual EXIT (no future leak into features)."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from research.pbv2_zero_base_revalidation.constants import (
    CF_MAX_HOLD_SEC,
    CF_NO_PROGRESS_MFE_PCT,
    CF_NO_PROGRESS_SEC,
    CF_STOP_PCT,
    CF_TRAIL_ACTIVATE_PCT,
    CF_TRAIL_GIVEBACK,
    LARGE_RISE_MFE_10M_PCT,
    LARGE_RISE_MFE_15M_PCT,
    LARGE_RISE_MFE_5M_PCT,
)
from research.pbv2_zero_base_revalidation.panel import CandidateRow, PricePoint, price_window
from research.pbv2_zero_base_revalidation.util import pnl_5bps, yen100

MIN_CF_PATH_POINTS = 2
MIN_CF_SPAN_SEC = 30.0


def _mfe_mae(entry: float, path: Sequence[PricePoint]) -> tuple[Optional[float], Optional[float]]:
    if not path or entry <= 0:
        return None, None
    mfe = -1e18
    mae = 1e18
    for p in path:
        ret = (p.px - entry) / entry * 100.0
        if ret > mfe:
            mfe = ret
        if ret < mae:
            mae = ret
    if mfe == -1e18:
        return None, None
    return float(mfe), float(mae)


def _forward_return(entry: float, path: Sequence[PricePoint], horizon_sec: float) -> Optional[float]:
    if not path or entry <= 0:
        return None
    px = path[-1].px
    return round((px - entry) / entry * 100.0, 6)


def _path_usable_for_cf(path: Sequence[PricePoint]) -> bool:
    if len(path) < MIN_CF_PATH_POINTS:
        return False
    span = (path[-1].t - path[0].t).total_seconds()
    return span >= MIN_CF_SPAN_SEC


def counterfactual_exit(entry: float, path: Sequence[PricePoint]) -> dict[str, Any]:
    """Replay simplified mainline-like EXIT on observed path only."""
    if not path or entry <= 0 or not _path_usable_for_cf(path):
        return {
            "ok": False,
            "pnl": None,
            "pnl_5bps": None,
            "exit_reason": "PATH_INSUFFICIENT",
            "mfe": None,
            "mae": None,
            "trailing_activated": False,
            "hold_sec": None,
        }
    peak = entry
    trailing_on = False
    mfe = 0.0
    mae = 0.0
    t0 = path[0].t
    for p in path:
        ret = (p.px - entry) / entry * 100.0
        mfe = max(mfe, ret)
        mae = min(mae, ret)
        hold = (p.t - t0).total_seconds()
        if ret <= CF_STOP_PCT:
            return {
                "ok": True,
                "pnl": yen100(entry, p.px),
                "pnl_5bps": pnl_5bps(entry, p.px),
                "exit_reason": "stop_hit",
                "mfe": mfe,
                "mae": mae,
                "trailing_activated": trailing_on,
                "hold_sec": hold,
            }
        if p.px > peak:
            peak = p.px
        peak_ret = (peak - entry) / entry * 100.0
        if peak_ret >= CF_TRAIL_ACTIVATE_PCT:
            trailing_on = True
            if peak_ret > 0 and (peak_ret - ret) >= peak_ret * CF_TRAIL_GIVEBACK:
                return {
                    "ok": True,
                    "pnl": yen100(entry, p.px),
                    "pnl_5bps": pnl_5bps(entry, p.px),
                    "exit_reason": "trailing_mfe_exit",
                    "mfe": mfe,
                    "mae": mae,
                    "trailing_activated": True,
                    "hold_sec": hold,
                }
        if hold >= CF_NO_PROGRESS_SEC and mfe < CF_NO_PROGRESS_MFE_PCT and not trailing_on:
            return {
                "ok": True,
                "pnl": yen100(entry, p.px),
                "pnl_5bps": pnl_5bps(entry, p.px),
                "exit_reason": "no_progress_exit",
                "mfe": mfe,
                "mae": mae,
                "trailing_activated": False,
                "hold_sec": hold,
            }
        if hold >= CF_MAX_HOLD_SEC:
            return {
                "ok": True,
                "pnl": yen100(entry, p.px),
                "pnl_5bps": pnl_5bps(entry, p.px),
                "exit_reason": "max_hold",
                "mfe": mfe,
                "mae": mae,
                "trailing_activated": trailing_on,
                "hold_sec": hold,
            }
    last = path[-1]
    return {
        "ok": True,
        "pnl": yen100(entry, last.px),
        "pnl_5bps": pnl_5bps(entry, last.px),
        "exit_reason": "path_end",
        "mfe": mfe,
        "mae": mae,
        "trailing_activated": trailing_on,
        "hold_sec": (last.t - t0).total_seconds(),
    }


def attach_labels(
    panel: list[CandidateRow],
    price_paths: Mapping[tuple[str, str], list[PricePoint]],
    *,
    winner_q: float = 0.80,
) -> dict[str, Any]:
    """Fill forward labels. Features are never modified with future fields."""
    pnls_for_winner: list[float] = []
    counts = {
        "forward_return_evaluable": 0,
        "mfe_mae_evaluable": 0,
        "large_rise_evaluable": 0,
        "counterfactual_exit_evaluable": 0,
        "pnl_evaluable": 0,
        "outcome_evaluable": 0,
    }
    print(f"[pbv2_zb] attach_labels n={len(panel)}", flush=True)
    for i, row in enumerate(panel):
        if i and i % 20000 == 0:
            print(f"[pbv2_zb] labels {i}/{len(panel)}", flush=True)
        path_key = (row.day, row.symbol)
        full = price_paths.get(path_key) or []
        entry = row.current_price
        fwd: dict[str, Optional[float]] = {}
        for name, sec in (
            ("forward_return_30s", 30),
            ("forward_return_60s", 60),
            ("forward_return_120s", 120),
            ("forward_return_5m", 300),
            ("forward_return_10m", 600),
            ("forward_return_15m", 900),
        ):
            w = price_window(full, row.evaluation_time, sec)
            fwd[name] = _forward_return(entry, w, sec) if len(w) >= 2 else None
        for name, sec in (
            ("forward_MFE_5m", 300),
            ("forward_MAE_5m", 300),
            ("forward_MFE_15m", 900),
            ("forward_MAE_15m", 900),
        ):
            w = price_window(full, row.evaluation_time, sec)
            mfe, mae = _mfe_mae(entry, w) if len(w) >= 2 else (None, None)
            if "MFE" in name:
                fwd[name] = mfe
            else:
                fwd[name] = mae
        w10 = price_window(full, row.evaluation_time, 600)
        mfe10, mae10 = _mfe_mae(entry, w10) if len(w10) >= 2 else (None, None)
        fwd["forward_MFE_10m"] = mfe10
        fwd["forward_MAE_10m"] = mae10

        path15 = price_window(full, row.evaluation_time, CF_MAX_HOLD_SEC)
        cf = counterfactual_exit(entry, path15)
        row.forward = fwd

        row.forward_return_evaluable = any(
            fwd.get(k) is not None
            for k in (
                "forward_return_30s",
                "forward_return_60s",
                "forward_return_120s",
                "forward_return_5m",
                "forward_return_10m",
                "forward_return_15m",
            )
        )
        row.mfe_mae_evaluable = fwd.get("forward_MFE_5m") is not None and fwd.get("forward_MAE_5m") is not None
        row.large_rise_evaluable = any(
            fwd.get(k) is not None for k in ("forward_MFE_5m", "forward_MFE_10m", "forward_MFE_15m")
        )
        row.counterfactual_exit_evaluable = bool(cf.get("ok"))
        row.pnl_evaluable = False

        if cf["ok"] and cf["pnl"] is not None:
            row.cf_pnl = float(cf["pnl"])
            row.cf_pnl_5bps = float(cf["pnl_5bps"])
            row.cf_exit_reason = str(cf["exit_reason"])
            row.cf_hold_sec = float(cf["hold_sec"]) if cf.get("hold_sec") is not None else None
            row.pnl_evaluable = True
            pnls_for_winner.append(row.cf_pnl_5bps if row.cf_pnl_5bps is not None else row.cf_pnl)

        if row.accept and row.actual_pnl is not None:
            row.cf_pnl = row.actual_pnl
            row.cf_pnl_5bps = row.actual_pnl_5bps
            row.cf_exit_reason = row.actual_exit_reason or row.cf_exit_reason
            row.pnl_evaluable = True
            row.counterfactual_exit_evaluable = True

        if row.pnl_evaluable:
            row.evaluability = "PNL_EVALUABLE"
        elif row.forward_return_evaluable or row.mfe_mae_evaluable or row.large_rise_evaluable:
            row.evaluability = "OUTCOME_EVALUABLE"
        elif row.features:
            row.evaluability = "FEATURE_EVALUABLE"
        else:
            row.evaluability = "COVERAGE_ONLY"

        for key in (
            "forward_return_evaluable",
            "mfe_mae_evaluable",
            "large_rise_evaluable",
            "counterfactual_exit_evaluable",
            "pnl_evaluable",
        ):
            if getattr(row, key):
                counts[key] += 1
        if row.evaluability == "OUTCOME_EVALUABLE" or (
            row.forward_return_evaluable or row.mfe_mae_evaluable or row.large_rise_evaluable
        ):
            # outcome includes rows that have path outcomes even if also pnl-evaluable
            if row.forward_return_evaluable or row.mfe_mae_evaluable or row.large_rise_evaluable:
                counts["outcome_evaluable"] += 1

        mfe5 = fwd.get("forward_MFE_5m")
        mfe10v = fwd.get("forward_MFE_10m")
        mfe15 = fwd.get("forward_MFE_15m")
        trail = bool(cf.get("trailing_activated")) if cf.get("ok") else False
        if row.large_rise_evaluable:
            row.is_large_rise = bool(
                (mfe5 is not None and mfe5 >= LARGE_RISE_MFE_5M_PCT)
                or (mfe10v is not None and mfe10v >= LARGE_RISE_MFE_10M_PCT)
                or (mfe15 is not None and mfe15 >= LARGE_RISE_MFE_15M_PCT)
                or trail
            )
        else:
            row.is_large_rise = False

    import numpy as np

    thr = float(np.quantile(pnls_for_winner, winner_q)) if len(pnls_for_winner) >= 20 else 0.0
    for row in panel:
        reason = (row.cf_exit_reason or row.actual_exit_reason or "").lower()
        pnl = row.cf_pnl_5bps if row.cf_pnl_5bps is not None else row.cf_pnl
        is_stop = "stop" in reason
        is_np = "no_progress" in reason
        is_winner = bool(row.pnl_evaluable and pnl is not None and pnl >= thr)
        if is_stop:
            row.cohort = "STOP"
            row.is_stop = True
            row.is_np = False
            row.is_winner = False
        elif is_np:
            row.cohort = "NoProgress"
            row.is_np = True
            row.is_stop = False
            row.is_winner = False
        elif is_winner:
            row.cohort = "Winner"
            row.is_winner = True
            row.is_stop = False
            row.is_np = False
        else:
            row.cohort = "Normal"
            row.is_stop = False
            row.is_np = False
            row.is_winner = False

    # n_outcome_evaluable must reflect real outcome labels (not always 0 when pnl exists)
    return {
        "winner_threshold_global_ref": thr,
        "n_pnl_evaluable": counts["pnl_evaluable"],
        "n_outcome_evaluable": counts["outcome_evaluable"],
        "n_forward_return_evaluable": counts["forward_return_evaluable"],
        "n_mfe_mae_evaluable": counts["mfe_mae_evaluable"],
        "n_large_rise_evaluable": counts["large_rise_evaluable"],
        "n_counterfactual_exit_evaluable": counts["counterfactual_exit_evaluable"],
        "n_large_rise": sum(1 for r in panel if r.is_large_rise and r.large_rise_evaluable),
        "outcome_label_pass": counts["outcome_evaluable"] > 0 and counts["large_rise_evaluable"] > 0,
    }


def assert_no_future_in_features(row: CandidateRow) -> None:
    forbidden = (
        "forward_",
        "cf_pnl",
        "actual_pnl",
        "exit_price",
        "future_",
        "mfe_future",
    )
    for k in row.features:
        lk = k.lower()
        if any(f in lk for f in forbidden):
            raise AssertionError(f"future leak feature key: {k}")
