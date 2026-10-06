"""Frozen-detector walk: matchability covariates, A1/A2/C1 timestamps, propensity pools. No PnL."""
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
from research.support_resistance_matched_separation_not_a_strategy_v1 import PROPENSITY_MAX_CONTROLS
from research.support_resistance_matched_separation_not_a_strategy_v1.executable import a1_passive, a2_confirmed, c1_hold
from research.support_resistance_matched_separation_not_a_strategy_v1.outcomes import signed_from_event


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


def _sign_num(s: Any) -> float:
    v = str(s or "")
    if v == "up":
        return 1.0
    if v == "down":
        return -1.0
    return 0.0


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


def _zone_age(activated: Any, date: str, disc: list[str]) -> float:
    act = str(activated or "")
    if not act:
        return float("nan")
    n = 0
    for d in disc:
        if act <= d < date:
            n += 1
    return float(n)


def _med_map(per: dict[str, dict[str, Any]], *, sector_of: dict[str, str]) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    by_t: dict[str, list[float]] = defaultdict(list)
    by_s: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for sym, rec in per.items():
        sec = sector_of.get(sym, "")
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
    tm = to_min(t)
    return {
        "i": i,
        "t": t,
        "bucket": _bucket(t),
        "tod_min": float(tm - 9 * 60) if tm is not None else float("nan"),
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


def _cov(tf: dict[str, Any], ep: dict[str, Any], *, open_px: float, atr: float, disc: list[str], date: str) -> dict[str, Any]:
    res = bool(ep.get("as_resistance"))
    dist = float("nan")
    if _finite(open_px) and _finite(atr) and atr > 0 and _finite(ep.get("center")):
        dist = (float(ep["center"]) - float(open_px)) / float(atr) if res else (float(open_px) - float(ep["center"])) / float(atr)
    return {
        "tod_min": tf.get("tod_min"),
        "as_resistance": 1.0 if res else 0.0,
        "dist_open_atr": dist,
        "r1": tf.get("r1"),
        "r3": tf.get("r3"),
        "r5": tf.get("r5"),
        "rng_rel": tf.get("rng_rel"),
        "vol_rel": tf.get("vol_rel"),
        "gap_num": _sign_num(tf.get("gap")),
        "mkt_num": _sign_num(tf.get("mkt_sign")),
        "sec_num": _sign_num(tf.get("sec_sign")),
        "zone_age": _zone_age(ep.get("zone_activated_at"), date, disc),
        "touch_count": float(ep.get("touch_count") or 0),
        "bucket": tf.get("bucket"),
        "gap": tf.get("gap"),
        "mkt_sign": tf.get("mkt_sign"),
        "sec_sign": tf.get("sec_sign"),
    }


def _flat_path(prefix: str, path: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "entry_t",
        "decision_t",
        "same_bar_entry",
        "mfe_bps",
        "mae_bps",
        "end_bps",
        "p20_before_m20",
        "p40_before_m20",
        "p80_before_m30",
        "ret_5m_bps",
        "ret_10m_bps",
        "ret_20m_bps",
        "time_to_failure_min",
        "time_to_extension_min",
        "mfe_before_mae",
    )
    return {f"{prefix}{k}": path.get(k) for k in keys}


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
    open_px: float,
    atr: float,
    disc: list[str],
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
    tr = signed_from_event(rec, int(i), sign)
    ctrl = find_control(feat, tf, by_bucket=by_bucket)
    cov = _cov(tf, ep, open_px=open_px, atr=atr, disc=disc, date=str(ep.get("date") or ""))
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
        "future_outcome_used_for_matching": False,
        **cov,
        **{f"tr_{k}": v for k, v in tr.items() if k not in {"entry_i"}},
    }
    if ctrl is not None:
        ct = signed_from_event(rec, int(ctrl["i"]), sign)
        row.update({f"ct_{k}": v for k, v in ct.items() if k not in {"entry_i"}})
        row["control_t"] = ctrl.get("t")
    return row


def _pool_controls(
    *,
    rec: dict[str, Any],
    tf: dict[str, Any],
    by_bucket: dict[str, list[dict[str, Any]]],
    sign: int,
    ep: dict[str, Any],
    question: str,
    open_px: float,
    atr: float,
    disc: list[str],
) -> list[dict[str, Any]]:
    buckets = [str(tf.get("bucket") or "")]
    try:
        b0 = int(buckets[0])
        buckets.extend([f"{b0 - 30:04d}", f"{b0 + 30:04d}"])
    except ValueError:
        pass
    out: list[dict[str, Any]] = []
    for bucket in buckets:
        for x in by_bucket.get(bucket, ()):
            if int(x["i"]) == int(tf["i"]):
                continue
            path = signed_from_event(rec, int(x["i"]), sign)
            cov = _cov(x, ep, open_px=open_px, atr=atr, disc=disc, date=str(ep.get("date") or ""))
            out.append(
                {
                    "treated": 0,
                    "question": question,
                    "symbol": ep.get("symbol"),
                    "date": ep.get("date"),
                    "block": ep.get("block"),
                    "y_p20": path.get("p20_before_m20"),
                    "y_end": path.get("end_bps"),
                    "y_mfe": path.get("mfe_bps"),
                    **cov,
                }
            )
            if len(out) >= PROPENSITY_MAX_CONTROLS:
                return out
    return out


def walk_executable(bind: dict[str, Any]) -> dict[str, Any]:
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
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)} sr_matched_not_strategy", flush=True)
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
    pools: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    same_bar_n = 0
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
            eps = []
            for z in chosen:
                ep = new_episode(z, date=date, symbol=symbol)
                ep["touch_count"] = z.get("touch_count")
                ep["zone_activated_at"] = z.get("ZONE_ACTIVATED_AT")
                ep["center"] = z.get("center")
                eps.append(ep)
            pbz = placebo_zones(prior=pack.get("prior"), atr=pack["atr"], active=active, open_px=pack["open"])
            peps = []
            for z in pbz:
                ep = new_episode(z, date=date, symbol=symbol)
                ep["as_resistance"] = str(z.get("role")) == "RESISTANCE"
                ep["placebo"] = True
                ep["touch_count"] = 0
                ep["zone_activated_at"] = date
                ep["center"] = z.get("center")
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
                    continue
                else:
                    continue
                res = bool(ep.get("as_resistance"))
                kw = dict(
                    ep=ep,
                    rec=rec,
                    feat=feat,
                    feat_by_i=feat_by_i,
                    by_bucket=by_bucket,
                    resistance=res,
                    open_px=pack["open"],
                    atr=pack["atr"],
                    disc=disc,
                )
                row_a = _pair(question="A", population=pop, event_t=ep.get("first_test_time"), **kw)
                if row_a:
                    if pop == "PRIMARY":
                        a1 = a1_passive(ep, rec)
                        a2 = a2_confirmed(ep, rec)
                        row_a["a1_fillable_approx"] = a1.get("fillable_approx")
                        row_a["a1_gap_through"] = a1.get("gap_through")
                        row_a["a1_order_placed_at"] = a1.get("order_placed_at")
                        row_a["a1_fill_bar"] = a1.get("fill_bar")
                        row_a["a1_limit_price"] = a1.get("limit_price")
                        row_a["a1_classification"] = a1.get("classification")
                        row_a["a1_same_bar_placement"] = a1.get("same_bar_placement")
                        row_a.update(_flat_path("a1_", a1["path"]))
                        row_a["a2_confirmed"] = a2.get("confirmed")
                        row_a["a2_event_loss"] = a2.get("event_loss")
                        row_a["a2_bars_to_confirm"] = a2.get("bars_first_test_to_confirm")
                        row_a.update(_flat_path("a2_", a2["path"]))
                        if a2["path"].get("same_bar_entry"):
                            same_bar_n += 1
                        i_dec = rec["idx"].get(str(a2.get("decision_t") or ""))
                        if i_dec is not None and a2.get("confirmed"):
                            tf2 = feat_by_i.get(int(i_dec))
                            if tf2 is not None:
                                ctrl2 = find_control(feat, tf2, by_bucket=by_bucket)
                                row_a["a2_matched"] = ctrl2 is not None
                                if ctrl2 is not None:
                                    ct2 = signed_from_event(rec, int(ctrl2["i"]), sign_for("A", resistance=res))
                                    row_a.update({f"a2_ct_{k}": v for k, v in ct2.items() if k in {"p20_before_m20", "p40_before_m20", "p80_before_m30", "mfe_bps", "mae_bps", "end_bps", "entry_t"}})
                        tf = feat_by_i.get(int(rec["idx"].get(str(ep.get("first_test_time") or ""), -1)))
                        if tf is not None:
                            pools.extend(
                                _pool_controls(
                                    rec=rec,
                                    tf=tf,
                                    by_bucket=by_bucket,
                                    sign=sign_for("A", resistance=res),
                                    ep=ep,
                                    question="A",
                                    open_px=pack["open"],
                                    atr=pack["atr"],
                                    disc=disc,
                                )
                            )
                    pairs.append(row_a)
                if pop != "PRIMARY":
                    if ep.get("retest_hold"):
                        row_c = _pair(question="C", population=pop, event_t=ep.get("retest_hold_time"), **kw)
                        if row_c:
                            pairs.append(row_c)
                    continue
                if ep.get("break"):
                    row_b = _pair(question="B", population=pop, event_t=ep.get("break_time"), **kw)
                    if row_b:
                        pairs.append(row_b)
                    counts["PRIMARY_break_n"] += 1
                if ep.get("retest_hold"):
                    row_c = _pair(question="C", population=pop, event_t=ep.get("retest_hold_time"), **kw)
                    if row_c:
                        c1 = c1_hold(ep, rec)
                        row_c.update(_flat_path("c1_", c1["path"]))
                        row_c["c1_confirmed"] = c1.get("confirmed")
                        if c1["path"].get("same_bar_entry"):
                            same_bar_n += 1
                        i_dec = rec["idx"].get(str(c1.get("decision_t") or ""))
                        if i_dec is not None:
                            tf2 = feat_by_i.get(int(i_dec))
                            if tf2 is not None:
                                ctrl2 = find_control(feat, tf2, by_bucket=by_bucket)
                                row_c["c1_matched"] = ctrl2 is not None
                                if ctrl2 is not None:
                                    ct2 = signed_from_event(rec, int(ctrl2["i"]), sign_for("C", resistance=res))
                                    row_c.update({f"c1_ct_{k}": v for k, v in ct2.items() if k in {"p20_before_m20", "p40_before_m20", "p80_before_m30", "mfe_bps", "mae_bps", "end_bps", "entry_t"}})
                                pools.extend(
                                    _pool_controls(
                                        rec=rec,
                                        tf=tf2,
                                        by_bucket=by_bucket,
                                        sign=sign_for("C", resistance=res),
                                        ep=ep,
                                        question="C",
                                        open_px=pack["open"],
                                        atr=pack["atr"],
                                        disc=disc,
                                    )
                                )
                        pairs.append(row_c)
                    counts["PRIMARY_retest_hold_n"] += 1
                if ep.get("failed_retest"):
                    row_d = _pair(question="D", population=pop, event_t=ep.get("failed_retest_time"), **kw)
                    if row_d:
                        pairs.append(row_d)
                    counts["PRIMARY_failed_retest_n"] += 1
                if ep.get("rejection"):
                    counts["PRIMARY_reject_n"] += 1
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
        "pools": pools,
        "counts": dict(counts),
        "same_bar_entry_n": int(same_bar_n),
        "n_days": n_days,
        "n_symbols_loaded": int(minutes["symbol"].nunique()),
        "forbidden_loaded": False,
    }
