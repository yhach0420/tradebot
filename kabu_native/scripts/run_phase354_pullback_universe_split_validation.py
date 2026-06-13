#!/usr/bin/env python3
"""
Phase354: Pullback misread guard universe/session split validation (shadow only).
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

    for p in (REPO, REPO / "kabu_native" / "src"):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _pf(yens: list[float]) -> Optional[float]:
    gp = sum(max(y, 0) for y in yens)
    gl = abs(sum(min(y, 0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _discover_sessions(*, min_day: str, max_sessions: Optional[int]) -> list[dict[str, Any]]:
    _bootstrap()
    from small_paper.limit_up_proximity_entry_guard_shadow import (
        _infer_session_kind,
        _load_session_summary,
        _session_source_label,
    )

    sessions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ev_path in sorted(SMALL_PAPER.rglob("small_paper_events.csv")):
        sess_dir = ev_path.parent
        key = str(sess_dir.resolve())
        if key in seen:
            continue
        seen.add(key)
        day = sess_dir.parent.name
        if not day.isdigit() or len(day) != 8 or day < min_day:
            continue
        summary = _load_session_summary(sess_dir)
        kind = _infer_session_kind(sess_dir, summary)
        sessions.append(
            {
                "session_id": f"{day}/{sess_dir.name}",
                "day": day,
                "session_dir": str(sess_dir),
                "session_kind": kind,
                "session_source": _session_source_label(sess_dir),
            }
        )
    sessions.sort(key=lambda s: s["session_id"])
    if max_sessions is not None:
        sessions = sessions[:max_sessions]
    return sessions


def _worker_job(job: dict[str, Any]) -> dict[str, Any]:
    _bootstrap()
    from small_paper.pullback_misread_entry_guard_shadow import evaluate_session

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


def _aggregate(session_results: list[dict[str, Any]], variants: tuple[str, ...]) -> dict[str, Any]:
    from small_paper.pullback_misread_entry_guard_shadow import variant_blocked

    by_variant: dict[str, dict[str, Any]] = {}
    by_day_variant: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {
            "delta_yen": 0.0,
            "skipped_trade_count": 0,
            "skipped_trade_pnl_actual": 0.0,
            "session_count": 0,
        }
    )
    by_am_pm_variant: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {
            "delta_yen": 0.0,
            "dynamic40_delta_yen": 0.0,
            "core10_delta_yen": 0.0,
            "session_count": 0,
        }
    )
    symbol_skipped: dict[tuple[str, str], float] = defaultdict(float)

    for variant in variants:
        actual_total = shadow_total = skipped_pnl = dyn_actual = dyn_shadow = core_actual = core_shadow = 0.0
        skipped = stops_red = improved = worsened = trade_actual = trade_shadow = 0
        all_actual_yens: list[float] = []
        all_shadow_yens: list[float] = []

        for sr in session_results:
            sm = sr["session_meta"]
            v = sr["variants"][variant]
            actual_total += float(v["actual_total_pnl_yen_100"])
            shadow_total += float(v["shadow_total_pnl_yen_100"])
            skipped += int(v["skipped_trade_count"])
            skipped_pnl += float(v["skipped_trade_pnl_actual"])
            stops_red += int(v["stop_hit_reduction_count"])
            trade_actual += int(v["trade_count_actual"])
            trade_shadow += int(v["trade_count_shadow"])
            dyn_actual += float(v["dynamic40_actual_pnl_yen_100"])
            dyn_shadow += float(v["dynamic40_shadow_pnl_yen_100"])
            core_actual += float(v["core10_actual_pnl_yen_100"])
            core_shadow += float(v["core10_shadow_pnl_yen_100"])
            delta = float(v["delta_yen"])
            if delta > 0:
                improved += 1
            elif delta < 0:
                worsened += 1

            day_key = (str(sm["day"]), variant)
            by_day_variant[day_key]["delta_yen"] += delta
            by_day_variant[day_key]["skipped_trade_count"] += int(v["skipped_trade_count"])
            by_day_variant[day_key]["skipped_trade_pnl_actual"] += float(v["skipped_trade_pnl_actual"])
            by_day_variant[day_key]["session_count"] += 1

            kind_key = (str(sm.get("session_kind") or ""), variant)
            by_am_pm_variant[kind_key]["delta_yen"] += delta
            by_am_pm_variant[kind_key]["dynamic40_delta_yen"] += float(v["dynamic40_delta_yen"])
            by_am_pm_variant[kind_key]["core10_delta_yen"] += float(v["core10_delta_yen"])
            by_am_pm_variant[kind_key]["session_count"] += 1

            for t in sr.get("trades") or []:
                ay = t.get("pnl_yen_100")
                if ay is None:
                    continue
                all_actual_yens.append(float(ay))
                blocked = variant_blocked(
                    variant, t, session_kind=str(sr.get("session_kind") or "")
                )
                all_shadow_yens.append(0.0 if blocked else float(ay))
                if blocked:
                    symbol_skipped[(variant, str(t["symbol"]))] += float(ay)

        delta_total = round(shadow_total - actual_total, 2)
        by_variant[variant] = {
            "variant": variant,
            "actual_total_pnl_yen_100": round(actual_total, 2),
            "shadow_total_pnl_yen_100": round(shadow_total, 2),
            "delta_yen": delta_total,
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
            "dynamic40_delta_yen": round(dyn_shadow - dyn_actual, 2),
            "core10_actual_pnl_yen_100": round(core_actual, 2),
            "core10_shadow_pnl_yen_100": round(core_shadow, 2),
            "core10_delta_yen": round(core_shadow - core_actual, 2),
        }

    am_612 = {}
    for variant in variants:
        for sr in session_results:
            sm = sr["session_meta"]
            if sm.get("day") == "20260612" and sm.get("session_kind") == "am":
                if "live_session" in str(sm.get("session_id") or ""):
                    am_612[variant] = sr["variants"][variant]["delta_yen"]

    best = max(variants, key=lambda v: by_variant[v]["delta_yen"])
    best_row = by_variant[best]
    pass_checks = {
        "total_pnl_improved": best_row["delta_yen"] > 0,
        "pf_improved": (best_row.get("shadow_pf") or 0) > (best_row.get("actual_pf") or 0),
        "skipped_pnl_negative": best_row["skipped_trade_pnl_actual"] < 0,
        "improved_ge_worsened": best_row["improved_session_count"] >= best_row["worsened_session_count"],
        "stop_hit_reduction": best_row["stop_hit_reduction_count"] > 0,
        "dynamic40_improved": best_row["dynamic40_delta_yen"] > 0,
        "core10_not_harmed": best_row["core10_delta_yen"] >= 0,
    }

    return {
        "by_variant": by_variant,
        "best_variant": best,
        "am_20260612_delta_by_variant": am_612,
        "by_day_variant": by_day_variant,
        "by_am_pm_variant": by_am_pm_variant,
        "symbol_skipped": symbol_skipped,
        "pass_checks_best": pass_checks,
        "production_shadow_ready": all(
            [
                pass_checks["total_pnl_improved"],
                pass_checks["pf_improved"],
                pass_checks["skipped_pnl_negative"],
                pass_checks["improved_ge_worsened"],
                pass_checks["stop_hit_reduction"],
                pass_checks["dynamic40_improved"],
            ]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase354 pullback universe split validation")
    parser.add_argument("--min-day", default=MIN_DAY)
    parser.add_argument("--all-available-sessions", action="store_true", default=True)
    parser.add_argument("--max-sessions", type=int, default=None)
    parser.add_argument("--parallel", action="store_true", default=False)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    parser.add_argument("--worker-temp-dir", type=Path, default=None)
    parser.add_argument("--keep-worker-temp", action="store_true", default=False)
    args = parser.parse_args()

    _bootstrap()
    from small_paper.pullback_misread_entry_guard_shadow import SPLIT_VARIANTS

    sessions = _discover_sessions(min_day=args.min_day, max_sessions=args.max_sessions)
    if not sessions:
        raise SystemExit("no sessions found")

    temp_dir = args.worker_temp_dir or (
        OUT_DIR / f"_phase354_temp_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}"
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
        from small_paper.pullback_misread_entry_guard_shadow import evaluate_session

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
    agg = _aggregate(session_results, SPLIT_VARIANTS)

    variant_rows = [agg["by_variant"][v] for v in SPLIT_VARIANTS]
    by_day_rows = [
        {
            "day": day,
            "variant": variant,
            "delta_yen": round(vals["delta_yen"], 2),
            "skipped_trade_count": int(vals["skipped_trade_count"]),
            "skipped_trade_pnl_actual": round(vals["skipped_trade_pnl_actual"], 2),
            "session_count": int(vals["session_count"]),
        }
        for (day, variant), vals in sorted(agg["by_day_variant"].items())
    ]
    by_symbol_rows = [
        {
            "variant": variant,
            "symbol": sym,
            "skipped_trade_pnl_actual": round(pnl, 2),
        }
        for (variant, sym), pnl in sorted(agg["symbol_skipped"].items(), key=lambda x: (x[0][0], x[1]))
    ]

    best = agg["best_variant"]
    summary = {
        "phase": 354,
        "title": "Pullback Misread Guard Universe Split Validation",
        "guard": "B_pullback_misread_guard",
        "variants": {
            "A_all_symbols": "all symbols (Phase353 reproduction)",
            "B_dynamic40_only": "Dynamic40 only",
            "C_core10_only": "Core10 only",
            "D_am_dynamic40_only": "AM session + Dynamic40 only",
            "E_am_all_symbols": "AM session + all symbols",
        },
        "sessions_evaluated": len(session_results),
        "sessions_discovered": len(sessions),
        "sessions_skipped": len(worker_errors),
        "parallel": bool(args.parallel),
        "max_workers": args.max_workers,
        "wall_runtime_sec": round(time.monotonic() - t0, 2),
        "by_variant": agg["by_variant"],
        "by_am_pm": {
            f"{kind}/{variant}": vals
            for (kind, variant), vals in sorted(agg["by_am_pm_variant"].items())
        },
        "best_variant": best,
        "best_variant_metrics": agg["by_variant"][best],
        "am_20260612_delta_by_variant": agg["am_20260612_delta_by_variant"],
        "pass_checks_best": agg["pass_checks_best"],
        "production_shadow_ready": agg["production_shadow_ready"],
        "conclusion": {
            "highest_expectancy_variant": best,
            "recommendation": (
                f"Proceed with production shadow pilot using {best}."
                if agg["production_shadow_ready"]
                else "Continue shadow monitoring; no variant meets adoption bar."
            ),
            "notes": [
                "B_dynamic40_only isolates Dynamic40 benefit without Core10 drag.",
                "D_am_dynamic40_only targets 6/12-type AM crash while limiting PM/Core10 side effects.",
                "No actual ENTRY / EXIT / Discord changes.",
            ],
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "phase354_pullback_universe_split_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(
        OUT_DIR / "phase354_pullback_universe_split_by_variant.csv",
        variant_rows,
        sorted({k for r in variant_rows for k in r}),
    )
    if by_day_rows:
        _write_csv(
            OUT_DIR / "phase354_pullback_universe_split_by_day.csv",
            by_day_rows,
            sorted({k for r in by_day_rows for k in r}),
        )
    if by_symbol_rows:
        _write_csv(
            OUT_DIR / "phase354_pullback_universe_split_by_symbol.csv",
            by_symbol_rows,
            ["variant", "symbol", "skipped_trade_pnl_actual"],
        )

    if not args.keep_worker_temp:
        shutil.rmtree(temp_dir, ignore_errors=True)

    br = agg["by_variant"][best]
    print(
        json.dumps(
            {
                "best_variant": best,
                "delta_yen": br["delta_yen"],
                "shadow_pf": br["shadow_pf"],
                "dynamic40_delta_yen": br["dynamic40_delta_yen"],
                "core10_delta_yen": br["core10_delta_yen"],
                "production_shadow_ready": agg["production_shadow_ready"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
