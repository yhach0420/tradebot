"""NEW_INFO universe: Core10 + Dynamic38 from existing Dynamic40 ranking. No manual picks."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.new_causal_information_acquisition_v1 import CORE_N, DYNAMIC_N, FUTURES_N, TOTAL_REGISTRATION_N
from small_paper.day_fixed_am_registration import AM_CSV_NAME, am_csv_path
from small_paper.v1r_live_dual_lane import canonical_symbol_key

CORE_BUCKET = "core10_discord"
DYNAMIC_BUCKET = "vol_liq_dynamic40"


class UniverseError(RuntimeError):
    pass


def _rank(row: Mapping[str, Any]) -> int:
    try:
        return int(float(row.get("rank") or 0))
    except (TypeError, ValueError):
        return 0


def _slot(row: Mapping[str, Any]) -> str:
    slot = str(row.get("universe_slot") or "").strip().lower()
    if slot in {"core", "dynamic"}:
        return slot
    bucket = str(row.get("source_bucket") or "").strip().lower()
    if bucket == CORE_BUCKET:
        return "core"
    if bucket == DYNAMIC_BUCKET:
        return "dynamic"
    return slot


def load_am_rows(csv_path: Path) -> list[dict[str, str]]:
    path = Path(csv_path)
    if not path.is_file():
        raise UniverseError(f"AM universe CSV missing: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def split_core_dynamic(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    core: list[dict[str, Any]] = []
    dynamic: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        slot = _slot(row)
        if slot == "core":
            core.append(row)
        elif slot == "dynamic":
            dynamic.append(row)
    core.sort(key=_rank)
    dynamic.sort(key=_rank)
    return core, dynamic


def dynamic38_from_dynamic40(dynamic40: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted((dict(r) for r in dynamic40), key=_rank)
    if len(ordered) < DYNAMIC_N:
        raise UniverseError(f"Dynamic40 ranking has {len(ordered)} rows; need {DYNAMIC_N}+")
    keep = ordered[:DYNAMIC_N]
    drop = ordered[DYNAMIC_N:]
    return keep, drop


def symbols_of(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        key = canonical_symbol_key(row.get("symbol") or row.get("Symbol") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def build_new_info_universe(csv_path: Path) -> dict[str, Any]:
    rows = load_am_rows(csv_path)
    core, dynamic40 = split_core_dynamic(rows)
    if len(core) != CORE_N:
        raise UniverseError(f"Core10 required; got {len(core)}")
    if len(dynamic40) != 40:
        raise UniverseError(f"Dynamic40 required as ranking source; got {len(dynamic40)}")
    dynamic38, dropped = dynamic38_from_dynamic40(dynamic40)
    core_syms = symbols_of(core)
    dyn_syms = symbols_of(dynamic38)
    dropped_syms = symbols_of(dropped)
    stock = core_syms + dyn_syms
    if len(core_syms) != CORE_N or len(dyn_syms) != DYNAMIC_N:
        raise UniverseError("symbol collapse after canonical_symbol_key")
    if len(stock) != CORE_N + DYNAMIC_N:
        raise UniverseError(f"stock N={len(stock)} != {CORE_N + DYNAMIC_N}")
    overlap = set(core_syms) & set(dyn_syms)
    if overlap:
        raise UniverseError(f"core/dynamic overlap: {sorted(overlap)}")
    if set(dropped_syms) & set(stock):
        raise UniverseError("dropped Dynamic40 tail leaked into Dynamic38")
    return {
        "ok": True,
        "csv_path": str(Path(csv_path)),
        "core_n": CORE_N,
        "dynamic_n": DYNAMIC_N,
        "futures_n": FUTURES_N,
        "stock_n": len(stock),
        "total_with_futures": len(stock) + FUTURES_N,
        "core_symbols": core_syms,
        "dynamic38_symbols": dyn_syms,
        "dynamic40_symbols": symbols_of(dynamic40),
        "dynamic40_n": len(dynamic40),
        "dropped_dynamic_tail": dropped_syms,
        "dropped_ranks": [_rank(r) for r in dropped],
        "dropped": [{"rank": _rank(r), "symbol": canonical_symbol_key(r.get("symbol") or "")} for r in dropped],
        "stock_symbols": stock,
        "manual_selection": False,
        "rule": "existing Dynamic40 ranking order; keep first 38; drop last 2; Core10 never cut",
    }


def same_day_am_csv(native_root: Path, trading_date: str) -> Path:
    return am_csv_path(Path(native_root), str(trading_date))


def latest_am_csv(native_root: Path) -> Path | None:
    reports = Path(native_root) / "results" / "reports"
    if not reports.is_dir():
        return None
    found = []
    for p in reports.iterdir():
        name = p.name
        if name.startswith("universe_core10_dynamic40_price_risk_am_") and name.endswith(".csv") and "refresh" not in name:
            day = name[len("universe_core10_dynamic40_price_risk_am_") : -len(".csv")]
            if len(day) == 8 and day.isdigit():
                found.append((day, p))
    if not found:
        return None
    found.sort()
    return found[-1][1]


assert CORE_N + DYNAMIC_N + FUTURES_N == TOTAL_REGISTRATION_N == 50
assert AM_CSV_NAME
