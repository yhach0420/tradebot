"""Per-mask Discovery path metrics (SELECTED only; no Evaluation)."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from . import DISCOVERY, HORIZONS, UPSIDE_LEVELS


def _q(arr: np.ndarray, q: float) -> Optional[float]:
    a = arr[np.isfinite(arr)]
    if a.size == 0:
        return None
    return float(np.quantile(a, q))


def _median(arr: np.ndarray) -> Optional[float]:
    return _q(arr, 0.5)


def discovery_selected_metrics(
    *,
    selected: np.ndarray,
    metrics: dict[str, np.ndarray],
    dates: np.ndarray,
    symbols: np.ndarray,
    path_ok: np.ndarray,
) -> dict[str, Any]:
    """Compute Discovery SELECTED path stats for one unique mask."""
    disc = np.isin(dates, list(DISCOVERY))
    base = disc & selected & path_ok
    idx = np.where(base)[0]
    n = int(idx.size)
    days = int(np.unique(dates[idx]).size) if n else 0
    syms = int(np.unique(symbols[idx]).size) if n else 0
    out: dict[str, Any] = {
        "selected_anchors": n,
        "days": days,
        "symbols": syms,
        "support_ok": n >= 20 and days >= 3 and syms >= 5,
    }

    for h in HORIZONS:
        key = f"{h}s"
        elig = base & metrics.get(f"eligible_{key}", np.ones(len(dates), dtype=bool))
        mfe = metrics[f"MFE_{key}_bps"][elig]
        mae = metrics[f"MAE_{key}_bps"][elig]
        max_gb = metrics[f"max_giveback_after_MFE_{key}_bps"][elig]
        term_gb = metrics[f"terminal_giveback_from_MFE_{key}_bps"][elig]
        out[f"MFE_{h}_q25"] = _q(mfe, 0.25)
        out[f"MFE_{h}_q50"] = _q(mfe, 0.50)
        out[f"MFE_{h}_q75"] = _q(mfe, 0.75)
        out[f"MAE_{h}_q25"] = _q(mae, 0.25)
        out[f"MAE_{h}_q50"] = _q(mae, 0.50)
        out[f"MAE_{h}_q75"] = _q(mae, 0.75)
        out[f"max_giveback_{h}_q25"] = _q(max_gb, 0.25)
        out[f"max_giveback_{h}_q50"] = _q(max_gb, 0.50)
        out[f"terminal_giveback_{h}_q50"] = _q(term_gb, 0.50)
        out[f"n_eligible_{h}"] = int(elig.sum())

    for up in UPSIDE_LEVELS:
        reached = base & metrics[f"up_{up}_reached"]
        ridx = np.where(reached)[0]
        rn = int(ridx.size)
        rdays = int(np.unique(dates[ridx]).size) if rn else 0
        times = metrics[f"up_{up}_time_sec"][reached]
        pre = metrics[f"pre_reach_MAE_{up}_bps"][reached]
        # pre-rise MAE is typically negative; absolute for stop sizing
        pre_abs = np.abs(pre)
        denom = int(base.sum()) if n else 0
        out[f"up_{up}_reach_rate"] = (rn / denom) if denom else None
        out[f"up_{up}_reached_n"] = rn
        out[f"up_{up}_reached_days"] = rdays
        out[f"up_{up}_time_q50"] = _q(times, 0.50)
        out[f"up_{up}_time_q75"] = _q(times, 0.75)
        out[f"pre_rise_MAE_{up}_q50"] = _q(pre, 0.50)
        out[f"pre_rise_MAE_abs_{up}_q75"] = _q(pre_abs, 0.75)
        out[f"up_{up}_metric_support_ok"] = rn >= 10 and rdays >= 3

    return out
