"""Per-symbol causal walk. SMA5/25/75 playbook. No displacement start. No peer metrics."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.cross_sectional_peer_propagation_discovery_v1.features import sess_ret
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.walk import next_dates_map
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol, to_min
from research.sma5_25_75_trend_pullback_playbook_discovery_v1 import B_REFRACTORY, SESSION_FLAT, SMA75
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.ma import aligned_reclaim, bar_touches, sma_pack, stack_aligned
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.machine import (
    b_trigger,
    c_trigger,
    confirm_minor_swing,
    location_cat,
    new_sides,
    step_side,
)
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.outcomes import execution_path
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
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)} sma_pullback", flush=True)
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
    out = {"A": [], "B": [], "C": []}
    same_bar_n = 0
    future_sel_n = 0
    dead_n = 0
    n_days = loaded["n_days"]
    for di, (day, g) in enumerate(minutes.groupby("date", sort=True), start=1):
        date = str(day)
        block = str(date_to_block.get(date) or "")
        lookback = disc[: disc.index(date)] if date in set(disc) else disc
        for sym, sg in g.groupby("symbol", sort=False):
            rec = prep_symbol(sg)
            if rec["n"] < int(SMA75) + 5:
                continue
            symbol = str(sym)
            session_idx = [i for i in range(int(rec["n"])) if not in_lunch(rec["t"][i])]
            if len(session_idx) < int(SMA75) + 5:
                continue
            atr = atr20(hist[symbol])
            dbar = daily_from_minutes(rec, date)
            opn = float(dbar["open"]) if dbar and _finite(dbar.get("open")) else float("nan")
            pdc = hist[symbol][-1]["close"] if hist[symbol] else None
            gap, gap_num = _sign_gap(opn, pdc)
            snap = snapshot_zones(
                symbol=symbol,
                reactions=reactions[symbol],
                atr=atr,
                session_date=date,
                lookback_dates=lookback,
                hist=hist[symbol],
            )
            sc = np.asarray([rec["c"][i] for i in session_idx], dtype=float)
            sma = sma_pack(sc)
            hs = rec["h"][np.asarray(session_idx, dtype=int)]
            ls = rec["l"][np.asarray(session_idx, dtype=int)]
            sides = new_sides()
            swing_h = swing_l = None
            last_c_pos = {1: None, -1: None}
            for pos, i in enumerate(session_idx):
                t = rec["t"][i]
                if str(t) >= SESSION_FLAT:
                    break
                sh, sl = confirm_minor_swing(hs, ls, pos)
                if sh is not None:
                    swing_h = sh
                if sl is not None:
                    swing_l = sl
                if pos < int(SMA75) - 1:
                    continue
                c = rec["c"][i]
                h, l = rec["h"][i], rec["l"][i]
                vw = rec["vw"][i]
                if not _finite(c):
                    continue
                prev_i = session_idx[pos - 1] if pos else i
                prev_c = rec["c"][prev_i]
                prev_vw = rec["vw"][prev_i]
                tv = clock.tv_pctl(symbol, t, float(rec["va"][i]) if _finite(rec["va"][i]) else float("nan"))
                for dsgn, direction in ((1, "BULLISH"), (-1, "BEARISH")):
                    st = sides[dsgn]
                    s5, s25, s75 = sma["sma5"][pos], sma["sma25"][pos], sma["sma75"][pos]
                    prev_s5 = sma["sma5"][pos - 1] if pos else float("nan")
                    stack_now = stack_aligned(s5, s25, s75, dsgn)
                    ev = step_side(
                        st,
                        pos=pos,
                        sign=dsgn,
                        close=float(c),
                        high=float(h) if _finite(h) else float("nan"),
                        low=float(l) if _finite(l) else float("nan"),
                        prev_c=prev_c,
                        sma5=s5,
                        sma25=s25,
                        sma75=s75,
                        prev_sma5=prev_s5,
                        vwap=vw,
                        prev_vw=prev_vw,
                        tv_pctl=tv,
                        swing_high=swing_h,
                        swing_low=swing_l,
                        stack_now=stack_now,
                    )
                    reclaim = aligned_reclaim(prev_c, prev_s5, c, s5, dsgn)
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
                            pullback=bool(st.pullback),
                        ):
                            if last_c_pos[dsgn] is not None and pos - int(last_c_pos[dsgn]) < int(B_REFRACTORY):
                                pass
                            else:
                                kind = "C"
                                last_c_pos[dsgn] = pos
                                inv = swing_l if dsgn > 0 else swing_h
                                meta = {"kind": "C", "reclaim": reclaim, "swing_break": swing_brk, "approached_25": False, "stack_seen": True}
                        elif b_trigger(stack_now=stack_now, reclaim=reclaim, swing_brk=swing_brk, st=st, pos=pos, refractory=B_REFRACTORY):
                            kind = "B"
                            inv = swing_l if dsgn > 0 else swing_h
                            if not _finite(inv):
                                inv = float(l) if dsgn > 0 else float(h)
                            meta = {"kind": "B", "reclaim": reclaim, "swing_break": swing_brk, "approached_25": False, "stack_seen": False}
                    if kind is None:
                        continue
                    if kind == "C" and not _finite(inv):
                        inv = float(l) if dsgn > 0 else float(h)
                    path = execution_path(rec, session_idx, pos, dsgn, inv)
                    if path.get("same_bar_outcome"):
                        same_bar_n += 1
                    if not path.get("entry_ok"):
                        dead_n += 1
                        continue
                    if not path.get("primary_complete"):
                        continue
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
                    row["invalidation"] = inv
                    if kind == "A":
                        sr_hit = _zone_touch(h, l, snap)
                        vwap_hit = bool(meta.get("vwap_near"))
                        row["location_cat"] = location_cat(ma25=True, vwap=vwap_hit, sr=sr_hit)
                        row["sr_touch"] = sr_hit
                    out[kind].append(row)
            clock.commit_day(symbol, rec, session_idx)
            if dbar:
                hist[symbol].append(dbar)
                atr_c = atr20(hist[symbol])
                nxt_d = nxt.get(date)
                for rx in step_swings(swing_st[symbol], dbar, atr=atr_c, next_session=nxt_d, symbol=symbol):
                    reactions[symbol].append(rx)
        if di % 20 == 0 or di == n_days:
            print(
                f"WALK {di}/{n_days} A={len(out['A'])} B={len(out['B'])} C={len(out['C'])}",
                flush=True,
            )
    return {
        "ok": True,
        "events": out,
        "same_bar_entry_n": same_bar_n,
        "same_bar_outcome_n": same_bar_n,
        "FUTURE_SETUP_SELECTION_N": int(future_sel_n),
        "dead_before_entry_n": dead_n,
        "n_days": n_days,
        "n_symbols_loaded": int(minutes["symbol"].nunique()),
        "displacement_used": False,
        "peer_used": False,
        "ma_period_tuned": False,
    }
