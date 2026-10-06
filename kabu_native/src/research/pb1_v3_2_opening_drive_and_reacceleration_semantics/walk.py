"""V3.2 Discovery walk. No PnL. Frozen V3.1/V3/V2 unread-write."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.pb1_opening_range_continuation_face_valid_v1.or15 import freeze_or15, session_idx_of, window_tv
from research.pb1_opening_range_continuation_face_valid_v2 import LAST_TRIGGER, NEAR_DAILY_MA_ATR
from research.pb1_opening_range_continuation_face_valid_v2.impulse import opening_path
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
from research.pb1_opening_range_continuation_face_valid_v2.machine import vwap_location
from research.pb1_opening_range_continuation_face_valid_v2.walk import _d5, _finite, _load_panel, _mean, _next_open, _sma
from research.pb1_playbook_redesign_v3.structure_route import build_day_zones, merge_unique, units
from research.pb1_v3_1_face_validity_fix.noise import NoiseHistory
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.drive import classify_drive, extract_or_bars
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.machine import new_side, step_side
from research.support_resistance_face_valid_first_interaction_rebuild_v1.swings import new_swing_state, step_swings


def emit_v32(bind: dict[str, Any]) -> dict[str, Any]:
    loaded = _load_panel(bind)
    if not loaded.get("ok"):
        return loaded
    minutes = loaded["minutes"]
    disc = loaded["disc"]
    symbols = loaded["symbols"]
    blocks = dict(bind.get("blocks") or {})
    date_to_block = dict(blocks.get("date_to_block") or {})
    next_of = {disc[i]: disc[i + 1] for i in range(len(disc) - 1)}
    clock = ClockHistory()
    win_tv = WindowTVHistory()
    noise = NoiseHistory()
    hist: dict[str, list] = defaultdict(list)
    swings: dict[str, dict[str, Any]] = defaultdict(new_swing_state)
    reactions: dict[str, list] = defaultdict(list)
    grouped = {d: g for d, g in minutes.groupby("date", sort=False)}
    events: list[dict[str, Any]] = []
    archive: list[dict[str, Any]] = []
    funnel_days: list[dict[str, Any]] = []
    counts = defaultdict(int)
    same_bar_n = 0
    or_modified_n = 0
    print(f"WALK_DAYS {len(disc)} pb1_v3_2", flush=True)
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
            or_bars = extract_or_bars(rec, session_idx)
            drive = classify_drive(path, or_bars)
            or_width = (
                float(or15["or_high"]) - float(or15["or_low"])
                if or15.get("ok") and _finite(or15.get("or_high")) and _finite(or15.get("or_low"))
                else float("nan")
            )
            sma25_open = _sma(prior_closes, open_px, 25)
            sma75_open = _sma(prior_closes, open_px, 75)
            day_zones = []
            if session_idx and _finite(open_px) and _finite(atr):
                vw0 = rec["vw"][session_idx[0]]
                day_zones = merge_unique(
                    build_day_zones(
                        symbol=symbol,
                        reactions=list(reactions[symbol]),
                        atr=float(atr),
                        session_date=str(date),
                        hist=list(prior),
                        pdh=pdh,
                        pdl=pdl,
                        pdc=pdc,
                        d5h=d5h,
                        d5l=d5l,
                        sma25=sma25_open,
                        sma75=sma75_open,
                        vwap=vw0,
                        ref_px=float(open_px),
                    ),
                    float(atr),
                )

            def _commit() -> None:
                clock.commit_day(symbol, rec, session_idx)
                win_tv.commit(symbol, item["tv0915"])
                noise.commit_day(symbol, rec=rec, session_idx=session_idx, or_width=or_width)
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

            counts["symbol_days"] += 1
            if not or15.get("ok"):
                _commit()
                continue
            or_high = float(or15["or_high"])
            or_low = float(or15["or_low"])
            frozen = (or_high, or_low)
            if play:
                counts["S0"] += 1
            if not play:
                _commit()
                continue
            if drive.get("state") != "CLEAN_OPENING_DRIVE_V3" or drive.get("DIR") not in (1, -1):
                counts["S0_no_drive"] += 1
                counts["NON_DIRECTIONAL_OPEN"] += 1
                funnel_days.append(
                    {
                        "symbol": symbol,
                        "date": date,
                        "block": date_to_block.get(date),
                        "S0": True,
                        "S1": False,
                        "death": "NON_DIRECTIONAL_OPEN",
                        "open_state": drive.get("state"),
                        "auction": drive.get("auction"),
                        "DIR": 0,
                    }
                )
                _commit()
                continue
            counts["S1"] += 1

            st = new_side(int(drive["DIR"]))
            day_event = None
            impulse_pctl: list[float] = []
            impulse_tv_raw: list[float] = []
            for i in session_idx:
                t = str(rec["t"][i])
                if "09:00" <= t <= "09:14":
                    p = clock.tv_pctl(symbol, t, rec["va"][i])
                    if p is not None:
                        impulse_pctl.append(float(p))
                    if _finite(rec["va"][i]):
                        impulse_tv_raw.append(float(rec["va"][i]))
            impulse_tv = _mean(impulse_pctl)
            impulse_tv_sum = float(sum(impulse_tv_raw)) if impulse_tv_raw else None
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
                n1m = noise.normal_1m(symbol, t)
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
                    n1m=n1m,
                    zones=list(day_zones),
                )
                if ev is None:
                    continue
                nxt = _next_open(rec, session_idx, i)
                if nxt is None:
                    counts["no_next_open_n"] += 1
                    st.dead = True
                    st.death = "OTHER"
                    break
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
                nsnap = noise.snapshot(symbol, t)
                r_px = ev.get("planned_R")
                deg = None
                if _finite(entry) and _finite(px) and float(px) > 0:
                    signed = (float(entry) - float(px)) * float(ev["DIR"])
                    deg = {
                        "next_open_minus_trigger_close_signed": signed,
                        "adverse_gap": bool(signed < 0),
                        "used_to_cancel": False,
                    }
                row = {
                    **ev,
                    **nxt,
                    **path,
                    **nsnap,
                    "symbol": symbol,
                    "date": date,
                    "block": date_to_block.get(date),
                    "in_play": play,
                    "in_play_reason": play_why,
                    "open_state": drive.get("state"),
                    "open_reason": drive.get("reason"),
                    "auction": drive.get("auction"),
                    "or_close_loc": drive.get("or_close_loc"),
                    "net15_frac": drive.get("net_session_open_over_or"),
                    "eff_or_low": drive.get("eff_or_low"),
                    "eff_or_high": drive.get("eff_or_high"),
                    "CLEAN_OPENING_DRIVE_V3": True,
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
                    "impulse_tv_raw": impulse_tv_sum,
                    "retest_tv_pctl": retest_tv,
                    "retest_tv_raw": float(rec["va"][rp]) if _finite(rec["va"][rp]) else None,
                    "trigger_tv_pctl": trig_tv,
                    "reacceleration_tv_raw": float(rec["va"][i]) if _finite(rec["va"][i]) else None,
                    "tv_gated": False,
                    "tv_sequence": tv_sequence(impulse_tv, retest_tv, trig_tv),
                    "or_known_from": "09:15",
                    "chart_zones": day_zones[:18],
                    "planned_R_units": units(
                        r_px,
                        px=px,
                        atr=atr,
                        med_1m=nsnap.get("NORMAL_1M_RANGE"),
                        med_or15=nsnap.get("normal_OR15"),
                    ),
                    "execution_degradation": deg,
                    "eligibility_frozen_at": "trigger_close",
                    "next_open_is_execution_only": True,
                    "CLEAN_OPENING_IMPULSE_rebuilt": True,
                    "CLEAN_OPENING_DRIVE_V3": True,
                }
                events.append(row)
                day_event = row
                counts["setup_n"] += 1
                counts["S6"] += 1
                counts["S7"] += 1
                counts["S8"] += 1
                counts["S9"] += 1
                counts[f"dir_{row['direction']}"] += 1
                break

            if st.failed_push_archive:
                for a in st.failed_push_archive:
                    archive.append({"symbol": symbol, "date": date, "block": date_to_block.get(date), "DIR": st.sign, **a})
                counts["FAILED_PUSH_NOT_PB1"] += len(st.failed_push_archive)
            if st.s2:
                counts["S2"] += 1
            if st.s3:
                counts["S3"] += 1
            if st.s4:
                counts["S4"] += 1
            if st.s5:
                counts["S5"] += 1
            death = st.death
            if day_event is None and not st.dead:
                if st.s5:
                    death = "NO_RECLAIM"
                elif st.s2:
                    death = "STALE_30M"
                else:
                    death = "OTHER"
                counts[death] += 1
            elif day_event is None and st.dead:
                death = st.death or "OTHER"
                counts[str(death)] += 1
            funnel_days.append(
                {
                    "symbol": symbol,
                    "date": date,
                    "block": date_to_block.get(date),
                    "DIR": st.sign,
                    "S0": True,
                    "S1": True,
                    "S2": st.s2,
                    "S3": st.s3,
                    "S4": st.s4,
                    "S5": st.s5,
                    "S6": bool(day_event),
                    "S7": bool(day_event),
                    "S8": bool(day_event),
                    "S9": bool(day_event),
                    "death": None if day_event else death,
                    "failed_push_seen": st.failed_push_seen,
                    "impulse_lost": st.impulse_lost,
                    "treated": bool(day_event is not None),
                    "open_state": drive.get("state"),
                    "auction": drive.get("auction"),
                    "or_close_loc": drive.get("or_close_loc"),
                }
            )
            _commit()

    risk_invalid_n = int(sum(1 for e in events if not _finite(e.get("planned_R")) or float(e.get("planned_R") or 0) <= 0))
    failed_push_leak = int(sum(1 for e in events if e.get("trigger_primary") == "FAILED_PUSH_THEN_CLOSE_BACK"))
    micro_r_leak = int(
        sum(
            1
            for e in events
            if not _finite(e.get("planned_R_over_NORMAL_1M_RANGE")) or float(e.get("planned_R_over_NORMAL_1M_RANGE") or 0) < 1.0
        )
    )
    return {
        "ok": True,
        "events": events,
        "failed_push_archive": archive,
        "funnel_days": funnel_days,
        "counts": dict(counts),
        "same_bar_entry_n": same_bar_n,
        "or_modified_after_freeze_n": or_modified_n,
        "future_outcome_n": 0,
        "risk_invalid_n": risk_invalid_n,
        "failed_push_leak_n": failed_push_leak,
        "micro_structure_leak_n": micro_r_leak,
        "n_days": len(disc),
        "n_symbols": len(symbols),
        "eligibility_changed": False,
        "failed_push_removed_for_return": False,
        "v2_mutated": False,
        "v3_mutated": False,
        "pnl_test": False,
    }
