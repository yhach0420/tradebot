"""Causal descriptors through trigger only. No outcome. No threshold search."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.daily import daily_from_minutes
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.pb1_opening_range_continuation_face_valid_v1.or15 import freeze_or15, session_idx_of
from research.pb1_opening_range_continuation_face_valid_v2.charts import _sma
from research.pb1_opening_range_continuation_face_valid_v2.inplay import daily_bias
from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite, close_loc
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.reaccel import bar_body, dir_close_loc, opposite_wick

FIVE_WINDOWS = (("09:00", "09:04"), ("09:05", "09:09"), ("09:10", "09:14"))
NORMAL_LOOKBACK = 20
NORMAL_MIN_OBS = 8


def _ratio(num: Any, den: Any) -> float | None:
    if not (_finite(num) and _finite(den) and float(den) > 0):
        return None
    return float(num) / float(den)


def _median(xs: list[float]) -> float | None:
    vals = [float(x) for x in xs if _finite(x)]
    if not vals:
        return None
    vals.sort()
    mid = len(vals) // 2
    if len(vals) % 2:
        return float(vals[mid])
    return float((vals[mid - 1] + vals[mid]) / 2.0)


def _pctl(hist: list[float], value: Any) -> float | None:
    vals = [float(x) for x in hist if _finite(x)]
    if len(vals) < int(NORMAL_MIN_OBS) or not _finite(value):
        return None
    k = int(sum(1 for x in vals if x <= float(value)))
    return float(k / len(vals))


def _bar_at(rec: dict[str, Any], t: str) -> dict[str, Any] | None:
    idx = rec.get("idx") or {}
    i = idx.get(str(t)[:5])
    if i is None:
        return None
    return {
        "i": int(i),
        "t": str(rec["t"][i])[:5],
        "o": rec["o"][i],
        "h": rec["h"][i],
        "l": rec["l"][i],
        "c": rec["c"][i],
        "va": rec["va"][i],
        "vw": rec["vw"][i],
    }


def _agg_window(rec: dict[str, Any], session_idx: list[int], t0: str, t1: str) -> dict[str, Any] | None:
    o = h = l = c = None
    tv = 0.0
    n = 0
    for i in session_idx:
        t = str(rec["t"][i])[:5]
        if t < t0 or t > t1:
            continue
        oi, hi, li, ci = rec["o"][i], rec["h"][i], rec["l"][i], rec["c"][i]
        if not (_finite(oi) and _finite(hi) and _finite(li) and _finite(ci)):
            continue
        if o is None:
            o = float(oi)
            h = float(hi)
            l = float(li)
        else:
            h = max(float(h), float(hi))
            l = min(float(l), float(li))
        c = float(ci)
        if _finite(rec["va"][i]):
            tv += float(rec["va"][i])
        n += 1
    if o is None or h is None or l is None or c is None or n <= 0:
        return None
    rng = float(h) - float(l)
    body = abs(float(c) - float(o))
    up_wick = float(h) - max(float(o), float(c))
    dn_wick = min(float(o), float(c)) - float(l)
    loc = close_loc(h, l, c)
    direction = 1 if float(c) > float(o) else (-1 if float(c) < float(o) else 0)
    return {
        "t0": t0,
        "t1": t1,
        "o": float(o),
        "h": float(h),
        "l": float(l),
        "c": float(c),
        "range": rng,
        "body": body,
        "body_over_range": _ratio(body, rng),
        "close_loc": loc,
        "upper_wick": up_wick,
        "lower_wick": dn_wick,
        "direction": direction,
        "net": float(c) - float(o),
        "tv": float(tv),
        "bar_n": n,
    }


def _five_sequence(bars: list[dict[str, Any]], *, sign: int, normal_range: Any) -> dict[str, Any]:
    dirs = [int(b.get("direction") or 0) for b in bars]
    n_same = int(sum(1 for d in dirs if d == int(sign)))
    n_opp = int(sum(1 for d in dirs if d == -int(sign) and d != 0))
    nets = [float(b["net"]) for b in bars if _finite(b.get("net"))]
    net = float(sum(nets)) if nets else None
    dir_disp = (float(net) * float(sign)) if net is not None else None
    opp_mags = [abs(float(b["net"])) for b in bars if int(b.get("direction") or 0) == -int(sign)]
    largest_counter = max(opp_mags) if opp_mags else 0.0
    if n_opp == 0 and n_same >= 2:
        seq = "same-direction"
    elif dirs and dirs[0] == -int(sign) and n_same >= 1 and int(sign) != 0:
        seq = "reversal"
    elif n_same and n_opp:
        seq = "two-sided"
    else:
        seq = "two-sided" if n_opp and n_same == 0 else "same-direction"
    ranges = [float(b["range"]) for b in bars if _finite(b.get("range"))]
    bodies = [float(b["body"]) for b in bars if _finite(b.get("body"))]
    return {
        "n_same_dir_5m": n_same,
        "n_counter_5m": n_opp,
        "net_directional_displacement": dir_disp,
        "net_displacement_over_normal_5m": _ratio(dir_disp, normal_range),
        "largest_counter_5m": largest_counter,
        "largest_counter_over_normal_5m": _ratio(largest_counter, normal_range),
        "sequence": seq,
        "mean_body_over_range": _ratio(sum(bodies), sum(ranges)) if ranges and sum(ranges) > 0 else None,
        "max_range_over_normal_5m": _ratio(max(ranges) if ranges else None, normal_range),
    }


def _opening_drive_family(seq: dict[str, Any], bars: list[dict[str, Any]], *, sign: int, or_h: Any, or_l: Any) -> str:
    """Descriptor family label only. Not a gate and not searched."""
    if not bars or len(bars) < 3:
        return "incomplete_5m"
    disp = seq.get("net_displacement_over_normal_5m")
    mx = seq.get("max_range_over_normal_5m")
    sequence = str(seq.get("sequence") or "")
    tiny = (disp is None or abs(float(disp)) < 0.35) and (mx is None or float(mx) < 0.60)
    if tiny:
        return "flat_or_micro"
    if sequence == "two-sided" and int(seq.get("n_counter_5m") or 0) >= 1 and int(seq.get("n_same_dir_5m") or 0) >= 1:
        first = int(bars[0].get("direction") or 0)
        last = int(bars[-1].get("direction") or 0)
        if first == -int(sign) and last == int(sign) and disp is not None and float(disp) >= 0.80:
            return "failed_open_then_drive_shape"
        return "two_sided_shape"
    if sequence == "reversal" and disp is not None and float(disp) >= 0.80:
        return "failed_open_then_drive_shape"
    if sequence == "same-direction" and disp is not None and float(disp) >= 0.80:
        return "true_drive_shape"
    return "weak_directional_shape"


def _reaccel_desc(rec: dict[str, Any], ev: dict[str, Any], n1m: Any, clock: ClockHistory) -> dict[str, Any]:
    trig = _bar_at(rec, str(ev.get("trigger_t") or ""))
    if trig is None:
        return {"ok": False, "reason": "no_trigger_bar"}
    i = int(trig["i"])
    sign = int(ev.get("DIR") or 0)
    o, h, l, c = trig["o"], trig["h"], trig["l"], trig["c"]
    rng = (float(h) - float(l)) if _finite(h) and _finite(l) else None
    body = bar_body(float(o), float(c)) if _finite(o) and _finite(c) else None
    wick = opposite_wick(sign=sign, open_px=float(o) if _finite(o) else float(c), high=float(h), low=float(l), close=float(c)) if _finite(h) and _finite(l) and _finite(c) else None
    dloc = dir_close_loc(sign=sign, high=float(h), low=float(l), close=float(c)) if _finite(h) and _finite(l) and _finite(c) else None
    prior3_r: list[float] = []
    prior3_b: list[float] = []
    dir_closes = 0
    for k in range(max(0, i - 3), i):
        if not (_finite(rec["h"][k]) and _finite(rec["l"][k]) and _finite(rec["o"][k]) and _finite(rec["c"][k])):
            continue
        prior3_r.append(float(rec["h"][k]) - float(rec["l"][k]))
        prior3_b.append(abs(float(rec["c"][k]) - float(rec["o"][k])))
    for k in range(max(0, i - 2), i + 1):
        if _finite(rec["c"][k]) and _finite(rec["o"][k]):
            if (float(rec["c"][k]) - float(rec["o"][k])) * float(sign) > 0:
                dir_closes += 1
    med_r = _median(prior3_r)
    med_b = _median(prior3_b)
    two_net = None
    if i >= 1 and _finite(rec["c"][i]) and _finite(rec["c"][i - 1]):
        two_net = (float(rec["c"][i]) - float(rec["c"][i - 1])) * float(sign)
    three_net = None
    if i >= 2 and _finite(rec["c"][i]) and _finite(rec["c"][i - 2]):
        three_net = (float(rec["c"][i]) - float(rec["c"][i - 2])) * float(sign)
    retest_ext = ev.get("retest_high") if sign > 0 else ev.get("retest_low")
    if sign > 0:
        disp = (float(c) - float(retest_ext)) if _finite(c) and _finite(retest_ext) else None
    else:
        disp = (float(retest_ext) - float(c)) if _finite(c) and _finite(retest_ext) else None
    rt = _bar_at(rec, str(ev.get("retest_t") or ""))
    tv_retest = rt.get("va") if rt else None
    tv_trig = trig.get("va")
    tv_ratio = _ratio(tv_trig, tv_retest)
    t = str(trig["t"])
    return {
        "trigger_range_over_NORMAL_1M_RANGE": _ratio(rng, n1m),
        "trigger_body_over_NORMAL_1M_RANGE": _ratio(body, n1m),
        "trigger_body_over_range": _ratio(body, rng),
        "close_location": dloc,
        "opposite_wick": wick,
        "opposite_wick_over_range": _ratio(wick, rng),
        "current_range_over_median_prior3_range": _ratio(rng, med_r),
        "current_body_over_median_prior3_body": _ratio(body, med_b),
        "two_bar_dir_net_over_NORMAL_1M_RANGE": _ratio(two_net, n1m),
        "three_bar_dir_net_over_NORMAL_1M_RANGE": _ratio(three_net, n1m),
        "n_directional_closes_last3": dir_closes,
        "retest_extreme_to_trigger_close_over_NORMAL_1M_RANGE": _ratio(disp, n1m),
        "tv_trigger": float(tv_trig) if _finite(tv_trig) else None,
        "tv_retest": float(tv_retest) if _finite(tv_retest) else None,
        "tv_trigger_over_retest": tv_ratio,
        "tv_trigger_same_clock_pctl": clock.tv_pctl(str(ev.get("symbol")), t, tv_trig),
        "expand_vs_retest_machine": ev.get("expand_vs_retest"),
    }


def _location_desc(ev: dict[str, Any], rec: dict[str, Any], trig: dict[str, Any] | None) -> dict[str, Any]:
    px = trig.get("c") if trig else None
    or_h, or_l = ev.get("or_high"), ev.get("or_low")
    pdh, pdl, pdc = ev.get("pdh"), ev.get("pdl"), ev.get("pdc")
    sma25, sma75 = ev.get("sma25"), ev.get("sma75")
    vw = trig.get("vw") if trig else None
    n1m = ev.get("NORMAL_1M_RANGE") or ev.get("median_1m_range_tod")
    near = ev.get("nearest_opposing") if isinstance(ev.get("nearest_opposing"), dict) else {}
    zone_mid = near.get("zone_mid")
    if zone_mid is None and _finite(near.get("zone_low")) and _finite(near.get("zone_high")):
        zone_mid = (float(near["zone_low"]) + float(near["zone_high"])) / 2.0
    def _dist(level: Any) -> float | None:
        if not (_finite(px) and _finite(level)):
            return None
        return abs(float(px) - float(level))

    or_level = or_h if int(ev.get("DIR") or 0) > 0 else or_l
    or_dist = _dist(or_level)
    return {
        "machine_defended_level_type": ev.get("defended_level_type"),
        "machine_zone_class": ev.get("zone_class"),
        "or_boundary_dist_over_n1m": _ratio(or_dist, n1m),
        "pdh_dist_over_n1m": _ratio(_dist(pdh), n1m),
        "pdl_dist_over_n1m": _ratio(_dist(pdl), n1m),
        "pdc_dist_over_n1m": _ratio(_dist(pdc), n1m),
        "vwap_dist_over_n1m": _ratio(_dist(vw), n1m),
        "sma25_dist_over_n1m": _ratio(_dist(sma25), n1m),
        "sma75_dist_over_n1m": _ratio(_dist(sma75), n1m),
        "prior_sr_mid_dist_over_n1m": _ratio(_dist(zone_mid), n1m),
        "or_x_pdh": bool(_finite(or_dist) and _finite(_dist(pdh)) and float(or_dist) <= 1.5 * float(n1m or 0) and float(_dist(pdh) or 99) <= 1.5 * float(n1m or 0)) if _finite(n1m) else False,
        "or_x_pdl": bool(_finite(or_dist) and _finite(_dist(pdl)) and _finite(n1m) and float(or_dist) <= 1.5 * float(n1m) and float(_dist(pdl) or 99) <= 1.5 * float(n1m)),
        "or_x_vwap": bool(_finite(or_dist) and _finite(_dist(vw)) and _finite(n1m) and float(or_dist) <= 1.5 * float(n1m) and float(_dist(vw) or 99) <= 1.5 * float(n1m)),
        "or_x_sma25": bool(_finite(or_dist) and _finite(_dist(sma25)) and _finite(n1m) and float(or_dist) <= 1.5 * float(n1m) and float(_dist(sma25) or 99) <= 1.5 * float(n1m)),
        "or_x_prior_sr": bool(_finite(or_dist) and _finite(_dist(zone_mid)) and _finite(n1m) and float(or_dist) <= 1.5 * float(n1m) and float(_dist(zone_mid) or 99) <= 1.5 * float(n1m)),
        "confluence_score_not_created": True,
    }


def _stale_desc(rec: dict[str, Any], ev: dict[str, Any], session_idx: list[int]) -> dict[str, Any]:
    pos = int(ev["trigger_pos"]) if _finite(ev.get("trigger_pos")) else None
    if pos is None:
        trig = _bar_at(rec, str(ev.get("trigger_t") or ""))
        pos = int(trig["i"]) if trig else None
    if pos is None:
        return {}
    or_h, or_l = ev.get("or_high"), ev.get("or_low")
    if not (_finite(or_h) and _finite(or_l) and float(or_h) > float(or_l)):
        return {}
    mid = (float(or_h) + float(or_l)) / 2.0
    sign = int(ev.get("DIR") or 0)
    recross = 0
    last_side = None
    through_mid = 0
    failed_breaks = 0
    last_exp = None
    no_exp_n = 0
    for i in session_idx:
        if i > pos:
            break
        t = str(rec["t"][i])[:5]
        if t < "09:15":
            continue
        c = rec["c"][i]
        if not _finite(c):
            continue
        side = 1 if float(c) > float(or_h) else (-1 if float(c) < float(or_l) else 0)
        if last_side in (1, -1) and side in (1, -1) and side != last_side:
            recross += 1
        if last_side is not None and ((float(c) - mid) * (1 if last_side > 0 else -1) < 0):
            through_mid += 1
        if side != 0:
            last_side = side
        if _finite(rec["h"][i]) and _finite(rec["l"][i]):
            rng = float(rec["h"][i]) - float(rec["l"][i])
            if last_exp is not None and rng <= last_exp:
                no_exp_n += 1
            else:
                no_exp_n = 0
            last_exp = rng
        if sign > 0 and _finite(rec["h"][i]) and float(rec["h"][i]) > float(or_h) and _finite(c) and float(c) <= float(or_h):
            failed_breaks += 1
        if sign < 0 and _finite(rec["l"][i]) and float(rec["l"][i]) < float(or_l) and _finite(c) and float(c) >= float(or_l):
            failed_breaks += 1
    open_px = rec["o"][session_idx[0]] if session_idx else None
    c_trig = rec["c"][pos] if pos < len(rec["c"]) else None
    retraced = None
    if _finite(open_px) and _finite(c_trig):
        retraced = ((float(c_trig) - float(open_px)) * float(sign)) <= 0
    return {
        "or_recross_n": recross,
        "closes_through_or_mid_n": through_mid,
        "failed_break_attempts_n": failed_breaks,
        "consecutive_no_expansion_1m": no_exp_n,
        "opening_direction_fully_retraced": retraced,
        "time_rule_not_created": True,
    }


def _htf_desc(prior_days: list[dict[str, Any]], ev: dict[str, Any], today: dict[str, Any]) -> dict[str, Any]:
    closes = [float(d["close"]) for d in prior_days if _finite(d.get("close"))]
    px = today.get("close")
    sma5 = _sma(closes, 5) if len(closes) >= 5 else None
    sma25 = _sma(closes, 25) if len(closes) >= 25 else ev.get("sma25")
    sma75 = _sma(closes, 75) if len(closes) >= 75 else ev.get("sma75")
    if _finite(px):
        if len(closes) >= 4:
            sma5 = _sma(closes[-4:] + [float(px)], 5)
        sma25 = _sma(closes + [float(px)], 25) if len(closes) >= 24 else sma25
        sma75 = _sma(closes + [float(px)], 75) if len(closes) >= 74 else sma75
    bias = daily_bias(sma5, sma25, sma75)
    atr = ev.get("atr20")
    recent = closes[-5:] if len(closes) >= 5 else closes
    ext = None
    if len(recent) >= 2:
        ext = abs(float(recent[-1]) - float(recent[0]))
    sign = int(ev.get("DIR") or 0)
    conflict = False
    if bias == "bull_aligned" and sign < 0:
        conflict = True
    if bias == "bear_aligned" and sign > 0:
        conflict = True
    return {
        "daily_sma5": sma5,
        "daily_sma25": sma25,
        "daily_sma75": sma75,
        "daily_bias": bias or ev.get("daily_bias"),
        "dist_sma25_over_atr": _ratio(abs(float(px) - float(sma25)) if _finite(px) and _finite(sma25) else None, atr),
        "dist_sma75_over_atr": _ratio(abs(float(px) - float(sma75)) if _finite(px) and _finite(sma75) else None, atr),
        "pdh_position": (
            "above_pdh"
            if _finite(px) and _finite(ev.get("pdh")) and float(px) > float(ev["pdh"])
            else ("below_pdl" if _finite(px) and _finite(ev.get("pdl")) and float(px) < float(ev["pdl"]) else "inside_pdh_pdl")
        ),
        "recent_daily_net": (float(recent[-1]) - float(recent[0])) if len(recent) >= 2 else None,
        "recent_daily_extension": ext,
        "htf_conflict_with_setup_dir": conflict,
        "daily_bias_gate_not_created": True,
    }


def compute_descriptors(bind: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    split = dict(bind.get("split") or {})
    disc = [str(d) for d in list(split.get("discovery_dates") or [])]
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    symbols = sorted({str(e["symbol"]) for e in events})
    minutes = load_minutes(symbols=symbols, allowed_dates=set(disc), forbidden_dates=conf | val)
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    recs: dict[tuple[str, str], dict[str, Any]] = {}
    days: dict[str, list[dict[str, Any]]] = defaultdict(list)
    clock = ClockHistory()
    five_hist: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    range_hist: dict[str, list[float]] = defaultdict(list)
    by_date: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for (sym, dt), sg in minutes.groupby(["symbol", "date"], sort=True):
        rec = prep_symbol(sg)
        rec["session_idx"] = session_idx_of(rec["t"])
        recs[(str(sym), str(dt))] = rec
        day = daily_from_minutes(rec, str(dt))
        if day:
            days[str(sym)].append(day)
        by_date[str(dt)].append((str(sym), rec))
    events_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in events:
        events_by_date[str(ev["date"])].append(ev)
    computed: dict[int, dict[str, Any]] = {}
    for date in disc:
        packed = by_date.get(str(date)) or []
        for ev0 in events_by_date.get(str(date)) or []:
            ev = dict(ev0)
            rec = recs.get((str(ev["symbol"]), str(ev["date"])))
            if rec is None:
                computed[int(ev["rca_id"])] = {**ev, "descriptors_ok": False, "reason": "minutes_missing"}
                continue
            session_idx = rec["session_idx"]
            if not _finite(ev.get("trigger_pos")):
                tb = _bar_at(rec, str(ev.get("trigger_t") or ""))
                if tb:
                    ev["trigger_pos"] = int(tb["i"])
            pos = int(ev["trigger_pos"]) if _finite(ev.get("trigger_pos")) else (max(session_idx) if session_idx else 0)
            sign = int(ev.get("DIR") or 0)
            or15 = freeze_or15(rec["t"], rec["h"], rec["l"], session_idx)
            if not _finite(ev.get("or_high")) and or15.get("ok"):
                ev["or_high"] = or15.get("or_high")
                ev["or_low"] = or15.get("or_low")
            n1m = ev.get("NORMAL_1M_RANGE") or ev.get("median_1m_range_tod")
            prior_ranges = list(range_hist.get(str(ev["symbol"])) or [])
            normal_5m = _median(prior_ranges[-NORMAL_LOOKBACK:])
            five_bars: list[dict[str, Any]] = []
            five_tv: list[dict[str, Any]] = []
            for t0, t1 in FIVE_WINDOWS:
                b = _agg_window(rec, session_idx, t0, t1)
                if b is None:
                    continue
                hist_tv = list(five_hist[str(ev["symbol"])][f"{t0}_{t1}"] or [])
                b["range_over_normal_opening_5m"] = _ratio(b.get("range"), normal_5m)
                b["tv_vs_same_clock_pctl"] = _pctl(hist_tv[-NORMAL_LOOKBACK:], b.get("tv"))
                b["tv_vs_same_clock_ratio"] = _ratio(b.get("tv"), _median(hist_tv[-NORMAL_LOOKBACK:]))
                five_bars.append(b)
                five_tv.append({"window": f"{t0}_{t1}", "tv": b.get("tv"), "pctl": b.get("tv_vs_same_clock_pctl")})
            seq = _five_sequence(five_bars, sign=sign, normal_range=normal_5m)
            family = _opening_drive_family(seq, five_bars, sign=sign, or_h=ev.get("or_high"), or_l=ev.get("or_low"))
            trig = _bar_at(rec, str(ev.get("trigger_t") or ""))
            prior_days = [d for d in days[str(ev["symbol"])] if str(d["date"]) < str(ev["date"])]
            o = [rec["o"][i] for i in session_idx if i <= pos]
            h = [rec["h"][i] for i in session_idx if i <= pos]
            l = [rec["l"][i] for i in session_idx if i <= pos]
            c = [rec["c"][i] for i in session_idx if i <= pos]
            today = {
                "open": o[0] if o else None,
                "high": max((float(x) for x in h if _finite(x)), default=None),
                "low": min((float(x) for x in l if _finite(x)), default=None),
                "close": c[-1] if c else None,
            }
            computed[int(ev["rca_id"])] = {
                **ev,
                "descriptors_ok": True,
                "future_hidden": True,
                "normal_opening_5m_range": normal_5m,
                "five_m_bars": five_bars,
                "five_m_tv": five_tv,
                "opening_5m": seq,
                "opening_descriptor_family": family,
                "or_close_loc_machine": ev.get("or_close_loc"),
                "reaccel": _reaccel_desc(rec, ev, n1m, clock),
                "location_desc": _location_desc(ev, rec, trig),
                "stale_desc": _stale_desc(rec, ev, session_idx),
                "htf": _htf_desc(prior_days, ev, today),
                "in_play": ev.get("in_play"),
                "in_play_reason": ev.get("in_play_reason"),
                "abs_gap_atr": ev.get("abs_gap_atr"),
                "tv_0915": ev.get("tv_0915"),
                "tv_0915_pctl": ev.get("tv_0915_pctl"),
                "xs_rank_pct": ev.get("xs_rank_pct"),
                "threshold_not_searched": True,
            }
        for symbol, rec in packed:
            session_idx = rec["session_idx"]
            clock.commit_day(symbol, rec, session_idx)
            b0 = _agg_window(rec, session_idx, "09:00", "09:04")
            if b0 and _finite(b0.get("range")):
                range_hist[symbol].append(float(b0["range"]))
                if len(range_hist[symbol]) > 60:
                    del range_hist[symbol][0]
            for t0, t1 in FIVE_WINDOWS:
                b = _agg_window(rec, session_idx, t0, t1)
                if b and _finite(b.get("tv")):
                    xs = five_hist[symbol][f"{t0}_{t1}"]
                    xs.append(float(b["tv"]))
                    if len(xs) > 60:
                        del xs[0]
    out: list[dict[str, Any]] = []
    for ev in events:
        rid = int(ev["rca_id"])
        out.append(computed.get(rid) or {**ev, "descriptors_ok": False, "reason": "not_computed"})
    return out
