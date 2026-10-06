"""Walk native participation events in frozen S/R context. No PnL. No Confirmation."""
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
from research.support_resistance_first_interaction_matched_causal_test_v1.direction import is_primary, is_secondary
from research.support_resistance_matched_separation_not_a_strategy_v1.outcomes import next_entry_i, signed_from_entry
from research.native_participation_x_sr_context_discovery_v1 import REFRACTORY_BARS, SESSION_FLAT
from research.native_participation_x_sr_context_discovery_v1.match import find_same_symbol, index_controls
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory, displacement, participation_expand
from research.native_participation_x_sr_context_discovery_v1.placebo import placebo_band

PATH_KEYS = (
    "entry_t",
    "mfe_bps",
    "mae_bps",
    "end_bps",
    "mfe_before_mae",
    "p20_before_m20",
    "p40_before_m20",
    "p80_before_m30",
    "ret_5m_bps",
    "ret_10m_bps",
    "ret_20m_bps",
    "time_to_failure_min",
    "time_to_extension_min",
)


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


def _prior_rv(rec: dict[str, Any], session_idx: list[int], pos: int, n: int = 20) -> float:
    """Pre-event realized vol: mean relative range of prior completed session bars. Not the event bar."""
    if pos < n:
        return float("nan")
    xs = [float(rec["rng"][j]) for j in session_idx[pos - n : pos] if _finite(rec["rng"][j])]
    if len(xs) < 5:
        return float("nan")
    return float(np.mean(xs))


def _in_any(h: float, l: float, zones: list[dict[str, Any]]) -> bool:
    for z in zones:
        if l <= float(z["hi"]) and h >= float(z["lo"]):
            return True
    return False


def _med_map(per: dict[str, dict[str, Any]], *, sector_of: dict[str, str]) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    by_t: dict[str, list[float]] = defaultdict(list)
    by_s: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for rec, sec in ((p["rec"], p["sector"]) for p in per.values()):
        for i, t in enumerate(rec["t"]):
            s = rec["sess"][i]
            if _finite(s):
                by_t[str(t)].append(float(s))
                if sec:
                    by_s[sec][str(t)].append(float(s))
    mkt = {t: float(np.median(xs)) for t, xs in by_t.items() if xs}
    sec = {s: {t: float(np.median(xs)) for t, xs in d.items() if xs} for s, d in by_s.items()}
    return mkt, sec


def _thesis_sign(family: str, resistance: bool, direction: str) -> int:
    if family == "I1":
        return -1 if resistance else 1
    return 1 if direction == "BULLISH" else -1


def _classify(eps: list[dict[str, Any]], t: str, disp: str) -> tuple[str | None, dict[str, Any] | None]:
    """I2 then I1 then I3. I1 only while the first-test is unresolved, or on the rejection bar itself."""
    for ep in eps:
        if not is_primary(str(ep.get("selection_slot") or "")):
            continue
        res = bool(ep.get("as_resistance"))
        through = "BULLISH" if res else "BEARISH"
        if ep.get("break") and ep.get("break_time") == t and disp == through:
            return "I2", ep
    for ep in eps:
        if not is_primary(str(ep.get("selection_slot") or "")) or ep.get("i1_fired"):
            continue
        bounce = "BEARISH" if ep.get("as_resistance") else "BULLISH"
        if not ep.get("first_test") or ep.get("break") or disp != bounce:
            continue
        if str(ep.get("first_test_time") or "") > t:
            continue
        if ep.get("rejection") and str(ep.get("rejection_time") or "") != t:
            continue
        ep["i1_fired"] = True
        return "I1", ep
    for ep in eps:
        if not is_primary(str(ep.get("selection_slot") or "")) or ep.get("i3_fired") or not ep.get("retest_hold"):
            continue
        ht = str(ep.get("retest_hold_time") or "")
        through = "BULLISH" if ep.get("as_resistance") else "BEARISH"
        if ht and t > ht and disp == through:
            ep["i3_fired"] = True
            return "I3", ep
    return None, None


def _pack_event(
    *,
    rec: dict[str, Any],
    i: int,
    symbol: str,
    date: str,
    block: str,
    direction: str,
    family: str,
    ep: dict[str, Any] | None,
    tv_pctl: float | None,
    vol_pctl: float | None,
    feat: dict[str, Any],
    placebo: bool,
) -> dict[str, Any]:
    res = bool(ep.get("as_resistance")) if ep else (direction == "BEARISH")
    sign = _thesis_sign(family, res, direction)
    j = next_entry_i(rec, i)
    path = signed_from_entry(rec, j, sign)
    zid = str(ep.get("zone_id") or "") if ep else ""
    giveback = None
    if _finite(path.get("mfe_bps")) and _finite(path.get("end_bps")) and float(path["mfe_bps"]) > 0:
        giveback = float(path["mfe_bps"]) - float(path["end_bps"])
    return {
        "signal_id": f"{family}|{symbol}|{date}|{zid}|{rec['t'][i]}|{direction}",
        "family": family,
        "symbol": symbol,
        "date": date,
        "block": block,
        "i": i,
        "t": rec["t"][i],
        "direction": direction,
        "as_resistance": res,
        "zone_id": zid,
        "placebo": bool(placebo),
        "tv_clock_pctl": tv_pctl,
        "vol_clock_pctl": vol_pctl,
        "event_range": float(rec["h"][i]) - float(rec["l"][i]),
        "same_bar_entry": bool(j is not None and rec["t"][j] == rec["t"][i]),
        "sign": sign,
        **feat,
        **{f"tr_{k}": path.get(k) for k in PATH_KEYS},
        "tr_giveback": giveback,
    }


def _attach_ctrl(row: dict[str, Any], ctrl: dict[str, Any] | None, *, kind: str | None) -> dict[str, Any]:
    out = dict(row)
    out["matched"] = ctrl is not None
    out["match_kind"] = kind if ctrl is not None else None
    if ctrl is not None:
        for k in PATH_KEYS:
            out[f"ct_{k}"] = ctrl.get(f"tr_{k}")
        for k in ("r1", "r3", "r5", "rng_rel", "tod_min", "mkt_rel", "sec_rel", "gap", "mkt_sign", "sec_sign"):
            out[f"ct_{k}"] = ctrl.get(k)
        out["control_t"] = ctrl.get("t")
        out["control_symbol"] = ctrl.get("symbol")
    return out


def walk_context(bind: dict[str, Any]) -> dict[str, Any]:
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
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)} native_part_x_sr", flush=True)
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
    clock = ClockHistory()
    treated: list[dict[str, Any]] = []
    away: list[dict[str, Any]] = []
    placebo_rows: list[dict[str, Any]] = []
    hold_no_i3: list[dict[str, Any]] = []
    break_no_part: list[dict[str, Any]] = []
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
            }
        mkt, sec_all = _med_map(per, sector_of={s: p["sector"] for s, p in per.items()})
        for symbol, pack in per.items():
            rec = pack["rec"]
            snap = pack["snap"]
            active = list(snap["resistance_active"]) + list(snap["support_active"])
            chosen = selected_list(pack["sel"])
            eps = []
            peps = []
            occupied = (
                list(snap.get("resistance_active") or [])
                + list(snap.get("support_active") or [])
                + list(snap.get("resistance_broken") or [])
                + list(snap.get("support_broken") or [])
            )
            for z in chosen:
                ep = new_episode(z, date=date, symbol=symbol)
                ep["placebo"] = False
                eps.append(ep)
                pb = placebo_band(z, atr=pack["atr"], symbol=symbol, date=date, occupied=occupied)
                if pb is not None:
                    pep = new_episode(pb, date=date, symbol=symbol)
                    pep["placebo"] = True
                    peps.append(pep)
                    occupied.append(pb)
            gap = _sign((float(pack["open"]) - float(pack["pdc"])) if _finite(pack.get("open")) and _finite(pack.get("pdc")) else None)
            sec_med = sec_all.get(pack["sector"], {})
            n = int(rec["n"])
            session_idx = [i for i in range(n) if not in_lunch(rec["t"][i])]
            pos_of = {i: p for p, i in enumerate(session_idx)}
            last_fire = {"BULLISH": -10**9, "BEARISH": -10**9}
            for i in session_idx:
                t = rec["t"][i]
                o, h, l, c = float(rec["o"][i]), float(rec["h"][i]), float(rec["l"][i]), float(rec["c"][i])
                for ep in eps + peps:
                    step_episode(ep, t=t, o=o, h=h, l=l, c=c)
                if str(t) >= SESSION_FLAT:
                    continue
                tv_p = clock.tv_pctl(symbol, t, float(rec["va"][i]) if _finite(rec["va"][i]) else float("nan"))
                vol_p = clock.vol_pctl(symbol, t, float(rec["v"][i]) if _finite(rec["v"][i]) else float("nan"))
                disp = displacement(rec, i, session_idx, pos=pos_of[i])
                native = bool(disp and participation_expand(tv_p))
                pos = pos_of[i]
                if native and (pos - last_fire[str(disp)]) <= int(REFRACTORY_BARS):
                    native = False
                    counts["refractory_suppressed_n"] += 1
                if native:
                    last_fire[str(disp)] = pos
                    counts["native_event_n"] += 1
                i_prev = session_idx[pos - 1] if pos > 0 else None
                t_prev = rec["t"][i_prev] if i_prev is not None else t
                sess_p = rec["sess"][i_prev] if i_prev is not None else rec["sess"][i]
                mktv = mkt.get(str(t_prev))
                secv = sec_med.get(str(t_prev))
                feat = {
                    "bucket": _bucket(t),
                    "tod_min": float((to_min(t) or 0) - 9 * 60),
                    "r1": rec["r1"][i_prev] if i_prev is not None else float("nan"),
                    "r3": rec["r3"][i_prev] if i_prev is not None else float("nan"),
                    "r5": rec["r5"][i_prev] if i_prev is not None else float("nan"),
                    "rng_rel": _prior_rv(rec, session_idx, pos),
                    "gap": gap,
                    "mkt_sign": _sign((float(sess_p) - float(mktv)) if _finite(sess_p) and _finite(mktv) else None),
                    "sec_sign": _sign((float(sess_p) - float(secv)) if _finite(sess_p) and _finite(secv) else None),
                    "mkt_rel": (float(sess_p) - float(mktv)) if _finite(sess_p) and _finite(mktv) else None,
                    "sec_rel": (float(sess_p) - float(secv)) if _finite(sess_p) and _finite(secv) else None,
                    "in_zone": _in_any(h, l, active),
                }
                if not native:
                    for ep in eps:
                        if is_primary(str(ep.get("selection_slot") or "")) and ep.get("break") and ep.get("break_time") == t:
                            counts["break_without_participation_n"] += 1
                            break_no_part.append({"symbol": symbol, "date": date, "t": t, "block": block})
                            break
                    continue
                assigned, assigned_ep = _classify(eps, t, str(disp))
                row = _pack_event(
                    rec=rec, i=i, symbol=symbol, date=date, block=block, direction=str(disp),
                    family=assigned or "AWAY", ep=assigned_ep, tv_pctl=tv_p, vol_pctl=vol_p, feat=feat, placebo=False,
                )
                if row.get("same_bar_entry"):
                    same_bar_n += 1
                if assigned:
                    treated.append(row)
                    counts[f"{assigned}_n"] += 1
                elif not feat.get("in_zone") and not any(
                    e.get("first_test") and not e.get("break") and not e.get("rejection")
                    for e in eps
                    if is_primary(str(e.get("selection_slot") or ""))
                ):
                    away.append(row)
                    counts["away_n"] += 1
                else:
                    counts["near_sr_unassigned_n"] += 1
                fam, pep = _classify(peps, t, str(disp))
                if fam and pep:
                    placebo_rows.append(
                        _pack_event(
                            rec=rec, i=i, symbol=symbol, date=date, block=block, direction=str(disp),
                            family=fam, ep=pep, tv_pctl=tv_p, vol_pctl=vol_p, feat=feat, placebo=True,
                        )
                    )
                    counts[f"placebo_{fam}_n"] += 1
            for ep in eps:
                close_episode(ep)
                slot = str(ep.get("selection_slot") or "")
                if ep.get("first_test"):
                    counts["first_test_n"] += 1
                    if is_primary(slot):
                        counts["primary_first_test_n"] += 1
                    elif is_secondary(slot):
                        counts["secondary_first_test_n"] += 1
                if is_primary(slot) and ep.get("retest_hold") and not ep.get("i3_fired"):
                    counts["hold_without_i3_n"] += 1
                    i_h = rec["idx"].get(str(ep.get("retest_hold_time") or ""))
                    j = next_entry_i(rec, int(i_h)) if i_h is not None else None
                    sign = 1 if ep.get("as_resistance") else -1
                    path = signed_from_entry(rec, j, sign)
                    hold_no_i3.append(
                        {
                            "symbol": symbol,
                            "date": date,
                            "block": block,
                            "zone_id": ep.get("zone_id"),
                            **{f"tr_{k}": path.get(k) for k in ("p20_before_m20", "mfe_bps", "mae_bps", "end_bps")},
                        }
                    )
            dbar = pack.get("dbar")
            if dbar:
                hist[symbol].append(dbar)
                atr_c = atr20(hist[symbol])
                nxt_d = nxt.get(date)
                for rx in step_swings(swing_st[symbol], dbar, atr=atr_c, next_session=nxt_d, symbol=symbol):
                    reactions[symbol].append(rx)
            clock.commit_day(symbol, rec, session_idx)
        if di % 20 == 0 or di == n_days:
            print(
                f"WALK {di}/{n_days} native={counts.get('native_event_n', 0)} I1={counts.get('I1_n', 0)} I2={counts.get('I2_n', 0)} I3={counts.get('I3_n', 0)} away={counts.get('away_n', 0)}",
                flush=True,
            )

    by_ctrl = index_controls(away)
    by_cross: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for a in away:
        by_cross[(str(a.get("date") or ""), str(a.get("direction") or ""))].append(a)
    pairs: list[dict[str, Any]] = []
    cross_pairs: list[dict[str, Any]] = []
    for row in treated:
        pool = by_ctrl.get((str(row["symbol"]), str(row["direction"])), [])
        ctrl = find_same_symbol(row, pool, allow_same_date=False)
        if ctrl is not None:
            pairs.append(_attach_ctrl(row, ctrl, kind="same_symbol"))
            continue
        pairs.append(_attach_ctrl(row, None, kind=None))
        xs = [
            a
            for a in by_cross.get((str(row.get("date") or ""), str(row.get("direction") or "")), [])
            if a.get("symbol") != row.get("symbol")
        ]
        cx = find_same_symbol(row, xs, allow_same_date=True)
        if cx is not None:
            cross_pairs.append(_attach_ctrl(row, cx, kind="cross_symbol"))
    pb_pairs = []
    for row in placebo_rows:
        pool = by_ctrl.get((str(row["symbol"]), str(row["direction"])), [])
        pb_pairs.append(_attach_ctrl(row, find_same_symbol(row, pool, allow_same_date=False), kind="same_symbol"))

    return {
        "ok": True,
        "pairs": pairs,
        "cross_pairs": cross_pairs,
        "placebo_pairs": pb_pairs,
        "away": away,
        "hold_no_i3": hold_no_i3,
        "break_no_part": break_no_part,
        "counts": dict(counts),
        "same_bar_entry_n": int(same_bar_n),
        "n_days": n_days,
        "n_symbols_loaded": int(minutes["symbol"].nunique()),
        "forbidden_loaded": False,
        "future_normalization": False,
        "retrospective_event_selection": False,
    }
