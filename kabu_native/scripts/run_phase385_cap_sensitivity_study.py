#!/usr/bin/env python3
"""Phase385: Concurrent position cap sensitivity study."""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REPORTS = REPO / "kabu_native" / "results" / "reports"


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _worker(job: dict) -> dict:
    _bootstrap()
    from research.phase382_capital_constrained_backtest import load_session_capital_backtest_trades

    t0 = time.monotonic()
    try:
        result = load_session_capital_backtest_trades(
            job["session_meta"],
            reports_dir=Path(job["reports_dir"]),
            min_day=job["min_day"],
            max_day=job.get("max_day"),
        )
        if int(result.get("valid_count") or 0) <= 0 and int(result.get("excluded_count") or 0) <= 0:
            return {
                "ok": False,
                "session_id": job["session_meta"].get("session_id"),
                "error": result.get("error") or "no_trades",
                "runtime_sec": round(time.monotonic() - t0, 2),
            }
        return {"ok": True, "result": result, "runtime_sec": round(time.monotonic() - t0, 2)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "runtime_sec": round(time.monotonic() - t0, 2)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase385 concurrent position cap sensitivity study")
    parser.add_argument("--min-day", default="20260529")
    parser.add_argument("--max-day", default="20260612")
    parser.add_argument("--initial-equity", type=float, default=2_000_000.0)
    parser.add_argument("--equity-floor", type=float, default=None)
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_false", dest="parallel")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    args = parser.parse_args()

    _bootstrap()
    from research.phase376_production_daily_pnl_review import discover_session_roots, discover_sessions_for_phase376
    from research.phase385_cap_sensitivity_study import CAP_LEVELS, Phase385CapSensitivityStudy

    equity_floor = args.equity_floor if args.equity_floor is not None else args.initial_equity * 0.5
    sessions = discover_sessions_for_phase376(
        discover_session_roots(REPO),
        min_day=args.min_day,
        max_day=args.max_day,
        all_available=True,
    )
    study = Phase385CapSensitivityStudy(
        reports_dir=REPORTS,
        min_day=args.min_day,
        max_day=args.max_day,
        initial_equity=args.initial_equity,
        equity_floor=equity_floor,
    )
    jobs = [
        {"session_meta": m, "reports_dir": str(REPORTS), "min_day": args.min_day, "max_day": args.max_day}
        for m in sessions
    ]
    print(
        f"phase385 period={args.min_day}-{args.max_day} equity={args.initial_equity} "
        f"caps={len(CAP_LEVELS)} sessions={len(sessions)}",
        flush=True,
    )
    t0 = time.monotonic()
    loaded = skipped = 0
    workers = max(1, args.max_workers) if args.parallel else 1
    if args.parallel and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for fut in as_completed({pool.submit(_worker, j): j for j in jobs}):
                st = fut.result()
                if st.get("ok"):
                    study.ingest_session(st["result"])
                    loaded += 1
                else:
                    skipped += 1
    else:
        for job in jobs:
            st = _worker(job)
            if st.get("ok"):
                study.ingest_session(st["result"])
                loaded += 1
            else:
                skipped += 1

    result = study.run(
        parallel=args.parallel,
        max_workers=args.max_workers,
        wall_runtime_sec=time.monotonic() - t0,
        sessions_discovered=len(sessions),
        sessions_evaluated=loaded,
    )
    paths = study.write_outputs(result)
    summary = {k: v for k, v in result.items() if not str(k).startswith("_")}
    rec = summary.get("recommendation") or {}

    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)} loaded={loaded} skipped={skipped}", flush=True)
    print("\n=== Phase385 Summary ===", flush=True)
    print(f"recommended_live_cap={rec.get('recommended_live_cap')}", flush=True)
    print(f"best_pnl_cap={rec.get('best_pnl_cap')} best_pf_cap={rec.get('best_pf_cap')} risk_adj={rec.get('best_risk_adjusted_cap')}", flush=True)
    for row in summary.get("by_cap") or []:
        print(
            f"  CAP={row.get('cap')}: pnl={row.get('total_pnl_yen_100')} accepted={row.get('accepted_trade_count')} "
            f"reject={round(float(row.get('reject_rate') or 0)*100,1)}% PF={row.get('profit_factor')} "
            f"dd={row.get('max_drawdown_yen')} trailing={row.get('trailing_mfe_exit_count')} "
            f"low_mfe={row.get('low_mfe_stop_count')}",
            flush=True,
        )
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
