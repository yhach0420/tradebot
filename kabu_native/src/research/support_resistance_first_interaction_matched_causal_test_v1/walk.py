"""Frozen-detector walk with matched non-zone controls and placebos. No PnL selection."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.walk import next_dates_map
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol, to_min
from research.support_resistance_face_valid_first_interaction_rebuild_v1.machine import close_episode, new_episode, step_episode
from research.support_resistance_face_valid_first_interaction_rebuild_v1.select import select_salient, selected_list
from research.support_resistance_face_valid_first_interaction_rebuild_v1.swings import new_swing_state, step_swings
from research.support_resistance_face_valid_first_interaction_rebuild_v1.zones import snapshot_zones
from research.support_resistance_first_interaction_matched_causal_test_v1.direction import is_primary, is_secondary, sign_for
from research.support_resistance_first_interaction_matched_causal_test_v1.match import find_control, placebo_zones
from research.support_resistance_first_interaction_matched_causal_test_v1.outcomes import signed_path


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


def _sign(x: Any) -> str:
    if not _finite(x):
        return "na"
    if float(x) > 0:
        return "up"
    if float(x) < 0:
        return "down"
    return "flat"


def _bucket(t: str) -> str:
    tm = to_min(t)
    if tm is None:
        return "na"
    return f"{(int(tm) // 30) * 30:04d}"


def _vol_rel(rec: dict[str, Any], i: int, n: int = 20) -> float:
    if i < n:
        return float("nan")
    m = float(np.nanmean(rec["v"][i - n : i]))
    v = float(rec["v"][i])
    if not _finite(m) or m <= 0 or not _finite(v):
        return float("nan")
    return v / m


def _rng_rel(rec: dict[str, Any], i: int, n: int = 20) -> float:
    if i < n:
        return float("nan")
    m = float(np.nanmean(rec["rng"][i - n : i]))
    r = float(rec["rng"][i])
    if not _finite(m) or m <= 0 or not _finite(r):
        return float("nan")
    return r / m


def _in_any(h: float, l: float, zones: list[dict[str, Any]]) -> bool:
    for z in zones:
        if l <= float(z["hi"]) and h >= float(z["lo"]):
            return True
    return False


def _med_map(per: dict[str, dict[str, Any]], *, sector_of: dict[str, str] | None = None) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    by_t: dict[str, list[float]] = defaultdict(list)
    by_s: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for sym, rec in per.items():
        sec = (sector_of or {}).get(sym, "") if sector_of is not None else ""
        for i, t in enumerate(rec["t"]):
            s = rec["sess"][i]
            if _finite(s):
                by_t[str(t)].append(float(s))
                if sec:
                    by_s[sec][str(t)].append(float(s))
    mkt = {t: float(np.median(xs)) for t, xs in by_t.items() if xs}
    sec = {s: {t: float(np.median(xs)) for t, xs in d.items() if xs} for s, d in by_s.items()}
    return mkt, sec


def _feat_row(rec: dict[str, Any], i: int, *, zones: list[dict[str, Any]], mkt: dict[str, float], sec_med: dict[str, float], gap: str) -> dict[str, Any]:
    t = rec["t"][i]
    sess = rec["sess"][i]
    mktv = mkt.get(str(t))
    secv = sec_med.get(str(t))
    return {
        "i": i,
        "t": t,
        "bucket": _bucket(t),
        "r1": rec["r1"][i],
        "r3": rec["r3"][i],
        "r5": rec["r5"][i],
        "vol_rel": _vol_rel(rec, i),
        "rng_rel": _rng_rel(rec, i),
        "in_zone": _in_any(float(rec["h"][i]), float(rec["l"][i]), zones),
        "gap": gap,
        "mkt_sign": _sign((float(sess) - float(mktv)) if _finite(sess) and _finite(mktv) else None),
        "sec_sign": _sign((float(sess) - float(secv)) if _finite(sess) and _finite(secv) else None),
    }


def _pair(
    *,
    question: str,
    population: str,
    ep: dict[str, Any],
    rec: dict[str, Any],
    event_t: str | None,
    feat: list[dict[str, Any]],
    feat_by_i: dict[int, dict[str, Any]],
    by_bucket: dict[str, list[dict[str, Any]]],
    resistance: bool,
) -> dict[str, Any] | None:
    if not event_t:
        return None
    i = rec["idx"].get(str(event_t))
    if i is None:
        return None
    tf = feat_by_i.get(int(i))
    if tf is None:
        return None
    sign = sign_for(question, resistance=resistance)
    tr = signed_path(rec, int(i), sign)
    ctrl = find_control(feat, tf, by_bucket=by_bucket)
    row = {
        "question": question,
        "population": population,
        "symbol": ep.get("symbol"),
        "date": ep.get("date"),
        "block": ep.get("block"),
        "selection_slot": ep.get("selection_slot"),
        "selection_label": ep.get("selection_label"),
        "as_resistance": bool(resistance),
        "event_t": event_t,
        "matched": ctrl is not None,
        **{f"tr_{k}": v for k, v in tr.items() if k not in {"entry_i"}},
        "future_outcome_used_for_matching": False,
    }
    if ctrl is not None:
        ct = signed_path(rec, int(ctrl["i"]), sign)
        row.update({f"ct_{k}": v for k, v in ct.items() if k not in {"entry_i"}})
        row["control_t"] = ctrl.get("t")
    return row


def walk_matched(bind: dict[str, Any]) -> dict[str, Any]:
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
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)} sr_matched_causal", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=disc_set, forbidden_dates=conf | val)
    if minutes.empty:
        return {"ok": False, "reason": "empty_minutes"}
    if minutes["date"].isin(list(conf | val)).any():
        raise RuntimeError("forbidden_partition_loaded")
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])

    hist: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reactions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    swing_st: dict[str, dict[str, Any]] = defaultdict(new_swing_state)
    pairs: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
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
            dbar = daily_from_minutes(rec, date)
            opn = float(dbar["open"]) if dbar and _finite(dbar.get("open")) else float("nan")
            sel = select_salient(snap=snap, open_px=opn)
            per[symbol] = {
                "rec": rec,
                "atr": atr,
                "snap": snap,
                "sel": sel,
                "dbar": dbar,
                "open": opn,
                "sector": sector_of.get(symbol, "") or "",
                "pdc": hist[symbol][-1]["close"] if hist[symbol] else None,
                "prior": hist[symbol][-1] if hist[symbol] else None,
            }
        mkt, sec_all = _med_map({s: p["rec"] for s, p in per.items()}, sector_of={s: p["sector"] for s, p in per.items()})
        for symbol, pack in per.items():
            rec = pack["rec"]
            snap = pack["snap"]
            active = list(snap["resistance_active"]) + list(snap["support_active"])
            chosen = selected_list(pack["sel"])
            eps = [new_episode(z, date=date, symbol=symbol) for z in chosen]
            pbz = placebo_zones(prior=pack.get("prior"), atr=pack["atr"], active=active, open_px=pack["open"])
            peps = []
            for z in pbz:
                ep = new_episode(z, date=date, symbol=symbol)
                ep["as_resistance"] = str(z.get("role")) == "RESISTANCE"
                ep["placebo"] = True
                peps.append(ep)
            gap = _sign((float(pack["open"]) - float(pack["pdc"])) if _finite(pack.get("open")) and _finite(pack.get("pdc")) else None)
            sec_med = sec_all.get(pack["sector"], {})
            feat: list[dict[str, Any]] = []
            n = int(rec["n"])
            for i in range(n):
                t = rec["t"][i]
                if in_lunch(t):
                    continue
                o, h, l, c = float(rec["o"][i]), float(rec["h"][i]), float(rec["l"][i]), float(rec["c"][i])
                feat.append(_feat_row(rec, i, zones=active, mkt=mkt, sec_med=sec_med, gap=gap))
                for ep in eps + peps:
                    step_episode(ep, t=t, o=o, h=h, l=l, c=c)
            feat_by_i = {int(x["i"]): x for x in feat}
            by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for x in feat:
                if not x.get("in_zone"):
                    by_bucket[str(x.get("bucket") or "")].append(x)
            for ep in eps + peps:
                close_episode(ep)
                ep["block"] = block
                ep["sector"] = pack["sector"]
                if not ep.get("first_test"):
                    continue
                slot = str(ep.get("selection_slot") or "")
                if ep.get("placebo"):
                    pop = "PLACEBO"
                    counts["placebo_first_test_n"] += 1
                elif is_primary(slot):
                    pop = "PRIMARY"
                    counts["primary_first_test_n"] += 1
                    counts["first_test_n"] += 1
                elif is_secondary(slot):
                    pop = "ROLE_FLIP_SECONDARY"
                    counts["secondary_first_test_n"] += 1
                    counts["first_test_n"] += 1
                else:
                    continue
                res = bool(ep.get("as_resistance"))
                kw = dict(ep=ep, rec=rec, feat=feat, feat_by_i=feat_by_i, by_bucket=by_bucket, resistance=res)
                row_a = _pair(question="A", population=pop, event_t=ep.get("first_test_time"), **kw)
                if row_a:
                    row_a["resolution"] = ep.get("resolution")
                    pairs.append(row_a)
                if ep.get("break"):
                    row_b = _pair(question="B", population=pop, event_t=ep.get("break_time"), **kw)
                    if row_b:
                        pairs.append(row_b)
                    counts[f"{pop}_break_n"] += 1
                if ep.get("retest_hold"):
                    row_c = _pair(question="C", population=pop, event_t=ep.get("retest_hold_time"), **kw)
                    if row_c:
                        pairs.append(row_c)
                    counts[f"{pop}_retest_hold_n"] += 1
                if ep.get("failed_retest"):
                    row_d = _pair(question="D", population=pop, event_t=ep.get("failed_retest_time"), **kw)
                    if row_d:
                        pairs.append(row_d)
                    counts[f"{pop}_failed_retest_n"] += 1
                if ep.get("rejection"):
                    counts[f"{pop}_reject_n"] += 1

            dbar = pack.get("dbar")
            if dbar:
                hist[symbol].append(dbar)
                atr_c = atr20(hist[symbol])
                nxt_d = nxt.get(date)
                for rx in step_swings(swing_st[symbol], dbar, atr=atr_c, next_session=nxt_d, symbol=symbol):
                    reactions[symbol].append(rx)
        if di % 20 == 0 or di == n_days:
            print(f"WALK {di}/{n_days} pairs={len(pairs)} primary_ft={counts.get('primary_first_test_n', 0)}", flush=True)

    return {
        "ok": True,
        "pairs": pairs,
        "counts": dict(counts),
        "n_days": n_days,
        "n_symbols_loaded": int(minutes["symbol"].nunique()),
        "forbidden_loaded": False,
        "mixed_primary_secondary": False,
    }
