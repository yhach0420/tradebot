"""
Phase 108: Opening dynamic50 screen — previous-day + intraday opening features (shadow only).
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
CHECKPOINTS = ("09:05", "09:10", "09:15", "09:20")
OPEN_WINDOW_START = time(9, 0)
PUSH_LIMIT = 50


def _as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm_symbol(code: str) -> str:
    c = code.strip().upper().split("@")[0].replace(".T", "")
    return f"{c}.T" if c else ""


def volatility_liquidity_score(atr_pct: Optional[float], trading_value_jpy: Optional[float]) -> Optional[float]:
    if atr_pct is None or trading_value_jpy is None or trading_value_jpy <= 0:
        return None
    return round(float(atr_pct) * math.log10(max(float(trading_value_jpy), 1.0)), 6)


def rank_normalize(values: dict[str, Optional[float]]) -> dict[str, float]:
    """Percentile rank 0..1; None -> 0."""
    valid = [(s, v) for s, v in values.items() if v is not None and not math.isnan(v)]
    if not valid:
        return {s: 0.0 for s in values}
    valid.sort(key=lambda x: x[1])
    n = len(valid)
    out: dict[str, float] = {s: 0.0 for s in values}
    for i, (sym, _) in enumerate(valid):
        out[sym] = i / max(n - 1, 1)
    return out


@dataclass
class PreviousDayFeatures:
    symbol: str
    close: Optional[float] = None
    volume: Optional[float] = None
    trading_value: Optional[float] = None
    atr_pct: Optional[float] = None
    intraday_range_pct: Optional[float] = None
    volume_surge_5: Optional[float] = None
    volatility_liquidity_score: Optional[float] = None
    data_source: str = ""


@dataclass
class OpeningWindowFeatures:
    symbol: str
    checkpoint: str
    price_change_pct: Optional[float] = None
    range_pct: Optional[float] = None
    volume_5m: Optional[float] = None
    trading_value_proxy: Optional[float] = None
    gap_pct: Optional[float] = None
    early_momentum_score: Optional[float] = None
    has_push_snapshot: bool = False


@dataclass
class ScreenedSymbol:
    symbol: str
    exchange: int = 1
    symbol_key: str = ""
    market: str = ""
    previous_day_vol_liq_score: Optional[float] = None
    opening_daytrade_score: Optional[float] = None
    rank: int = 0
    previous_day: Optional[PreviousDayFeatures] = None
    opening: Optional[OpeningWindowFeatures] = None


def parse_recorded_at(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=JST)


def load_push_window_first_last(
    push_day_dir: Path,
    *,
    cutoff_hhmm: str,
    window_start: time = OPEN_WINDOW_START,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """First and last payload per symbol in [window_start, cutoff] on trade date."""
    hh, mm = map(int, cutoff_hhmm.split(":"))
    cutoff = time(hh, mm, 59)
    trade_d: Optional[date] = None
    try:
        trade_d = date.fromisoformat(push_day_dir.name)
    except ValueError:
        pass

    first: dict[str, dict[str, Any]] = {}
    last: dict[str, dict[str, Any]] = {}
    if not push_day_dir.is_dir():
        return first, last

    for path in push_day_dir.glob("*.jsonl"):
        sym = _norm_symbol(path.stem)
        rows: list[tuple[datetime, dict[str, Any]]] = []
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
                dt = parse_recorded_at(rec)
                local = dt.astimezone(JST)
                if trade_d and local.date() != trade_d:
                    continue
                t = local.time()
                if t < window_start or t > cutoff:
                    continue
                payload = row.get("payload")
                if isinstance(payload, dict):
                    rows.append((dt, payload))
        if rows:
            rows.sort(key=lambda x: x[0])
            first[sym] = rows[0][1]
            last[sym] = rows[-1][1]
    return first, last


def load_push_snapshots_by_cutoff(
    push_day_dir: Path,
    *,
    cutoff_hhmm: str,
    window_start: time = OPEN_WINDOW_START,
) -> dict[str, dict[str, Any]]:
    """Last PUSH payload per symbol with recorded_at <= cutoff (JST) on trade date."""
    hh, mm = map(int, cutoff_hhmm.split(":"))
    cutoff = time(hh, mm, 59)
    trade_d: Optional[date] = None
    if push_day_dir.name[:4].isdigit():
        try:
            trade_d = date.fromisoformat(push_day_dir.name)
        except ValueError:
            pass

    per_sym: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
    if not push_day_dir.is_dir():
        return {}

    for path in push_day_dir.glob("*.jsonl"):
        sym = _norm_symbol(path.stem)
        rows: list[tuple[datetime, dict[str, Any]]] = []
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
                dt = parse_recorded_at(rec)
                local = dt.astimezone(JST)
                if trade_d and local.date() != trade_d:
                    continue
                t = local.time()
                if t < window_start or t > cutoff:
                    continue
                payload = row.get("payload")
                if isinstance(payload, dict):
                    rows.append((dt, payload))
        if rows:
            per_sym[sym] = rows

    out: dict[str, dict[str, Any]] = {}
    for sym, rows in per_sym.items():
        rows.sort(key=lambda x: x[0])
        out[sym] = rows[-1][1]
    return out


def opening_features_from_push(
    symbol: str,
    payload: Mapping[str, Any],
    *,
    checkpoint: str,
    first_payload: Optional[Mapping[str, Any]] = None,
) -> OpeningWindowFeatures:
    prev_close = _as_float(payload.get("PreviousClose"))
    cur = _as_float(payload.get("CurrentPrice")) or _as_float(payload.get("CalcPrice"))
    op = _as_float(payload.get("OpeningPrice"))
    hi = _as_float(payload.get("HighPrice")) or cur
    lo = _as_float(payload.get("LowPrice")) or cur
    tv = _as_float(payload.get("TradingValue"))
    vol = _as_float(payload.get("TradingVolume"))
    chg_pct = _as_float(payload.get("ChangePreviousClosePer"))

    open_px = op
    if first_payload and open_px is None:
        open_px = _as_float(first_payload.get("OpeningPrice")) or _as_float(
            first_payload.get("CurrentPrice")
        )
    if open_px is None:
        open_px = _as_float(first_payload.get("CurrentPrice")) if first_payload else cur

    price_change_pct = None
    if open_px and cur and open_px > 0:
        price_change_pct = round((cur - open_px) / open_px * 100.0, 6)

    range_pct = None
    if prev_close and hi is not None and lo is not None and prev_close > 0:
        range_pct = round((hi - lo) / prev_close * 100.0, 6)

    gap_pct = chg_pct
    if gap_pct is None and prev_close and (op or cur) and prev_close > 0:
        ref = op or cur
        gap_pct = round((ref - prev_close) / prev_close * 100.0, 6)

    vol_5m = vol
    if first_payload:
        v0 = _as_float(first_payload.get("TradingVolume"))
        if vol is not None and v0 is not None and vol >= v0:
            vol_5m = vol - v0

    mom = 0.0
    n = 0
    for v in (abs(gap_pct or 0), abs(price_change_pct or 0), range_pct or 0):
        if v > 0:
            mom += v
            n += 1
    early_momentum_score = round(mom / max(n, 1), 6) if n else None

    return OpeningWindowFeatures(
        symbol=symbol,
        checkpoint=checkpoint,
        price_change_pct=price_change_pct,
        range_pct=range_pct,
        volume_5m=vol_5m,
        trading_value_proxy=tv,
        gap_pct=gap_pct,
        early_momentum_score=early_momentum_score,
        has_push_snapshot=True,
    )


def fetch_previous_day_yfinance(
    symbols: Sequence[str],
    trade_date: date,
    *,
    lookback_days: int = 12,
) -> dict[str, PreviousDayFeatures]:
    """T-1 daily features via yfinance (shadow design fetch; not persisted)."""
    try:
        import yfinance as yf
    except ImportError:
        return {}

    prev = trade_date
    from datetime import timedelta

    start = trade_date - timedelta(days=lookback_days)
    end = trade_date + timedelta(days=1)
    tickers = [s.replace(".T", ".T") for s in symbols]
    out: dict[str, PreviousDayFeatures] = {}

    chunk = 80
    for i in range(0, len(tickers), chunk):
        batch = tickers[i : i + chunk]
        try:
            data = yf.download(
                batch,
                start=start.isoformat(),
                end=end.isoformat(),
                interval="1d",
                group_by="ticker",
                progress=False,
                threads=False,
                auto_adjust=False,
            )
        except Exception:
            continue
        if data is None or data.empty:
            continue
        import pandas as pd

        for sym in batch:
            try:
                if len(batch) == 1:
                    df = data.copy()
                    if isinstance(df.columns, pd.MultiIndex):
                        df = data.xs(sym, axis=1, level=0)
                elif isinstance(data.columns, pd.MultiIndex):
                    lvl0 = set(data.columns.get_level_values(0))
                    if sym in lvl0:
                        df = data.xs(sym, axis=1, level=0)
                    elif sym in data.columns.get_level_values(1):
                        df = data.xs(sym, axis=1, level=1)
                    else:
                        continue
                else:
                    if sym not in data.columns:
                        continue
                    df = data[sym]
                if df is None or df.empty or len(df) < 2:
                    continue
                df = df.dropna(how="all")
                if len(df) < 2:
                    continue
                row = df.iloc[-2]
                o, h, l, c = map(_as_float, (row.get("Open"), row.get("High"), row.get("Low"), row.get("Close")))
                if c is None or c <= 0:
                    continue
                atr_pct = None
                if h is not None and l is not None:
                    atr_pct = round((h - l) / c * 100.0, 6)
                vol = _as_float(row.get("Volume")) or 0.0
                tv = vol * c if vol and c else None
                vols = [_as_float(df.iloc[j].get("Volume")) or 0.0 for j in range(max(0, len(df) - 6), len(df) - 1)]
                avg5 = statistics.mean(vols) if vols else None
                surge = round(vol / avg5, 6) if avg5 and avg5 > 0 and vol else None
                vl = volatility_liquidity_score(atr_pct, tv)
                ysym = _norm_symbol(sym)
                out[ysym] = PreviousDayFeatures(
                    symbol=ysym,
                    close=c,
                    volume=vol if vol else None,
                    trading_value=tv,
                    atr_pct=atr_pct,
                    intraday_range_pct=atr_pct,
                    volume_surge_5=surge,
                    volatility_liquidity_score=vl,
                    data_source="yfinance_daily",
                )
            except Exception:
                continue
    return out


def compute_opening_daytrade_scores(
    symbols: Sequence[str],
    prev_by_sym: Mapping[str, PreviousDayFeatures],
    opening_by_sym: Mapping[str, OpeningWindowFeatures],
) -> dict[str, float]:
    prev_vl = {s: (prev_by_sym[s].volatility_liquidity_score if s in prev_by_sym else None) for s in symbols}
    mom = {s: (opening_by_sym[s].early_momentum_score if s in opening_by_sym else None) for s in symbols}
    tv = {s: (opening_by_sym[s].trading_value_proxy if s in opening_by_sym else None) for s in symbols}
    rng = {s: (opening_by_sym[s].range_pct if s in opening_by_sym else None) for s in symbols}

    n_prev = rank_normalize(prev_vl)
    n_mom = rank_normalize(mom)
    n_tv = rank_normalize(tv)
    n_rng = rank_normalize(rng)

    scores: dict[str, float] = {}
    for s in symbols:
        scores[s] = round(
            0.35 * n_prev.get(s, 0.0)
            + 0.25 * n_mom.get(s, 0.0)
            + 0.20 * n_tv.get(s, 0.0)
            + 0.20 * n_rng.get(s, 0.0),
            6,
        )
    return scores


def select_top50(
    scores: Mapping[str, float],
    *,
    prev_by_sym: Mapping[str, PreviousDayFeatures],
    opening_by_sym: Mapping[str, OpeningWindowFeatures],
    symbol_meta: Mapping[str, Mapping[str, Any]],
) -> list[ScreenedSymbol]:
    ranked = sorted(scores.keys(), key=lambda s: scores.get(s, 0.0), reverse=True)[:PUSH_LIMIT]
    out: list[ScreenedSymbol] = []
    for i, sym in enumerate(ranked, start=1):
        m = symbol_meta.get(sym, {})
        prev = prev_by_sym.get(sym)
        op = opening_by_sym.get(sym)
        out.append(
            ScreenedSymbol(
                symbol=sym,
                exchange=int(m.get("exchange") or 1),
                symbol_key=str(m.get("symbol_key") or f"{sym.replace('.T', '')}@1"),
                market=str(m.get("market") or ""),
                previous_day_vol_liq_score=prev.volatility_liquidity_score if prev else None,
                opening_daytrade_score=scores.get(sym),
                rank=i,
                previous_day=prev,
                opening=op,
            )
        )
    return out


def churn_between(
    before: Sequence[str],
    after: Sequence[str],
) -> dict[str, Any]:
    b, a = set(before), set(after)
    added = sorted(a - b)
    removed = sorted(b - a)
    stayed = sorted(b & a)
    n = max(len(b), 1)
    return {
        "added_symbols": added,
        "removed_symbols": removed,
        "stayed_symbols": stayed,
        "added_count": len(added),
        "removed_count": len(removed),
        "stayed_count": len(stayed),
        "churn_rate": round((len(added) + len(removed)) / (2 * n), 4),
    }
