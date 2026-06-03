"""
Phase 157: Intraday universe refresh (10:00 AM / 14:30 PM) for shadow daily runner.

Register merge priority: open_symbols → Core10 → Dynamic fill (max 50).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from universe.am_pm_universe import _norm
from universe.core10_dynamic40 import CORE_BUCKET, CORE_SLOTS, TOTAL_SLOTS
from universe.core10_dynamic40_price_risk import (
    build_am_universe_price_risk,
    build_pm_universe_price_risk,
    enrich_row_price_risk,
    enrich_universe_csv_rows,
    write_price_risk_universe_csv,
)
from universe.price_risk_filter import close_from_feature

AM_REFRESH_TIME = "10:00"
PM_REFRESH_TIME = "14:30"
FOCUS_EXCLUDE_SYMBOL = "5856.T"
MAX_OPEN_SYMBOLS_CAP3 = 3

REFRESH_CSV_FIELDS = (
    "symbol",
    "symbol_key",
    "exchange",
    "passed",
    "source_bucket",
    "selected_reason",
    "universe_slot",
    "rank",
    "volatility_liquidity_score",
    "am_pm_session",
    "refresh_time",
    "is_open_position_carried",
    "close_price",
    "tick_size",
    "tick_ratio_pct",
    "price_risk_flag",
    "price_risk_reason",
)


def universe_am_refresh_path(reports_dir: Path, day_stamp: str) -> Path:
    return reports_dir / f"universe_core10_dynamic40_price_risk_am_refresh1000_{day_stamp}.csv"


def universe_pm_refresh_path(reports_dir: Path, day_stamp: str) -> Path:
    return reports_dir / f"universe_core10_dynamic40_price_risk_pm_refresh1430_{day_stamp}.csv"


def _row_symbol(row: Mapping[str, Any]) -> str:
    return _norm(str(row.get("symbol") or ""))


def _open_carry_row(
    sym: str,
    *,
    feature_rows: Sequence[Mapping[str, str]],
    symbol_meta: Mapping[str, Mapping[str, Any]],
    session: str,
    refresh_time: str,
) -> dict[str, Any]:
    feat_by = {_norm(r["symbol"]): r for r in feature_rows}
    feat = feat_by.get(sym, {})
    meta = symbol_meta.get(sym, {})
    ex = int(meta.get("exchange") or 1)
    slot = "core" if str(meta.get("source_bucket") or "") == CORE_BUCKET else "dynamic"
    base: dict[str, Any] = {
        "symbol": sym,
        "symbol_key": str(meta.get("symbol_key") or f"{sym.replace('.T', '')}@{ex}"),
        "exchange": str(ex),
        "passed": "true",
        "source_bucket": str(meta.get("source_bucket") or "open_position_carry"),
        "selected_reason": "open_position_carried",
        "universe_slot": slot,
        "rank": "0",
        "volatility_liquidity_score": feat.get("volatility_liquidity_score", ""),
        "am_pm_session": session,
        "refresh_time": refresh_time,
        "is_open_position_carried": "true",
    }
    return enrich_row_price_risk(base, feat, slot=slot)


def merge_universe_with_open_symbols(
    base_rows: Sequence[Mapping[str, Any]],
    *,
    open_symbols: Sequence[str],
    feature_rows: Sequence[Mapping[str, str]],
    symbol_meta: Mapping[str, Mapping[str, Any]],
    session: str,
    refresh_time: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Priority: open_symbols → core rows from base → dynamic rows from base → fill to 50.
    """
    open_set = {_norm(s) for s in open_symbols if _norm(s)}
    meta: dict[str, Any] = {
        "open_symbols_input": sorted(open_set),
        "open_symbols_count": len(open_set),
        "duplicate_count": 0,
        "carried_count": 0,
    }
    # Phase242b: keep ALL open symbols, but never exceed TOTAL_SLOTS.
    # If open positions exceed TOTAL_SLOTS, refresh cannot proceed without dropping open symbols (forbidden).
    if len(open_set) > TOTAL_SLOTS:
        meta["error"] = "open_symbols_exceed_cap"
        return [], meta

    by_sym = {_row_symbol(r): dict(r) for r in base_rows if _row_symbol(r)}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for sym in sorted(open_set):
        if sym in by_sym:
            row = dict(by_sym[sym])
            row["is_open_position_carried"] = "true"
            row["refresh_time"] = refresh_time
            row["selected_reason"] = row.get("selected_reason") or "open_position_carried"
        else:
            row = _open_carry_row(
                sym,
                feature_rows=feature_rows,
                symbol_meta=symbol_meta,
                session=session,
                refresh_time=refresh_time,
            )
        out.append(row)
        seen.add(sym)
        meta["carried_count"] += 1

    refresh_symbols_added_count = 0

    core_rows = [dict(by_sym[s]) for s in by_sym if by_sym[s].get("universe_slot") == "core" and s not in seen]
    for row in core_rows:
        sym = _row_symbol(row)
        row["refresh_time"] = refresh_time
        row["is_open_position_carried"] = "false"
        if len(out) < TOTAL_SLOTS:
            out.append(row)
            seen.add(sym)
            refresh_symbols_added_count += 1

    dyn_rows = [dict(by_sym[s]) for s in by_sym if by_sym[s].get("universe_slot") != "core" and s not in seen]
    for row in dyn_rows:
        sym = _row_symbol(row)
        if sym in seen:
            meta["duplicate_count"] += 1
            continue
        row["refresh_time"] = refresh_time
        row["is_open_position_carried"] = "false"
        if len(out) < TOTAL_SLOTS:
            out.append(row)
            seen.add(sym)
            refresh_symbols_added_count += 1
        else:
            # Hard cap reached: silently trim refresh universe (Phase242b expected behavior).
            break

    for i, row in enumerate(out[:TOTAL_SLOTS], start=1):
        row["rank"] = str(i)
    meta["total_count"] = len(out[:TOTAL_SLOTS])
    meta["register_count_ok"] = len(out[:TOTAL_SLOTS]) <= TOTAL_SLOTS
    meta["carried_open_symbols_count"] = meta.get("carried_count", 0)
    meta["refresh_symbols_added_count"] = refresh_symbols_added_count
    meta["final_register_count"] = len(out[:TOTAL_SLOTS])
    if meta.get("carried_open_symbols_count", 0) <= TOTAL_SLOTS and len(by_sym) > TOTAL_SLOTS:
        meta["fallback_reason"] = "trim_refresh_to_fit_cap"
    return out[:TOTAL_SLOTS], meta


def build_am_refresh_universe_price_risk(
    *,
    core_symbols: Sequence[str],
    feature_rows: Sequence[Mapping[str, str]],
    symbol_meta: Mapping[str, Mapping[str, Any]],
    push_day_dir: Path,
    open_symbols: Sequence[str] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_rows, excluded, replacements = build_am_universe_price_risk(
        core_symbols=core_symbols,
        feature_rows=feature_rows,
        symbol_meta=symbol_meta,
    )
    merged, merge_meta = merge_universe_with_open_symbols(
        base_rows,
        open_symbols=open_symbols,
        feature_rows=feature_rows,
        symbol_meta=symbol_meta,
        session="am",
        refresh_time=AM_REFRESH_TIME,
    )
    enriched = enrich_universe_csv_rows(merged, feature_rows)
    for row in enriched:
        row.setdefault("refresh_time", AM_REFRESH_TIME)
        row.setdefault("is_open_position_carried", "false")
    return enriched, {
        "excluded": excluded,
        "replacements": replacements,
        "merge": merge_meta,
        "focus_5856_excluded": FOCUS_EXCLUDE_SYMBOL in excluded,
    }


def build_pm_refresh_universe_price_risk(
    *,
    core_symbols: Sequence[str],
    feature_rows: Sequence[Mapping[str, str]],
    symbol_meta: Mapping[str, Mapping[str, Any]],
    push_day_dir: Path,
    open_symbols: Sequence[str] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_rows, excluded, replacements = build_pm_universe_price_risk(
        core_symbols=core_symbols,
        feature_rows=feature_rows,
        symbol_meta=symbol_meta,
        push_day_dir=push_day_dir,
    )
    merged, merge_meta = merge_universe_with_open_symbols(
        base_rows,
        open_symbols=open_symbols,
        feature_rows=feature_rows,
        symbol_meta=symbol_meta,
        session="pm",
        refresh_time=PM_REFRESH_TIME,
    )
    enriched = enrich_universe_csv_rows(merged, feature_rows)
    for row in enriched:
        row.setdefault("refresh_time", PM_REFRESH_TIME)
        row.setdefault("is_open_position_carried", "false")
    return enriched, {
        "excluded": excluded,
        "replacements": replacements,
        "merge": merge_meta,
        "focus_5856_excluded": FOCUS_EXCLUDE_SYMBOL in excluded,
    }


def write_refresh_universe_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(REFRESH_CSV_FIELDS), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in REFRESH_CSV_FIELDS})


def validate_refresh_universe_csv(path: Path, *, expected_session: str) -> dict[str, Any]:
    from universe.core10_dynamic40_shadow import validate_runner_universe

    val = validate_runner_universe(path, expected_session=expected_session)
    syms: list[str] = []
    dup = 0
    carried = 0
    has_5856 = False
    if path.is_file():
        with path.open(encoding="utf-8", newline="") as f:
            seen: set[str] = set()
            for row in csv.DictReader(f):
                sym = _norm(row.get("symbol") or "")
                if sym in seen:
                    dup += 1
                seen.add(sym)
                syms.append(sym)
                if sym == FOCUS_EXCLUDE_SYMBOL:
                    has_5856 = True
                if str(row.get("is_open_position_carried") or "").lower() == "true":
                    carried += 1
    val["duplicate_count"] = dup
    val["open_carried_count"] = carried
    val["has_5856"] = has_5856
    val["total_rows"] = len(syms)
    val["ok"] = (
        bool(val.get("passed"))
        and dup == 0
        and len(syms) == TOTAL_SLOTS
        and not has_5856
    )
    return val


def merge_register_specs(
    rows: Sequence[Mapping[str, Any]],
    *,
    symbol_meta: Mapping[str, Mapping[str, Any]],
    max_count: int = TOTAL_SLOTS,
) -> tuple[list[tuple[str, int]], dict[str, Any]]:
    """Build kabu register (code, exchange) tuples in CSV row order."""
    specs: list[tuple[str, int]] = []
    seen: set[str] = set()
    dup = 0
    for row in rows:
        sym = _row_symbol(row)
        if not sym or sym in seen:
            if sym:
                dup += 1
            continue
        seen.add(sym)
        meta = symbol_meta.get(sym, {})
        sym_key = str(row.get("symbol_key") or meta.get("symbol_key") or "")
        code = sym_key.split("@")[0] if sym_key else sym.replace(".T", "")
        ex = int(row.get("exchange") or meta.get("exchange") or 1)
        specs.append((code, ex))
    meta = {
        "register_count": len(specs),
        "duplicate_count": dup,
        "register_count_ok": len(specs) <= max_count,
    }
    if len(specs) > max_count:
        meta["error"] = "register_count_over_50"
    return specs[:max_count], meta


def load_refresh_universe_symbols(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    out: set[str] = set()
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = _norm(row.get("symbol") or "")
            if sym:
                out.add(sym)
    return out


def check_intraday_refresh_policy(
    *,
    refresh_enabled: bool,
    max_concurrent_positions: int,
    register_count: int,
    open_symbols_count: int,
    price_risk_mode: bool,
    entry_guard_enabled: bool,
) -> dict[str, Any]:
    issues: list[str] = []
    if not refresh_enabled:
        return {"ok": True, "skipped": True, "issues": []}
    if max_concurrent_positions > 3:
        issues.append("refresh_requires_max_concurrent_lte_3")
    if open_symbols_count > max_concurrent_positions:
        issues.append("open_symbols_exceed_cap")
    if register_count > TOTAL_SLOTS:
        issues.append("register_count_over_50")
    if not price_risk_mode:
        issues.append("refresh_requires_price_risk_universe_mode")
    if not entry_guard_enabled:
        issues.append("refresh_requires_entry_price_risk_guard")
    return {"ok": len(issues) == 0, "issues": issues}
