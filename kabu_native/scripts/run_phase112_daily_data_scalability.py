#!/usr/bin/env python3
"""Phase 112: Full-market (3575) previous-day data scalability probe (review only)."""

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


def bench_to_dict(b: Any) -> dict[str, Any]:
    return {
        "benchmark_size": b.benchmark_size,
        "requested": b.requested,
        "elapsed_sec": b.elapsed_sec,
        "success_count": b.success_count,
        "missing_count": b.missing_count,
        "chunk_failures": b.chunk_failures,
        "success_rate": b.success_rate,
        "missing_rate": b.missing_rate,
        "api_failure_rate": b.api_failure_rate,
        "vol_liq_generated": b.vol_liq_generated,
        "feature_coverage_rate": b.feature_coverage_rate,
        "symbols_per_sec": b.symbols_per_sec,
    }


def main() -> int:
    _bootstrap()
    from universe.daily_data_scalability import (
        BENCHMARK_SIZES,
        benchmark_yfinance_fetch,
        determine_verdict,
        focus_diagnostics,
        inventory_data_sources,
        top50_by_vol_liq,
    )
    from universe.dynamic_build import load_dynamic_config, resolve_symbol_master

    parser = argparse.ArgumentParser(description="Phase 112 daily data scalability")
    parser.add_argument("--trade-date", default="2026-05-21")
    parser.add_argument(
        "--sizes",
        default=",".join(str(s) for s in BENCHMARK_SIZES),
        help="Comma-separated benchmark sizes",
    )
    args = parser.parse_args()
    trade_d = date.fromisoformat(args.trade_date)
    sizes = [int(x.strip()) for x in args.sizes.split(",") if x.strip()]

    cfg = load_dynamic_config(NATIVE / "configs" / "universe_dynamic_trial.yaml")
    _, entries = resolve_symbol_master(ROOT, cfg.symbol_master_paths)
    all_symbols = [f"{e.parsed.code}.T" for e in entries]

    try:
        import yfinance  # noqa: F401

        yfinance_available = True
    except ImportError:
        yfinance_available = False

    benchmarks: list[dict[str, Any]] = []
    full_prev: dict[str, Any] = {}

    for size in sizes:
        n = min(size, len(all_symbols))
        syms = all_symbols[:n]
        print(f"benchmark n={n} ...", flush=True)
        prev, bench = benchmark_yfinance_fetch(syms, trade_d)
        benchmarks.append(bench_to_dict(bench))
        if n >= len(all_symbols):
            full_prev = prev

    full_bench_row = benchmarks[-1] if benchmarks else {}
    from universe.daily_data_scalability import FetchBenchmark

    full_bench = FetchBenchmark(**{k: full_bench_row[k] for k in FetchBenchmark.__dataclass_fields__})
    bench_objs = [FetchBenchmark(**{k: row[k] for k in FetchBenchmark.__dataclass_fields__}) for row in benchmarks]
    verdict, verdict_notes = determine_verdict(
        full_bench, yfinance_available=yfinance_available, benchmarks=bench_objs
    )

    top50 = top50_by_vol_liq(full_prev) if full_prev else []
    focus = focus_diagnostics(full_prev, top50, all_symbols=all_symbols) if full_prev else {}

    morning_estimate_min = round(full_bench.elapsed_sec / 60.0, 2)

    report: dict[str, Any] = {
        "phase": 112,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "verdict_options": {
            "A": "full_market_daily_data_ready — majority of 3575 fetched; morning run feasible",
            "B": "partial_market_data_ready — ~1000-2000 symbols practical",
            "C": "need_alternative_data_source — yfinance stability insufficient",
            "D": "daily_data_too_slow — too heavy for daily morning ops",
        },
        "primary_question": "Can yfinance fetch all 3575 tradable symbols each morning?",
        "trade_date": trade_d.isoformat(),
        "previous_day_label": "T-1 (yfinance daily bar before trade_date)",
        "tradable_symbol_count": len(all_symbols),
        "benchmark_order": "ascending size (100→3575); later runs may benefit from network cache",
        "data_source_inventory": inventory_data_sources(NATIVE, ROOT),
        "benchmarks": benchmarks,
        "operational_estimate": {
            "full_market_elapsed_sec": full_bench.elapsed_sec,
            "full_market_elapsed_min": morning_estimate_min,
            "slow_threshold_min": 30,
            "symbols_per_sec": full_bench.symbols_per_sec,
            "phase111_cap_comparison": {
                "old_cap": 600,
                "old_coverage_pct": round(600 / len(all_symbols), 4),
                "full_coverage_pct": full_bench.success_rate,
            },
        },
        "feature_generation": {
            "fields": ["trading_value", "intraday_range_pct", "volume_surge_5", "volatility_liquidity_score"],
            "formula": "volatility_liquidity_score = atr_pct * log10(trading_value)",
            "full_market_vol_liq_count": full_bench.vol_liq_generated,
            "feature_coverage_rate": full_bench.feature_coverage_rate,
        },
        "dynamic50_preview": {
            "method": "top50 by volatility_liquidity_score (full-market fetch)",
            "focus_diagnostics": focus,
        },
        "phase111_link": {
            "3905.T_master_index": focus.get("3905.T", {}).get("master_index"),
            "3905_in_top50_if_full_data": focus.get("3905.T", {}).get("in_top50_preview"),
            "6613_in_top50_if_full_data": focus.get("6613.T", {}).get("in_top50_preview"),
        },
        "design_options_if_not_ready": [
            "persist daily OHLCV under kabu_native/data/daily/ to avoid morning yfinance",
            "J-Quants / JPX official EOD bulk",
            "overnight batch job before 08:00",
        ],
        "constraints": ["review_only", "no_pilot_yaml_change", "no_pf_evaluation"],
    }

    out_json = REPORTS / "phase112_daily_data_scalability.json"
    out_feat = REPORTS / "phase112_feature_generation.csv"
    out_prev = REPORTS / "phase112_dynamic50_full_market_preview.csv"

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with out_feat.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(benchmarks[0].keys()) if benchmarks else [])
        if benchmarks:
            w.writeheader()
            w.writerows(benchmarks)

    if top50:
        with out_prev.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(top50[0].keys()))
            w.writeheader()
            w.writerows(top50)

    print(
        json.dumps(
            {
                "verdict": verdict,
                "full_elapsed_sec": full_bench.elapsed_sec,
                "success_rate": full_bench.success_rate,
                "json": _rel(out_json),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
