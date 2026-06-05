"""
Load symbol lists for data accumulation scripts (universe / morning_screen / CLI).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    symbol_key: str
    exchange: int
    code: str


def symbol_name(spec: SymbolSpec | Mapping[str, Any] | Sequence[Any] | str) -> str:
    """Extract display symbol from SymbolSpec, dict, legacy tuple, or str."""
    if isinstance(spec, str):
        return spec.strip()
    if isinstance(spec, SymbolSpec):
        return str(spec.symbol)
    if isinstance(spec, Mapping):
        return str(spec.get("symbol") or spec.get("code") or "").strip()
    if isinstance(spec, Sequence) and not isinstance(spec, (str, bytes)):
        if len(spec) >= 1:
            return str(spec[0]).strip()
    return str(spec).strip()


def symbol_key_name(spec: SymbolSpec | Mapping[str, Any] | Sequence[Any] | str) -> str:
    """Extract kabu symbol_key from SymbolSpec, dict, legacy tuple, or str."""
    if isinstance(spec, SymbolSpec):
        return str(spec.symbol_key)
    if isinstance(spec, Mapping):
        key = str(spec.get("symbol_key") or "").strip()
        if key:
            return key
        sym = symbol_name(spec)
        ex = int(spec.get("exchange") or 1)
        code = sym.split("@")[0].replace(".T", "")
        return f"{code}@{ex}" if code else sym
    if isinstance(spec, Sequence) and not isinstance(spec, (str, bytes)):
        if len(spec) >= 2:
            return str(spec[1]).strip()
        return symbol_name(spec)
    raw = str(spec).strip()
    if "@" in raw:
        return raw
    code = raw.split("@")[0].replace(".T", "")
    return f"{code}@1" if code else raw


def symbols_list(specs: Sequence[Any]) -> list[str]:
    out: list[str] = []
    for spec in specs:
        sym = symbol_name(spec)
        if sym:
            out.append(sym)
    return out


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
