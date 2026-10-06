"""Discovery walk. State counts only. No future returns. Parents unread-write."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.pb1_opening_range_continuation_face_valid_v1.or15 import freeze_or15, session_idx_of, window_tv
from research.pb1_opening_range_continuation_face_valid_v2 import NEAR_DAILY_MA_ATR
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
from research.pb1_v4_clarified_machine_correction_v4 import EXEC_1M_CONFIRMED, EXEC_5M_DIRECT, OPERATIONAL_AM_LAST
from research.pb1_v4_clarified_machine_correction_v4.active import classify_active_loss, maybe_establish_opposite
from research.pb1_v4_clarified_machine_correction_v4.baselines import SameClockOpeningHistory
from research.pb1_v4_clarified_machine_correction_v4.execution import classify_e1
from research.pb1_v4_clarified_machine_correction_v4.machine import (
    mint_active,
    mint_seed,
    mint_why,
    new_side,
    note_expansion,
    observe_1m_interaction,
    step_5m_thesis,
)
from research.pb1_v4_clarified_machine_correction_v4.s0 import classify_s0
from research.pb1_v4_clarified_machine_correction_v4.seed import classify_seed
from research.pb1_v4_machine_implementation.bars5 import build_five_m, is_five_m_close
from research.support_resistance_face_valid_first_interaction_rebuild_v1.swings import new_swing_state, step_swings


def _opening_label(st: Any, seed_row: dict[str, Any]) -> str:
    if st.opening_drive_reached or st.opening_drive_id:
        if st.seed == "FAILED_OPEN_SEED":
            return "FAILED_OPEN_THEN_REAL_DRIVE"
        if st.seed == "TRUE_OPENING_DRIVE_SEED":
            return "TRUE_OPENING_DRIVE"
    if st.seed == "FAILED_OPEN_SEED":
        return "FAILED_OPEN_THEN_REAL_DRIVE" if st.extra.get("opposite_ok") else "FAILED_OPEN_SEED"
    return str(seed_row.get("subtype") or st.seed or "NO_VALID_DRIVE_SEED")


def _emit(
    *,
    st: Any,
    symbol: str,
    date: str,
    block: Any,
    s0: dict[str, Any],
    seed_row: dict[str, Any],
    nsnap: dict[str, Any],
    clock_snap: dict[str, Any],
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
        "WHY_THIS_STOCK": st.why_this_stock,
        "OPENING_DRIVE_SEED": st.seed,
        "OPENING_DRIVE_ACTIVE": bool(st.opening_drive_live),
        "OPENING_DRIVE_REACHED": bool(st.opening_drive_reached or st.opening_drive_id),
        "OPENING_DRIVE_LIVE": bool(st.opening_drive_live),
        "LOCATION_IDENTIFIED": st.location_identified,
        "THESIS_READY": bool(st.thesis_live),
        "THESIS_REACHED": bool(st.thesis_reached or st.thesis_id),
        "THESIS_LIVE": bool(st.thesis_live),
        "THESIS_LOST": st.thesis_lost,
        "THESIS_LOST_AT": st.thesis_lost_at,
        "THESIS_LOST_REASON": st.thesis_lost_reason or st.death,
        "E0_5M_CONFIRMATION": st.e0_5m_confirmation,
        "E1_1M_LEVEL_INTERACTION": st.e1_1m_level_interaction,
        "EXECUTION_READY": st.execution_ready,
        "FIVE_M_SETUP_VALID": bool(st.thesis_live),
        "ONE_M_ENTRY_ALLOWED": bool(st.thesis_live),
        "opening_state": _opening_label(st, seed_row),
        "location_family": loc.get("family"),
        "location_A_class": loc.get("A_class"),
        "location_reason": loc.get("reason"),
        "failed_open_form": (seed_row.get("failed_open") or {}).get("form"),
        "last_progress_class": st.extra.get("last_progress_class"),
        "post_dominant_class": (st.extra.get("post_dominant") or {}).get("post_dominant_class"),
        "POST_DOMINANT_PATH_STATE": (st.extra.get("post_dominant") or {}).get("post_dominant_class"),
        "sandwich_semantic_class": (st.extra.get("sandwich_counter") or {}).get("semantic_class"),
        "auction_end_family": st.extra.get("auction_end_family"),
        "interaction": st.interaction,
        "candidate_day_id": st.candidate_day_id,
        "opening_seed_id": st.opening_seed_id,
        "opening_drive_id": st.opening_drive_id,
        "location_id": st.location_id,
        "interaction_id": st.interaction_id,
        "thesis_id": st.thesis_id,
        "execution_id": st.execution_id,
        "or_high": or_high,
        "or_low": or_low,
        "s0_reason": s0.get("reason"),
        "seed_reason": seed_row.get("reason"),
        **nsnap,
        "same_clock": clock_snap,
        "abs_gap_atr": gap_atr,
        "xs_rank_pct": xs,
        "tv_0915": tv0915,
        "tv_0915_pctl": tv_pctl,
        "atr20": atr,
        "pdh": pdh,
        "pdl": pdl,
        "pdc": pdc,
        "hidden_1m_snapshot": st.hidden_snap,
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
    open5 = SameClockOpeningHistory()
    hist: dict[str, list] = defaultdict(list)
    swings: dict[str, dict[str, Any]] = defaultdict(new_swing_state)
    reactions: dict[str, list] = defaultdict(list)
    grouped = {d: g for d, g in minutes.groupby("date", sort=False)}
    setups: list[dict[str, Any]] = []
    e0_events: list[dict[str, Any]] = []
    e1_events: list[dict[str, Any]] = []
    funnel_days: list[dict[str, Any]] = []
    hidden_checks: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    same_bar_n = 0
    print(f"WALK_DAYS {len(disc)} pb1_v4_clarified", flush=True)
    for di, date in enumerate(disc):
        if di % 50 == 0:
            print(f"WALK {di}/{len(disc)} {date} thesis={len(setups)} e0={len(e0_events)} e1={len(e1_events)}", flush=True)
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
                open5.commit_day(symbol, rec, session_idx, date)
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
                "WHY_THIS_STOCK": False,
                "OPENING_DRIVE_SEED": None,
                "OPENING_DRIVE_ACTIVE": False,
                "LOCATION_IDENTIFIED": False,
                "THESIS_READY": False,
                "E0": False,
                "E1": False,
                "DIR": 0,
                "opening_state": None,
                "death": None,
                "S0": False,
                "S1": False,
                "S2": False,
                "S3": False,
                "S4": False,
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
            clock_snap = open5.snapshot_open(symbol, bars_open, atr20=atr)
            s0 = classify_s0(
                bars_open=bars_open,
                clock_snap=clock_snap,
                abs_gap_atr=gap_atr,
                atr20=atr,
                xs_rank_pct=xs,
                tv_0915_pctl=tv_pctl,
            )
            if not s0.get("ok"):
                counts["S0_fail"] += 1
                funnel["death"] = str(s0.get("reason") or "S0_FAIL")
                funnel_days.append(funnel)
                _commit()
                continue
            counts["S0"] += 1
            counts["WHY_THIS_STOCK"] += 1
            funnel["S0"] = True
            funnel["WHY_THIS_STOCK"] = True
            seed_row = classify_seed(bars_open, clock_snap=clock_snap, atr20=atr)
            st = new_side(int(seed_row.get("DIR") or 0) if int(seed_row.get("DIR") or 0) in (1, -1) else 1)
            mint_why(st, symbol=symbol, date=date)
            mint_seed(st, seed=str(seed_row.get("seed") or "NO_VALID_DRIVE_SEED"), subtype=seed_row.get("subtype"))
            st.extra["seed"] = dict(seed_row)
            st.extra["scale"] = seed_row.get("scale")
            st.extra["drive_origin"] = open_px
            st.extra["post_dominant"] = dict((seed_row.get("intent") or {}).get("post_dominant") or {})
            st.extra["sandwich_counter"] = dict((seed_row.get("intent") or {}).get("sandwich_counter") or {})
            if seed_row.get("seed") == "TRUE_OPENING_DRIVE_SEED" and int(seed_row.get("DIR") or 0) in (1, -1):
                st.sign = int(seed_row["DIR"])
                mint_active(st)
            elif seed_row.get("seed") == "FAILED_OPEN_SEED" and int(seed_row.get("DIR") or 0) in (1, -1):
                st.sign = int(seed_row["DIR"])
                st.extra["forming_failed"] = not bool(seed_row.get("opposite_established_at_seed"))
                if seed_row.get("opposite_established_at_seed"):
                    st.extra["opposite_ok"] = True
                    mint_active(st)
                fail = dict(seed_row.get("failed_open") or {})
                if _finite(fail.get("failed_extreme")):
                    st.extra["drive_origin"] = float(fail["failed_extreme"])
                last_open = bars_open[-1]
                if st.sign > 0 and _finite(last_open.get("h")):
                    st.extra["drive_ext"] = float(last_open["h"])
                elif st.sign < 0 and _finite(last_open.get("l")):
                    st.extra["drive_ext"] = float(last_open["l"])
                st.extra["progress_bar"] = dict(last_open)
            else:
                counts["S1_fail"] += 1
                counts[str(seed_row.get("subtype") or "NO_VALID_DRIVE_SEED")] += 1
                funnel["OPENING_DRIVE_SEED"] = seed_row.get("seed")
                funnel["opening_state"] = seed_row.get("subtype")
                funnel["death"] = str(seed_row.get("subtype") or "NO_VALID_DRIVE_SEED")
                funnel["post_dominant_class"] = (st.extra.get("post_dominant") or {}).get("post_dominant_class")
                funnel["POST_DOMINANT_PATH_STATE"] = (st.extra.get("post_dominant") or {}).get("post_dominant_class")
                funnel["sandwich_semantic_class"] = (st.extra.get("sandwich_counter") or {}).get("semantic_class")
                funnel_days.append(funnel)
                _commit()
                continue

            day_setup = None
            e0_row = None
            e1_row = None
            for k, i in enumerate(session_idx):
                t = str(rec["t"][i])
                if t > OPERATIONAL_AM_LAST:
                    break
                if (or_high, or_low) != frozen:
                    or_high, or_low = frozen
                n1m = noise.normal_1m(symbol, t)
                vw = rec["vw"][i]
                if t >= "09:14" and is_five_m_close(t):
                    bars_now = [b for b in bars_all if b["t1"] <= t[:5]]
                    bar5 = next((b for b in bars_all if b["t1"] == t[:5]), None)
                    if st.extra.get("forming_failed") and (not st.opening_drive_id):
                        opp = maybe_establish_opposite(
                            bars=bars_now,
                            seed_row=dict(st.extra.get("seed") or seed_row),
                            scale=st.extra.get("scale"),
                        )
                        if opp.get("ok"):
                            st.extra["forming_failed"] = False
                            st.extra["opposite_ok"] = True
                            mint_active(st)
                        else:
                            extra = [b for b in bars_now if b["t1"] > "09:14"]
                            if extra:
                                note_bar = extra[-1]
                                prev_progress = st.extra.get("progress_bar")
                                note_expansion(st, bar=note_bar)
                                loss = classify_active_loss(
                                    sign=st.sign,
                                    close=float(note_bar["c"]),
                                    or_high=or_high,
                                    or_low=or_low,
                                    open_0900=st.extra.get("drive_origin") or open_px,
                                    peak_disp=st.peak_disp,
                                    wick_only_n=0,
                                    micro_break_n=0,
                                    recross_closes=st.recross_closes,
                                    left=False,
                                    five_m_no_expansion_n=st.five_m_no_expansion_n,
                                    both_or_extremes_revisited=st.both_or_extremes,
                                    location_identified=False,
                                    had_renewed_auction=bool(st.extra.get("had_renewed_auction")),
                                    bar=note_bar,
                                    prev_bar=prev_progress,
                                    extra=st.extra,
                                )
                                if loss.get("lost"):
                                    st.death = str(loss.get("reason") or "OPENING_RESOLVED_WITHOUT_DRIVE")
                                    st.extra["forming_failed"] = False
                                    break
                    if bar5 is not None and st.opening_drive_live and (not st.thesis_lost):
                        step_5m_thesis(
                            st,
                            symbol=symbol,
                            date=date,
                            t=t,
                            pos=i,
                            bar=bar5,
                            or_high=or_high,
                            or_low=or_low,
                            open_0900=st.extra.get("drive_origin") or open_px,
                            zones=list(day_zones),
                            pdh=pdh,
                            pdl=pdl,
                            pdc=pdc,
                            vwap=vw,
                            n1m=n1m,
                            session_open=open_px,
                        )
                        if st.thesis_id and day_setup is None:
                            nsnap = noise.snapshot(symbol, t)
                            sma25 = _sma(prior_closes, rec["c"][i], 25)
                            sma75 = _sma(prior_closes, rec["c"][i], 75)
                            day_setup = {
                                **_emit(
                                    st=st,
                                    symbol=symbol,
                                    date=date,
                                    block=date_to_block.get(date),
                                    s0=s0,
                                    seed_row=seed_row,
                                    nsnap=nsnap,
                                    clock_snap=clock_snap,
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
                                "vwap_retest": vwap_location(st.retest_high, st.retest_low, rec["c"][i], vw, st.sign)
                                if st.retest_high is not None
                                else None,
                                "daily_bias": daily_bias(_sma(prior_closes, rec["c"][i], 5), sma25, sma75),
                                "near_sma25": near_ma(rec["c"][i], sma25, atr, NEAR_DAILY_MA_ATR),
                            }
                            setups.append(day_setup)
                            counts["THESIS_READY"] += 1
                            counts["S4"] += 1
                            counts["setup_n"] += 1
                            counts[f"dir_{day_setup['direction']}"] += 1
                if t < "09:15":
                    continue
                if st.thesis_lost and not st.execution_ready:
                    break
                if st.thesis_live and st.location:
                    observe_1m_interaction(
                        st,
                        t=t,
                        high=float(rec["h"][i]),
                        low=float(rec["l"][i]),
                        close=float(rec["c"][i]),
                    )
                if st.e0_5m_confirmation and (not st.extra.get("e0_emitted")) and st.e0_pos is not None:
                    if (not st.thesis_live) or st.thesis_lost:
                        counts["E0_EMIT_FAIL_CLOSED_NOT_LIVE"] += 1
                        st.e0_5m_confirmation = False
                        st.execution_ready = False
                        st.extra["e0_emitted"] = True
                    else:
                        nxt = _next_open(rec, session_idx, int(st.e0_pos))
                        if nxt is None:
                            counts["e0_no_next_open_n"] += 1
                            st.extra["e0_emitted"] = True
                        elif nxt.get("same_bar_entry"):
                            same_bar_n += 1
                        else:
                            st.extra["e0_emitted"] = True
                            st.execution_id = f"{st.thesis_id}|E0|{nxt.get('entry_t')}"
                            e0_row = {
                                **(day_setup or {}),
                                **nxt,
                                "exec_variant": EXEC_5M_DIRECT,
                                "entry_kind": EXEC_5M_DIRECT,
                                "same_bar_entry": False,
                                "execution_id": st.execution_id,
                                "thesis_id": st.thesis_id,
                                "THESIS_LIVE_AT_ENTRY": True,
                            }
                            e0_events.append(e0_row)
                            counts["E0"] += 1
                if st.thesis_live and (not st.e1_1m_level_interaction) and (not st.thesis_lost):
                    if st.thesis_ready_pos is not None and i <= int(st.thesis_ready_pos):
                        continue
                    if st.interaction in (None, "NO_INTERACTION_YET"):
                        continue
                    if st.retest_high is None or st.retest_low is None:
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
                        thesis_ready=bool(st.thesis_live),
                        thesis_lost=bool(st.thesis_lost),
                    )
                    if e1.get("state") == "E1_ATTEMPT_CANCELLED":
                        counts["E1_ATTEMPT_CANCELLED"] += 1
                        continue
                    if not e1.get("ok"):
                        continue
                    nxt = _next_open(rec, session_idx, i)
                    if nxt is None:
                        counts["e1_no_next_open_n"] += 1
                        continue
                    if nxt.get("same_bar_entry"):
                        same_bar_n += 1
                        continue
                    st.e1_1m_level_interaction = True
                    st.execution_ready = True
                    st.e1_pos = i
                    eid = f"{st.thesis_id}|E1|{nxt.get('entry_t')}"
                    if st.execution_id is None:
                        st.execution_id = eid
                    e1_row = {
                        **(day_setup or {}),
                        **nxt,
                        **{k: v for k, v in e1.items() if k != "ok"},
                        "exec_variant": EXEC_1M_CONFIRMED,
                        "entry_kind": EXEC_1M_CONFIRMED,
                        "same_bar_entry": False,
                        "execution_id": eid,
                        "thesis_id": st.thesis_id,
                        "opening_drive_id": st.opening_drive_id,
                        "location_id": st.location_id,
                    }
                    e1_events.append(e1_row)
                    counts["E1"] += 1

            active_reached = bool(st.opening_drive_reached or st.opening_drive_id)
            active_live = bool(st.opening_drive_live)
            thesis_reached = bool(st.thesis_reached or st.thesis_id)
            thesis_live = bool(st.thesis_live)
            if st.seed and st.seed != "NO_VALID_DRIVE_SEED":
                counts["OPENING_DRIVE_SEED"] += 1
                counts["S1"] += 1
            if active_reached:
                counts["OPENING_DRIVE_REACHED"] += 1
            if active_live:
                counts["OPENING_DRIVE_ACTIVE"] += 1
                counts["OPENING_DRIVE_LIVE"] += 1
            if thesis_reached:
                counts["THESIS_REACHED"] += 1
            if thesis_live:
                counts["THESIS_LIVE"] += 1
            if st.thesis_lost:
                counts["THESIS_LOST"] += 1
            if st.location_identified:
                counts["LOCATION_IDENTIFIED"] += 1
                counts["S2"] += 1
                counts["S3"] += 1
                a_cls = str((st.location or {}).get("A_class") or "")
                if a_cls:
                    counts[a_cls] += 1
                fam = str((st.location or {}).get("family") or "")
                if fam:
                    counts[f"LOCATION_FAMILY_{fam}"] += 1
            for a_seen in list(st.extra.get("family_a_seen") or []):
                counts[f"FAMILY_A_SEEN_{a_seen}"] += 1
            if st.hidden_snap:
                snap = dict(st.hidden_snap)
                hidden_ok = (
                    snap.get("stock") == symbol
                    and snap.get("direction") == ("bull" if st.sign > 0 else "bear")
                    and snap.get("opening_drive_id") == st.opening_drive_id
                    and snap.get("location_id") == st.location_id
                    and snap.get("thesis_id") == st.thesis_id
                    and bool(snap.get("thesis_ready")) is True
                )
                hidden_checks.append({"symbol": symbol, "date": date, "ok": hidden_ok, **snap})
                if not hidden_ok:
                    counts["HIDDEN_1M_THESIS_PARITY_FAIL"] += 1
            counts["identity_mismatch_n"] += int(st.identity_mismatch)
            counts["1M_created_location_n"] += int(st.one_m_created_location)
            counts["1M_changed_direction_n"] += int(st.one_m_changed_direction)
            counts["1M_changed_opening_drive_n"] += int(st.one_m_changed_drive)
            counts["THESIS_REVIVED_BY_EXECUTION_n"] += int(st.revived_by_execution)
            funnel.update(
                {
                    "DIR": st.sign,
                    "S1": active_reached,
                    "S2": st.location_identified,
                    "S3": st.location_identified,
                    "S4": thesis_reached,
                    "E0": bool(e0_row),
                    "E1": bool(e1_row),
                    "WHY_THIS_STOCK": True,
                    "OPENING_DRIVE_SEED": st.seed,
                    "OPENING_DRIVE_ACTIVE": active_live,
                    "OPENING_DRIVE_REACHED": active_reached,
                    "OPENING_DRIVE_LIVE": active_live,
                    "LOCATION_IDENTIFIED": st.location_identified,
                    "THESIS_READY": thesis_live,
                    "THESIS_REACHED": thesis_reached,
                    "THESIS_LIVE": thesis_live,
                    "THESIS_LOST": st.thesis_lost,
                    "THESIS_LOST_AT": st.thesis_lost_at,
                    "THESIS_LOST_REASON": st.thesis_lost_reason or st.death,
                    "opening_state": _opening_label(st, seed_row),
                    "death": st.thesis_lost_reason or st.death or (None if thesis_reached else seed_row.get("subtype")),
                    "location_family": (st.location or {}).get("family"),
                    "location_A_class": (st.location or {}).get("A_class"),
                    "location_reason": (st.location or {}).get("reason"),
                    "failed_open_form": (seed_row.get("failed_open") or {}).get("form"),
                    "last_progress_class": st.extra.get("last_progress_class"),
                    "family_a_seen": list(st.extra.get("family_a_seen") or []),
                    "progress_log": list(st.extra.get("progress_log") or []),
                    "post_dominant_class": (st.extra.get("post_dominant") or {}).get("post_dominant_class"),
                    "POST_DOMINANT_PATH_STATE": (st.extra.get("post_dominant") or {}).get("post_dominant_class"),
                    "sandwich_semantic_class": (st.extra.get("sandwich_counter") or {}).get("semantic_class"),
                    "auction_end_family": st.extra.get("auction_end_family"),
                    "interaction": st.interaction,
                    "candidate_day_id": st.candidate_day_id,
                    "opening_seed_id": st.opening_seed_id,
                    "opening_drive_id": st.opening_drive_id,
                    "location_id": st.location_id,
                    "thesis_id": st.thesis_id,
                    "execution_id": st.execution_id,
                    "hidden_1m_snapshot": st.hidden_snap,
                }
            )
            if funnel["death"]:
                counts[str(funnel["death"])] += 1
            funnel_days.append(funnel)
            _commit()

    hidden_parity = (not hidden_checks) or all(bool(x.get("ok")) for x in hidden_checks)
    return {
        "ok": True,
        "setups": setups,
        "e0_events": e0_events,
        "e1_events": e1_events,
        "funnel_days": funnel_days,
        "hidden_1m_checks": hidden_checks,
        "HIDDEN_1M_THESIS_PARITY": bool(hidden_parity),
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
