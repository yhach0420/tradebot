"""Sector and stock response functions. Market/sector controls. No threshold search. Discovery only."""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy.stats import spearmanr

from research.sector_symbol_driver_response_mapping_v1 import FWD_HORIZONS, MIN_CLOCK_N, MIN_DAY_N, PRIMARY_HORIZON_MIN
from research.sector_symbol_driver_response_mapping_v1.panel import DRIVER_IDS

RHO_STRONG = 0.12
RHO_WEAK = 0.05
RHO_INC = 0.03
BLOCK_MIN = 3
STOCK_DIFF = 0.04


def _spearman(x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    m = np.isfinite(x) & np.isfinite(y)
    xa, ya = x[m], y[m]
    if xa.size < 40 or float(np.std(xa)) == 0 or float(np.std(ya)) == 0:
        return {"n": int(xa.size), "rho": None, "ok": False}
    rho, p = spearmanr(xa, ya)
    return {"n": int(xa.size), "rho": float(rho) if rho == rho else None, "p": float(p) if p == p else None, "ok": True}


def _arr(xs: list[Any]) -> np.ndarray:
    return np.asarray(xs, dtype=float) if xs and not isinstance(xs[0], str) else np.asarray(xs)


def _status(*, rho: float | None, inc: float | None, block_rhos: list[float | None], n: int, day_n: int, regime: dict[str, Any]) -> str:
    if n < MIN_CLOCK_N or day_n < MIN_DAY_N or rho is None:
        return "UNSTABLE"
    signs = [np.sign(r) for r in block_rhos if r is not None and r == r and r != 0]
    agree = sum(1 for s in signs if s == np.sign(rho))
    inc_ok = inc is not None and abs(float(inc)) >= RHO_INC
    pooled_ok = abs(float(rho)) >= RHO_WEAK
    if pooled_ok and not inc_ok:
        return "NO_INCREMENTAL_VALUE"
    if agree >= 4 and abs(float(rho)) >= RHO_STRONG and inc_ok:
        return "STRONG_STABLE"
    if agree >= BLOCK_MIN and pooled_ok and inc_ok:
        return "WEAK_STABLE"
    hi = (regime.get("high") or {}).get("rho")
    lo = (regime.get("low") or {}).get("rho")
    if (hi is not None and abs(float(hi)) >= RHO_WEAK and (lo is None or abs(float(lo)) < RHO_WEAK)) or (
        lo is not None and abs(float(lo)) >= RHO_WEAK and (hi is None or abs(float(hi)) < RHO_WEAK)
    ):
        return "REGIME_DEPENDENT"
    if pooled_ok and agree < BLOCK_MIN:
        return "UNSTABLE"
    return "UNSTABLE"


def _lead_label(fwd_rhos: dict[int, float | None], rev_rho: float | None) -> str:
    primary = fwd_rhos.get(PRIMARY_HORIZON_MIN)
    if primary is None:
        return "not_testable"
    if rev_rho is not None and abs(float(rev_rho)) > abs(float(primary)) + 0.03:
        return "sector_leads_or_simultaneous"
    if abs(float(primary)) < RHO_INC:
        return "weak_or_contemporaneous_unusable"
    best_h = PRIMARY_HORIZON_MIN
    best = abs(float(primary))
    for h, r in fwd_rhos.items():
        if r is None:
            continue
        if abs(float(r)) > best:
            best = abs(float(r))
            best_h = h
    if best_h == 1 and abs(float(fwd_rhos.get(1) or 0)) >= abs(float(fwd_rhos.get(5) or 0)):
        return "short_lead_or_near_contemporaneous"
    return f"driver_leads_about_{best_h}m"


def _mask_block(blocks: np.ndarray, blk: str) -> np.ndarray:
    return blocks == blk


def analyze_sector_driver(bkt: dict[str, Any], did: str) -> dict[str, Any]:
    d = np.asarray(bkt["d"][did], dtype=float)
    y5 = np.asarray(bkt["y"][PRIMARY_HORIZON_MIN], dtype=float)
    ym5 = np.asarray(bkt["ym"][PRIMARY_HORIZON_MIN], dtype=float)
    sec_ret = np.asarray(bkt["sec_ret"], dtype=float)
    blocks = np.asarray(bkt["block"])
    tod = np.asarray(bkt["tod"])
    vol = np.asarray(bkt["vol"], dtype=float)
    dates = np.asarray(bkt["date"])
    raw = _spearman(d, y5)
    inc = _spearman(d, y5 - ym5)
    fwd = {h: _spearman(d, np.asarray(bkt["y"][h], dtype=float)).get("rho") for h in FWD_HORIZONS}
    rev = _spearman(sec_ret, d)
    block_rhos = [_spearman(d[_mask_block(blocks, b)], y5[_mask_block(blocks, b)]).get("rho") for b in ("D1", "D2", "D3", "D4")]
    cut = float(np.nanmedian(vol)) if np.isfinite(vol).sum() >= 40 else float("nan")
    hi = vol >= cut if cut == cut else np.zeros(len(d), dtype=bool)
    lo = vol < cut if cut == cut else np.zeros(len(d), dtype=bool)
    regime = {"cut": cut, "high": _spearman(d[hi], y5[hi]), "low": _spearman(d[lo], y5[lo])} if cut == cut else {}
    tod_map = {k: _spearman(d[tod == k], y5[tod == k]) for k in ("open", "mid_am", "early_pm", "late_pm")}
    pos = y5[np.isfinite(d) & np.isfinite(y5) & (d > 0)]
    neg = y5[np.isfinite(d) & np.isfinite(y5) & (d < 0)]
    mp = float(np.mean(pos)) if pos.size else None
    mn = float(np.mean(neg)) if neg.size else None
    day_n = len(set(str(x) for x in dates.tolist())) if dates.size else 0
    status = _status(rho=raw.get("rho"), inc=inc.get("rho"), block_rhos=block_rhos, n=int(raw.get("n") or 0), day_n=day_n, regime=regime)
    lead = _lead_label(fwd, rev.get("rho"))
    return {
        "n": int(raw.get("n") or 0),
        "day_n": day_n,
        "rho_raw": raw.get("rho"),
        "rho_incremental_after_market_ex_sector": inc.get("rho"),
        "fwd_rho": fwd,
        "reverse_rho_sector_now_vs_driver": rev.get("rho"),
        "lead_lag": lead,
        "block_rho": {"D1": block_rhos[0], "D2": block_rhos[1], "D3": block_rhos[2], "D4": block_rhos[3]},
        "asymmetry": {
            "n_pos": int(pos.size),
            "n_neg": int(neg.size),
            "mean_y_when_d_pos": mp,
            "mean_y_when_d_neg": mn,
            "symmetric": bool(mp is not None and mn is not None and abs(mp + mn) <= 0.35 * (abs(mp) + abs(mn) + 1e-9)),
        },
        "regime": regime,
        "tod": tod_map,
        "status": status,
        "signed_response": None if raw.get("rho") is None else ("pos" if float(raw["rho"]) > 0 else ("neg" if float(raw["rho"]) < 0 else "zero")),
        "usable_before_stock_move": bool(status in {"STRONG_STABLE", "WEAK_STABLE"} and str(lead).startswith("driver_leads")),
        "only_contemporaneous": bool(lead in {"sector_leads_or_simultaneous", "weak_or_contemporaneous_unusable", "short_lead_or_near_contemporaneous"}),
    }


def analyze_stock_driver(bkt: dict[str, Any], did: str) -> dict[str, Any]:
    d = np.asarray(bkt["d"][did], dtype=float)
    y5 = np.asarray(bkt["y5"], dtype=float)
    ym5 = np.asarray(bkt["ym5"], dtype=float)
    ys5 = np.asarray(bkt["ys5"], dtype=float)
    blocks = np.asarray(bkt["block"])
    tod = np.asarray(bkt["tod"])
    dates = np.asarray(bkt["date"])
    raw = _spearman(d, y5)
    mkt = _spearman(d, y5 - ym5)
    sec = _spearman(d, y5 - ys5)
    block_rhos = [_spearman(d[_mask_block(blocks, b)], (y5 - ys5)[_mask_block(blocks, b)]).get("rho") for b in ("D1", "D2", "D3", "D4")]
    day_n = len(set(str(x) for x in dates.tolist())) if dates.size else 0
    status = _status(rho=sec.get("rho"), inc=sec.get("rho"), block_rhos=block_rhos, n=int(sec.get("n") or 0), day_n=day_n, regime={})
    pos = y5[np.isfinite(d) & np.isfinite(y5) & (d > 0)]
    neg = y5[np.isfinite(d) & np.isfinite(y5) & (d < 0)]
    return {
        "n": int(raw.get("n") or 0),
        "day_n": day_n,
        "rho_raw": raw.get("rho"),
        "rho_after_market": mkt.get("rho"),
        "rho_after_sector": sec.get("rho"),
        "block_rho_after_sector": {"D1": block_rhos[0], "D2": block_rhos[1], "D3": block_rhos[2], "D4": block_rhos[3]},
        "asymmetry": {
            "n_pos": int(pos.size),
            "n_neg": int(neg.size),
            "mean_y_when_d_pos": float(np.mean(pos)) if pos.size else None,
            "mean_y_when_d_neg": float(np.mean(neg)) if neg.size else None,
        },
        "tod": {k: _spearman(d[tod == k], y5[tod == k]) for k in ("open", "mid_am", "early_pm", "late_pm")},
        "direct_beyond_sector": bool(status in {"STRONG_STABLE", "WEAK_STABLE"} and sec.get("rho") is not None and abs(float(sec["rho"])) >= RHO_WEAK),
        "status": status,
    }


def map_responses(obs: dict[str, Any]) -> dict[str, Any]:
    sector_rows = []
    for sec, bkt in sorted(dict(obs.get("sec_obs") or {}).items()):
        if not bkt.get("date"):
            continue
        for did in DRIVER_IDS:
            got = analyze_sector_driver(bkt, did)
            got.update({"sector": sec, "driver": did})
            sector_rows.append(got)
    stock_rows = []
    for sym, bkt in sorted(dict(obs.get("stk_obs") or {}).items()):
        if not bkt.get("date"):
            continue
        for did in DRIVER_IDS:
            got = analyze_stock_driver(bkt, did)
            got.update({"symbol": sym, "driver": did})
            stock_rows.append(got)
    stable_sec = [r for r in sector_rows if r.get("status") in {"STRONG_STABLE", "WEAK_STABLE"}]
    return {
        "sector_driver": sector_rows,
        "stock_driver": stock_rows,
        "stable_sector_driver_n": len(stable_sec),
        "stable_sector_driver": [
            {"sector": r["sector"], "driver": r["driver"], "status": r["status"], "rho_inc": r.get("rho_incremental_after_market_ex_sector"), "lead": r.get("lead_lag")}
            for r in stable_sec
        ],
    }
