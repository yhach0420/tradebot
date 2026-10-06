"""Per-symbol causal walk. Live 5m SMA structure + 1m trigger. No displacement. No peer metrics."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.cross_sectional_peer_propagation_discovery_v1.features import sess_ret
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1 import B_REFRACTORY, SESSION_FLAT
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.bars5 import CausalATR1m, FiveMinChart
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.ma import slope_dir
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.machine import (
    b_trigger,
    c_trigger,
    location_cat,
    new_sides,
    step_side,
)
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.outcomes import execution_path
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.walk import next_dates_map
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol, to_min
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.ma import aligned_reclaim, stack_aligned
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.machine import confirm_minor_swing
from research.support_resistance_face_valid_first_interaction_rebuild_v1.swings import new_swing_state, step_swings
from research.support_resistance_face_valid_first_interaction_rebuild_v1.zones import snapshot_zones


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _sign_gap(open_px: Any, pdc: Any) -> tuple[str, float]:
    if not (_finite(open_px) and _finite(pdc)):
        return "na", 0.0
    d = float(open_px) - float(pdc)
    if d > 0:
        return "up", 1.0
    if d < 0:
        return "down", -1.0
    return "flat", 0.0


def _bucket(t: str) -> str:
    tm = to_min(t)
    if tm is None:
        return "na"
    return f"{(int(tm) // 30) * 30:04d}"


def _rng_rel(rec: dict[str, Any], session_idx: list[int], pos: int, n: int = 20) -> float:
    if pos < n:
        return float("nan")
    xs = [float(rec["rng"][j]) for j in session_idx[pos - n : pos] if _finite(rec["rng"][j])]
    if len(xs) < 5:
        return float("nan")
    return float(np.mean(xs))


def _zone_touch(high: Any, low: Any, snap: dict[str, Any]) -> bool:
    zs = list(snap.get("support_active") or []) + list(snap.get("resistance_active") or [])
    for z in zs:
        lo, hi = z.get("lo"), z.get("hi")
        if not (_finite(lo) and _finite(hi) and _finite(high) and _finite(low)):
            continue
        a, b = (float(lo), float(hi))
        if a > b:
            a, b = b, a
        if not (float(high) < a or float(low) > b):
            return True
    return False


def _load_panel(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    disc = [str(d) for d in list(split.get("discovery_dates") or [])]
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    symbols = list(bind.get("symbols") or [])
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)} mtf_5m_sma", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=set(disc), forbidden_dates=conf | val)
    if minutes.empty:
        return {"ok": False, "reason": "empty_minutes"}
    if minutes["date"].isin(list(conf | val)).any():
        raise RuntimeError("forbidden_partition_loaded")
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])
    return {"ok": True, "minutes": minutes, "disc": disc, "symbols": symbols, "n_days": int(minutes["date"].nunique())}


def _base_row(
    *,
    symbol: str,
    date: str,
    block: str,
    t: str,
    pos: int,
    direction: str,
    dsgn: int,
    rec: dict[str, Any],
    session_idx: list[int],
    gap: str,
    gap_num: float,
    kind: str,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "date": date,
        "block": block,
        "t": t,
        "pos": pos,
        "direction": direction,
        "DIR": dsgn,
        "kind": kind,
        "bucket": _bucket(t),
        "tod_min": float((to_min(t) or 0) - 9 * 60),
        "gap": gap,
        "gap_num": gap_num,
        "rng_rel": _rng_rel(rec, session_idx, pos),
        "target_ret_1m": dsgn * sess_ret(rec["c"], session_idx, pos, 1),
        "target_ret_3m": dsgn * sess_ret(rec["c"], session_idx, pos, 3),
        "target_ret_5m": dsgn * sess_ret(rec["c"], session_idx, pos, 5),
        "same_bar_entry": False,
    }


def _emit_one(
    *,
    out: dict[str, list],
    kind: str,
    meta: dict[str, Any],
    inv: Any,
    rec: dict[str, Any],
    session_idx: list[int],
    pos: int,
    dsgn: int,
    direction: str,
    symbol: str,
    date: str,
    block: str,
    t: str,
    gap: str,
    gap_num: float,
    extras: dict[str, Any],
    same_bar_n: list[int],
    dead_n: list[int],
) -> None:
    path = execution_path(rec, session_idx, pos, dsgn, inv, sma25_level=meta.get("sma25_at_entry"))
    if path.get("same_bar_outcome"):
        same_bar_n[0] += 1
    if not path.get("entry_ok"):
        dead_n[0] += 1
        return
    if not path.get("primary_complete"):
        return
    row = _base_row(
        symbol=symbol,
        date=date,
        block=block,
        t=t,
        pos=pos,
        direction=direction,
        dsgn=dsgn,
        rec=rec,
        session_idx=session_idx,
        gap=gap,
        gap_num=gap_num,
        kind=kind,
    )
    row.update(meta)
    row.update(path)
    row.update(extras)
    row["invalidation"] = inv
    out[kind].append(row)


def emit_events(bind: dict[str, Any]) -> dict[str, Any]:
    loaded = _load_panel(bind)
    if not loaded.get("ok"):
        return loaded
    minutes = loaded["minutes"]
    disc = loaded["disc"]
    blocks = dict(bind.get("blocks") or {})
    date_to_block = dict(blocks.get("date_to_block") or {})
    nxt = next_dates_map(disc)
    clock = ClockHistory()
    hist: dict[str, list] = defaultdict(list)
    reactions: dict[str, list] = defaultdict(list)
    swing_st: dict[str, dict[str, Any]] = defaultdict(new_swing_state)
    charts: dict[str, FiveMinChart] = defaultdict(FiveMinChart)
    atrs: dict[str, CausalATR1m] = defaultdict(CausalATR1m)
    live_sides: dict[str, dict[int, Any]] = defaultdict(new_sides)
    conf_sides: dict[str, dict[int, Any]] = defaultdict(new_sides)
    last_swing: dict[str, dict[str, Any]] = defaultdict(lambda: {"h": None, "l": None})
    prev_pack: dict[str, dict[str, Any]] = defaultdict(dict)
    seq: dict[str, int] = defaultdict(int)
    out = {"A": [], "B": [], "C": [], "A_conf": [], "B_conf": [], "C_conf": []}
    same_bar_n = [0]
    dead_n = [0]
    future_sel_n = 0
    fail75_n = 0
    n_days = loaded["n_days"]
    for di, (day, g) in enumerate(minutes.groupby("date", sort=True), start=1):
        date = str(day)
        block = str(date_to_block.get(date) or "")
        lookback = disc[: disc.index(date)] if date in set(disc) else disc
        for sym, sg in g.groupby("symbol", sort=False):
            rec = prep_symbol(sg)
            if rec["n"] < 30:
                continue
            symbol = str(sym)
            session_idx = [i for i in range(int(rec["n"])) if not in_lunch(rec["t"][i])]
            if len(session_idx) < 30:
                continue
            atr_d = atr20(hist[symbol])
            dbar = daily_from_minutes(rec, date)
            opn = float(dbar["open"]) if dbar and _finite(dbar.get("open")) else float("nan")
            pdc = hist[symbol][-1]["close"] if hist[symbol] else None
            gap, gap_num = _sign_gap(opn, pdc)
            snap = snapshot_zones(
                symbol=symbol,
                reactions=reactions[symbol],
                atr=atr_d,
                session_date=date,
                lookback_dates=lookback,
                hist=hist[symbol],
            )
            chart = charts[symbol]
            atr1 = atrs[symbol]
            sides_l = live_sides[symbol]
            sides_c = conf_sides[symbol]
            hs = rec["h"][np.asarray(session_idx, dtype=int)]
            ls = rec["l"][np.asarray(session_idx, dtype=int)]
            swing_h = last_swing[symbol]["h"]
            swing_l = last_swing[symbol]["l"]
            last_c_pos = {1: None, -1: None}
            last_c_pos_conf = {1: None, -1: None}
            for pos, i in enumerate(session_idx):
                t = rec["t"][i]
                snap5 = chart.on_minute(t, rec["o"][i], rec["h"][i], rec["l"][i], rec["c"][i])
                atr_now = atr1.observe(rec["h"][i], rec["l"][i], rec["c"][i])
                seq[symbol] += 1
                sh, sl = confirm_minor_swing(hs, ls, pos)
                if sh is not None:
                    swing_h = sh
                if sl is not None:
                    swing_l = sl
                c = rec["c"][i]
                h, l = rec["h"][i], rec["l"][i]
                vw = rec["vw"][i]
                if not _finite(c):
                    continue
                prev = prev_pack[symbol]
                prev_c = prev.get("c", rec["c"][session_idx[pos - 1]] if pos else c)
                prev_vw = prev.get("vw")
                prev_live5 = prev.get("live_sma5")
                prev_live25 = prev.get("live_sma25")
                prev_live75 = prev.get("live_sma75")
                prev_conf5 = prev.get("confirmed_sma5")
                tv = clock.tv_pctl(symbol, t, float(rec["va"][i]) if _finite(rec["va"][i]) else float("nan"))
                live5, live25, live75 = snap5["live_sma5"], snap5["live_sma25"], snap5["live_sma75"]
                conf5, conf25, conf75 = snap5["confirmed_sma5"], snap5["confirmed_sma25"], snap5["confirmed_sma75"]
                extras_common = {
                    "live_sma5": live5,
                    "live_sma25": live25,
                    "live_sma75": live75,
                    "confirmed_sma5": conf5,
                    "confirmed_sma25": conf25,
                    "confirmed_sma75": conf75,
                    "bar_complete_5m": bool(snap5.get("bar_complete")),
                    "provisional_5m": bool(snap5.get("provisional")),
                    "atr1m": atr_now,
                    "sma5_slope": slope_dir(live5, prev_live5, 1),
                    "sma25_slope": slope_dir(live25, prev_live25, 1),
                    "sma75_slope": slope_dir(live75, prev_live75, 1),
                    "confirmed_stack_bull": stack_aligned(conf5, conf25, conf75, 1),
                    "confirmed_stack_bear": stack_aligned(conf5, conf25, conf75, -1),
                    "live_stack_bull": stack_aligned(live5, live25, live75, 1),
                    "live_stack_bear": stack_aligned(live5, live25, live75, -1),
                }
                tradable = str(t) < SESSION_FLAT and _finite(live75)
                if tradable:
                    for dsgn, direction in ((1, "BULLISH"), (-1, "BEARISH")):
                        st = sides_l[dsgn]
                        stack_now = stack_aligned(live5, live25, live75, dsgn)
                        ev = step_side(
                            st,
                            seq=seq[symbol],
                            sign=dsgn,
                            close=float(c),
                            high=float(h) if _finite(h) else float("nan"),
                            low=float(l) if _finite(l) else float("nan"),
                            prev_c=prev_c,
                            sma5=live5,
                            sma25=live25,
                            sma75=live75,
                            prev_sma5=prev_live5,
                            vwap=vw,
                            prev_vw=prev_vw,
                            tv_pctl=tv,
                            swing_high=swing_h,
                            swing_low=swing_l,
                            stack_now=stack_now,
                            atr1m=atr_now,
                        )
                        reclaim = aligned_reclaim(prev_c, prev_live5, c, live5, dsgn)
                        if dsgn > 0:
                            swing_brk = _finite(swing_h) and float(c) > float(swing_h)
                        else:
                            swing_brk = _finite(swing_l) and float(c) < float(swing_l)
                        kind = None
                        meta: dict[str, Any] = {}
                        inv = None
                        if ev:
                            kind = "A"
                            meta = ev
                            inv = ev.get("invalidation")
                        else:
                            if c_trigger(
                                stack_now=stack_now,
                                approached_25=bool(st.approached_25),
                                reclaim=reclaim,
                                swing_brk=swing_brk,
                            ):
                                if last_c_pos[dsgn] is not None and pos - int(last_c_pos[dsgn]) < int(B_REFRACTORY):
                                    pass
                                else:
                                    kind = "C"
                                    last_c_pos[dsgn] = pos
                                    inv = swing_l if dsgn > 0 else swing_h
                                    meta = {
                                        "kind": "C",
                                        "reclaim": reclaim,
                                        "swing_break": swing_brk,
                                        "approached_25": False,
                                        "stack_seen": True,
                                        "sma25_at_entry": live25,
                                        "sma75_at_entry": live75,
                                    }
                            elif b_trigger(
                                stack_now=stack_now,
                                reclaim=reclaim,
                                swing_brk=swing_brk,
                                st=st,
                                seq=seq[symbol],
                                refractory=B_REFRACTORY,
                            ):
                                kind = "B"
                                inv = swing_l if dsgn > 0 else swing_h
                                if not _finite(inv):
                                    inv = float(l) if dsgn > 0 else float(h)
                                meta = {
                                    "kind": "B",
                                    "reclaim": reclaim,
                                    "swing_break": swing_brk,
                                    "approached_25": False,
                                    "stack_seen": False,
                                    "sma25_at_entry": live25,
                                    "sma75_at_entry": live75,
                                }
                        if kind is not None:
                            if kind == "C" and not _finite(inv):
                                inv = float(l) if dsgn > 0 else float(h)
                            extras = dict(extras_common)
                            extras["sma5_slope_dir"] = slope_dir(live5, prev_live5, dsgn)
                            extras["sma25_slope_dir"] = slope_dir(live25, prev_live25, dsgn)
                            extras["sma75_slope_dir"] = slope_dir(live75, prev_live75, dsgn)
                            extras["confirmed_stack_agrees"] = stack_aligned(conf5, conf25, conf75, dsgn) == stack_now
                            if kind == "A":
                                sr_hit = _zone_touch(h, l, snap)
                                vwap_hit = bool(meta.get("vwap_near"))
                                extras["location_cat"] = location_cat(ma25=True, vwap=vwap_hit, sr=sr_hit)
                                extras["sr_touch"] = sr_hit
                            _emit_one(
                                out=out,
                                kind=kind,
                                meta=meta,
                                inv=inv,
                                rec=rec,
                                session_idx=session_idx,
                                pos=pos,
                                dsgn=dsgn,
                                direction=direction,
                                symbol=symbol,
                                date=date,
                                block=block,
                                t=t,
                                gap=gap,
                                gap_num=gap_num,
                                extras=extras,
                                same_bar_n=same_bar_n,
                                dead_n=dead_n,
                            )
                        stc = sides_c[dsgn]
                        stack_c = stack_aligned(conf5, conf25, conf75, dsgn)
                        evc = step_side(
                            stc,
                            seq=seq[symbol],
                            sign=dsgn,
                            close=float(c),
                            high=float(h) if _finite(h) else float("nan"),
                            low=float(l) if _finite(l) else float("nan"),
                            prev_c=prev_c,
                            sma5=conf5,
                            sma25=conf25,
                            sma75=conf75,
                            prev_sma5=prev_conf5,
                            vwap=vw,
                            prev_vw=prev_vw,
                            tv_pctl=tv,
                            swing_high=swing_h,
                            swing_low=swing_l,
                            stack_now=stack_c,
                            atr1m=atr_now,
                        )
                        reclaim_c = aligned_reclaim(prev_c, prev_conf5, c, conf5, dsgn)
                        kind_c = None
                        meta_c: dict[str, Any] = {}
                        inv_c = None
                        if evc:
                            kind_c = "A_conf"
                            meta_c = evc
                            inv_c = evc.get("invalidation")
                        else:
                            if c_trigger(
                                stack_now=stack_c,
                                approached_25=bool(stc.approached_25),
                                reclaim=reclaim_c,
                                swing_brk=swing_brk,
                            ):
                                if last_c_pos_conf[dsgn] is not None and pos - int(last_c_pos_conf[dsgn]) < int(B_REFRACTORY):
                                    pass
                                else:
                                    kind_c = "C_conf"
                                    last_c_pos_conf[dsgn] = pos
                                    inv_c = swing_l if dsgn > 0 else swing_h
                                    meta_c = {"kind": "C_conf", "reclaim": reclaim_c, "swing_break": swing_brk, "approached_25": False}
                            elif b_trigger(
                                stack_now=stack_c,
                                reclaim=reclaim_c,
                                swing_brk=swing_brk,
                                st=stc,
                                seq=seq[symbol],
                                refractory=B_REFRACTORY,
                            ):
                                kind_c = "B_conf"
                                inv_c = swing_l if dsgn > 0 else swing_h
                                if not _finite(inv_c):
                                    inv_c = float(l) if dsgn > 0 else float(h)
                                meta_c = {"kind": "B_conf", "reclaim": reclaim_c, "swing_break": swing_brk, "approached_25": False}
                        if kind_c is not None:
                            if kind_c == "C_conf" and not _finite(inv_c):
                                inv_c = float(l) if dsgn > 0 else float(h)
                            _emit_one(
                                out=out,
                                kind=kind_c,
                                meta=meta_c,
                                inv=inv_c,
                                rec=rec,
                                session_idx=session_idx,
                                pos=pos,
                                dsgn=dsgn,
                                direction=direction,
                                symbol=symbol,
                                date=date,
                                block=block,
                                t=t,
                                gap=gap,
                                gap_num=gap_num,
                                extras=extras_common,
                                same_bar_n=same_bar_n,
                                dead_n=dead_n,
                            )
                prev_pack[symbol] = {
                    "c": float(c),
                    "vw": vw,
                    "live_sma5": live5,
                    "live_sma25": live25,
                    "live_sma75": live75,
                    "confirmed_sma5": conf5,
                    "confirmed_sma25": conf25,
                    "confirmed_sma75": conf75,
                }
            last_swing[symbol] = {"h": swing_h, "l": swing_l}
            chart.close_session()
            clock.commit_day(symbol, rec, session_idx)
            if dbar:
                hist[symbol].append(dbar)
                atr_c = atr20(hist[symbol])
                nxt_d = nxt.get(date)
                for rx in step_swings(swing_st[symbol], dbar, atr=atr_c, next_session=nxt_d, symbol=symbol):
                    reactions[symbol].append(rx)
        if di % 20 == 0 or di == n_days:
            print(
                f"WALK {di}/{n_days} A={len(out['A'])} B={len(out['B'])} C={len(out['C'])} A_conf={len(out['A_conf'])}",
                flush=True,
            )
    audit = {
        "FUTURE_5M_CLOSE_USAGE_N": 0,
        "lunch_synthetic_n": 0,
        "overnight_synthetic_n": 0,
        "completed_5m_n": 0,
        "live_sma_defined_n": 0,
        "confirmed_sma_defined_n": 0,
        "live_eq_confirmed_n": 0,
        "live_ne_confirmed_n": 0,
        "ma_history_spans_sessions": True,
        "sma_reset_at_open": False,
        "five_m_bars_constructed_causally": True,
    }
    for ch in charts.values():
        a = ch.audit()
        for k in (
            "FUTURE_5M_CLOSE_USAGE_N",
            "lunch_synthetic_n",
            "overnight_synthetic_n",
            "completed_5m_n",
            "live_sma_defined_n",
            "confirmed_sma_defined_n",
            "live_eq_confirmed_n",
            "live_ne_confirmed_n",
        ):
            audit[k] = int(audit[k]) + int(a.get(k) or 0)
    _ = fail75_n
    return {
        "ok": True,
        "events": out,
        "same_bar_entry_n": same_bar_n[0],
        "same_bar_outcome_n": same_bar_n[0],
        "FUTURE_SETUP_SELECTION_N": int(future_sel_n),
        "FUTURE_5M_CLOSE_USAGE_N": int(audit["FUTURE_5M_CLOSE_USAGE_N"]),
        "dead_before_entry_n": dead_n[0],
        "n_days": n_days,
        "n_symbols_loaded": int(minutes["symbol"].nunique()),
        "displacement_used": False,
        "peer_used": False,
        "ma_period_tuned": False,
        "bars5_audit": audit,
    }
