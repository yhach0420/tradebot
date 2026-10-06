"""Discovery walk: swing confirm → zones → salient select → one first-interaction episode. No PnL."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.walk import next_dates_map
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.support_resistance_face_valid_first_interaction_rebuild_v1.machine import close_episode, new_episode, step_episode
from research.support_resistance_face_valid_first_interaction_rebuild_v1.select import select_salient, selected_list
from research.support_resistance_face_valid_first_interaction_rebuild_v1.swings import new_swing_state, step_swings
from research.support_resistance_face_valid_first_interaction_rebuild_v1.zones import snapshot_zones


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _sector_of(bind: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        out[str(sym)] = str(row.get("tse33_name") or row.get("sector33_name") or row.get("sector17_name") or "")
    return out


def _vol_regime(atr: float, hist_atr: list[float]) -> str:
    xs = [x for x in hist_atr if _finite(x) and x > 0]
    if not xs or not _finite(atr) or atr <= 0:
        return "unknown"
    lo, hi = float(np.percentile(xs, 33)), float(np.percentile(xs, 67))
    if atr < lo:
        return "low"
    if atr > hi:
        return "high"
    return "mid"


def walk_discovery(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = [str(d) for d in list(split.get("discovery_dates") or [])]
    disc_set = set(disc)
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    date_to_block = dict(blocks.get("date_to_block") or {})
    nxt = next_dates_map(disc)
    symbols = list(bind.get("symbols") or [])
    sector_of = _sector_of(bind)
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)} sr_face_rebuild", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=disc_set, forbidden_dates=conf | val)
    if minutes.empty:
        return {"ok": False, "reason": "empty_minutes"}
    if minutes["date"].isin(list(conf | val)).any():
        raise RuntimeError("forbidden_partition_loaded")
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])

    hist: dict[str, list[dict[str, Any]]] = defaultdict(list)
    hist_atr: dict[str, list[float]] = defaultdict(list)
    reactions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    swing_st: dict[str, dict[str, Any]] = defaultdict(new_swing_state)
    rows: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    all_rx: list[dict[str, Any]] = []
    gate = Counter()
    n_days = int(minutes["date"].nunique())

    for di, (day, g) in enumerate(minutes.groupby("date", sort=True), start=1):
        date = str(day)
        block = str(date_to_block.get(date) or "")
        lookback = disc[: disc.index(date)] if date in disc_set else disc
        for sym, sg in g.groupby("symbol", sort=False):
            rec = prep_symbol(sg)
            if rec["n"] < 20:
                continue
            symbol = str(sym)
            atr = atr20(hist[symbol])
            snap = snapshot_zones(
                symbol=symbol,
                reactions=reactions[symbol],
                atr=atr,
                session_date=date,
                lookback_dates=lookback,
                hist=hist[symbol],
            )
            gate["retroactive_zone_n"] += int(snap.get("retroactive_zone_n") or 0)
            gate["already_broken_active_zone_n"] += int(snap.get("already_broken_active_n") or 0)
            gate["future_pivot_leakage_n"] += int(snap.get("future_pivot_n") or 0)
            dbar = daily_from_minutes(rec, date)
            opn = float(dbar["open"]) if dbar and _finite(dbar.get("open")) else float("nan")
            sel = select_salient(snap=snap, open_px=opn)
            chosen = selected_list(sel)
            eps = [new_episode(z, date=date, symbol=symbol) for z in chosen]
            seen: set[str] = set()
            for ep in eps:
                zid = str(ep.get("zone_id") or "")
                if zid in seen:
                    ep["duplicate"] = True
                    gate["duplicate_first_interaction_n"] += 1
                seen.add(zid)
            n = int(rec["n"])
            for i in range(n):
                t = rec["t"][i]
                if in_lunch(t):
                    continue
                o, h, l, c = float(rec["o"][i]), float(rec["h"][i]), float(rec["l"][i]), float(rec["c"][i])
                for ep in eps:
                    step_episode(ep, t=t, o=o, h=h, l=l, c=c)
            for ep in eps:
                close_episode(ep)
                ep["block"] = block
                gate["retest_without_clear_n"] += int(ep.get("retest_without_clear") or 0)
                gate["retest_hold_and_fail_overlap_n"] += int(ep.get("hold_fail_overlap") or 0)
                gate["retroactive_event_timestamp_n"] += int(ep.get("retroactive_timestamp") or 0)
                episodes.append(ep)
            rows.append(
                {
                    "date": date,
                    "block": block,
                    "symbol": symbol,
                    "sector": sector_of.get(symbol, "") or "",
                    "atr": atr if _finite(atr) else None,
                    "vol_regime": _vol_regime(atr, hist_atr[symbol]),
                    "open": opn if _finite(opn) else None,
                    "n_res_active": len(snap["resistance_active"]),
                    "n_sup_active": len(snap["support_active"]),
                    "n_res_broken": len(snap["resistance_broken"]),
                    "n_sup_broken": len(snap["support_broken"]),
                    "n_selected_resistance": sel.get("n_selected_resistance") or 0,
                    "n_selected_support": sel.get("n_selected_support") or 0,
                    "no_salient_resistance": bool(sel.get("no_salient_resistance")),
                    "no_salient_support": bool(sel.get("no_salient_support")),
                    "no_level": bool(sel.get("no_salient_resistance") and sel.get("no_salient_support")),
                    "selected_res_center": (sel["resistance"] or {}).get("center") if sel.get("resistance") else None,
                    "selected_sup_center": (sel["support"] or {}).get("center") if sel.get("support") else None,
                    "selected_res_lo": (sel["resistance"] or {}).get("lo") if sel.get("resistance") else None,
                    "selected_res_hi": (sel["resistance"] or {}).get("hi") if sel.get("resistance") else None,
                    "selected_sup_lo": (sel["support"] or {}).get("lo") if sel.get("support") else None,
                    "selected_sup_hi": (sel["support"] or {}).get("hi") if sel.get("support") else None,
                }
            )
            if dbar:
                hist[symbol].append(dbar)
                if _finite(atr) and atr > 0:
                    hist_atr[symbol].append(float(atr))
                atr_c = atr20(hist[symbol])
                nxt_d = nxt.get(date)
                for rx in step_swings(swing_st[symbol], dbar, atr=atr_c, next_session=nxt_d, symbol=symbol):
                    if str(rx.get("confirmation_date") or "") < str(rx.get("pivot_date") or ""):
                        gate["future_pivot_leakage_n"] += 1
                        rx["future_pivot"] = True
                    if str(rx.get("available_from") or "") <= str(rx.get("confirmation_date") or ""):
                        gate["future_pivot_leakage_n"] += 1
                    reactions[symbol].append(rx)
                    all_rx.append(rx)
        if di % 20 == 0 or di == n_days:
            print(f"WALK {di}/{n_days} sd={len(rows)} rx={len(all_rx)} ep={len(episodes)}", flush=True)

    return {
        "ok": True,
        "minutes": minutes,
        "hist": dict(hist),
        "reactions": dict(reactions),
        "rows": rows,
        "episodes": episodes,
        "all_rx": all_rx,
        "gate": dict(gate),
        "n_days": n_days,
        "n_symbols_loaded": int(minutes["symbol"].nunique()),
        "forbidden_loaded": False,
        "disc": disc,
    }
