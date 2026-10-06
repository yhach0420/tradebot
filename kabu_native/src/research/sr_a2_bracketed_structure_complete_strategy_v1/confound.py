"""Target-availability confound audit. Pre-entry covariates only. Not an ENTRY filter."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.sr_a2_bracketed_structure_complete_strategy_v1 import SMD_MATERIAL


COV = (
    "tod_min",
    "decision_px",
    "notional",
    "as_resistance",
    "r1",
    "r3",
    "r5",
    "vol_rel",
    "rng_rel",
    "gap_num",
    "mkt_num",
    "sec_num",
    "zone_age",
    "n_active_sr",
    "touch_count",
)


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _arr(rows: list[dict[str, Any]], key: str) -> np.ndarray:
    xs = [float(r[key]) for r in rows if _finite(r.get(key))]
    return np.asarray(xs, dtype=float)


def _smd(a: np.ndarray, b: np.ndarray) -> float | None:
    if a.size < 8 or b.size < 8:
        return None
    va = float(np.nanvar(a))
    vb = float(np.nanvar(b))
    den = (va + vb) / 2.0
    if den <= 0:
        return 0.0
    return float((np.nanmean(a) - np.nanmean(b)) / np.sqrt(den))


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = _arr(rows, key)
    return float(np.nanmean(xs)) if xs.size else None


def _median(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = _arr(rows, key)
    return float(np.nanmedian(xs)) if xs.size else None


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = [r.get(key) for r in rows if r.get(key) is not None]
    if not xs:
        return None
    return float(sum(1 for v in xs if v) / len(xs))


def confound_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    avail = [r for r in rows if r.get("opposing_zone_available")]
    skip = [r for r in rows if not r.get("opposing_zone_available")]
    smds = {}
    material = []
    for k in COV:
        v = _smd(_arr(avail, k), _arr(skip, k))
        smds[k] = v
        if v is not None and abs(v) >= float(SMD_MATERIAL):
            material.append(k)
    yen_proxy = False
    pa, ps = _mean(avail, "decision_px"), _mean(skip, "decision_px")
    na, ns = _mean(avail, "notional"), _mean(skip, "notional")
    ba, bs = _mean(avail, "path_mfe_bps"), _mean(skip, "path_mfe_bps")
    r20a, r20s = _rate(avail, "p20_before_m20"), _rate(skip, "p20_before_m20")
    if pa and ps and pa / ps > 1.5 and ba is not None and bs is not None and ba <= bs:
        yen_proxy = True
    if na and ns and na / ns > 1.5 and r20a is not None and r20s is not None and r20a <= r20s:
        yen_proxy = True
    return {
        "available_n": len(avail),
        "skip_n": len(skip),
        "smd": smds,
        "material_smd_keys": material,
        "IS_TARGET_AVAILABILITY_JUST_A_PROXY": bool(material),
        "notional_confound": bool(yen_proxy),
        "mean_decision_px_available": pa,
        "mean_decision_px_skip": ps,
        "mean_notional_available": na,
        "mean_notional_skip": ns,
        "median_tod_available": _median(avail, "tod_min"),
        "median_tod_skip": _median(skip, "tod_min"),
        "share_short_available": _mean(avail, "as_resistance"),
        "share_short_skip": _mean(skip, "as_resistance"),
        "mean_vol_rel_available": _mean(avail, "vol_rel"),
        "mean_vol_rel_skip": _mean(skip, "vol_rel"),
        "mean_r5_available": _mean(avail, "r5"),
        "mean_r5_skip": _mean(skip, "r5"),
        "mean_n_active_sr_available": _mean(avail, "n_active_sr"),
        "mean_n_active_sr_skip": _mean(skip, "n_active_sr"),
        "path_mfe_available": ba,
        "path_mfe_skip": bs,
        "p20_available": r20a,
        "p20_skip": r20s,
        "used_as_entry_filter": False,
    }


def path_split(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    keys = (
        "path_mfe_bps",
        "path_mae_bps",
        "p20_before_m20",
        "p40_before_m20",
        "p80_before_m30",
        "ret_5m_bps",
        "ret_10m_bps",
        "ret_20m_bps",
        "time_to_mfe",
        "path_giveback",
    )
    for block in ("ALL", "D1", "D2", "D3", "D4"):
        sub = rows if block == "ALL" else [r for r in rows if str(r.get("block") or "") == block]
        avail = [r for r in sub if r.get("opposing_zone_available")]
        skip = [r for r in sub if not r.get("opposing_zone_available")]
        rec: dict[str, Any] = {"available_n": len(avail), "skip_n": len(skip)}
        for k in keys:
            if k.startswith("p") and "before" in k:
                rec[f"{k}_available"] = _rate(avail, k)
                rec[f"{k}_skip"] = _rate(skip, k)
            else:
                rec[f"{k}_available"] = _mean(avail, k)
                rec[f"{k}_skip"] = _mean(skip, k)
        out[block] = rec
    return {"diagnostic_only": True, "used_as_entry_threshold": False, "by_block": out}
