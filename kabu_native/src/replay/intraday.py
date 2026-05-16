"""
Intraday 1m CSV loading for kabu_native replay.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

REQUIRED_OHLCV = frozenset({"open", "high", "low", "close", "volume"})


@dataclass(frozen=True)
class IntradayLoadResult:
    ok: bool
    df: pd.DataFrame | None
    skip_reason: str | None
    csv_path: Path | None


def yahoo_csv_filename(symbol: str) -> str:
    s = symbol.strip().upper()
    if s.endswith(".T"):
        return f"{s}.csv"
    if s.endswith(".CSV"):
        return s
    return f"{s}.T.csv"


def resolve_intraday_csv(
    data_roots: list[Path],
    trade_date: str,
    symbol: str,
) -> Path | None:
    fname = yahoo_csv_filename(symbol)
    for root in data_roots:
        candidate = root / trade_date / fname
        if candidate.is_file():
            return candidate
    return None


def load_intraday_csv(path: Path) -> IntradayLoadResult:
    if not path.is_file():
        return IntradayLoadResult(False, None, "missing_intraday_csv", None)

    try:
        raw = pd.read_csv(path)
    except Exception as e:
        return IntradayLoadResult(False, None, f"csv_read_error:{e}", path)

    if raw.empty:
        return IntradayLoadResult(False, None, "empty_csv", path)

    cols = {str(c).lower() for c in raw.columns}
    if not REQUIRED_OHLCV.issubset(cols):
        missing = sorted(REQUIRED_OHLCV - cols)
        return IntradayLoadResult(False, None, f"invalid_columns:missing={missing}", path)

    try:
        from src.signal_engine import normalize_ohlcv_dataframe

        df = normalize_ohlcv_dataframe(raw)
    except Exception as e:
        return IntradayLoadResult(False, None, f"invalid_columns:{e}", path)

    if df.empty:
        return IntradayLoadResult(False, None, "empty_csv", path)

    return IntradayLoadResult(True, df, None, path)
