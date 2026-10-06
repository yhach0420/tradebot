"""Frozen V2 funnel walk. Earlier-state membership does not require later completion."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.cross_sectional_peer_propagation_discovery_v1.peers import sector_of_map
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.pb1_causal_path_failure_rca_v1.metrics import (
    break_window_stats,
    first_passage,
    opening_shape,
    or_fail_class,
    risk_invalid_why,
    stage_path,
)
from research.pb1_opening_range_causal_path_test_v1.path import loc_features, ratios_to_r, structural_r, target_semantics, trigger_to_entry_bps
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


def _px_at(rec: dict[str, Any], pos: int | None) -> float:
    if pos is None:
        return float("nan")
    c = rec["c"][int(pos)]
    return float(c) if _finite(c) else float("nan")


def emit_funnel(bind: dict[str, Any]) -> dict[str, Any]:
    loaded = _load_panel(bind)
    if not loaded.get("ok"):
        return loaded
    minutes = loaded["minutes"]
    disc = loaded["disc"]
    symbols = loaded["symbols"]
    blocks = dict(bind.get("blocks") or {})
    date_to_block = dict(blocks.get("date_to_block") or {})
    sector_of = sector_of_map(bind)
    clock = ClockHistory()
    win_tv = WindowTVHistory()
    hist: dict[str, list] = defaultdict(list)
    grouped = {d: g for d, g in minutes.groupby("date", sort=False)}
    events: list[dict[str, Any]] = []
    recs: dict[tuple[str, str], dict[str, Any]] = {}
    eligible: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    market_days: list[dict[str, Any]] = []
    counts = defaultdict(int)
    same_bar_n = 0
    false_break_n = 0
    or_modified_n = 0
    print(f"WALK_DAYS {len(disc)} pb1_failure_rca", flush=True)
    for di, date in enumerate(disc):
        if di % 50 == 0:
            print(f"WALK {di}/{len(disc)} {date} s6={len(events)} s0={counts.get('S0', 0)}", flush=True)
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
        later_or = 0
        later_or_n = 0
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
            or15 = item["or15"]
            if or15.get("ok"):
                later_or_n += 1
                oh, ol = float(or15["or_high"]), float(or15["or_low"])
                broke = False
                for i in session_idx:
                    t = str(rec["t"][i])
                    if t <= "09:15":
                        continue
                    if t >= "11:30":
                        break
                    c = rec["c"][i]
                    if _finite(c) and (float(c) > oh or float(c) < ol):
                        broke = True
                        break
                if broke:
                    later_or += 1
        med = float(sorted(rets_0915)[len(rets_0915) // 2]) if rets_0915 else float("nan")
        disp = float((sum((x - (sum(rets_0915) / len(rets_0915))) ** 2 for x in rets_0915) / len(rets_0915)) ** 0.5) if len(rets_0915) > 1 else float("nan")
        sec_agree = []
        mkt_sign = 1 if _finite(med) and med > 0 else (-1 if _finite(med) and med < 0 else 0)
        for _sec, xs in sec_pos.items():
            if len(xs) < 2:
                continue
            p = sum(xs) / len(xs)
            sec_sign = 1 if p > 0.5 else (-1 if p < 0.5 else 0)
            sec_agree.append(int(sec_sign == mkt_sign and sec_sign != 0))
        market_days.append(
            {
                "date": date,
                "block": date_to_block.get(date),
                "n_panel": n_panel,
                "breadth_0915": float(above_open / n_panel) if n_panel else None,
                "median_dir_bps": med,
                "dispersion_bps": disp,
                "frac_above_open": float(above_open / n_panel) if n_panel else None,
                "frac_break_own_or_later": float(later_or / later_or_n) if later_or_n else None,
                "sector_agree": float(sum(sec_agree) / len(sec_agree)) if sec_agree else None,
                "gap_dispersion": disp,
            }
        )

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
            prior_range = float(prior[-1]["high"] - prior[-1]["low"]) if prior and _finite(prior[-1].get("high")) and _finite(prior[-1].get("low")) else float("nan")
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

            counts["S0"] += 1
            sign = int(opened["DIR"])
            st = new_side(sign)
            onset: dict[str, dict[str, Any]] = {}
            day_event = None
            first_trig = None
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
                if st.broken and "S1" not in onset and st.break_pos is not None:
                    onset["S1"] = {"pos": int(st.break_pos), "t": st.break_t, "px": _px_at(rec, st.break_pos)}
                if st.left and "S2" not in onset and st.left_pos is not None:
                    onset["S2"] = {"pos": int(st.left_pos), "t": str(rec["t"][st.left_pos]), "px": _px_at(rec, st.left_pos)}
                if st.retest_pos is not None and "S3" not in onset:
                    onset["S3"] = {"pos": int(st.retest_pos), "t": st.retest_t, "px": _px_at(rec, st.retest_pos)}
                if st.held and "S4" not in onset and st.retest_pos is not None:
                    onset["S4"] = {"pos": int(st.retest_pos), "t": st.retest_t, "px": _px_at(rec, st.retest_pos)}
                if ev is None:
                    continue
                if ev.get("false_break"):
                    false_break_n += 1
                    continue
                onset["S5"] = {"pos": int(i), "t": t, "px": _px_at(rec, i)}
                nxt = _next_open(rec, session_idx, i)
                if nxt is None:
                    counts["no_next_open_n"] += 1
                    continue
                if nxt.get("same_bar_entry"):
                    same_bar_n += 1
                    continue
                onset["S6"] = {"pos": int(nxt["entry_pos"]), "t": nxt["entry_t"], "px": float(nxt["entry_px"])}
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
                    "sector": sector_of.get(symbol, ""),
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
                    "prior_day_range": prior_range,
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
            shape = opening_shape(path, rec, session_idx, sign)
            base_stage = {
                "symbol": symbol,
                "date": date,
                "block": date_to_block.get(date),
                "DIR": sign,
                "direction": "bull" if sign > 0 else "bear",
                "in_play_reason": play_why,
                "or_high": or_high,
                "or_low": or_low,
                "or_range": or_high - or_low,
                "atr20": atr,
                "abs_gap_atr": gap_atr,
                "tv_0915_pctl": tv_pctl,
                "impulse_mag": abs(float(path["net_or15"])) / float(path["or_range"]) if _finite(path.get("net_or15")) and _finite(path.get("or_range")) and float(path["or_range"]) > 0 else float("nan"),
                **shape,
                "death": st.death,
                "treated": bool(day_event is not None),
            }
            stages.append({**base_stage, "stage": "S0", "pos": session_idx[min(15, len(session_idx) - 1)] if session_idx else None, "t": "09:15", "px": None})
            for stg, info in onset.items():
                stages.append({**base_stage, "stage": stg, **info, "break_beyond": st.break_beyond, "away_n": st.away_n, "break_t": st.break_t, "retest_t": st.retest_t, "break_to_retest_minutes": st.break_to_retest_minutes, "trigger_primary": (day_event or {}).get("trigger_primary")})
                counts[stg] += 1
            px_0915 = rec["c"][session_idx[min(15, len(session_idx) - 1)]] if session_idx else float("nan")
            sma5 = _sma(prior_closes, float(px_0915) if _finite(px_0915) else float("nan"), 5)
            sma25 = _sma(prior_closes, float(px_0915) if _finite(px_0915) else float("nan"), 25)
            sma75 = _sma(prior_closes, float(px_0915) if _finite(px_0915) else float("nan"), 75)
            loc0915 = loc_features(px_0915, atr, pdh, pdl, d5h, d5l)
            eligible.append(
                {
                    "symbol": symbol,
                    "date": date,
                    "block": date_to_block.get(date),
                    "DIR": sign,
                    "direction": "bull" if sign > 0 else "bear",
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
                    **loc0915,
                }
            )
            counts["eligible_n"] += 1
            _commit()

    treated: list[dict[str, Any]] = []
    for ev in events:
        rec = recs.get((str(ev["symbol"]), str(ev["date"])))
        if rec is None:
            continue
        attached = _attach_path(ev, rec)
        rng = attached.get("or_range")
        net = attached.get("net_or15")
        attached["impulse_mag"] = abs(float(net)) / float(rng) if _finite(net) and _finite(rng) and float(rng) > 0 else float("nan")
        sign = int(attached["DIR"])
        risk = structural_r(sign=sign, entry=float(attached["entry_px"]), retest_high=attached.get("retest_high"), retest_low=attached.get("retest_low"))
        cons = break_window_stats(
            rec,
            sign=sign,
            break_pos=int(attached["break_pos"]),
            retest_pos=attached.get("retest_pos"),
            entry_pos=attached.get("entry_pos"),
            trigger_pos=attached.get("trigger_pos"),
            or_range=rng,
            atr=attached.get("atr20"),
            r_px=risk.get("R"),
        )
        fp = first_passage(
            rec,
            entry_pos=int(attached["entry_pos"]),
            sign=sign,
            entry=float(attached["entry_px"]),
            r_px=risk.get("R"),
            or_high=float(attached["or_high"]),
            or_low=float(attached["or_low"]),
            retest_high=attached.get("retest_high"),
            retest_low=attached.get("retest_low"),
        )
        ofc = or_fail_class(
            rec,
            entry_pos=int(attached["entry_pos"]),
            sign=sign,
            or_high=float(attached["or_high"]),
            or_low=float(attached["or_low"]),
            entry=float(attached["entry_px"]),
        )
        labels = list(attached.get("trigger_labels") or [])
        why = None
        if attached.get("risk_state") == "RISK_INVALID":
            why = risk_invalid_why(
                sign,
                attached.get("entry_px"),
                attached.get("retest_high"),
                attached.get("retest_low"),
                rec["h"][int(attached["trigger_pos"])],
                rec["l"][int(attached["trigger_pos"])],
                labels,
            )
        brk = rec["c"][int(attached["break_pos"])]
        body = attached.get("break_body")
        brng = attached.get("break_range")
        attached.update(
            {
                "or_range_over_atr": float(rng) / float(attached["atr20"]) if _finite(rng) and _finite(attached.get("atr20")) and float(attached["atr20"]) > 0 else float("nan"),
                "break_beyond_over_or": float(attached["break_beyond"]) / float(rng) if _finite(attached.get("break_beyond")) and _finite(rng) and float(rng) > 0 else float("nan"),
                "break_body_over_range": float(body) / float(brng) if _finite(body) and _finite(brng) and float(brng) > 0 else float("nan"),
                "max_away_over_or": float(attached.get("max_away")) / float(rng) if _finite(attached.get("max_away")) and _finite(rng) and float(rng) > 0 else float("nan"),
                "risk_invalid_why": why,
                "trigger_both_labels": len(labels) >= 2,
                **{f"cons_{k}_{uk}": (v or {}).get(uk) if isinstance(v, dict) else v for k, v in cons.items() for uk in ("bps", "or_units", "atr_units", "R_units") if isinstance(v, dict)},
                **{f"fp_{k}": v for k, v in fp.items()},
                **{f"orf_{k}": v for k, v in ofc.items()},
                **opening_shape(attached, rec, rec["session_idx"], sign),
            }
        )
        treated.append(attached)

    stage_paths: list[dict[str, Any]] = []
    for row in stages:
        if row.get("stage") == "S0" or not _finite(row.get("pos")):
            continue
        rec = recs.get((str(row["symbol"]), str(row["date"])))
        if rec is None:
            continue
        sp = stage_path(rec, pos=int(row["pos"]), sign=int(row["DIR"]), or_high=float(row["or_high"]), or_low=float(row["or_low"]), px0=row.get("px"))
        stage_paths.append({**{k: row.get(k) for k in ("symbol", "date", "block", "direction", "stage", "t", "in_play_reason", "treated", "trigger_primary", "break_t")}, **sp})

    return {
        "ok": True,
        "events": treated,
        "eligible": eligible,
        "recs": recs,
        "stages": stages,
        "stage_paths": stage_paths,
        "market_days": market_days,
        "counts": dict(counts),
        "same_bar_entry_n": same_bar_n,
        "false_break_n": false_break_n,
        "or_modified_after_freeze_n": or_modified_n,
        "future_outcome_n": 0,
        "n_days": len(disc),
        "n_symbols": len(symbols),
        "eligibility_changed": False,
    }
