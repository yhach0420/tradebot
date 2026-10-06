"""NQ vs ES impulse-response, incremental residual, reverse causality, D1-D4. Characterization, not lag search."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.nq_es_sector_symbol_response_v1 import (
    BETA_PER_10BPS,
    BLOCK_MIN,
    FWD_HORIZONS,
    MIN_CLOCK_N,
    MIN_DAY_N,
    MIN_STOCK_N,
    PRIMARY_LEAD_HORIZONS,
    RHO_LEAD,
    RHO_WEAK,
    TECH_FOCUS,
)
from research.usd_jpy_sector_symbol_response_v1.response import (
    _agree,
    _arr,
    _lead_class,
    _ols_beta,
    _spearman,
    _stable,
    summarize_series,
)

SIM = {"simultaneous_only", "simultaneous_common_news"}
NEAR = "unusable_near_contemporaneous"


def residualize(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    """OLS residual of y after x. Does not invent values across missing observations."""
    out = np.full(y.shape, np.nan, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    if int(m.sum()) < 40:
        return out
    xa = x[m]
    ya = y[m]
    x0 = xa - xa.mean()
    den = float(np.dot(x0, x0))
    if den == 0:
        return out
    beta = float(np.dot(x0, ya - ya.mean()) / den)
    alpha = float(ya.mean() - beta * xa.mean())
    out[m] = ya - (alpha + beta * xa)
    return out


def _relabel(lead: str, driver: str) -> str:
    return str(lead).replace("fx_leads", f"{driver}_leads").replace("stocks_lead_fx", "stock_leads_driver")


def _bools(lead: str) -> dict[str, bool]:
    causal = lead.startswith("nq_leads") or lead.startswith("es_leads") or lead.startswith("driver_leads")
    sim_news = lead == "simultaneous_common_news"
    usable = bool(causal and not sim_news and lead != "simultaneous_only" and lead != NEAR)
    if sim_news:
        usable = False
    return {
        "causal_lead": bool(causal),
        "usable_before_stock_move": bool(usable),
        "simultaneous_common_news": bool(sim_news),
        "simultaneous_only": lead == "simultaneous_only",
        "near_contemporaneous": lead == NEAR,
        "stock_leads_driver": lead == "stock_leads_driver",
    }


def _map_one_driver(
    *,
    obs: dict[str, dict[str, Any]],
    xkey: str,
    driver: str,
    residual: bool,
    sector_of: dict[str, str] | None = None,
    stock: bool = False,
) -> list[dict[str, Any]]:
    rows = []
    min_n = MIN_STOCK_N if stock else MIN_CLOCK_N
    for name, b in obs.items():
        x = _arr(b.get(xkey) or [])
        if x.size == 0:
            continue
        blocks = np.asarray(b.get("block") or [], dtype=object)
        dates = np.asarray(b.get("date") or [], dtype=object)
        curve = []
        fwd = {}
        for h in FWD_HORIZONS:
            ykey = f"ys{h}" if stock else (f"yr{h}" if residual else f"y{h}")
            if stock:
                raw = summarize_series(x=x, y=_arr(b.get(f"y{h}") or []), blocks=blocks, dates=dates, min_n=min_n)
                mkt = summarize_series(x=x, y=_arr(b.get(f"ym{h}") or []), blocks=blocks, dates=dates, min_n=min_n)
                sec = summarize_series(x=x, y=_arr(b.get(f"ys{h}") or []), blocks=blocks, dates=dates, min_n=min_n)
                curve.append({"h": h, "raw": raw, "mkt": mkt, "sec": sec})
                fwd[h] = sec.get("rho")
            else:
                raw = summarize_series(x=x, y=_arr(b.get(f"y{h}") or []), blocks=blocks, dates=dates, min_n=min_n)
                res = summarize_series(x=x, y=_arr(b.get(f"yr{h}") or []), blocks=blocks, dates=dates, min_n=min_n)
                curve.append({"h": h, "raw": raw, "residual": res})
                fwd[h] = (res if residual else raw).get("rho")
        sim_y = _arr(b.get("sim_resid") or b.get("sim_raw") or [])
        sim = summarize_series(x=x, y=sim_y, blocks=blocks, dates=dates, min_n=min_n)
        rev_y = _arr(b.get(f"rev_{driver}") or [])
        rev = summarize_series(x=sim_y, y=rev_y, blocks=blocks, dates=dates, min_n=min_n)
        if stock:
            stable_fwd = any(c["sec"].get("stable") and int(c["h"]) in PRIMARY_LEAD_HORIZONS for c in curve)
        else:
            key = "residual" if residual else "raw"
            stable_fwd = any(c[key].get("stable") and int(c["h"]) in PRIMARY_LEAD_HORIZONS for c in curve)
        lead = _relabel(
            _lead_class(fwd=fwd, sim=sim.get("rho"), rev=rev.get("rho"), stable_fwd=stable_fwd),
            driver,
        )
        flags = _bools(lead)
        if stock:
            raw1 = curve[0]["raw"]
            mkt1 = curve[0]["mkt"]
            sec1 = curve[0]["sec"]
            rows.append(
                {
                    "symbol": name,
                    "sector": (sector_of or {}).get(name) or "",
                    "driver": driver,
                    "raw_1m": raw1,
                    "market_adj_1m": mkt1,
                    "sector_adj_1m": sec1,
                    "fwd_rho_raw": {c["h"]: c["raw"].get("rho") for c in curve},
                    "fwd_rho_mkt": {c["h"]: c["mkt"].get("rho") for c in curve},
                    "fwd_rho_sec": {c["h"]: c["sec"].get("rho") for c in curve},
                    "lead_lag": lead,
                    "stable_residual_lead": bool(stable_fwd),
                    "d1_d4_agree": sec1.get("d1_d4_agree"),
                    "d1_d4_sec": sec1.get("block_rho"),
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
                    **flags,
                }
            )
        else:
            primary = curve[0]["residual"] if residual else curve[0]["raw"]
            rows.append(
                {
                    "sector": name,
                    "driver": driver,
                    "n": int(primary.get("n") or 0),
                    "day_n": int(primary.get("day_n") or 0),
                    "fwd_rho_raw": {c["h"]: c["raw"].get("rho") for c in curve},
                    "fwd_rho_residual": {c["h"]: c["residual"].get("rho") for c in curve},
                    "residual_1m": curve[0]["residual"],
                    "raw_1m": curve[0]["raw"],
                    "simultaneous_residual": {k: sim.get(k) for k in ("n", "rho", "beta_per_10bps_fx", "stable")},
                    "reverse_rho": rev.get("rho"),
                    "lead_lag": lead,
                    "stable_residual_lead": bool(stable_fwd),
                    "block_rho_1m": (curve[0]["residual"].get("block_rho") if residual else curve[0]["raw"].get("block_rho")),
                    **flags,
                    "only_simultaneous": lead in SIM,
                }
            )
    return rows


def map_incremental_stock(obs: dict[str, dict[str, Any]], sector_of: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for sym, b in obs.items():
        nq = _arr(b.get("nq1") or [])
        es = _arr(b.get("es1") or [])
        if nq.size == 0:
            continue
        nq_ae = residualize(nq, es)
        es_an = residualize(es, nq)
        blocks = np.asarray(b.get("block") or [], dtype=object)
        dates = np.asarray(b.get("date") or [], dtype=object)
        y = _arr(b.get("ys1") or [])
        ym = _arr(b.get("ym1") or [])
        yr = _arr(b.get("y1") or [])
        nq_sec = summarize_series(x=nq, y=y, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        es_sec = summarize_series(x=es, y=y, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        nq_mkt = summarize_series(x=nq, y=ym, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        es_mkt = summarize_series(x=es, y=ym, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        nq_raw = summarize_series(x=nq, y=yr, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        es_raw = summarize_series(x=es, y=yr, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        nq_after_es = summarize_series(x=nq_ae, y=y, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        es_after_nq = summarize_series(x=es_an, y=y, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        sim = summarize_series(x=nq, y=_arr(b.get("sim_raw") or []), blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        nq_fwd = {}
        es_fwd = {}
        nq_ae_fwd = {}
        for h in FWD_HORIZONS:
            ys = _arr(b.get(f"ys{h}") or [])
            nq_fwd[h] = summarize_series(x=nq, y=ys, blocks=blocks, dates=dates, min_n=MIN_STOCK_N).get("rho")
            es_fwd[h] = summarize_series(x=es, y=ys, blocks=blocks, dates=dates, min_n=MIN_STOCK_N).get("rho")
            nq_ae_fwd[h] = summarize_series(x=nq_ae, y=ys, blocks=blocks, dates=dates, min_n=MIN_STOCK_N).get("rho")
        nq_lead = _relabel(
            _lead_class(
                fwd=nq_fwd,
                sim=sim.get("rho"),
                rev=_spearman(_arr(b.get("sim_raw") or []), _arr(b.get("rev_nq") or [])).get("rho"),
                stable_fwd=bool(nq_sec.get("stable")),
            ),
            "nq",
        )
        es_lead = _relabel(
            _lead_class(
                fwd=es_fwd,
                sim=summarize_series(x=es, y=_arr(b.get("sim_raw") or []), blocks=blocks, dates=dates, min_n=MIN_STOCK_N).get("rho"),
                rev=_spearman(_arr(b.get("sim_raw") or []), _arr(b.get("rev_es") or [])).get("rho"),
                stable_fwd=bool(es_sec.get("stable")),
            ),
            "es",
        )
        nq_inc_lead = _relabel(
            _lead_class(
                fwd=nq_ae_fwd,
                sim=sim.get("rho"),
                rev=_spearman(_arr(b.get("sim_raw") or []), _arr(b.get("rev_nq") or [])).get("rho"),
                stable_fwd=bool(nq_after_es.get("stable")),
            ),
            "nq",
        )
        kind = _classify(
            nq_raw=nq_raw,
            es_raw=es_raw,
            nq_mkt=nq_mkt,
            es_mkt=es_mkt,
            nq_sec=nq_sec,
            es_sec=es_sec,
            nq_after_es=nq_after_es,
            es_after_nq=es_after_nq,
            nq_lead=nq_lead,
            es_lead=es_lead,
            nq_inc_lead=nq_inc_lead,
        )
        nq_flags = _bools(nq_lead)
        es_flags = _bools(es_lead)
        inc_flags = _bools(nq_inc_lead)
        usable = bool(
            (kind in ("NQ_SPECIFIC_AFTER_ES", "DIRECT_NQ_SENSITIVITY") and inc_flags["usable_before_stock_move"] or nq_flags["usable_before_stock_move"])
            or (kind == "DIRECT_ES_SENSITIVITY" and es_flags["usable_before_stock_move"])
        )
        if kind == "NQ_SPECIFIC_AFTER_ES":
            usable = bool(inc_flags["usable_before_stock_move"])
        elif kind == "DIRECT_NQ_SENSITIVITY":
            usable = bool(nq_flags["usable_before_stock_move"])
        elif kind == "DIRECT_ES_SENSITIVITY":
            usable = bool(es_flags["usable_before_stock_move"])
        else:
            usable = False
        rows.append(
            {
                "symbol": sym,
                "sector": sector_of.get(sym) or "",
                "class": kind,
                "nq_lead_lag": nq_lead,
                "es_lead_lag": es_lead,
                "nq_after_es_lead_lag": nq_inc_lead,
                "lead_lag": nq_inc_lead if kind == "NQ_SPECIFIC_AFTER_ES" else (nq_lead if kind == "DIRECT_NQ_SENSITIVITY" else (es_lead if kind == "DIRECT_ES_SENSITIVITY" else nq_lead)),
                "nq_raw_1m": nq_raw,
                "es_raw_1m": es_raw,
                "nq_market_adj_1m": nq_mkt,
                "es_market_adj_1m": es_mkt,
                "nq_sector_adj_1m": nq_sec,
                "es_sector_adj_1m": es_sec,
                "nq_after_es_1m": nq_after_es,
                "es_after_nq_1m": es_after_nq,
                "nq_curve_sec": nq_fwd,
                "es_curve_sec": es_fwd,
                "nq_after_es_curve_sec": nq_ae_fwd,
                "direct_sensitivity": kind in ("DIRECT_NQ_SENSITIVITY", "DIRECT_ES_SENSITIVITY", "NQ_SPECIFIC_AFTER_ES"),
                "causal_lead": bool(nq_flags["causal_lead"] or es_flags["causal_lead"] or inc_flags["causal_lead"]) and kind not in ("SIMULTANEOUS_ONLY", "NO_STABLE_RESPONSE"),
                "usable_before_stock_move": bool(usable),
                "simultaneous_common_news": bool(nq_flags["simultaneous_common_news"] or es_flags["simultaneous_common_news"]),
                "tech_focus": sym in TECH_FOCUS,
                "d1_d4_nq": nq_sec.get("d1_d4_agree"),
                "d1_d4_es": es_sec.get("d1_d4_agree"),
                "d1_d4_nq_after_es": nq_after_es.get("d1_d4_agree"),
            }
        )
    return rows


def _classify(
    *,
    nq_raw: dict[str, Any],
    es_raw: dict[str, Any],
    nq_mkt: dict[str, Any],
    es_mkt: dict[str, Any],
    nq_sec: dict[str, Any],
    es_sec: dict[str, Any],
    nq_after_es: dict[str, Any],
    es_after_nq: dict[str, Any],
    nq_lead: str,
    es_lead: str,
    nq_inc_lead: str,
) -> str:
    nq_inc = bool(nq_after_es.get("stable")) and nq_inc_lead.startswith("nq_leads")
    nq_dir = bool(nq_sec.get("stable")) and nq_lead.startswith("nq_leads")
    es_dir = bool(es_sec.get("stable")) and es_lead.startswith("es_leads")
    if nq_inc:
        return "NQ_SPECIFIC_AFTER_ES"
    if nq_dir and not nq_inc:
        # NQ sector residual exists but ES explains the same move.
        if bool(nq_after_es.get("stable")) is False and (es_dir or bool(es_sec.get("stable"))):
            return "DIRECT_ES_SENSITIVITY" if es_dir else "GLOBAL_MARKET_MEDIATED"
        return "DIRECT_NQ_SENSITIVITY"
    if es_dir:
        return "DIRECT_ES_SENSITIVITY"
    if nq_lead in SIM or es_lead in SIM:
        if not (nq_raw.get("stable") or es_raw.get("stable") or nq_mkt.get("stable") or es_mkt.get("stable")):
            return "SIMULTANEOUS_ONLY"
        if (nq_raw.get("stable") or es_raw.get("stable")) and not (nq_mkt.get("stable") or es_mkt.get("stable") or nq_sec.get("stable") or es_sec.get("stable")):
            return "GLOBAL_MARKET_MEDIATED"
        if (nq_mkt.get("stable") or es_mkt.get("stable")) and not (nq_sec.get("stable") or es_sec.get("stable")):
            return "SECTOR_MEDIATED"
        return "SIMULTANEOUS_ONLY"
    if (nq_raw.get("stable") or es_raw.get("stable")) and not (nq_mkt.get("stable") or es_mkt.get("stable") or nq_sec.get("stable") or es_sec.get("stable")):
        return "GLOBAL_MARKET_MEDIATED"
    if (nq_mkt.get("stable") or es_mkt.get("stable")) and not (nq_sec.get("stable") or es_sec.get("stable")):
        return "SECTOR_MEDIATED"
    return "NO_STABLE_RESPONSE"


def map_incremental_sector(obs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for sec, b in obs.items():
        nq = _arr(b.get("nq1") or [])
        es = _arr(b.get("es1") or [])
        if nq.size == 0:
            continue
        nq_ae = residualize(nq, es)
        es_an = residualize(es, nq)
        blocks = np.asarray(b.get("block") or [], dtype=object)
        dates = np.asarray(b.get("date") or [], dtype=object)
        y = _arr(b.get("yr1") or [])
        nq_res = summarize_series(x=nq, y=y, blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
        es_res = summarize_series(x=es, y=y, blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
        nq_after = summarize_series(x=nq_ae, y=y, blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
        es_after = summarize_series(x=es_an, y=y, blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
        nq_fwd = {h: summarize_series(x=nq, y=_arr(b.get(f"yr{h}") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N).get("rho") for h in FWD_HORIZONS}
        es_fwd = {h: summarize_series(x=es, y=_arr(b.get(f"yr{h}") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N).get("rho") for h in FWD_HORIZONS}
        nq_ae_fwd = {h: summarize_series(x=nq_ae, y=_arr(b.get(f"yr{h}") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N).get("rho") for h in FWD_HORIZONS}
        sim = summarize_series(x=nq, y=_arr(b.get("sim_resid") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
        nq_lead = _relabel(_lead_class(fwd=nq_fwd, sim=sim.get("rho"), rev=summarize_series(x=_arr(b.get("sim_resid") or []), y=_arr(b.get("rev_nq") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N).get("rho"), stable_fwd=bool(nq_res.get("stable"))), "nq")
        es_lead = _relabel(_lead_class(fwd=es_fwd, sim=summarize_series(x=es, y=_arr(b.get("sim_resid") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N).get("rho"), rev=summarize_series(x=_arr(b.get("sim_resid") or []), y=_arr(b.get("rev_es") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N).get("rho"), stable_fwd=bool(es_res.get("stable"))), "es")
        nq_inc_lead = _relabel(_lead_class(fwd=nq_ae_fwd, sim=sim.get("rho"), rev=summarize_series(x=_arr(b.get("sim_resid") or []), y=_arr(b.get("rev_nq") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N).get("rho"), stable_fwd=bool(nq_after.get("stable"))), "nq")
        rows.append(
            {
                "sector": sec,
                "nq_residual_1m": nq_res,
                "es_residual_1m": es_res,
                "nq_after_es_1m": nq_after,
                "es_after_nq_1m": es_after,
                "nq_lead_lag": nq_lead,
                "es_lead_lag": es_lead,
                "nq_after_es_lead_lag": nq_inc_lead,
                "nq_fwd_rho_residual": nq_fwd,
                "es_fwd_rho_residual": es_fwd,
                "nq_after_es_fwd_rho": nq_ae_fwd,
                "nq_incremental_after_es": bool(nq_after.get("stable") and nq_inc_lead.startswith("nq_leads")),
                "es_incremental_after_nq": bool(es_after.get("stable") and str(es_after.get("rho") or 0) != "nan" and abs(float(es_after.get("rho") or 0)) >= RHO_WEAK and int(es_after.get("d1_d4_agree") or 0) >= BLOCK_MIN),
                **_bools(nq_inc_lead if nq_after.get("stable") else nq_lead),
            }
        )
    return rows


def map_preopen(obs: dict[str, dict[str, Any]], *, xkey: str, residual_open: bool = False) -> list[dict[str, Any]]:
    rows = []
    for name, b in obs.items():
        x = _arr(b.get(xkey) or [])
        if x.size == 0:
            continue
        blocks = np.asarray(b.get("block") or [], dtype=object)
        dates = np.asarray(b.get("date") or [], dtype=object)
        y_open = _arr(b.get("y_open_resid") or b.get("y_open") or []) if residual_open else _arr(b.get("y_open") or [])
        y15 = b.get("y_15_resid") if residual_open else b.get("y_15")
        rows.append(
            {
                "name": name,
                "driver": xkey,
                "open": summarize_series(x=x, y=y_open, blocks=blocks, dates=dates, min_n=MIN_DAY_N),
                "m5": summarize_series(x=x, y=_arr(b.get("y_5") or []), blocks=blocks, dates=dates, min_n=MIN_DAY_N),
                "m15": summarize_series(x=x, y=_arr(y15 or []), blocks=blocks, dates=dates, min_n=MIN_DAY_N),
                "m30": summarize_series(x=x, y=_arr(b.get("y_30") or []), blocks=blocks, dates=dates, min_n=MIN_DAY_N),
            }
        )
    return rows


def map_preopen_incremental(obs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for name, b in obs.items():
        nq = _arr(b.get("nq_ov") or [])
        es = _arr(b.get("es_ov") or [])
        fx = _arr(b.get("fx_ov") or [])
        if nq.size == 0:
            continue
        y = _arr(b.get("y_open_resid") or b.get("y_open") or [])
        blocks = np.asarray(b.get("block") or [], dtype=object)
        dates = np.asarray(b.get("date") or [], dtype=object)
        nq_ae = residualize(nq, es)
        nq_afx = residualize(nq, fx)
        rows.append(
            {
                "name": name,
                "nq_open": summarize_series(x=nq, y=y, blocks=blocks, dates=dates, min_n=MIN_DAY_N),
                "es_open": summarize_series(x=es, y=y, blocks=blocks, dates=dates, min_n=MIN_DAY_N),
                "fx_open": summarize_series(x=fx, y=y, blocks=blocks, dates=dates, min_n=MIN_DAY_N),
                "nq_after_es_open": summarize_series(x=nq_ae, y=y, blocks=blocks, dates=dates, min_n=MIN_DAY_N),
                "nq_after_fx_open": summarize_series(x=nq_afx, y=y, blocks=blocks, dates=dates, min_n=MIN_DAY_N),
                "nq_m5": summarize_series(x=nq, y=_arr(b.get("y_5") or []), blocks=blocks, dates=dates, min_n=MIN_DAY_N),
                "nq_m15": summarize_series(x=nq, y=_arr(b.get("y_15_resid") or b.get("y_15") or []), blocks=blocks, dates=dates, min_n=MIN_DAY_N),
            }
        )
    return rows


def map_nq_sectors(obs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return _map_one_driver(obs=obs, xkey="nq1", driver="nq", residual=True)


def map_es_sectors(obs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return _map_one_driver(obs=obs, xkey="es1", driver="es", residual=True)
