#!/usr/bin/env python3
"""Phase 113: Full-market vol_liq top50 → shadow universe CSV (review only)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"
PUSH_LIMIT = 50


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _day_stamp(d: str | None) -> str:
    if d:
        return d.replace("-", "")
    return datetime.now(JST).strftime("%Y%m%d")


def runner_check(universe_csv: Path) -> dict[str, Any]:
    _bootstrap()
    from storage.symbol_sources import load_symbols
    from universe.vol_liq_dynamic50_universe import validate_universe_csv

    val = validate_universe_csv(universe_csv)
    syms = load_symbols(universe=universe_csv, native_root=NATIVE) if universe_csv.is_file() else []
    passed = (
        val.get("passed")
        and len(syms) == PUSH_LIMIT
        and val.get("duplicate_count", 1) == 0
        and val.get("total_count") == PUSH_LIMIT
    )
    return {
        "passed": passed,
        "symbol_count": len(syms),
        "duplicate_count": val.get("duplicate_count", 0),
        "universe_validation": val,
        "universe_csv_path": _rel(universe_csv),
        "load_symbols_ok": len(syms) == PUSH_LIMIT,
    }


def determine_verdict(
    *,
    features_exists: bool,
    valid_vol_liq: int,
    universe_val: dict[str, Any],
    runner: dict[str, Any],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not features_exists:
        return "missing_daily_features", ["features_YYYYMMDD.csv not found"]
    if valid_vol_liq < PUSH_LIMIT:
        notes.append(f"valid_vol_liq={valid_vol_liq} < {PUSH_LIMIT}")
        return "insufficient_valid_features", notes
    if universe_val.get("total_count", 0) < PUSH_LIMIT or not universe_val.get("passed"):
        notes.append(f"universe total={universe_val.get('total_count')}")
        return "insufficient_valid_features", notes
    if not runner.get("passed"):
        notes.append(f"runner symbol_count={runner.get('symbol_count')}")
        return "runner_load_failed", notes
    notes.append("top50 vol_liq universe; runner load OK")
    return "vol_liq_dynamic50_ready", notes


def build_phase113(
    day_stamp: str,
    *,
    trade_date: date,
    generate_features: bool,
    reports_dir: Path = REPORTS,
) -> dict[str, Any]:
    _bootstrap()
    from universe.daily_features import (
        features_csv_path,
        generate_features_csv,
        load_features_csv,
        select_top50_by_vol_liq,
        universe_csv_path,
    )
    from universe.dynamic_build import load_dynamic_config, resolve_symbol_master
    from universe.hero_backtest import load_static27
    from universe.vol_liq_dynamic50_universe import (
        build_universe_rows,
        diagnostics,
        validate_universe_csv,
        write_universe_csv,
    )

    cfg = load_dynamic_config(NATIVE / "configs" / "universe_dynamic_trial.yaml")
    _, entries = resolve_symbol_master(ROOT, cfg.symbol_master_paths)
    symbol_meta: dict[str, dict[str, Any]] = {}
    all_symbols: list[str] = []
    for e in entries:
        sym = f"{e.parsed.code}.T"
        symbol_meta[sym] = {
            "exchange": e.parsed.exchange,
            "symbol_key": e.parsed.symbol_key,
            "market": e.market,
        }
        all_symbols.append(sym)

    static27 = load_static27(NATIVE)
    feat_path = features_csv_path(reports_dir, day_stamp)
    uni_path = universe_csv_path(reports_dir, day_stamp)

    feature_gen: dict[str, Any] = {"generated": False}
    if generate_features or not feat_path.is_file():
        print(f"generating features ({len(all_symbols)} symbols) ...", flush=True)
        feature_gen = generate_features_csv(
            symbols=all_symbols,
            trade_date=trade_date,
            symbol_meta=symbol_meta,
            out_path=feat_path,
        )
        feature_gen["generated"] = True

    features_exists = feat_path.is_file()
    feature_rows = load_features_csv(feat_path) if features_exists else []
    valid_vol_liq = sum(
        1 for r in feature_rows if str(r.get("volatility_liquidity_score") or "").strip()
    )

    top50 = select_top50_by_vol_liq(feature_rows) if features_exists else []
    universe_rows: list[dict[str, Any]] = []
    if len(top50) >= PUSH_LIMIT:
        universe_rows = build_universe_rows(top50)
        write_universe_csv(uni_path, universe_rows)

    universe_val = validate_universe_csv(uni_path) if uni_path.is_file() else {"passed": False, "total_count": 0}
    runner = runner_check(uni_path) if uni_path.is_file() else {"passed": False, "symbol_count": 0}
    diag = diagnostics(
        universe_rows,
        static27=static27,
        feature_meta=feature_gen if feature_gen.get("generated") else {"from_cache": True, "row_count": len(feature_rows)},
        feature_top50=top50,
    )

    verdict, verdict_notes = determine_verdict(
        features_exists=features_exists,
        valid_vol_liq=valid_vol_liq,
        universe_val=universe_val,
        runner=runner,
    )

    return {
        "phase": 113,
        "day_stamp": day_stamp,
        "trade_date": trade_date.isoformat(),
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "verdict_options": {
            "A": "vol_liq_dynamic50_ready",
            "B": "missing_daily_features",
            "C": "insufficient_valid_features",
            "D": "runner_load_failed",
        },
        "static27_used": False,
        "inputs": {
            "features_csv": _rel(feat_path),
            "features_exists": features_exists,
            "feature_row_count": len(feature_rows),
            "valid_vol_liq_count": valid_vol_liq,
        },
        "outputs": {
            "universe_vol_liq_dynamic50_csv": _rel(uni_path),
            "phase113_json": _rel(reports_dir / f"phase113_vol_liq_dynamic50_universe_{day_stamp}.json"),
            "phase113_runner_check_json": _rel(reports_dir / f"phase113_runner_check_{day_stamp}.json"),
        },
        "feature_generation": feature_gen,
        "universe_validation": universe_val,
        "runner_check": runner,
        "diagnostics": diag,
        "constraints_confirmed": [
            "no_production_pilot_yaml_change",
            "no_overwrite_universe_intraday_full",
            "no_entry_exit_quality_vol_liq_cap_change",
            "no_symbol_hardcode",
            "shadow_dry_run_only",
            "no_pf_evaluation",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 113 vol_liq dynamic50 universe")
    parser.add_argument("--day-stamp", default=None)
    parser.add_argument("--trade-date", default=None, help="YYYY-MM-DD (default from day-stamp)")
    parser.add_argument(
        "--no-generate",
        action="store_true",
        help="Do not fetch yfinance; fail if features CSV absent",
    )
    args = parser.parse_args()

    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")
    if args.trade_date:
        trade_d = date.fromisoformat(args.trade_date)
        day_stamp = trade_d.strftime("%Y%m%d")
    else:
        trade_d = date(int(day_stamp[:4]), int(day_stamp[4:6]), int(day_stamp[6:8]))

    generate = not args.no_generate
    report = build_phase113(day_stamp, trade_date=trade_d, generate_features=generate)

    json_main = REPORTS / f"phase113_vol_liq_dynamic50_universe_{day_stamp}.json"
    json_runner = REPORTS / f"phase113_runner_check_{day_stamp}.json"
    json_main.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    json_runner.write_text(
        json.dumps(
            {
                "phase": 113,
                "day_stamp": day_stamp,
                "runner_check": report["runner_check"],
                "universe_validation": report["universe_validation"],
                "verdict": report["verdict"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "universe_csv": report["outputs"]["universe_vol_liq_dynamic50_csv"],
                "symbol_count": report["runner_check"].get("symbol_count"),
                "3905_in_top50": report["diagnostics"].get("focus_3905_in_top50"),
            },
            ensure_ascii=True,
        )
    )
    return 0 if report["verdict"] == "vol_liq_dynamic50_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
