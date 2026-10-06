"""Frozen V2 machine walk + entry-time structure. Eligibility/trigger unchanged."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.cross_sectional_peer_propagation_discovery_v1.peers import sector_of_map
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.pb1_causal_path_failure_rca_v1.metrics import first_passage
from research.pb1_opening_range_causal_path_test_v1.path import structural_r
from research.pb1_opening_range_causal_path_test_v1.walk import _attach_path, _finite, _slim_rec
from research.pb1_opening_range_continuation_face_valid_v1.or15 import freeze_or15, session_idx_of, window_tv
from research.pb1_opening_range_continuation_face_valid_v2 import LAST_TRIGGER, NEAR_DAILY_MA_ATR
from research.pb1_opening_range_continuation_face_valid_v2.impulse import classify_opening, opening_path
from research.pb1_opening_range_continuation_face_valid_v2.inplay import (
    WindowTVHistory,
    abs_gap_atr,
    daily_bias,
    in_play_flag,
    in_play_reason,
    near_ma,
    tv_sequence,
    xs_rank_pct,
)
from research.pb1_opening_range_continuation_face_valid_v2.machine import new_side, step_side, vwap_location
from research.pb1_opening_range_continuation_face_valid_v2.walk import _d5, _load_panel, _mean, _next_open, _sma, _targets
from research.pb1_structure_and_symbol_context_rca_v1.baseline import commit_day as commit_base
from research.pb1_structure_and_symbol_context_rca_v1.baseline import new_baselines, snapshot as snap_base
from research.pb1_structure_and_symbol_context_rca_v1.behavior import day_behavior, describe, new_history
from research.pb1_structure_and_symbol_context_rca_v1.structure import snapshot_and_classify
from research.support_resistance_face_valid_first_interaction_rebuild_v1.swings import new_swing_state, step_swings


def _break_close(rec: dict[str, Any], pos: Any) -> Any:
    if pos is None:
        return None
    try:
        c = rec["c"][int(pos)]
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    return float(c) if _finite(c) else None


def emit_structured(bind: dict[str, Any]) -> dict[str, Any]:
    loaded = _load_panel(bind)
    if not loaded.get("ok"):
        return loaded
    minutes = loaded["minutes"]
    disc = loaded["disc"]
    symbols = loaded["symbols"]
    blocks = dict(bind.get("blocks") or {})
    date_to_block = dict(blocks.get("date_to_block") or {})
    next_of = {disc[i]: disc[i + 1] for i in range(len(disc) - 1)}
    sector_of = sector_of_map(bind)
    clock = ClockHistory()
    win_tv = WindowTVHistory()
    hist: dict[str, list] = defaultdict(list)
    swings: dict[str, dict[str, Any]] = defaultdict(new_swing_state)
    reactions: dict[str, list] = defaultdict(list)
    bases: dict[str, dict[str, Any]] = defaultdict(new_baselines)
    beh: dict[str, Any] = defaultdict(new_history)
    grouped = {d: g for d, g in minutes.groupby("date", sort=False)}
    events: list[dict[str, Any]] = []
    recs: dict[tuple[str, str], dict[str, Any]] = {}
    eligible: list[dict[str, Any]] = []
    market_days: list[dict[str, Any]] = []
    counts = defaultdict(int)
    same_bar_n = 0
    false_break_n = 0
    or_modified_n = 0
    print(f"WALK_DAYS {len(disc)} pb1_structure_rca", flush=True)
    for di, date in enumerate(disc):
        if di % 50 == 0:
            print(f"WALK {di}/{len(disc)} {date} events={len(events)}", flush=True)
        gdate = grouped.get(date)
        if gdate is None:
            continue
        packed: list[dict[str, Any]] = []
        tvs: dict[str, float] = {}
        by_sym = {str(s): sg for s, sg in gdate.groupby("symbol", sort=False)}
        for symbol in symbols:
            sg = by_sym.get(str(symbol))
            if sg is None or sg.empty:
                continue
            rec = prep_symbol(sg)
            rec["session_idx"] = session_idx_of(rec["t"])
            or15 = freeze_or15(rec["t"], rec["h"], rec["l"], rec["session_idx"])
            tv0915 = window_tv(rec["t"], rec["va"], rec["session_idx"], "09:00", "09:15")
            tvs[str(symbol)] = tv0915
            packed.append({"symbol": str(symbol), "rec": rec, "or15": or15, "tv0915": tv0915})
        ranks = xs_rank_pct(tvs)
        rets_0915: list[float] = []
        above_open = 0
        n_panel = 0
        sec_pos: dict[str, list[int]] = defaultdict(list)
        for item in packed:
            rec = item["rec"]
            session_idx = rec["session_idx"]
            if not session_idx:
                continue
            o0 = rec["o"][session_idx[0]]
            c15 = None
            for i in session_idx:
                if str(rec["t"][i]) == "09:15":
                    c15 = rec["c"][i]
                    break
            if _finite(o0) and _finite(c15) and float(o0) > 0:
                r = (float(c15) / float(o0) - 1.0) * 10000.0
                rets_0915.append(r)
                n_panel += 1
                if float(c15) > float(o0):
                    above_open += 1
                sec_pos[sector_of.get(str(item["symbol"]), "") or ""].append(int(r > 0))
        med = float(sorted(rets_0915)[len(rets_0915) // 2]) if rets_0915 else float("nan")
        mean = (sum(rets_0915) / len(rets_0915)) if rets_0915 else float("nan")
        disp = float((sum((x - mean) ** 2 for x in rets_0915) / len(rets_0915)) ** 0.5) if len(rets_0915) > 1 else float("nan")
        mkt_sign = 1 if _finite(med) and med > 0 else (-1 if _finite(med) and med < 0 else 0)
        sec_agree = []
        sec_breadth = {}
        for sec, xs in sec_pos.items():
            if not xs:
                continue
            p = sum(xs) / len(xs)
            sec_breadth[sec] = p
            if len(xs) < 2:
                continue
            sec_sign = 1 if p > 0.5 else (-1 if p < 0.5 else 0)
            sec_agree.append(int(sec_sign == mkt_sign and sec_sign != 0))
        mkt = {
            "date": date,
            "block": date_to_block.get(date),
            "n_panel": n_panel,
            "breadth_0915": float(above_open / n_panel) if n_panel else None,
            "median_dir_bps": med,
            "dispersion_bps": disp,
            "sector_agree": float(sum(sec_agree) / len(sec_agree)) if sec_agree else None,
            "sector_breadth": sec_breadth,
        }
        market_days.append(mkt)

        for item in packed:
            symbol = item["symbol"]
            rec = item["rec"]
            or15 = item["or15"]
            session_idx = rec["session_idx"]
            prior = hist[symbol]
            pdc = float(prior[-1]["close"]) if prior and _finite(prior[-1].get("close")) else float("nan")
            pdh = float(prior[-1]["high"]) if prior and _finite(prior[-1].get("high")) else float("nan")
            pdl = float(prior[-1]["low"]) if prior and _finite(prior[-1].get("low")) else float("nan")
            atr = atr20(prior) if prior else float("nan")
            d5h, d5l = _d5(prior)
            prior_closes = [float(d["close"]) for d in prior if _finite(d.get("close"))]
            open_px = float(rec["o"][session_idx[0]]) if session_idx and _finite(rec["o"][session_idx[0]]) else float("nan")
            gap_atr = abs_gap_atr(open_px, pdc, atr)
            gap_signed = float(open_px - pdc) if _finite(open_px) and _finite(pdc) else float("nan")
            tv_pctl = win_tv.pctl(symbol, item["tv0915"])
            xs = ranks.get(symbol)
            play = in_play_flag(gap_atr, tv_pctl, xs)
            play_why = in_play_reason(gap_atr, tv_pctl, xs)
            path = opening_path(rec["t"], rec["o"], rec["h"], rec["l"], rec["c"], session_idx)
            opened = classify_opening(path)
            impulse_pctl: list[float] = []
            for i in session_idx:
                t = str(rec["t"][i])
                if "09:00" <= t <= "09:14":
                    p = clock.tv_pctl(symbol, t, rec["va"][i])
                    if p is not None:
                        impulse_pctl.append(float(p))
            impulse_tv = _mean(impulse_pctl)
            counts["symbol_days"] += 1
            if play:
                counts["in_play_n"] += 1
            lookback_dates = [str(d["date"]) for d in prior]
            bsnap = snap_base(bases[symbol], clock=None, atr=atr)
            bdesc = describe(beh[symbol], atr_pctl=bsnap.get("atr20_own_pctl"), med_tv=bsnap.get("median_tv0915"))
            or_width = (
                float(or15["or_high"]) - float(or15["or_low"])
                if or15.get("ok") and _finite(or15.get("or_high")) and _finite(or15.get("or_low"))
                else float("nan")
            )

            def _commit() -> None:
                clock.commit_day(symbol, rec, session_idx)
                win_tv.commit(symbol, item["tv0915"])
                day = daily_from_minutes(rec, date)
                if day:
                    hist[symbol].append(day)
                    rx = step_swings(
                        swings[symbol],
                        day,
                        atr=float(atr) if _finite(atr) else float("nan"),
                        next_session=next_of.get(date),
                        symbol=symbol,
                    )
                    if rx:
                        reactions[symbol].extend(rx)
                commit_base(
                    bases[symbol],
                    rec=rec,
                    session_idx=session_idx,
                    or_width=or_width,
                    tv0915=item["tv0915"],
                    gap_atr=gap_atr,
                    atr=atr,
                )
                db = day_behavior(rec, session_idx, pdc=pdc, tv0915=item["tv0915"], atr=atr)
                if db:
                    beh[symbol].append(db)

            if not or15.get("ok"):
                _commit()
                continue
            or_high = float(or15["or_high"])
            or_low = float(or15["or_low"])
            frozen = (or_high, or_low)
            if opened.get("state") != "CLEAN_OPENING_IMPULSE" or opened.get("DIR") not in (1, -1) or not play:
                _commit()
                continue

            st = new_side(int(opened["DIR"]))
            first_trig = None
            day_event = None
            for k, i in enumerate(session_idx):
                t = str(rec["t"][i])
                if t < "09:15":
                    continue
                if t > LAST_TRIGGER:
                    break
                if (or_high, or_low) != frozen:
                    or_modified_n += 1
                    or_high, or_low = frozen
                prev_c = rec["c"][session_idx[k - 1]] if k > 0 else None
                ev = step_side(
                    st,
                    pos=i,
                    t=t,
                    open_px=rec["o"][i],
                    high=rec["h"][i],
                    low=rec["l"][i],
                    close=rec["c"][i],
                    prev_close=prev_c,
                    or_high=or_high,
                    or_low=or_low,
                    atr=atr,
                )
                if ev is None:
                    continue
                if ev.get("false_break"):
                    false_break_n += 1
                    continue
                nxt = _next_open(rec, session_idx, i)
                if nxt is None:
                    counts["no_next_open_n"] += 1
                    continue
                if nxt.get("same_bar_entry"):
                    same_bar_n += 1
                    continue
                entry = float(nxt["entry_px"])
                px = float(rec["c"][i])
                sma5 = _sma(prior_closes, px, 5)
                sma25 = _sma(prior_closes, px, 25)
                sma75 = _sma(prior_closes, px, 75)
                br = int(ev["break_pos"])
                rp = int(ev["retest_pos"])
                retest_tv = clock.tv_pctl(symbol, str(rec["t"][rp]), rec["va"][rp])
                trig_tv = clock.tv_pctl(symbol, t, rec["va"][i])
                levels = {
                    "PDH": pdh,
                    "PDL": pdl,
                    "PDC": pdc,
                    "D5H": d5h,
                    "D5L": d5l,
                    "SMA25": sma25,
                    "SMA75": sma75,
                    "VWAP": rec["vw"][i],
                }
                tgt = _targets(int(ev["DIR"]), entry, levels, float(ev["invalidation"]))
                held = bool(ev.get("held"))
                b_at = snap_base(bases[symbol], clock=t, atr=atr)
                risk = structural_r(sign=int(ev["DIR"]), entry=entry, retest_high=ev.get("retest_high"), retest_low=ev.get("retest_low"))
                struct = snapshot_and_classify(
                    symbol=symbol,
                    reactions=list(reactions[symbol]),
                    atr=float(atr) if _finite(atr) else float("nan"),
                    session_date=str(date),
                    lookback_dates=lookback_dates,
                    hist=list(prior),
                    open_px=open_px,
                    entry=entry,
                    sign=int(ev["DIR"]),
                    or_high=or_high,
                    or_low=or_low,
                    break_close=_break_close(rec, br),
                    retest_high=ev.get("retest_high"),
                    retest_low=ev.get("retest_low"),
                    trigger_close=rec["c"][i],
                    held=held,
                    pdh=pdh,
                    pdl=pdl,
                    pdc=pdc,
                    d5h=d5h,
                    d5l=d5l,
                    sma25=sma25,
                    sma75=sma75,
                    vwap=rec["vw"][i],
                    r_px=risk.get("R"),
                    med_1m=b_at.get("median_1m_range_tod"),
                    med_or15=b_at.get("median_or15"),
                    or_width=or_width,
                )
                row = {
                    **ev,
                    **nxt,
                    **path,
                    "symbol": symbol,
                    "date": date,
                    "block": date_to_block.get(date),
                    "sector": sector_of.get(symbol, "") or "",
                    "in_play": play,
                    "in_play_reason": play_why,
                    "open_state": opened.get("state"),
                    "open_reason": opened.get("reason"),
                    "abs_gap_atr": gap_atr,
                    "gap_signed": gap_signed,
                    "tv_0915": item["tv0915"],
                    "tv_0915_pctl": tv_pctl,
                    "xs_rank_pct": xs,
                    "pdh": pdh,
                    "pdl": pdl,
                    "pdc": pdc,
                    "atr20": atr,
                    "d5h": d5h,
                    "d5l": d5l,
                    "sma5": sma5,
                    "sma25": sma25,
                    "sma75": sma75,
                    "daily_bias": daily_bias(sma5, sma25, sma75),
                    "near_sma25": near_ma(px, sma25, atr, NEAR_DAILY_MA_ATR),
                    "near_sma75": near_ma(px, sma75, atr, NEAR_DAILY_MA_ATR),
                    "vwap_break": vwap_location(rec["h"][br], rec["l"][br], rec["c"][br], rec["vw"][br], int(ev["DIR"])),
                    "vwap_retest": vwap_location(rec["h"][rp], rec["l"][rp], rec["c"][rp], rec["vw"][rp], int(ev["DIR"])),
                    "vwap_trigger": vwap_location(rec["h"][i], rec["l"][i], rec["c"][i], rec["vw"][i], int(ev["DIR"])),
                    "impulse_tv_pctl": impulse_tv,
                    "retest_tv_pctl": retest_tv,
                    "trigger_tv_pctl": trig_tv,
                    "tv_sequence": tv_sequence(impulse_tv, retest_tv, trig_tv),
                    "or_known_from": "09:15",
                    "market_breadth_0915": mkt.get("breadth_0915"),
                    "market_dispersion_bps": mkt.get("dispersion_bps"),
                    "market_sector_agree": mkt.get("sector_agree"),
                    "sector_breadth_0915": (mkt.get("sector_breadth") or {}).get(sector_of.get(symbol, "") or ""),
                    "archetype": bdesc.get("archetype"),
                    "persist_rate": bdesc.get("persist_rate"),
                    "reversal_rate": bdesc.get("reversal_rate"),
                    "gap_cont_rate": bdesc.get("gap_cont_rate"),
                    "intraday_autocorr": bdesc.get("autocorr"),
                    "atr20_own_pctl": bsnap.get("atr20_own_pctl"),
                    **b_at,
                    **tgt,
                    **{f"st_{k}": v for k, v in struct.items() if k != "chart_zones"},
                    "chart_zones": struct.get("chart_zones") or [],
                    "structural_class": struct.get("structural_class"),
                    "flip_class": struct.get("flip_class"),
                    "room_class": struct.get("room_class"),
                }
                events.append(row)
                day_event = row
                if first_trig is None:
                    first_trig = t
                counts["setup_n"] += 1
                counts[f"dir_{row['direction']}"] += 1
                counts[f"trig_{row['trigger_primary']}"] += 1
                counts[f"class_{row['structural_class']}"] += 1

            slim = _slim_rec(rec, session_idx)
            recs[(symbol, date)] = slim
            px_0915 = None
            pos_0915 = None
            for i in session_idx:
                if str(rec["t"][i]) == "09:15":
                    pos_0915 = i
                    px_0915 = rec["c"][i]
                    break
            if pos_0915 is None and session_idx:
                pos_0915 = session_idx[min(15, len(session_idx) - 1)]
                px_0915 = rec["c"][pos_0915]
            sma5 = _sma(prior_closes, float(px_0915) if _finite(px_0915) else float("nan"), 5)
            sma25 = _sma(prior_closes, float(px_0915) if _finite(px_0915) else float("nan"), 25)
            sma75 = _sma(prior_closes, float(px_0915) if _finite(px_0915) else float("nan"), 75)
            eligible.append(
                {
                    "symbol": symbol,
                    "date": date,
                    "block": date_to_block.get(date),
                    "DIR": int(opened["DIR"]),
                    "direction": "bull" if int(opened["DIR"]) > 0 else "bear",
                    "in_play": True,
                    "open_state": "CLEAN_OPENING_IMPULSE",
                    "or_high": or_high,
                    "or_low": or_low,
                    "pdh": pdh,
                    "pdl": pdl,
                    "pdc": pdc,
                    "d5h": d5h,
                    "d5l": d5l,
                    "atr20": atr,
                    "sma25": sma25,
                    "sma75": sma75,
                    "daily_bias": daily_bias(sma5, sma25, sma75),
                    "prior_closes": list(prior_closes[-80:]),
                    "trigger_t": first_trig,
                    "treated": bool(day_event is not None),
                    "structural_class": (day_event or {}).get("structural_class"),
                }
            )
            counts["eligible_n"] += 1
            _commit()

    treated: list[dict[str, Any]] = []
    for ev in events:
        rec = recs.get((str(ev["symbol"]), str(ev["date"])))
        if rec is None:
            counts["missing_rec_n"] += 1
            continue
        attached = _attach_path(ev, rec)
        rng = attached.get("or_range")
        net = attached.get("net_or15")
        attached["impulse_mag"] = (
            abs(float(net)) / float(rng) if _finite(net) and _finite(rng) and float(rng) > 0 else float("nan")
        )
        risk = structural_r(
            sign=int(attached["DIR"]),
            entry=float(attached["entry_px"]),
            retest_high=attached.get("retest_high"),
            retest_low=attached.get("retest_low"),
        )
        fp = first_passage(
            rec,
            entry_pos=int(attached["entry_pos"]),
            sign=int(attached["DIR"]),
            entry=float(attached["entry_px"]),
            r_px=risk.get("R"),
            or_high=float(attached["or_high"]),
            or_low=float(attached["or_low"]),
            retest_high=attached.get("retest_high"),
            retest_low=attached.get("retest_low"),
        )
        attached["fp_risk_defined"] = fp.get("risk_defined")
        for k, v in fp.items():
            attached[f"fp_{k}"] = v
        treated.append(attached)
    return {
        "ok": True,
        "events": treated,
        "eligible": eligible,
        "recs": recs,
        "market_days": market_days,
        "counts": dict(counts),
        "same_bar_entry_n": same_bar_n,
        "false_break_n": false_break_n,
        "or_modified_after_freeze_n": or_modified_n,
        "future_outcome_n": 0,
        "n_days": len(disc),
        "n_symbols": len(symbols),
        "eligibility_changed": False,
        "failed_push_purged": False,
        "reclaim_selected": False,
    }
