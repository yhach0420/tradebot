"""V2 Discovery walk. No future returns. No matching. No PnL. V1 is a diagnostic shadow only."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.pb1_opening_range_continuation_face_valid_v1.machine import new_sides as v1_sides
from research.pb1_opening_range_continuation_face_valid_v1.machine import step_side as v1_step
from research.pb1_opening_range_continuation_face_valid_v1.or15 import freeze_or15, session_idx_of, window_tv
from research.pb1_opening_range_continuation_face_valid_v2 import (
    LAST_TRIGGER,
    NEAR_DAILY_MA_ATR,
    ROOM_RR_AVAILABLE,
    ROOM_RR_QUESTIONABLE,
    SESSION_FLAT,
)
from research.pb1_opening_range_continuation_face_valid_v2.impulse import classify_opening, opening_path
from research.pb1_opening_range_continuation_face_valid_v2.inplay import (
    WindowTVHistory,
    abs_gap_atr,
    daily_bias,
    in_play_flag,
    in_play_reason,
    near_ma,
    tv_sequence,
    v1_in_play_flag,
    xs_rank_pct,
)
from research.pb1_opening_range_continuation_face_valid_v2.machine import new_side, step_side, trading_minutes, vwap_location
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.machine import confirm_minor_swing


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _sma(prior: list[float], px: float, n: int) -> float:
    if not _finite(px) or n <= 0:
        return float("nan")
    xs = list(prior[-(n - 1) :]) + [float(px)] if n > 1 else [float(px)]
    if len(xs) < n:
        return float("nan")
    return float(sum(xs) / float(n))


def _mean(xs: list[float]) -> float:
    ys = [float(x) for x in xs if _finite(x)]
    if not ys:
        return float("nan")
    return float(sum(ys) / len(ys))


def _load_panel(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    disc = [str(d) for d in list(split.get("discovery_dates") or [])]
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    symbols = list(bind.get("symbols") or [])
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)} pb1_or_cont_v2", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=set(disc), forbidden_dates=conf | val)
    if minutes.empty:
        return {"ok": False, "reason": "empty_minutes"}
    if minutes["date"].isin(list(conf | val)).any():
        raise RuntimeError("forbidden_partition_loaded")
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])
    return {"ok": True, "minutes": minutes, "disc": disc, "symbols": symbols}


def _next_open(rec: dict[str, Any], session_idx: list[int], pos: int) -> dict[str, Any] | None:
    loc = None
    for k, i in enumerate(session_idx):
        if i == pos:
            loc = k
            break
    if loc is None or loc + 1 >= len(session_idx):
        return None
    j = session_idx[loc + 1]
    t = str(rec["t"][j])
    if t >= "11:30" or in_lunch(t) or t >= SESSION_FLAT:
        return None
    o = rec["o"][j]
    if not _finite(o) or float(o) <= 0:
        return None
    return {"entry_pos": j, "entry_t": t, "entry_px": float(o), "same_bar_entry": False}


def _d5(hist: list[dict[str, Any]]) -> tuple[float, float]:
    w = [d for d in hist[-5:] if _finite(d.get("high")) and _finite(d.get("low"))]
    if len(w) < 5:
        return float("nan"), float("nan")
    return float(max(float(d["high"]) for d in w)), float(min(float(d["low"]) for d in w))


def _targets(sign: int, entry: float, levels: dict[str, float], inv: float) -> dict[str, Any]:
    ahead: list[tuple[str, float]] = []
    for name, lvl in levels.items():
        if not _finite(lvl):
            continue
        if sign > 0 and float(lvl) > entry:
            ahead.append((name, float(lvl)))
        if sign < 0 and float(lvl) < entry:
            ahead.append((name, float(lvl)))
    if sign > 0:
        ahead.sort(key=lambda x: x[1])
        risk = (entry - float(inv)) if _finite(inv) and float(inv) < entry else float("nan")
    else:
        ahead.sort(key=lambda x: -x[1])
        risk = (float(inv) - entry) if _finite(inv) and float(inv) > entry else float("nan")
    nearest = ahead[0] if ahead else None
    reward = abs(nearest[1] - entry) if nearest else float("nan")
    risk_bps = float(risk / entry * 10000.0) if _finite(risk) and entry else float("nan")
    reward_bps = float(reward / entry * 10000.0) if _finite(reward) and entry else float("nan")
    ratio = float(reward / risk) if _finite(reward) and _finite(risk) and risk > 0 else float("nan")
    if nearest is None or not _finite(ratio) or ratio < float(ROOM_RR_QUESTIONABLE):
        room = "NO_OBVIOUS_ROOM"
    elif ratio < float(ROOM_RR_AVAILABLE):
        room = "ROOM_QUESTIONABLE"
    else:
        room = "ROOM_AVAILABLE"
    return {
        "nearest_target": None if nearest is None else nearest[0],
        "nearest_target_px": None if nearest is None else nearest[1],
        "structural_risk_px": risk,
        "structural_risk_bps": risk_bps,
        "reward_px": reward,
        "reward_bps": reward_bps,
        "reward_to_risk": ratio,
        "reward_space_plausible": bool(room == "ROOM_AVAILABLE"),
        "already_extended": bool(room == "NO_OBVIOUS_ROOM"),
        "room_class": room,
        "target_names_ahead": [n for n, _ in ahead],
    }


def _v1_impulse_dir(path: dict[str, Any]) -> int:
    c15, mid = path.get("close_0914"), path.get("or_mid")
    if _finite(c15) and _finite(mid):
        if float(c15) > float(mid):
            return 1
        if float(c15) < float(mid):
            return -1
    net = path.get("net_or15")
    if _finite(net) and float(net) > 0:
        return 1
    if _finite(net) and float(net) < 0:
        return -1
    return 0


def emit_events(bind: dict[str, Any]) -> dict[str, Any]:
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
    day_rows: list[dict[str, Any]] = []
    counts = defaultdict(int)
    same_bar_n = 0
    false_break_n = 0
    or_modified_n = 0
    print(f"WALK_DAYS {len(disc)}", flush=True)
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
            v1_play = v1_in_play_flag(gap_atr, tv_pctl, xs)
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
            day_rows.append(
                {
                    "symbol": symbol,
                    "date": date,
                    "block": date_to_block.get(date),
                    "or_ok": bool(or15.get("ok")),
                    "abs_gap_atr": gap_atr,
                    "tv_0915": item["tv0915"],
                    "tv_0915_pctl": tv_pctl,
                    "xs_rank_pct": xs,
                    "v1_in_play": v1_play,
                    "in_play": play,
                    "in_play_reason": play_why,
                    "open_state": opened.get("state"),
                    "open_dir": opened.get("DIR"),
                    "open_reason": opened.get("reason"),
                    "atr20": atr,
                }
            )
            counts["symbol_days"] += 1
            counts[f"open_{opened.get('state')}"] += 1
            if or15.get("ok"):
                counts["or_complete_n"] += 1
            else:
                counts["or_incomplete_n"] += 1
            if v1_play:
                counts["v1_in_play_n"] += 1
            if play:
                counts["in_play_n"] += 1
            if v1_play and not play:
                counts["v1_in_play_removed_n"] += 1
            if opened.get("state") == "FAILED_OPEN_REVERSAL":
                counts["failed_open_n"] += 1
                if v1_play:
                    counts["v1_failed_open_in_play_n"] += 1

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

            # V1 shadow: same-side-as-V1-midpoint machine, in-play V1 only.
            v1_dir = _v1_impulse_dir(path)
            if v1_play and v1_dir in (1, -1):
                shadow = v1_sides()[v1_dir]
                for i in session_idx:
                    t = str(rec["t"][i])
                    if t < "09:15" or t > LAST_TRIGGER:
                        continue
                    sh, sl = confirm_minor_swing(rec["h"], rec["l"], i)
                    ev1 = v1_step(
                        shadow,
                        pos=i,
                        t=t,
                        high=rec["h"][i],
                        low=rec["l"][i],
                        close=rec["c"][i],
                        or_high=or_high,
                        or_low=or_low,
                        swing_high=sh,
                        swing_low=sl,
                    )
                    if ev1 is None:
                        continue
                    counts["v1_shadow_setup_n"] += 1
                    mins = trading_minutes(ev1.get("break_t"), ev1.get("retest_t"))
                    if opened.get("state") == "FAILED_OPEN_REVERSAL":
                        counts["v1_failed_open_setups_removed_n"] += 1
                    if _finite(mins) and mins > 30:
                        counts["v1_stale_setups_removed_n"] += 1
                    if not play:
                        counts["v1_quiet_setups_removed_n"] += 1
                    break

            if opened.get("state") != "CLEAN_OPENING_IMPULSE" or opened.get("DIR") not in (1, -1):
                _commit()
                continue
            if not play:
                counts["clean_not_in_play_n"] += 1
                _commit()
                continue

            st = new_side(int(opened["DIR"]))
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
                counts["setup_n"] += 1
                counts[f"dir_{row['direction']}"] += 1
                counts[f"trig_{row['trigger_primary']}"] += 1
                counts[f"room_{row['room_class']}"] += 1
            if st.death == "stale_retest" or st.death == "stale_no_retest":
                counts["stale_retest_n"] += 1
            if st.micro_break_n and not st.broken:
                counts["micro_break_only_n"] += 1
            if st.broken and not st.left:
                counts["break_never_left_n"] += 1
            _commit()
    return {
        "ok": True,
        "events": events,
        "day_rows": day_rows,
        "counts": dict(counts),
        "same_bar_entry_n": same_bar_n,
        "false_break_n": false_break_n,
        "or_modified_after_freeze_n": or_modified_n,
        "future_outcome_n": 0,
        "n_days": len(disc),
        "n_symbols": len(symbols),
    }
