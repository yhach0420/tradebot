"""
Phase279: Symbol display names for Discord UX (read-only master lookup).
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Optional


def _norm_symbol(sym: str) -> str:
    s = str(sym or "").strip().upper()
    if not s:
        return ""
    if "." not in s and s.isdigit():
        return f"{s}.T"
    return s


def _repo_root_guess() -> Path:
    here = Path(__file__).resolve()
    # kabu_native/src/small_paper -> repo root
    return here.parents[3]


def load_symbol_name_map(
    *,
    master_path: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> dict[str, str]:
    """Load symbol -> name from data/jpx/tradable_symbols.csv (code-only if missing)."""
    root = repo_root or _repo_root_guess()
    path = master_path or (root / "data" / "jpx" / "tradable_symbols.csv")
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sym = _norm_symbol(row.get("symbol") or "")
            if not sym:
                continue
            name = str(row.get("name") or row.get("symbol_name") or "").strip()
            if name:
                out[sym] = name
    return out


@lru_cache(maxsize=1)
def get_cached_symbol_name_map() -> dict[str, str]:
    return load_symbol_name_map()


def format_symbol_label(symbol: str, name_map: Optional[Mapping[str, str]] = None) -> str:
    sym = _norm_symbol(symbol)
    code = sym.replace(".T", "") if sym else "—"
    names = name_map if name_map is not None else get_cached_symbol_name_map()
    name = (names.get(sym) or "").strip()
    if name:
        return f"{code} {name}"
    return code


def format_symbol_display(
    symbol: str,
    name: Optional[str] = None,
    *,
    name_map: Optional[Mapping[str, str]] = None,
) -> str:
    """Discord ENTRY/EXIT header: code with .T suffix and optional Japanese name."""
    sym = _norm_symbol(symbol)
    if not sym:
        return "—"
    resolved = (name or "").strip()
    if not resolved:
        names = name_map if name_map is not None else get_cached_symbol_name_map()
        resolved = (names.get(sym) or "").strip()
    if resolved:
        return f"{sym} {resolved}"
    return sym
