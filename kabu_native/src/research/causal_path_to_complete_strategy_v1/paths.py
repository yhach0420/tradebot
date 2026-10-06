"""Attach outcome-only forward path labels after event identities exist. Never used as decision features."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, interval_crosses_lunch
from research.causal_path_to_complete_strategy_v1 import X1_TAX_BPS

HORIZONS = (1, 3, 5, 10, 15, 30)
PATH_BARS = 30
TAXONOMY = (
    "IMMEDIATE_CONTINUATION",
    "PULLBACK_THEN_CONTINUATION",
    "BREAKOUT_THEN_FAILURE",
    "PROFIT_THEN_FULL_GIVEBACK",
    "IMMEDIATE_FAILURE",
    "MEAN_REVERSION",
    "STAGNANT_OR_AMBIGUOUS",
    "UNLABELED",
)


def _bps(exit_px: float, entry_px: float) -> float | None:
    if not np.isfinite(exit_px) or not np.isfinite(entry_px) or entry_px == 0:
        return None
    return float((exit_px / entry_px - 1.0) * 10_000.0)


def attach_forward_paths(events: list[dict[str, Any]]) -> None:
    for e in events:
        rec = e.get("rec")
        if rec is None:
            e["outcomes_attached"] = False
            e["path_taxonomy"] = "UNLABELED"
            continue
        entry_t = str(e["event_time"])
        ie = rec["idx"].get(entry_t)
        px = rec["o"][ie] if ie is not None else float("nan")
        e["x0_entry_open"] = float(px) if ie is not None and np.isfinite(px) else None
        e["x0_entry_open_available"] = bool(ie is not None and np.isfinite(px) and px > 0)
        feature_bar = str(e["feature_bar"])
        for n in HORIZONS:
            mark = hhmm_add(feature_bar, n)
            key = f"ret_p{n}m_bps"
            e[key] = None
            if not mark or not np.isfinite(px) or px == 0:
                continue
            if interval_crosses_lunch(entry_t, mark):
                continue
            j = rec["idx"].get(mark)
            if j is None:
                continue
            cl = rec["c"][j]
            e[key] = _bps(cl, px)
        if e.get("ret_p15m_bps") is not None:
            e["x0_h15_bps"] = e["ret_p15m_bps"]
            e["x1_h15_bps"] = float(e["ret_p15m_bps"]) - float(X1_TAX_BPS)
        else:
            e["x0_h15_bps"] = None
            e["x1_h15_bps"] = None

        fwd = []
        mfe = None
        mae = None
        ttmfe = None
        ttmae = None
        mfe_i = None
        mae_i = None
        if ie is not None and np.isfinite(px) and px > 0:
            last = min(ie + PATH_BARS, len(rec["t"]) - 1)
            for k in range(ie, last + 1):
                hh = rec["t"][k]
                if interval_crosses_lunch(entry_t, hh):
                    break
                hi, lo, cl = rec["h"][k], rec["l"][k], rec["c"][k]
                vw = rec["vw"][k]
                e9, e21 = rec["e9"][k], rec["e21"][k]
                ema_ok = bool(np.isfinite(e9) and np.isfinite(e21) and e9 > e21)
                above_vw = bool(np.isfinite(vw) and np.isfinite(cl) and cl > vw)
                fwd.append((hh, float(rec["o"][k]), float(hi), float(lo), float(cl), float(vw) if np.isfinite(vw) else None, ema_ok, above_vw))
                hbps = _bps(hi, px)
                lbps = _bps(lo, px)
                step = k - ie
                if hbps is not None and (mfe is None or hbps > mfe):
                    mfe, ttmfe, mfe_i = hbps, step, step
                if lbps is not None and (mae is None or lbps < mae):
                    mae, ttmae, mae_i = lbps, step, step
        e["fwd_bars"] = fwd
        e["mfe_bps"] = mfe
        e["mae_bps"] = mae
        e["time_to_mfe_min"] = ttmfe
        e["time_to_mae_min"] = ttmae
        e["mfe_before_mae"] = bool(mfe_i is not None and mae_i is not None and mfe_i <= mae_i)
        e["path_quality_bps"] = (float(mfe) + float(mae)) if mfe is not None and mae is not None else None
        if e.get("ret_p5m_bps") is not None:
            e["initial_move_direction"] = 1 if e["ret_p5m_bps"] > 0 else (-1 if e["ret_p5m_bps"] < 0 else 0)
        else:
            e["initial_move_direction"] = None
        later = None
        if e.get("ret_p15m_bps") is not None and e.get("ret_p5m_bps") is not None:
            later = float(e["ret_p15m_bps"]) - float(e["ret_p5m_bps"])
        e["later_move_direction"] = 1 if later is not None and later > 0 else (-1 if later is not None and later < 0 else (0 if later is not None else None))
        e["reversal_after_initial_move"] = bool(
            e.get("initial_move_direction") in (1, -1)
            and e.get("later_move_direction") in (1, -1)
            and e["initial_move_direction"] != e["later_move_direction"]
        )
        voln = abs(float(e["ret_p5m_bps"])) if e.get("ret_p5m_bps") is not None else None
        e["mfe_vol_norm"] = (float(mfe) / voln) if mfe is not None and voln not in (None, 0) else None
        e["mae_vol_norm"] = (float(mae) / voln) if mae is not None and voln not in (None, 0) else None
        e["path_taxonomy"] = classify_path(e)
        e["outcomes_attached"] = True
        e["outcomes_are_labels_only"] = True


def classify_path(e: dict[str, Any]) -> str:
    mfe = e.get("mfe_bps")
    mae = e.get("mae_bps")
    if mfe is None or mae is None:
        return "UNLABELED"
    ttmfe = e.get("time_to_mfe_min")
    init = e.get("initial_move_direction")
    later = e.get("later_move_direction")
    if mae <= -10 and mfe < 8:
        return "IMMEDIATE_FAILURE"
    if mfe >= 12 and mae > -6 and ttmfe is not None and ttmfe <= 5:
        return "IMMEDIATE_CONTINUATION"
    if mfe >= 12 and ttmfe is not None and ttmfe > 5 and bool(e.get("mfe_before_mae")):
        return "PULLBACK_THEN_CONTINUATION"
    if mfe >= 10 and mae <= -8 and (float(mfe) + float(mae)) < 2:
        return "PROFIT_THEN_FULL_GIVEBACK"
    if mfe >= 8 and mae <= -10 and not bool(e.get("mfe_before_mae")):
        return "BREAKOUT_THEN_FAILURE"
    if init == -1 and later == 1:
        return "MEAN_REVERSION"
    if mfe < 6 and mae > -6:
        return "STAGNANT_OR_AMBIGUOUS"
    return "STAGNANT_OR_AMBIGUOUS"


def taxonomy_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in events:
        k = str(e.get("path_taxonomy") or "UNLABELED")
        out[k] = out.get(k, 0) + 1
    return out
