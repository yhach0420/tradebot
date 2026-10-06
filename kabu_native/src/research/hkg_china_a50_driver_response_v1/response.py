"""HKG vs CHI impulse-response, incremental residual, reverse causality, D1-D4. Characterization, not lag search."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.hkg_china_a50_driver_response_v1 import (
    BLOCK_MIN,
    FWD_HORIZONS,
    MIN_CLOCK_N,
    MIN_DAY_N,
    MIN_STOCK_N,
    PRIMARY_LEAD_HORIZONS,
    RHO_WEAK,
    TECH_FOCUS,
)
from research.nq_es_sector_symbol_response_v1.response import residualize
from research.usd_jpy_sector_symbol_response_v1.response import _arr, _lead_class, _spearman, summarize_series

SIM = {"simultaneous_only", "simultaneous_common_news"}
NEAR = "unusable_near_contemporaneous"


def _relabel(lead: str, driver: str) -> str:
    return str(lead).replace("fx_leads", f"{driver}_leads").replace("stocks_lead_fx", "stock_leads_driver")


def _bools(lead: str) -> dict[str, bool]:
    causal = lead.startswith("hkg_leads") or lead.startswith("chi_leads") or lead.startswith("driver_leads")
    sim_news = lead == "simultaneous_common_news"
    usable = bool(causal and not sim_news and lead != "simultaneous_only" and lead != NEAR)
    return {
        "causal_lead": bool(causal),
        "usable_before_stock_move": bool(usable),
        "simultaneous_common_news": bool(sim_news),
        "simultaneous_only": lead == "simultaneous_only",
        "near_contemporaneous": lead == NEAR,
        "stock_leads_driver": lead == "stock_leads_driver",
    }


def _fwd(x: np.ndarray, b: dict[str, Any], ykey: str, blocks: np.ndarray, dates: np.ndarray, min_n: int) -> dict[int, Any]:
    out = {}
    for h in FWD_HORIZONS:
        out[h] = summarize_series(x=x, y=_arr(b.get(f"{ykey}{h}") or []), blocks=blocks, dates=dates, min_n=min_n)
    return out


def map_incremental_stock(obs: dict[str, dict[str, Any]], sector_of: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for sym, b in obs.items():
        hkg = _arr(b.get("hkg1") or [])
        chi = _arr(b.get("chi1") or [])
        if hkg.size == 0:
            continue
        hkg_ac = residualize(hkg, chi)
        chi_ah = residualize(chi, hkg)
        fx = _arr(b.get("fx1") or [])
        es = _arr(b.get("es1") or [])
        hkg_afx = residualize(hkg, fx) if fx.size == hkg.size else hkg
        chi_afx = residualize(chi, fx) if fx.size == chi.size else chi
        hkg_aes = residualize(hkg, es) if es.size == hkg.size else hkg
        chi_aes = residualize(chi, es) if es.size == chi.size else chi
        blocks = np.asarray(b.get("block") or [], dtype=object)
        dates = np.asarray(b.get("date") or [], dtype=object)
        y = _arr(b.get("ys1") or [])
        ym = _arr(b.get("ym1") or [])
        yr = _arr(b.get("y1") or [])
        hkg_sec = summarize_series(x=hkg, y=y, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        chi_sec = summarize_series(x=chi, y=y, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        hkg_mkt = summarize_series(x=hkg, y=ym, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        chi_mkt = summarize_series(x=chi, y=ym, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        hkg_raw = summarize_series(x=hkg, y=yr, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        chi_raw = summarize_series(x=chi, y=yr, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        hkg_after_chi = summarize_series(x=hkg_ac, y=y, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        chi_after_hkg = summarize_series(x=chi_ah, y=y, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        hkg_after_fx = summarize_series(x=hkg_afx, y=y, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        chi_after_fx = summarize_series(x=chi_afx, y=y, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        hkg_after_es = summarize_series(x=hkg_aes, y=y, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        chi_after_es = summarize_series(x=chi_aes, y=y, blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        sim = summarize_series(x=hkg, y=_arr(b.get("sim_raw") or []), blocks=blocks, dates=dates, min_n=MIN_STOCK_N)
        hkg_fwd = {h: summarize_series(x=hkg, y=_arr(b.get(f"ys{h}") or []), blocks=blocks, dates=dates, min_n=MIN_STOCK_N).get("rho") for h in FWD_HORIZONS}
        chi_fwd = {h: summarize_series(x=chi, y=_arr(b.get(f"ys{h}") or []), blocks=blocks, dates=dates, min_n=MIN_STOCK_N).get("rho") for h in FWD_HORIZONS}
        hkg_ac_fwd = {h: summarize_series(x=hkg_ac, y=_arr(b.get(f"ys{h}") or []), blocks=blocks, dates=dates, min_n=MIN_STOCK_N).get("rho") for h in FWD_HORIZONS}
        chi_ah_fwd = {h: summarize_series(x=chi_ah, y=_arr(b.get(f"ys{h}") or []), blocks=blocks, dates=dates, min_n=MIN_STOCK_N).get("rho") for h in FWD_HORIZONS}
        hkg_lead = _relabel(
            _lead_class(
                fwd=hkg_fwd,
                sim=sim.get("rho"),
                rev=_spearman(_arr(b.get("sim_raw") or []), _arr(b.get("rev_hkg") or [])).get("rho"),
                stable_fwd=bool(hkg_sec.get("stable")),
            ),
            "hkg",
        )
        chi_lead = _relabel(
            _lead_class(
                fwd=chi_fwd,
                sim=summarize_series(x=chi, y=_arr(b.get("sim_raw") or []), blocks=blocks, dates=dates, min_n=MIN_STOCK_N).get("rho"),
                rev=_spearman(_arr(b.get("sim_raw") or []), _arr(b.get("rev_chi") or [])).get("rho"),
                stable_fwd=bool(chi_sec.get("stable")),
            ),
            "chi",
        )
        hkg_inc_lead = _relabel(
            _lead_class(fwd=hkg_ac_fwd, sim=sim.get("rho"), rev=_spearman(_arr(b.get("sim_raw") or []), _arr(b.get("rev_hkg") or [])).get("rho"), stable_fwd=bool(hkg_after_chi.get("stable"))),
            "hkg",
        )
        chi_inc_lead = _relabel(
            _lead_class(fwd=chi_ah_fwd, sim=sim.get("rho"), rev=_spearman(_arr(b.get("sim_raw") or []), _arr(b.get("rev_chi") or [])).get("rho"), stable_fwd=bool(chi_after_hkg.get("stable"))),
            "chi",
        )
        kind = _classify(
            hkg_raw=hkg_raw,
            chi_raw=chi_raw,
            hkg_mkt=hkg_mkt,
            chi_mkt=chi_mkt,
            hkg_sec=hkg_sec,
            chi_sec=chi_sec,
            hkg_after_chi=hkg_after_chi,
            chi_after_hkg=chi_after_hkg,
            hkg_lead=hkg_lead,
            chi_lead=chi_lead,
            hkg_inc_lead=hkg_inc_lead,
            chi_inc_lead=chi_inc_lead,
        )
        hkg_flags = _bools(hkg_lead)
        chi_flags = _bools(chi_lead)
        hkg_inc_flags = _bools(hkg_inc_lead)
        chi_inc_flags = _bools(chi_inc_lead)
        usable = False
        if kind == "HKG_DIRECT_LEAD":
            usable = bool(hkg_inc_flags["usable_before_stock_move"] or hkg_flags["usable_before_stock_move"])
        elif kind == "CHINA_A50_DIRECT_LEAD":
            usable = bool(chi_inc_flags["usable_before_stock_move"] or chi_flags["usable_before_stock_move"])
        elif kind == "GREATER_CHINA_COMMON_LEAD":
            usable = bool(hkg_flags["usable_before_stock_move"] or chi_flags["usable_before_stock_move"])
        rows.append(
            {
                "symbol": sym,
                "sector": sector_of.get(sym) or "",
                "class": kind,
                "hkg_lead_lag": hkg_lead,
                "chi_lead_lag": chi_lead,
                "hkg_after_chi_lead_lag": hkg_inc_lead,
                "chi_after_hkg_lead_lag": chi_inc_lead,
                "hkg_raw_1m": hkg_raw,
                "chi_raw_1m": chi_raw,
                "hkg_market_adj_1m": hkg_mkt,
                "chi_market_adj_1m": chi_mkt,
                "hkg_sector_adj_1m": hkg_sec,
                "chi_sector_adj_1m": chi_sec,
                "hkg_after_chi_1m": hkg_after_chi,
                "chi_after_hkg_1m": chi_after_hkg,
                "hkg_after_fx_1m": hkg_after_fx,
                "chi_after_fx_1m": chi_after_fx,
                "hkg_after_es_1m": hkg_after_es,
                "chi_after_es_1m": chi_after_es,
                "hkg_curve_sec": hkg_fwd,
                "chi_curve_sec": chi_fwd,
                "usable_before_stock_move": bool(usable),
                "simultaneous_common_news": bool(hkg_flags["simultaneous_common_news"] or chi_flags["simultaneous_common_news"]),
                "tech_focus": sym in TECH_FOCUS,
                "d1_d4_hkg": hkg_sec.get("d1_d4_agree"),
                "d1_d4_chi": chi_sec.get("d1_d4_agree"),
                "d1_d4_hkg_after_chi": hkg_after_chi.get("d1_d4_agree"),
                "d1_d4_chi_after_hkg": chi_after_hkg.get("d1_d4_agree"),
                "survives_japan_market_removal": bool((hkg_mkt.get("stable") and hkg_lead.startswith("hkg_leads")) or (chi_mkt.get("stable") and chi_lead.startswith("chi_leads"))),
                "survives_sector_removal": bool((hkg_sec.get("stable") and hkg_lead.startswith("hkg_leads")) or (chi_sec.get("stable") and chi_lead.startswith("chi_leads"))),
                "hkg_incremental_after_fx": bool(hkg_after_fx.get("stable")),
                "chi_incremental_after_fx": bool(chi_after_fx.get("stable")),
                "hkg_incremental_after_es": bool(hkg_after_es.get("stable")),
                "chi_incremental_after_es": bool(chi_after_es.get("stable")),
            }
        )
    return rows


def _classify(
    *,
    hkg_raw: dict[str, Any],
    chi_raw: dict[str, Any],
    hkg_mkt: dict[str, Any],
    chi_mkt: dict[str, Any],
    hkg_sec: dict[str, Any],
    chi_sec: dict[str, Any],
    hkg_after_chi: dict[str, Any],
    chi_after_hkg: dict[str, Any],
    hkg_lead: str,
    chi_lead: str,
    hkg_inc_lead: str,
    chi_inc_lead: str,
) -> str:
    hkg_inc = bool(hkg_after_chi.get("stable")) and hkg_inc_lead.startswith("hkg_leads")
    chi_inc = bool(chi_after_hkg.get("stable")) and chi_inc_lead.startswith("chi_leads")
    hkg_dir = bool(hkg_sec.get("stable")) and hkg_lead.startswith("hkg_leads")
    chi_dir = bool(chi_sec.get("stable")) and chi_lead.startswith("chi_leads")
    if hkg_inc and not chi_inc:
        return "HKG_DIRECT_LEAD"
    if chi_inc and not hkg_inc:
        return "CHINA_A50_DIRECT_LEAD"
    if hkg_inc and chi_inc:
        return "GREATER_CHINA_COMMON_LEAD"
    if hkg_dir and chi_dir:
        return "GREATER_CHINA_COMMON_LEAD"
    if hkg_dir:
        return "HKG_DIRECT_LEAD"
    if chi_dir:
        return "CHINA_A50_DIRECT_LEAD"
    if hkg_lead in SIM or chi_lead in SIM:
        if (hkg_raw.get("stable") or chi_raw.get("stable")) and not (hkg_mkt.get("stable") or chi_mkt.get("stable") or hkg_sec.get("stable") or chi_sec.get("stable")):
            return "JAPAN_MARKET_MEDIATED"
        if (hkg_mkt.get("stable") or chi_mkt.get("stable")) and not (hkg_sec.get("stable") or chi_sec.get("stable")):
            return "JAPAN_SECTOR_MEDIATED"
        if hkg_lead == "simultaneous_common_news" or chi_lead == "simultaneous_common_news":
            return "SIMULTANEOUS_COMMON_NEWS"
        return "SIMULTANEOUS_COMMON_NEWS"
    if hkg_lead == NEAR or chi_lead == NEAR:
        return "UNUSABLE_NEAR_CONTEMPORANEOUS"
    if (hkg_raw.get("stable") or chi_raw.get("stable")) and not (hkg_mkt.get("stable") or chi_mkt.get("stable") or hkg_sec.get("stable") or chi_sec.get("stable")):
        return "JAPAN_MARKET_MEDIATED"
    if (hkg_mkt.get("stable") or chi_mkt.get("stable")) and not (hkg_sec.get("stable") or chi_sec.get("stable")):
        return "JAPAN_SECTOR_MEDIATED"
    return "NO_STABLE_RESPONSE"


def map_incremental_sector(obs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for sec, b in obs.items():
        hkg = _arr(b.get("hkg1") or [])
        chi = _arr(b.get("chi1") or [])
        if hkg.size == 0:
            continue
        hkg_ac = residualize(hkg, chi)
        chi_ah = residualize(chi, hkg)
        blocks = np.asarray(b.get("block") or [], dtype=object)
        dates = np.asarray(b.get("date") or [], dtype=object)
        y = _arr(b.get("yr1") or [])
        hkg_res = summarize_series(x=hkg, y=y, blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
        chi_res = summarize_series(x=chi, y=y, blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
        hkg_after = summarize_series(x=hkg_ac, y=y, blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
        chi_after = summarize_series(x=chi_ah, y=y, blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
        hkg_fwd = {h: summarize_series(x=hkg, y=_arr(b.get(f"yr{h}") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N).get("rho") for h in FWD_HORIZONS}
        chi_fwd = {h: summarize_series(x=chi, y=_arr(b.get(f"yr{h}") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N).get("rho") for h in FWD_HORIZONS}
        sim = summarize_series(x=hkg, y=_arr(b.get("sim_resid") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
        hkg_lead = _relabel(
            _lead_class(
                fwd=hkg_fwd,
                sim=sim.get("rho"),
                rev=summarize_series(x=_arr(b.get("sim_resid") or []), y=_arr(b.get("rev_hkg") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N).get("rho"),
                stable_fwd=bool(hkg_res.get("stable")),
            ),
            "hkg",
        )
        chi_lead = _relabel(
            _lead_class(
                fwd=chi_fwd,
                sim=summarize_series(x=chi, y=_arr(b.get("sim_resid") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N).get("rho"),
                rev=summarize_series(x=_arr(b.get("sim_resid") or []), y=_arr(b.get("rev_chi") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N).get("rho"),
                stable_fwd=bool(chi_res.get("stable")),
            ),
            "chi",
        )
        rows.append(
            {
                "sector": sec,
                "hkg_residual_1m": hkg_res,
                "chi_residual_1m": chi_res,
                "hkg_after_chi_1m": hkg_after,
                "chi_after_hkg_1m": chi_after,
                "hkg_lead_lag": hkg_lead,
                "chi_lead_lag": chi_lead,
                "hkg_fwd_rho_residual": hkg_fwd,
                "chi_fwd_rho_residual": chi_fwd,
                "hkg_leads": bool(hkg_res.get("stable") and hkg_lead.startswith("hkg_leads")),
                "chi_leads": bool(chi_res.get("stable") and chi_lead.startswith("chi_leads")),
                "hkg_incremental_after_chi": bool(hkg_after.get("stable") and str(hkg_after.get("rho") or "") not in ("", "None") and abs(float(hkg_after.get("rho") or 0)) >= RHO_WEAK and int(hkg_after.get("d1_d4_agree") or 0) >= BLOCK_MIN),
                "chi_incremental_after_hkg": bool(chi_after.get("stable") and str(chi_after.get("rho") or "") not in ("", "None") and abs(float(chi_after.get("rho") or 0)) >= RHO_WEAK and int(chi_after.get("d1_d4_agree") or 0) >= BLOCK_MIN),
                "up_mean_hkg": hkg_res.get("mean_y_fx_up"),
                "down_mean_hkg": hkg_res.get("mean_y_fx_down"),
                "up_mean_chi": chi_res.get("mean_y_fx_up"),
                "down_mean_chi": chi_res.get("mean_y_fx_down"),
                "d1_d4_hkg": hkg_res.get("d1_d4_agree"),
                "d1_d4_chi": chi_res.get("d1_d4_agree"),
            }
        )
    return rows


def map_one_driver_sector(obs: dict[str, dict[str, Any]], *, xkey: str, driver: str) -> list[dict[str, Any]]:
    rows = []
    for sec, b in obs.items():
        x = _arr(b.get(xkey) or [])
        if x.size == 0:
            continue
        blocks = np.asarray(b.get("block") or [], dtype=object)
        dates = np.asarray(b.get("date") or [], dtype=object)
        curve = []
        fwd = {}
        for h in FWD_HORIZONS:
            raw = summarize_series(x=x, y=_arr(b.get(f"y{h}") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
            res = summarize_series(x=x, y=_arr(b.get(f"yr{h}") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
            curve.append({"h": h, "raw": raw, "residual": res})
            fwd[h] = res.get("rho")
        sim = summarize_series(x=x, y=_arr(b.get("sim_resid") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
        rev = summarize_series(x=_arr(b.get("sim_resid") or []), y=_arr(b.get(f"rev_{driver}") or []), blocks=blocks, dates=dates, min_n=MIN_CLOCK_N)
        lead = _relabel(_lead_class(fwd=fwd, sim=sim.get("rho"), rev=rev.get("rho"), stable_fwd=any(c["residual"].get("stable") and int(c["h"]) in PRIMARY_LEAD_HORIZONS for c in curve)), driver)
        rows.append(
            {
                "sector": sec,
                "driver": driver,
                "raw_1m": curve[0]["raw"],
                "residual_1m": curve[0]["residual"],
                "fwd_rho_raw": {c["h"]: c["raw"].get("rho") for c in curve},
                "fwd_rho_residual": {c["h"]: c["residual"].get("rho") for c in curve},
                "reverse_rho": rev.get("rho"),
                "lead_lag": lead,
                **_bools(lead),
            }
        )
    return rows


def map_foreign_open(obs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for name, b in obs.items():
        drivers = list(b.get("driver") or [])
        if not drivers:
            continue
        for drv in sorted(set(str(x) for x in drivers)):
            mask = np.asarray([str(x) == drv for x in drivers])
            x = _arr(b.get("x1") or [])[mask]
            if x.size == 0:
                continue
            blocks = np.asarray(b.get("block") or [], dtype=object)[mask]
            dates = np.asarray(b.get("date") or [], dtype=object)[mask]
            rec = {"name": name, "driver": drv, "first_live_mode": None}
            hh = [str(h) for h, m in zip(b.get("first_live_jst") or [], mask) if m]
            rec["first_live_mode"] = Counter_hh(hh)
            for h in (1, 3, 5, 10):
                rec[f"y{h}"] = summarize_series(x=x, y=_arr(b.get(f"y{h}") or [])[mask], blocks=blocks, dates=dates, min_n=MIN_DAY_N)
            rows.append(rec)
    return rows


def Counter_hh(xs: list[str]) -> str | None:
    if not xs:
        return None
    from collections import Counter

    return Counter(xs).most_common(1)[0][0]
