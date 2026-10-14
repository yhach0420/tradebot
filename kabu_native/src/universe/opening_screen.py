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
    source_bar_date: str = ""
    source_available_at: str = ""


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


WITNESS_SYMBOLS = ("1301.T", "7203.T")
SESSION_AVAILABLE = time(15, 30)


def session_available_at(day_ymd: str) -> datetime:
    """A cash daily bar is confirmed at 15:30 JST on its session date."""
    day = date(int(day_ymd[:4]), int(day_ymd[4:6]), int(day_ymd[6:8]))
    return datetime.combine(day, SESSION_AVAILABLE, tzinfo=JST)


def confirmed_session(sessions: Sequence[str], target_ymd: str, as_of: datetime) -> Optional[str]:
    """Latest observed session strictly before the target whose bar is already available."""
    target = target_ymd.replace("-", "")
    prior = [
        session
        for session in sorted({item.replace("-", "") for item in sessions})
        if session < target and session_available_at(session) <= as_of
    ]
    return prior[-1] if prior else None


def select_confirmed_bar(
    bars: Sequence[Mapping[str, Any]],
    *,
    required: str,
    target_ymd: str,
    as_of: datetime,
) -> tuple[str, Optional[Mapping[str, Any]]]:
    """Use the required session. An older bar is a retreat, not a substitute."""
    target = target_ymd.replace("-", "")
    eligible = []
    for bar in bars:
        day = str(bar.get("source_bar_date") or "").replace("-", "")
        if len(day) != 8 or day >= target:
            continue
        if session_available_at(day) > as_of:
            continue
        eligible.append(bar)
    eligible.sort(key=lambda item: str(item["source_bar_date"]))
    if not eligible:
        return "LATEST_SESSION_DATA_MISSING", None
    newest = str(eligible[-1]["source_bar_date"]).replace("-", "")
    if newest < required:
        return "STALE_BAR", None
    if newest != required:
        return "CALENDAR_MISMATCH", None
    return "", eligible[-1]


def _bar_ymd(index: Any) -> str:
    stamp = index
    if getattr(stamp, "tzinfo", None) is not None:
        stamp = stamp.tz_convert(JST)
    return stamp.strftime("%Y%m%d")


def _frame_for_symbol(data: Any, sym: str, batch_size: int) -> Any:
    import pandas as pd

    if batch_size == 1:
        frame = data.copy()
        if isinstance(frame.columns, pd.MultiIndex):
            return data.xs(sym, axis=1, level=0)
        return frame
    if isinstance(data.columns, pd.MultiIndex):
        level0 = set(data.columns.get_level_values(0))
        if sym in level0:
            return data.xs(sym, axis=1, level=0)
        level1 = set(data.columns.get_level_values(1))
        if sym in level1:
            return data.xs(sym, axis=1, level=1)
        return None
    if sym not in data.columns:
        return None
    return data[sym]


def _bars_from_frame(frame: Any) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    frame = frame.dropna(how="all")
    bars: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        close = _as_float(row.get("Close"))
        high = _as_float(row.get("High"))
        low = _as_float(row.get("Low"))
        volume = _as_float(row.get("Volume"))
        if close is None or close <= 0 or high is None or low is None or volume is None:
            continue
        bars.append(
            {
                "source_bar_date": _bar_ymd(index),
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    bars.sort(key=lambda item: item["source_bar_date"])
    return bars


def _feature_from_bars(symbol: str, bars: Sequence[Mapping[str, Any]], required: str) -> Optional[PreviousDayFeatures]:
    chosen = [bar for bar in bars if str(bar["source_bar_date"]) <= required]
    if not chosen or str(chosen[-1]["source_bar_date"]) != required:
        return None
    row = chosen[-1]
    close = float(row["close"])
    high = float(row["high"])
    low = float(row["low"])
    volume = float(row["volume"])
    atr_pct = round((high - low) / close * 100.0, 6)
    trading_value = volume * close if volume and close else None
    recent = [float(bar["volume"]) for bar in chosen[-5:]]
    average = statistics.mean(recent) if recent else None
    surge = round(volume / average, 6) if average and average > 0 else None
    return PreviousDayFeatures(
        symbol=symbol,
        close=close,
        volume=volume,
        trading_value=trading_value,
        atr_pct=atr_pct,
        intraday_range_pct=atr_pct,
        volume_surge_5=surge,
        volatility_liquidity_score=volatility_liquidity_score(atr_pct, trading_value),
        data_source="yfinance_daily",
        source_bar_date=required,
        source_available_at=session_available_at(required).isoformat(),
    )


def fetch_confirmed_daily(
    symbols: Sequence[str],
    trade_date: date,
    *,
    as_of: Optional[datetime] = None,
    lookback_days: int = 12,
) -> tuple[dict[str, PreviousDayFeatures], str, str]:
    """Previous JPX session for trade_date. Does not use a bar on or after that date."""
    try:
        import yfinance as yf
    except ImportError:
        return {}, "", "YFINANCE_MISSING"
    when = as_of or datetime.now(JST)
    if when.tzinfo is None:
        when = when.replace(tzinfo=JST)
    from datetime import timedelta

    target = trade_date.strftime("%Y%m%d")
    start = trade_date - timedelta(days=lookback_days)
    end = trade_date + timedelta(days=1)
    requested = [_norm_symbol(symbol) for symbol in symbols if _norm_symbol(symbol)]
    tickers = list(dict.fromkeys([*WITNESS_SYMBOLS, *requested]))
    panels: dict[str, list[dict[str, Any]]] = {}
    for offset in range(0, len(tickers), 80):
        batch = tickers[offset : offset + 80]
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
        if data is None or getattr(data, "empty", True):
            continue
        for sym in batch:
            try:
                panels[_norm_symbol(sym)] = _bars_from_frame(_frame_for_symbol(data, sym, len(batch)))
            except Exception:
                continue
    sessions: set[str] = set()
    for symbol in WITNESS_SYMBOLS:
        sessions.update(bar["source_bar_date"] for bar in panels.get(symbol) or [])
    required = confirmed_session(sorted(sessions), target, when)
    if not required:
        return {}, "", "NO_CONFIRMED_SESSION"
    if required >= target:
        return {}, required, "FUTURE_BAR"
    out: dict[str, PreviousDayFeatures] = {}
    for symbol in requested:
        reason, _bar = select_confirmed_bar(
            panels.get(symbol) or [],
            required=required,
            target_ymd=target,
            as_of=when,
        )
        if reason:
            continue
        feature = _feature_from_bars(symbol, panels.get(symbol) or [], required)
        if feature is not None:
            out[symbol] = feature
    return out, required, ""


def fetch_previous_day_yfinance(
    symbols: Sequence[str],
    trade_date: date,
    *,
    lookback_days: int = 12,
    as_of: Optional[datetime] = None,
) -> dict[str, PreviousDayFeatures]:
    """Confirmed previous cash session. An older bar is not used as a substitute."""
    features, _required, _reason = fetch_confirmed_daily(
        symbols,
        trade_date,
        as_of=as_of,
        lookback_days=lookback_days,
    )
    return features


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
