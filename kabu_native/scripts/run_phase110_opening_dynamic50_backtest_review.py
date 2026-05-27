#!/usr/bin/env python3
"""
Phase 110: Backtest hero-symbol coverage — static27 vs opening_dynamic50 (review only).
"""

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
PHASE108 = NATIVE / "scripts" / "run_phase108_opening_screen_design.py"
TARGET_DAYS = ("2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22")


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


def ensure_opening_0905(day_stamp: str, *, generate: bool) -> Path:
    path = REPORTS / f"opening_dynamic50_0905_{day_stamp}.csv"
    if path.is_file():
        return path
    if not generate:
        return path
    subprocess.run(
        [
            sys.executable,
            str(PHASE108),
            "--trade-date",
            f"{day_stamp[:4]}-{day_stamp[4:6]}-{day_stamp[6:8]}",
            "--yfinance-max",
            "600",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    return path


def find_live_session(day_stamp: str) -> Path | None:
    day_dir = SMALL_PAPER / day_stamp
    if not day_dir.is_dir():
        return None
    live = sorted(day_dir.glob("live_full_session_*"))
    return live[0] if live else None


def determine_verdict(daily_rows: list[dict[str, Any]]) -> tuple[str, list[str]]:
    notes: list[str] = []
    valid = [r for r in daily_rows if r.get("opening_dynamic50_path_exists")]
    if len(valid) < 2:
        return "insufficient_historical_data", ["fewer than 2 days with opening_dynamic50 CSV"]

    avg_static = sum(float(r.get("static27_hit_rate_top20") or 0) for r in valid) / len(valid)
    avg_open = sum(float(r.get("opening_hit_rate_top20") or 0) for r in valid) / len(valid)
    avg_static_hits = sum(int(r.get("static27_hero_top20_hits") or 0) for r in valid) / len(valid)
    avg_open_hits = sum(int(r.get("opening_hero_top20_hits") or 0) for r in valid) / len(valid)

    notes.append(
        f"avg hit_rate top20 static27={avg_static:.2%} opening50={avg_open:.2%}; "
        f"avg hits static={avg_static_hits:.1f} opening={avg_open_hits:.1f}"
    )

    if avg_open > avg_static + 0.03 or avg_open_hits >= avg_static_hits + 1.5:
        return "opening_dynamic50_coverage_improved", notes
    if avg_open + 0.02 < avg_static and avg_open_hits < avg_static_hits - 1:
        return "opening_dynamic50_worse_than_static", notes
    if avg_open == avg_static and abs(avg_open_hits - avg_static_hits) < 1:
        return "opening_dynamic50_no_clear_improvement", notes
    if avg_open >= avg_static:
        return "opening_dynamic50_no_clear_improvement", notes + ["opening slightly better but below threshold"]
    return "opening_dynamic50_worse_than_static", notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 110 hero coverage backtest")
    parser.add_argument("--generate-missing", action="store_true", default=True)
    parser.add_argument("--no-generate", action="store_true")
    parser.add_argument("--yfinance-max", type=int, default=1200)
    args = parser.parse_args()
    generate = args.generate_missing and not args.no_generate

    _bootstrap()
    from universe.dynamic_build import load_dynamic_config, resolve_symbol_master
    from universe.hero_backtest import (
        augment_hero_with_session,
        build_hero_definition,
        compare_universes,
        load_static27,
        load_symbol_set_from_csv,
        link_session_to_opening,
        load_session_activity,
    )

    cfg = load_dynamic_config(NATIVE / "configs" / "universe_dynamic_trial.yaml")
    _, master_entries = resolve_symbol_master(ROOT, cfg.symbol_master_paths)
    master_symbols = [f"{e.parsed.code}.T" for e in master_entries]
    static27 = load_static27(NATIVE)

    daily_rows: list[dict[str, Any]] = []
    hero_rows: list[dict[str, Any]] = []
    compare_rows: list[dict[str, Any]] = []
    per_day_detail: dict[str, Any] = {}

    for trade_date in TARGET_DAYS:
        td = date.fromisoformat(trade_date)
        day_stamp = _day_stamp(trade_date)
        opening_path = ensure_opening_0905(day_stamp, generate=generate)
        opening_exists = opening_path.is_file()
        opening50 = load_symbol_set_from_csv(opening_path) if opening_exists else set()

        push_dir = PUSH_ROOT / trade_date
        hero_def = build_hero_definition(
            trade_date=td,
            master_symbols=master_symbols,
            push_day_dir=push_dir,
            yfinance_max=args.yfinance_max,
        )

        session_dir = find_live_session(day_stamp)
        session_link: dict[str, Any] = {"found": False}
        if session_dir:
            act = load_session_activity(session_dir)
            augment_hero_with_session(hero_def, act)
            session_link = link_session_to_opening(act, opening50, static27)
            cand_top = set(act.get("candidate_top20") or [])
            acc_syms = set(act.get("accepted_symbols") or [])
            session_link["opening50_accepted_count"] = len(acc_syms & opening50)
            session_link["static27_accepted_count"] = len(acc_syms & static27)
            session_link["accepted_not_in_opening50"] = sorted(acc_syms - opening50)
            session_link["accepted_not_in_static27"] = sorted(acc_syms - static27)
            session_link["opening50_candidates_in_top20"] = len(cand_top & opening50)
            session_link["static27_candidates_in_top20"] = len(cand_top & static27)

        cmp = compare_universes(
            hero_top20=hero_def.hero_top20,
            hero_top10=hero_def.hero_top10,
            static27=static27,
            opening50=opening50,
        )

        daily_rows.append(
            {
                "trade_date": trade_date,
                "day_stamp": day_stamp,
                "opening_dynamic50_path_exists": opening_exists,
                "opening_dynamic50_count": len(opening50),
                "static27_count": len(static27),
                "hero_top20_count": len(hero_def.hero_top20),
                "hero_metrics_symbols": len(hero_def.metrics_by_symbol),
                "static27_hero_top20_hits": cmp["hero_top20_hit_count"]["static27"],
                "opening_hero_top20_hits": cmp["hero_top20_hit_count"]["opening_dynamic50"],
                "static27_hit_rate_top20": cmp["hit_rate"]["static27"],
                "opening_hit_rate_top20": cmp["hit_rate"]["opening_dynamic50"],
                "static27_hero_top10_hits": cmp["hero_top10_hit_count"]["static27"],
                "opening_hero_top10_hits": cmp["hero_top10_hit_count"]["opening_dynamic50"],
                "overlap_static_opening": cmp["overlap_count"],
                "dynamic_only_hit_heroes": "|".join(cmp["dynamic_only_hits"]),
                "static_only_hit_heroes": "|".join(cmp["static_only_hits"]),
                "missed_heroes_opening": "|".join(cmp["hero_top20"]["opening_dynamic50"]["missed_heroes"][:15]),
                "session_found": session_link.get("found", False),
                "opening50_accepted_count": session_link.get("opening50_accepted_count"),
                "proxy_notes": "|".join(hero_def.proxy_notes),
            }
        )

        for sym in sorted(hero_def.hero_top20):
            m = hero_def.metrics_by_symbol.get(sym, {})
            hero_rows.append(
                {
                    "trade_date": trade_date,
                    "symbol": sym,
                    "in_hero_top20": True,
                    "in_hero_top10": sym in hero_def.hero_top10,
                    "in_static27": sym in static27,
                    "in_opening_dynamic50": sym in opening50,
                    "change_pct": m.get("change_pct"),
                    "trading_value_proxy": m.get("trading_value_proxy"),
                    "range_pct": m.get("range_pct"),
                    "volume_surge_5": m.get("volume_surge_5"),
                    "metric_source": m.get("data_source"),
                }
            )

        for metric_key, hit_key in (("hero_top10", "hero_top10_hit_count"), ("hero_top20", "hero_top20_hit_count")):
            block = cmp[metric_key]
            compare_rows.append(
                {
                    "trade_date": trade_date,
                    "metric": metric_key,
                    "static27_hits": cmp[hit_key]["static27"],
                    "opening_dynamic50_hits": cmp[hit_key]["opening_dynamic50"],
                    "static27_hit_rate": block["static27"]["hit_rate"],
                    "opening_hit_rate": block["opening_dynamic50"]["hit_rate"],
                    "overlap_count": cmp["overlap_count"],
                    "dynamic_only_hits": "|".join(cmp["dynamic_only_hits"]),
                    "static_only_hits": "|".join(cmp["static_only_hits"]),
                    "missed_heroes_opening": "|".join(block["opening_dynamic50"]["missed_heroes"][:20]),
                    "missed_heroes_static27": "|".join(block["static27"]["missed_heroes"][:20]),
                }
            )

        per_day_detail[trade_date] = {
            "hero_definition": {
                "proxy_notes": hero_def.proxy_notes,
                "hero_sources": hero_def.hero_sources,
            },
            "comparison": cmp,
            "session": session_link,
            "focus": cmp["focus_diagnostics"],
        }

    verdict, verdict_notes = determine_verdict(daily_rows)

    report: dict[str, Any] = {
        "phase": 110,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "verdict_options": {
            "A": "opening_dynamic50_coverage_improved",
            "B": "opening_dynamic50_no_clear_improvement",
            "C": "insufficient_historical_data",
            "D": "opening_dynamic50_worse_than_static",
        },
        "target_days": list(TARGET_DAYS),
        "hero_definition_doc": {
            "hero_top20": "union of top20 by change_pct, trading_value_proxy, range_pct, volume_surge_5",
            "hero_top10": "union of top10 by change_pct and trading_value_proxy",
            "primary_data": "yfinance daily T vs T-1 (capped symbols) + push_jsonl EOD for watchlist",
            "session_proxy": "small_paper candidate frequency top20 when session exists",
            "not_pf_evaluation": True,
        },
        "per_day": per_day_detail,
        "constraints": [
            "review_only_no_pilot_yaml_change",
            "no_symbol_hardcode_add_exclude",
            "focus_3905_6613_diagnostic_only",
        ],
    }

    out_json = REPORTS / "phase110_opening_dynamic50_backtest_review.json"
    daily_csv = REPORTS / "phase110_daily_coverage.csv"
    hero_csv = REPORTS / "phase110_hero_symbol_coverage.csv"
    cmp_csv = REPORTS / "phase110_static27_vs_opening_dynamic50.csv"

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for path, rows, fields in (
        (daily_csv, daily_rows, None),
        (hero_csv, hero_rows, None),
        (cmp_csv, compare_rows, None),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            if rows:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)

    print(
        json.dumps(
            {
                "verdict": verdict,
                "json": _rel(out_json),
                "days": len(daily_rows),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
