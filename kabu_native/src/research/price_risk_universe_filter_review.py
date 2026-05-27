"""
Phase 153d: Review shadow price-risk universe filter vs baseline Core10+Dynamic40.
"""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.low_price_risk_review import jpx_tick_size_yen, tick_ratio_pct
from research.small_paper_performance_review import _profit_factor
from small_paper.entry_price_risk_guard import (
    EntryPriceRiskGuardConfig,
    EntryPriceRiskGuardState,
)
from universe.core10_dynamic40 import TOTAL_SLOTS, validate_universe
from universe.core10_dynamic40_price_risk import (
    build_price_risk_universes,
    enrich_universe_csv_rows,
)
from universe.core10_dynamic40_price_risk_shadow import shadow_live_commands
from universe.core_watchlist import load_core_watchlist
from universe.daily_features import load_features_csv
from universe.dynamic_build import load_dynamic_config, resolve_symbol_master
from universe.price_risk_filter import UNIVERSE_MODE

DAY_STAMP = "20260525"
FOCUS = "5856.T"
COMPARE = "4392.T"
BASELINE_AM = f"universe_core10_dynamic40_am_{DAY_STAMP}.csv"


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _norm(sym: str) -> str:
    s = str(sym or "").strip().upper()
    return s if s.endswith(".T") else f"{s}.T"


def _load_universe_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [{k: str(v or "") for k, v in row.items()} for row in csv.DictReader(f)]


def _pnl_proxy(session_dir: Path) -> dict[str, dict[str, Any]]:
    path = session_dir / "structural_trades.csv"
    if not path.is_file():
        return {}
    by_sym: dict[str, list[float]] = {}
    stops: dict[str, list[float]] = {}
    for row in csv.DictReader(path.open(encoding="utf-8")):
        sym = _norm(row.get("symbol") or "")
        pnl = float(row.get("realized_pnl_pct") or 0)
        by_sym.setdefault(sym, []).append(pnl)
        if str(row.get("close_reason") or "") == "stop_hit":
            stops.setdefault(sym, []).append(pnl)
    out: dict[str, dict[str, Any]] = {}
    for sym, pnls in by_sym.items():
        out[sym] = {
            "trade_count": len(pnls),
            "sum_pnl_pct": round(sum(pnls), 4),
            "avg_pnl_pct": round(statistics.mean(pnls), 4),
            "stop_hit_count": len(stops.get(sym, [])),
            "stop_loss_sum_pct": round(sum(stops.get(sym, [])), 4),
            "max_loss_pct": round(min(pnls), 4),
        }
    return out


def _universe_pnl_metrics(
    universe_syms: set[str],
    pnl_proxy: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    kept = [pnl_proxy[s] for s in universe_syms if s in pnl_proxy]
    pnls_flat: list[float] = []
    for s in universe_syms:
        if s not in pnl_proxy:
            continue
        path = pnl_proxy[s]
        for _ in range(int(path.get("trade_count") or 0)):
            pnls_flat.append(float(path.get("avg_pnl_pct") or 0))
    stop_sum = sum(float(p.get("stop_loss_sum_pct") or 0) for p in kept)
    max_loss = min((float(p.get("max_loss_pct") or 0) for p in kept), default=0.0)
    sum_pnl = sum(float(p.get("sum_pnl_pct") or 0) for p in kept)
    return {
        "accepted_symbols_with_trades": len(kept),
        "accepted_pnl_proxy_sum": round(sum_pnl, 4),
        "stop_loss_sum_proxy": round(stop_sum, 4),
        "max_loss_proxy_pct": round(max_loss, 4) if kept else None,
        "structural_pf_proxy": round(_profit_factor(pnls_flat), 4)
        if pnls_flat and _profit_factor(pnls_flat) not in (None, float("inf"))
        else None,
    }


def _comparison_rows(
    baseline_rows: Sequence[Mapping[str, str]],
    price_risk_rows: Sequence[Mapping[str, Any]],
    *,
    feat_by: Mapping[str, Mapping[str, str]],
    pnl_proxy: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    base_by = {_norm(r["symbol"]): r for r in baseline_rows}
    pr_by = {_norm(r["symbol"]): r for r in price_risk_rows}
    all_syms = sorted(set(base_by) | set(pr_by))
    rows: list[dict[str, Any]] = []
    for sym in all_syms:
        b = base_by.get(sym)
        p = pr_by.get(sym)
        feat = feat_by.get(sym, {})
        px = float(feat.get("close") or 0) if feat.get("close") else 0
        tr = tick_ratio_pct(px) if px > 0 else 0
        pp = pnl_proxy.get(sym, {})
        rows.append(
            {
                "symbol": sym,
                "in_baseline_universe": sym in base_by,
                "in_price_risk_universe": sym in pr_by,
                "baseline_slot": b.get("universe_slot") if b else "",
                "price_risk_slot": p.get("universe_slot") if p else "",
                "baseline_rank": b.get("rank") if b else "",
                "price_risk_rank": p.get("rank") if p else "",
                "close_price_features": px,
                "tick_ratio_pct": round(tr, 4),
                "volatility_liquidity_score": feat.get("volatility_liquidity_score", ""),
                "change": (
                    "removed"
                    if sym in base_by and sym not in pr_by
                    else "added"
                    if sym not in base_by and sym in pr_by
                    else "kept"
                ),
                "session_pnl_proxy_sum": pp.get("sum_pnl_pct"),
                "session_stop_loss_sum": pp.get("stop_loss_sum_pct"),
            }
        )
    return rows


def _replaced_detail_rows(
    excluded: Sequence[str],
    replacements: Sequence[str],
    feat_by: Mapping[str, Mapping[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sym in excluded:
        f = feat_by.get(sym, {})
        px = float(f.get("close") or 0)
        rows.append(
            {
                "symbol": sym,
                "action": "excluded_from_dynamic",
                "close_price": px,
                "tick_size": jpx_tick_size_yen(px) if px > 0 else "",
                "tick_ratio_pct": tick_ratio_pct(px) if px > 0 else "",
                "volatility_liquidity_score": f.get("volatility_liquidity_score", ""),
                "exclude_reason": "close_below_50_or_tick_ratio_above_5",
            }
        )
    for sym in replacements:
        f = feat_by.get(sym, {})
        px = float(f.get("close") or 0)
        rows.append(
            {
                "symbol": sym,
                "action": "replacement_dynamic",
                "close_price": px,
                "tick_size": jpx_tick_size_yen(px) if px > 0 else "",
                "tick_ratio_pct": tick_ratio_pct(px) if px > 0 else "",
                "volatility_liquidity_score": f.get("volatility_liquidity_score", ""),
                "exclude_reason": "",
            }
        )
    return rows


def _dual_defense_check(
    *,
    price_risk_syms: set[str],
    session_dir: Path,
) -> dict[str, Any]:
    guard = EntryPriceRiskGuardState(
        config=EntryPriceRiskGuardConfig(
            enabled=True,
            min_entry_price=50.0,
            max_tick_ratio_pct=5.0,
            shadow_only=True,
        )
    )
    trades_path = session_dir / "structural_trades.csv"
    checks: list[dict[str, Any]] = []
    for row in csv.DictReader(trades_path.open(encoding="utf-8")) if trades_path.is_file() else []:
        sym = _norm(row.get("symbol") or "")
        trade = {"symbol": sym, "entry_price": row.get("entry_price")}
        chk = guard.check(trade)
        checks.append(
            {
                "symbol": sym,
                "in_price_risk_universe": sym in price_risk_syms,
                "entry_guard_blocked": chk.blocked,
                "entry_guard_trigger": chk.trigger,
                "realized_pnl_pct": row.get("realized_pnl_pct"),
            }
        )
    focus = [c for c in checks if c["symbol"] == FOCUS]
    compare = [c for c in checks if c["symbol"] == COMPARE]
    false_pos_universe = [
        c["symbol"]
        for c in checks
        if c["symbol"] not in price_risk_syms
        and c["entry_guard_blocked"]
        and float(c.get("realized_pnl_pct") or 0) > 0
    ]
    return {
        "5856_in_price_risk_universe": FOCUS in price_risk_syms,
        "5856_entry_guard_blocked_on_trades": any(c["entry_guard_blocked"] for c in focus),
        "4392_in_price_risk_universe": COMPARE in price_risk_syms,
        "4392_entry_guard_blocked": any(c["entry_guard_blocked"] for c in compare),
        "entry_guard_false_positive_good_trades": false_pos_universe,
        "trade_checks": checks,
    }


def determine_verdict(
    *,
    am_val: Mapping[str, Any],
    price_risk_val: Mapping[str, Any],
    checks: Mapping[str, Any],
    dual: Mapping[str, Any],
    core_warnings: Sequence[Mapping[str, Any]],
    maintains_50: bool,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not checks.get("features_exists"):
        return "tick_data_missing", ["features CSV missing"]
    if not maintains_50 or not am_val.get("passed") or not price_risk_val.get("passed"):
        notes.append(f"maintains_50={maintains_50} baseline_val={am_val.get('passed')} pr_val={price_risk_val.get('passed')}")
        return "universe_filter_too_strict", notes + ["cannot maintain validated 50-symbol universe"]
    if not checks.get("5856_excluded"):
        return "entry_gate_sufficient", notes + ["5856 still in price-risk universe — filter ineffective"]
    if not checks.get("4392_retained"):
        return "universe_filter_too_strict", notes + ["4392.T dropped from universe"]
    if checks.get("duplicate_count", 1) > 0:
        return "universe_filter_too_strict", notes + ["duplicate symbols in price-risk universe"]
    if dual.get("entry_guard_false_positive_good_trades"):
        return "universe_filter_too_strict", notes + ["entry gate would drop profitable names"]
    notes.append("5856 excluded at universe; entry gate remains second layer; 50 symbols maintained")
    if core_warnings:
        notes.append(f"core_price_risk_warnings={len(core_warnings)} (warn only; entry gate final reject)")
        return "core_handling_needed", notes
    return "price_risk_universe_filter_promising", notes


def run_phase153d_price_risk_universe_filter_review(
    *,
    repo_root: Path,
    reports_dir: Path,
    session_dir: Path,
    day_stamp: str = DAY_STAMP,
) -> dict[str, Any]:
    feat_path = reports_dir / f"features_{day_stamp}.csv"
    baseline_path = reports_dir / BASELINE_AM
    feature_rows = load_features_csv(feat_path) if feat_path.is_file() else []
    feat_by = {_norm(r["symbol"]): r for r in feature_rows}

    cfg = load_dynamic_config(repo_root / "kabu_native/configs/universe_dynamic_trial.yaml")
    _, entries = resolve_symbol_master(repo_root, cfg.symbol_master_paths)
    symbol_meta: dict[str, dict[str, Any]] = {}
    for e in entries:
        sym = f"{e.parsed.code}.T"
        symbol_meta[sym] = {
            "exchange": e.parsed.exchange,
            "symbol_key": e.parsed.symbol_key,
            "market": e.market,
        }

    core_symbols, _ = load_core_watchlist(repo_root)
    trade_d_parts = (int(day_stamp[:4]), int(day_stamp[4:6]), int(day_stamp[6:8]))
    push_dir = repo_root / "kabu_native/data/push_jsonl" / f"{trade_d_parts[0]}-{trade_d_parts[1]:02d}-{trade_d_parts[2]:02d}"

    build = build_price_risk_universes(
        reports_dir=reports_dir,
        day_stamp=day_stamp,
        core_symbols=core_symbols,
        feature_rows=feature_rows,
        symbol_meta=symbol_meta,
        push_day_dir=push_dir,
    )

    baseline_rows = _load_universe_csv(baseline_path)
    price_risk_rows = build.get("am_rows") or []
    baseline_syms = {_norm(r["symbol"]) for r in baseline_rows}
    price_risk_syms = {_norm(r["symbol"]) for r in price_risk_rows}

    pnl_proxy = _pnl_proxy(session_dir)
    baseline_metrics = _universe_pnl_metrics(baseline_syms, pnl_proxy)
    price_risk_metrics = _universe_pnl_metrics(price_risk_syms, pnl_proxy)

    am_val_baseline = validate_universe(baseline_path, expected_session="am") if baseline_path.is_file() else {}
    am_val_pr = validate_universe(
        Path(build["am_output"]), expected_session="am"
    )

    comparison = _comparison_rows(baseline_rows, price_risk_rows, feat_by=feat_by, pnl_proxy=pnl_proxy)
    replaced_rows = _replaced_detail_rows(
        build.get("am_excluded") or [],
        build.get("am_replacements") or [],
        feat_by,
    )
    core_warnings = build.get("core_price_risk_warnings") or []
    dual = _dual_defense_check(price_risk_syms=price_risk_syms, session_dir=session_dir)

    core_n = sum(1 for r in price_risk_rows if r.get("universe_slot") == "core")
    dyn_n = sum(1 for r in price_risk_rows if r.get("universe_slot") == "dynamic")
    dup = len(price_risk_syms) - len(set(price_risk_syms))

    validation_checks = {
        "5856_excluded": FOCUS not in price_risk_syms,
        "4392_retained": COMPARE in price_risk_syms,
        "maintains_50": len(price_risk_rows) == TOTAL_SLOTS,
        "duplicate_count": dup,
        "core10_count": core_n,
        "dynamic40_count": dyn_n,
        "features_exists": feat_path.is_file(),
        "baseline_exists": baseline_path.is_file(),
    }

    verdict, verdict_notes = determine_verdict(
        am_val=am_val_baseline,
        price_risk_val=am_val_pr,
        checks=validation_checks,
        dual=dual,
        core_warnings=core_warnings,
        maintains_50=validation_checks["maintains_50"],
    )

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(repo_root))
        except ValueError:
            return str(p)

    am_csv_rel = _rel(Path(build["am_output"]))
    pm_csv_rel = _rel(Path(build["pm_output"]))
    shadow_cmds = shadow_live_commands(am_csv_rel=am_csv_rel, pm_csv_rel=pm_csv_rel)

    report: dict[str, Any] = {
        "phase": "153d",
        "day_stamp": day_stamp,
        "universe_mode": UNIVERSE_MODE,
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "verdict_options": {
            "A": "price_risk_universe_filter_promising",
            "B": "entry_gate_sufficient",
            "C": "universe_filter_too_strict",
            "D": "core_handling_needed",
            "E": "tick_data_missing",
        },
        "filter_rules": {
            "dynamic_exclude": f"close_price < 50 OR tick_ratio_pct > 5",
            "core": "warn_only — core_price_risk_warning",
        },
        "validation_checks": validation_checks,
        "baseline_universe_am": str(baseline_path),
        "price_risk_universe_am": build["am_output"],
        "price_risk_universe_pm": build["pm_output"],
        "am_excluded": build.get("am_excluded"),
        "am_replacements": build.get("am_replacements"),
        "baseline_pnl_proxy": baseline_metrics,
        "price_risk_pnl_proxy": price_risk_metrics,
        "pnl_proxy_delta": round(
            float(price_risk_metrics.get("accepted_pnl_proxy_sum") or 0)
            - float(baseline_metrics.get("accepted_pnl_proxy_sum") or 0),
            4,
        ),
        "dual_defense": {
            k: v for k, v in dual.items() if k != "trade_checks"
        },
        "universe_validation": {
            "baseline_am": am_val_baseline,
            "price_risk_am": am_val_pr,
        },
        "shadow_live_commands": shadow_cmds,
        "constraints": [
            "no_production_yaml_change",
            "no_production_universe_change",
            "no_daily_runner_wire_phase154_decision",
        ],
    }

    _write_csv(reports_dir / "phase153d_universe_comparison.csv", comparison)
    _write_csv(reports_dir / "phase153d_replaced_symbols.csv", replaced_rows)
    _write_csv(reports_dir / "phase153d_core_price_risk_warnings.csv", core_warnings)
    (reports_dir / "phase153d_shadow_commands.json").write_text(
        json.dumps(shadow_cmds, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (reports_dir / "phase153d_price_risk_universe_filter_review.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report["output_files"] = {
        "json": str(reports_dir / "phase153d_price_risk_universe_filter_review.json"),
        "comparison_csv": str(reports_dir / "phase153d_universe_comparison.csv"),
        "replaced_csv": str(reports_dir / "phase153d_replaced_symbols.csv"),
        "core_warnings_csv": str(reports_dir / "phase153d_core_price_risk_warnings.csv"),
        "shadow_commands": str(reports_dir / "phase153d_shadow_commands.json"),
        "universe_am_csv": build["am_output"],
        "universe_pm_csv": build["pm_output"],
    }
    return report
