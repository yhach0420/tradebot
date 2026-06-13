#!/usr/bin/env python3
"""
Phase352: Limit-up proximity ENTRY guard historical validation (shadow only).
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
MIN_DAY = "20260518"


def _bootstrap() -> None:
    import sys

    src = REPO / "kabu_native" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _pf(yens: list[float]) -> Optional[float]:
    gp = sum(max(y, 0) for y in yens)
    gl = abs(sum(min(y, 0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _discover_sessions(
    *,
    min_day: str,
    all_available: bool,
    max_sessions: Optional[int],
    session_dirs: list[Path],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _bootstrap()
    from small_paper.limit_up_proximity_entry_guard_shadow import (
        _infer_session_kind,
        _load_session_summary,
        _session_source_label,
    )

    skipped: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []

    candidates: list[Path] = []
    if session_dirs:
        candidates = list(session_dirs)
    else:
        for ev_path in sorted(SMALL_PAPER.rglob("small_paper_events.csv")):
            candidates.append(ev_path.parent)

    seen: set[str] = set()
    for sess_dir in candidates:
        key = str(sess_dir.resolve())
        if key in seen:
            continue
        seen.add(key)
        day = sess_dir.parent.name
        if not day.isdigit() or len(day) != 8:
            skipped.append({"session_dir": str(sess_dir), "reason": "invalid_day_dir"})
            continue
        if day < min_day:
            continue
        ev_path = sess_dir / "small_paper_events.csv"
        if not ev_path.is_file():
            skipped.append({"session_dir": str(sess_dir), "reason": "missing_events_csv"})
            continue
        summary = _load_session_summary(sess_dir)
        kind = _infer_session_kind(sess_dir, summary)
        sessions.append(
            {
                "session_id": f"{day}/{sess_dir.name}",
                "day": day,
                "session_dir": str(sess_dir),
                "session_kind": kind,
                "session_start": summary.get("session_start"),
                "session_end": summary.get("session_end"),
                "session_source": _session_source_label(sess_dir),
            }
        )

    sessions.sort(key=lambda s: s["session_id"])
    if not all_available and max_sessions is not None:
        sessions = sessions[: max_sessions]
    elif max_sessions is not None:
        sessions = sessions[: max_sessions]

    return sessions, skipped


def _worker_job(job: dict[str, Any]) -> dict[str, Any]:
    _bootstrap()
    from small_paper.limit_up_proximity_entry_guard_shadow import evaluate_session

    t0 = time.monotonic()
    try:
        result = evaluate_session(job["session_meta"], reports_dir=Path(job["reports_dir"]))
        if int(result.get("trade_count_actual") or 0) <= 0:
            return {
                "ok": False,
                "session_id": job["session_meta"].get("session_id"),
                "error": "no_observer_exit_trades",
                "runtime_sec": round(time.monotonic() - t0, 2),
            }
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


def _rollup(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "session_count": 0,
            "actual_total_pnl_yen_100": 0.0,
            "shadow_total_pnl_yen_100": 0.0,
            "delta_yen": 0.0,
            "skipped_trade_count": 0,
            "skipped_trade_pnl_actual": 0.0,
            "stop_hit_reduction_count": 0,
            "improved_session_count": 0,
            "worsened_session_count": 0,
            "trade_count_actual": 0,
            "trade_count_shadow": 0,
            "_actual_yens": [],
            "_shadow_yens": [],
        }
    )
    for r in rows:
        k = str(r.get(key) or "unknown")
        bucket = acc[k]
        bucket["session_count"] += 1
        bucket["actual_total_pnl_yen_100"] += float(r["actual_total_pnl_yen_100"])
        bucket["shadow_total_pnl_yen_100"] += float(r["shadow_total_pnl_yen_100"])
        bucket["delta_yen"] += float(r["delta_yen"])
        bucket["skipped_trade_count"] += int(r["skipped_trade_count"])
        bucket["skipped_trade_pnl_actual"] += float(r["skipped_trade_pnl_actual"])
        bucket["stop_hit_reduction_count"] += int(r["stop_hit_reduction_count"])
        bucket["trade_count_actual"] += int(r["trade_count_actual"])
        bucket["trade_count_shadow"] += int(r["trade_count_shadow"])
        if r["delta_yen"] > 0:
            bucket["improved_session_count"] += 1
        elif r["delta_yen"] < 0:
            bucket["worsened_session_count"] += 1
        for t in r.get("_trade_yens") or []:
            bucket["_actual_yens"].append(float(t["actual"]))
            bucket["_shadow_yens"].append(float(t["shadow"]))

    out: dict[str, dict[str, Any]] = {}
    for k, b in acc.items():
        out[k] = {
            key: k,
            "session_count": b["session_count"],
            "actual_total_pnl_yen_100": round(b["actual_total_pnl_yen_100"], 2),
            "shadow_total_pnl_yen_100": round(b["shadow_total_pnl_yen_100"], 2),
            "delta_yen": round(b["delta_yen"], 2),
            "actual_pf": _pf(b["_actual_yens"]),
            "shadow_pf": _pf(b["_shadow_yens"]),
            "skipped_trade_count": b["skipped_trade_count"],
            "skipped_trade_pnl_actual": round(b["skipped_trade_pnl_actual"], 2),
            "stop_hit_reduction_count": b["stop_hit_reduction_count"],
            "improved_session_count": b["improved_session_count"],
            "worsened_session_count": b["worsened_session_count"],
            "trade_count_actual": b["trade_count_actual"],
            "trade_count_shadow": b["trade_count_shadow"],
        }
    return out


def _dependency_check(
    *,
    day_deltas: dict[str, float],
    symbol_skipped_pnl: dict[str, float],
    total_delta: float,
) -> dict[str, Any]:
    top_day = max(day_deltas, key=lambda d: day_deltas[d]) if day_deltas else ""
    top_day_share = (
        round(day_deltas[top_day] / total_delta, 4) if total_delta > 0 and top_day else None
    )
    neg_symbol_pnl = {s: p for s, p in symbol_skipped_pnl.items() if p < 0}
    top_sym = min(neg_symbol_pnl, key=neg_symbol_pnl.get) if neg_symbol_pnl else ""
    total_neg_skipped = sum(neg_symbol_pnl.values())
    top_sym_share = (
        round(abs(neg_symbol_pnl[top_sym]) / abs(total_neg_skipped), 4)
        if total_neg_skipped < 0 and top_sym
        else None
    )
    single_day_dominated = bool(top_day_share is not None and top_day_share > 0.5)
    single_symbol_dominated = bool(top_sym_share is not None and top_sym_share > 0.4)
    return {
        "top_day_by_delta": top_day,
        "top_day_delta_share": top_day_share,
        "top_symbol_by_negative_skipped_pnl": top_sym,
        "top_symbol_negative_skipped_share": top_sym_share,
        "single_day_dominated": single_day_dominated,
        "single_symbol_dominated": single_symbol_dominated,
        "dependency_ok": not single_day_dominated and not single_symbol_dominated,
    }


def _aggregate(session_results: list[dict[str, Any]]) -> dict[str, Any]:
    actual_total = 0.0
    shadow_total = 0.0
    skipped = 0
    skipped_pnl = 0.0
    stops_red = 0
    improved = 0
    worsened = 0
    trade_actual = 0
    trade_shadow = 0
    dyn_actual = 0.0
    dyn_shadow = 0.0
    core_actual = 0.0
    core_shadow = 0.0
    all_actual_yens: list[float] = []
    all_shadow_yens: list[float] = []

    session_rows: list[dict[str, Any]] = []
    day_deltas: dict[str, float] = defaultdict(float)
    symbol_skipped: dict[str, float] = defaultdict(float)

    for sr in session_results:
        sm = sr["session_meta"]
        row = {
            "session_id": sm["session_id"],
            "day": sm["day"],
            "session_kind": sm.get("session_kind", ""),
            "session_source": sr.get("session_source", ""),
            "actual_total_pnl_yen_100": sr["actual_total_pnl_yen_100"],
            "shadow_total_pnl_yen_100": sr["shadow_total_pnl_yen_100"],
            "delta_yen": sr["delta_yen"],
            "actual_pf": sr.get("actual_profit_factor_yen_100"),
            "shadow_pf": sr.get("profit_factor_yen_100"),
            "skipped_trade_count": sr["skipped_trade_count"],
            "skipped_trade_pnl_actual": sr["skipped_trade_pnl_actual"],
            "stop_hit_reduction_count": sr["stop_hit_reduction_count"],
            "improved_vs_actual": sr["improved_vs_actual"],
            "trade_count_actual": sr["trade_count_actual"],
            "trade_count_shadow": sr["trade_count_shadow"],
            "dynamic40_actual_pnl_yen_100": sr.get("dynamic40_actual_pnl_yen_100", 0.0),
            "dynamic40_shadow_pnl_yen_100": sr.get("dynamic40_shadow_pnl_yen_100", 0.0),
            "dynamic40_delta_yen": sr.get("dynamic40_delta_yen", 0.0),
            "core10_actual_pnl_yen_100": sr.get("core10_actual_pnl_yen_100", 0.0),
            "core10_shadow_pnl_yen_100": sr.get("core10_shadow_pnl_yen_100", 0.0),
            "core10_delta_yen": sr.get("core10_delta_yen", 0.0),
            "_trade_yens": [],
        }
        trade_yens = []
        for t in sr.get("trades") or []:
            ay = t.get("pnl_yen_100")
            sy = t.get("limit_up_proximity_shadow_pnl_yen_100")
            if ay is not None:
                all_actual_yens.append(float(ay))
            if sy is not None:
                all_shadow_yens.append(float(sy))
            trade_yens.append(
                {
                    "actual": float(ay or 0.0),
                    "shadow": float(sy or 0.0),
                }
            )
            if t.get("limit_up_proximity_guard_shadow_blocked"):
                symbol_skipped[str(t["symbol"])] += float(ay or 0.0)
        row["_trade_yens"] = trade_yens
        session_rows.append(row)

        actual_total += float(sr["actual_total_pnl_yen_100"])
        shadow_total += float(sr["shadow_total_pnl_yen_100"])
        skipped += int(sr["skipped_trade_count"])
        skipped_pnl += float(sr["skipped_trade_pnl_actual"])
        stops_red += int(sr["stop_hit_reduction_count"])
        trade_actual += int(sr["trade_count_actual"])
        trade_shadow += int(sr["trade_count_shadow"])
        dyn_actual += float(sr.get("dynamic40_actual_pnl_yen_100") or 0.0)
        dyn_shadow += float(sr.get("dynamic40_shadow_pnl_yen_100") or 0.0)
        core_actual += float(sr.get("core10_actual_pnl_yen_100") or 0.0)
        core_shadow += float(sr.get("core10_shadow_pnl_yen_100") or 0.0)
        day_deltas[str(sm["day"])] += float(sr["delta_yen"])
        if sr["delta_yen"] > 0:
            improved += 1
        elif sr["delta_yen"] < 0:
            worsened += 1

    delta = round(shadow_total - actual_total, 2)
    dyn_delta = round(dyn_shadow - dyn_actual, 2)
    core_delta = round(core_shadow - core_actual, 2)
    dep = _dependency_check(
        day_deltas=dict(day_deltas),
        symbol_skipped_pnl=dict(symbol_skipped),
        total_delta=delta,
    )

    by_am_pm = _rollup(session_rows, "session_kind")
    by_universe = {
        "dynamic40": {
            "universe_bucket": "dynamic40",
            "actual_total_pnl_yen_100": round(dyn_actual, 2),
            "shadow_total_pnl_yen_100": round(dyn_shadow, 2),
            "delta_yen": dyn_delta,
        },
        "core10": {
            "universe_bucket": "core10",
            "actual_total_pnl_yen_100": round(core_actual, 2),
            "shadow_total_pnl_yen_100": round(core_shadow, 2),
            "delta_yen": core_delta,
        },
    }
    by_day = _rollup(session_rows, "day")

    pass_checks = {
        "total_pnl_improved": delta > 0,
        "pf_improved": (_pf(all_shadow_yens) or 0) > (_pf(all_actual_yens) or 0),
        "skipped_pnl_negative": skipped_pnl < 0,
        "improved_ge_worsened": improved >= worsened,
        "dynamic40_improved": dyn_delta > 0,
        "dependency_ok": dep["dependency_ok"],
        "trade_count_not_too_low": trade_shadow >= trade_actual * 0.5,
    }

    return {
        "session_rows": session_rows,
        "by_day": by_day,
        "by_am_pm": by_am_pm,
        "by_universe": by_universe,
        "dependency": dep,
        "actual_total_pnl_yen_100": round(actual_total, 2),
        "shadow_total_pnl_yen_100": round(shadow_total, 2),
        "delta_yen": delta,
        "actual_pf": _pf(all_actual_yens),
        "shadow_pf": _pf(all_shadow_yens),
        "skipped_trade_count": skipped,
        "skipped_trade_pnl_actual": round(skipped_pnl, 2),
        "stop_hit_reduction_count": stops_red,
        "improved_session_count": improved,
        "worsened_session_count": worsened,
        "trade_count_actual": trade_actual,
        "trade_count_shadow": trade_shadow,
        "dynamic40_actual_pnl_yen_100": round(dyn_actual, 2),
        "dynamic40_shadow_pnl_yen_100": round(dyn_shadow, 2),
        "dynamic40_delta_yen": dyn_delta,
        "core10_actual_pnl_yen_100": round(core_actual, 2),
        "core10_shadow_pnl_yen_100": round(core_shadow, 2),
        "core10_delta_yen": core_delta,
        "pass_checks": pass_checks,
        "production_adoption_ready": all(pass_checks.values()),
        "symbol_skipped_pnl": dict(sorted(symbol_skipped.items(), key=lambda x: x[1])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase352 limit-up guard historical validation")
    parser.add_argument("--min-day", default=MIN_DAY)
    parser.add_argument("--all-available-sessions", action="store_true", default=False)
    parser.add_argument("--max-sessions", type=int, default=None)
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
    sessions, discover_skipped = _discover_sessions(
        min_day=args.min_day,
        all_available=bool(args.all_available_sessions or not args.max_sessions),
        max_sessions=args.max_sessions,
        session_dirs=session_dirs,
    )
    if not sessions:
        raise SystemExit("no sessions found")

    temp_dir = args.worker_temp_dir or (
        OUT_DIR / f"_phase352_temp_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}"
    )
    temp_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    session_results: list[dict[str, Any]] = []
    worker_errors: list[dict[str, Any]] = []

    if args.parallel and args.max_workers > 1 and len(sessions) > 1:
        jobs = [
            {
                "session_meta": sm,
                "reports_dir": str(OUT_DIR),
                "output_path": str(temp_dir / f"worker_{i:04d}.json"),
            }
            for i, sm in enumerate(sessions)
        ]
        with ProcessPoolExecutor(max_workers=args.max_workers) as pool:
            futures = {pool.submit(_worker_job, job): job for job in jobs}
            for fut in as_completed(futures):
                status = fut.result()
                if not status.get("ok"):
                    worker_errors.append(status)
                    print(f"SKIP {status.get('session_id')}: {status.get('error')}")
                    continue
                session_results.append(
                    json.loads(Path(status["output_path"]).read_text(encoding="utf-8"))
                )
    else:
        _bootstrap()
        from small_paper.limit_up_proximity_entry_guard_shadow import evaluate_session

        for sm in sessions:
            try:
                result = evaluate_session(sm, reports_dir=OUT_DIR)
                if int(result.get("trade_count_actual") or 0) <= 0:
                    worker_errors.append(
                        {"session_id": sm["session_id"], "error": "no_observer_exit_trades"}
                    )
                    continue
                session_results.append(result)
            except Exception as exc:
                worker_errors.append({"session_id": sm["session_id"], "error": str(exc)})

    session_results.sort(key=lambda r: r["session_meta"]["session_id"])
    agg = _aggregate(session_results)

    session_rows_out = []
    for r in agg["session_rows"]:
        row = {k: v for k, v in r.items() if k != "_trade_yens"}
        session_rows_out.append(row)

    by_day_rows = [agg["by_day"][k] for k in sorted(agg["by_day"])]
    by_universe_rows = list(agg["by_universe"].values())
    by_symbol_rows = []
    for sym, pnl in agg["symbol_skipped_pnl"].items():
        by_symbol_rows.append(
            {
                "symbol": sym,
                "skipped_trade_pnl_actual": round(pnl, 2),
                "skipped_trade_count": sum(
                    1
                    for sr in session_results
                    for t in sr.get("trades") or []
                    if str(t.get("symbol")) == sym
                    and t.get("limit_up_proximity_guard_shadow_blocked")
                ),
            }
        )

    trade_rows = []
    for sr in session_results:
        sm = sr["session_meta"]
        for t in sr.get("trades") or []:
            trade_rows.append({**t, "session_id": sm["session_id"]})

    summary = {
        "phase": 352,
        "title": "Limit-Up Proximity Entry Guard Historical Validation",
        "guard_variant": "A_limit_up_proximity_guard",
        "min_day": args.min_day,
        "sessions_evaluated": len(session_results),
        "sessions_discovered": len(sessions),
        "sessions_skipped": len(worker_errors),
        "discover_skipped": discover_skipped,
        "worker_errors": worker_errors,
        "parallel": bool(args.parallel),
        "max_workers": args.max_workers,
        "streaming": bool(args.streaming),
        "wall_runtime_sec": round(time.monotonic() - t0, 2),
        "actual_total_pnl_yen_100": agg["actual_total_pnl_yen_100"],
        "shadow_total_pnl_yen_100": agg["shadow_total_pnl_yen_100"],
        "delta_yen": agg["delta_yen"],
        "actual_pf": agg["actual_pf"],
        "shadow_pf": agg["shadow_pf"],
        "skipped_trade_count": agg["skipped_trade_count"],
        "skipped_trade_pnl_actual": agg["skipped_trade_pnl_actual"],
        "stop_hit_reduction_count": agg["stop_hit_reduction_count"],
        "improved_session_count": agg["improved_session_count"],
        "worsened_session_count": agg["worsened_session_count"],
        "trade_count_actual": agg["trade_count_actual"],
        "trade_count_shadow": agg["trade_count_shadow"],
        "by_am_pm": agg["by_am_pm"],
        "by_universe": agg["by_universe"],
        "dependency_analysis": agg["dependency"],
        "pass_checks": agg["pass_checks"],
        "production_adoption_ready": agg["production_adoption_ready"],
        "notes": [
            "Shadow only: blocked ENTRY removed from PnL; no replacement entries.",
            "No production ENTRY / EXIT / Discord changes.",
        ],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "phase352_limit_up_guard_historical_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if session_rows_out:
        _write_csv(
            OUT_DIR / "phase352_limit_up_guard_historical_sessions.csv",
            session_rows_out,
            sorted({k for r in session_rows_out for k in r}),
        )
    if by_day_rows:
        _write_csv(
            OUT_DIR / "phase352_limit_up_guard_historical_by_day.csv",
            by_day_rows,
            sorted({k for r in by_day_rows for k in r}),
        )
    if by_symbol_rows:
        _write_csv(
            OUT_DIR / "phase352_limit_up_guard_historical_by_symbol.csv",
            by_symbol_rows,
            sorted({k for r in by_symbol_rows for k in r}),
        )
    if by_universe_rows:
        _write_csv(
            OUT_DIR / "phase352_limit_up_guard_historical_by_universe.csv",
            by_universe_rows,
            sorted({k for r in by_universe_rows for k in r}),
        )
    if trade_rows:
        _write_csv(
            OUT_DIR / "phase352_limit_up_guard_historical_trades.csv",
            trade_rows,
            sorted({k for r in trade_rows for k in r}),
        )

    if not args.keep_worker_temp:
        shutil.rmtree(temp_dir, ignore_errors=True)

    print(
        json.dumps(
            {
                "sessions_evaluated": len(session_results),
                "delta_yen": agg["delta_yen"],
                "actual_pf": agg["actual_pf"],
                "shadow_pf": agg["shadow_pf"],
                "skipped_trade_count": agg["skipped_trade_count"],
                "skipped_trade_pnl_actual": agg["skipped_trade_pnl_actual"],
                "stop_hit_reduction_count": agg["stop_hit_reduction_count"],
                "improved_session_count": agg["improved_session_count"],
                "worsened_session_count": agg["worsened_session_count"],
                "production_adoption_ready": agg["production_adoption_ready"],
                "pass_checks": agg["pass_checks"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
