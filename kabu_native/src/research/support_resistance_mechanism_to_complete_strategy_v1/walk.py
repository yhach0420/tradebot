"""Walk all causally valid A2/C1 signals. No matchability filter. No Confirmation."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.walk import next_dates_map
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.support_resistance_face_valid_first_interaction_rebuild_v1.machine import close_episode, new_episode, step_episode
from research.support_resistance_face_valid_first_interaction_rebuild_v1.select import select_salient, selected_list
from research.support_resistance_face_valid_first_interaction_rebuild_v1.swings import new_swing_state, step_swings
from research.support_resistance_face_valid_first_interaction_rebuild_v1.zones import snapshot_zones
from research.support_resistance_first_interaction_matched_causal_test_v1.direction import is_primary, is_secondary
from research.support_resistance_matched_separation_not_a_strategy_v1.executable import a1_passive, a2_confirmed, c1_hold
from research.support_resistance_mechanism_to_complete_strategy_v1.exits import a1_fill_path, attach_exit
from research.support_resistance_mechanism_to_complete_strategy_v1.targets import structural_target
from research.support_resistance_matched_separation_not_a_strategy_v1.outcomes import next_entry_i


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _compact_zones(xs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for z in xs:
        out.append(
            {
                "zone_id": z.get("zone_id"),
                "lo": float(z["lo"]),
                "hi": float(z["hi"]),
                "center": float(z["center"]),
                "ZONE_ACTIVATED_AT": z.get("ZONE_ACTIVATED_AT"),
                "role": z.get("role"),
            }
        )
    return out


def _side_a2(resistance: bool) -> str:
    return "SHORT" if resistance else "LONG"


def _side_c1(resistance: bool) -> str:
    return "LONG" if resistance else "SHORT"


def _zone_role(resistance: bool) -> str:
    return "RESISTANCE" if resistance else "SUPPORT"


def _base_signal(
    *,
    mechanism: str,
    ep: dict[str, Any],
    rec: dict[str, Any],
    decision_t: str,
    entry_i: int,
    side: str,
    snap: dict[str, Any],
    block: str,
) -> dict[str, Any]:
    di = rec["idx"].get(str(decision_t))
    zid = str(ep.get("zone_id") or "")
    px = float(rec["o"][int(entry_i)])
    tgt = structural_target(
        side=side,
        entry_px=px,
        entry_zone_id=zid,
        session_date=str(ep.get("date") or ""),
        resistance_active=list(snap.get("resistance_active") or []),
        support_active=list(snap.get("support_active") or []),
    )
    leak = False
    act = tgt.get("target_activated_at")
    if act and str(act) > str(ep.get("date") or ""):
        leak = True
    sig = {
        "signal_id": f"{mechanism}|{ep.get('symbol')}|{ep.get('date')}|{zid}|{decision_t}",
        "episode_id": f"{ep.get('symbol')}|{ep.get('date')}|{zid}",
        "mechanism": mechanism,
        "symbol": ep.get("symbol"),
        "date": ep.get("date"),
        "block": block,
        "side": side,
        "zone_id": zid,
        "zone_role": _zone_role(bool(ep.get("as_resistance"))),
        "entry_zone_id": zid,
        "entry_zone_lo": float(ep["lo"]),
        "entry_zone_hi": float(ep["hi"]),
        "entry_zone_bounds": [float(ep["lo"]), float(ep["hi"])],
        "inv_lo": float(ep["lo"]),
        "inv_hi": float(ep["hi"]),
        "entry_thesis": (
            "support_held"
            if mechanism == "A2" and side == "LONG"
            else "resistance_held"
            if mechanism == "A2"
            else "old_resistance_became_support"
            if side == "LONG"
            else "old_support_became_resistance"
        ),
        "decision_bar": decision_t,
        "decision_i": int(di) if di is not None else None,
        "decision_t": decision_t,
        "entry_i": int(entry_i),
        "selection_slot": ep.get("selection_slot"),
        "target_future_leakage": bool(leak),
        **tgt,
    }
    return sig


def walk_complete(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = [str(d) for d in list(split.get("discovery_dates") or [])]
    disc_set = set(disc)
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    date_to_block = dict(blocks.get("date_to_block") or {})
    nxt = next_dates_map(disc)
    symbols = list(bind.get("symbols") or [])
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)} sr_complete_strategy", flush=True)
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
    a2_signals: list[dict[str, Any]] = []
    c1_signals: list[dict[str, Any]] = []
    a1_rows: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    same_bar_n = 0
    leak_n = 0
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
            per[symbol] = {"rec": rec, "atr": atr, "snap": snap, "sel": sel, "dbar": dbar, "open": opn}

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
                ep["block"] = block
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
                res = bool(ep.get("as_resistance"))
                a1 = a1_passive(ep, rec)
                counts["a1_prepositioned_order_n"] += 1
                if a1.get("fillable_approx"):
                    counts["a1_minute_fillable_n"] += 1
                    ft = ep.get("first_test_time")
                    fi = rec["idx"].get(str(ft)) if ft else None
                    if fi is not None and _finite(a1.get("fill_price")):
                        side = _side_a2(res)
                        tgt = structural_target(
                            side=side,
                            entry_px=float(a1["fill_price"]),
                            entry_zone_id=str(ep.get("zone_id") or ""),
                            session_date=date,
                            resistance_active=list(snap.get("resistance_active") or []),
                            support_active=list(snap.get("support_active") or []),
                        )
                        path = a1_fill_path(
                            rec,
                            fill_i=int(fi),
                            fill_price=float(a1["fill_price"]),
                            side=side,
                            inv_lo=float(ep["lo"]),
                            inv_hi=float(ep["hi"]),
                            target_price=tgt.get("target_price"),
                        )
                        nxt_i = next_entry_i(rec, int(fi))
                        nxt_o = float(rec["o"][nxt_i]) if nxt_i is not None else None
                        adv = None
                        if nxt_o is not None:
                            if side == "LONG":
                                adv = (nxt_o - float(a1["fill_price"])) / float(a1["fill_price"]) * 10_000.0
                            else:
                                adv = (float(a1["fill_price"]) - nxt_o) / float(a1["fill_price"]) * 10_000.0
                        a1_rows.append(
                            {
                                "symbol": symbol,
                                "date": date,
                                "block": block,
                                "side": side,
                                "zone_id": ep.get("zone_id"),
                                "fill_bar": ft,
                                "fill_price": a1.get("fill_price"),
                                "limit_price": a1.get("limit_price"),
                                "gap_through": a1.get("gap_through"),
                                "next_open": nxt_o,
                                "adverse_vs_next_open_bps": adv,
                                "classification": "PASSIVE_FILL_NOT_ACTUAL_PROVEN",
                                "actual_fill_proven": False,
                                **{k: path.get(k) for k in (
                                    "ok",
                                    "exit_reason",
                                    "exit_price",
                                    "gross_yen",
                                    "stress_yen",
                                    "mfe_bps",
                                    "mae_bps",
                                    "hold_min",
                                    "target_filled",
                                )},
                                **{k: tgt.get(k) for k in ("target_price", "target_zone_id")},
                            }
                        )
                a2 = a2_confirmed(ep, rec)
                if a2.get("confirmed") and a2.get("entry_t") is not None:
                    j = rec["idx"].get(str(a2.get("entry_t")))
                    if j is not None:
                        sig = _base_signal(
                            mechanism="A2",
                            ep=ep,
                            rec=rec,
                            decision_t=str(a2.get("decision_t")),
                            entry_i=int(j),
                            side=_side_a2(res),
                            snap=snap,
                            block=block,
                        )
                        sig = attach_exit(sig, rec)
                        if sig.get("same_bar_entry"):
                            same_bar_n += 1
                        if sig.get("target_future_leakage"):
                            leak_n += 1
                        a2_signals.append(sig)
                        counts["a2_signal_n"] += 1
                if ep.get("break"):
                    counts["PRIMARY_break_n"] += 1
                if ep.get("retest_hold"):
                    counts["PRIMARY_retest_hold_n"] += 1
                    c1 = c1_hold(ep, rec)
                    if c1.get("confirmed") and c1.get("entry_t") is not None:
                        j = rec["idx"].get(str(c1.get("entry_t")))
                        if j is not None:
                            sig = _base_signal(
                                mechanism="C1",
                                ep=ep,
                                rec=rec,
                                decision_t=str(c1.get("decision_t")),
                                entry_i=int(j),
                                side=_side_c1(res),
                                snap=snap,
                                block=block,
                            )
                            sig = attach_exit(sig, rec)
                            if sig.get("same_bar_entry"):
                                same_bar_n += 1
                            if sig.get("target_future_leakage"):
                                leak_n += 1
                            c1_signals.append(sig)
                            counts["c1_signal_n"] += 1
                if ep.get("rejection"):
                    counts["PRIMARY_reject_n"] += 1
                if ep.get("failed_retest"):
                    counts["PRIMARY_failed_retest_n"] += 1
            dbar = pack.get("dbar")
            if dbar:
                hist[symbol].append(dbar)
                atr_c = atr20(hist[symbol])
                nxt_d = nxt.get(date)
                for rx in step_swings(swing_st[symbol], dbar, atr=atr_c, next_session=nxt_d, symbol=symbol):
                    reactions[symbol].append(rx)
        if di % 20 == 0 or di == n_days:
            print(
                f"WALK {di}/{n_days} a2={len(a2_signals)} c1={len(c1_signals)} primary_ft={counts.get('primary_first_test_n', 0)}",
                flush=True,
            )

    pre = int(counts.get("a1_prepositioned_order_n") or 0)
    fillable = int(counts.get("a1_minute_fillable_n") or 0)
    a1_yen = [float(r["gross_yen"]) for r in a1_rows if r.get("ok") and _finite(r.get("gross_yen"))]
    a1_adv = [float(r["adverse_vs_next_open_bps"]) for r in a1_rows if _finite(r.get("adverse_vs_next_open_bps"))]
    return {
        "ok": True,
        "a2_signals": a2_signals,
        "c1_signals": c1_signals,
        "a1_rows": a1_rows,
        "counts": dict(counts),
        "same_bar_entry_n": int(same_bar_n),
        "target_future_leakage_n": int(leak_n),
        "n_days": n_days,
        "n_symbols_loaded": int(minutes["symbol"].nunique()),
        "forbidden_loaded": False,
        "matchability_filter_applied": False,
        "a1_diagnostic": {
            "classification": "PASSIVE_FILL_NOT_ACTUAL_PROVEN",
            "actual_fill_proven": False,
            "merged_with_a2": False,
            "prepositioned_order_n": pre,
            "minute_fillable_n": fillable,
            "fillable_rate": (fillable / pre) if pre else None,
            "adverse_vs_next_open_mean_bps": (sum(a1_adv) / len(a1_adv)) if a1_adv else None,
            "structural_exit_gross_yen": float(sum(a1_yen)) if a1_yen else 0.0,
            "structural_exit_n": len(a1_yen),
            "label": "PASSIVE_FILL_NOT_ACTUAL_PROVEN",
        },
    }
