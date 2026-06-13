#!/usr/bin/env python3
"""
Phase351: Limit-Up Proximity ENTRY guard production shadow aggregation.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT_DIR = REPO / "kabu_native" / "results" / "reports"
JST = ZoneInfo("Asia/Tokyo")


def _bootstrap() -> None:
    import sys

    src = REPO / "kabu_native" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _discover_sessions(
    *,
    recent_days: Optional[int],
    session_dirs: list[Path],
) -> list[dict[str, Any]]:
    if session_dirs:
        sessions: list[dict[str, Any]] = []
        for sess_dir in session_dirs:
            summ_path = sess_dir / "small_paper_summary.json"
            ev_path = sess_dir / "small_paper_events.csv"
            if not summ_path.is_file() or not ev_path.is_file():
                continue
            day = sess_dir.parent.name
            summary = json.loads(summ_path.read_text(encoding="utf-8"))
            start = str(summary.get("session_start") or "")
            kind = "am" if start < "12:00" else "pm"
            sessions.append(
                {
                    "session_id": f"{day}/{sess_dir.name}",
                    "day": day,
                    "session_dir": str(sess_dir),
                    "session_kind": kind,
                    "session_start": start,
                    "session_end": summary.get("session_end"),
                }
            )
        return sorted(sessions, key=lambda s: s["session_id"])

    day_dirs = sorted(
        [p for p in SMALL_PAPER.iterdir() if p.is_dir() and p.name.isdigit() and len(p.name) == 8],
        key=lambda p: p.name,
        reverse=True,
    )
    if recent_days is not None:
        day_dirs = day_dirs[:recent_days]
    sessions = []
    for day_path in sorted(day_dirs, key=lambda p: p.name):
        day = day_path.name
        for sess_dir in sorted(day_path.glob("live_session_*")):
            summ_path = sess_dir / "small_paper_summary.json"
            ev_path = sess_dir / "small_paper_events.csv"
            if not summ_path.is_file() or not ev_path.is_file():
                continue
            summary = json.loads(summ_path.read_text(encoding="utf-8"))
            start = str(summary.get("session_start") or "")
            kind = "am" if start < "12:00" else "pm"
            sessions.append(
                {
                    "session_id": f"{day}/{sess_dir.name}",
                    "day": day,
                    "session_dir": str(sess_dir),
                    "session_kind": kind,
                    "session_start": start,
                    "session_end": summary.get("session_end"),
                }
            )
    return sessions


def _worker_job(job: dict[str, Any]) -> dict[str, Any]:
    _bootstrap()
    from small_paper.limit_up_proximity_entry_guard_shadow import evaluate_session

    t0 = time.monotonic()
    try:
        result = evaluate_session(job["session_meta"], reports_dir=Path(job["reports_dir"]))
        out_path = Path(job["output_path"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return {
            "ok": True,
            "output_path": str(out_path),
            "session_id": job["session_meta"].get("session_id"),
            "runtime_sec": round(time.monotonic() - t0, 2),
        }
    except Exception as exc:
        return {
            "ok": False,
            "session_id": job.get("session_meta", {}).get("session_id"),
            "error": str(exc),
            "runtime_sec": round(time.monotonic() - t0, 2),
        }


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _aggregate(session_results: list[dict[str, Any]]) -> dict[str, Any]:
    actual_total = 0.0
    shadow_total = 0.0
    skipped = 0
    skipped_pnl = 0.0
    stops_actual = 0
    stops_shadow = 0
    improved = 0
    worsened = 0
    trade_count_actual = 0
    trade_count_shadow = 0
    dyn_shadow = 0.0
    core_shadow = 0.0
    affected: set[str] = set()

    for sr in session_results:
        actual_total += float(sr["actual_total_pnl_yen_100"])
        shadow_total += float(sr["shadow_total_pnl_yen_100"])
        skipped += int(sr["skipped_trade_count"])
        skipped_pnl += float(sr["skipped_trade_pnl_actual"])
        stops_actual += int(sr["stop_hit_count_actual"])
        stops_shadow += int(sr["stop_hit_count_shadow"])
        trade_count_actual += int(sr["trade_count_actual"])
        trade_count_shadow += int(sr["trade_count_shadow"])
        dyn_shadow += float(sr["dynamic40_shadow_pnl_yen_100"])
        core_shadow += float(sr["core10_shadow_pnl_yen_100"])
        affected.update(sr.get("affected_symbols") or [])
        if sr["delta_yen"] > 0:
            improved += 1
        elif sr["delta_yen"] < 0:
            worsened += 1

    delta = round(shadow_total - actual_total, 2)
    pass_checks = {
        "total_pnl_improved": delta > 0,
        "skipped_pnl_negative": skipped_pnl < 0,
        "fewer_worsened_sessions": worsened <= improved,
        "trade_count_not_too_low": trade_count_shadow >= trade_count_actual * 0.5,
    }
    return {
        "actual_total_pnl_yen_100": round(actual_total, 2),
        "shadow_total_pnl_yen_100": round(shadow_total, 2),
        "delta_yen": delta,
        "skipped_trade_count": skipped,
        "skipped_trade_pnl_actual": round(skipped_pnl, 2),
        "stop_hit_count_actual": stops_actual,
        "stop_hit_count_shadow": stops_shadow,
        "stop_hit_reduction_count": stops_actual - stops_shadow,
        "trade_count_actual": trade_count_actual,
        "trade_count_shadow": trade_count_shadow,
        "improved_session_count": improved,
        "worsened_session_count": worsened,
        "dynamic40_shadow_pnl_yen_100": round(dyn_shadow, 2),
        "core10_shadow_pnl_yen_100": round(core_shadow, 2),
        "affected_symbols": sorted(affected),
        "pass_checks": pass_checks,
        "monitoring_ready": all(pass_checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase351 limit-up ENTRY guard production shadow")
    parser.add_argument("--recent-days", type=int, default=None)
    parser.add_argument("--session-dir", action="append", default=[], type=Path)
    parser.add_argument("--parallel", action="store_true", default=False)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-streaming", action="store_false", dest="streaming")
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    parser.add_argument("--worker-temp-dir", type=Path, default=None)
    parser.add_argument("--keep-worker-temp", action="store_true", default=False)
    args = parser.parse_args()

    session_dirs = [Path(p) for p in args.session_dir]
    sessions = _discover_sessions(recent_days=args.recent_days, session_dirs=session_dirs)
    if not sessions:
        raise SystemExit("no sessions found")

    temp_dir = args.worker_temp_dir or (
        OUT_DIR / f"_phase351_temp_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}"
    )
    temp_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    session_results: list[dict[str, Any]] = []

    if args.parallel and args.max_workers > 1 and len(sessions) > 1:
        jobs = [
            {
                "session_meta": sm,
                "reports_dir": str(OUT_DIR),
                "output_path": str(temp_dir / f"worker_{i:03d}.json"),
            }
            for i, sm in enumerate(sessions)
        ]
        with ProcessPoolExecutor(max_workers=args.max_workers) as pool:
            futures = {pool.submit(_worker_job, job): job for job in jobs}
            for fut in as_completed(futures):
                status = fut.result()
                if not status.get("ok"):
                    print(f"FAIL {status.get('session_id')}: {status.get('error')}")
                    continue
                session_results.append(
                    json.loads(Path(status["output_path"]).read_text(encoding="utf-8"))
                )
    else:
        _bootstrap()
        from small_paper.limit_up_proximity_entry_guard_shadow import evaluate_session

        for sm in sessions:
            session_results.append(evaluate_session(sm, reports_dir=OUT_DIR))

    session_results.sort(key=lambda r: r["session_meta"]["session_id"])
    agg = _aggregate(session_results)

    session_rows = []
    trade_rows = []
    symbol_acc: dict[str, dict[str, float]] = defaultdict(
        lambda: {"skipped_pnl": 0.0, "trade_count": 0, "actual_pnl": 0.0}
    )
    for sr in session_results:
        sm = sr["session_meta"]
        session_rows.append(
            {
                "session_id": sm["session_id"],
                "day": sm["day"],
                "session_kind": sm["session_kind"],
                "actual_total_pnl_yen_100": sr["actual_total_pnl_yen_100"],
                "shadow_total_pnl_yen_100": sr["shadow_total_pnl_yen_100"],
                "delta_yen": sr["delta_yen"],
                "skipped_trade_count": sr["skipped_trade_count"],
                "skipped_trade_pnl_actual": sr["skipped_trade_pnl_actual"],
                "stop_hit_reduction_count": sr["stop_hit_reduction_count"],
                "improved_vs_actual": sr["improved_vs_actual"],
                "trade_count_actual": sr["trade_count_actual"],
                "trade_count_shadow": sr["trade_count_shadow"],
                "dynamic40_shadow_pnl_yen_100": sr["dynamic40_shadow_pnl_yen_100"],
                "core10_shadow_pnl_yen_100": sr["core10_shadow_pnl_yen_100"],
                "affected_symbol_count": len(sr.get("affected_symbols") or []),
            }
        )
        for t in sr.get("trades") or []:
            trade_rows.append({**t, "session_id": sm["session_id"]})
            sym = str(t["symbol"])
            symbol_acc[sym]["actual_pnl"] += float(t.get("pnl_yen_100") or 0)
            if t.get("limit_up_proximity_guard_shadow_blocked"):
                symbol_acc[sym]["skipped_pnl"] += float(t.get("pnl_yen_100") or 0)
                symbol_acc[sym]["trade_count"] += 1

    summary = {
        "phase": 351,
        "title": "Limit-Up Proximity Entry Guard Production Shadow",
        "guard_variant": "A_limit_up_proximity_guard",
        "guard_rules": [
            "distance_to_limit_up_pct <= 0.5%",
            "OR day_high_near_limit = true",
        ],
        "sessions": [s["session_id"] for s in sessions],
        "session_count": len(sessions),
        "parallel": bool(args.parallel),
        "max_workers": args.max_workers,
        "streaming": bool(args.streaming),
        "wall_runtime_sec": round(time.monotonic() - t0, 2),
        **agg,
        "notes": [
            "Shadow only: blocked ENTRY removed from PnL; no replacement entries.",
            "No production ENTRY / EXIT / Discord changes.",
            "B_pullback_misread_guard and C_combined_entry_guard are frozen.",
        ],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "phase351_limit_up_entry_guard_shadow_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if session_rows:
        _write_csv(
            OUT_DIR / "phase351_limit_up_entry_guard_shadow_sessions.csv",
            session_rows,
            sorted({k for r in session_rows for k in r}),
        )
    if trade_rows:
        _write_csv(
            OUT_DIR / "phase351_limit_up_entry_guard_shadow_trades.csv",
            trade_rows,
            sorted({k for r in trade_rows for k in r}),
        )

    if not args.keep_worker_temp:
        shutil.rmtree(temp_dir, ignore_errors=True)

    print(
        json.dumps(
            {
                "delta_yen": agg["delta_yen"],
                "skipped_trade_count": agg["skipped_trade_count"],
                "skipped_trade_pnl_actual": agg["skipped_trade_pnl_actual"],
                "stop_hit_reduction_count": agg["stop_hit_reduction_count"],
                "monitoring_ready": agg["monitoring_ready"],
                "pass_checks": agg["pass_checks"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
