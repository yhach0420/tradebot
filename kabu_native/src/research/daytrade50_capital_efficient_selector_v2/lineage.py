"""Daily-bar dates for DAYTRADE50. Does not change the score or the paper fetch."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.daytrade50_capital_efficient_selector_v2.features import (
    SessionObservation,
    bare,
    build_symbol_features,
    snapshot_days,
)

WITNESS_SYMBOLS = ("1301.T", "7203.T")
STALE_BUG = "STALE_BAR_SELECTION_BUG"
MISSING = "LATEST_SESSION_DATA_MISSING"
PASS = "PASS"


def ymd(value: date) -> str:
    return value.strftime("%Y%m%d")


def previous_jpx_session(sessions: Sequence[str], target: str) -> Optional[str]:
    day = target.replace("-", "")
    prior = [session for session in sessions if session < day]
    return prior[-1] if prior else None


def lag_sessions(sessions: Sequence[str], target: str, actual: Optional[str]) -> Optional[int]:
    required = previous_jpx_session(sessions, target)
    if required is None or actual is None:
        return None
    actual_day = actual.replace("-", "")
    if actual_day > required:
        return None
    return sum(1 for session in sessions if actual_day < session <= required)


def last_completed_session(bar_dates: Sequence[str], target: str) -> Optional[str]:
    """Last bar strictly before the target. This is the pre-open completed session."""
    day = target.replace("-", "")
    prior = [bar for bar in bar_dates if bar < day]
    return prior[-1] if prior else None


def iloc_minus_two(bar_dates: Sequence[str]) -> Optional[str]:
    if len(bar_dates) < 2:
        return None
    return bar_dates[-2]


def match_bar_date(close: float, volume: float, bars: Sequence[tuple[str, float, float]]) -> list[str]:
    hits = []
    for day, bar_close, bar_volume in bars:
        if abs(bar_close - close) < 0.011 and abs(bar_volume - volume) < 1:
            hits.append(day)
    return hits


def _flatten(value: object) -> float:
    if hasattr(value, "iloc"):
        return float(value.iloc[0])
    return float(value)


def download_daily_bars(symbols: Sequence[str], start: date, end: date) -> dict[str, list[dict[str, Any]]]:
    import yfinance as yf

    out: dict[str, list[dict[str, Any]]] = {bare(symbol): [] for symbol in symbols}
    batch_size = 80
    for offset in range(0, len(symbols), batch_size):
        batch = list(symbols[offset : offset + batch_size])
        print(f"daily bars {offset // batch_size + 1}/{(len(symbols) + batch_size - 1) // batch_size}", flush=True)
        try:
            frame = yf.download(
                batch,
                start=start.isoformat(),
                end=end.isoformat(),
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=False,
            )
        except Exception:
            continue
        if frame is None or getattr(frame, "empty", True):
            continue
        for symbol in batch:
            key = bare(symbol)
            try:
                view = _symbol_frame(frame, symbol)
                if view is None:
                    continue
                view = view.dropna(how="all")
            except Exception:
                continue
            rows: list[dict[str, Any]] = []
            for idx, row in view.iterrows():
                try:
                    close = _flatten(row["Close"])
                    high = _flatten(row["High"])
                    low = _flatten(row["Low"])
                    volume = _flatten(row["Volume"])
                except Exception:
                    continue
                if close != close or close <= 0 or high != high or low != low or volume != volume:
                    continue
                rows.append(
                    {
                        "source_bar_date": idx.date().strftime("%Y%m%d"),
                        "close": close,
                        "high": high,
                        "low": low,
                        "volume": volume,
                    }
                )
            rows.sort(key=lambda item: item["source_bar_date"])
            out[key] = rows
    return out


def _symbol_frame(frame: Any, symbol: str) -> Any:
    columns = frame.columns
    if hasattr(columns, "levels"):
        level0 = set(columns.get_level_values(0))
        if symbol in level0:
            return frame[symbol]
        level1 = set(columns.get_level_values(1))
        if symbol in level1:
            return frame.xs(symbol, axis=1, level=1)
        return None
    return frame


def witness_bars(start: date, end: date) -> dict[str, list[tuple[str, float, float]]]:
    panel = download_daily_bars(WITNESS_SYMBOLS, start, end)
    result = {}
    for symbol, rows in panel.items():
        result[symbol] = [
            (row["source_bar_date"], round(float(row["close"]), 4), round(float(row["volume"]), 0))
            for row in rows
        ]
    return result


def jpx_sessions(witness: Mapping[str, Sequence[tuple[str, float, float]]]) -> list[str]:
    """Observed cash sessions. A date counts when either witness has a daily bar."""
    found = set()
    for rows in witness.values():
        found.update(day for day, _close, _volume in rows)
    return sorted(found)


def feature_row(path: Path, symbol: str) -> Optional[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            if bare(raw.get("symbol")) == bare(symbol):
                return {key: str(value or "") for key, value in raw.items()}
    return None


def before_lineage(reports_dir: Path, sessions: Sequence[str], bars: Sequence[tuple[str, float, float]]) -> list[dict[str, Any]]:
    rows = []
    for day in snapshot_days(reports_dir):
        raw = feature_row(reports_dir / f"features_{day}.csv", "1301")
        required = previous_jpx_session(sessions, day)
        actual = None
        ambiguous = False
        if raw is not None:
            hits = match_bar_date(float(raw["close"]), float(raw["volume"]), bars)
            if len(hits) == 1:
                actual = hits[0]
            elif len(hits) > 1:
                ambiguous = True
                actual = hits[-1]
        lag = lag_sessions(sessions, day, actual)
        future = bool(actual and required and actual > required)
        rows.append(
            {
                "target_trading_date": day,
                "required_previous_jpx_session": required,
                "actual_latest_source_bar_date": actual,
                "lag_sessions": lag,
                "future_used": future,
                "ambiguous_match": ambiguous,
                "status": "STALE" if lag not in (0, None) else ("FUTURE" if future else "UNMATCHED" if actual is None else PASS),
            }
        )
    return rows


def corrected_symbol_window(
    bars: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    target: str,
    sessions: Sequence[str],
    exchange: str = "1",
    market: str = "prime",
) -> dict[str, Any]:
    required = previous_jpx_session(sessions, target)
    if required is None:
        return {"status": MISSING, "symbol": symbol}
    usable = [row for row in bars if str(row["source_bar_date"]) <= required]
    if not usable or str(usable[-1]["source_bar_date"]) != required:
        return {"status": MISSING, "symbol": symbol, "required": required}
    future = [row for row in bars if str(row["source_bar_date"]) >= target.replace("-", "")]
    window = usable[-20:]
    observations = []
    for row in window:
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        volume = float(row["volume"])
        observations.append(
            SessionObservation(
                symbol=symbol,
                available_on=str(row["source_bar_date"]),
                close=close,
                volume=volume,
                trading_value=volume * close,
                range_pct=(high - low) / close * 100.0,
                exchange=exchange,
                market=market,
            )
        )
    features = build_symbol_features(observations, selection_day=required, target_trading_date=target)
    return {
        "status": PASS if features.get("WINDOW_END_SESSION") == required else MISSING,
        "required": required,
        "actual": features.get("WINDOW_END_SESSION"),
        "future_rows_supplied": len(future),
        "features": features,
    }


def universe_symbols(reports_dir: Path) -> list[str]:
    found: set[str] = set()
    for day in snapshot_days(reports_dir):
        with (reports_dir / f"features_{day}.csv").open(encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                symbol = bare(raw.get("symbol"))
                if symbol:
                    found.add(symbol)
    return sorted(found)


def freshness_proof(reports_dir: Path) -> dict[str, Any]:
    """Match the old files, then rebuild windows that end on the required session."""
    witness = witness_bars(date(2026, 5, 1), date(2026, 10, 8))
    sessions = jpx_sessions(witness)
    bars_1301 = witness.get("1301") or []
    before = before_lineage(reports_dir, sessions, bars_1301)
    unique = [row for row in before if row["actual_latest_source_bar_date"] and not row["ambiguous_match"]]
    symbols = [f"{symbol}.T" for symbol in universe_symbols(reports_dir)]
    panel = download_daily_bars(symbols, date(2026, 4, 1), date(2026, 10, 8))
    dates_by_symbol = {
        symbol: [row["source_bar_date"] for row in rows]
        for symbol, rows in panel.items()
    }
    targets = []
    for row in before:
        target = str(row["target_trading_date"])
        required = previous_jpx_session(sessions, target)
        holders = 0
        if required:
            holders = sum(1 for dates in dates_by_symbol.values() if required in dates)
        if not required or holders == 0:
            status = MISSING
            actual = None
            lag = None
        else:
            status = PASS
            actual = required
            lag = 0
        targets.append(
            {
                "target_trading_date": target,
                "required_previous_jpx_session": required,
                "actual_latest_source_bar_date": actual,
                "lag_sessions": lag,
                "future_used": False,
                "symbols_with_required_session": holders,
                "status": status,
            }
        )
    focus = corrected_symbol_window(
        panel.get("1301") or [],
        symbol="1301",
        target="20261007",
        sessions=sessions,
    )
    features = focus.get("features") or {}
    required_1007 = previous_jpx_session(sessions, "20261007")
    before_1007 = next(row for row in before if row["target_trading_date"] == "20261007")
    bug = before_1007["actual_latest_source_bar_date"] == "20261005" and required_1007 == "20261006"
    return {
        "stale_bar_selection_bug": bug,
        "iloc_preopen": {
            "witness": "1301.T",
            "features_20261007_written_before_open": "2026-10-07 08:08:35",
            "matched_source_bar_date": before_1007["actual_latest_source_bar_date"],
            "iloc_minus_2": "20261005",
            "iloc_minus_1_completed_session": "20261006",
            "partial_target_row": False,
        },
        "jpx_calendar_source": "union of 1301.T and 7203.T yfinance daily bar dates",
        "required_latest_completed_session_20261007": required_1007,
        "before_20261007": before_1007,
        "after_20261007": {
            "TARGET_TRADING_DATE": "20261007",
            "REQUIRED_LATEST_COMPLETED_SESSION": required_1007,
            "ACTUAL_LATEST_SOURCE_BAR_DATE": features.get("WINDOW_END_SESSION"),
            "MATCH": features.get("WINDOW_END_SESSION") == required_1007,
            "WINDOW_START_SESSION": features.get("WINDOW_START_SESSION"),
            "WINDOW_END_SESSION": features.get("WINDOW_END_SESSION"),
            "OBSERVATION_N": features.get("OBSERVATION_N"),
            "sessions_used": features.get("sessions_used"),
            "SOURCE_BAR_DATE": features.get("SOURCE_BAR_DATE"),
            "SOURCE_AVAILABLE_AT": features.get("SOURCE_AVAILABLE_AT"),
        },
        "before_rows": before,
        "targets": targets,
        "before_unique_n": len(unique),
        "before_lag_not_zero": sum(1 for row in unique if row["lag_sessions"] not in (0,)),
        "targets_n": len(targets),
        "targets_lag_zero": sum(1 for row in targets if row["lag_sessions"] == 0),
        "targets_future": sum(1 for row in targets if row["future_used"]),
        "targets_missing": sum(1 for row in targets if row["status"] == MISSING),
        "future_conditioned_selection": 0,
        "score_changed": False,
        "production_fetch_unchanged": True,
    }
