"""Adaptive noise band (no imputation of missing components)."""
from __future__ import annotations

from typing import Any, Sequence

from research.price_flow_exit.path_mfe import PathBar


def tick_size(px: float) -> float:
    if px >= 5000:
        return 1.0
    if px >= 1000:
        return 0.5
    if px >= 100:
        return 0.1
    return 0.05


def compute_noise_band(
    path: Sequence[PathBar],
    i: int,
    *,
    tick_mult: float,
    range_mult: float,
    spread_mult: float,
    lookback_sec: float = 30.0,
) -> dict[str, Any]:
    """noise_band = max(valid components).

    - tick_band always from price (tick table × multiplier)
    - spread_band only when ask > bid (crossed/missing → component NOT_EVALUABLE, omitted)
    - range_band from prior 15s/30s path bars (need ≥2 prior points; else omitted)
    Overall NOT_EVALUABLE only when no component can be formed (should not happen if px exists).
    Missing values are never imputed into synthetic spread/range.
    """
    b = path[i]
    px = float(b.px)
    ts = tick_size(px)
    tick_band = ts * tick_mult

    spread_band = None
    spread_status = "OK"
    if b.bid is not None and b.ask is not None and float(b.ask) > float(b.bid):
        spread_band = (float(b.ask) - float(b.bid)) * spread_mult
    else:
        spread_status = "NOT_EVALUABLE_SPREAD"

    def _range(window: float) -> float | None:
        t1 = b.t
        xs = []
        for j in range(i, -1, -1):
            if (t1 - path[j].t).total_seconds() > window:
                break
            if j == i:
                continue
            xs.append(path[j].px)
        if len(xs) < 2:
            return None
        return (max(xs) - min(xs)) * range_mult

    range_band = _range(lookback_sec)
    range_status = "OK"
    if range_band is None:
        range_band = _range(15.0)
    if range_band is None:
        range_status = "NOT_EVALUABLE_RANGE"

    comps = [tick_band]
    if spread_band is not None:
        comps.append(spread_band)
    if range_band is not None:
        comps.append(range_band)

    if not comps:
        return {
            "ok": False,
            "reason": "NOT_EVALUABLE",
            "noise_band": None,
            "tick_band": tick_band,
            "spread_band": spread_band,
            "range_band": range_band,
            "spread_status": spread_status,
            "range_status": range_status,
        }

    return {
        "ok": True,
        "reason": "",
        "noise_band": float(max(comps)),
        "tick_band": float(tick_band),
        "spread_band": float(spread_band) if spread_band is not None else None,
        "range_band": float(range_band) if range_band is not None else None,
        "tick_size": ts,
        "spread_status": spread_status,
        "range_status": range_status,
        "components_used": len(comps),
    }


def iter_noise_grid() -> list[dict[str, float]]:
    from research.eec_noise_hysteresis.constants import RANGE_MULTIPLIERS, SPREAD_MULTIPLIERS, TICK_MULTIPLIERS

    out = []
    for tm in TICK_MULTIPLIERS:
        for rm in RANGE_MULTIPLIERS:
            for sm in SPREAD_MULTIPLIERS:
                out.append({"tick_mult": tm, "range_mult": rm, "spread_mult": sm})
    return out
