"""USDJPY impulse-response, reverse causality, asymmetry, D1-D4. Characterization, not lag search."""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy.stats import spearmanr

from research.usd_jpy_sector_symbol_response_v1 import (
    BETA_PER_10BPS_FX,
    BLOCK_MIN,
    FWD_HORIZONS,
    MIN_CLOCK_N,
    MIN_DAY_N,
    MIN_STOCK_N,
    PRIMARY_LEAD_HORIZONS,
    RHO_LEAD,
    RHO_WEAK,
)

BLOCKS = ("D1", "D2", "D3", "D4")


def _arr(xs: list[Any]) -> np.ndarray:
    return np.asarray(xs, dtype=float)


def _spearman(x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    m = np.isfinite(x) & np.isfinite(y)
    xa, ya = x[m], y[m]
    if xa.size < 40 or float(np.std(xa)) == 0 or float(np.std(ya)) == 0:
        return {"n": int(xa.size), "rho": None, "ok": False}
    rho, p = spearmanr(xa, ya)
    return {"n": int(xa.size), "rho": float(rho) if rho == rho else None, "p": float(p) if p == p else None, "ok": True}


def _ols_beta(x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    m = np.isfinite(x) & np.isfinite(y)
    xa, ya = x[m], y[m]
    if xa.size < 40 or float(np.var(xa)) == 0:
        return {"n": int(xa.size), "beta": None, "beta_per_10bps_fx": None}
    x0 = xa - xa.mean()
    y0 = ya - ya.mean()
    beta = float(np.dot(x0, y0) / np.dot(x0, x0))
    return {"n": int(xa.size), "beta": beta, "beta_per_10bps_fx": beta * 10.0}


def _mag(x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    m = np.isfinite(x) & np.isfinite(y)
    xa, ya = x[m], y[m]
    if xa.size < 40:
        return {"n": int(xa.size)}
    up = xa > 0
    dn = xa < 0
    same = np.sign(xa) == np.sign(ya)
    return {
        "n": int(xa.size),
        "mean_y": float(np.mean(ya)),
        "median_y": float(np.median(ya)),
        "p10_y": float(np.percentile(ya, 10)),
        "p90_y": float(np.percentile(ya, 90)),
        "mean_y_fx_up": float(np.mean(ya[up])) if up.any() else None,
        "mean_y_fx_down": float(np.mean(ya[dn])) if dn.any() else None,
        "median_y_fx_up": float(np.median(ya[up])) if up.any() else None,
        "median_y_fx_down": float(np.median(ya[dn])) if dn.any() else None,
        "direction_prob_same_sign": float(np.mean(np.sign(xa) == np.sign(ya))) if xa.size else None,
        "up_n": int(up.sum()),
        "down_n": int(dn.sum()),
        "asym_mean_diff": (float(np.mean(ya[up])) - float(np.mean(ya[dn]))) if up.any() and dn.any() else None,
    }


def _block_rhos(x: np.ndarray, y: np.ndarray, blocks: np.ndarray) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for b in BLOCKS:
        m = blocks == b
        out[b] = _spearman(x[m], y[m]).get("rho")
    return out


def _agree(rho: float | None, block_rhos: dict[str, float | None]) -> int:
    if rho is None:
        return 0
    s = np.sign(rho)
    n = 0
    for r in block_rhos.values():
        if r is None or r != r or r == 0:
            continue
        if np.sign(r) == s:
            n += 1
    return n


def _stable(*, rho: float | None, beta10: float | None, agree: int, n: int, day_n: int, min_n: int) -> bool:
    if rho is None or n < min_n or day_n < MIN_DAY_N or agree < BLOCK_MIN:
        return False
    mag = abs(float(beta10 or 0)) >= BETA_PER_10BPS_FX or abs(float(rho)) >= RHO_LEAD
    return mag and abs(float(rho)) >= RHO_WEAK


def _lead_class(*, fwd: dict[int, float | None], sim: float | None, rev: float | None, stable_fwd: bool) -> str:
    best_h = None
    best = 0.0
    for h, r in fwd.items():
        if r is None:
            continue
        if abs(float(r)) > best:
            best = abs(float(r))
            best_h = h
    sim_a = abs(float(sim)) if sim is not None else 0.0
    rev_a = abs(float(rev)) if rev is not None else 0.0
    lead_a = max((abs(float(fwd.get(h) or 0)) for h in PRIMARY_LEAD_HORIZONS), default=0.0)
    if rev_a > lead_a + 0.02 and rev_a >= RHO_WEAK:
        return "stocks_lead_fx"
    if stable_fwd and lead_a >= RHO_WEAK and lead_a + 0.005 >= sim_a - 0.02:
        return f"fx_leads_about_{best_h}m" if best_h else "fx_leads"
    if sim_a >= RHO_WEAK and lead_a < RHO_WEAK:
        return "simultaneous_only"
    if sim_a >= RHO_WEAK and sim_a > lead_a + 0.03:
        return "simultaneous_common_news"
    if lead_a < RHO_WEAK and sim_a < RHO_WEAK:
        return "weak_or_none"
    return "unusable_near_contemporaneous"


def summarize_series(*, x: np.ndarray, y: np.ndarray, blocks: np.ndarray, dates: np.ndarray, min_n: int) -> dict[str, Any]:
    sp = _spearman(x, y)
    ol = _ols_beta(x, y)
    mag = _mag(x, y)
    br = _block_rhos(x, y, blocks)
    rho = sp.get("rho")
    day_n = int(len({str(d) for d, a, b in zip(dates, x, y) if np.isfinite(a) and np.isfinite(b)}))
    agree = _agree(rho, br)
    stable = _stable(rho=rho, beta10=ol.get("beta_per_10bps_fx"), agree=agree, n=int(sp.get("n") or 0), day_n=day_n, min_n=min_n)
    up = x > 0
    dn = x < 0
    up_sp = _spearman(x[up], y[up]) if up.any() else {"rho": None}
    dn_sp = _spearman(x[dn], y[dn]) if dn.any() else {"rho": None}
    return {
        **sp,
        **ol,
        **mag,
        "block_rho": br,
        "d1_d4_agree": agree,
        "day_n": day_n,
        "stable": stable,
        "rho_fx_up": up_sp.get("rho"),
        "rho_fx_down": dn_sp.get("rho"),
    }


def map_sector(obs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for sec, b in obs.items():
        x = _arr(b.get("fx1") or [])
        blocks = np.asarray(b.get("block") or [], dtype=object)
        dates = np.asarray(b.get("date") or [], dtype=object)
        if x.size == 0:
            continue
        fwd_raw = {}
        fwd_res = {}
        curve = []
        for h in FWD_HORIZONS:
            raw = summarize_series(x=x, y=_arr(b.get(f"y{h}") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
            res = summarize_series(x=x, y=_arr(b.get(f"yr{h}") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
            fwd_raw[h] = raw.get("rho")
            fwd_res[h] = res.get("rho")
            curve.append({"h": h, "raw": raw, "residual": res})
        sim = summarize_series(x=x, y=_arr(b.get("sim_resid") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
        rev = summarize_series(x=_arr(b.get("sim_resid") or []), y=_arr(b.get("rev_fx") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
        primary = curve[0]["residual"] if curve else {}
        stable_lead = any(c["residual"].get("stable") and int(c["h"]) in PRIMARY_LEAD_HORIZONS for c in curve)
        lead = _lead_class(
            fwd=fwd_res,
            sim=sim.get("rho"),
            rev=rev.get("rho"),
            stable_fwd=stable_lead,
        )
        usable = lead.startswith("fx_leads")
        y1 = _arr(b.get("yr1") or [])
        y5 = _arr(b.get("yr5") or [])
        pm = np.isfinite(y1) & np.isfinite(y5) & (y1 != 0)
        persist = float(np.mean(np.sign(y1[pm]) == np.sign(y5[pm]))) if pm.any() else None
        reversal = float(np.mean(np.sign(y1[pm]) == -np.sign(y5[pm]))) if pm.any() else None
        rows.append(
            {
                "sector": sec,
                "n": int(primary.get("n") or 0),
                "day_n": int(primary.get("day_n") or 0),
                "fwd_rho_raw": fwd_raw,
                "fwd_rho_residual": fwd_res,
                "residual_1m": curve[0]["residual"] if curve else {},
                "residual_curve": [{k: c["residual"].get(k) for k in ("h", "n", "rho", "beta_per_10bps_fx", "mean_y", "median_y", "stable", "d1_d4_agree")} | {"h": c["h"]} for c in curve],
                "simultaneous_residual": {k: sim.get(k) for k in ("n", "rho", "beta_per_10bps_fx", "stable")},
                "reverse": {"rho": rev.get("rho"), "n": rev.get("n"), "stable": rev.get("stable")},
                "reverse_rho": rev.get("rho"),
                "lead_lag": lead,
                "usable_before_stock_move": usable,
                "only_simultaneous": lead in ("simultaneous_only", "simultaneous_common_news"),
                "jpy_weakening_sector_up": bool((primary.get("mean_y_fx_up") or 0) > 0 and (primary.get("stable") or abs(primary.get("rho") or 0) >= RHO_WEAK)),
                "jpy_strengthening_sector_up": bool((primary.get("mean_y_fx_down") or 0) > 0 and (primary.get("stable") or abs(primary.get("rho") or 0) >= RHO_WEAK)),
                "asymmetric": bool(abs((primary.get("mean_y_fx_up") or 0) + (primary.get("mean_y_fx_down") or 0)) >= 0.3),
                "response_persistence_1m_to_5m": persist,
                "reversal_probability_1m_to_5m": reversal,
                "stable_residual_lead": bool(stable_lead),
                "block_rho_1m": (curve[0]["residual"].get("block_rho") if curve else {}),
            }
        )
    return rows


def map_stock(obs: dict[str, dict[str, Any]], sector_of: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for sym, b in obs.items():
        x = _arr(b.get("fx1") or [])
        if x.size == 0:
            continue
        blocks = np.asarray(b.get("block") or [], dtype=object)
        dates = np.asarray(b.get("date") or [], dtype=object)
        raw = summarize_series(x=x, y=_arr(b.get("y1") or []), blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        mkt = summarize_series(x=x, y=_arr(b.get("ym1") or []), blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        sec = summarize_series(x=x, y=_arr(b.get("ys1") or []), blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        curve = []
        for h in FWD_HORIZONS:
            curve.append(
                {
                    "h": h,
                    "raw": summarize_series(x=x, y=_arr(b.get(f"y{h}") or []), blocks=blocks, dates=dates, min_n=MIN_STOCK_N),
                    "mkt": summarize_series(x=x, y=_arr(b.get(f"ym{h}") or []), blocks=blocks, dates=dates, min_n=MIN_STOCK_N),
                    "sec": summarize_series(x=x, y=_arr(b.get(f"ys{h}") or []), blocks=blocks, dates=dates, min_n=MIN_STOCK_N),
                }
            )
        sim = summarize_series(x=x, y=_arr(b.get("sim_raw") or []), blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        fwd = {c["h"]: c["sec"].get("rho") for c in curve}
        lead = _lead_class(
            fwd=fwd,
            sim=sim.get("rho"),
            rev=_spearman(_arr(b.get("sim_raw") or []), _arr(b.get("rev_fx") or [])).get("rho"),
            stable_fwd=any(c["sec"].get("stable") and int(c["h"]) in PRIMARY_LEAD_HORIZONS for c in curve),
        )
        if sec.get("stable"):
            kind = "DIRECT_STOCK_SENSITIVITY"
        elif mkt.get("stable"):
            kind = "SECTOR_MEDIATED" if not sec.get("stable") else "DIRECT_STOCK_SENSITIVITY"
        elif raw.get("stable"):
            kind = "MARKET_MEDIATED"
        else:
            kind = "NO_STABLE_RESPONSE"
        if mkt.get("stable") and not sec.get("stable") and not raw.get("stable"):
            kind = "SECTOR_MEDIATED"
        if raw.get("stable") and not mkt.get("stable"):
            kind = "MARKET_MEDIATED"
        if sec.get("stable"):
            kind = "DIRECT_STOCK_SENSITIVITY"
        best_h = 1
        best = -1.0
        for c in curve:
            r = c["sec"].get("rho")
            if r is not None and abs(float(r)) > best:
                best = abs(float(r))
                best_h = int(c["h"])
        rows.append(
            {
                "symbol": sym,
                "sector": sector_of.get(sym) or "",
                "class": kind,
                "lead_lag": lead,
                "lead_time_min": best_h if kind != "NO_STABLE_RESPONSE" else None,
                "raw_1m": raw,
                "market_adj_1m": mkt,
                "sector_adj_1m": sec,
                "curve": [
                    {
                        "h": c["h"],
                        "rho_raw": c["raw"].get("rho"),
                        "rho_mkt": c["mkt"].get("rho"),
                        "rho_sec": c["sec"].get("rho"),
                        "beta10_sec": c["sec"].get("beta_per_10bps_fx"),
                        "mean_sec": c["sec"].get("mean_y"),
                        "stable_sec": c["sec"].get("stable"),
                    }
                    for c in curve
                ],
                "up_mean_sec": sec.get("mean_y_fx_up"),
                "down_mean_sec": sec.get("mean_y_fx_down"),
                "asymmetric": bool(abs((sec.get("mean_y_fx_up") or 0) + (sec.get("mean_y_fx_down") or 0)) >= 0.3),
                "d1_d4_sec": sec.get("block_rho"),
                "d1_d4_agree": sec.get("d1_d4_agree"),
                "tod_open_rho": _tod_rho(b, "open"),
                "usable_before_stock_move": lead.startswith("fx_leads") and kind != "NO_STABLE_RESPONSE",
            }
        )
    return rows


def _tod_rho(b: dict[str, Any], bucket: str) -> float | None:
    tod = np.asarray(b.get("tod") or [], dtype=object)
    x = _arr(b.get("fx1") or [])
    y = _arr(b.get("ys1") or [])
    if tod.size != x.size:
        return None
    return _spearman(x[tod == bucket], y[tod == bucket]).get("rho")


def map_preopen(obs: dict[str, dict[str, Any]], *, residual: bool) -> list[dict[str, Any]]:
    rows = []
    ykey = "y_15_resid" if residual else "y_15"
    for name, b in obs.items():
        x = _arr(b.get("fx_ov") or [])
        if x.size == 0:
            continue
        blocks = np.asarray(b.get("block") or [], dtype=object)
        dates = np.asarray(b.get("date") or [], dtype=object)
        rows.append(
            {
                "name": name,
                "open": summarize_series(x=x, y=_arr(b.get("y_open") or []), blocks=blocks, dates=dates, min_n=MIN_DAY_N),
                "m15": summarize_series(x=x, y=_arr(b.get(ykey) or b.get("y_15") or []), blocks=blocks, dates=dates, min_n=MIN_DAY_N),
                "m30": summarize_series(x=x, y=_arr(b.get("y_30") or []), blocks=blocks, dates=dates, min_n=MIN_DAY_N),
            }
        )
    return rows
