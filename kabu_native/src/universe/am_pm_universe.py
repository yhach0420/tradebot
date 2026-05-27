"""
Phase 114: AM/PM independent dynamic50 universe design (shadow only).
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from zoneinfo import ZoneInfo

from universe.opening_screen import rank_normalize, volatility_liquidity_score

JST = ZoneInfo("Asia/Tokyo")
PUSH_LIMIT = 50
FOCUS_SYMBOLS = ("3905.T", "6613.T")

AM_CUTOFF = "09:03"
AM_SCREEN_END = "09:03"
PM_SCREEN_START = "12:25"
PM_SCREEN_END = "12:32"
PM_CUTOFF = "12:25"
MORNING_END = "11:30"
NEAR_LIMIT_PCT = 0.5
THIN_BOARD_QTY = 500.0

AM_UNIVERSE_FIELDS = (
    "symbol",
    "symbol_key",
    "exchange",
    "passed",
    "source_bucket",
    "selected_reason",
    "session_bucket",
    "screening_time",
    "volatility_liquidity_score",
    "previous_day_vol_liq_score",
    "atr_pct",
    "intraday_range_pct",
    "trading_value",
    "volume",
    "rank",
)

PM_UNIVERSE_FIELDS = AM_UNIVERSE_FIELDS + (
    "morning_trading_value",
    "morning_range_pct",
    "pm_trading_value",
    "spread_bps_proxy",
    "board_liquidity_proxy",
    "pm_composite_score",
)


def _norm(code: str) -> str:
    c = str(code).strip().upper().split("@")[0]
    return c if c.endswith(".T") else f"{c}.T"


def _as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_time(hhmm: str) -> time:
    h, m = map(int, hhmm.split(":"))
    return time(h, m, 59)


def estimate_daily_limit_prices(
    prev_close: Optional[float],
    *,
    market: str = "",
) -> tuple[Optional[float], Optional[float], str]:
    """Shadow proxy: JPX-style absolute yen band from previous close tier."""
    if prev_close is None or prev_close <= 0:
        return None, None, "missing_prev_close"
    tiers = (
        (100, 30),
        (200, 50),
        (500, 70),
        (700, 100),
        (1000, 150),
        (1500, 300),
        (2000, 400),
        (3000, 500),
        (5000, 700),
        (7000, 1000),
        (1_000_000, 1500),
    )
    move = 30
    for cap, m in tiers:
        if prev_close <= cap:
            move = m
            break
    if market == "growth":
        move = int(move * 1.1)
    up = prev_close + move
    down = max(prev_close - move, 0.01)
    return round(up, 4), round(down, 4), "proxy_jpx_tier_abs_yen"


def limit_status_from_prices(
    *,
    current: Optional[float],
    limit_up: Optional[float],
    limit_down: Optional[float],
    bid_qty: Optional[float],
    ask_qty: Optional[float],
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "daily_limit_up_price": limit_up,
        "daily_limit_down_price": limit_down,
        "current_price": current,
        "distance_to_limit_up_pct": None,
        "distance_to_limit_down_pct": None,
        "is_limit_up": False,
        "is_limit_down": False,
        "near_limit_up": False,
        "near_limit_down": False,
        "board_liquidity_thin": False,
        "shadow_exclude_candidate": False,
        "shadow_warn": "",
    }
    if current is None or limit_up is None or limit_down is None:
        return out
    if limit_up > 0:
        out["distance_to_limit_up_pct"] = round((limit_up - current) / limit_up * 100.0, 4)
    if limit_down > 0:
        out["distance_to_limit_down_pct"] = round((current - limit_down) / limit_down * 100.0, 4)
    eps = 0.001
    out["is_limit_up"] = current >= limit_up * (1 - eps)
    out["is_limit_down"] = current <= limit_down * (1 + eps)
    out["near_limit_up"] = (
        not out["is_limit_up"]
        and out["distance_to_limit_up_pct"] is not None
        and out["distance_to_limit_up_pct"] <= NEAR_LIMIT_PCT
    )
    out["near_limit_down"] = (
        not out["is_limit_down"]
        and out["distance_to_limit_down_pct"] is not None
        and out["distance_to_limit_down_pct"] <= NEAR_LIMIT_PCT
    )
    bq = bid_qty or 0
    aq = ask_qty or 0
    out["board_liquidity_thin"] = min(bq, aq) < THIN_BOARD_QTY
    warns: list[str] = []
    if out["is_limit_up"]:
        warns.append("is_limit_up")
        out["shadow_exclude_candidate"] = True
    if out["is_limit_down"]:
        warns.append("is_limit_down")
        out["shadow_exclude_candidate"] = True
    if out["near_limit_up"] and out["board_liquidity_thin"]:
        warns.append("near_limit_up_thin_board")
        out["shadow_exclude_candidate"] = True
    if out["near_limit_down"]:
        warns.append("near_limit_down")
    out["shadow_warn"] = "|".join(warns)
    return out


@dataclass
class PushSessionSlice:
    symbol: str
    has_data: bool = False
    trading_value: Optional[float] = None
    volume: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    current_price: Optional[float] = None
    prev_close: Optional[float] = None
    bid_qty: Optional[float] = None
    ask_qty: Optional[float] = None
    bid_price: Optional[float] = None
    ask_price: Optional[float] = None
    last_trade_ok: bool = False


def _payload_metrics(payload: Mapping[str, Any]) -> dict[str, Optional[float]]:
    cur = _as_float(payload.get("CurrentPrice")) or _as_float(payload.get("CalcPrice"))
    return {
        "trading_value": _as_float(payload.get("TradingValue")),
        "volume": _as_float(payload.get("TradingVolume")),
        "high": _as_float(payload.get("HighPrice")),
        "low": _as_float(payload.get("LowPrice")),
        "current": cur,
        "prev_close": _as_float(payload.get("PreviousClose")),
        "bid_qty": _as_float(payload.get("BidQty")),
        "ask_qty": _as_float(payload.get("AskQty")),
        "bid_price": _as_float(payload.get("BidPrice")),
        "ask_price": _as_float(payload.get("AskPrice")),
    }


def load_push_slice(
    push_day_dir: Path,
    symbol: str,
    *,
    window_start: time,
    window_end: time,
) -> PushSessionSlice:
    stem = symbol.replace(".T", "")
    path = push_day_dir / f"{stem}.T.jsonl"
    if not path.is_file():
        path = push_day_dir / f"{symbol}.jsonl"
    if not path.is_file():
        return PushSessionSlice(symbol=symbol)

    trade_d: Optional[date] = None
    try:
        trade_d = date.fromisoformat(push_day_dir.name)
    except ValueError:
        pass

    last_payload: Optional[dict] = None
    last_ts = ""
    hi: Optional[float] = None
    lo: Optional[float] = None
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec = str(row.get("recorded_at") or "")
            if not rec:
                continue
            dt = datetime.fromisoformat(rec.replace("Z", "+00:00")).astimezone(JST)
            if trade_d and dt.date() != trade_d:
                continue
            t = dt.time()
            if t < window_start or t > window_end:
                continue
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            m = _payload_metrics(payload)
            if m["high"] is not None:
                hi = max(hi, m["high"]) if hi is not None else m["high"]
            if m["low"] is not None:
                lo = min(lo, m["low"]) if lo is not None else m["low"]
            if rec >= last_ts:
                last_ts = rec
                last_payload = payload

    if not last_payload:
        return PushSessionSlice(symbol=symbol)

    m = _payload_metrics(last_payload)
    rng_pct = None
    if m["prev_close"] and hi is not None and lo is not None and m["prev_close"] > 0:
        rng_pct = round((hi - lo) / m["prev_close"] * 100.0, 6)
    spread_bps = None
    if m["bid_price"] and m["ask_price"] and m["ask_price"] > 0:
        mid = (m["bid_price"] + m["ask_price"]) / 2.0
        if mid > 0:
            spread_bps = round((m["ask_price"] - m["bid_price"]) / mid * 10000.0, 4)

    return PushSessionSlice(
        symbol=symbol,
        has_data=True,
        trading_value=m["trading_value"],
        volume=m["volume"],
        high=hi,
        low=lo,
        current_price=m["current"],
        prev_close=m["prev_close"],
        bid_qty=m["bid_qty"],
        ask_qty=m["ask_qty"],
        bid_price=m["bid_price"],
        ask_price=m["ask_price"],
        last_trade_ok=m["current"] is not None and (m["trading_value"] or 0) > 0,
    )


def spread_bps_proxy(sl: PushSessionSlice) -> Optional[float]:
    if sl.bid_price and sl.ask_price and sl.ask_price > 0:
        mid = (sl.bid_price + sl.ask_price) / 2.0
        if mid > 0:
            return round((sl.ask_price - sl.bid_price) / mid * 10000.0, 4)
    return None


def board_liquidity_proxy(sl: PushSessionSlice) -> Optional[float]:
    if sl.bid_qty is None and sl.ask_qty is None:
        return None
    return round(min(sl.bid_qty or 0, sl.ask_qty or 0), 2)


@dataclass
class PmScoreInputs:
    symbol: str
    prev_vol_liq: Optional[float] = None
    morning_tv: Optional[float] = None
    morning_range_pct: Optional[float] = None
    morning_volume: Optional[float] = None
    pm_tv: Optional[float] = None
    pm_board_liq: Optional[float] = None
    pm_spread_bps: Optional[float] = None
    has_morning_push: bool = False
    has_pm_push: bool = False


def compute_pm_composite_scores(inputs: Sequence[PmScoreInputs]) -> dict[str, float]:
    syms = [x.symbol for x in inputs]
    prev = {x.symbol: x.prev_vol_liq for x in inputs}
    mtv = {x.symbol: x.morning_tv for x in inputs}
    mrng = {x.symbol: x.morning_range_pct for x in inputs}
    mvol = {x.symbol: x.morning_volume for x in inputs}
    ptv = {x.symbol: x.pm_tv for x in inputs}
    bliq = {x.symbol: x.pm_board_liq for x in inputs}

    n_prev = rank_normalize(prev)
    n_mtv = rank_normalize(mtv)
    n_mrng = rank_normalize(mrng)
    n_mvol = rank_normalize(mvol)
    n_ptv = rank_normalize(ptv)
    n_bliq = rank_normalize(bliq)

    scores: dict[str, float] = {}
    for s in syms:
        scores[s] = round(
            0.30 * n_prev.get(s, 0.0)
            + 0.20 * n_mtv.get(s, 0.0)
            + 0.15 * n_mrng.get(s, 0.0)
            + 0.10 * n_mvol.get(s, 0.0)
            + 0.15 * n_ptv.get(s, 0.0)
            + 0.10 * n_bliq.get(s, 0.0),
            6,
        )
    return scores


def build_am_universe_rows(
    feature_rows: Sequence[Mapping[str, str]],
    *,
    symbol_meta: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    scored = []
    for row in feature_rows:
        vl = _as_float(row.get("volatility_liquidity_score"))
        if vl is None:
            continue
        scored.append((vl, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[dict[str, Any]] = []
    for i, (vl, row) in enumerate(scored[:PUSH_LIMIT], start=1):
        sym = _norm(row["symbol"])
        ex = int(row.get("exchange") or symbol_meta.get(sym, {}).get("exchange") or 1)
        out.append(
            {
                "symbol": sym,
                "symbol_key": str(row.get("symbol_key") or f"{sym.replace('.T', '')}@{ex}"),
                "exchange": ex,
                "passed": "True",
                "source_bucket": "am_vol_liq_dynamic50",
                "selected_reason": "previous_day_vol_liq_top50",
                "session_bucket": "am",
                "screening_time": AM_CUTOFF,
                "volatility_liquidity_score": vl,
                "previous_day_vol_liq_score": vl,
                "atr_pct": row.get("atr_pct") or "",
                "intraday_range_pct": row.get("intraday_range_pct") or "",
                "trading_value": row.get("trading_value") or "",
                "volume": row.get("volume") or "",
                "rank": str(i),
            }
        )
    return out


def build_pm_universe_rows(
    feature_rows: Sequence[Mapping[str, str]],
    *,
    symbol_meta: Mapping[str, Mapping[str, Any]],
    push_day_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    morning_start = time(9, 0)
    morning_end = _parse_time(MORNING_END)
    pm_start = time(12, 20)
    pm_end = _parse_time(PM_CUTOFF)

    pm_inputs: list[PmScoreInputs] = []
    morning_push_n = 0
    pm_push_n = 0

    for row in feature_rows:
        sym = _norm(row["symbol"])
        vl = _as_float(row.get("volatility_liquidity_score"))
        if vl is None:
            continue
        morning = load_push_slice(push_day_dir, sym, window_start=morning_start, window_end=morning_end)
        pm = load_push_slice(push_day_dir, sym, window_start=pm_start, window_end=pm_end)
        if morning.has_data:
            morning_push_n += 1
        if pm.has_data:
            pm_push_n += 1
        m_rng = None
        if morning.prev_close and morning.high and morning.low and morning.prev_close > 0:
            m_rng = round((morning.high - morning.low) / morning.prev_close * 100.0, 6)
        pm_inputs.append(
            PmScoreInputs(
                symbol=sym,
                prev_vol_liq=vl,
                morning_tv=morning.trading_value,
                morning_range_pct=m_rng,
                morning_volume=morning.volume,
                pm_tv=pm.trading_value,
                pm_board_liq=board_liquidity_proxy(pm),
                pm_spread_bps=spread_bps_proxy(pm),
                has_morning_push=morning.has_data,
                has_pm_push=pm.has_data,
            )
        )

    scores = compute_pm_composite_scores(pm_inputs)
    meta_by_sym = {p.symbol: p for p in pm_inputs}
    ranked = sorted(scores.keys(), key=lambda s: scores[s], reverse=True)[:PUSH_LIMIT]

    out: list[dict[str, Any]] = []
    for i, sym in enumerate(ranked, start=1):
        row = next((r for r in feature_rows if _norm(r["symbol"]) == sym), {})
        p = meta_by_sym[sym]
        ex = int(row.get("exchange") or symbol_meta.get(sym, {}).get("exchange") or 1)
        out.append(
            {
                "symbol": sym,
                "symbol_key": str(row.get("symbol_key") or f"{sym.replace('.T', '')}@{ex}"),
                "exchange": ex,
                "passed": "True",
                "source_bucket": "pm_vol_liq_dynamic50",
                "selected_reason": "pm_rescreen_vol_liq_morning_liquidity",
                "session_bucket": "pm",
                "screening_time": PM_CUTOFF,
                "volatility_liquidity_score": row.get("volatility_liquidity_score") or "",
                "previous_day_vol_liq_score": p.prev_vol_liq,
                "atr_pct": row.get("atr_pct") or "",
                "intraday_range_pct": row.get("intraday_range_pct") or "",
                "trading_value": row.get("trading_value") or "",
                "volume": row.get("volume") or "",
                "rank": str(i),
                "morning_trading_value": p.morning_tv or "",
                "morning_range_pct": p.morning_range_pct or "",
                "pm_trading_value": p.pm_tv or "",
                "spread_bps_proxy": p.pm_spread_bps or "",
                "board_liquidity_proxy": p.pm_board_liq or "",
                "pm_composite_score": scores.get(sym),
            }
        )

    cov = {
        "morning_push_symbols": morning_push_n,
        "pm_push_symbols": pm_push_n,
        "feature_pool": len(pm_inputs),
        "push_watchlist_only": push_day_dir.is_dir(),
    }
    return out, cov


def compare_am_pm(am_rows: Sequence[Mapping[str, str]], pm_rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    am_set = {_norm(r["symbol"]) for r in am_rows}
    pm_set = {_norm(r["symbol"]) for r in pm_rows}
    overlap = am_set & pm_set
    added = sorted(pm_set - am_set)
    removed = sorted(am_set - pm_set)
    n = max(len(am_set), 1)
    churn = round((len(added) + len(removed)) / (2 * n), 4)
    return {
        "overlap_count": len(overlap),
        "overlap_symbols": sorted(overlap),
        "added_symbols": added,
        "removed_symbols": removed,
        "churn_rate": churn,
    }


def build_diff_rows(am_rows: Sequence[Mapping[str, str]], pm_rows: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    am_set = {_norm(r["symbol"]) for r in am_rows}
    pm_set = {_norm(r["symbol"]) for r in pm_rows}
    all_syms = sorted(am_set | pm_set)
    rows: list[dict[str, str]] = []
    for sym in all_syms:
        in_am = sym in am_set
        in_pm = sym in pm_set
        if in_am and in_pm:
            cat = "stayed"
        elif in_pm:
            cat = "added_pm"
        else:
            cat = "removed_pm"
        rows.append(
            {
                "symbol": sym,
                "in_am_universe": in_am,
                "in_pm_universe": in_pm,
                "change_type": cat,
            }
        )
    return rows


def build_limit_diagnostics(
    symbols: Sequence[str],
    *,
    feature_by_sym: Mapping[str, Mapping[str, str]],
    symbol_meta: Mapping[str, Mapping[str, Any]],
    push_day_dir: Path,
) -> list[dict[str, Any]]:
    pm_end = _parse_time(PM_CUTOFF)
    rows: list[dict[str, Any]] = []
    for sym in symbols:
        feat = feature_by_sym.get(sym, {})
        market = str(feat.get("market") or symbol_meta.get(sym, {}).get("market") or "")
        prev_close = _as_float(feat.get("close"))
        if prev_close is None and feat.get("volume") and feat.get("trading_value"):
            vol = _as_float(feat.get("volume"))
            tv = _as_float(feat.get("trading_value"))
            if vol and tv and vol > 0:
                prev_close = tv / vol
        pm = load_push_slice(push_day_dir, sym, window_start=time(12, 20), window_end=pm_end)
        if pm.prev_close:
            prev_close = pm.prev_close
        lim_up, lim_dn, lim_src = estimate_daily_limit_prices(prev_close, market=market)
        cur = pm.current_price
        lim = limit_status_from_prices(
            current=cur,
            limit_up=lim_up,
            limit_down=lim_dn,
            bid_qty=pm.bid_qty,
            ask_qty=pm.ask_qty,
        )
        rows.append(
            {
                "symbol": sym,
                "market": market,
                "limit_price_source": lim_src,
                **lim,
            }
        )
    return rows


def session_close_design() -> dict[str, Any]:
    return {
        "morning_session": {
            "morning_session_close_time": "11:30",
            "force_exit_before_lunch": True,
            "exit_reason": "morning_session_close",
            "phase114_status": "design_whatif_only_not_in_production",
            "note": "AM universe positions should not carry into PM; close before lunch",
        },
        "afternoon_session": {
            "afternoon_session_close_time": "15:25",
            "force_exit_before_close": True,
            "exit_reason": "afternoon_session_close",
            "distinct_from": "session_end_exit",
            "phase114_status": "design_whatif_only_not_in_production",
        },
        "am_pm_independence": {
            "carry_am_symbols_to_pm": False,
            "pm_rescreen_independent": True,
            "no_symbol_hard_exclude": True,
        },
    }


def determine_verdict(
    *,
    features_exists: bool,
    am_count: int,
    pm_count: int,
    push_morning_n: int,
    comparison: Mapping[str, Any],
    limit_rows: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not features_exists:
        return "need_intraday_liquidity_source", ["features_YYYYMMDD.csv missing"]
    if am_count < PUSH_LIMIT or pm_count < PUSH_LIMIT:
        return "need_intraday_liquidity_source", [f"am={am_count} pm={pm_count}"]
    if push_morning_n < 20:
        notes.append(f"pm rescreen uses PUSH morning stats for only {push_morning_n}/3575 symbols")
    if not any(r.get("limit_price_source") == "proxy_jpx_tier_abs_yen" for r in limit_rows):
        return "need_limit_price_source", ["no limit price proxy available"]
    churn = float(comparison.get("churn_rate") or 0)
    if churn < 0.08 and int(comparison.get("overlap_count") or 0) >= 45:
        notes.append(f"low churn={churn:.1%} overlap={comparison.get('overlap_count')}")
        return "am_pm_screening_not_worthwhile", notes
    notes.append(
        f"am/pm churn={churn:.1%} overlap={comparison.get('overlap_count')} "
        f"push_morning={push_morning_n}"
    )
    if push_morning_n < 100:
        notes.append("official limit prices not in PUSH; using tier proxy")
        notes.append("full-market PM needs intraday store beyond 27-symbol PUSH")
    return "am_pm_screening_design_ready", notes


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})
