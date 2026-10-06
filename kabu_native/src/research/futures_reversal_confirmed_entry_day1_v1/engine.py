"""Causal BOTH_DOWN → BOTH_UP confirmed-entry machine. No precursor inputs."""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.futures_context_day1_effect_check_v1.engine import (
    _epoch,
    expected_symbols,
    load_futures_series,
    mean,
    median,
    ret_asof,
    stock_outcomes_at,
)
from research.futures_reversal_confirmed_entry_day1_v1 import (
    ARM_EXPIRY_SEC,
    SELECTOR,
    STOCK_HORIZON_LABEL,
    STOCK_HORIZONS_SEC,
    TERCILE_N,
    TRADING_DATE,
    UNIVERSE_N,
)
from research.futures_reversal_precursor_day1_v1.engine import minute_grid
from research.futures_x_stock_state_interaction_day1_v1.engine import (
    agreement_180,
    load_stock_tapes,
)
from research.new_causal_information_acquisition_v1.writer import day_layout

JST = ZoneInfo("Asia/Tokyo")


def _hm(te: float) -> str:
    return datetime.fromtimestamp(float(te), JST).strftime("%H:%M:%S")


def _hm_min(te: float) -> str:
    return datetime.fromtimestamp(float(te), JST).strftime("%H:%M")


def tercile_three(rows: list[dict[str, Any]], *, key: str = "value", n: int = TERCILE_N) -> Optional[dict[str, list[str]]]:
    ranked = [r for r in rows if r.get(key) is not None and math.isfinite(float(r[key]))]
    ranked.sort(key=lambda r: (-float(r[key]), str(r.get("symbol") or "")))
    k = int(n)
    if len(ranked) < 3 * k:
        return None
    return {
        "TOP": [str(r["symbol"]) for r in ranked[:k]],
        "MIDDLE": [str(r["symbol"]) for r in ranked[k : 2 * k]],
        "BOTTOM": [str(r["symbol"]) for r in ranked[-k:]],
    }


def agreement_at(nk: Any, tx: Any, te: float) -> tuple[Optional[str], int]:
    nk_r, lk = ret_asof(nk, te, 180)
    tx_r, lk2 = ret_asof(tx, te, 180)
    return agreement_180(nk_r, tx_r), int(lk) + int(lk2)


def rank_at(tapes: dict[str, Any], symbols: list[str], te: float) -> Optional[dict[str, list[str]]]:
    rows = []
    for sym in symbols:
        sel = tapes[sym].selectors_at(te)
        rows.append({"symbol": sym, "value": (sel or {}).get(SELECTOR)})
    return tercile_three(rows)


def _ask_at(quotes: Any, te: float) -> Optional[float]:
    q0, _ = quotes.at(te)
    if q0 is None:
        return None
    ask = q0[1]
    try:
        x = float(ask)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 0 else None


def basket_at(
    quotes: dict[str, Any], names: list[str], te: float
) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(names), "asks": {}}
    for h in STOCK_HORIZONS_SEC:
        hl = STOCK_HORIZON_LABEL[h]
        mids: list[float] = []
        longs: list[float] = []
        for sym in names:
            rec = stock_outcomes_at(quotes[sym], te, h)
            if rec.get("MID_RETURN_BPS") is not None:
                mids.append(float(rec["MID_RETURN_BPS"]))
            if rec.get("LONG_EXEC_MARKOUT_BPS") is not None:
                longs.append(float(rec["LONG_EXEC_MARKOUT_BPS"]))
        out[hl] = {
            "MID_mean": mean(mids),
            "MID_median": median(mids),
            "LONG_mean": mean(longs),
            "LONG_median": median(longs),
            "LONG_n": len(longs),
            "LONG_pos_n": sum(1 for v in longs if v > 0),
            "LONG_neg_n": sum(1 for v in longs if v < 0),
            "LONG_values": longs,
        }
    asks = []
    for sym in names:
        a = _ask_at(quotes[sym], te)
        out["asks"][sym] = a
        if a is not None:
            asks.append(a)
    out["entry_ask_mean"] = mean(asks)
    return out


def _episodes(minute_rows: list[tuple[float, Optional[str]]]) -> list[dict[str, Any]]:
    runs: list[list[float]] = []
    cur: list[float] = []
    for te, agr in minute_rows:
        if agr != "BOTH_DOWN":
            if cur:
                runs.append(cur)
                cur = []
            continue
        if cur and abs(float(te) - float(cur[-1]) - 60.0) < 1e-6:
            cur.append(float(te))
        else:
            if cur:
                runs.append(cur)
            cur = [float(te)]
    if cur:
        runs.append(cur)
    out = []
    for i, times in enumerate(runs, start=1):
        out.append(
            {
                "episode_id": i,
                "arm_t": times[0],
                "both_down_minutes": times,
                "n_minutes": len(times),
                "start": _hm_min(times[0]),
                "end": _hm_min(times[-1]),
            }
        )
    return out


def _candidate_times(nk: Any, tx: Any, t0: float, t1: float) -> list[float]:
    times = set()
    for series in (nk, tx):
        for t in series.times:
            if t0 < float(t) <= t1:
                times.add(float(t))
    t = t0 + 60.0
    while t <= t1 + 1e-9:
        times.add(float(t))
        t += 60.0
    times.add(float(t1))
    return sorted(times)


def run_state_machine(*, native_root: Path, day: str = TRADING_DATE) -> dict[str, Any]:
    layout = day_layout(day, native_root=native_root)
    symbols = expected_symbols(native_root, day)
    if len(symbols) != UNIVERSE_N:
        raise RuntimeError(f"expected {UNIVERSE_N} stocks, got {len(symbols)}")
    nk = load_futures_series(layout["nk225mini_jsonl"])
    tx = load_futures_series(layout["topix_jsonl"])
    tapes = load_stock_tapes(layout["stock"], symbols)
    quotes = {s: tapes[s].quotes_asof() for s in symbols}
    leak = 0
    minute_rows: list[tuple[float, Optional[str]]] = []
    for dt in minute_grid(day):
        te = _epoch(dt)
        agr, lk = agreement_at(nk, tx, te)
        leak += lk
        minute_rows.append((te, agr))
    episodes = _episodes(minute_rows)
    agr_by_minute = {te: agr for te, agr in minute_rows}
    events: list[dict[str, Any]] = []
    expired = 0
    armed = 0
    for ep in episodes:
        armed += 1
        arm_t = float(ep["arm_t"])
        expire_t = arm_t + float(ARM_EXPIRY_SEC)
        ep["arm_hm"] = _hm(arm_t)
        ep["expire_hm"] = _hm(expire_t)
        last_down = arm_t
        trigger_t = None
        trigger_src = None
        for t in _candidate_times(nk, tx, arm_t, expire_t):
            agr, lk = agreement_at(nk, tx, t)
            leak += lk
            if agr == "BOTH_DOWN":
                last_down = float(t)
            elif agr == "BOTH_UP":
                trigger_t = float(t)
                trigger_src = "futures_received_at"
                break
        ep["last_down_hm"] = _hm(last_down)
        if trigger_t is None:
            expired += 1
            ranks = rank_at(tapes, symbols, last_down)
            ep["status"] = "EXPIRED"
            ep["trigger_t"] = None
            ep["latency_sec"] = None
            ep["top16"] = None if ranks is None else ranks["TOP"]
            continue
        # Last causal BOTH_DOWN snapshot strictly before trigger. Never re-rank after BOTH_UP.
        sel_t = arm_t
        for te in ep["both_down_minutes"]:
            if float(te) < float(trigger_t) and agr_by_minute.get(float(te)) == "BOTH_DOWN":
                sel_t = float(te)
        if last_down + 1e-9 < trigger_t:
            sel_t = float(last_down)
        ranks = rank_at(tapes, symbols, sel_t)
        if ranks is None:
            ep["status"] = "NO_RANK"
            ep["trigger_t"] = trigger_t
            continue
        top = ranks["TOP"]
        middle = ranks["MIDDLE"]
        bottom = ranks["BOTTOM"]
        top_pack = basket_at(quotes, top, trigger_t)
        mid_pack = basket_at(quotes, middle, trigger_t)
        bot_pack = basket_at(quotes, bottom, trigger_t)
        all_pack = basket_at(quotes, symbols, trigger_t)
        tb_long = None
        if top_pack["10m"]["LONG_mean"] is not None and bot_pack["10m"]["LONG_mean"] is not None:
            tb_long = float(top_pack["10m"]["LONG_mean"]) - float(bot_pack["10m"]["LONG_mean"])
        ev = {
            "episode_id": ep["episode_id"],
            "status": "TRIGGER",
            "arm_t": arm_t,
            "arm_hm": _hm(arm_t),
            "arm_min": _hm_min(arm_t),
            "select_t": sel_t,
            "select_hm": _hm(sel_t),
            "trigger_t": trigger_t,
            "trigger_hm": _hm(trigger_t),
            "trigger_src": trigger_src,
            "entry_t": trigger_t,
            "entry_hm": _hm(trigger_t),
            "confirmation_received_at_le_entry": True,
            "latency_sec": float(trigger_t) - arm_t,
            "expire_t": expire_t,
            "episode_start": ep["start"],
            "episode_end": ep["end"],
            "episode_minutes": ep["n_minutes"],
            "top16": top,
            "middle16": middle,
            "bottom16": bottom,
            "TOP": top_pack,
            "MIDDLE": mid_pack,
            "BOTTOM": bot_pack,
            "ALL48": all_pack,
            "TOP_BOTTOM_LONG_10m": tb_long,
            "select_before_trigger": bool(sel_t + 1e-9 < trigger_t),
        }
        # select must be <= trigger and ranking uses <= sel_t; entry uses <= trigger
        events.append(ev)
        ep["status"] = "TRIGGER"
        ep["trigger_t"] = trigger_t
        ep["latency_sec"] = ev["latency_sec"]
        ep["select_t"] = sel_t
        ep["top16"] = top

    return {
        "symbols": symbols,
        "future_leakage_n": leak,
        "grid_n": len(minute_rows),
        "episode_n": len(episodes),
        "armed_n": armed,
        "expired_n": expired,
        "trigger_n": len(events),
        "episodes": episodes,
        "events": events,
        "tapes": tapes,
        "quotes": quotes,
    }
