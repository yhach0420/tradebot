"""
Phase 112: Full-market previous-day OHLCV / vol_liq scalability (review only).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional, Sequence

from universe.opening_screen import PreviousDayFeatures, fetch_previous_day_yfinance

FOCUS_SYMBOLS = ("3905.T", "6613.T")
BENCHMARK_SIZES = (100, 500, 1000, 3575)
# Verdict criteria (Phase112 user spec):
# A full_market_daily_data_ready: majority of 3575 + feasible each morning
# B partial_market_data_ready: ~1000-2000 symbols practical
# D daily_data_too_slow: too heavy for daily morning ops
# C need_alternative_data_source: yfinance not stable enough
SLOW_THRESHOLD_SEC = 1800.0  # 30 min — above this → D
READY_SUCCESS_RATE = 0.90  # majority of full market
PARTIAL_SUCCESS_RATE = 0.55  # ~2000/3575
PARTIAL_SIZE_FLOOR = 1000
FEATURE_COVERAGE_READY = 0.90


@dataclass
class FetchBenchmark:
    benchmark_size: int
    requested: int
    elapsed_sec: float
    success_count: int
    missing_count: int
    chunk_failures: int
    success_rate: float
    missing_rate: float
    api_failure_rate: float
    vol_liq_generated: int
    feature_coverage_rate: float
    symbols_per_sec: float


@dataclass
class FeatureRow:
    symbol: str
    close: Optional[float]
    volume: Optional[float]
    trading_value: Optional[float]
    intraday_range_pct: Optional[float]
    volatility_liquidity_score: Optional[float]
    volume_surge_5: Optional[float]
    data_source: str


def inventory_data_sources(native_root: Path, root: Path) -> list[dict[str, Any]]:
    daily_dir = native_root / "data" / "daily"
    daily_files = list(daily_dir.glob("**/*.csv")) if daily_dir.is_dir() else []
    intraday = root / "data" / "intraday_1m"
    n_intraday = len(list(intraday.glob("**/*.csv"))) if intraday.is_dir() else 0
    push = native_root / "data" / "push_jsonl"
    n_push = len(list(push.glob("**/*.jsonl"))) if push.is_dir() else 0
    return [
        {
            "source_id": "yfinance",
            "path": "(network)",
            "available": True,
            "coverage": "batch_download",
            "notes": "Primary probe for Phase112; T-1 OHLCV via yf.download chunks of 80",
        },
        {
            "source_id": "kabu_native_daily_store",
            "path": str(daily_dir),
            "available": bool(daily_files),
            "coverage": f"{len(daily_files)} files",
            "notes": "Empty in repo" if not daily_files else "partial",
        },
        {
            "source_id": "intraday_1m_archive",
            "path": str(intraday),
            "available": n_intraday > 0,
            "coverage": f"{n_intraday} csv files",
            "notes": "~27-symbol subset; not full market EOD",
        },
        {
            "source_id": "kabu_push_jsonl",
            "path": str(push),
            "available": n_push > 0,
            "coverage": f"{n_push} jsonl",
            "notes": "Same-day ticks; not previous-day universe rank",
        },
    ]


def benchmark_yfinance_fetch(
    symbols: Sequence[str],
    trade_date: date,
) -> tuple[dict[str, PreviousDayFeatures], FetchBenchmark]:
    syms = list(symbols)
    n = len(syms)
    n_chunks = max((n + 79) // 80, 1)
    t0 = time.perf_counter()
    try:
        import yfinance  # noqa: F401
        yfinance_ok = True
    except ImportError:
        yfinance_ok = False
        prev: dict[str, PreviousDayFeatures] = {}
    else:
        prev = fetch_previous_day_yfinance(syms, trade_date)
    elapsed = time.perf_counter() - t0

    success = len(prev)
    missing = n - success
    vol_liq_n = sum(1 for p in prev.values() if p.volatility_liquidity_score is not None)
    chunk_failures = 0 if yfinance_ok else n_chunks
    if yfinance_ok and missing > 0:
        chunk_failures = max(0, int(missing / max(len(syms) / n_chunks, 1) * 0.1))

    return prev, FetchBenchmark(
        benchmark_size=n,
        requested=n,
        elapsed_sec=round(elapsed, 3),
        success_count=success,
        missing_count=missing,
        chunk_failures=chunk_failures,
        success_rate=round(success / max(n, 1), 4),
        missing_rate=round(missing / max(n, 1), 4),
        api_failure_rate=round(chunk_failures / n_chunks, 4),
        vol_liq_generated=vol_liq_n,
        feature_coverage_rate=round(vol_liq_n / max(success, 1), 4),
        symbols_per_sec=round(success / max(elapsed, 0.001), 2),
    )


def top50_by_vol_liq(prev_by_sym: dict[str, PreviousDayFeatures]) -> list[dict[str, Any]]:
    ranked = sorted(
        prev_by_sym.items(),
        key=lambda x: x[1].volatility_liquidity_score or 0.0,
        reverse=True,
    )[:50]
    rows: list[dict[str, Any]] = []
    for i, (sym, p) in enumerate(ranked, start=1):
        rows.append(
            {
                "rank": i,
                "symbol": sym,
                "volatility_liquidity_score": p.volatility_liquidity_score,
                "trading_value_prev": p.trading_value,
                "intraday_range_pct": p.intraday_range_pct,
                "volume_surge_5": p.volume_surge_5,
                "atr_pct": p.atr_pct,
                "data_source": p.data_source,
                "is_focus_diagnostic": sym in FOCUS_SYMBOLS,
            }
        )
    return rows


def focus_diagnostics(
    prev_by_sym: dict[str, PreviousDayFeatures],
    top50: list[dict[str, Any]],
    *,
    all_symbols: Sequence[str],
) -> dict[str, Any]:
    top50_set = {r["symbol"] for r in top50}
    idx = {s: i for i, s in enumerate(all_symbols)}
    ranked_all = sorted(
        prev_by_sym.items(),
        key=lambda x: x[1].volatility_liquidity_score or 0.0,
        reverse=True,
    )
    rank_map = {s: i + 1 for i, (s, _) in enumerate(ranked_all)}
    out: dict[str, Any] = {}
    for sym in FOCUS_SYMBOLS:
        p = prev_by_sym.get(sym)
        entry: dict[str, Any] = {
            "master_index": idx.get(sym),
            "fetched": p is not None,
            "volatility_liquidity_score": p.volatility_liquidity_score if p else None,
            "rank_by_vol_liq": rank_map.get(sym),
            "in_top50_preview": sym in top50_set,
            "gap_to_rank50_score": None,
        }
        if p and len(ranked_all) >= 50:
            score50 = ranked_all[49][1].volatility_liquidity_score or 0.0
            entry["gap_to_rank50_score"] = round((p.volatility_liquidity_score or 0) - score50, 6)
        out[sym] = entry
    return out


def determine_verdict(
    full_bench: FetchBenchmark,
    *,
    yfinance_available: bool,
    benchmarks: Sequence[FetchBenchmark] = (),
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not yfinance_available:
        return "need_alternative_data_source", ["yfinance not importable"]

    if full_bench.elapsed_sec > SLOW_THRESHOLD_SEC:
        notes.append(
            f"full market fetch {full_bench.elapsed_sec}s > {SLOW_THRESHOLD_SEC}s — too heavy for morning"
        )
        return "daily_data_too_slow", notes

    if (
        full_bench.success_rate >= READY_SUCCESS_RATE
        and full_bench.feature_coverage_rate >= FEATURE_COVERAGE_READY
        and full_bench.requested >= 3000
    ):
        notes.append(
            f"3575 probe: success={full_bench.success_rate:.1%} "
            f"vol_liq={full_bench.feature_coverage_rate:.1%} "
            f"elapsed={full_bench.elapsed_sec}s (~{full_bench.elapsed_sec / 60:.1f} min) — morning feasible"
        )
        return "full_market_daily_data_ready", notes

    # ~1000-2000 practical: check 1000-benchmark row if present
    b1000 = next((b for b in benchmarks if b.benchmark_size == 1000), None)
    if b1000 and b1000.success_count >= PARTIAL_SIZE_FLOOR and b1000.elapsed_sec < SLOW_THRESHOLD_SEC:
        notes.append(f"1000-symbol bench: {b1000.success_count} ok in {b1000.elapsed_sec}s")
        return "partial_market_data_ready", notes

    if full_bench.success_rate >= PARTIAL_SUCCESS_RATE:
        notes.append(f"partial success_rate={full_bench.success_rate:.1%}")
        return "partial_market_data_ready", notes

    if full_bench.api_failure_rate > 0.15 or full_bench.success_rate < PARTIAL_SUCCESS_RATE:
        notes.append(
            f"unstable: success_rate={full_bench.success_rate:.1%} api_fail={full_bench.api_failure_rate:.1%}"
        )
        return "need_alternative_data_source", notes

    return "need_alternative_data_source", notes
