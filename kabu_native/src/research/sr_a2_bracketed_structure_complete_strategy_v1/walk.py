"""Walk ALL causal A2 signals. Eligibility at decision-bar close. Retest-hold path stays closed. No matchability filter."""
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
from research.support_resistance_matched_separation_not_a_strategy_v1.executable import a2_confirmed
from research.support_resistance_matched_separation_not_a_strategy_v1.outcomes import next_entry_i, signed_from_entry
from research.support_resistance_mechanism_to_complete_strategy_v1.exits import attach_exit
from research.sr_a2_bracketed_structure_complete_strategy_v1.eligibility import audit_vs_entry_open, opposing_at_decision
from research.sr_a2_bracketed_structure_complete_strategy_v1 import SHARES


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


def _sign_num(x: Any) -> float:
    if not _finite(x):
        return 0.0
    if float(x) > 0:
        return 1.0
    if float(x) < 0:
        return -1.0
    return 0.0


def _zone_age(activated: Any, date: str, disc: list[str]) -> float:
    act = str(activated or "")
    if not act:
        return float("nan")
    n = 0
    for d in disc:
        if act <= d < date:
            n += 1
    return float(n)


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


def walk_bracketed(bind: dict[str, Any]) -> dict[str, Any]:
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
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)} sr_a2_bracketed", flush=True)
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
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    same_bar_n = 0
    leak_n = 0
    uses_entry_open_n = 0
    uses_future_bar_n = 0
    disagree_n = 0
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
        mkt, sec_all = _med_map({s: p["rec"] for s, p in per.items()}, sector_of={s: p["sector"] for s, p in per.items()})
        for symbol, pack in per.items():
            rec = pack["rec"]
            snap = pack["snap"]
            chosen = selected_list(pack["sel"])
            eps = []
            for z in chosen:
                ep = new_episode(z, date=date, symbol=symbol)
                ep["touch_count"] = z.get("touch_count")
                ep["zone_activated_at"] = z.get("ZONE_ACTIVATED_AT")
                ep["center"] = z.get("center")
                eps.append(ep)
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
                slot = str(ep.get("selection_slot") or "")
                if not ep.get("first_test"):
                    continue
                if is_secondary(slot):
                    counts["secondary_first_test_n"] += 1
                    counts["first_test_n"] += 1
                    continue
                if not is_primary(slot):
                    continue
                counts["primary_first_test_n"] += 1
                counts["first_test_n"] += 1
                if ep.get("rejection"):
                    counts["PRIMARY_reject_n"] += 1
                a2 = a2_confirmed(ep, rec)
                if not a2.get("confirmed") or not a2.get("decision_t"):
                    continue
                counts["a2_confirmed_n"] += 1
                decision_t = str(a2.get("decision_t"))
                di_i = rec["idx"].get(decision_t)
                if di_i is None:
                    continue
                j = next_entry_i(rec, int(di_i))
                if j is None:
                    counts["a2_no_executable_entry_n"] += 1
                    continue
                counts["a2_signal_n"] += 1
                res = bool(ep.get("as_resistance"))
                side = "SHORT" if res else "LONG"
                zid = str(ep.get("zone_id") or "")
                decision_px = float(rec["c"][int(di_i)])
                if not _finite(decision_px) or decision_px <= 0:
                    counts["a2_no_decision_px_n"] += 1
                    continue
                elig = opposing_at_decision(
                    side=side,
                    decision_px=decision_px,
                    entry_zone_id=zid,
                    session_date=date,
                    resistance_active=list(snap.get("resistance_active") or []),
                    support_active=list(snap.get("support_active") or []),
                )
                if elig.get("used_entry_open"):
                    uses_entry_open_n += 1
                if elig.get("used_future_bar"):
                    uses_future_bar_n += 1
                entry_px = float(rec["o"][int(j)])
                open_elig = opposing_at_decision(
                    side=side,
                    decision_px=entry_px,
                    entry_zone_id=zid,
                    session_date=date,
                    resistance_active=list(snap.get("resistance_active") or []),
                    support_active=list(snap.get("support_active") or []),
                )
                aud = audit_vs_entry_open(elig, open_elig)
                if aud.get("disagrees_with_entry_open"):
                    disagree_n += 1
                tm = to_min(decision_t)
                sess = rec["sess"][int(di_i)]
                mktv = mkt.get(decision_t)
                secv = (sec_all.get(pack["sector"]) or {}).get(decision_t)
                gap = None
                if _finite(pack.get("open")) and _finite(pack.get("pdc")) and float(pack["pdc"]) != 0:
                    gap = (float(pack["open"]) - float(pack["pdc"])) / float(pack["pdc"])
                sign = sign_for("A", resistance=res)
                path = signed_from_entry(rec, int(j), sign)
                same = bool(rec["t"][int(j)] == decision_t)
                if same:
                    same_bar_n += 1
                if elig.get("target_future_leakage"):
                    counts["target_leak_attempts_n"] += 1
                available = bool(elig.get("opposing_zone_available")) and not bool(elig.get("target_future_leakage"))
                row = {
                    "signal_id": f"A2|{symbol}|{date}|{zid}|{decision_t}",
                    "episode_id": f"{symbol}|{date}|{zid}",
                    "mechanism": "A2",
                    "symbol": symbol,
                    "date": date,
                    "block": block,
                    "side": side,
                    "zone_id": zid,
                    "zone_role": "RESISTANCE" if res else "SUPPORT",
                    "entry_zone_id": zid,
                    "entry_zone_lo": float(ep["lo"]),
                    "entry_zone_hi": float(ep["hi"]),
                    "entry_zone_bounds": [float(ep["lo"]), float(ep["hi"])],
                    "inv_lo": float(ep["lo"]),
                    "inv_hi": float(ep["hi"]),
                    "entry_thesis": "resistance_held" if res else "support_held",
                    "decision_bar": decision_t,
                    "decision_i": int(di_i),
                    "decision_t": decision_t,
                    "decision_available_at": rec["t"][int(j)],
                    "decision_px": decision_px,
                    "eligibility_price_kind": "decision_bar_close",
                    "entry_i": int(j),
                    "entry_t": rec["t"][int(j)],
                    "entry_price": entry_px,
                    "same_bar_entry": same,
                    "used_entry_open_for_eligibility": False,
                    "used_future_bar_for_eligibility": False,
                    "disagrees_with_entry_open_eligibility": bool(aud.get("disagrees_with_entry_open")),
                    "opposing_zone_available": available,
                    "skip_reason": None if available else "NO_OPPOSING_ZONE_SKIP",
                    "notional": float(entry_px) * float(SHARES),
                    "tod_min": float(tm - 9 * 60) if tm is not None else float("nan"),
                    "r1": rec["r1"][int(di_i)],
                    "r3": rec["r3"][int(di_i)],
                    "r5": rec["r5"][int(di_i)],
                    "vol_rel": _vol_rel(rec, int(di_i)),
                    "rng_rel": _rng_rel(rec, int(di_i)),
                    "gap": gap,
                    "gap_num": _sign_num(gap),
                    "mkt_num": _sign_num((float(sess) - float(mktv)) if _finite(sess) and _finite(mktv) else None),
                    "sec_num": _sign_num((float(sess) - float(secv)) if _finite(sess) and _finite(secv) else None),
                    "zone_age": _zone_age(ep.get("zone_activated_at"), date, disc),
                    "touch_count": float(ep.get("touch_count") or 0),
                    "n_active_sr": elig.get("n_active_sr"),
                    "as_resistance": 1.0 if res else 0.0,
                    "p20_before_m20": path.get("p20_before_m20"),
                    "p40_before_m20": path.get("p40_before_m20"),
                    "p80_before_m30": path.get("p80_before_m30"),
                    "path_mfe_bps": path.get("mfe_bps"),
                    "path_mae_bps": path.get("mae_bps"),
                    "ret_5m_bps": path.get("ret_5m_bps"),
                    "ret_10m_bps": path.get("ret_10m_bps"),
                    "ret_20m_bps": path.get("ret_20m_bps"),
                    "time_to_mfe": path.get("time_to_extension_min"),
                    "path_giveback": (
                        float(path["mfe_bps"]) - float(path["end_bps"])
                        if _finite(path.get("mfe_bps")) and _finite(path.get("end_bps")) and float(path["mfe_bps"]) > 0
                        else None
                    ),
                    **{k: elig.get(k) for k in (
                        "target_zone_id",
                        "target_price",
                        "target_lo",
                        "target_hi",
                        "target_known_at_entry",
                        "target_activated_at",
                        "target_role",
                        "target_distance_bps",
                        "target_future_leakage",
                    )},
                }
                if available:
                    counts["eligible_signal_n"] += 1
                    filled = attach_exit(row, rec)
                    if filled.get("target_future_leakage"):
                        leak_n += 1
                    rows.append(filled)
                else:
                    counts["no_opposing_zone_skip_n"] += 1
                    row["ok"] = False
                    rows.append(row)
            dbar = pack.get("dbar")
            if dbar:
                hist[symbol].append(dbar)
                atr_c = atr20(hist[symbol])
                nxt_d = nxt.get(date)
                for rx in step_swings(swing_st[symbol], dbar, atr=atr_c, next_session=nxt_d, symbol=symbol):
                    reactions[symbol].append(rx)
        if di % 20 == 0 or di == n_days:
            print(
                f"WALK {di}/{n_days} a2={counts.get('a2_signal_n', 0)} elig={counts.get('eligible_signal_n', 0)} skip={counts.get('no_opposing_zone_skip_n', 0)}",
                flush=True,
            )

    return {
        "ok": True,
        "rows": rows,
        "counts": dict(counts),
        "same_bar_entry_n": int(same_bar_n),
        "target_future_leakage_n": int(leak_n),
        "TARGET_ELIGIBILITY_USES_ENTRY_OPEN_N": int(uses_entry_open_n),
        "TARGET_ELIGIBILITY_FUTURE_BAR_N": int(uses_future_bar_n),
        "eligibility_disagrees_with_entry_open_n": int(disagree_n),
        "n_days": n_days,
        "n_symbols_loaded": int(minutes["symbol"].nunique()),
        "forbidden_loaded": False,
        "c1_reopened": False,
        "matchability_filter_applied": False,
    }
