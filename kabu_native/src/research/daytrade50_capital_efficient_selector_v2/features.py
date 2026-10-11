"""Causal 5D/20D daytrade features.

features_YYYYMMDD.csv is the morning artifact for target trading date YYYYMMDD.
The runner stamps that date into the trade_date column. The OHLCV is
fetch_previous_day_yfinance iloc[-2], and these files are written before 09:00.
The filename is not the completed-session date. A target date D uses only
artifacts whose filename is strictly before D.
"""
from __future__ import annotations

import csv
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

from research.auto50_research_capture.mode import tick_yen

MARKETS = {"prime", "standard", "growth"}
EXCLUDED_SELECTION_DATES = frozenset({"20260929", "20260930", "20261002", "20261005"})


@dataclass(frozen=True)
class SessionObservation:
    symbol: str
    available_on: str
    close: float
    volume: float
    trading_value: float
    range_pct: float
    exchange: str = "1"
    market: str = "prime"
    invalid_reason: str = ""


def bare(symbol: object) -> str:
    text = str(symbol or "").strip().upper()
    if text.endswith(".T"):
        text = text[:-2]
    if "@" in text:
        text = text.split("@", 1)[0]
    return text


def _num(value: object) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def median(values: Sequence[float]) -> float:
    return float(statistics.median(values))


def percentile_high_is_good(pairs: Sequence[tuple[str, float]]) -> dict[str, float]:
    """Deterministic 0..1 percentile. Ties share the average rank. Symbol orders ties."""
    ordered = sorted(pairs, key=lambda item: (item[1], item[0]))
    count = len(ordered)
    if count == 0:
        return {}
    if count == 1:
        return {ordered[0][0]: 1.0}
    out: dict[str, float] = {}
    index = 0
    while index < count:
        end = index
        while end + 1 < count and ordered[end + 1][1] == ordered[index][1]:
            end += 1
        average = sum(range(index, end + 1)) / (end - index + 1)
        percent = average / (count - 1)
        for cursor in range(index, end + 1):
            out[ordered[cursor][0]] = percent
        index = end + 1
    return out


def execution_suitability(close: float) -> Optional[float]:
    """Higher is better. tick/price is suitability, not an alpha boost for cheap names."""
    if close <= 0 or not math.isfinite(close):
        return None
    ratio = tick_yen(close) / close
    if not math.isfinite(ratio) or ratio <= 0:
        return None
    return -ratio


def build_symbol_features(
    observations: Sequence[SessionObservation],
    *,
    selection_day: str,
    target_trading_date: Optional[str] = None,
) -> dict[str, object]:
    usable = [row for row in observations if row.available_on <= selection_day]
    usable.sort(key=lambda row: row.available_on)
    symbol = bare(observations[0].symbol) if observations else ""
    base: dict[str, object] = {
        "symbol": symbol,
        "selection_day": selection_day,
        "sessions_used": [row.available_on for row in usable[-20:]],
        "eligible": False,
        "reject": "",
    }
    if any(row.available_on > selection_day for row in observations):
        base["future_rows_ignored"] = sum(1 for row in observations if row.available_on > selection_day)
    if len(usable) < 20:
        base["reject"] = "INSUFFICIENT_HISTORY"
        return base
    window = usable[-20:]
    if window[-1].available_on != selection_day:
        base["reject"] = "PRIOR_SESSION_MISSING"
        return base
    latest = window[-1]
    if latest.invalid_reason:
        base["reject"] = "KNOWN_INVALID"
        return base
    if latest.exchange != "1" or latest.market.strip().lower() not in MARKETS:
        base["reject"] = "NOT_JPX_TRADABLE"
        return base
    if latest.close <= 0 or any(row.close <= 0 or row.range_pct < 0 for row in window):
        base["reject"] = "PRICE_INVALID"
        return base
    last5 = window[-5:]
    tv5 = median([row.trading_value for row in last5])
    tv20 = median([row.trading_value for row in window])
    vol5 = median([row.volume for row in last5])
    vol20 = median([row.volume for row in window])
    range5 = median([row.range_pct for row in last5])
    range20 = median([row.range_pct for row in window])
    suitability = execution_suitability(latest.close)
    if min(tv5, tv20) <= 0 or suitability is None:
        base["reject"] = "ILLIQUID_OR_UNTICKABLE"
        return base
    base.update(
        {
            "eligible": True,
            "reject": "",
            "prior_close": latest.close,
            "one_lot_notional": round(latest.close * 100.0, 2),
            "tv_5d_median": tv5,
            "tv_20d_median": tv20,
            "volume_5d_median": vol5,
            "volume_20d_median": vol20,
            "range_pct_5d_median": range5,
            "range_pct_20d_median": range20,
            "liquidity_feature": min(tv5, tv20),
            "suitability_feature": suitability,
            "tick_ratio": -suitability,
            "latest_available_on": latest.available_on,
            "future_rows_ignored": int(base.get("future_rows_ignored") or 0),
            "SOURCE_BAR_DATE": latest.available_on,
            "SOURCE_AVAILABLE_AT": _session_complete_stamp(latest.available_on),
            "TARGET_TRADING_DATE": target_trading_date or "",
            "WINDOW_END_SESSION": window[-1].available_on,
            "WINDOW_START_SESSION": window[0].available_on,
            "OBSERVATION_N": len(window),
        }
    )
    return base


def _session_complete_stamp(day: str) -> str:
    if len(day) == 8 and day.isdigit() and day[4:6] != "00":
        return f"{day[:4]}-{day[4:6]}-{day[6:8]}T15:30:00+09:00"
    return ""


def load_feature_file(path: Path) -> list[SessionObservation]:
    day = path.stem.replace("features_", "")
    rows: list[SessionObservation] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            symbol = bare(raw.get("symbol"))
            close = _num(raw.get("close"))
            volume = _num(raw.get("volume"))
            trading_value = _num(raw.get("trading_value"))
            range_pct = _num(raw.get("atr_pct"))
            if not symbol or close is None or volume is None or trading_value is None or range_pct is None:
                continue
            rows.append(
                SessionObservation(
                    symbol=symbol,
                    available_on=day,
                    close=close,
                    volume=volume,
                    trading_value=trading_value,
                    range_pct=range_pct,
                    exchange=str(raw.get("exchange") or ""),
                    market=str(raw.get("market") or ""),
                    invalid_reason=str(raw.get("invalid_reason") or "").strip(),
                )
            )
    return rows


def snapshot_days(reports_dir: Path) -> list[str]:
    days = []
    for path in sorted(reports_dir.glob("features_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].csv")):
        days.append(path.stem.replace("features_", ""))
    return days


def selection_calendar(reports_dir: Path) -> list[str]:
    """Days that may be audited. Excluded dates remain usable as completed history."""
    return [day for day in snapshot_days(reports_dir) if day not in EXCLUDED_SELECTION_DATES]


def window_for_target(days: Sequence[str], target_trading_date: str, lookback: int = 20) -> list[str]:
    """Artifact dates strictly before the target. The target-dated file is excluded."""
    usable = [day for day in days if day < target_trading_date]
    return usable[-lookback:]


def load_window(reports_dir: Path, selection_day: str, lookback: int = 20) -> list[SessionObservation]:
    chosen = window_for_target(snapshot_days(reports_dir), selection_day, lookback)
    rows: list[SessionObservation] = []
    for day in chosen:
        rows.extend(load_feature_file(reports_dir / f"features_{day}.csv"))
    return rows


def group_features(observations: Iterable[SessionObservation], *, selection_day: str) -> list[dict[str, object]]:
    by_symbol: dict[str, list[SessionObservation]] = {}
    for row in observations:
        by_symbol.setdefault(row.symbol, []).append(row)
    return [build_symbol_features(rows, selection_day=selection_day) for _, rows in sorted(by_symbol.items())]
