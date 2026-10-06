"""Discovery walk: causal events at first knowable time. Confirmation and Frozen Validation never loaded."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol, to_min
from research.reference_level_1m_price_action_discovery_v1.levels import (
    DayOHLC,
    dist_bps,
    gap_side,
    near_level,
    prior_extremes,
    range_hl,
)
from research.reference_level_1m_price_action_discovery_v1.machine import (
    new_gap_state,
    new_state,
    step_csh_csl,
    step_gap,
    step_static,
    step_vwap,
)
from research.reference_level_1m_price_action_discovery_v1.outcomes import attach_fwd, stamp_event
from research.reference_level_1m_price_action_discovery_v1.playbooks import PLAYBOOK_SPECS, match_playbook


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _sector_of(bind: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        out[str(sym)] = str(row.get("tse33_name") or row.get("sector33_name") or "")
    return out


def _med(xs: list[float]) -> float:
    arr = np.asarray([x for x in xs if _finite(x)], dtype=float)
    if arr.size == 0:
        return float("nan")
    return float(np.median(arr))


def _market_med(per: dict[str, dict[str, Any]]) -> dict[str, float]:
    by_t: dict[str, list[float]] = defaultdict(list)
    for rec in per.values():
        for i, t in enumerate(rec["t"]):
            s = rec["sess"][i]
            if _finite(s):
                by_t[str(t)].append(float(s))
    return {t: _med(xs) for t, xs in by_t.items()}


def _static_levels(hist: list[DayOHLC], rec: dict[str, Any]) -> list[dict[str, Any]]:
    pdh = hist[-1].high if hist else float("nan")
    pdl = hist[-1].low if hist else float("nan")
    pdc = hist[-1].close if hist else float("nan")
    d5h, d5l = prior_extremes(hist, 5)
    d20h, d20l = prior_extremes(hist, 20)
    session_open = float(rec["o"][0]) if rec["n"] else float("nan")
    or5h, or5l = range_hl(rec, "09:00", "09:04")
    or15h, or15l = range_hl(rec, "09:00", "09:14")
    rows = [
        new_state("PDH", "previous_day", pdh),
        new_state("PDL", "previous_day", pdl),
        new_state("PDC", "previous_day", pdc),
        new_state("OPEN", "current_session", session_open),
        new_state("OR5H", "opening_range", or5h),
        new_state("OR5L", "opening_range", or5l),
        new_state("OR15H", "opening_range", or15h),
        new_state("OR15L", "opening_range", or15l),
        new_state("D5H", "multi_day", d5h),
        new_state("D5L", "multi_day", d5l),
        new_state("D20H", "multi_day", d20h),
        new_state("D20L", "multi_day", d20l),
    ]
    for st in rows:
        if st["level_id"] in {"OR5H", "OR5L"}:
            st["available"] = False
            st["_unlock"] = "09:05"
        elif st["level_id"] in {"OR15H", "OR15L"}:
            st["available"] = False
            st["_unlock"] = "09:15"
    return rows


PATH_KINDS = {
    "BREAK_ABOVE",
    "BREAK_BELOW",
    "ACCEPT1_ABOVE",
    "ACCEPT2_ABOVE",
    "ACCEPT1_BELOW",
    "ACCEPT2_BELOW",
    "REJECT_FROM_BELOW",
    "REJECT_FROM_ABOVE",
    "RETEST_HOLD_ABOVE",
    "RETEST_HOLD_BELOW",
    "RETEST_FAIL_ABOVE",
    "RETEST_FAIL_BELOW",
    "RECLAIM_ABOVE",
    "RECLAIM_BELOW",
    "FAILED_BREAK_ABOVE",
    "FAILED_BREAK_BELOW",
    "VWAP_RECLAIM",
    "VWAP_LOSS",
    "GAP_UP_HOLD_OR15",
    "GAP_DOWN_HOLD_OR15",
    "GAP_UP_PARTIAL_FILL",
    "GAP_DOWN_PARTIAL_FILL",
    "GAP_UP_FULL_FILL",
    "GAP_DOWN_FULL_FILL",
    "GAP_FILL_RECLAIM",
    "GAP_FILL_FAILURE",
    "GAP_DOWN_FILL_RECLAIM",
    "GAP_DOWN_FILL_FAILURE",
}


def walk_symbol_day(
    rec: dict[str, Any],
    hist: list[DayOHLC],
    *,
    date: str,
    block: str,
    mkt_med: dict[str, float],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    atlas_hits: list[dict[str, Any]] = []
    playbook_rows: list[dict[str, Any]] = []
    causal_flags: list[dict[str, Any]] = []
    n = int(rec["n"])
    if n < 20:
        return atlas_hits, playbook_rows, causal_flags
    statics = _static_levels(hist, rec)
    by_id = {st["level_id"]: st for st in statics}
    vwap_st = new_state("VWAP", "current_session", float("nan"), moving=True)
    csh_st = new_state("CSH", "current_session", float("nan"))
    csl_st = new_state("CSL", "current_session", float("nan"))
    csh_st["available"] = False
    csl_st["available"] = False
    pdc = hist[-1].close if hist else float("nan")
    session_open = float(rec["o"][0])
    gs = new_gap_state(side=gap_side(session_open, pdc), session_open=session_open, pdc=pdc)
    pdh = by_id["PDH"]["value"]
    pdl = by_id["PDL"]["value"]
    sess_high = float("nan")
    sess_low = float("nan")
    first_session_i = None
    for i in range(n):
        t = rec["t"][i]
        if in_lunch(t):
            continue
        if first_session_i is None:
            first_session_i = i
        tm = to_min(t)
        if tm is None:
            continue
        for st in statics:
            unlock = st.get("_unlock")
            if unlock and t >= unlock and _finite(st["value"]):
                st["available"] = True
        c = float(rec["c"][i])
        h = float(rec["h"][i])
        l = float(rec["l"][i])
        prev_c = float(rec["c"][i - 1]) if i > 0 else float("nan")
        vw = rec["vw"][i]
        emits: list[dict[str, Any]] = []
        if _finite(sess_high):
            csh_st["available"] = True
            csh_st["value"] = float(sess_high)
            emits.extend(step_csh_csl(csh_st, extreme=h, tm=tm, feature_bar=t, kind="SESSION_HIGH_EXTENSION"))
        if _finite(sess_low):
            csl_st["available"] = True
            csl_st["value"] = float(sess_low)
            emits.extend(step_csh_csl(csl_st, extreme=l, tm=tm, feature_bar=t, kind="SESSION_LOW_EXTENSION"))
        for st in statics:
            emits.extend(step_static(st, c=c, h=h, l=l, prev_c=prev_c, tm=tm, feature_bar=t))
        emits.extend(step_vwap(vwap_st, c=c, vw=float(vw) if _finite(vw) else float("nan"), prev_c=prev_c, tm=tm, feature_bar=t))
        emits.extend(
            step_gap(
                gs,
                c=c,
                h=h,
                l=l,
                tm=tm,
                feature_bar=t,
                first_bar=i == first_session_i,
                sess_high=sess_high if _finite(sess_high) else h,
                sess_low=sess_low if _finite(sess_low) else l,
            )
        )
        if _finite(h):
            sess_high = h if not _finite(sess_high) else max(sess_high, h)
        if _finite(l):
            sess_low = l if not _finite(sess_low) else min(sess_low, l)
        mkt = mkt_med.get(t)
        sess = rec["sess"][i]
        for ev in emits:
            ev["date"] = date
            ev["block"] = block
            ev["symbol"] = rec["symbol"]
            ev["sector"] = rec.get("sector") or ""
            ev["close"] = c
            ev["dist_bps"] = dist_bps(c, float(ev.get("level_value") or np.nan))
            ev["near_pdh"] = near_level(c, pdh)
            ev["near_pdl"] = near_level(c, pdl)
            ev["sess_ret"] = float(sess) if _finite(sess) else None
            ev["mkt_rel"] = (float(sess) - float(mkt)) if _finite(sess) and _finite(mkt) else None
            ev["gap_side"] = gs.get("side")
            stamp_event(ev)
            if not ev.get("event_time") or rec["idx"].get(str(ev["event_time"])) is None:
                causal_flags.append({"ok": False, "reason": "event_time_bar_missing", "feature_bar": t, "event_time": ev.get("event_time")})
                continue
            if ev.get("available_at") != ev.get("event_time"):
                causal_flags.append({"ok": False, "reason": "timestamp_mismatch", "feature_bar": t})
                ev["retroactive_timestamp"] = True
            kind = str(ev.get("event_kind") or "")
            hit = {
                "date": date,
                "block": block,
                "symbol": rec["symbol"],
                "sector": rec.get("sector") or "",
                "family": ev.get("family"),
                "level_id": ev.get("level_id"),
                "event_kind": kind,
                "bucket": ev.get("bucket"),
                "future_dependent": False,
                "retroactive_timestamp": bool(ev.get("retroactive_timestamp")),
                "near_pdh": bool(ev.get("near_pdh")),
                "near_pdl": bool(ev.get("near_pdl")),
            }
            if kind in PATH_KINDS:
                attach_fwd(ev, rec, until_session_flat=False)
                hit.update(
                    {
                        "path_type": ev.get("path_type"),
                        "continuation": ev.get("continuation"),
                        "reversal": ev.get("reversal"),
                        "stall": ev.get("stall"),
                        "favorable_first": ev.get("favorable_first"),
                        "adverse_first": ev.get("adverse_first"),
                        "mfe_bps": ev.get("mfe_bps"),
                        "mae_bps": ev.get("mae_bps"),
                        "time_to_mfe_min": ev.get("time_to_mfe_min"),
                        "time_to_mae_min": ev.get("time_to_mae_min"),
                        "fwd_crosses_lunch": ev.get("fwd_crosses_lunch"),
                        "fwd_last_hh": ev.get("fwd_last_hh"),
                    }
                )
            atlas_hits.append(hit)
            for spec in PLAYBOOK_SPECS:
                if match_playbook(ev, spec):
                    row = {
                        k: ev.get(k)
                        for k in (
                            "date",
                            "block",
                            "symbol",
                            "sector",
                            "level_id",
                            "family",
                            "event_kind",
                            "level_value",
                            "feature_bar",
                            "available_at",
                            "event_time",
                            "break_feature_bar",
                            "x0_entry_open",
                            "fwd_bars",
                            "path_type",
                            "continuation",
                            "reversal",
                            "stall",
                            "favorable_first",
                            "adverse_first",
                            "mfe_bps",
                            "mae_bps",
                            "near_pdh",
                            "near_pdl",
                            "gap_side",
                            "accept_not_moved_to_break",
                        )
                    }
                    row["playbook_id"] = spec["playbook_id"]
                    row["exit_kind"] = spec["exit_kind"]
                    attach_fwd(ev, rec, until_session_flat=True)
                    row["fwd_bars"] = ev.get("fwd_bars")
                    row["x0_entry_open"] = ev.get("x0_entry_open")
                    playbook_rows.append(row)
    return atlas_hits, playbook_rows, causal_flags


def finish_day_hist(rec: dict[str, Any], hist: list[DayOHLC], date: str) -> None:
    hs = [float(x) for x in rec["h"] if _finite(x)]
    ls = [float(x) for x in rec["l"] if _finite(x)]
    cs = [float(x) for x in rec["c"] if _finite(x)]
    os = [float(x) for x in rec["o"] if _finite(x)]
    if not hs or not ls or not cs or not os:
        return
    hist.append(DayOHLC(date=date, high=max(hs), low=min(ls), close=cs[-1], open=os[0]))
    if len(hist) > 20:
        del hist[:-20]


def walk_discovery(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = set(str(d) for d in list(split.get("discovery_dates") or []))
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    date_to_block = dict(blocks.get("date_to_block") or {})
    symbols = list(bind.get("symbols") or [])
    sector_of = _sector_of(bind)
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)} reference_level", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=disc, forbidden_dates=conf | val)
    if minutes.empty:
        return {"ok": False, "atlas_hits": [], "playbook_rows": [], "causal_flags": []}
    if minutes["date"].isin(list(conf | val)).any():
        raise RuntimeError("forbidden_partition_loaded")
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])
    hist: dict[str, list[DayOHLC]] = defaultdict(list)
    atlas_hits: list[dict[str, Any]] = []
    playbook_rows: list[dict[str, Any]] = []
    causal_flags: list[dict[str, Any]] = []
    n_days = int(minutes["date"].nunique())
    loaded_conf = bool(minutes["date"].isin(list(conf)).any())
    loaded_val = bool(minutes["date"].isin(list(val)).any())
    for di, (day, g) in enumerate(minutes.groupby("date", sort=True), start=1):
        date = str(day)
        block = str(date_to_block.get(date) or "")
        per: dict[str, dict[str, Any]] = {}
        for sym, sg in g.groupby("symbol", sort=False):
            rec = prep_symbol(sg)
            if rec["n"] < 20:
                continue
            rec["symbol"] = str(sym)
            rec["sector"] = sector_of.get(str(sym), "") or ""
            per[str(sym)] = rec
        mkt = _market_med(per)
        for sym, rec in per.items():
            hits, pbs, flags = walk_symbol_day(rec, hist[sym], date=date, block=block, mkt_med=mkt)
            atlas_hits.extend(hits)
            playbook_rows.extend(pbs)
            causal_flags.extend(flags)
            finish_day_hist(rec, hist[sym], date)
        if di % 20 == 0 or di == n_days:
            print(f"WALK {di}/{n_days} hits={len(atlas_hits)} playbooks={len(playbook_rows)}", flush=True)
    return {
        "ok": True,
        "atlas_hits": atlas_hits,
        "playbook_rows": playbook_rows,
        "causal_flags": causal_flags,
        "n_days": n_days,
        "n_symbols_loaded": int(minutes["symbol"].nunique()),
        "loaded_confirmation": loaded_conf,
        "loaded_frozen_validation": loaded_val,
        "forbidden_rows": 0,
    }
