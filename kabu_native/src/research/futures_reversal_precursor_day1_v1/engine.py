"""1-minute BOTH_DOWN grid. Features causal at T. Reversal labels are noncausal."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.futures_context_day1_effect_check_v1 import HORIZON_LABEL, HORIZONS_SEC
from research.futures_context_day1_effect_check_v1.engine import (
    _epoch,
    clock_dt,
    expected_symbols,
    load_futures_series,
    mean,
    median,
    ret_asof,
    stock_outcomes_at,
)
from research.futures_reversal_precursor_day1_v1 import (
    GRID_END_HM,
    GRID_START_HM,
    LABEL_HORIZONS_MIN,
    SELECTOR,
    TERCILE_N,
    TRADING_DATE,
    UNIVERSE_N,
)
from research.futures_reversal_precursor_day1_v1.features import precursors
from research.futures_x_stock_state_interaction_day1_v1.engine import (
    agreement_180,
    load_stock_tapes,
    tercile_split,
)
from research.new_causal_information_acquisition_v1.writer import day_layout

JST = ZoneInfo("Asia/Tokyo")


def minute_grid(day: str) -> list[datetime]:
    t = clock_dt(day, GRID_START_HM)
    end = clock_dt(day, GRID_END_HM)
    out: list[datetime] = []
    while t <= end:
        out.append(t)
        t = t + timedelta(minutes=1)
    return out


def _hm_key(dt: datetime) -> str:
    return dt.astimezone(JST).strftime("%H:%M")


def _rets(series: Any, te: float) -> dict[int, Optional[float]]:
    out: dict[int, Optional[float]] = {}
    for win in (30, 60, 180):
        r, _lk = ret_asof(series, te, win)
        out[win] = r
    return out


def reversal_labels(nk: Any, tx: Any, te: float) -> dict[str, bool]:
    """NONCAUSAL labels. Never used as input."""
    by1 = False
    by3 = False
    for m in range(1, max(LABEL_HORIZONS_MIN) + 1):
        t1 = te + 60.0 * float(m)
        nk_r, _ = ret_asof(nk, t1, 180)
        tx_r, _ = ret_asof(tx, t1, 180)
        hit = nk_r is not None and tx_r is not None and float(nk_r) > 0 and float(tx_r) > 0
        if m <= 1 and hit:
            by1 = True
        if m <= 3 and hit:
            by3 = True
    return {"REVERSAL_BY_1M": by1, "REVERSAL_BY_3M": by3}


def _basket_pre_and_outcomes(members: list[dict[str, Any]], side: str) -> dict[str, Any]:
    pre = mean([m["past_ret_180"] for m in members if m.get("past_ret_180") is not None])
    out: dict[str, Any] = {"pre_ret_180": pre, "n": len(members)}
    for hl in ("1m", "3m", "5m", "10m"):
        mids = []
        longs = []
        for m in members:
            pack = m.get(hl) or {}
            if pack.get("MID_RETURN_BPS") is not None:
                mids.append(float(pack["MID_RETURN_BPS"]))
            if pack.get("LONG_EXEC_MARKOUT_BPS") is not None:
                longs.append(float(pack["LONG_EXEC_MARKOUT_BPS"]))
        out[hl] = {
            "MID_mean": mean(mids),
            "MID_median": median(mids),
            "LONG_mean": mean(longs),
            "LONG_median": median(longs),
            "LONG_n": len(longs),
        }
    return out


def _stock_snapshot(tapes: dict[str, Any], quotes: dict[str, Any], symbols: list[str], te: float) -> dict[str, Any]:
    members: list[dict[str, Any]] = []
    past: list[float] = []
    for sym in symbols:
        tape = tapes[sym]
        sel = tape.selectors_at(te)
        pret = tape.past_mid_ret(te, 180)
        if pret is not None:
            past.append(float(pret))
        rec: dict[str, Any] = {"symbol": sym, "selectors": sel, "past_ret_180": pret, "value": (sel or {}).get(SELECTOR)}
        series = quotes[sym]
        for h in HORIZONS_SEC:
            rec[HORIZON_LABEL[h]] = stock_outcomes_at(series, te, h)
        members.append(rec)
    split = tercile_split(members, key="value", n=TERCILE_N)
    top = split[0] if split else []
    bottom = split[1] if split else []
    return {
        "cash_ew_ret_180": mean(past) if past else None,
        "top": _basket_pre_and_outcomes(top, "TOP") if top else None,
        "bottom": _basket_pre_and_outcomes(bottom, "BOTTOM") if bottom else None,
        "ranked_n": sum(1 for m in members if m.get("value") is not None),
    }


def build_both_down_grid(*, native_root: Path, day: str = TRADING_DATE) -> dict[str, Any]:
    layout = day_layout(day, native_root=native_root)
    symbols = expected_symbols(native_root, day)
    if len(symbols) != UNIVERSE_N:
        raise RuntimeError(f"expected {UNIVERSE_N} stocks, got {len(symbols)}")
    nk = load_futures_series(layout["nk225mini_jsonl"])
    tx = load_futures_series(layout["topix_jsonl"])
    tapes = load_stock_tapes(layout["stock"], symbols)
    quotes = {s: tapes[s].quotes_asof() for s in symbols}
    grid = minute_grid(day)
    minutes: list[dict[str, Any]] = []
    leak = 0
    for dt in grid:
        te = _epoch(dt)
        nk_r = _rets(nk, te)
        tx_r = _rets(tx, te)
        for win in (30, 60, 180):
            _, lk = ret_asof(nk, te, win)
            _, lk2 = ret_asof(tx, te, win)
            leak += int(lk) + int(lk2)
        agr = agreement_180(nk_r[180], tx_r[180])
        if agr != "BOTH_DOWN":
            continue
        stock = _stock_snapshot(tapes, quotes, symbols, te)
        top_pre = None if stock["top"] is None else stock["top"]["pre_ret_180"]
        bot_pre = None if stock["bottom"] is None else stock["bottom"]["pre_ret_180"]
        feat = precursors(
            nk30=nk_r[30],
            nk60=nk_r[60],
            nk180=nk_r[180],
            tx30=tx_r[30],
            tx60=tx_r[60],
            tx180=tx_r[180],
            cash_ew_180=stock["cash_ew_ret_180"],
            top_pre=top_pre,
            bottom_pre=bot_pre,
        )
        lab = reversal_labels(nk, tx, te)
        minutes.append(
            {
                "clock": _hm_key(dt),
                "t_epoch": te,
                "nk_ret_30": nk_r[30],
                "nk_ret_60": nk_r[60],
                "nk_ret_180": nk_r[180],
                "tx_ret_30": tx_r[30],
                "tx_ret_60": tx_r[60],
                "tx_ret_180": tx_r[180],
                "cash_ew_ret_180": stock["cash_ew_ret_180"],
                "top": stock["top"],
                "bottom": stock["bottom"],
                **feat,
                **lab,
            }
        )
    episodes = _episodes(minutes)
    return {
        "symbols": symbols,
        "grid_n": len(grid),
        "both_down_n": len(minutes),
        "future_leakage_n": leak,
        "minutes": minutes,
        "episodes": episodes,
    }


def _episodes(minutes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runs: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for row in minutes:
        if cur and abs(float(row["t_epoch"]) - float(cur[-1]["t_epoch"]) - 60.0) < 1e-6:
            cur.append(row)
        else:
            if cur:
                runs.append(cur)
            cur = [row]
    if cur:
        runs.append(cur)
    out = []
    for i, run in enumerate(runs, start=1):
        first = run[0]
        out.append(
            {
                "episode_id": i,
                "start": first["clock"],
                "end": run[-1]["clock"],
                "n_minutes": len(run),
                "anchor": first,
            }
        )
    return out
