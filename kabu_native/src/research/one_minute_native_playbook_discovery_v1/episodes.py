"""Collapse overlapping same-symbol onsets into episodes. Boundaries ignore future outcomes."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, interval_crosses_lunch, parse_hhmm
from research.one_minute_native_playbook_discovery_v1 import EPISODE_GAP_MIN, FAVOR_BPS, PATH_BARS, X1_TAX_BPS


def _mins(hhmm: str) -> int | None:
    p = parse_hhmm(hhmm)
    if p is None:
        return None
    return p[0] * 60 + p[1]


def _bps(exit_px: float, entry_px: float) -> float | None:
    if not np.isfinite(exit_px) or not np.isfinite(entry_px) or entry_px == 0:
        return None
    return float((exit_px / entry_px - 1.0) * 10_000.0)


def _has_before(fams: list[str], earlier: set[str], later: str) -> bool:
    if later not in fams:
        return False
    i = fams.index(later)
    return any(f in earlier for f in fams[:i])


def classify(seq: list[str]) -> str:
    fams = seq
    if _has_before(fams, {"PULLBACK_START"}, "VWAP_RECLAIM"):
        return "PULLBACK_THEN_RECLAIM"
    if _has_before(fams, {"COMPRESSION"}, "BREAKOUT20") or _has_before(fams, {"COMPRESSION"}, "RANGE_EXPAND"):
        return "COMPRESSION_THEN_BREAKOUT"
    if _has_before(fams, {"VOL_EXPAND", "VA_EXPAND"}, "IMPULSE_UP"):
        return "VOL_EXPAND_THEN_IMPULSE_UP"
    if _has_before(fams, {"IMPULSE_UP"}, "PAUSE_AFTER_IMPULSE"):
        return "IMPULSE_THEN_PAUSE"
    if "LAG_CATCHUP" in fams:
        return "LAG_THEN_CATCHUP"
    if "OPENING_GAP_HOLD" in fams:
        return "OPENING_GAP_HOLD"
    if "BREAKOUT20" in fams:
        return "BREAKOUT20"
    if "VWAP_RECLAIM" in fams:
        return "VWAP_RECLAIM"
    if "IMPULSE_UP" in fams:
        return "IMPULSE_UP"
    if "IMPULSE_DOWN" in fams:
        return "IMPULSE_DOWN"
    if fams:
        return "OTHER_" + fams[-1]
    return "OTHER"


def cluster_symbol_day(events: list[dict[str, Any]], *, gap_min: int = EPISODE_GAP_MIN) -> list[list[dict[str, Any]]]:
    ordered = sorted(events, key=lambda e: (str(e.get("event_time") or ""), str(e.get("event_family") or "")))
    clusters: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    last_m: int | None = None
    for e in ordered:
        m = _mins(str(e.get("event_time") or ""))
        if m is None:
            continue
        if not cur or last_m is None or (m - last_m) <= int(gap_min):
            cur.append(e)
            last_m = m
        else:
            clusters.append(cur)
            cur = [e]
            last_m = m
    if cur:
        clusters.append(cur)
    return clusters


def attach_path(ep: dict[str, Any]) -> None:
    rec = ep.get("rec")
    entry_t = str(ep.get("event_time") or "")
    if rec is None or not entry_t:
        ep["outcomes_attached"] = False
        return
    ie = rec["idx"].get(entry_t)
    px = rec["o"][ie] if ie is not None else float("nan")
    ep["x0_entry_open"] = float(px) if ie is not None and np.isfinite(px) else None
    fwd = []
    mfe = mae = None
    mfe_i = mae_i = None
    if ie is not None and np.isfinite(px) and px > 0:
        last = min(ie + PATH_BARS, len(rec["t"]) - 1)
        for k in range(ie, last + 1):
            hh = rec["t"][k]
            if interval_crosses_lunch(entry_t, hh):
                break
            hi, lo, cl = rec["h"][k], rec["l"][k], rec["c"][k]
            vw = rec["vw"][k]
            above_vw = bool(np.isfinite(vw) and np.isfinite(cl) and cl > vw)
            fwd.append((hh, float(rec["o"][k]), float(hi), float(lo), float(cl), float(vw) if np.isfinite(vw) else None, above_vw, float(ep.get("hi20") or np.nan)))
            hbps = _bps(hi, px)
            lbps = _bps(lo, px)
            step = k - ie
            if hbps is not None and (mfe is None or hbps > mfe):
                mfe, mfe_i = hbps, step
            if lbps is not None and (mae is None or lbps < mae):
                mae, mae_i = lbps, step
    ep["fwd_bars"] = fwd
    ep["mfe_bps"] = mfe
    ep["mae_bps"] = mae
    ep["time_to_mfe_min"] = mfe_i
    ep["time_to_mae_min"] = mae_i
    fav_i = None
    adv_i = None
    if ie is not None and np.isfinite(px) and px > 0:
        for step, row in enumerate(fwd):
            hbps = _bps(row[2], px)
            lbps = _bps(row[3], px)
            if fav_i is None and hbps is not None and hbps >= FAVOR_BPS:
                fav_i = step
            if adv_i is None and lbps is not None and lbps <= -FAVOR_BPS:
                adv_i = step
    ep["favor_first"] = bool(fav_i is not None and (adv_i is None or fav_i <= adv_i))
    ep["adverse_first"] = bool(adv_i is not None and (fav_i is None or adv_i < fav_i))
    ep["stall"] = bool(fav_i is None and adv_i is None)
    ep["mfe_before_mae"] = bool(mfe_i is not None and mae_i is not None and mfe_i <= mae_i)
    ret5 = None
    mark = hhmm_add(str(ep.get("feature_bar") or ""), 5)
    if mark and rec and not interval_crosses_lunch(str(ep.get("feature_bar") or ""), mark):
        j = rec["idx"].get(mark)
        if j is not None and np.isfinite(px) and px > 0:
            ret5 = _bps(rec["c"][j], px)
    ep["ret_p5m_bps"] = ret5
    if mfe is not None and mae is not None and abs(float(mfe)) < FAVOR_BPS and abs(float(mae)) < FAVOR_BPS:
        path = "STALL"
    elif ep["adverse_first"] and (mfe is None or abs(float(mae or 0)) > abs(float(mfe or 0))):
        path = "REVERSAL"
    elif ep["favor_first"] and ret5 is not None and ret5 < 0:
        path = "GIVEBACK"
    elif ep["favor_first"] and mfe is not None and mfe >= 20:
        path = "CONTINUATION_SECOND_LEG"
    elif ep["favor_first"]:
        path = "CONTINUATION"
    elif ep["adverse_first"]:
        path = "FAILURE"
    else:
        path = "AMBIGUOUS"
    ep["path_class"] = path
    ep["outcomes_attached"] = True
    if ep.get("x0_entry_open"):
        ep["x0_h5_bps"] = ret5
        ep["x1_h5_bps"] = (float(ret5) - float(X1_TAX_BPS)) if ret5 is not None else None


def build_episodes(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        by_key[(str(e.get("date")), str(e.get("symbol")))].append(e)
    episodes: list[dict[str, Any]] = []
    for (day, sym), xs in by_key.items():
        for cluster in cluster_symbol_day(xs):
            trigger = cluster[-1]
            seq = [str(e.get("event_family")) for e in cluster]
            ep = {
                "date": day,
                "block": trigger.get("block"),
                "symbol": sym,
                "sector": trigger.get("sector"),
                "start": cluster[0].get("event_time"),
                "event_time": trigger.get("event_time"),
                "feature_bar": trigger.get("feature_bar"),
                "available_at": trigger.get("available_at"),
                "families": seq,
                "sequence": classify(seq),
                "event_family": trigger.get("event_family"),
                "leading": trigger.get("leading"),
                "lagging": trigger.get("lagging"),
                "hi20": trigger.get("hi20"),
                "lo20": trigger.get("lo20"),
                "gap": trigger.get("gap"),
                "rec": trigger.get("rec"),
            }
            attach_path(ep)
            ep.pop("rec", None)
            episodes.append(ep)
    return {
        "episode_n": len(episodes),
        "raw_event_n": len(events),
        "episodes": episodes,
        "gap_min": EPISODE_GAP_MIN,
        "future_in_boundary": False,
    }
