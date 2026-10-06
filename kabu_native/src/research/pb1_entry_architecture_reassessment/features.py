"""Causal ENTRY-time features. All values use bars completed at or before signal_t."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.bars import build_nm, enrich_1m, open_0900, or_levels
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.levels import restore_level
from research.pb1_v4_clarified_machine_correction_v4.active import classify_progress
from research.pb1_v4_clarified_machine_correction_v4.encoding import RCA_COMPARABLE_OPPOSITE_BODY
from research.pb1_v4_clarified_machine_correction_v4.location import five_m_left
from research.pb1_v4_complete_strategy_build_and_economic_validation.clocks import hhmm_to_min
from research.pb1_v4_complete_strategy_economic_failure_decomposition.path import dir_bps, parse_identity


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f and abs(f) < 1e15


def _sign(side: str) -> int:
    return 1 if str(side).lower() in {"bull", "long", "1"} else -1


def _mins(a: str | None, b: str | None) -> int | None:
    ma = hhmm_to_min(str(a or "")[:5])
    mb = hhmm_to_min(str(b or "")[:5])
    if ma is None or mb is None:
        return None
    d = int(mb) - int(ma)
    if str(a) < "11:30" and str(b) >= "12:30":
        d -= 60
    return d


def signal_t_of(entry_t: str) -> str:
    prev = hhmm_add(str(entry_t)[:5], -1)
    return str(prev or entry_t)[:5]


def _idx_at_or_before(times: list[str], t: str) -> int | None:
    last = None
    for i, x in enumerate(times):
        if str(x)[:5] <= str(t)[:5]:
            last = i
        else:
            break
    return last


def extract_features(
    trade: dict[str, Any],
    rec: dict[str, Any],
    *,
    prior: list[dict[str, Any]],
    atr: float | None,
) -> dict[str, Any]:
    side = str(trade.get("side") or "")
    sign = _sign(side)
    entry_t = str(trade.get("entry_t") or "")[:5]
    entry_px = float(trade.get("entry_px") or 0)
    sig_t = signal_t_of(entry_t)
    one = enrich_1m(rec)
    times = list(one.get("t") or [])
    i = _idx_at_or_before(times, sig_t)
    orl = or_levels(rec)
    or_h = float(orl["or_high"]) if orl and orl.get("ok") else None
    or_l = float(orl["or_low"]) if orl and orl.get("ok") else None
    opn = open_0900(rec)
    pdc = float(prior[-1]["close"]) if prior and _finite(prior[-1].get("close")) else None
    lvl = restore_level(trade, or_high=or_h, or_low=or_l, prior_dailies=prior, rec=rec)
    level = lvl.get("level")
    ident = parse_identity(str(trade.get("execution_id") or ""))
    loc_t = None
    parts = str(trade.get("execution_id") or "").split("|")
    if len(parts) > 8:
        loc_t = str(parts[8])[:5]
    five = [b for b in build_nm(rec, width=5) if str(b.get("t1") or "")[:5] <= sig_t]
    out: dict[str, Any] = {
        "signal_t": sig_t,
        "entry_allowed_at": entry_t,
        "feature_available_at": sig_t,
        "causal_ok": bool(sig_t < entry_t),
        "atr20": atr,
        "location_family": ident.get("location_family"),
        "location_subtype": ident.get("location_subtype"),
        "level": level,
        "level_source": lvl.get("level_source"),
        "entry_kind": str(trade.get("entry_kind") or ""),
    }
    if i is None or not _finite(entry_px):
        return out
    c = float(one["c"][i]) if _finite(one["c"][i]) else None
    h_ext = None
    l_ext = None
    tv_or = 0.0
    tv_all = 0.0
    vol_sig = 0.0
    vol_prev = 0.0
    for j in range(0, i + 1):
        t = times[j]
        if _finite(one["h"][j]):
            h_ext = float(one["h"][j]) if h_ext is None else max(h_ext, float(one["h"][j]))
        if _finite(one["l"][j]):
            l_ext = float(one["l"][j]) if l_ext is None else min(l_ext, float(one["l"][j]))
        va = rec.get("va") or []
        vo = rec.get("v") or []
        if j < len(va) and _finite(va[j]):
            tv_all += float(va[j])
            if t <= "09:14":
                tv_or += float(va[j])
        if j < len(vo) and _finite(vo[j]):
            if j == i:
                vol_sig = float(vo[j])
            elif j == i - 1:
                vol_prev = float(vo[j])
    vw = float(one["vwap"][i]) if i < len(one["vwap"]) and _finite(one["vwap"][i]) else None
    atr_u = float(atr) if _finite(atr) and float(atr) > 0 else None

    def _atr_n(num: float | None) -> float | None:
        if num is None or atr_u is None:
            return None
        return float(num) / atr_u

    disp = None
    if _finite(opn) and _finite(c):
        disp = (float(c) - float(opn)) * float(sign)
    or_w = (float(or_h) - float(or_l)) if _finite(or_h) and _finite(or_l) else None
    gap = abs(float(opn) - float(pdc)) if _finite(opn) and _finite(pdc) else None
    same = [b for b in five if int(b.get("direction") or 0) == sign]
    opp = [b for b in five if int(b.get("direction") or 0) == -sign]
    bodies = [float(b["body"]) / atr_u for b in opp if _finite(b.get("body")) and atr_u]
    run = 0
    cur = 0
    for b in five:
        if int(b.get("direction") or 0) == sign:
            cur += 1
            run = max(run, cur)
        else:
            cur = 0
    left_t = None
    for b in five:
        if _finite(or_h) and _finite(or_l) and five_m_left(sign=sign, bar=b, or_high=or_h, or_low=or_l):
            left_t = str(b["t1"])[:5]
            break
    or_bound = float(or_h) if sign > 0 and _finite(or_h) else (float(or_l) if sign < 0 and _finite(or_l) else None)
    sess_ext = h_ext if sign > 0 else l_ext
    retrace = None
    if _finite(sess_ext) and _finite(opn) and _finite(c) and abs(float(sess_ext) - float(opn)) > 1e-12:
        retrace = (float(sess_ext) - float(c)) / (float(sess_ext) - float(opn))
        if sign < 0:
            retrace = (float(c) - float(sess_ext)) / (float(opn) - float(sess_ext)) if abs(float(opn) - float(sess_ext)) > 1e-12 else None
    mins_open = _mins("09:00", sig_t)
    mins_leave = _mins(left_t, sig_t) if left_t else None
    loc_age = _mins(loc_t, entry_t) if loc_t else None
    dist_loc = dir_bps(side=side, entry=float(level), px=float(c)) if _finite(level) and _finite(c) else None
    # signed distance in trade direction: positive = beyond location
    if _finite(level) and _finite(c):
        dist_loc = ((float(c) - float(level)) * float(sign)) * 10_000.0 / float(entry_px)
    touch_n = 0
    if _finite(level):
        for b in five:
            if float(b["l"]) <= float(level) <= float(b["h"]):
                touch_n += 1
    c3 = float(one["c"][i - 3]) if i >= 3 and _finite(one["c"][i - 3]) else None
    approach = dir_bps(side=side, entry=float(c3), px=float(c)) if _finite(c3) and _finite(c) else None
    prev_ext = None
    fail_n = 0
    prev_b = None
    for b in five:
        prog = classify_progress(sign=sign, bar=b, prev_bar=prev_b, prev_ext=prev_ext)
        if str(prog.get("class") or "") == "NO_DIRECTIONAL_PROGRESS":
            fail_n += 1
        ext = b.get("h") if sign > 0 else b.get("l")
        if _finite(ext):
            prev_ext = float(ext) if prev_ext is None else (max(float(prev_ext), float(ext)) if sign > 0 else min(float(prev_ext), float(ext)))
        prev_b = b
    counter_n = 0
    for b in five:
        body = b.get("body_over_range")
        if int(b.get("direction") or 0) == -sign and _finite(body) and float(body) >= float(RCA_COMPARABLE_OPPOSITE_BODY):
            counter_n += 1
    early = [float(b["range"]) for b in five[:3] if _finite(b.get("range")) and float(b["range"]) > 0]
    last_r = float(five[-1]["range"]) if five and _finite(five[-1].get("range")) else None
    compress = (last_r / (sum(early) / len(early))) if early and _finite(last_r) else None
    persist = (len(same) / len(five)) if five else None
    o_sig = float(one["o"][i]) if _finite(one["o"][i]) else None
    h_sig = float(one["h"][i]) if _finite(one["h"][i]) else None
    l_sig = float(one["l"][i]) if _finite(one["l"][i]) else None
    body_frac = None
    close_loc = None
    rng = None
    if _finite(o_sig) and _finite(c) and _finite(h_sig) and _finite(l_sig) and float(h_sig) > float(l_sig):
        rng = float(h_sig) - float(l_sig)
        body_frac = abs(float(c) - float(o_sig)) / rng
        close_loc = (float(c) - float(l_sig)) / rng if sign > 0 else (float(h_sig) - float(c)) / rng
    vol_ratio = (vol_sig / vol_prev) if vol_prev > 0 else None
    pace = None
    if mins_open and mins_open > 15 and tv_or > 0:
        pace = (tv_all / float(mins_open)) / (tv_or / 15.0)
    ft = None
    if _finite(or_bound) and _finite(c):
        ft = ((float(c) - float(or_bound)) * float(sign)) * 10_000.0 / float(entry_px)
    dist_ext = None
    if _finite(sess_ext) and _finite(c):
        dist_ext = abs(float(sess_ext) - float(c))
    out.update(
        {
            "opening_disp_atr": _atr_n(disp),
            "or_width_atr": _atr_n(or_w),
            "gap_atr": _atr_n(gap),
            "dominant_5m_frac": (len(same) / len(five)) if five else None,
            "max_counter_body_atr": max(bodies) if bodies else 0.0,
            "followthrough_or_bps": ft,
            "same_dir_run_5m": float(run),
            "minutes_since_or_leave": float(mins_leave) if mins_leave is not None else None,
            "dist_session_extreme_atr": _atr_n(dist_ext),
            "retrace_from_extreme_frac": retrace,
            "dist_vwap_bps": dir_bps(side=side, entry=float(vw), px=float(c)) if _finite(vw) and _finite(c) else None,
            "dist_pdc_bps": dir_bps(side=side, entry=float(pdc), px=float(c)) if _finite(pdc) and _finite(c) else None,
            "tv_pace_vs_or": pace,
            "dist_location_bps": dist_loc,
            "dist_location_atr": _atr_n(abs(float(c) - float(level))) if _finite(level) and _finite(c) else None,
            "location_age_min": float(loc_age) if loc_age is not None else None,
            "level_touch_n": float(touch_n),
            "approach_3m_bps": approach,
            "minutes_from_open": float(mins_open) if mins_open is not None else None,
            "failed_ext_n": float(fail_n),
            "counter_committed_n": float(counter_n),
            "range_compression": compress,
            "dir_persist_frac": persist,
            "confirm_body_frac": body_frac,
            "confirm_close_loc": close_loc,
            "confirm_range_atr": _atr_n(rng),
            "confirm_vol_ratio": vol_ratio,
        }
    )
    # dist_vwap_bps above used entry=vwap which inverts meaning; overwrite signed vs VWAP in trade direction
    if _finite(vw) and _finite(c):
        out["dist_vwap_bps"] = ((float(c) - float(vw)) * float(sign)) * 10_000.0 / float(entry_px)
    if _finite(pdc) and _finite(c):
        out["dist_pdc_bps"] = ((float(c) - float(pdc)) * float(sign)) * 10_000.0 / float(entry_px)
    return out
