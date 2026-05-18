"""
Load symbol lists for data accumulation scripts (universe / morning_screen / CLI).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    symbol_key: str
    exchange: int
    code: str


def _normalize_symbol(code: str) -> str:
    c = code.strip().upper().split("@")[0]
    if not c.endswith(".T"):
        c = f"{c}.T"
    return c


def load_symbols_from_universe(path: Path, *, passed_only: bool = True) -> list[SymbolSpec]:
    out: list[SymbolSpec] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if passed_only:
                p = str(row.get("passed", "")).strip().lower()
                if p not in ("true", "1", "yes"):
                    continue
            code = str(row.get("symbol", "")).strip().split("@")[0].replace(".T", "")
            if not code:
                continue
            ex = int(row.get("exchange") or 1)
            sym = _normalize_symbol(code)
            key = str(row.get("symbol_key") or f"{code}@{ex}").strip()
            out.append(SymbolSpec(symbol=sym, symbol_key=key, exchange=ex, code=code))
    return out


def load_symbols_from_csv(path: Path, *, symbol_col: str = "symbol") -> list[SymbolSpec]:
    out: list[SymbolSpec] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            raw = str(row.get(symbol_col, "")).strip()
            if not raw:
                continue
            code = raw.split("@")[0].replace(".T", "")
            ex = int(row.get("exchange") or 1)
            sym = _normalize_symbol(code)
            key = str(row.get("symbol_key") or f"{code}@{ex}").strip()
            out.append(SymbolSpec(symbol=sym, symbol_key=key, exchange=ex, code=code))
    return out


def load_symbols(
    *,
    universe: Path | None = None,
    morning_screen: Path | None = None,
    watchlist: Path | None = None,
    symbols_csv: Path | None = None,
    symbols: Sequence[str] | None = None,
    native_root: Path | None = None,
    passed_only: bool = True,
) -> list[SymbolSpec]:
    """Resolve symbols from the first available source."""
    if symbols:
        out: list[SymbolSpec] = []
        for raw in symbols:
            code = raw.strip().split("@")[0].replace(".T", "")
            if not code:
                continue
            ex = 1
            if "@" in raw:
                ex = int(raw.split("@", 1)[1])
            sym = _normalize_symbol(code)
            out.append(SymbolSpec(symbol=sym, symbol_key=f"{code}@{ex}", exchange=ex, code=code))
        return out

    if universe and universe.is_file():
        return load_symbols_from_universe(universe, passed_only=passed_only)

    if morning_screen and native_root:
        from shadow.watchlist import load_from_morning_screen

        wl = load_from_morning_screen(morning_screen, native_root=native_root, top_n=9999, passed_only=passed_only)
        return [
            SymbolSpec(symbol=w.symbol, symbol_key=w.symbol_key, exchange=w.exchange, code=w.code)
            for w in wl
        ]

    if watchlist and watchlist.is_file():
        return load_symbols_from_csv(watchlist)

    if symbols_csv and symbols_csv.is_file():
        return load_symbols_from_csv(symbols_csv)

    raise FileNotFoundError("no symbol source: provide --universe, --morning-screen, --watchlist, or --symbols")
