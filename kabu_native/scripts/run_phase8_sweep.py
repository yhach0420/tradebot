#!/usr/bin/env python3
"""
Phase 8: common-parameter sweep across full intraday universe.

例::
    python kabu_native/scripts/run_phase8_sweep.py \\
        --start-date 2026-04-10 --end-date 2026-05-15 \\
        --universe kabu_native/data/universe/universe_intraday_full.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any


def _paths() -> tuple[Path, Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    src_root = native_root / "src"
    return repo_root, native_root, src_root


def _bootstrap() -> tuple[Path, Path]:
    repo_root, native_root, src_root = _paths()
    for p in (src_root, repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo_root, native_root


def _run_one_sweep(task: dict[str, Any]) -> dict[str, Any]:
    repo_root = Path(task["repo_root"])
    native_src = Path(task["native_src"])
    for p in (native_src, repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    from replay.sweep_runner import SweepParams, replay_cached, summarize_sweep

    params = SweepParams(
        sweep_id=task["sweep_id"],
        sweep_group=task["sweep_group"],
        fail_window_min=float(task["fail_window_min"]),
        fail_buffer_pct=float(task["fail_buffer_pct"]),
        bf_confirm_count=int(task["bf_confirm_count"]),
        market_session_control=bool(task["market_session_control"]),
        hard_stop_pct=float(task["hard_stop_pct"]),
    )
    cache = task["cache"]
    trades = replay_cached(
        cache,
        params,
        repo_root=repo_root,
        tier=str(task["tier"]),
        entry_score_min=int(task["entry_score_min"]),
        require_timing_ok=bool(task["require_timing_ok"]),
        relaxed_signal=bool(task["relaxed_signal"]),
    )
    return summarize_sweep(trades, params)


def _symbols_from_universe(path: Path) -> list[str]:
    symbols: list[str] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            p = str(row.get("passed", "true")).strip().lower()
            if p not in ("true", "1", "yes", ""):
                continue
            sym = str(row.get("symbol", "")).strip()
            if not sym:
                continue
            symbols.append(sym if sym.endswith(".T") else f"{sym}.T")
    return symbols


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    repo_root, native_root = _bootstrap()

    from replay.runner import load_replay_config
    from replay.sweep_runner import (
        apply_trade_floor,
        build_event_cache,
        iter_phase8_sweeps,
        pick_candidates,
        replay_cached,
        summarize_sweep,
    )

    parser = argparse.ArgumentParser(description="Phase 8 common-parameter sweep")
    parser.add_argument("--start-date", default="2026-04-10")
    parser.add_argument("--end-date", default="2026-05-15")
    parser.add_argument(
        "--universe",
        type=Path,
        default=native_root / "data" / "universe" / "universe_intraday_full.csv",
    )
    parser.add_argument("--report-date", default=None, help="YYYYMMDD for output filenames")
    parser.add_argument("--workers", type=int, default=4, help="parallel sweep workers")
    args = parser.parse_args()

    cfg_raw = load_replay_config(
        native_root / "configs" / "replay.yaml",
        native_root=native_root,
        repo_root=repo_root,
    )
    symbols = _symbols_from_universe(args.universe.resolve())
    if not symbols:
        logging.error("no symbols from universe %s", args.universe)
        return 1

    data_roots = cfg_raw["data_roots"]
    report_date = args.report_date or datetime.now().strftime("%Y%m%d")
    reports_dir = native_root / "results" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    csv_path = reports_dir / f"phase8_sweep_{report_date}.csv"
    json_path = reports_dir / f"phase8_sweep_{report_date}.json"

    logging.info("building event cache for %d symbols, %s .. %s", len(symbols), args.start_date, args.end_date)
    cache = build_event_cache(
        repo_root=repo_root,
        symbols=symbols,
        start_date=args.start_date,
        end_date=args.end_date,
        data_roots=data_roots,
        synthetic_push_keep=float(cfg_raw.get("synthetic_push_keep", 1.0)),
        synthetic_spread_bps=float(cfg_raw.get("synthetic_spread_bps", 8.0)),
        synthetic_events_per_minute=int(cfg_raw.get("synthetic_events_per_minute", 10)),
    )
    logging.info("cached %d symbol-days", len(cache))

    sweeps = iter_phase8_sweeps()
    native_src = native_root / "src"
    tasks = [
        {
            "repo_root": str(repo_root),
            "native_src": str(native_src),
            "cache": cache,
            "tier": str(cfg_raw.get("tier", "B")),
            "entry_score_min": int(cfg_raw.get("entry_score_min", 60)),
            "require_timing_ok": bool(cfg_raw.get("require_timing_ok", True)),
            "relaxed_signal": bool(cfg_raw.get("relaxed_signal", False)),
            **p.to_dict(),
        }
        for p in sweeps
    ]

    rows: list[dict] = []
    workers = max(1, int(args.workers))
    if workers == 1:
        from replay.sweep_runner import SweepParams, replay_cached, summarize_sweep

        for i, params in enumerate(sweeps, 1):
            logging.info("[%d/%d] %s (%s)", i, len(sweeps), params.sweep_id, params.sweep_group)
            trades = replay_cached(
                cache,
                params,
                repo_root=repo_root,
                tier=str(cfg_raw.get("tier", "B")),
                entry_score_min=int(cfg_raw.get("entry_score_min", 60)),
                require_timing_ok=bool(cfg_raw.get("require_timing_ok", True)),
                relaxed_signal=bool(cfg_raw.get("relaxed_signal", False)),
            )
            row = summarize_sweep(trades, params)
            rows.append(row)
            logging.info(
                "  trades=%s total_pnl=%.2f pf=%s bf=%s opening=%s",
                row.get("trades"),
                float(row.get("total_pnl_pct") or 0),
                row.get("profit_factor"),
                row.get("breakout_failure_exit_count"),
                row.get("opening_trade_count"),
            )
    else:
        logging.info("running %d sweeps with %d workers", len(tasks), workers)
        by_id: dict[str, dict] = {}
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_one_sweep, t): t["sweep_id"] for t in tasks}
            done = 0
            for fut in as_completed(futures):
                sid = futures[fut]
                row = fut.result()
                by_id[sid] = row
                done += 1
                logging.info(
                    "[%d/%d] %s trades=%s total_pnl=%.2f bf=%s opening=%s",
                    done,
                    len(tasks),
                    sid,
                    row.get("trades"),
                    float(row.get("total_pnl_pct") or 0),
                    row.get("breakout_failure_exit_count"),
                    row.get("opening_trade_count"),
                )
        rows = [by_id[p.sweep_id] for p in sweeps if p.sweep_id in by_id]

    baseline_trades = next(
        (int(r["trades"]) for r in rows if r.get("sweep_id") == "baseline"),
        int(rows[0].get("trades") or 0) if rows else 0,
    )
    rows = apply_trade_floor(rows, baseline_trades=baseline_trades)
    candidates = pick_candidates(rows, max_n=3)

    fieldnames = [
        "sweep_id",
        "sweep_group",
        "fail_window_min",
        "fail_buffer_pct",
        "bf_confirm_count",
        "market_session_control",
        "hard_stop_pct",
        "trades",
        "symbols_with_trades",
        "win_rate",
        "total_pnl_pct",
        "avg_pnl_pct",
        "median_pnl_pct",
        "max_loss_pct",
        "profit_factor",
        "breakout_failure_exit_count",
        "hard_stop_count",
        "opening_trade_count",
        "pnl_concentration_top_symbol",
        "pnl_concentration_top_share",
        "excluded_low_trades",
        "trade_floor",
    ]

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)

    payload = {
        "meta": {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "universe": str(args.universe.resolve()),
            "symbol_count": len(symbols),
            "cached_symbol_days": len(cache),
            "baseline_trades": baseline_trades,
            "sweep_count": len(rows),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "rows": rows,
        "adoption_candidates": candidates,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    logging.info("wrote %s", csv_path)
    logging.info("wrote %s", json_path)
    logging.info("candidates: %s", [c.get("sweep_id") for c in candidates])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
