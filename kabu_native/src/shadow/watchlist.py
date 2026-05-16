"""
Build shadow watchlist from universe or morning_screen CSV.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from glob import glob
from pathlib import Path


@dataclass(frozen=True)
class WatchSymbol:
    symbol: str
    symbol_key: str
    code: str
    exchange: int
    rank: int | None = None
    screen_score: float | None = None


def _to_yahoo_symbol(code: str) -> str:
    c = code.strip()
    if c.endswith(".T"):
        return c.upper()
    return f"{c}.T"


def load_from_universe(path: Path, *, passed_only: bool = True) -> list[WatchSymbol]:
    out: list[WatchSymbol] = []
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
            sym = _to_yahoo_symbol(code)
            key = str(row.get("symbol_key") or f"{code}@{ex}").strip()
            out.append(WatchSymbol(symbol=sym, symbol_key=key, code=code, exchange=ex))
    return out


def _resolve_morning_screen_csv(path: Path | None, native_root: Path) -> Path:
    if path is not None:
        p = path
        if p.is_file():
            return p
        if p.is_dir():
            matches = sorted(p.glob("morning_screen_*.csv"))
            if matches:
                return matches[-1]
    matches = sorted(glob(str(native_root / "results" / "morning_screen" / "*" / "morning_screen_*.csv")))
    if not matches:
        raise FileNotFoundError("no morning_screen CSV found under kabu_native/results/morning_screen")
    return Path(matches[-1])


def load_from_morning_screen(
    path: Path | None,
    *,
    native_root: Path,
    top_n: int,
    passed_only: bool,
) -> list[WatchSymbol]:
    csv_path = _resolve_morning_screen_csv(path, native_root)
    rows: list[dict] = []
    with csv_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if passed_only:
                p = str(row.get("pass_screen", "")).strip().lower()
                if p not in ("true", "1", "yes"):
                    continue
            rows.append(row)

    def _rank_key(r: dict) -> tuple:
        rank = str(r.get("rank", "")).strip()
        try:
            ri = int(rank)
        except ValueError:
            ri = 9999
        score = float(r.get("score") or 0)
        return (ri, -score)

    rows.sort(key=_rank_key)
    out: list[WatchSymbol] = []
    for row in rows[:top_n]:
        code = str(row.get("symbol", "")).strip()
        if not code:
            continue
        ex = 1
        sym = _to_yahoo_symbol(code)
        key = f"{code}@{ex}"
        rank_val = row.get("rank")
        rank_i = int(rank_val) if str(rank_val).strip().isdigit() else None
        score = row.get("score")
        score_f = float(score) if score not in (None, "") else None
        out.append(
            WatchSymbol(
                symbol=sym,
                symbol_key=key,
                code=code.replace(".T", ""),
                exchange=ex,
                rank=rank_i,
                screen_score=score_f,
            )
        )
    return out


def build_watchlist(
    *,
    source: str,
    native_root: Path,
    repo_root: Path,
    path: Path | None,
    universe_path: str,
    top_n: int,
    passed_only: bool,
) -> list[WatchSymbol]:
    if source == "universe":
        up = Path(universe_path)
        if not up.is_absolute():
            up = (repo_root / up).resolve()
        return load_from_universe(up, passed_only=passed_only)[:top_n] if top_n else load_from_universe(up, passed_only=passed_only)
    if source == "morning_screen":
        mp = path
        if mp is not None and not mp.is_absolute():
            mp = (repo_root / mp).resolve()
        return load_from_morning_screen(mp, native_root=native_root, top_n=top_n, passed_only=passed_only)
    raise ValueError(f"unknown watchlist source: {source!r}")
