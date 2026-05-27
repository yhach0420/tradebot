#!/usr/bin/env python3
"""Phase 118: Wire Core10 + Dynamic40 to AM/PM shadow live pipeline."""

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
SHADOW_PILOT_YAML = "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"


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


def runner_load_check(universe_csv: Path) -> dict[str, Any]:
    _bootstrap()
    from storage.symbol_sources import load_symbols

    syms = load_symbols(universe=universe_csv, native_root=NATIVE) if universe_csv.is_file() else []
    return {
        "passed": len(syms) == PUSH_LIMIT,
        "symbol_count": len(syms),
        "load_symbols_ok": len(syms) > 0,
        "path": _rel(universe_csv),
    }


def ensure_features(day_stamp: str, trade_date: date, *, generate: bool) -> Path:
    path = REPORTS / f"features_{day_stamp}.csv"
    if path.is_file() or not generate:
        return path
    subprocess.run(
        [sys.executable, str(PHASE113), "--day-stamp", day_stamp, "--trade-date", trade_date.isoformat()],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    return path


def build_phase118(day_stamp: str, *, generate_features: bool) -> dict[str, Any]:
    _bootstrap()
    from universe.core10_dynamic40_shadow import build_pipeline_report
    from universe.daily_features import load_features_csv
    from universe.dynamic_build import load_dynamic_config, resolve_symbol_master

    trade_d = date(int(day_stamp[:4]), int(day_stamp[4:6]), int(day_stamp[6:8]))
    feat_path = ensure_features(day_stamp, trade_d, generate=generate_features)
    features = load_features_csv(feat_path) if feat_path.is_file() else []
    push_dir = PUSH_ROOT / trade_d.isoformat()

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

    report = build_pipeline_report(
        repo_root=ROOT,
        reports_dir=REPORTS,
        day_stamp=day_stamp,
        trade_date=trade_d,
        feature_rows=features,
        push_day_dir=push_dir,
        symbol_meta=symbol_meta,
        am_runner_fn=runner_load_check,
        pm_runner_fn=runner_load_check,
    )
    report["generated_at"] = datetime.now(JST).isoformat(timespec="seconds")
    report["features_path"] = _rel(feat_path)
    report["features_exists"] = feat_path.is_file()
    report["outputs"]["phase118_json"] = _rel(REPORTS / f"phase118_core10_dynamic40_pipeline_{day_stamp}.json")
    report["outputs"]["phase118_runner_check_json"] = _rel(
        REPORTS / f"phase118_runner_check_{day_stamp}.json"
    )
    report["constraints"] = [
        "no_production_pilot_yaml_change",
        "no_overwrite_universe_intraday_full",
        "no_auto_order",
        "shadow_dry_run_only",
        "no_pf_evaluation",
    ]
    return report


def main() -> int:
    _bootstrap()
    from universe.day_stamp import normalize_day_stamp

    parser = argparse.ArgumentParser(description="Phase 118 Core10+Dynamic40 shadow pipeline")
    parser.add_argument("--day-stamp", default=None, help="8-digit trade date e.g. 20260521")
    parser.add_argument("--generate-features", action="store_true", help="Run phase113 if features missing")
    args = parser.parse_args()

    day_stamp = (
        normalize_day_stamp(args.day_stamp)
        if args.day_stamp
        else datetime.now(JST).strftime("%Y%m%d")
    )

    report = build_phase118(day_stamp, generate_features=args.generate_features)

    out_main = REPORTS / f"phase118_core10_dynamic40_pipeline_{day_stamp}.json"
    out_runner = REPORTS / f"phase118_runner_check_{day_stamp}.json"
    out_main.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_runner.write_text(
        json.dumps(
            {
                "phase": 118,
                "day_stamp": day_stamp,
                "verdict": report["verdict"],
                "core10_diagnosis": report["core10_diagnosis"],
                "runner_check": report["runner_check"],
                "universe_validation": report["universe_validation"],
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
                "am_csv": report["outputs"]["universe_am_csv"],
                "pm_csv": report["outputs"]["universe_pm_csv"],
                "core_count": report["core10_diagnosis"].get("core_count"),
            },
            ensure_ascii=True,
        )
    )
    return 0 if report["verdict"] == "core10_dynamic40_pipeline_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
