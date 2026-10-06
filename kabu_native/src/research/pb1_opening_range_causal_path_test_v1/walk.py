"""Discovery walk: frozen V2 machine + next-open path. No retune. No PnL."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.pb1_opening_range_continuation_face_valid_v2 import LAST_TRIGGER, NEAR_DAILY_MA_ATR
from research.pb1_opening_range_causal_path_test_v1.path import (
    loc_features,
    ratios_to_r,
    signed_path,
    structural_r,
    target_semantics,
    trigger_to_entry_bps,
)
from research.pb1_opening_range_continuation_face_valid_v1.or15 import freeze_or15, session_idx_of, window_tv
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


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _slim_rec(rec: dict[str, Any], session_idx: list[int]) -> dict[str, Any]:
    t_to_pos = {str(rec["t"][i]): int(i) for i in session_idx}
    return {
        "t": rec["t"],
        "o": rec["o"],
        "h": rec["h"],
        "l": rec["l"],
        "c": rec["c"],
        "vw": rec["vw"],
        "session_idx": list(session_idx),
        "t_to_pos": t_to_pos,
    }


def _attach_path(ev: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    session_idx = rec["session_idx"]
    sign = int(ev["DIR"])
    entry = float(ev["entry_px"])
    trig_c = rec["c"][int(ev["trigger_pos"])]
    t2e = trigger_to_entry_bps(trig_c, entry)
    signed_t2e = float(sign) * t2e if _finite(t2e) else float("nan")
    risk = structural_r(sign=sign, entry=entry, retest_high=ev.get("retest_high"), retest_low=ev.get("retest_low"))
    lv = {
        "PDH": ev.get("pdh"),
        "PDL": ev.get("pdl"),
        "PDC": ev.get("pdc"),
        "D5H": ev.get("d5h"),
        "D5L": ev.get("d5l"),
        "SMA25": ev.get("sma25"),
        "SMA75": ev.get("sma75"),
        "VWAP": rec["vw"][int(ev["trigger_pos"])],
    }
    tgt = target_semantics(sign=sign, entry=entry, levels=lv, r_px=risk.get("R"))
    path = signed_path(
        rec,
        session_idx=session_idx,
        entry_pos=int(ev["entry_pos"]),
        sign=sign,
        or_high=float(ev["or_high"]),
        or_low=float(ev["or_low"]),
        retest_high=ev.get("retest_high"),
        retest_low=ev.get("retest_low"),
        target_px=tgt.get("nearest_target_px"),
    )
    from_close = signed_path(
        rec,
        session_idx=session_idx,
        entry_pos=int(ev["trigger_pos"]),
        sign=sign,
        or_high=float(ev["or_high"]),
        or_low=float(ev["or_low"]),
        retest_high=ev.get("retest_high"),
        retest_low=ev.get("retest_low"),
        target_px=tgt.get("nearest_target_px"),
        px0_override=float(trig_c) if _finite(trig_c) else None,
    )
    rr = ratios_to_r(path, risk.get("R"), entry)
    loc = loc_features(entry, ev.get("atr20"), ev.get("pdh"), ev.get("pdl"), ev.get("d5h"), ev.get("d5l"))
    known = {
        "or15_known_before_entry": True,
        "in_play_known_before_entry": True,
        "opening_impulse_known_before_entry": True,
        "break_leave_retest_trigger_completed_before_entry": True,
        "same_bar_entry": False,
        "states_known_before_next_open": True,
    }
    return {
        **ev,
        **path,
        **tgt,
        **risk,
        **rr,
        **loc,
        **known,
        "trigger_close": float(trig_c) if _finite(trig_c) else None,
        "trigger_to_entry_bps": t2e,
        "trigger_to_entry_signed_bps": signed_t2e,
        "from_trigger_close_r5_bps": from_close.get("r5_bps"),
        "from_trigger_close_r10_bps": from_close.get("r10_bps"),
        "from_trigger_close_r20_bps": from_close.get("r20_bps"),
        "from_trigger_close_MFE_bps": from_close.get("MFE_bps"),
        "consumption_r5_bps": (
            float(from_close["r5_bps"]) - float(path["r5_bps"])
            if _finite(from_close.get("r5_bps")) and _finite(path.get("r5_bps"))
            else float("nan")
        ),
        "consumption_r10_bps": (
            float(from_close["r10_bps"]) - float(path["r10_bps"])
            if _finite(from_close.get("r10_bps")) and _finite(path.get("r10_bps"))
            else float("nan")
        ),
        "consumption_MFE_bps": (
            float(from_close["MFE_bps"]) - float(path["MFE_bps"])
            if _finite(from_close.get("MFE_bps")) and _finite(path.get("MFE_bps"))
            else float("nan")
        ),
    }


def emit_treated_and_eligible(bind: dict[str, Any]) -> dict[str, Any]:
    loaded = _load_panel(bind)
    if not loaded.get("ok"):
        return loaded
    minutes = loaded["minutes"]
    disc = loaded["disc"]
    symbols = loaded["symbols"]
    blocks = dict(bind.get("blocks") or {})
    date_to_block = dict(blocks.get("date_to_block") or {})
    clock = ClockHistory()
    win_tv = WindowTVHistory()
    hist: dict[str, list] = defaultdict(list)
    grouped = {d: g for d, g in minutes.groupby("date", sort=False)}
    events: list[dict[str, Any]] = []
    recs: dict[tuple[str, str], dict[str, Any]] = {}
    eligible: list[dict[str, Any]] = []
    counts = defaultdict(int)
    same_bar_n = 0
    false_break_n = 0
    or_modified_n = 0
    print(f"WALK_DAYS {len(disc)} pb1_causal_path", flush=True)
    for di, date in enumerate(disc):
        if di % 50 == 0:
            print(f"WALK {di}/{len(disc)} {date} events={len(events)} eligible={len(eligible)}", flush=True)
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
            counts[f"open_{opened.get('state')}"] += 1

            def _commit() -> None:
                clock.commit_day(symbol, rec, session_idx)
                win_tv.commit(symbol, item["tv0915"])
                day = daily_from_minutes(rec, date)
                if day:
                    hist[symbol].append(day)

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
                row = {
                    **ev,
                    **nxt,
                    **path,
                    "symbol": symbol,
                    "date": date,
                    "block": date_to_block.get(date),
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
                    **tgt,
                }
                events.append(row)
                day_event = row
                if first_trig is None:
                    first_trig = t
                counts["setup_n"] += 1
                counts[f"dir_{row['direction']}"] += 1
                counts[f"trig_{row['trigger_primary']}"] += 1

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
        treated.append(attached)
    return {
        "ok": True,
        "events": treated,
        "eligible": eligible,
        "recs": recs,
        "counts": dict(counts),
        "same_bar_entry_n": same_bar_n,
        "false_break_n": false_break_n,
        "or_modified_after_freeze_n": or_modified_n,
        "future_outcome_n": 0,
        "n_days": len(disc),
        "n_symbols": len(symbols),
    }
