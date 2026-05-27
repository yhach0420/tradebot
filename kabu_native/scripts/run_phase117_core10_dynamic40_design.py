#!/usr/bin/env python3
"""Phase 117: Core10 (Discord) + Dynamic40 (vol_liq) universe design and 4-day comparison."""

from __future__ import annotations

import argparse
import csv
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
SMALL_PAPER = NATIVE / "results" / "small_paper"
PHASE113 = NATIVE / "scripts" / "run_phase113_vol_liq_dynamic50_universe.py"
TARGET_DAYS = ("2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22")
DISCORD_BOT = ROOT / "discord_issue_bot" / "discord_issue_bot.py"


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


def _day_stamp(trade_date: str) -> str:
    return trade_date.replace("-", "")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: tuple[str, ...] | None = None) -> None:
    if not rows:
        return
    cols = fields or tuple(rows[0].keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cols), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in cols})


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


def discord_enforcement_ok() -> bool:
    if not DISCORD_BOT.is_file():
        return False
    text = DISCORD_BOT.read_text(encoding="utf-8")
    return (
        "can_add_to_core" in text
        and "CORE_LIMIT_MESSAGE" in text
        and "REJECT_CORE_LIMIT_EXCEEDED" in text
    )


def find_live_session(day_stamp: str) -> Path | None:
    day_dir = SMALL_PAPER / day_stamp
    if not day_dir.is_dir():
        return None
    live = sorted(day_dir.glob("live_full_session_*"))
    return live[0] if live else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 117 Core10+Dynamic40 design")
    parser.add_argument("--generate-missing", action="store_true", default=True)
    parser.add_argument("--no-generate", action="store_true")
    parser.add_argument("--yfinance-max", type=int, default=1200)
    parser.add_argument(
        "--skip-hero",
        action="store_true",
        help="Skip yfinance hero comparison (faster; uses zeros for hero metrics)",
    )
    args = parser.parse_args()
    generate = args.generate_missing and not args.no_generate

    _bootstrap()
    from universe.am_pm_universe import _norm
    from universe.core10_dynamic40 import (
        CORE_SLOTS,
        build_am_universe,
        build_core_inventory,
        build_pm_universe,
        compare_universe_sets,
        determine_verdict,
        universe_am_path,
        universe_pm_path,
        validate_universe,
        write_universe_csv,
    )
    from universe.core_watchlist import (
        CORE_LIMIT,
        load_core_watchlist,
        resolve_core_symbol_source_path,
    )
    from universe.daily_features import load_features_csv
    from universe.dynamic_build import load_dynamic_config, resolve_symbol_master
    from universe.hero_backtest import (
        build_hero_definition,
        load_static27,
        load_symbol_set_from_csv,
        load_session_activity,
    )

    source_info = resolve_core_symbol_source_path(ROOT)
    core_symbols, _write_path = load_core_watchlist(ROOT)
    enforcement_ok = discord_enforcement_ok()

    cfg = load_dynamic_config(NATIVE / "configs" / "universe_dynamic_trial.yaml")
    _, master_entries = resolve_symbol_master(ROOT, cfg.symbol_master_paths)
    symbol_meta: dict[str, dict[str, Any]] = {}
    for e in master_entries:
        sym = f"{e.parsed.code}.T"
        symbol_meta[sym] = {
            "exchange": e.parsed.exchange,
            "symbol_key": e.parsed.symbol_key,
            "market": e.market,
        }

    static27 = load_static27(NATIVE)
    inventory = build_core_inventory(core_symbols, symbol_meta=symbol_meta)
    inv_csv = REPORTS / "phase117_core10_inventory.csv"
    _write_csv(
        inv_csv,
        inventory,
        ("rank", "symbol", "valid", "reject_reason", "in_symbol_master", "exchange"),
    )

    daily_rows: list[dict[str, Any]] = []
    per_day: dict[str, Any] = {}

    for trade_date in TARGET_DAYS:
        td = date.fromisoformat(trade_date)
        day_stamp = _day_stamp(trade_date)
        feat_path = ensure_features(day_stamp, td, generate=generate)
        features = load_features_csv(feat_path) if feat_path.is_file() else []
        push_dir = PUSH_ROOT / trade_date

        am_rows = (
            build_am_universe(core_symbols=core_symbols, feature_rows=features, symbol_meta=symbol_meta)
            if features
            else []
        )
        pm_rows = (
            build_pm_universe(
                core_symbols=core_symbols,
                feature_rows=features,
                symbol_meta=symbol_meta,
                push_day_dir=push_dir,
            )
            if features
            else []
        )

        am_csv = universe_am_path(REPORTS, day_stamp)
        pm_csv = universe_pm_path(REPORTS, day_stamp)
        if am_rows:
            write_universe_csv(am_csv, am_rows)
        if pm_rows:
            write_universe_csv(pm_csv, pm_rows)

        am_val = validate_universe(am_csv, expected_session="am") if am_csv.is_file() else {"passed": False}
        pm_val = validate_universe(pm_csv, expected_session="pm") if pm_csv.is_file() else {"passed": False}

        vol_liq50 = load_symbol_set_from_csv(REPORTS / f"universe_vol_liq_dynamic50_{day_stamp}.csv")
        core40_am = {_norm(r["symbol"]) for r in am_rows}
        core40_pm = {_norm(r["symbol"]) for r in pm_rows}

        if args.skip_hero:
            hero_def = type("H", (), {"hero_top20": set(), "hero_top10": set()})()
        else:
            hero_def = build_hero_definition(
                trade_date=td,
                master_symbols=[f"{e.parsed.code}.T" for e in master_entries],
                push_day_dir=push_dir,
                yfinance_max=args.yfinance_max,
            )

        session_dir = find_live_session(day_stamp)
        act: dict[str, Any] = {}
        if session_dir:
            act = load_session_activity(session_dir)

        cmp = compare_universe_sets(
            static27=static27,
            vol_liq50=vol_liq50,
            core40_am=core40_am,
            core40_pm=core40_pm,
            hero_top20=hero_def.hero_top20,
        )

        cand_top = set(act.get("candidate_top20") or [])
        acc_syms = set(act.get("accepted_symbols") or [])

        daily_rows.append(
            {
                "trade_date": trade_date,
                "features_exists": feat_path.is_file(),
                "core_count": len(core_symbols),
                "am_universe_count": len(am_rows),
                "pm_universe_count": len(pm_rows),
                "static27_count": len(static27),
                "vol_liq50_count": len(vol_liq50),
                "static27_hero_top20_hits": cmp["static27"]["hit_count"],
                "vol_liq50_hero_top20_hits": cmp["vol_liq_dynamic50"]["hit_count"],
                "core40_am_hero_top20_hits": cmp["core10_dynamic40_am"]["hit_count"],
                "core40_pm_hero_top20_hits": cmp["core10_dynamic40_pm"]["hit_count"],
                "static27_hit_rate": cmp["static27"]["hit_rate"],
                "vol_liq50_hit_rate": cmp["vol_liq_dynamic50"]["hit_rate"],
                "core40_am_hit_rate": cmp["core10_dynamic40_am"]["hit_rate"],
                "overlap_static_vol_liq": cmp["overlap"]["static27_vol_liq50"],
                "overlap_static_core40_am": cmp["overlap"]["static27_core40_am"],
                "overlap_vol_liq_core40_am": cmp["overlap"]["vol_liq50_core40_am"],
                "session_found": bool(session_dir),
                "candidate_count_unique": act.get("candidate_unique", 0),
                "accepted_count_unique": act.get("accepted_unique", 0),
                "core40_am_candidates_in_top20": len(cand_top & core40_am),
                "static27_candidates_in_top20": len(cand_top & static27),
                "vol_liq50_candidates_in_top20": len(cand_top & vol_liq50),
                "core40_am_accepted_count": len(acc_syms & core40_am),
                "static27_accepted_count": len(acc_syms & static27),
                "vol_liq50_accepted_count": len(acc_syms & vol_liq50),
                "3905_in_static27": cmp["focus"]["3905.T"]["in_static27"],
                "3905_in_vol_liq50": cmp["focus"]["3905.T"]["in_vol_liq50"],
                "3905_in_core40_am": cmp["focus"]["3905.T"]["in_core40_am"],
                "6613_in_static27": cmp["focus"]["6613.T"]["in_static27"],
                "6613_in_vol_liq50": cmp["focus"]["6613.T"]["in_vol_liq50"],
                "6613_in_core40_am": cmp["focus"]["6613.T"]["in_core40_am"],
            }
        )
        per_day[trade_date] = {
            "comparison": cmp,
            "session": act,
            "am_validation": am_val,
            "pm_validation": pm_val,
        }

    valid_days = [r for r in daily_rows if r.get("features_exists")]
    comparison_avg: dict[str, Any] = {}
    if valid_days:
        n = len(valid_days)
        comparison_avg = {
            "static27_hero_hit_rate": sum(float(r["static27_hit_rate"] or 0) for r in valid_days) / n,
            "vol_liq50_hero_hit_rate": sum(float(r["vol_liq50_hit_rate"] or 0) for r in valid_days) / n,
            "core40_am_hero_hit_rate": sum(float(r["core40_am_hit_rate"] or 0) for r in valid_days) / n,
            "days": n,
        }

    ref_day = valid_days[-1]["trade_date"] if valid_days else TARGET_DAYS[-1]
    last_am_val = per_day.get(ref_day, {}).get("am_validation", {})
    last_pm_val = per_day.get(ref_day, {}).get("pm_validation", {})

    verdict, verdict_notes = determine_verdict(
        source_info=source_info,
        core_count=len(core_symbols),
        am_val=last_am_val if last_am_val else {"total_count": 0, "passed": False},
        pm_val=last_pm_val if last_pm_val else {"total_count": 0, "passed": False},
        enforcement_ok=enforcement_ok,
        comparison_avg=comparison_avg,
    )

    report: dict[str, Any] = {
        "phase": 117,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "verdict_options": {
            "A": "core10_dynamic40_ready",
            "B": "need_core_symbol_source",
            "C": "core_limit_enforcement_missing",
            "D": "dynamic40_not_improving_coverage",
        },
        "core_symbol_source": source_info,
        "core_watchlist": {
            "count": len(core_symbols),
            "limit": CORE_LIMIT,
            "symbols": core_symbols,
            "write_path": str(_write_path),
        },
        "design": {
            "core10": {
                "origin": "discord_watchlist",
                "max_slots": CORE_SLOTS,
                "reject_reason_overflow": "core_limit_exceeded",
            },
            "dynamic40": {
                "driver_am": "vol_liq_top40_exclude_core",
                "driver_pm": "pm_composite_top40_exclude_core_with_vol_liq_fill",
                "fill_policy": "total=50 when features allow",
            },
            "universe": "Core10 + Dynamic40 <= 50, dedupe on dynamic side",
        },
        "discord_enforcement": {
            "core_limit_in_bot_source": enforcement_ok,
            "reject_message": (
                "Core watchlist limit reached (10/10).\n"
                "Remove an existing symbol before adding a new one."
            ),
        },
        "comparison_avg": comparison_avg,
        "target_days": list(TARGET_DAYS),
        "per_day": per_day,
        "daily_summary": daily_rows,
        "outputs": {
            "phase117_json": _rel(REPORTS / "phase117_core10_dynamic40_design.json"),
            "core_inventory_csv": _rel(inv_csv),
            "universe_am_csv_pattern": _rel(REPORTS / "universe_core10_dynamic40_am_YYYYMMDD.csv"),
            "universe_pm_csv_pattern": _rel(REPORTS / "universe_core10_dynamic40_pm_YYYYMMDD.csv"),
        },
        "constraints": [
            "no_production_pilot_yaml_change",
            "no_overwrite_universe_intraday_full",
            "no_entry_exit_quality_change",
            "no_symbol_hardcode_add_exclude",
            "shadow_dry_run_only",
            "no_pf_evaluation",
        ],
    }

    out_json = REPORTS / "phase117_core10_dynamic40_design.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "verdict": verdict,
                "core_count": len(core_symbols),
                "core_symbol_source_path": source_info.get("core_symbol_source_path"),
                "comparison_avg": comparison_avg,
            },
            ensure_ascii=True,
        )
    )
    return 0 if verdict == "core10_dynamic40_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
