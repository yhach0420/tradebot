#!/usr/bin/env python3
"""Phase 114: AM/PM independent dynamic50 universe design (shadow only)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"
PUSH_ROOT = NATIVE / "data" / "push_jsonl"
PHASE113 = NATIVE / "scripts" / "run_phase113_vol_liq_dynamic50_universe.py"
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


def ensure_features(day_stamp: str, trade_date: date, *, generate: bool) -> Path:
    path = REPORTS / f"features_{day_stamp}.csv"
    if path.is_file() or not generate:
        return path
    subprocess.run(
        [
            sys.executable,
            str(PHASE113),
            "--day-stamp",
            day_stamp,
            "--trade-date",
            trade_date.isoformat(),
        ],
        cwd=str(ROOT),
        timeout=3600,
    )
    return path


def main() -> int:
    _bootstrap()
    from universe.am_pm_universe import (
        AM_UNIVERSE_FIELDS,
        PM_UNIVERSE_FIELDS,
        FOCUS_SYMBOLS,
        build_am_universe_rows,
        build_diff_rows,
        build_limit_diagnostics,
        build_pm_universe_rows,
        compare_am_pm,
        determine_verdict,
        session_close_design,
        write_csv,
    )
    from universe.daily_features import load_features_csv
    from universe.dynamic_build import load_dynamic_config, resolve_symbol_master

    parser = argparse.ArgumentParser(description="Phase 114 AM/PM universe design")
    parser.add_argument("--day-stamp", default=None, help="8-digit JST trade date (e.g. 20260521)")
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--no-generate", action="store_true")
    args = parser.parse_args()

    from universe.day_stamp import normalize_day_stamp

    day_stamp = (
        normalize_day_stamp(args.day_stamp)
        if args.day_stamp
        else datetime.now(JST).strftime("%Y%m%d")
    )
    if args.trade_date:
        trade_d = date.fromisoformat(args.trade_date)
        day_stamp = trade_d.strftime("%Y%m%d")
    else:
        trade_d = date(int(day_stamp[:4]), int(day_stamp[4:6]), int(day_stamp[6:8]))

    feat_path = ensure_features(day_stamp, trade_d, generate=not args.no_generate)
    features_exists = feat_path.is_file()
    feature_rows = load_features_csv(feat_path) if features_exists else []

    cfg = load_dynamic_config(NATIVE / "configs" / "universe_dynamic_trial.yaml")
    _, entries = resolve_symbol_master(ROOT, cfg.symbol_master_paths)
    symbol_meta: dict[str, dict[str, Any]] = {}
    for e in entries:
        sym = f"{e.parsed.code}.T"
        symbol_meta[sym] = {
            "exchange": e.parsed.exchange,
            "symbol_key": e.parsed.symbol_key,
            "market": e.market,
        }

    push_dir = PUSH_ROOT / trade_d.isoformat()
    am_rows = build_am_universe_rows(feature_rows, symbol_meta=symbol_meta) if features_exists else []
    pm_rows, pm_cov = (
        build_pm_universe_rows(feature_rows, symbol_meta=symbol_meta, push_day_dir=push_dir)
        if features_exists
        else ([], {})
    )

    comparison = compare_am_pm(am_rows, pm_rows) if am_rows and pm_rows else {}
    diff_rows = build_diff_rows(am_rows, pm_rows)

    from universe.am_pm_universe import _norm

    feat_by_sym = {_norm(r["symbol"]): r for r in feature_rows}
    diag_syms = sorted({_norm(r["symbol"]) for r in am_rows} | {_norm(r["symbol"]) for r in pm_rows})
    limit_rows = build_limit_diagnostics(
        diag_syms, feature_by_sym=feat_by_sym, symbol_meta=symbol_meta, push_day_dir=push_dir
    )

    limit_stats = {
        "is_limit_up": sum(1 for r in limit_rows if r.get("is_limit_up")),
        "is_limit_down": sum(1 for r in limit_rows if r.get("is_limit_down")),
        "near_limit_up": sum(1 for r in limit_rows if r.get("near_limit_up")),
        "near_limit_down": sum(1 for r in limit_rows if r.get("near_limit_down")),
        "shadow_exclude_candidate": sum(1 for r in limit_rows if r.get("shadow_exclude_candidate")),
    }

    am_set = {_norm(r["symbol"]) for r in am_rows}
    pm_set = {_norm(r["symbol"]) for r in pm_rows}
    pm_meta = {p["symbol"]: p for p in pm_rows}
    liquidity_notes = {
        "am_only_depleted_at_pm": [],
        "pm_new_liquidity": sorted(pm_set - am_set),
    }
    for sym in sorted(am_set - pm_set):
        liquidity_notes["am_only_depleted_at_pm"].append(sym)

    verdict, verdict_notes = determine_verdict(
        features_exists=features_exists,
        am_count=len(am_rows),
        pm_count=len(pm_rows),
        push_morning_n=int(pm_cov.get("morning_push_symbols") or 0),
        comparison=comparison,
        limit_rows=limit_rows,
    )

    focus = {}
    for sym in FOCUS_SYMBOLS:
        focus[sym] = {
            "in_am": sym in am_set,
            "in_pm": sym in pm_set,
            "am_rank": next((r["rank"] for r in am_rows if _norm(r["symbol"]) == sym), None),
            "pm_rank": next((r["rank"] for r in pm_rows if _norm(r["symbol"]) == sym), None),
        }

    report: dict[str, Any] = {
        "phase": 114,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "verdict_options": {
            "A": "am_pm_screening_design_ready",
            "B": "need_limit_price_source",
            "C": "need_intraday_liquidity_source",
            "D": "am_pm_screening_not_worthwhile",
        },
        "trade_date": trade_d.isoformat(),
        "design": {
            "am_screening": {
                "time": "09:05",
                "driver": "previous_day_vol_liq_score",
                "source_bucket": "am_vol_liq_dynamic50",
            },
            "pm_screening": {
                "time": "12:20-12:25",
                "driver": "pm_composite: prev_vol_liq + morning push + pm push liquidity",
                "source_bucket": "pm_vol_liq_dynamic50",
            },
            "session_close": session_close_design(),
            "limit_screening": {
                "mode": "shadow_diagnostic_only",
                "exclude_not_applied_to_production": True,
                "fields": [
                    "daily_limit_up_price",
                    "daily_limit_down_price",
                    "distance_to_limit_up_pct",
                    "distance_to_limit_down_pct",
                    "is_limit_up",
                    "is_limit_down",
                    "near_limit_up",
                    "near_limit_down",
                ],
            },
        },
        "pm_push_coverage": pm_cov,
        "comparison": comparison,
        "limit_status_summary": limit_stats,
        "liquidity_flow": liquidity_notes,
        "focus_diagnostics": focus,
        "outputs": {
            "phase114_json": _rel(REPORTS / "phase114_am_pm_universe_design.json"),
            "am_csv": _rel(REPORTS / f"phase114_am_universe_dynamic50_{day_stamp}.csv"),
            "pm_csv": _rel(REPORTS / f"phase114_pm_universe_dynamic50_{day_stamp}.csv"),
            "diff_csv": _rel(REPORTS / f"phase114_am_pm_universe_diff_{day_stamp}.csv"),
            "limit_csv": _rel(REPORTS / f"phase114_limit_status_diagnostics_{day_stamp}.csv"),
        },
        "constraints": [
            "no_production_pilot_yaml_change",
            "no_symbol_hardcode_add_exclude",
            "no_time_of_day_entry_filter_in_production",
            "shadow_dry_run_only",
            "no_pf_evaluation",
        ],
    }

    out_json = REPORTS / f"phase114_am_pm_universe_design.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    write_csv(REPORTS / f"phase114_am_universe_dynamic50_{day_stamp}.csv", am_rows, AM_UNIVERSE_FIELDS)
    write_csv(REPORTS / f"phase114_pm_universe_dynamic50_{day_stamp}.csv", pm_rows, PM_UNIVERSE_FIELDS)
    write_csv(REPORTS / f"phase114_am_pm_universe_diff_{day_stamp}.csv", diff_rows, ("symbol", "in_am_universe", "in_pm_universe", "change_type"))
    if limit_rows:
        write_csv(
            REPORTS / f"phase114_limit_status_diagnostics_{day_stamp}.csv",
            limit_rows,
            tuple(limit_rows[0].keys()),
        )

    print(
        json.dumps(
            {
                "verdict": verdict,
                "churn_rate": comparison.get("churn_rate"),
                "overlap": comparison.get("overlap_count"),
                "limit_up": limit_stats.get("is_limit_up"),
            },
            ensure_ascii=True,
        )
    )
    return 0 if verdict == "am_pm_screening_design_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
