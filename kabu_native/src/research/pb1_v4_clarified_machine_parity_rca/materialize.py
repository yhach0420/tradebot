"""Causal 88-day reconstruction. Discovery only. No future outcome. Does not mutate the machine."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.fixed_universe_historical_foundation_v1.technical import session_vwap
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.pb1_opening_range_continuation_face_valid_v1.or15 import freeze_or15, session_idx_of
from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_opening_range_continuation_face_valid_v2.walk import _d5, _sma
from research.pb1_playbook_redesign_v3.structure_route import build_day_zones as build_causal_sr_zones, merge_unique
from research.pb1_v3_1_face_validity_fix import NOISE_LOOKBACK_SESSIONS
from research.pb1_v3_2_face_failure_rca.second_pass import HUMAN_LABELS
from research.pb1_v4_clarified_machine_implementation.baselines import SameClockOpeningHistory
from research.pb1_v4_clarified_machine_implementation.location import identify_location
from research.pb1_v4_clarified_machine_implementation.seed import classify_seed
from research.pb1_v4_clarified_machine_spec_parity_audit.reconstruct import join_88, load_walked
from research.pb1_v4_machine_implementation.bars5 import build_five_m
from research.support_resistance_face_valid_first_interaction_rebuild_v1.swings import new_swing_state, step_swings

_ = NOISE_LOOKBACK_SESSIONS


def _num(s) -> np.ndarray:
    return np.asarray(s, dtype=float)


def slim_rec(sg) -> dict[str, Any]:
    sg = sg.sort_values("time_label")
    times = [str(t)[:5] for t in sg["time_label"].tolist()]
    o = _num(sg["open"])
    h = _num(sg["high"])
    l = _num(sg["low"])
    c = _num(sg["close"])
    v = _num(sg["volume"])
    va = _num(sg["trading_value"])
    vw = session_vwap(h, l, c, v, va)
    return {"t": times, "o": o, "h": h, "l": l, "c": c, "v": v, "va": va, "vw": vw}


def _bar_slim(b: dict[str, Any]) -> dict[str, Any]:
    return {
        "t0": b.get("t0"),
        "t1": str(b.get("t1") or "")[:5],
        "o": b.get("o"),
        "h": b.get("h"),
        "l": b.get("l"),
        "c": b.get("c"),
        "range": b.get("range"),
        "body": b.get("body"),
        "body_over_range": b.get("body_over_range"),
        "direction": b.get("direction"),
        "net": b.get("net"),
    }


def _zone_slim(z: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": z.get("name") or z.get("source") or z.get("kind") or z.get("role"),
        "role": z.get("role"),
        "kind": z.get("kind"),
        "source": z.get("source"),
        "zone_low": z.get("zone_low"),
        "zone_high": z.get("zone_high"),
        "zone_mid": z.get("zone_mid"),
    }


def materialize_88(*, bind: dict[str, Any]) -> dict[str, Any]:
    walked = load_walked()
    if not walked.get("ok"):
        return {"ok": False, "reason": "walk_cache_missing"}
    rows = join_88(walked=walked)
    split = dict(bind.get("split") or {})
    disc = [str(d) for d in list(split.get("discovery_dates") or [])]
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    keys = {(str(r["symbol"]), str(r["date"])) for r in rows}
    symbols = sorted({s for s, _d in keys})
    disc_set = set(disc)
    if {d for _s, d in keys} - disc_set:
        raise RuntimeError("non_discovery_date_in_88")
    print(f"RCA_LOAD symbols={len(symbols)} disc_n={len(disc)} keys={len(keys)}", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=disc_set, forbidden_dates=conf | val)
    if minutes is None or minutes.empty:
        return {"ok": False, "reason": "empty_minutes"}
    minutes["date"] = minutes["date"].astype(str)
    minutes["symbol"] = minutes["symbol"].astype(str)
    grouped = {d: g for d, g in minutes.groupby("date", sort=False)}
    open5 = SameClockOpeningHistory()
    hist: dict[str, list] = defaultdict(list)
    swings: dict[str, dict[str, Any]] = defaultdict(new_swing_state)
    reactions: dict[str, list] = defaultdict(list)
    next_of = {disc[i]: disc[i + 1] for i in range(len(disc) - 1)}
    snaps: dict[tuple[str, str], dict[str, Any]] = {}
    for di, date in enumerate(disc):
        if di % 50 == 0:
            print(f"RCA_WALK {di}/{len(disc)} {date} snapped={len(snaps)}", flush=True)
        gdate = grouped.get(date)
        if gdate is None:
            continue
        by_sym = {str(s): sg for s, sg in gdate.groupby("symbol", sort=False)}
        for symbol in symbols:
            sg = by_sym.get(symbol)
            if sg is None or sg.empty:
                continue
            rec = slim_rec(sg)
            rec["session_idx"] = session_idx_of(rec["t"])
            session_idx = rec["session_idx"]
            prior = hist[symbol]
            atr = atr20(prior) if prior else float("nan")
            pdh = float(prior[-1]["high"]) if prior and _finite(prior[-1].get("high")) else None
            pdl = float(prior[-1]["low"]) if prior and _finite(prior[-1].get("low")) else None
            pdc = float(prior[-1]["close"]) if prior and _finite(prior[-1].get("close")) else None
            want = (symbol, date) in keys
            bars = build_five_m(rec, session_idx, through="11:19") if want else []
            if want:
                or15 = freeze_or15(rec["t"], rec["h"], rec["l"], session_idx)
                clock_snap = open5.snapshot_open(symbol, bars[:3], atr20=atr)
                for ck in list(clock_snap.get("same_clock") or []):
                    ck.pop("date_coverage", None)
                seed_row = classify_seed(bars[:3], clock_snap=clock_snap, atr20=atr)
                open_px = float(rec["o"][session_idx[0]]) if session_idx and _finite(rec["o"][session_idx[0]]) else None
                d5h, d5l = _d5(prior)
                prior_closes = [float(d["close"]) for d in prior if _finite(d.get("close"))]
                sma25 = _sma(prior_closes, open_px, 25) if _finite(open_px) else None
                sma75 = _sma(prior_closes, open_px, 75) if _finite(open_px) else None
                vw0 = rec["vw"][session_idx[0]] if session_idx else None
                zones = []
                if session_idx and _finite(open_px) and _finite(atr):
                    zones = merge_unique(
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
                            sma25=sma25,
                            sma75=sma75,
                            vwap=vw0,
                            ref_px=float(open_px),
                        ),
                        float(atr),
                    )
                sign = int(seed_row.get("DIR") or 0)
                loc = None
                if sign in (1, -1) and bars[:3] and or15.get("ok"):
                    loc = identify_location(
                        sign=sign,
                        active=True,
                        bar=bars[2],
                        or_high=float(or15["or_high"]),
                        or_low=float(or15["or_low"]),
                        zones=list(zones),
                        pdh=pdh,
                        pdl=pdl,
                        pdc=pdc,
                        vwap=vw0,
                        n1m=None,
                        left=False,
                    )
                snaps[(symbol, date)] = {
                    "atr20": float(atr) if _finite(atr) else None,
                    "pdh": pdh,
                    "pdl": pdl,
                    "pdc": pdc,
                    "open": open_px,
                    "or_high": or15.get("or_high") if or15.get("ok") else None,
                    "or_low": or15.get("or_low") if or15.get("ok") else None,
                    "clock_snap": clock_snap,
                    "seed_row": seed_row,
                    "bars": [_bar_slim(b) for b in bars],
                    "zones": [_zone_slim(z) for z in zones],
                    "location_at_0914": loc,
                    "vwap_open": float(vw0) if _finite(vw0) else None,
                }
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
    out_rows = []
    for r in rows:
        key = (str(r["symbol"]), str(r["date"]))
        snap = dict(snaps.get(key) or {})
        lab = dict(HUMAN_LABELS.get(int(r.get("rca_id") or 0)) or {})
        out_rows.append({**r, "snap": snap, "human_attrs": lab.get("sp_attrs"), "human_note": lab.get("sp_note")})
    return {
        "ok": True,
        "n": len(out_rows),
        "snapped_n": len(snaps),
        "rows": out_rows,
        "future_outcome_n": 0,
        "machine_classify_seed_called_read_only": True,
    }
