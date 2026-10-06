"""Walk causal native PRICE DISPLACEMENT events with a compact context stack. No TV gate. No clustering."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.walk import next_dates_map
from research.native_causal_context_stack_discovery_v1 import FEATURE_NAMES, REFRACTORY_BARS, SESSION_FLAT
from research.native_causal_context_stack_discovery_v1.features import local_structure, sr_context, vwap_context
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory, displacement
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol, to_min
from research.support_resistance_face_valid_first_interaction_rebuild_v1.machine import close_episode, new_episode, step_episode
from research.support_resistance_face_valid_first_interaction_rebuild_v1.select import select_salient, selected_list
from research.support_resistance_face_valid_first_interaction_rebuild_v1.swings import new_swing_state, step_swings
from research.support_resistance_face_valid_first_interaction_rebuild_v1.zones import snapshot_zones
from research.support_resistance_matched_separation_not_a_strategy_v1.outcomes import next_entry_i, signed_from_entry


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
    if pos < n:
        return float("nan")
    xs = [float(rec["rng"][j]) for j in session_idx[pos - n : pos] if _finite(rec["rng"][j])]
    if len(xs) < 5:
        return float("nan")
    return float(np.mean(xs))


def _med_map(per: dict[str, dict[str, Any]]) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    by_t: dict[str, list[float]] = defaultdict(list)
    by_s: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for p in per.values():
        rec, sec = p["rec"], p["sector"]
        for i, t in enumerate(rec["t"]):
            s = rec["sess"][i]
            if _finite(s):
                by_t[str(t)].append(float(s))
                if sec:
                    by_s[sec][str(t)].append(float(s))
    mkt = {t: float(np.median(xs)) for t, xs in by_t.items() if xs}
    sec = {s: {t: float(np.median(xs)) for t, xs in d.items() if xs} for s, d in by_s.items()}
    return mkt, sec


def walk_events(bind: dict[str, Any]) -> dict[str, Any]:
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
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)} native_context_stack", flush=True)
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
    events: list[dict[str, Any]] = []
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
        mkt, sec_all = _med_map(per)
        for symbol, pack in per.items():
            rec = pack["rec"]
            snap = pack["snap"]
            chosen = selected_list(pack["sel"])
            eps = []
            for z in chosen:
                ep = new_episode(z, date=date, symbol=symbol)
                eps.append(ep)
            gap = _sign((float(pack["open"]) - float(pack["pdc"])) if _finite(pack.get("open")) and _finite(pack.get("pdc")) else None)
            sec_med = sec_all.get(pack["sector"], {})
            n = int(rec["n"])
            session_idx = [i for i in range(n) if not in_lunch(rec["t"][i])]
            pos_of = {i: p for p, i in enumerate(session_idx)}
            last_fire = {"BULLISH": -10**9, "BEARISH": -10**9}
            for i in session_idx:
                t = rec["t"][i]
                o, h, l, c = float(rec["o"][i]), float(rec["h"][i]), float(rec["l"][i]), float(rec["c"][i])
                for ep in eps:
                    step_episode(ep, t=t, o=o, h=h, l=l, c=c)
                if str(t) >= SESSION_FLAT:
                    continue
                pos = pos_of[i]
                disp = displacement(rec, i, session_idx, pos=pos)
                if not disp:
                    continue
                counts["displacement_raw_n"] += 1
                if (pos - last_fire[str(disp)]) <= int(REFRACTORY_BARS):
                    counts["refractory_suppressed_n"] += 1
                    continue
                last_fire[str(disp)] = pos
                counts["native_event_n"] += 1
                sign = 1 if disp == "BULLISH" else -1
                j = next_entry_i(rec, i)
                if j is not None and rec["t"][j] == rec["t"][i]:
                    same_bar_n += 1
                    continue
                path = signed_from_entry(rec, j, sign)
                y40 = path.get("p40_before_m20")
                if y40 is None:
                    counts["no_outcome_n"] += 1
                    continue
                tv_p = clock.tv_pctl(symbol, t, float(rec["va"][i]) if _finite(rec["va"][i]) else float("nan"))
                i_prev = session_idx[pos - 1] if pos > 0 else None
                t_prev = rec["t"][i_prev] if i_prev is not None else t
                sess_p = rec["sess"][i_prev] if i_prev is not None else rec["sess"][i]
                mktv = mkt.get(str(t_prev))
                secv = sec_med.get(str(t_prev))
                sr = sr_context(rec=rec, i=i, atr=float(pack["atr"]) if _finite(pack.get("atr")) else float("nan"), snap=snap, eps=eps, t=t)
                loc = local_structure(rec, session_idx, pos, str(disp))
                vw = vwap_context(rec, i)
                row = {
                    "symbol": symbol,
                    "date": date,
                    "block": block,
                    "sector": pack["sector"],
                    "i": i,
                    "t": t,
                    "direction": str(disp),
                    "bucket": _bucket(t),
                    "tod_min": float((to_min(t) or 0) - 9 * 60),
                    "gap": gap,
                    "rng_rel": _prior_rv(rec, session_idx, pos),
                    "tv_clock_pctl": float(tv_p) if tv_p is not None else float("nan"),
                    "r5": float(rec["r5"][i]) if _finite(rec["r5"][i]) else float("nan"),
                    "r15": float(rec["r15"][i]) if _finite(rec["r15"][i]) else float("nan"),
                    "mkt_rel": (float(sess_p) - float(mktv)) if _finite(sess_p) and _finite(mktv) else float("nan"),
                    "sec_rel": (float(sess_p) - float(secv)) if _finite(sess_p) and _finite(secv) else float("nan"),
                    "y_p40": 1 if y40 else 0,
                    "y_p20": 1 if path.get("p20_before_m20") else (0 if path.get("p20_before_m20") is not None else None),
                    "y_p80": 1 if path.get("p80_before_m30") else (0 if path.get("p80_before_m30") is not None else None),
                    "mfe": path.get("mfe_bps"),
                    "mae": path.get("mae_bps"),
                    "ret5": path.get("ret_5m_bps"),
                    "ret10": path.get("ret_10m_bps"),
                    "ret20": path.get("ret_20m_bps"),
                    **sr,
                    **loc,
                    **vw,
                }
                events.append(row)
                counts[f"{disp}_n"] += 1
                counts[f"{block}_n"] += 1
            for ep in eps:
                close_episode(ep)
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
                f"WALK {di}/{n_days} events={counts.get('native_event_n', 0)} kept={len(events)} D1={counts.get('D1_n', 0)}",
                flush=True,
            )

    return {
        "ok": True,
        "events": events,
        "counts": dict(counts),
        "same_bar_entry_n": int(same_bar_n),
        "n_days": n_days,
        "n_symbols_loaded": int(minutes["symbol"].nunique()),
        "forbidden_loaded": False,
        "future_event_selection_n": 0,
        "retrospective_cluster_n": 0,
        "feature_names": list(FEATURE_NAMES),
        "tv_not_required": True,
    }
