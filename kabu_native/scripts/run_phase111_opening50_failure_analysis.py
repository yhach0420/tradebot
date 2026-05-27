#!/usr/bin/env python3
"""Phase 111: Decompose opening_dynamic50 failure vs static27 (review only)."""

from __future__ import annotations

import argparse
import csv
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
PUSH_ROOT = NATIVE / "data" / "push_jsonl"
SMALL_PAPER = NATIVE / "results" / "small_paper"


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


def _day_stamp(d: str) -> str:
    return d.replace("-", "")


def find_live_session(day_stamp: str) -> Path | None:
    day_dir = SMALL_PAPER / day_stamp
    if not day_dir.is_dir():
        return None
    live = sorted(day_dir.glob("live_full_session_*"))
    return live[0] if live else None


def symbol_score_to_row(d: Any) -> dict[str, Any]:
    return {
        "trade_date": d.trade_date,
        "symbol": d.symbol,
        "in_yfinance_cap": d.in_yfinance_cap,
        "master_index": d.master_index,
        "yfinance_prev_fetched": d.yfinance_prev_fetched,
        "has_push_jsonl": d.has_push_jsonl,
        "has_opening_push_0905": d.has_opening_push_0905,
        "previous_day_vol_liq_score": d.previous_day_vol_liq_score,
        "early_momentum_score": d.early_momentum_score,
        "early_trading_value_score": d.early_trading_value_score,
        "early_range_score": d.early_range_score,
        "opening_daytrade_score": d.opening_daytrade_score,
        "rank_among_scored": d.rank_among_scored,
        "rank_total_pool": d.rank_total_pool,
        "gap_to_top50_score": d.gap_to_top50_score,
        "in_opening_dynamic50": d.in_opening_dynamic50,
        "in_static27": d.in_static27,
        "in_hero_top20": d.in_hero_top20,
        "hero_reasons": "|".join(d.hero_reasons),
        "failure_tags": "|".join(d.failure_tags),
    }


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser(description="Phase 111 opening50 failure analysis")
    parser.add_argument("--yfinance-cap", type=int, default=600)
    args = parser.parse_args()

    from universe.dynamic_build import load_dynamic_config, resolve_symbol_master
    from universe.failure_analysis import (
        FOCUS_DAY,
        FOCUS_SYMBOLS,
        TARGET_DAYS,
        SymbolScoreDetail,
        analyze_focus_symbol,
        build_20260521_session_rows,
        classify_day_failure,
        determine_verdict,
        load_opening_csv_rows,
        master_index_map,
    )
    from universe.hero_backtest import augment_hero_with_session, build_hero_definition, load_static27

    cfg = load_dynamic_config(NATIVE / "configs" / "universe_dynamic_trial.yaml")
    _, entries = resolve_symbol_master(ROOT, cfg.symbol_master_paths)
    all_symbols = [f"{e.parsed.code}.T" for e in entries]
    index_map = master_index_map(all_symbols)
    static27 = load_static27(NATIVE)

    focus_rows: list[dict[str, Any]] = []
    breakdown_rows: list[dict[str, Any]] = []
    per_day: dict[str, Any] = {}

    for trade_date in TARGET_DAYS:
        td = date.fromisoformat(trade_date)
        day_stamp = _day_stamp(trade_date)
        opening_path = REPORTS / f"opening_dynamic50_0905_{day_stamp}.csv"
        push_dir = PUSH_ROOT / trade_date
        p108 = REPORTS / f"phase108_opening_screen_design_{day_stamp}.json"
        phase108 = json.loads(p108.read_text(encoding="utf-8")) if p108.is_file() else None

        hero_def = build_hero_definition(
            trade_date=td,
            master_symbols=all_symbols,
            push_day_dir=push_dir,
            yfinance_max=1200,
        )
        session_dir = find_live_session(day_stamp)
        if session_dir:
            from universe.hero_backtest import load_session_activity

            augment_hero_with_session(hero_def, load_session_activity(session_dir))

        opening_rows = load_opening_csv_rows(opening_path)
        bd = classify_day_failure(
            trade_date=trade_date,
            opening_rows=opening_rows,
            static27=static27,
            hero_def=hero_def,
            phase108_json=phase108,
            yfinance_cap=args.yfinance_cap,
            index_map=index_map,
            session_found=session_dir is not None,
        )
        breakdown_rows.append(bd)

        for sym in FOCUS_SYMBOLS:
            detail = analyze_focus_symbol(
                sym,
                trade_date=td,
                all_symbols=all_symbols,
                index_map=index_map,
                yfinance_cap=args.yfinance_cap,
                push_day_dir=push_dir,
                opening_path=opening_path,
                static27=static27,
                hero_def=hero_def,
            )
            focus_rows.append(symbol_score_to_row(detail))

        per_day[trade_date] = {
            "phase108": phase108,
            "breakdown": bd,
            "opening50_has_opening_push_count": sum(
                1 for r in opening_rows if str(r.get("has_opening_push")).lower() == "true"
            ),
            "opening50_top5": [
                {
                    "symbol": r.get("symbol"),
                    "score": r.get("opening_daytrade_score"),
                    "prev_vol_liq": r.get("previous_day_vol_liq_score"),
                    "has_push": r.get("has_opening_push"),
                }
                for r in opening_rows[:5]
            ],
        }

    session_0521_rows: list[dict[str, Any]] = []
    day_stamp = _day_stamp(FOCUS_DAY)
    session_dir = find_live_session(day_stamp)
    opening_path = REPORTS / f"opening_dynamic50_0905_{day_stamp}.csv"
    hero_21 = build_hero_definition(
        trade_date=date.fromisoformat(FOCUS_DAY),
        master_symbols=all_symbols,
        push_day_dir=PUSH_ROOT / FOCUS_DAY,
        yfinance_max=1200,
    )
    if session_dir:
        from universe.hero_backtest import load_session_activity

        augment_hero_with_session(hero_21, load_session_activity(session_dir))
    if session_dir:
        session_0521_rows = build_20260521_session_rows(
            opening_path=opening_path,
            static27=static27,
            session_dir=session_dir,
            hero_def=hero_21,
        )

    focus_details = []
    for row in focus_rows:
        focus_details.append(
            SymbolScoreDetail(
                symbol=row["symbol"],
                trade_date=row["trade_date"],
                in_yfinance_cap=row["in_yfinance_cap"],
                master_index=row["master_index"],
                yfinance_prev_fetched=row["yfinance_prev_fetched"],
                has_push_jsonl=row["has_push_jsonl"],
                has_opening_push_0905=row["has_opening_push_0905"],
                failure_tags=row["failure_tags"].split("|") if row["failure_tags"] else [],
            )
        )
    verdict, verdict_notes = determine_verdict(breakdown_rows, focus_details)

    design_options = [
        {
            "option": "expand_yfinance_cap_to_full_master",
            "description": "Fetch previous-day features for all 3575 symbols (not first 600)",
            "addresses": ["yfinance_cap_sampling_bias", "previous_day_data_missing"],
        },
        {
            "option": "persist_daily_ohlcv_store",
            "description": "Local daily OHLCV cache to avoid cap sampling and repeated yfinance",
            "addresses": ["previous_day_data_missing", "yfinance_cap_sampling_bias"],
        },
        {
            "option": "opening_push_50_or_minute_bars",
            "description": "09:05 opening features for >=50 symbols via PUSH or 1m store",
            "addresses": ["early_intraday_data_missing"],
        },
        {
            "option": "reweight_prev_vol_liq",
            "description": "Reduce 0.35 prev_vol_liq weight when opening data sparse",
            "addresses": ["score_formula_not_sensitive"],
        },
        {
            "option": "strengthen_volume_surge_or_gap",
            "description": "Add same-day gap/change/surge terms aligned with hero metrics",
            "addresses": ["hero_definition_mismatch", "score_formula_not_sensitive"],
        },
        {
            "option": "align_hero_backtest_definition",
            "description": "Compare universes using prev-day vol_liq heroes not same-day change",
            "addresses": ["hero_definition_mismatch"],
        },
    ]

    report: dict[str, Any] = {
        "phase": 111,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "verdict_options": {
            "A": "failure_due_to_data_coverage",
            "B": "failure_due_to_score_formula",
            "C": "failure_due_to_static_session_bias",
            "D": "mixed_failure",
        },
        "primary_findings": {
            "2026-05-21": {
                "static27_opening50_overlap": 0,
                "opening_0905_push_coverage": 0,
                "selection_driver": "previous_day_vol_liq among first ~600 master symbols only",
                "session_accepted_in_opening50": 0,
            },
            "3905.T": {
                "master_index": index_map.get("3905.T"),
                "in_yfinance_cap_600": False,
                "in_push_watchlist": False,
                "hero_driver": "same_day_change_pct_not_prev_vol_liq",
            },
        },
        "yfinance_cap_used": args.yfinance_cap,
        "per_day": per_day,
        "design_options": design_options,
        "constraints": ["review_only", "no_symbol_hardcode", "no_pilot_yaml_change"],
    }

    out_json = REPORTS / "phase111_opening50_failure_analysis.json"
    out_session = REPORTS / "phase111_20260521_opening_vs_session.csv"
    out_focus = REPORTS / "phase111_focus_3905_analysis.csv"
    out_breakdown = REPORTS / "phase111_failure_reason_breakdown.csv"

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for path, rows in (
        (out_session, session_0521_rows),
        (out_focus, focus_rows),
        (out_breakdown, breakdown_rows),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        if rows:
            with path.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)

    print(json.dumps({"verdict": verdict, "json": _rel(out_json)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
