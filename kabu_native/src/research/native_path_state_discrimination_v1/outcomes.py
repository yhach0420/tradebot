"""Path-type labels from forward geometry. Outcome only. Never an ENTRY feature."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.native_path_state_discrimination_v1 import (
    FAILURE_CLASSES,
    FAVOR_BPS,
    FAVORABLE_CLASSES,
    LARGE_WINNER_BPS,
    PATH_BARS,
    TIME_STOP_MIN,
)


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _bps(exit_px: Any, entry_px: Any) -> float | None:
    if not _finite(exit_px) or not _finite(entry_px) or float(entry_px) == 0:
        return None
    return float((float(exit_px) / float(entry_px) - 1.0) * 10_000.0)


def attach_fwd(ep: dict[str, Any], rec: dict[str, Any], snaps: dict[str, dict[str, Any]]) -> None:
    from research.cause_first_mechanism_discovery_v1.clock import interval_crosses_lunch

    entry_t = str(ep.get("event_time") or "")
    ie = rec["idx"].get(entry_t)
    px = rec["o"][ie] if ie is not None else float("nan")
    ep["x0_entry_open"] = float(px) if ie is not None and np.isfinite(px) else None
    fwd = []
    mfe = mae = None
    mfe_i = mae_i = None
    sec = str(ep.get("sector") or "")
    if ie is not None and np.isfinite(px) and px > 0:
        last = min(ie + PATH_BARS, len(rec["t"]) - 1)
        for k in range(ie, last + 1):
            hh = rec["t"][k]
            if interval_crosses_lunch(entry_t, hh):
                break
            st = (snaps.get(hh) or {}).get("sectors", {}).get(sec) or {}
            cl = rec["c"][k]
            vw = rec["vw"][k]
            above_vw = bool(np.isfinite(vw) and np.isfinite(cl) and cl > vw)
            r1 = rec["r1"][k]
            sec_r1 = st.get("r1")
            rs1 = (float(r1) - float(sec_r1)) if _finite(r1) and _finite(sec_r1) else float("nan")
            fwd.append(
                (
                    hh,
                    float(rec["o"][k]),
                    float(rec["h"][k]),
                    float(rec["l"][k]),
                    float(cl) if np.isfinite(cl) else float("nan"),
                    float(vw) if np.isfinite(vw) else None,
                    above_vw,
                    float(ep.get("hi20") or np.nan),
                    float(sec_r1) if _finite(sec_r1) else float("nan"),
                    rs1,
                )
            )
            hbps = _bps(rec["h"][k], px)
            lbps = _bps(rec["l"][k], px)
            step = k - ie
            if hbps is not None and (mfe is None or hbps > mfe):
                mfe, mfe_i = hbps, step
            if lbps is not None and (mae is None or lbps < mae):
                mae, mae_i = lbps, step
    ep["fwd_bars"] = fwd
    ep["mfe_bps"] = mfe
    ep["mae_bps"] = mae
    ep["time_to_mfe_min"] = mfe_i
    ep["time_to_mae_min"] = mae_i
    ep["outcomes_attached"] = True
    ep["outcomes_are_labels_only"] = True


def realized_x20(ep: dict[str, Any]) -> float | None:
    px = ep.get("x0_entry_open")
    fwd = list(ep.get("fwd_bars") or [])
    if not fwd or not _finite(px) or float(px) <= 0:
        return None
    hold_i = min(len(fwd) - 1, TIME_STOP_MIN)
    return _bps(fwd[hold_i][4], px)


def classify_path(ep: dict[str, Any]) -> str:
    """RCA path types using frozen 20m time-stop close as retained-path analog of trade x0."""
    mfe = ep.get("mfe_bps")
    mae = ep.get("mae_bps")
    if mfe is None or mae is None:
        return "UNLABELED"
    mfe_v = float(mfe)
    mae_v = float(mae)
    mfe_i = ep.get("time_to_mfe_min")
    mae_i = ep.get("time_to_mae_min")
    x20 = realized_x20(ep)
    fwd = list(ep.get("fwd_bars") or [])
    hold_i = min(max(len(fwd) - 1, 0), TIME_STOP_MIN)
    above_vw = sum(1 for row in fwd[: hold_i + 1] if row[6] is True)
    if abs(mfe_v) < FAVOR_BPS and abs(mae_v) < FAVOR_BPS:
        return "STALL"
    if mae_v <= -FAVOR_BPS and mae_i is not None and int(mae_i) <= 1 and mfe_v < FAVOR_BPS:
        return "IMMEDIATE_FAILURE"
    if (mfe_i is None or mfe_v < FAVOR_BPS) and mae_i is not None and int(mae_i) <= 1 and mae_v <= -FAVOR_BPS:
        return "IMMEDIATE_FAILURE"
    if x20 is not None and x20 >= LARGE_WINNER_BPS:
        return "LARGE_WINNER"
    if mfe_v >= FAVOR_BPS and (x20 is None or x20 <= 0):
        return "SMALL_PROGRESS_THEN_FAILURE"
    if x20 is not None and x20 > 0 and above_vw >= 5:
        return "SUSTAINED_CONTINUATION"
    if x20 is not None and x20 > 0:
        return "OTHER"
    return "OTHER"


def label_row(ep: dict[str, Any]) -> None:
    cls = classify_path(ep)
    ep["path_type"] = cls
    if cls in FAVORABLE_CLASSES:
        ep["y"] = 1
        ep["in_contrast"] = True
    elif cls in FAILURE_CLASSES:
        ep["y"] = 0
        ep["in_contrast"] = True
    else:
        ep["y"] = None
        ep["in_contrast"] = False
    ep["x20_label_only_bps"] = realized_x20(ep)
