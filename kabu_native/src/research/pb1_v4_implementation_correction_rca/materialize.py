"""Materialize first-three 5m bars + baselines for the 88. Discovery only. No future."""
from __future__ import annotations

import json
from collections import defaultdict, deque
from typing import Any

from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.pb1_opening_range_continuation_face_valid_v1.or15 import freeze_or15, session_idx_of
from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite, close_loc
from research.pb1_opening_range_continuation_face_valid_v2.walk import _load_panel
from research.pb1_v3_1_face_validity_fix import NOISE_LOOKBACK_SESSIONS
from research.pb1_v3_2_face_failure_rca.second_pass import HUMAN_LABELS
from research.pb1_v4_implementation_correction import OPENING_5M_MIN_OBS, TRUE_BODY_FRAC_MIN, TRUE_COUNTER_FRAC, TRUE_DISP_MIN, TRUE_N_SAME_MIN, TRUE_RANGE_MIN
from research.pb1_v4_implementation_correction.isolation import RCA_CACHE
from research.pb1_v4_implementation_correction.machine import five_m_fully_beyond
from research.pb1_v4_implementation_correction.s1 import classify_s1, s1_pass
from research.pb1_v4_machine_implementation.bars5 import build_five_m, sequence_for_sign
from research.pb1_v4_opening_drive_location_reaccel_spec import VALID_OPENING_STATES

WINDOWS = (("09:00", "09:04"), ("09:05", "09:09"), ("09:10", "09:14"))


def _ratio(num: Any, den: Any) -> float | None:
    if not (_finite(num) and _finite(den) and float(den) > 0):
        return None
    return float(num) / float(den)


def _median(xs: list[float]) -> float | None:
    vs = sorted(float(x) for x in xs if _finite(x) and float(x) > 0)
    if len(vs) < int(OPENING_5M_MIN_OBS):
        return None
    n = len(vs)
    mid = n // 2
    if n % 2:
        return float(vs[mid])
    return 0.5 * (float(vs[mid - 1]) + float(vs[mid]))


def _bar_slim(b: dict[str, Any], *, normal: Any) -> dict[str, Any]:
    rng = b.get("range")
    body = b.get("body")
    signed = (float(b["c"]) - float(b["o"])) if _finite(b.get("c")) and _finite(b.get("o")) else None
    return {
        "t0": b.get("t0"),
        "t1": b.get("t1"),
        "o": b.get("o"),
        "h": b.get("h"),
        "l": b.get("l"),
        "c": b.get("c"),
        "signed_body": signed,
        "body_over_range": b.get("body_over_range"),
        "range_over_normal": _ratio(rng, normal),
        "close_location": b.get("close_loc"),
        "direction": b.get("direction"),
        "net": b.get("net"),
        "range": rng,
        "body": body,
    }


def opening_sequence(bars: list[dict[str, Any]], *, normal: Any, or_high: Any, or_low: Any) -> dict[str, Any]:
    if len(bars) < 3:
        return {"ok": False}
    first_o = bars[0].get("o")
    last_c = bars[2].get("c")
    net = (float(last_c) - float(first_o)) if _finite(first_o) and _finite(last_c) else None
    ranges = [float(b["range"]) for b in bars if _finite(b.get("range"))]
    nets = [abs(float(b["net"])) for b in bars if _finite(b.get("net"))]
    gross_range = sum(ranges) if ranges else None
    gross_net = sum(nets) if nets else None
    dirs = [int(b.get("direction") or 0) for b in bars]
    c1, c2, c3 = bars[0].get("c"), bars[1].get("c"), bars[2].get("c")
    p12 = (float(c2) - float(c1)) if _finite(c1) and _finite(c2) else None
    p23 = (float(c3) - float(c2)) if _finite(c2) and _finite(c3) else None
    sign_guess = 1 if _finite(net) and float(net) >= 0 else -1
    seq = sequence_for_sign(bars[:3], sign=sign_guess, normal_range=normal)
    opp_seq = sequence_for_sign(bars[:3], sign=-sign_guess, normal_range=normal)
    best = seq if abs(float(seq.get("net_displacement_over_normal_5m") or 0)) >= abs(float(opp_seq.get("net_displacement_over_normal_5m") or 0)) else opp_seq
    dir_disp = best.get("net_directional_displacement")
    ctr = best.get("largest_counter_over_normal_5m")
    disp_n = best.get("net_displacement_over_normal_5m")
    or_range = (float(or_high) - float(or_low)) if _finite(or_high) and _finite(or_low) else None
    opp_ext = float(or_low) if int(best.get("first_bar_direction") or sign_guess) > 0 or (disp_n or 0) >= 0 else float(or_high) if _finite(or_high) else None
    if _finite(disp_n) and float(disp_n) < 0:
        opp_ext = float(or_high) if _finite(or_high) else None
        session_disp_from_opp = (float(opp_ext) - float(last_c)) if _finite(opp_ext) and _finite(last_c) else None
    else:
        opp_ext = float(or_low) if _finite(or_low) else None
        session_disp_from_opp = (float(last_c) - float(opp_ext)) if _finite(opp_ext) and _finite(last_c) else None
    one_bar = (max(ranges) / gross_range) if gross_range and gross_range > 0 else None
    return {
        "net_directional_displacement_over_normal": disp_n,
        "gross_path_length_over_normal": _ratio(gross_range, normal),
        "directional_efficiency": _ratio(abs(float(net)) if _finite(net) else None, gross_range),
        "max_counter_over_normal": ctr,
        "counter_over_displacement": _ratio(best.get("largest_counter_5m"), abs(float(dir_disp)) if _finite(dir_disp) and abs(float(dir_disp)) > 0 else None),
        "n_same_direction_bodies": best.get("n_same_dir_5m"),
        "n_opposite_bodies": best.get("n_counter_5m"),
        "close_progression": [p12, p23],
        "first_to_second_progression": p12,
        "second_to_third_progression": p23,
        "first_direction": dirs[0] if dirs else 0,
        "last_direction": dirs[-1] if dirs else 0,
        "or15_range_over_normal": _ratio(or_range, normal),
        "session_open_to_0914_displacement": net,
        "session_open_to_0914_over_normal": _ratio(net, normal),
        "opposite_or_extreme_to_0914_displacement": session_disp_from_opp,
        "one_bar_range_share": one_bar,
        "mean_body_over_range": best.get("mean_body_over_range"),
        "max_range_over_normal": best.get("max_range_over_normal_5m"),
        "sequence": best.get("sequence"),
        "DIR_scored": sign_guess if best is seq else -sign_guess,
        "close_to_close_same_sign_n": int(sum(1 for p in (p12, p23) if _finite(p) and ((float(p) > 0 and sign_guess > 0) or (float(p) < 0 and sign_guess < 0)))),
    }


def true_clauses(seq: dict[str, Any]) -> dict[str, Any]:
    disp = seq.get("net_directional_displacement_over_normal")
    mx = seq.get("max_range_over_normal")
    n_same = int(seq.get("n_same_direction_bodies") or 0)
    body = seq.get("mean_body_over_range")
    ctr = seq.get("max_counter_over_normal")
    clauses = {
        "disp_ge_TRUE_DISP_MIN": bool(_finite(disp) and float(disp) >= float(TRUE_DISP_MIN)),
        "max_range_ge_TRUE_RANGE_MIN": bool(_finite(mx) and float(mx) >= float(TRUE_RANGE_MIN)),
        "n_same_ge_TRUE_N_SAME_MIN": n_same >= int(TRUE_N_SAME_MIN),
        "body_ge_TRUE_BODY_FRAC_MIN": bool(_finite(body) and float(body) >= float(TRUE_BODY_FRAC_MIN)),
        "counter_le_TRUE_COUNTER_FRAC_times_disp": bool(
            (not _finite(ctr) or float(ctr) <= float(TRUE_COUNTER_FRAC) * float(disp)) if _finite(disp) else False
        ),
    }
    clauses["all_true_clauses"] = all(clauses.values())
    passing = [k for k, v in clauses.items() if v and k != "all_true_clauses"]
    return {**clauses, "passing_clause_ids": passing}


def _five_m_hold(*, sign: int, bar: dict[str, Any], level: float) -> bool:
    h, l, c = bar.get("h"), bar.get("l"), bar.get("c")
    if not (_finite(h) and _finite(l) and _finite(c)):
        return False
    tests = float(l) <= float(level) <= float(h)
    holds = float(c) > float(level) if int(sign) > 0 else float(c) < float(level)
    loc = close_loc(float(h), float(l), float(c))
    loc_ok = loc is not None and float(loc) >= 0.50
    return bool(tests and holds and loc_ok)


def _timeline(bars: list[dict[str, Any]], *, sign: int, or_high: float, or_low: float) -> list[dict[str, Any]]:
    out = []
    left = False
    for b in bars:
        fully = five_m_fully_beyond(sign=sign, bar=b, or_high=or_high, or_low=or_low)
        if fully:
            left = True
        level = float(or_high) if int(sign) > 0 else float(or_low)
        out.append(
            {
                "t0": b.get("t0"),
                "t1": b.get("t1"),
                "o": b.get("o"),
                "h": b.get("h"),
                "l": b.get("l"),
                "c": b.get("c"),
                "direction": b.get("direction"),
                "body_over_range": b.get("body_over_range"),
                "close_loc": b.get("close_loc"),
                "fully_beyond_or": fully,
                "tests_or": float(b.get("l") or 0) <= level <= float(b.get("h") or 0) if _finite(b.get("l")) and _finite(b.get("h")) else False,
                "completed_5m_hold_or": _five_m_hold(sign=sign, bar=b, level=level),
                "left_so_far": left,
            }
        )
    return out


def materialize_88(bind: dict[str, Any], *, audit_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rca_path = RCA_CACHE / "descriptor_slim.json"
    rca_rows = json.loads(rca_path.read_text(encoding="utf-8")) if rca_path.is_file() else []
    events: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rca_rows:
        events[(str(r.get("symbol")), str(r.get("date")))] = r
    aidx = {(str(r.get("symbol")), str(r.get("date"))): r for r in audit_rows}
    needed = {s for s, _d in events}
    loaded = _load_panel(bind)
    if not loaded.get("ok"):
        return loaded
    minutes = loaded["minutes"]
    disc = loaded["disc"]
    grouped = {d: g for d, g in minutes.groupby("date", sort=False)}
    hist: dict[str, list] = defaultdict(list)
    first5: dict[str, deque] = defaultdict(lambda: deque(maxlen=int(NOISE_LOOKBACK_SESSIONS)))
    first5_dated: dict[str, deque] = defaultdict(lambda: deque(maxlen=int(NOISE_LOOKBACK_SESSIONS)))
    rows: list[dict[str, Any]] = []
    timelines: dict[str, Any] = {}
    print(f"RCA_MATERIALIZE events={len(events)} symbols={len(needed)} days={len(disc)}", flush=True)
    for di, date in enumerate(disc):
        gdate = grouped.get(date)
        if gdate is None:
            continue
        by_sym = {str(s): sg for s, sg in gdate.groupby("symbol", sort=False)}
        for symbol in needed:
            sg = by_sym.get(symbol)
            if sg is None or sg.empty:
                continue
            rec = prep_symbol(sg)
            rec["session_idx"] = session_idx_of(rec["t"])
            session_idx = rec["session_idx"]
            bars_all = build_five_m(rec, session_idx, through="11:19")
            open_bars = [b for b in bars_all if (b.get("t0"), b.get("t1")) in WINDOWS]
            if len(open_bars) < 3:
                open_bars = bars_all[:3]
            prior = hist[symbol]
            atr = atr20(prior) if prior else float("nan")
            px = float(open_bars[0]["o"]) if open_bars and _finite(open_bars[0].get("o")) else float("nan")
            obs = list(first5[symbol])
            dated = list(first5_dated[symbol])
            normal = _median(obs)
            key = (symbol, str(date))
            if key in events:
                ev = events[key]
                rid = int(ev.get("rca_id") or 0)
                lab = dict(HUMAN_LABELS.get(rid) or {})
                mach = dict(aidx.get(key) or {})
                or15 = freeze_or15(rec["t"], rec["h"], rec["l"], session_idx)
                or_high = float(or15["or_high"]) if or15.get("ok") else float("nan")
                or_low = float(or15["or_low"]) if or15.get("ok") else float("nan")
                seq = opening_sequence(open_bars[:3], normal=normal, or_high=or_high, or_low=or_low)
                s1 = classify_s1(open_bars[:3], normal_opening_5m=normal, atr20=atr)
                clauses = true_clauses(seq)
                ratios = [float(x) for x in obs]
                split_flag = bool(ratios) and (max(ratios) / min(ratios) >= 4.0) if ratios and min(ratios) > 0 else False
                early = ratios[:5] if len(ratios) >= 8 else ratios
                late = ratios[-3:] if len(ratios) >= 8 else ratios
                regime = None
                if early and late:
                    me, ml = sum(early) / len(early), sum(late) / len(late)
                    regime = _ratio(ml, me)
                current_first = open_bars[0].get("range") if open_bars else None
                current_max = max((float(b["range"]) for b in open_bars[:3] if _finite(b.get("range"))), default=None)
                h_s1 = str(lab.get("sp_opening_state") or "") in VALID_OPENING_STATES
                m_s1 = bool(mach.get("machine_S1"))
                row = {
                    "rca_id": rid,
                    "symbol": symbol,
                    "date": date,
                    "direction_v32": ev.get("direction"),
                    "human_pattern": lab.get("sp_pattern"),
                    "human_opening_state": lab.get("sp_opening_state"),
                    "human_location": lab.get("sp_location"),
                    "human_location_kind": lab.get("sp_location_kind"),
                    "human_retest": lab.get("sp_retest"),
                    "human_trigger": lab.get("sp_trigger"),
                    "human_note": lab.get("sp_note"),
                    "human_s1_valid": h_s1,
                    "machine_S0": mach.get("machine_S0"),
                    "machine_S1": m_s1,
                    "machine_S2": mach.get("machine_S2"),
                    "machine_S3": mach.get("machine_S3"),
                    "machine_S4": mach.get("machine_S4"),
                    "machine_E0": mach.get("machine_E0"),
                    "machine_E1": mach.get("machine_E1"),
                    "machine_opening_state": mach.get("machine_opening_state"),
                    "machine_death": mach.get("machine_death"),
                    "location_family": mach.get("location_family"),
                    "location_reason": mach.get("location_reason"),
                    "s1_fp": (not h_s1) and m_s1,
                    "s1_fn": h_s1 and (not m_s1),
                    "s1_live_ok": s1_pass(s1),
                    "s1_live_state": s1.get("state"),
                    "bars": [_bar_slim(b, normal=normal) for b in open_bars[:3]],
                    "sequence": seq,
                    "true_clauses": clauses,
                    "baseline": {
                        "observation_n": len(obs),
                        "baseline_dates": [d for d, _r in dated],
                        "median_range": normal,
                        "current_first5_over_baseline": _ratio(current_first, normal),
                        "current_max3_over_baseline": _ratio(current_max, normal),
                        "price_level": px,
                        "atr20": atr if _finite(atr) else None,
                        "insufficient_prior_obs": len(obs) < int(OPENING_5M_MIN_OBS),
                        "split_or_level_jump_flag": split_flag,
                        "late_vs_early_mean_range_ratio": regime,
                        "baseline_is_first_5m_only": True,
                    },
                    "or_high": or_high if _finite(or_high) else None,
                    "or_low": or_low if _finite(or_low) else None,
                    "s1_reason": s1.get("reason"),
                }
                rows.append(row)
                need_tl = key in {
                    ("3382", "20241004"),
                    ("7011", "20250523"),
                    ("4063", "20251118"),
                    ("7011", "20241205"),
                    ("6920", "20250404"),
                    ("9501", "20251113"),
                } or str(lab.get("sp_opening_state") or "") == "FAILED_OPEN_THEN_REAL_DRIVE" or str(lab.get("sp_pattern") or "") == "CLEAR_CONTINUATION"
                if need_tl:
                    sign = int(mach.get("machine_DIR") or 0)
                    if sign not in (1, -1):
                        sign = 1 if str(ev.get("direction") or "") == "bull" else -1
                    timelines[f"{symbol}|{date}"] = {
                        "symbol": symbol,
                        "date": date,
                        "sign": sign,
                        "or_high": or_high if _finite(or_high) else None,
                        "or_low": or_low if _finite(or_low) else None,
                        "five_m": _timeline(bars_all, sign=sign, or_high=or_high, or_low=or_low) if _finite(or_high) else [],
                        "any_completed_5m_leave": any(x.get("fully_beyond_or") for x in (_timeline(bars_all, sign=sign, or_high=or_high, or_low=or_low) if _finite(or_high) else [])),
                        "any_completed_5m_or_hold_after_leave": False,
                    }
                    tl = timelines[f"{symbol}|{date}"]["five_m"]
                    left = False
                    hold_after = False
                    for x in tl:
                        if x.get("fully_beyond_or"):
                            left = True
                        if left and x.get("completed_5m_hold_or"):
                            hold_after = True
                    timelines[f"{symbol}|{date}"]["any_completed_5m_leave"] = left
                    timelines[f"{symbol}|{date}"]["any_completed_5m_or_hold_after_leave"] = hold_after
            if open_bars and _finite(open_bars[0].get("range")) and float(open_bars[0]["range"]) > 0:
                first5[symbol].append(float(open_bars[0]["range"]))
                first5_dated[symbol].append((str(date), float(open_bars[0]["range"])))
            day = daily_from_minutes(rec, date)
            if day:
                hist[symbol].append(day)
        if di % 50 == 0:
            print(f"RCA_WALK {di}/{len(disc)} rows={len(rows)}", flush=True)
    rows.sort(key=lambda r: int(r.get("rca_id") or 0))
    return {"ok": True, "n": len(rows), "rows": rows, "timelines": timelines, "future_outcome_n": 0}
