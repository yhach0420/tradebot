"""V4 Discovery walk. State counts only. No future returns. Parents unread-write."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.pb1_opening_range_continuation_face_valid_v1.or15 import freeze_or15, session_idx_of, window_tv
from research.pb1_opening_range_continuation_face_valid_v2 import LAST_TRIGGER, NEAR_DAILY_MA_ATR
from research.pb1_opening_range_continuation_face_valid_v2.inplay import (
    WindowTVHistory,
    abs_gap_atr,
    daily_bias,
    near_ma,
    xs_rank_pct,
)
from research.pb1_opening_range_continuation_face_valid_v2.machine import vwap_location
from research.pb1_opening_range_continuation_face_valid_v2.walk import _d5, _finite, _load_panel, _next_open, _sma
from research.pb1_playbook_redesign_v3.structure_route import build_day_zones as build_causal_sr_zones, merge_unique
from research.pb1_v3_1_face_validity_fix.noise import NoiseHistory
from research.pb1_v4_implementation_correction import EXEC_1M_CONFIRMED, EXEC_5M_DIRECT, LAST_BREAK, STALL_5M_BARS
from research.pb1_v4_implementation_correction.diagnostics import persist_route_diagnostics
from research.pb1_v4_implementation_correction.machine import commit_s2_s3, mint_s1_identity, new_side, note_five_m_leave, step_break_retest
from research.pb1_v4_implementation_correction.s1 import classify_s1, extend_failed_open, s1_pass
from research.pb1_v4_machine_implementation.bars5 import build_five_m, is_five_m_close
from research.pb1_v4_machine_implementation.baselines import Opening5mHistory
from research.pb1_v4_machine_implementation.execution import classify_e1
from research.pb1_v4_machine_implementation.s0 import classify_s0
from research.pb1_v4_machine_implementation.s4 import classify_s4
from research.support_resistance_face_valid_first_interaction_rebuild_v1.swings import new_swing_state, step_swings


def _emit_common(
    *,
    st: Any,
    symbol: str,
    date: str,
    block: Any,
    s0: dict[str, Any],
    s1: dict[str, Any],
    nsnap: dict[str, Any],
    open_snap: dict[str, Any],
    or_high: float,
    or_low: float,
    gap_atr: Any,
    xs: Any,
    tv0915: Any,
    tv_pctl: Any,
    atr: Any,
    pdh: Any,
    pdl: Any,
    pdc: Any,
) -> dict[str, Any]:
    loc = dict(st.location or {})
    return {
        "symbol": symbol,
        "date": date,
        "block": block,
        "DIR": st.sign,
        "direction": "bull" if st.sign > 0 else "bear",
        "FIVE_M_SETUP_VALID": bool(st.s4),
        "ONE_M_ENTRY_ALLOWED": bool(st.s4),
        "opening_state": st.opening_state,
        "S0": True,
        "S1": True,
        "S2": st.s2,
        "S3": st.s3,
        "S4": st.s4,
        "SETUP_ELIGIBLE": st.setup_eligible,
        "SETUP_ELIGIBLE_AT": st.setup_eligible_at,
        "location_family": loc.get("family"),
        "location_reason": loc.get("reason"),
        "or_touch_alone": loc.get("or_touch_alone"),
        "break_t": st.break_t,
        "retest_t": st.retest_t,
        "proto_setup_id": st.proto_setup_id,
        "opening_drive_id": st.opening_drive_id,
        "break_id": st.break_id,
        "location_id": st.location_id,
        "retest_id": st.retest_id,
        "setup_id": st.setup_id,
        "or_high": or_high,
        "or_low": or_low,
        "s0_reason": s0.get("reason"),
        "s1_reason": s1.get("reason"),
        "s1_or_half_used": False,
        **nsnap,
        **open_snap,
        "abs_gap_atr": gap_atr,
        "xs_rank_pct": xs,
        "tv_0915": tv0915,
        "tv_0915_pctl": tv_pctl,
        "atr20": atr,
        "pdh": pdh,
        "pdl": pdl,
        "pdc": pdc,
        "invalidation": float(st.retest_low) if st.sign > 0 and _finite(st.retest_low) else (float(st.retest_high) if _finite(st.retest_high) else None),
        "future_return_used": False,
        "pnl_used": False,
    }


def emit_v4(bind: dict[str, Any]) -> dict[str, Any]:
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
    open5 = Opening5mHistory()
    hist: dict[str, list] = defaultdict(list)
    swings: dict[str, dict[str, Any]] = defaultdict(new_swing_state)
    reactions: dict[str, list] = defaultdict(list)
    grouped = {d: g for d, g in minutes.groupby("date", sort=False)}
    setups: list[dict[str, Any]] = []
    e0_events: list[dict[str, Any]] = []
    e1_events: list[dict[str, Any]] = []
    funnel_days: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    same_bar_n = 0
    print(f"WALK_DAYS {len(disc)} pb1_v4", flush=True)
    for di, date in enumerate(disc):
        if di % 50 == 0:
            print(f"WALK {di}/{len(disc)} {date} setups={len(setups)} e0={len(e0_events)} e1={len(e1_events)}", flush=True)
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
            tv_pctl = win_tv.pctl(symbol, item["tv0915"])
            xs = ranks.get(symbol)
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
                    build_causal_sr_zones(
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
                open5.commit_day(symbol, rec, session_idx)
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
            funnel = {
                "symbol": symbol,
                "date": date,
                "block": date_to_block.get(date),
                "S0": False,
                "S1": False,
                "S2": False,
                "S3": False,
                "S4": False,
                "E0": False,
                "E1": False,
                "DIR": 0,
                "opening_state": None,
                "death": None,
                "SETUP_ELIGIBLE_AT": None,
            }
            if not or15.get("ok"):
                funnel["death"] = "or_invalid"
                funnel_days.append(funnel)
                _commit()
                continue
            or_high = float(or15["or_high"])
            or_low = float(or15["or_low"])
            frozen = (or_high, or_low)
            bars_all = build_five_m(rec, session_idx, through="11:19")
            bars_open = [b for b in bars_all if b["t1"] <= "09:14"]
            n_open = open5.normal(symbol)
            open_snap = open5.snapshot(symbol)
            s0 = classify_s0(
                bars_open=bars_open,
                normal_opening_5m=n_open,
                abs_gap_atr=gap_atr,
                atr20=atr,
                xs_rank_pct=xs,
                tv_0915_pctl=tv_pctl,
            )
            if not s0.get("ok"):
                counts["S0_fail"] += 1
                funnel["death"] = str(s0.get("reason") or "S0_FAIL")
                funnel["opening_state"] = None
                funnel_days.append(funnel)
                _commit()
                continue
            counts["S0"] += 1
            counts["S0_PREFIX_N"] += 1
            funnel["S0"] = True
            s1 = classify_s1(bars_open, normal_opening_5m=n_open, atr20=atr)
            st = None
            if s1_pass(s1):
                st = new_side(int(s1["DIR"]))
                st.s0 = True
                st.s1 = True
                st.opening_state = str(s1["state"])
                mint_s1_identity(st, symbol=symbol, date=date)
                if _finite(s1.get("failed_extreme")):
                    st.extra["drive_origin"] = float(s1["failed_extreme"])
                else:
                    st.extra["drive_origin"] = open_px
            elif s1.get("forming") and int(s1.get("DIR") or 0) in (1, -1) and str(s1.get("state")) == "FAILED_OPEN_THEN_REAL_DRIVE":
                st = new_side(int(s1["DIR"]))
                st.s0 = True
                st.opening_state = "FAILED_OPEN_THEN_REAL_DRIVE"
                st.extra["forming_s1"] = True
                st.extra["s1_locked_open"] = dict(s1)
                if _finite(s1.get("failed_extreme")):
                    st.extra["drive_origin"] = float(s1["failed_extreme"])
                else:
                    st.extra["drive_origin"] = open_px
            else:
                counts["S1_fail"] += 1
                counts[str(s1.get("state") or "S1_FAIL")] += 1
                funnel["opening_state"] = s1.get("state")
                funnel["death"] = str(s1.get("state") or "S1_FAIL")
                funnel_days.append(funnel)
                _commit()
                continue

            day_setup = None
            e0_row = None
            e1_row = None
            last_s1 = s1
            for k, i in enumerate(session_idx):
                t = str(rec["t"][i])
                if t > LAST_TRIGGER:
                    break
                if (or_high, or_low) != frozen:
                    or_high, or_low = frozen
                if t >= "09:14" and is_five_m_close(t):
                    bars_now = [b for b in bars_all if b["t1"] <= t[:5]]
                    extra = [b for b in bars_now if b["t1"] > "09:14"]
                    bar5 = None
                    for b in bars_all:
                        if b["t1"] == t[:5]:
                            bar5 = b
                            break
                    if (not st.s1) and st.extra.get("forming_s1"):
                        last_s1 = extend_failed_open(
                            dict(st.extra.get("s1_locked_open") or s1),
                            extra,
                            open_bars=bars_open,
                            normal_opening_5m=n_open,
                            atr20=atr,
                        )
                        if last_s1.get("true_from_later_bars"):
                            counts["true_from_later_bars_n"] += 1
                        if s1_pass(last_s1) and str(last_s1.get("state")) == "FAILED_OPEN_THEN_REAL_DRIVE":
                            st.sign = int(last_s1["DIR"])
                            st.s1 = True
                            st.opening_state = "FAILED_OPEN_THEN_REAL_DRIVE"
                            st.extra["forming_s1"] = False
                            mint_s1_identity(st, symbol=symbol, date=date)
                            if _finite(last_s1.get("failed_extreme")):
                                st.extra["drive_origin"] = float(last_s1["failed_extreme"])
                        elif last_s1.get("forming"):
                            if int(last_s1.get("DIR") or 0) in (1, -1):
                                st.sign = int(last_s1["DIR"])
                        else:
                            st.dead = True
                            st.death = str(last_s1.get("state") or "S1_FAIL")
                            st.opening_state = str(last_s1.get("state") or st.opening_state)
                            break
                    if st.s1 and bar5 is not None:
                        note_five_m_leave(st, bar=bar5, or_high=or_high, or_low=or_low)
                    if st.s1 and st.retest_pos is not None and (not st.s2) and (not st.dead) and bar5 is not None:
                        n1m_now = noise.normal_1m(symbol, t)
                        vw_now = rec["vw"][i]
                        commit_s2_s3(
                            st,
                            close=float(rec["c"][i]),
                            or_high=or_high,
                            or_low=or_low,
                            n1m=n1m_now,
                            zones=list(day_zones),
                            five_m_bar=bar5,
                            pdh=pdh,
                            pdl=pdl,
                            pdc=pdc,
                            vwap=vw_now,
                            sma25=_sma(prior_closes, rec["c"][i], 25),
                            sma75=_sma(prior_closes, rec["c"][i], 75),
                            atr=atr,
                        )
                    if st.s1 and (not st.broken) and t > "09:14" and (not st.dead):
                        extremes = [float(b["h"]) if st.sign > 0 else float(b["l"]) for b in bars_now]
                        ext_now = max(extremes) if st.sign > 0 else min(extremes)
                        prev_ext = st.extra.get("drive_ext")
                        if prev_ext is None:
                            st.extra["drive_ext"] = ext_now
                            st.five_m_no_expansion_n = 0
                        elif (st.sign > 0 and ext_now > float(prev_ext)) or (st.sign < 0 and ext_now < float(prev_ext)):
                            st.extra["drive_ext"] = ext_now
                            st.five_m_no_expansion_n = 0
                        else:
                            st.five_m_no_expansion_n += 1
                            if st.five_m_no_expansion_n >= int(STALL_5M_BARS):
                                st.dead = True
                                st.death = "NO_DIRECTIONAL_EXPANSION_BEFORE_BREAK"
                                break
                if not st.s1:
                    continue
                if t < "09:15":
                    continue
                prev_c = rec["c"][session_idx[k - 1]] if k > 0 else None
                n1m = noise.normal_1m(symbol, t)
                vw = rec["vw"][i]
                sma25 = _sma(prior_closes, rec["c"][i], 25)
                sma75 = _sma(prior_closes, rec["c"][i], 75)
                step_break_retest(
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
                    open_0900=st.extra.get("drive_origin") or open_px,
                    zones=list(day_zones),
                    pdh=pdh,
                    pdl=pdl,
                    pdc=pdc,
                    vwap=vw,
                    sma25=sma25,
                    sma75=sma75,
                )
                if is_five_m_close(t) and st.s1 and (not st.dead):
                    bar5 = None
                    for b in bars_all:
                        if b["t1"] == t[:5]:
                            bar5 = b
                            break
                    if bar5 is not None:
                        note_five_m_leave(st, bar=bar5, or_high=or_high, or_low=or_low)
                        if st.retest_pos is not None and (not st.s2):
                            commit_s2_s3(
                                st,
                                close=float(rec["c"][i]),
                                or_high=or_high,
                                or_low=or_low,
                                n1m=n1m,
                                zones=list(day_zones),
                                five_m_bar=bar5,
                                pdh=pdh,
                                pdl=pdl,
                                pdc=pdc,
                                vwap=vw,
                                sma25=sma25,
                                sma75=sma75,
                                atr=atr,
                            )
                if st.dead and not st.setup_eligible:
                    break
                if st.s2 and st.s3 and (not st.s4) and is_five_m_close(t) and t <= LAST_TRIGGER:
                    bar = None
                    for b in bars_all:
                        if b["t1"] == t[:5]:
                            bar = b
                            break
                    if bar is not None and (st.setup_eligible_pos is None or i > int(st.retest_pos or -1)):
                        s4 = classify_s4(
                            sign=st.sign,
                            bar=bar,
                            retest_high=st.retest_high,
                            retest_low=st.retest_low,
                            normal_opening_5m=n_open,
                            n1m=n1m,
                        )
                        if s4.get("ok"):
                            st.s4 = True
                            st.setup_eligible = True
                            st.setup_eligible_at = t[:5]
                            st.setup_eligible_pos = i
                            st.setup_id = f"{st.proto_setup_id}|{st.setup_eligible_at}"
                            st.extra["s4"] = s4
                            nsnap = noise.snapshot(symbol, t)
                            diag = persist_route_diagnostics(
                                sign=st.sign,
                                close=float(rec["c"][i]),
                                retest_high=st.retest_high,
                                retest_low=st.retest_low,
                                n1m=n1m,
                                zones=list(day_zones),
                            )
                            day_setup = {
                                **_emit_common(
                                    st=st,
                                    symbol=symbol,
                                    date=date,
                                    block=date_to_block.get(date),
                                    s0=s0,
                                    s1=last_s1,
                                    nsnap=nsnap,
                                    open_snap=open_snap,
                                    or_high=or_high,
                                    or_low=or_low,
                                    gap_atr=gap_atr,
                                    xs=xs,
                                    tv0915=item["tv0915"],
                                    tv_pctl=tv_pctl,
                                    atr=atr,
                                    pdh=pdh,
                                    pdl=pdl,
                                    pdc=pdc,
                                ),
                                "s4_reason": s4.get("reason"),
                                "route_diagnostic": diag,
                                "vwap_retest": vwap_location(st.retest_high, st.retest_low, rec["c"][i], vw, st.sign)
                                if st.retest_high is not None
                                else None,
                                "daily_bias": daily_bias(_sma(prior_closes, rec["c"][i], 5), sma25, sma75),
                                "near_sma25": near_ma(rec["c"][i], sma25, atr, NEAR_DAILY_MA_ATR),
                            }
                            setups.append(day_setup)
                            counts["S4"] += 1
                            counts["setup_n"] += 1
                            counts[f"dir_{day_setup['direction']}"] += 1
                        else:
                            st.five_m_no_expansion_n += 1
                if st.setup_eligible and (not st.e0_emitted) and (not st.dead):
                    nxt = _next_open(rec, session_idx, int(st.setup_eligible_pos))
                    if nxt is None:
                        counts["e0_no_next_open_n"] += 1
                        st.e0_emitted = True
                    elif nxt.get("same_bar_entry"):
                        same_bar_n += 1
                    else:
                        st.e0_emitted = True
                        e0_row = {
                            **(day_setup or {}),
                            **nxt,
                            "exec_variant": EXEC_5M_DIRECT,
                            "entry_kind": EXEC_5M_DIRECT,
                            "same_bar_entry": False,
                            "setup_id": (day_setup or {}).get("setup_id"),
                        }
                        e0_events.append(e0_row)
                        counts["E0"] += 1
                if st.setup_eligible and (not st.e1_emitted) and t > LAST_BREAK:
                    pass
                if st.setup_eligible and (not st.e1_emitted):
                    if st.dead:
                        st.e1_cancel = True
                        counts["E1_CANCEL"] += 1
                        st.e1_emitted = True
                        continue
                    if st.setup_eligible_pos is not None and i <= int(st.setup_eligible_pos):
                        continue
                    e1 = classify_e1(
                        sign=st.sign,
                        rec=rec,
                        pos=i,
                        open_px=float(rec["o"][i]) if _finite(rec["o"][i]) else float(rec["c"][i]),
                        high=float(rec["h"][i]),
                        low=float(rec["l"][i]),
                        close=float(rec["c"][i]),
                        retest_high=st.retest_high,
                        retest_low=st.retest_low,
                        n1m=n1m,
                        setup_eligible=True,
                        five_m_setup_valid=bool(st.s4),
                        thesis_lost=bool(st.dead),
                    )
                    if e1.get("state") == "CANCEL_EXECUTION_OPPORTUNITY":
                        st.e1_cancel = True
                        st.e1_emitted = True
                        counts["E1_CANCEL"] += 1
                        continue
                    if not e1.get("ok"):
                        continue
                    nxt = _next_open(rec, session_idx, i)
                    if nxt is None:
                        counts["e1_no_next_open_n"] += 1
                        st.e1_emitted = True
                        continue
                    if nxt.get("same_bar_entry"):
                        same_bar_n += 1
                        continue
                    st.e1_emitted = True
                    e1_row = {
                        **(day_setup or {}),
                        **nxt,
                        **{k: v for k, v in e1.items() if k != "ok"},
                        "exec_variant": EXEC_1M_CONFIRMED,
                        "entry_kind": EXEC_1M_CONFIRMED,
                        "same_bar_entry": False,
                        "setup_id": (day_setup or {}).get("setup_id"),
                    }
                    e1_events.append(e1_row)
                    counts["E1"] += 1

            if st.s1:
                counts["S1"] += 1
            if st.s2:
                counts["S2"] += 1
            if st.s3:
                if not st.s2:
                    counts["S3_without_S2_n"] += 1
                else:
                    counts["S3"] += 1
            if funnel.get("S0") and st.s1:
                counts["S1_PREFIX_N"] += 1
            if funnel.get("S0") and st.s1 and st.s2:
                counts["S2_PREFIX_N"] += 1
            if funnel.get("S0") and st.s1 and st.s2 and st.s3:
                counts["S3_PREFIX_N"] += 1
            if funnel.get("S0") and st.s1 and st.s2 and st.s3 and st.s4:
                counts["S4_PREFIX_N"] += 1
            funnel.update(
                {
                    "DIR": st.sign,
                    "S1": st.s1,
                    "S2": st.s2,
                    "S3": st.s3,
                    "S4": st.s4,
                    "E0": bool(e0_row),
                    "E1": bool(e1_row),
                    "opening_state": st.opening_state,
                    "death": None if st.s4 else (st.death or last_s1.get("state")),
                    "SETUP_ELIGIBLE_AT": st.setup_eligible_at,
                    "location_family": (st.location or {}).get("family"),
                    "location_reason": (st.location or {}).get("reason"),
                    "s1_reason": last_s1.get("reason"),
                    "proto_setup_id": st.proto_setup_id,
                    "opening_drive_id": st.opening_drive_id,
                    "break_id": st.break_id,
                    "location_id": st.location_id,
                    "retest_id": st.retest_id,
                    "setup_id": st.setup_id,
                }
            )
            if funnel["death"]:
                counts[str(funnel["death"])] += 1
            funnel_days.append(funnel)
            _commit()

    return {
        "ok": True,
        "setups": setups,
        "e0_events": e0_events,
        "e1_events": e1_events,
        "funnel_days": funnel_days,
        "counts": dict(counts),
        "same_bar_entry_n": same_bar_n,
        "future_outcome_n": 0,
        "n_days": len(disc),
        "n_symbols": len(symbols),
        "v32_mutated": False,
        "v3_mutated": False,
        "v2_mutated": False,
        "pnl_test": False,
        "economic_e0_e1_comparison": False,
        "return_test": False,
        "mfe_mae": False,
    }
