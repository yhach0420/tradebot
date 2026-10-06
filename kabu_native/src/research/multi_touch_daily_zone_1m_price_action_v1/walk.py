"""Discovery walk: causal daily zones then 1m interactions. Confirmation and Frozen Validation never loaded."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes, same_day_reactions
from research.multi_touch_daily_zone_1m_price_action_v1.limits import new_limit_order, step_pending
from research.multi_touch_daily_zone_1m_price_action_v1.machine import new_zone_state, step_zone
from research.multi_touch_daily_zone_1m_price_action_v1.zones import snapshot_zones
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol, to_min
from research.reference_level_1m_price_action_discovery_v1.machine import new_state, step_static
from research.reference_level_1m_price_action_discovery_v1.outcomes import attach_fwd, stamp_event

PATH_KINDS = {
    "BREAK_ABOVE_ZONE",
    "BREAK_BELOW_ZONE",
    "ACCEPT2_ABOVE",
    "ACCEPT2_BELOW",
    "REJECT_FROM_RESISTANCE",
    "REJECT_FROM_SUPPORT",
    "RETEST_HOLD",
    "RETEST_ZONE",
    "FAIL_BACK_BELOW",
    "RESISTANCE_TO_SUPPORT_FLIP",
    "SUPPORT_TO_RESISTANCE_FLIP",
    "SUPPORT_FALSE_BREAK_RECLAIM",
    "PDH_BREAK_ABOVE",
    "RESISTANCE_BROKEN",
    "SUPPORT_BROKEN",
}

PLAYBOOK_KINDS = {
    "P1": ("ACCEPT2_ABOVE", "RESISTANCE", False),
    "P2": ("RETEST_HOLD", "RESISTANCE", False),
    "P3": ("RESISTANCE_TO_SUPPORT_FLIP", "RESISTANCE", False),
    "P4": ("REJECT_FROM_SUPPORT", "SUPPORT", False),
    "P5": ("SUPPORT_FALSE_BREAK_RECLAIM", "SUPPORT", False),
}


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


def _vol_rel(rec: dict[str, Any], i: int, n: int = 20) -> float:
    if i < n:
        return float("nan")
    w = rec["v"][i - n : i]
    m = float(np.nanmean(w))
    v = float(rec["v"][i])
    if not _finite(m) or m <= 0 or not _finite(v):
        return float("nan")
    return v / m


def next_dates_map(dates: list[str]) -> dict[str, str | None]:
    return {d: dates[i + 1] if i + 1 < len(dates) else None for i, d in enumerate(dates)}


def match_playbook(ev: dict[str, Any]) -> list[str]:
    if ev.get("control_single_touch"):
        return []
    kind = str(ev.get("event_kind") or "")
    role = str(ev.get("role") or "")
    ids = []
    if kind == "ACCEPT2_ABOVE" and role == "RESISTANCE":
        ids.append("P1")
    if kind == "RETEST_HOLD" and (role == "RESISTANCE" or ev.get("flipped")):
        ids.append("P2")
        if ev.get("flipped"):
            ids.append("P3")
    if kind == "RESISTANCE_TO_SUPPORT_FLIP":
        ids.append("P3")
    if kind == "REJECT_FROM_SUPPORT" and role == "SUPPORT":
        ids.append("P4")
    if kind == "SUPPORT_FALSE_BREAK_RECLAIM":
        ids.append("P5")
    if kind == "PDH_BREAK_ABOVE" or (kind == "BREAK_ABOVE" and ev.get("level_id") == "PDH"):
        ids.append("PDH")
    return ids


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
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)} multi_touch_zone", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=disc_set, forbidden_dates=conf | val)
    if minutes.empty:
        return {"ok": False}
    if minutes["date"].isin(list(conf | val)).any():
        raise RuntimeError("forbidden_partition_loaded")
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])

    hist: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reactions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    atlas_hits: list[dict[str, Any]] = []
    playbook_rows: list[dict[str, Any]] = []
    limit_orders: list[dict[str, Any]] = []
    zone_day_rows: list[dict[str, Any]] = []
    reaction_n = 0
    reaction_compact: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    n_days = int(minutes["date"].nunique())

    for di, (day, g) in enumerate(minutes.groupby("date", sort=True), start=1):
        date = str(day)
        block = str(date_to_block.get(date) or "")
        lookback = disc[: disc.index(date)] if date in disc_set else disc
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
            atr = atr20(hist[sym])
            snap = snapshot_zones(symbol=sym, reactions=reactions[sym], atr=atr, session_date=date, lookback_dates=lookback)
            zone_day_rows.append(
                {
                    "date": date,
                    "block": block,
                    "symbol": sym,
                    "atr": atr,
                    "n_res_active": len(snap["resistance_active"]),
                    "n_sup_active": len(snap["support_active"]),
                    "n_res_2": snap["n_res_2"],
                    "n_res_3": snap["n_res_3"],
                    "n_res_4": snap["n_res_4"],
                    "n_sup_2": snap["n_sup_2"],
                    "n_sup_3": snap["n_sup_3"],
                    "n_sup_4": snap["n_sup_4"],
                    "n_res_single": len(snap["resistance_single"]),
                    "symbol_zone_identity_integrity": True,
                }
            )
            zstates = [new_zone_state(z) for z in snap["resistance_active"] + snap["support_active"]]
            single_states = [new_zone_state(z) for z in snap["resistance_single"][:3]]
            pdh = hist[sym][-1]["high"] if hist[sym] else float("nan")
            pdh_st = new_state("PDH", "previous_day", pdh)
            pending: list[dict[str, Any]] = []
            n = int(rec["n"])
            first_i = None
            for i in range(n):
                t = rec["t"][i]
                if in_lunch(t):
                    continue
                if first_i is None:
                    first_i = i
                tm = to_min(t)
                if tm is None:
                    continue
                c = float(rec["c"][i])
                h = float(rec["h"][i])
                l = float(rec["l"][i])
                o = float(rec["o"][i])
                prev_c = float(rec["c"][i - 1]) if i > 0 else float("nan")
                for order in pending:
                    step_pending(order, t=t, o=o, h=h, l=l, c=c)
                emits: list[dict[str, Any]] = []
                for st in zstates + single_states:
                    emits.extend(step_zone(st, c=c, h=h, l=l, prev_c=prev_c, tm=tm, feature_bar=t))
                if pdh_st.get("available"):
                    pe = step_static(pdh_st, c=c, h=h, l=l, prev_c=prev_c, tm=tm, feature_bar=t)
                    for e in pe:
                        if e.get("event_kind") == "BREAK_ABOVE":
                            e["event_kind"] = "PDH_BREAK_ABOVE"
                            e["bucket"] = "pdh_break"
                            e["role"] = "PDH"
                            e["level_id"] = "PDH"
                            e["control_single_touch"] = False
                            emits.append(e)
                vw = rec["vw"][i]
                sess = rec["sess"][i]
                volr = _vol_rel(rec, i)
                var = float("nan")
                if i >= 20:
                    mva = float(np.nanmean(rec["va"][i - 20 : i]))
                    if _finite(mva) and mva > 0 and _finite(rec["va"][i]):
                        var = float(rec["va"][i] / mva)
                rng_rel = float("nan")
                if i >= 20:
                    mr = float(np.nanmean((rec["h"][i - 20 : i] - rec["l"][i - 20 : i])))
                    if _finite(mr) and mr > 0:
                        rng_rel = float((h - l) / mr)
                close_pos = float((c - l) / (h - l)) if _finite(h) and _finite(l) and h > l else float("nan")
                mktv = mkt.get(t)
                for ev in emits:
                    ev["date"] = date
                    ev["block"] = block
                    ev["symbol"] = sym
                    ev["sector"] = rec.get("sector") or ""
                    ev["close"] = c
                    ev["vol_rel20"] = volr
                    ev["va_rel20"] = var
                    ev["rng_rel20"] = rng_rel
                    ev["close_pos"] = close_pos
                    ev["above_vwap"] = bool(_finite(c) and _finite(vw) and c > vw)
                    ev["sess_ret"] = float(sess) if _finite(sess) else None
                    ev["mkt_rel"] = (float(sess) - float(mktv)) if _finite(sess) and _finite(mktv) else None
                    ev["pdh"] = pdh
                    stamp_event(ev)
                    if not ev.get("event_time") or rec["idx"].get(str(ev["event_time"])) is None:
                        flags.append({"ok": False, "reason": "event_time_bar_missing"})
                        continue
                    kind = str(ev.get("event_kind") or "")
                    hit = {
                        "date": date,
                        "block": block,
                        "symbol": sym,
                        "sector": ev.get("sector"),
                        "event_kind": kind,
                        "bucket": ev.get("bucket"),
                        "role": ev.get("role"),
                        "touch_bucket": ev.get("touch_bucket"),
                        "touch_count": ev.get("touch_count"),
                        "control_single_touch": bool(ev.get("control_single_touch")),
                        "flipped": bool(ev.get("flipped")),
                        "zone_id": ev.get("zone_id"),
                        "vol_rel20": volr,
                        "va_rel20": var,
                        "bars_inside": ev.get("bars_inside"),
                        "above_vwap": ev.get("above_vwap"),
                        "future_dependent": False,
                        "retroactive_timestamp": False,
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
                            }
                        )
                    atlas_hits.append(hit)
                    pids = match_playbook(ev)
                    if pids:
                        attach_fwd(ev, rec, until_session_flat=True)
                    for pid in pids:
                        row = {k: ev.get(k) for k in ev.keys() if k != "zone"}
                        row["playbook_id"] = pid
                        row["fwd_bars"] = ev.get("fwd_bars")
                        row["x0_entry_open"] = ev.get("x0_entry_open")
                        if pid == "PDH":
                            row["exit_kind"] = "lose_level"
                            row["level_value"] = pdh
                        else:
                            row["exit_kind"] = "lose_zone"
                            row["level_value"] = ev.get("zone_lo")
                        playbook_rows.append(row)
                    if kind == "BREAK_ABOVE_ZONE" and not ev.get("control_single_touch") and ev.get("role") == "RESISTANCE":
                        z = {"zone_id": ev.get("zone_id"), "lo": ev.get("zone_lo"), "hi": ev.get("zone_hi"), "touch_bucket": ev.get("touch_bucket")}
                        order = new_limit_order(placed_at=str(ev.get("event_time")), limit_price=float(ev.get("zone_hi")), zone=z, break_event=ev)
                        if order:
                            pending.append(order)
            for order in pending:
                if not order.get("filled") and not order.get("cancel_reason"):
                    order["cancel_reason"] = "session_end_unfilled"
                    order["cancel_time"] = rec["t"][-1]
                if order.get("filled") and order.get("fill_time"):
                    fake = {"event_time": order["fill_time"], "level_value": order.get("zone_lo")}
                    attach_fwd(fake, rec, until_session_flat=True)
                    order["fwd_bars"] = fake.get("fwd_bars")
                    order["x0_entry_open"] = order.get("fill_price")
                    order["path_type"] = fake.get("path_type")
                    order["continuation"] = fake.get("continuation")
                    order["reversal"] = fake.get("reversal")
                    order["mfe_bps"] = fake.get("mfe_bps")
                    order["mae_bps"] = fake.get("mae_bps")
                limit_orders.append(order)

            dbar = daily_from_minutes(rec, date)
            if dbar:
                hist[sym].append(dbar)
                if len(hist[sym]) > 80:
                    del hist[sym][:-80]
                nxt_d = nxt.get(date)
                for rx in same_day_reactions(dbar, atr, next_session=nxt_d):
                    reactions[sym].append(rx)
                    reaction_n += 1
                    reaction_compact.append({"date": date, "symbol": sym, "role": rx["role"], "price": rx["price"], "available_from": rx["available_from"], "reaction_date": rx["reaction_date"]})
        if di % 20 == 0 or di == n_days:
            print(f"WALK {di}/{n_days} hits={len(atlas_hits)} pb={len(playbook_rows)} rx={reaction_n} lim={len(limit_orders)}", flush=True)

    return {
        "ok": True,
        "atlas_hits": atlas_hits,
        "playbook_rows": playbook_rows,
        "limit_orders": limit_orders,
        "zone_day_rows": zone_day_rows,
        "reaction_n": reaction_n,
        "reaction_compact": reaction_compact,
        "n_days": n_days,
        "n_symbols_loaded": int(minutes["symbol"].nunique()),
        "loaded_confirmation": False,
        "loaded_frozen_validation": False,
        "causal_flags": flags,
        "symbol_zone_identity_integrity": True,
    }
