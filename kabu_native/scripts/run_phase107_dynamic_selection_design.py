#!/usr/bin/env python3
"""
Phase 107: Document current dynamic selection + dynamic50 design + data inventory.
No PF evaluation. Shadow / dry-run design only.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"
CONFIG = NATIVE / "configs" / "universe_dynamic_trial.yaml"
FOCUS = ("3905.T", "6613.T")


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


def _count_glob(base: Path, pattern: str) -> int:
    if not base.is_dir():
        return 0
    return sum(1 for _ in base.rglob(pattern))


def inventory_data_sources() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(
        source_id: str,
        path: str,
        exists: bool,
        coverage: str,
        fields_available: str,
        fields_missing_for_daytrade: str,
        usable_for_dynamic50: str,
        notes: str = "",
    ) -> None:
        rows.append(
            {
                "source_id": source_id,
                "path": path,
                "exists": exists,
                "coverage": coverage,
                "fields_available": fields_available,
                "fields_missing_for_daytrade": fields_missing_for_daytrade,
                "usable_for_dynamic50": usable_for_dynamic50,
                "notes": notes,
            }
        )

    tradable = ROOT / "data/jpx/tradable_symbols.csv"
    add(
        "jpx_tradable_master",
        _rel(tradable),
        tradable.is_file(),
        "~3575 tradable (4-digit + 162 alnum skipped at load)",
        "symbol,exchange,market,name,sector,scale,is_etf,is_reit,is_active",
        "close,volume,trading_value,ATR,prev_volume_avg",
        "market_label_only",
        "Phase105 dynamic pool input; no price/volume features",
    )

    static_u = NATIVE / "data/universe/universe_intraday_full.csv"
    add(
        "static_intraday_full",
        _rel(static_u),
        static_u.is_file(),
        "27 symbols fixed",
        "symbol,exchange,symbol_key,passed",
        "cross-sectional rank features",
        "static27_only_not_dynamic50",
        "Not overwritten by Phase105; user may deprecate for dynamic50",
    )

    daily_dir = NATIVE / "data/daily"
    daily_files = list(daily_dir.glob("**/*.csv")) if daily_dir.is_dir() else []
    add(
        "kabu_native_daily_store",
        _rel(daily_dir),
        daily_dir.is_dir(),
        f"{len(daily_files)} CSV files" if daily_files else "empty (.gitkeep only)",
        "none" if not daily_files else "per-file TBD",
        "OHLCV,trading_value,volume",
        "no" if not daily_files else "partial",
        "Planned store; not populated in repo",
    )

    intraday = ROOT / "data/intraday_1m"
    n_dates = len(list(intraday.iterdir())) if intraday.is_dir() else 0
    n_csv = _count_glob(intraday, "*.csv")
    add(
        "intraday_1m_archive",
        _rel(intraday),
        intraday.is_dir() and n_csv > 0,
        f"{n_dates} session dirs, {n_csv} symbol files (~27 names)",
        "timestamp_utc,open,high,low,close,volume",
        "full_market_coverage",
        "partial_subset_only",
        "Can derive intraday range for static-27 basket only; not market-wide",
    )

    push = NATIVE / "data/push_jsonl"
    n_push = _count_glob(push, "*.jsonl") if push.is_dir() else 0
    add(
        "kabu_push_jsonl",
        _rel(push),
        push.is_dir() and n_push > 0,
        f"{n_push} jsonl files (watched symbols)",
        "tick/board snapshots",
        "previous_day aggregates",
        "same_day_only",
        "Live session capture; not EOD universe rank",
    )

    u_daily = NATIVE / "data/universe"
    u_files = list(u_daily.glob("universe_*.csv")) if u_daily.is_dir() else []
    add(
        "build_universe_daily_csv",
        _rel(u_daily),
        bool(u_files),
        f"{len(u_files)} dated universe_*.csv",
        "board snapshot: trading_value,spread,current_price (when built)",
        "full_tradable_prev_day",
        "small_include_list_only",
        "build_universe.py uses include_symbols not full master",
    )

    ms = list((NATIVE / "results/morning_screen").rglob("morning_screen_*.csv")) if (
        NATIVE / "results/morning_screen"
    ).is_dir() else []
    add(
        "morning_screen_artifacts",
        "kabu_native/results/morning_screen/",
        bool(ms),
        f"{len(ms)} CSV artifacts",
        "screen score, pass_screen",
        "market-wide prev_day TV",
        "pipeline_dependent",
        "Not guaranteed daily for all tradable names",
    )

    add(
        "yfinance_stooq_bulk_daily",
        "(not stored in repo)",
        False,
        "0 — no persisted bulk daily bars",
        "N/A",
        "OHLCV,TV,volume,ATR inputs",
        "no",
        "Phase95/96 used yfinance ad-hoc for focus symbols only",
    )

    add(
        "kabu_board_batch",
        "GET /kabusapi/board/{code}@1",
        True,
        "max 50 register per session",
        "TradingValue,ChangePreviousClosePer,spread",
        "previous_day_EOD",
        "score_mode_max50_only",
        "Phase104: 400 bulk fetch hits register_limit; not prev-day rank",
    )

    add(
        "small_paper_vol_liq_gate",
        "runtime prior-session trades",
        True,
        "prior accepted trades only",
        "atr_pct,trading_value,volatility_liquidity_score",
        "ex-ante universe50",
        "entry_gate_not_universe_build",
        "Phase84 gate; different from morning universe selection",
    )

    return rows


def focus_stride_analysis(day_stamp: str) -> dict[str, Any]:
    _bootstrap()
    from universe.dynamic_build import (
        TRADABLE_MARKETS,
        _market_pools,
        _seed_int,
        load_dynamic_config,
        load_static_universe,
        resolve_symbol_master,
        stride_sample_positions,
    )

    cfg = load_dynamic_config(CONFIG)
    _, entries = resolve_symbol_master(ROOT, cfg.symbol_master_paths)
    static_path = ROOT / cfg.static_universe_path
    static_rows = load_static_universe(static_path, static_max=cfg.static_max)
    static_codes = {r["symbol"].replace(".T", "").upper() for r in static_rows}
    by_market = _market_pools(entries, static_codes)
    seed = cfg.sample_seed or day_stamp
    seed_int = _seed_int(seed)
    quota = cfg.dynamic_growth_quota

    out: dict[str, Any] = {}
    for sym in FOCUS:
        code = sym.replace(".T", "").upper()
        in_static = code in static_codes
        rec: dict[str, Any] = {
            "symbol": sym,
            "in_static27": in_static,
            "hardcoded_add": False,
            "hardcoded_exclude": False,
        }
        if in_static:
            rec["not_in_dynamic23_reason"] = "present_in_static27"
            out[sym] = rec
            continue

        found = False
        for market in TRADABLE_MARKETS:
            pool = by_market[market]
            for i, e in enumerate(pool):
                if e.parsed.code.upper() != code:
                    continue
                found = True
                n = len(pool)
                start = seed_int % n if n else 0
                picked = stride_sample_positions(n, quota if market == "growth" else cfg.dynamic_prime_quota, start)
                if market == "standard":
                    picked = stride_sample_positions(n, cfg.dynamic_standard_quota, start)
                elif market == "growth":
                    picked = stride_sample_positions(n, cfg.dynamic_growth_quota, start)

                rec.update(
                    {
                        "market": market,
                        "pool_size_excluding_static": n,
                        "master_order_index": i,
                        "market_position_pct": round(i / max(n - 1, 1), 6),
                        "rotation_seed": seed,
                        "seed_int_mod_pool": start,
                        "market_quota": cfg.dynamic_growth_quota
                        if market == "growth"
                        else (
                            cfg.dynamic_prime_quota
                            if market == "prime"
                            else cfg.dynamic_standard_quota
                        ),
                        "stride_picked_positions": picked,
                        "in_stride_sample": i in picked,
                        "not_in_dynamic23_reason": (
                            "market_stratified_stride_quota_not_hit"
                            if i not in picked
                            else "would_be_selected_if_quota_increased"
                        ),
                        "explanation": (
                            f"Growth pool has {n} names but only "
                            f"{cfg.dynamic_growth_quota} stride slots; "
                            f"index {i} not among picked positions {picked} "
                            f"for seed {seed} (start={start})."
                            if market == "growth" and i not in picked
                            else ""
                        ),
                    }
                )
                break
        if not found:
            rec["not_in_dynamic23_reason"] = "not_in_tradable_pool_or_alphanumeric_parse_skip"
        out[sym] = rec
    return out


def build_design_options() -> list[dict[str, Any]]:
    return [
        {
            "option_id": "A",
            "name": "previous_day_vol_liq_top50",
            "requires": "prev_day_OHLCV,trading_value,ATR_pct_or_range",
            "score_formula": "volatility_liquidity_score = ATR% * log10(TradingValue); rank top 50",
            "market_quota": "none_or_soft_cap_optional",
            "board_calls": "0 at selection (board-free)",
            "data_ready": "no_full_market_daily",
            "recommended": "yes_if_daily_built",
        },
        {
            "option_id": "B",
            "name": "previous_day_volume_surge_top50",
            "requires": "prev_volume,avg_volume_20d,min_trading_value",
            "score_formula": "volume_ratio * log10(TV); filters; top 50",
            "market_quota": "optional",
            "board_calls": "0",
            "data_ready": "no_volume_history",
            "recommended": "secondary",
        },
        {
            "option_id": "C",
            "name": "hybrid_vol_liq_gap_top50",
            "requires": "TV,ATR%,range%,volume_surge,spread_floor",
            "score_formula": "weighted z-score blend; top 50",
            "market_quota": "no_fixed_prime_standard_growth",
            "board_calls": "0 or validate 50 only",
            "data_ready": "no_full_market_daily",
            "recommended": "primary_if_daily_built",
        },
        {
            "option_id": "D",
            "name": "intraday_opening_candidate_top50",
            "requires": "opening board/quote",
            "score_formula": "opening gap + liquidity",
            "market_quota": "n/a",
            "board_calls": "up to 50 register",
            "data_ready": "register_limit_risk",
            "recommended": "no_not_primary",
        },
        {
            "option_id": "current",
            "name": "phase105_market_stratified_stride",
            "requires": "tradable_symbols.csv only",
            "score_formula": "none (deterministic index stride)",
            "market_quota": "prime8_standard8_growth7",
            "board_calls": "none|validate50|score23",
            "data_ready": "yes",
            "recommended": "interim_only",
        },
        {
            "option_id": "dynamic50_no_static",
            "name": "all_push_slots_dynamic",
            "requires": "same as A or C",
            "score_formula": "top50 by daytrade score; static27 removed",
            "market_quota": "forbidden_fixed_symbol_bias",
            "board_calls": "<=50",
            "data_ready": "blocked_on_daily_OHLCV",
            "recommended": "target_architecture",
        },
    ]


def determine_verdict(
    inventory: list[dict[str, Any]],
    design_options: list[dict[str, Any]],
) -> tuple[str, list[str], bool]:
    notes: list[str] = []
    has_daily = any(
        r["source_id"] == "kabu_native_daily_store" and r["exists"] and r["usable_for_dynamic50"] == "partial"
        for r in inventory
    )
    full_market_daily = any(
        r["source_id"] in ("kabu_native_daily_store", "yfinance_stooq_bulk_daily")
        and r["usable_for_dynamic50"] in ("yes", "partial")
        and "full" in r.get("coverage", "").lower()
        for r in inventory
    )

    notes.append("Current Phase105 dynamic23 uses market_stratified_stride without price/volume features")
    current_sampling_only = True

    a_ready = design_options[0]["data_ready"] == "yes_if_daily_built"
    c_ready = design_options[2]["data_ready"] == "no_full_market_daily"

    if full_market_daily:
        return "dynamic50_design_ready", notes + ["Full-market previous-day bars available"], current_sampling_only

    if not has_daily:
        notes.append("kabu_native/data/daily empty; intraday_1m covers ~27 symbols only")
        return "need_daily_ohlcv_source", notes, current_sampling_only

    return "need_daily_ohlcv_source", notes, current_sampling_only


def write_md(path: Path, report: dict[str, Any]) -> None:
    cur = report["current_dynamic_selection"]
    inv = report["data_source_inventory"]
    opts = report["design_options"]
    lines = [
        "# Phase 107 — Dynamic Universe Selection Design",
        "",
        f"**Date:** {report['day_stamp']}",
        f"**Verdict:** `{report['verdict']}`",
        "",
        "## Primary conclusion (one point)",
        "",
        report.get("primary_conclusion", ""),
        "",
        f"- **Daytrade-oriented selection?** `{report.get('is_daytrade_selection')}`",
        f"- **Market-stratified sampling?** `{report.get('is_market_stratified_sampling')}`",
        f"- **Full-market daily OHLCV in repo?** `{report.get('has_full_market_daily_ohlcv_in_repo')}`",
        f"- **Target dynamic50:** `{report.get('target_dynamic50')}`",
        "",
        report.get("verdict_detail", ""),
        "",
        "## 1. Current dynamic extraction (Phase105)",
        "",
        "### Input data",
        "",
    ]
    for k, v in cur["input_data"].items():
        lines.append(f"- **{k}:** {v}")
    lines.extend(["", "### Features used", ""])
    for f in cur["features_used"]:
        lines.append(f"- {f}")
    lines.extend(["", "### Features NOT used (daytrade)", ""])
    for f in cur["features_not_used"]:
        lines.append(f"- {f}")
    lines.extend(
        [
            "",
            "### Sampling",
            "",
            f"- **Method:** {cur['sampling']['method']}",
            f"- **Formula:** `{cur['sampling']['formula']}`",
            f"- **Seed:** {cur['sampling']['rotation_seed']}",
            f"- **Market quotas:** {cur['sampling']['market_quotas']}",
            "",
            "### dynamic_score",
            "",
            cur["dynamic_score_note"],
            "",
            "### board_mode differences",
            "",
        ]
    )
    for mode, diff in cur["board_mode_diff"].items():
        lines.append(f"- **{mode}:** {diff}")
    lines.extend(["", "### Focus symbols (3905.T / 6613.T)", ""])
    for sym, d in cur.get("focus_symbols", {}).items():
        lines.append(f"#### {sym}")
        lines.append(f"- Reason: {d.get('not_in_dynamic23_reason')}")
        if d.get("explanation"):
            lines.append(f"- {d['explanation']}")
    lines.extend(["", "## 2. dynamic50 (no static27)", ""])
    for p in report["dynamic50_design"]["principles"]:
        lines.append(f"- {p}")
    lines.extend(["", "## 3. Design options", ""])
    lines.append("| ID | Name | Data ready | Recommended |")
    lines.append("|----|------|------------|-------------|")
    for o in opts:
        lines.append(
            f"| {o['option_id']} | {o['name']} | {o['data_ready']} | {o['recommended']} |"
        )
    lines.extend(["", "## 4. Data source inventory", ""])
    lines.append("| source | exists | coverage | usable_for_dynamic50 |")
    lines.append("|--------|--------|----------|----------------------|")
    for r in inv:
        lines.append(
            f"| {r['source_id']} | {r['exists']} | {r['coverage']} | {r['usable_for_dynamic50']} |"
        )
    lines.extend(["", "## 5. Next step", ""])
    for s in report.get("next_steps", []):
        lines.append(f"- {s}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 107 dynamic selection design report")
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    _bootstrap()
    from universe.dynamic_build import load_dynamic_config

    cfg = load_dynamic_config(CONFIG)
    inventory = inventory_data_sources()
    design_options = build_design_options()
    focus = focus_stride_analysis(day_stamp)
    verdict, notes, sampling_only = determine_verdict(inventory, design_options)

    current = {
        "phase": "105",
        "mode": "hybrid_static_plus_dynamic",
        "input_data": {
            "symbol_master": str(cfg.symbol_master_path),
            "static_universe": str(cfg.static_universe_path),
            "static_max": cfg.static_max,
            "dynamic_max": cfg.dynamic_max,
            "push_limit": cfg.push_limit,
            "excludes_static_from_dynamic_pool": True,
        },
        "features_used": [
            "market (prime/standard/growth from JPX master)",
            "master_csv_row_order (implicit ordering)",
            "is_active filter",
            "4-digit parse_symbol only (alphanumeric rows skipped)",
            "rotation_seed = sample_seed or YYYYMMDD",
            "stride_sample_positions per market pool",
        ],
        "features_not_used": [
            "previous_day_trading_value",
            "previous_day_volume / volume_surge",
            "ATR_pct / intraday_range_pct",
            "change_previous_close_pct (board-free path)",
            "volatility_liquidity_score",
            "spread_bps floor (board-free path)",
            "sector/scale score bias (explicitly disabled)",
            "per-symbol hardcode",
            "time_of_day filter",
        ],
        "sampling": {
            "method": cfg.candidate_sampling_mode,
            "formula": (
                "per market m: pool = tradable \\ static; start = seed_int % |pool|; "
                "positions = stride_sample_positions(|pool|, quota_m, start); "
                "pick pool[pos] for pos in positions"
            ),
            "rotation_seed": cfg.sample_seed or f"{day_stamp} (default)",
            "market_quotas": {
                "prime": cfg.dynamic_prime_quota,
                "standard": cfg.dynamic_standard_quota,
                "growth": cfg.dynamic_growth_quota,
            },
            "legacy_bulk_mode": "hybrid_stride_plus_rotation over 400 candidates (disabled in Phase105 default)",
        },
        "dynamic_score_note": (
            "board_mode=none: dynamic_score column empty; no ranking. "
            "board_mode=score: dynamic_score = log10(TV)*w1 + |chg%|*w2 + log10(liq)*w3 - spread*w4 "
            "for up to 23 symbols only (requires kabu board)."
        ),
        "board_mode_diff": {
            "none": "0 board API calls; dynamic23 from stride only",
            "validate": "up to min(50, push_limit) board GET for final universe; warnings only, no replacement",
            "score": "board on dynamic23 only (<=50 register); may reorder by dynamic_score",
        },
        "focus_symbols": focus,
        "is_sampling_only_not_daytrade_rank": True,
    }

    dynamic50 = {
        "principles": [
            "push_limit=50 all dynamic (static27 deprecated for shadow trial)",
            "no per-symbol hardcode add/exclude",
            "no market-segment fixed quota favoritism (optional soft diversity cap only)",
            "no time-of-day filter in universe build",
            "selection board-free; optional validate <=50 board calls",
            "same register_limit_aware constraints as Phase105",
        ],
        "recommended_option": "C (hybrid_vol_liq_gap_top50) when daily OHLCV exists; else A",
        "blocked_until": "full-market previous-day OHLCV + trading_value persisted",
    }

    next_steps = [
        "Add daily OHLCV ingest (JPX tradable universe, T-1) under kabu_native/data/daily or data/daily_bars",
        "Implement shadow-only dynamic50 builder: rank by vol_liq, output universe_dynamic50_trial_YYYYMMDD.csv",
        "Keep Phase105 stride builder as fallback (--selection-mode stride|vol_liq)",
        "Phase108+: implement_dynamic50_trial after daily source validated",
    ]
    if verdict == "need_daily_ohlcv_source":
        next_steps.insert(0, "Verdict B: build previous-day feature table before changing shadow live universe")

    verdict_flags = {
        "dynamic50_design_ready": verdict == "dynamic50_design_ready",
        "need_daily_ohlcv_source": verdict == "need_daily_ohlcv_source",
        "current_dynamic_is_sampling_only": sampling_only,
        "implement_dynamic50_trial": False,
    }
    if verdict == "dynamic50_design_ready":
        verdict_flags["implement_dynamic50_trial"] = True

    primary_conclusion = (
        "Current dynamic universe is market-stratified sampling only, NOT daytrade-oriented "
        "selection. Full-market previous-day OHLCV/trading_value is not available in-repo; "
        "next step is daily data foundation, then dynamic50 = volatility_liquidity_score top 50."
    )

    report: dict[str, Any] = {
        "phase": 107,
        "day_stamp": day_stamp,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "primary_conclusion": primary_conclusion,
        "is_daytrade_selection": False,
        "is_market_stratified_sampling": True,
        "has_full_market_daily_ohlcv_in_repo": False,
        "target_dynamic50": "volatility_liquidity_score_top50",
        "ideal_pipeline": [
            "all_tradable_ordinary_shares",
            "previous_day_trading_value_filter",
            "ATR_pct_and_intraday_range",
            "volume_surge",
            "volatility_liquidity_score",
            "top50_for_PUSH_register",
            "existing_quality_vol_liq_cap3_entry_gate",
        ],
        "verdict": verdict,
        "verdict_detail": "; ".join(notes),
        "verdict_flags": verdict_flags,
        "current_dynamic_selection": current,
        "dynamic50_design": dynamic50,
        "design_options": design_options,
        "data_source_inventory": inventory,
        "next_steps": next_steps,
        "constraints_confirmed": [
            "no_production_pilot_yaml_change",
            "no_overwrite_universe_intraday_full",
            "no_entry_exit_quality_vol_liq_change",
            "no_symbol_hardcode",
            "no_time_of_day_filter",
            "shadow_dry_run_only",
            "no_pf_evaluation_phase107",
        ],
    }

    json_path = REPORTS / f"phase107_dynamic_selection_conditions_{day_stamp}.json"
    md_path = REPORTS / f"phase107_dynamic_selection_conditions_{day_stamp}.md"
    inv_path = REPORTS / f"phase107_data_source_inventory_{day_stamp}.csv"
    opt_path = REPORTS / f"phase107_dynamic50_design_options_{day_stamp}.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(md_path, report)

    with inv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(inventory[0].keys()))
        w.writeheader()
        w.writerows(inventory)

    with opt_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(design_options[0].keys()))
        w.writeheader()
        w.writerows(design_options)

    print(
        json.dumps(
            {
                "verdict": verdict,
                "verdict_flags": verdict_flags,
                "json": _rel(json_path),
                "md": _rel(md_path),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
