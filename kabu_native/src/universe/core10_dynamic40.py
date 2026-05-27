"""
Phase 117: Core10 (Discord) + Dynamic40 (vol_liq) shadow universe.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from universe.am_pm_universe import _as_float, _norm, build_pm_universe_rows
CORE_SLOTS = 10
DYNAMIC_SLOTS = 40
TOTAL_SLOTS = 50

CORE_BUCKET = "core10_discord"
DYNAMIC_BUCKET = "vol_liq_dynamic40"

UNIVERSE_FIELDS = (
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
)

FOCUS_SYMBOLS = ("3905.T", "6613.T")


def select_dynamic_vol_liq(
    feature_rows: Sequence[Mapping[str, str]],
    *,
    exclude: set[str],
    target_count: int,
) -> list[dict[str, str]]:
    """Rank by vol_liq excluding core; prefer up to 40 dynamic slots then fill to total=50."""
    scored: list[tuple[float, dict[str, str]]] = []
    for row in feature_rows:
        sym = _norm(row["symbol"])
        if sym in exclude:
            continue
        vl = _as_float(row.get("volatility_liquidity_score"))
        if vl is None:
            continue
        scored.append((vl, dict(row)))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[dict[str, str]] = []
    for _, row in scored[:target_count]:
        out.append(row)
    return out


def dynamic_target_count(core_count: int) -> int:
    remaining = TOTAL_SLOTS - min(core_count, CORE_SLOTS)
    return min(DYNAMIC_SLOTS, remaining) if remaining > 0 else 0


def fill_to_total(
    core_rows: list[dict[str, Any]],
    dynamic_rows: list[dict[str, Any]],
    feature_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    selected = {_norm(r["symbol"]) for r in core_rows + dynamic_rows}
    if len(core_rows) + len(dynamic_rows) >= TOTAL_SLOTS:
        return core_rows + dynamic_rows[: max(0, TOTAL_SLOTS - len(core_rows))]
    need = TOTAL_SLOTS - len(core_rows) - len(dynamic_rows)
    extra = select_dynamic_vol_liq(feature_rows, exclude=selected, target_count=need)
    extra_rows = build_dynamic_rows(
        extra,
        session=str(dynamic_rows[0].get("am_pm_session") if dynamic_rows else "am"),
        start_rank=len(core_rows) + len(dynamic_rows) + 1,
    )
    merged = core_rows + dynamic_rows + extra_rows
    for i, row in enumerate(merged, start=1):
        row["rank"] = str(i)
    return merged[:TOTAL_SLOTS]


def build_core_rows(
    core_symbols: Sequence[str],
    *,
    symbol_meta: Mapping[str, Mapping[str, Any]],
    session: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, raw in enumerate(core_symbols[:CORE_SLOTS], start=1):
        sym = _norm(raw)
        if not sym:
            continue
        meta = symbol_meta.get(sym, {})
        ex = int(meta.get("exchange") or 1)
        rows.append(
            {
                "symbol": sym,
                "symbol_key": str(meta.get("symbol_key") or f"{sym.replace('.T', '')}@{ex}"),
                "exchange": ex,
                "passed": "True",
                "source_bucket": CORE_BUCKET,
                "selected_reason": "discord_core_watchlist",
                "universe_slot": "core",
                "rank": str(i),
                "volatility_liquidity_score": "",
                "am_pm_session": session,
            }
        )
    return rows


def build_dynamic_rows(
    dynamic_feature_rows: Sequence[Mapping[str, str]],
    *,
    session: str,
    start_rank: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for j, row in enumerate(dynamic_feature_rows, start=0):
        sym = _norm(row["symbol"])
        ex = int(row.get("exchange") or 1)
        vl = row.get("volatility_liquidity_score") or ""
        rows.append(
            {
                "symbol": sym,
                "symbol_key": str(row.get("symbol_key") or f"{sym.replace('.T', '')}@{ex}"),
                "exchange": ex,
                "passed": "True",
                "source_bucket": DYNAMIC_BUCKET,
                "selected_reason": "vol_liq_dynamic40_exclude_core",
                "universe_slot": "dynamic",
                "rank": str(start_rank + j),
                "volatility_liquidity_score": vl,
                "am_pm_session": session,
            }
        )
    return rows


def build_am_universe(
    *,
    core_symbols: Sequence[str],
    feature_rows: Sequence[Mapping[str, str]],
    symbol_meta: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    core_set = {_norm(s) for s in core_symbols if _norm(s)}
    core_rows = build_core_rows(core_symbols, symbol_meta=symbol_meta, session="am")
    n_dyn = dynamic_target_count(len(core_rows))
    dynamic_src = select_dynamic_vol_liq(feature_rows, exclude=core_set, target_count=n_dyn)
    dynamic_rows = build_dynamic_rows(dynamic_src, session="am", start_rank=len(core_rows) + 1)
    return fill_to_total(core_rows, dynamic_rows, feature_rows)


def build_pm_universe(
    *,
    core_symbols: Sequence[str],
    feature_rows: Sequence[Mapping[str, str]],
    symbol_meta: Mapping[str, Mapping[str, Any]],
    push_day_dir: Path,
) -> list[dict[str, Any]]:
    core_set = {_norm(s) for s in core_symbols if _norm(s)}
    core_rows = build_core_rows(core_symbols, symbol_meta=symbol_meta, session="pm")
    pm50, _ = build_pm_universe_rows(feature_rows, symbol_meta=symbol_meta, push_day_dir=push_day_dir)
    dynamic_src: list[dict[str, str]] = []
    for row in pm50:
        sym = _norm(row["symbol"])
        if sym in core_set:
            continue
        dynamic_src.append(dict(row))
        if len(dynamic_src) >= DYNAMIC_SLOTS:
            break
    n_dyn = dynamic_target_count(len(core_rows))
    while len(dynamic_src) < n_dyn:
        extra = select_dynamic_vol_liq(
            feature_rows,
            exclude=core_set | {_norm(r["symbol"]) for r in dynamic_src},
            target_count=n_dyn - len(dynamic_src),
        )
        if not extra:
            break
        dynamic_src.extend(extra)
    dynamic_rows = build_dynamic_rows(dynamic_src, session="pm", start_rank=len(core_rows) + 1)
    return fill_to_total(core_rows, dynamic_rows, feature_rows)


def write_universe_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(UNIVERSE_FIELDS), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in UNIVERSE_FIELDS})


def validate_universe(path: Path, *, expected_session: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    ok = True

    def add(cid: str, passed: bool, detail: str) -> None:
        nonlocal ok
        if not passed:
            ok = False
        checks.append({"check_id": cid, "passed": passed, "detail": detail})

    if not path.is_file():
        add("file_exists", False, "missing")
        return {"passed": False, "checks": checks, "total_count": 0, "symbol_count": 0}

    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({k: str(v or "") for k, v in row.items()})

    syms = [_norm(r.get("symbol", "")) for r in rows]
    dup = len(syms) - len(set(syms))
    core_n = sum(1 for r in rows if r.get("universe_slot") == "core")
    dyn_n = sum(1 for r in rows if r.get("universe_slot") == "dynamic")
    sessions = {r.get("am_pm_session") for r in rows}

    add("symbol_count_50", len(rows) == TOTAL_SLOTS, f"count={len(rows)}")
    add("no_duplicate_symbols", dup == 0, f"duplicates={dup}")
    add("core_slots_le_10", core_n <= CORE_SLOTS, f"core={core_n}")
    add(
        "dynamic_fills_remainder",
        dyn_n == len(rows) - core_n,
        f"core={core_n} dynamic={dyn_n}",
    )
    add("am_pm_session", sessions == {expected_session}, f"sessions={sessions}")
    add("all_passed_true", all(str(r.get("passed", "")).lower() in ("true", "1", "yes") for r in rows), "passed")

    return {
        "passed": ok,
        "checks": checks,
        "total_count": len(rows),
        "symbol_count": len(rows),
        "duplicate_count": dup,
        "core_count": core_n,
        "dynamic_count": dyn_n,
    }


def build_core_inventory(
    core_symbols: Sequence[str],
    *,
    symbol_meta: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    from universe.core_watchlist import validate_watch_symbol

    rows: list[dict[str, Any]] = []
    for i, raw in enumerate(core_symbols, start=1):
        sym = _norm(raw)
        ok, reject = validate_watch_symbol(sym)
        rows.append(
            {
                "rank": i,
                "symbol": sym,
                "valid": ok,
                "reject_reason": reject or "",
                "in_symbol_master": sym in symbol_meta,
                "exchange": symbol_meta.get(sym, {}).get("exchange", ""),
            }
        )
    return rows


def universe_am_path(reports_dir: Path, day_stamp: str) -> Path:
    return reports_dir / f"universe_core10_dynamic40_am_{day_stamp}.csv"


def universe_pm_path(reports_dir: Path, day_stamp: str) -> Path:
    return reports_dir / f"universe_core10_dynamic40_pm_{day_stamp}.csv"


def compare_universe_sets(
    *,
    static27: set[str],
    vol_liq50: set[str],
    core40_am: set[str],
    core40_pm: set[str],
    hero_top20: set[str],
) -> dict[str, Any]:
    from universe.hero_backtest import coverage_vs_universe

    def cov(u: set[str]) -> dict[str, Any]:
        return coverage_vs_universe(hero_top20, u)

    return {
        "static27": {**cov(static27), "universe_count": len(static27)},
        "vol_liq_dynamic50": {**cov(vol_liq50), "universe_count": len(vol_liq50)},
        "core10_dynamic40_am": {**cov(core40_am), "universe_count": len(core40_am)},
        "core10_dynamic40_pm": {**cov(core40_pm), "universe_count": len(core40_pm)},
        "overlap": {
            "static27_vol_liq50": len(static27 & vol_liq50),
            "static27_core40_am": len(static27 & core40_am),
            "vol_liq50_core40_am": len(vol_liq50 & core40_am),
            "core40_am_pm": len(core40_am & core40_pm),
        },
        "focus": {
            sym: {
                "in_static27": sym in static27,
                "in_vol_liq50": sym in vol_liq50,
                "in_core40_am": sym in core40_am,
                "in_core40_pm": sym in core40_pm,
                "in_hero_top20": sym in hero_top20,
            }
            for sym in FOCUS_SYMBOLS
        },
    }


def determine_verdict(
    *,
    source_info: Mapping[str, Any],
    core_count: int,
    am_val: Mapping[str, Any],
    pm_val: Mapping[str, Any],
    enforcement_ok: bool,
    comparison_avg: Mapping[str, Any],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if core_count > CORE_SLOTS:
        return "core_limit_enforcement_missing", [f"core_count={core_count} exceeds {CORE_SLOTS}"]
    if not enforcement_ok:
        return "core_limit_enforcement_missing", ["discord_issue_bot missing core_limit_exceeded guard"]
    if not source_info.get("readable_exists") and core_count == 0:
        notes.append("watchlist.json missing and core empty — optional !watch add for Core10")
    if am_val.get("total_count", 0) < TOTAL_SLOTS or pm_val.get("total_count", 0) < TOTAL_SLOTS:
        notes.append(f"am={am_val.get('total_count')} pm={pm_val.get('total_count')}")
        return "need_core_symbol_source", notes + ["insufficient features for dynamic fill to 50"]
    if not am_val.get("passed") or not pm_val.get("passed"):
        return "need_core_symbol_source", ["universe validation failed"]

    am_hr = float(comparison_avg.get("core40_am_hero_hit_rate") or 0)
    s_hr = float(comparison_avg.get("static27_hero_hit_rate") or 0)
    v_hr = float(comparison_avg.get("vol_liq50_hero_hit_rate") or 0)
    notes.append(
        f"avg hero_top20 hit_rate static27={s_hr:.2%} vol_liq50={v_hr:.2%} core40_am={am_hr:.2%}"
    )
    if am_hr + 0.02 < s_hr and am_hr + 0.02 < v_hr:
        return "dynamic40_not_improving_coverage", notes
    return "core10_dynamic40_ready", notes
