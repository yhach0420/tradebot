"""
Phase 153d: Core10 + Dynamic40 with shadow price/tick risk filter on Dynamic40 only.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from universe.am_pm_universe import _as_float, _norm, build_pm_universe_rows
from universe.core10_dynamic40 import (
    CORE_BUCKET,
    CORE_SLOTS,
    DYNAMIC_BUCKET,
    DYNAMIC_SLOTS,
    TOTAL_SLOTS,
    UNIVERSE_FIELDS,
    build_core_rows,
    build_dynamic_rows,
    dynamic_target_count,
    select_dynamic_vol_liq,
)
from universe.price_risk_filter import (
    DYNAMIC_SELECTED_REASON,
    MIN_CLOSE_PRICE,
    MAX_TICK_RATIO_PCT,
    UNIVERSE_MODE,
    close_from_feature,
    core_price_risk_warning,
    enrich_row_price_risk,
    passes_dynamic_price_risk,
)

PRICE_RISK_EXTRA_FIELDS = (
    "close_price",
    "tick_size",
    "tick_ratio_pct",
    "price_risk_flag",
    "price_risk_reason",
)

PRICE_RISK_UNIVERSE_FIELDS = tuple(UNIVERSE_FIELDS) + PRICE_RISK_EXTRA_FIELDS


def universe_am_price_risk_path(reports_dir: Path, day_stamp: str) -> Path:
    return reports_dir / f"universe_core10_dynamic40_price_risk_am_{day_stamp}.csv"


def universe_pm_price_risk_path(reports_dir: Path, day_stamp: str) -> Path:
    return reports_dir / f"universe_core10_dynamic40_price_risk_pm_{day_stamp}.csv"


def select_dynamic_vol_liq_price_risk(
    feature_rows: Sequence[Mapping[str, str]],
    *,
    exclude: set[str],
    target_count: int,
) -> list[dict[str, str]]:
    scored: list[tuple[float, dict[str, str]]] = []
    for row in feature_rows:
        sym = _norm(row["symbol"])
        if sym in exclude or not passes_dynamic_price_risk(row):
            continue
        vl = _as_float(row.get("volatility_liquidity_score"))
        if vl is None:
            continue
        scored.append((vl, dict(row)))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in scored[:target_count]]


def fill_to_total_price_risk(
    core_rows: list[dict[str, Any]],
    dynamic_rows: list[dict[str, Any]],
    feature_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    selected = {_norm(r["symbol"]) for r in core_rows + dynamic_rows}
    if len(core_rows) + len(dynamic_rows) >= TOTAL_SLOTS:
        merged = core_rows + dynamic_rows[: max(0, TOTAL_SLOTS - len(core_rows))]
    else:
        need = TOTAL_SLOTS - len(core_rows) - len(dynamic_rows)
        extra = select_dynamic_vol_liq_price_risk(
            feature_rows, exclude=selected, target_count=need
        )
        session = str(dynamic_rows[0].get("am_pm_session") if dynamic_rows else "am")
        extra_rows = build_dynamic_rows_price_risk(
            extra,
            session=session,
            start_rank=len(core_rows) + len(dynamic_rows) + 1,
        )
        merged = core_rows + dynamic_rows + extra_rows
    for i, row in enumerate(merged, start=1):
        row["rank"] = str(i)
    return merged[:TOTAL_SLOTS]


def build_dynamic_rows_price_risk(
    dynamic_feature_rows: Sequence[Mapping[str, str]],
    *,
    session: str,
    start_rank: int,
) -> list[dict[str, Any]]:
    rows = build_dynamic_rows(dynamic_feature_rows, session=session, start_rank=start_rank)
    for row in rows:
        row["selected_reason"] = DYNAMIC_SELECTED_REASON
    return rows


def build_am_universe_price_risk(
    *,
    core_symbols: Sequence[str],
    feature_rows: Sequence[Mapping[str, str]],
    symbol_meta: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    core_set = {_norm(s) for s in core_symbols if _norm(s)}
    core_rows = build_core_rows(core_symbols, symbol_meta=symbol_meta, session="am")
    n_dyn = dynamic_target_count(len(core_rows))

    baseline_dyn = select_dynamic_vol_liq(feature_rows, exclude=core_set, target_count=n_dyn)
    baseline_set = {_norm(r["symbol"]) for r in baseline_dyn}

    dynamic_src = select_dynamic_vol_liq_price_risk(
        feature_rows, exclude=core_set, target_count=n_dyn
    )
    while len(dynamic_src) < n_dyn:
        extra = select_dynamic_vol_liq_price_risk(
            feature_rows,
            exclude=core_set | {_norm(r["symbol"]) for r in dynamic_src},
            target_count=n_dyn - len(dynamic_src),
        )
        if not extra:
            break
        dynamic_src.extend(extra)

    dynamic_rows = build_dynamic_rows_price_risk(
        dynamic_src, session="am", start_rank=len(core_rows) + 1
    )
    merged = fill_to_total_price_risk(core_rows, dynamic_rows, feature_rows)
    excluded = sorted(s for s in baseline_set if s not in {_norm(r["symbol"]) for r in dynamic_src})
    replacements = sorted(
        s for s in {_norm(r["symbol"]) for r in dynamic_src} if s not in baseline_set
    )
    return merged, excluded, replacements


def build_pm_universe_price_risk(
    *,
    core_symbols: Sequence[str],
    feature_rows: Sequence[Mapping[str, str]],
    symbol_meta: Mapping[str, Mapping[str, Any]],
    push_day_dir: Path,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    core_set = {_norm(s) for s in core_symbols if _norm(s)}
    core_rows = build_core_rows(core_symbols, symbol_meta=symbol_meta, session="pm")
    pm50, _ = build_pm_universe_rows(feature_rows, symbol_meta=symbol_meta, push_day_dir=push_day_dir)
    n_dyn = dynamic_target_count(len(core_rows))

    baseline_dyn: list[dict[str, str]] = []
    for row in pm50:
        sym = _norm(row["symbol"])
        if sym in core_set:
            continue
        baseline_dyn.append(dict(row))
        if len(baseline_dyn) >= n_dyn:
            break
    while len(baseline_dyn) < n_dyn:
        extra = select_dynamic_vol_liq(
            feature_rows,
            exclude=core_set | {_norm(r["symbol"]) for r in baseline_dyn},
            target_count=n_dyn - len(baseline_dyn),
        )
        if not extra:
            break
        baseline_dyn.extend(extra)
    baseline_set = {_norm(r["symbol"]) for r in baseline_dyn[:n_dyn]}

    dynamic_src: list[dict[str, str]] = []
    for row in pm50:
        sym = _norm(row["symbol"])
        if sym in core_set or not passes_dynamic_price_risk(row):
            continue
        dynamic_src.append(dict(row))
        if len(dynamic_src) >= n_dyn:
            break
    while len(dynamic_src) < n_dyn:
        extra = select_dynamic_vol_liq_price_risk(
            feature_rows,
            exclude=core_set | {_norm(r["symbol"]) for r in dynamic_src},
            target_count=n_dyn - len(dynamic_src),
        )
        if not extra:
            break
        dynamic_src.extend(extra)

    dynamic_rows = build_dynamic_rows_price_risk(
        dynamic_src, session="pm", start_rank=len(core_rows) + 1
    )
    merged = fill_to_total_price_risk(core_rows, dynamic_rows, feature_rows)
    excluded = sorted(s for s in baseline_set if s not in {_norm(r["symbol"]) for r in dynamic_src})
    replacements = sorted(
        s for s in {_norm(r["symbol"]) for r in dynamic_src} if s not in baseline_set
    )
    return merged, excluded, replacements


def scan_core_price_risk_warnings(
    core_symbols: Sequence[str],
    feature_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    feat_by = {_norm(r["symbol"]): r for r in feature_rows}
    warnings: list[dict[str, Any]] = []
    for raw in core_symbols:
        sym = _norm(raw)
        if not sym:
            continue
        feat = feat_by.get(sym, {})
        px = close_from_feature(feat)
        warn = core_price_risk_warning(feat)
        if not warn:
            continue
        pr = enrich_row_price_risk({"symbol": sym}, feat, slot="core")
        warnings.append(
            {
                "symbol": sym,
                "close_price": pr.get("close_price"),
                "tick_size": pr.get("tick_size"),
                "tick_ratio_pct": pr.get("tick_ratio_pct"),
                "price_risk_flag": "warning",
                "price_risk_reason": warn,
                "action": "warn_only_entry_gate_final_reject",
            }
        )
    return warnings


def enrich_universe_csv_rows(
    rows: Sequence[Mapping[str, Any]],
    feature_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    feat_by = {_norm(r["symbol"]): r for r in feature_rows}
    out: list[dict[str, Any]] = []
    for row in rows:
        sym = _norm(str(row.get("symbol") or ""))
        feat = feat_by.get(sym, {})
        slot = str(row.get("universe_slot") or "dynamic")
        out.append(enrich_row_price_risk(dict(row), feat, slot=slot))
    return out


def write_price_risk_universe_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    from small_paper.day_fixed_am_registration import (
        FrozenArtifactWriteError,
        maybe_block_universe_csv_write,
    )

    blocked = maybe_block_universe_csv_write(path)
    if blocked:
        if blocked.get("fatal"):
            raise FrozenArtifactWriteError(
                str(blocked.get("reason") or "FROZEN_ARTIFACT_WRITE_ATTEMPT")
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(PRICE_RISK_UNIVERSE_FIELDS), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in PRICE_RISK_UNIVERSE_FIELDS})


def build_price_risk_universes(
    *,
    reports_dir: Path,
    day_stamp: str,
    core_symbols: Sequence[str],
    feature_rows: list[dict[str, str]],
    symbol_meta: Mapping[str, Mapping[str, Any]],
    push_day_dir: Path,
    write_am: bool = True,
    write_pm: bool = True,
) -> dict[str, Any]:
    """Build AM/PM price-risk universes.

    PM screening must pass write_am=False so the AM source CSV is never
    overwritten after SAME_DAY_AM_FROZEN_UNIVERSE is bound.
    """
    am_path = universe_am_price_risk_path(reports_dir, day_stamp)
    pm_path = universe_pm_price_risk_path(reports_dir, day_stamp)

    am_rows: list[dict[str, Any]] = []
    pm_rows: list[dict[str, Any]] = []
    am_excluded: list[str] = []
    am_replacements: list[str] = []
    pm_excluded: list[str] = []
    pm_replacements: list[str] = []
    am_written = False
    pm_written = False

    if feature_rows:
        if write_am:
            am_rows, am_excluded, am_replacements = build_am_universe_price_risk(
                core_symbols=core_symbols,
                feature_rows=feature_rows,
                symbol_meta=symbol_meta,
            )
            am_enriched = enrich_universe_csv_rows(am_rows, feature_rows)
            write_price_risk_universe_csv(am_path, am_enriched)
            am_rows = am_enriched
            am_written = am_path.is_file()
        if write_pm:
            pm_rows, pm_excluded, pm_replacements = build_pm_universe_price_risk(
                core_symbols=core_symbols,
                feature_rows=feature_rows,
                symbol_meta=symbol_meta,
                push_day_dir=push_day_dir,
            )
            pm_enriched = enrich_universe_csv_rows(pm_rows, feature_rows)
            write_price_risk_universe_csv(pm_path, pm_enriched)
            pm_rows = pm_enriched
            pm_written = pm_path.is_file()

    core_warnings = scan_core_price_risk_warnings(core_symbols, feature_rows)

    return {
        "universe_mode": UNIVERSE_MODE,
        "min_close_price": MIN_CLOSE_PRICE,
        "max_tick_ratio_pct": MAX_TICK_RATIO_PCT,
        "am_output": str(am_path),
        "pm_output": str(pm_path),
        "am_row_count": len(am_rows),
        "pm_row_count": len(pm_rows),
        "am_excluded": am_excluded,
        "am_replacements": am_replacements,
        "pm_excluded": pm_excluded,
        "pm_replacements": pm_replacements,
        "core_price_risk_warnings": core_warnings,
        "am_rows": am_rows,
        "pm_rows": pm_rows,
        "am_written": am_written,
        "pm_written": pm_written,
        "write_am": bool(write_am),
        "write_pm": bool(write_pm),
    }
