#!/usr/bin/env python3
"""
Phase350: Recent N-day ENTRY guard shadow validation (no production changes).
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT_DIR = REPO / "kabu_native" / "results" / "reports"
JST = ZoneInfo("Asia/Tokyo")
NEAR_LIMIT_PCT = 0.5

VARIANTS = (
    "A_limit_up_proximity_guard",
    "B_pullback_misread_guard",
    "C_combined_entry_guard",
    "D_dynamic40_limit_penalty",
)


def _bootstrap() -> None:
    import sys

    src = REPO / "kabu_native" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _bool(v: Any) -> bool:
    return str(v or "").lower() in ("true", "1", "yes")


def _pf(yens: list[float]) -> Optional[float]:
    gp = sum(max(y, 0) for y in yens)
    gl = abs(sum(min(y, 0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _entry_key(sym: str, ent: str) -> str:
    return f"{sym}|{ent}"


def _load_universe(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {str(r.get("symbol") or ""): r for r in csv.DictReader(f)}


def _universe_path_for_session(day: str, session_kind: str, summary: dict[str, Any]) -> Path:
    p = summary.get("intraday_refresh_csv")
    if p:
        path = Path(str(p))
        if path.is_file():
            return path
    if session_kind == "am":
        return OUT_DIR / f"universe_core10_dynamic40_price_risk_am_refresh1000_{day}.csv"
    return OUT_DIR / f"universe_core10_dynamic40_price_risk_pm_refresh1430_{day}.csv"


def _discover_sessions(recent_days: int) -> list[dict[str, Any]]:
    day_dirs = sorted(
        [p for p in SMALL_PAPER.iterdir() if p.is_dir() and p.name.isdigit() and len(p.name) == 8],
        key=lambda p: p.name,
        reverse=True,
    )
    days = [p.name for p in day_dirs[:recent_days]]
    sessions: list[dict[str, Any]] = []
    for day in sorted(days):
        day_path = SMALL_PAPER / day
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
                    "universe_path": str(_universe_path_for_session(day, kind, summary)),
                }
            )
    return sessions


def _stream_events_csv(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            yield row


def _enrich_entry_features(
    acc: dict[str, str],
    ex: dict[str, str],
    universe: dict[str, dict[str, str]],
) -> dict[str, Any]:
    from universe.am_pm_universe import estimate_daily_limit_prices, limit_status_from_prices

    sym = str(ex.get("symbol") or "")
    u = universe.get(sym, {})
    prev_close = _float(u.get("close_price"))
    entry_px = _float(ex.get("entry_price")) or _float(acc.get("current_price"))
    near_high = _float(acc.get("entry_near_day_high_pct") or ex.get("entry_near_day_high_pct"))
    rise5 = _float(acc.get("entry_rise_5min_pct") or ex.get("entry_rise_5min_pct"))
    vwap_dev = _float(acc.get("entry_vwap_dev_pct") or ex.get("entry_vwap_dev_pct"))

    implied_day_high: Optional[float] = None
    if entry_px and near_high is not None and near_high < 100:
        implied_day_high = round(entry_px / (1.0 - near_high / 100.0), 2)

    lim_up, lim_down, _ = estimate_daily_limit_prices(prev_close)
    lim = limit_status_from_prices(
        current=entry_px,
        limit_up=lim_up,
        limit_down=lim_down,
        bid_qty=None,
        ask_qty=None,
    )
    dist_up = _float(lim.get("distance_to_limit_up_pct"))
    day_high_near_limit = False
    if implied_day_high and lim_up and lim_up > 0:
        day_high_near_limit = (lim_up - implied_day_high) / lim_up * 100.0 <= NEAR_LIMIT_PCT

    ep, xp = _float(ex.get("entry_price")), _float(ex.get("exit_price"))
    yen = round((xp - ep) * 100.0, 2) if ep is not None and xp is not None else None
    reason = str(ex.get("structural_exit_reason") or ex.get("exit_reason") or "")

    return {
        "session_id": "",
        "day": "",
        "session_kind": "",
        "trade_key": _entry_key(sym, str(ex.get("entry_time") or "")),
        "symbol": sym,
        "entry_time": ex.get("entry_time"),
        "exit_time": ex.get("exit_time"),
        "entry_price": entry_px,
        "exit_price": xp,
        "pnl_yen_100": yen,
        "pnl_pct": _float(ex.get("pnl_pct")),
        "is_stop_hit": reason == "stop_hit",
        "exit_reason": reason,
        "distance_to_limit_up_pct": dist_up,
        "near_limit_up": bool(lim.get("near_limit_up")),
        "is_limit_up": bool(lim.get("is_limit_up")),
        "day_high_near_limit": day_high_near_limit,
        "entry_rise_5min_pct": rise5,
        "entry_vwap_dev_pct": vwap_dev,
        "entry_near_day_high_pct": near_high,
        "universe_slot": u.get("universe_slot", ""),
        "source_bucket": u.get("source_bucket", ""),
    }


def _guard_a(t: Mapping[str, Any]) -> bool:
    dist = _float(t.get("distance_to_limit_up_pct"))
    if dist is not None and dist <= NEAR_LIMIT_PCT:
        return True
    return _bool(t.get("day_high_near_limit"))


def _guard_b(t: Mapping[str, Any]) -> bool:
    rise5 = _float(t.get("entry_rise_5min_pct"))
    vwap_dev = _float(t.get("entry_vwap_dev_pct"))
    return rise5 is not None and rise5 < 0 and vwap_dev is not None and vwap_dev < 0


def _guard_blocked(variant: str, t: Mapping[str, Any]) -> bool:
    if variant == "A_limit_up_proximity_guard":
        return _guard_a(t)
    if variant == "B_pullback_misread_guard":
        return _guard_b(t)
    if variant == "C_combined_entry_guard":
        return _guard_a(t) or _guard_b(t)
    if variant == "D_dynamic40_limit_penalty":
        if str(t.get("universe_slot") or "") != "dynamic":
            return False
        return _guard_a(t)
    return False


def _metrics(trades: list[dict[str, Any]], *, blocked_keys: set[str]) -> dict[str, Any]:
    kept = [t for t in trades if t["trade_key"] not in blocked_keys]
    skipped = [t for t in trades if t["trade_key"] in blocked_keys]
    yens_kept = [float(t["pnl_yen_100"]) for t in kept if t.get("pnl_yen_100") is not None]
    yens_skip = [float(t["pnl_yen_100"]) for t in skipped if t.get("pnl_yen_100") is not None]
    stops_kept = sum(1 for t in kept if t.get("is_stop_hit"))
    stops_skip = sum(1 for t in skipped if t.get("is_stop_hit"))
    dyn_kept = [t for t in kept if t.get("universe_slot") == "dynamic"]
    core_kept = [t for t in kept if t.get("universe_slot") == "core"]
    dyn_yens = [float(t["pnl_yen_100"]) for t in dyn_kept if t.get("pnl_yen_100") is not None]
    core_yens = [float(t["pnl_yen_100"]) for t in core_kept if t.get("pnl_yen_100") is not None]
    return {
        "trade_count": len(kept),
        "shadow_total_pnl_yen_100": round(sum(yens_kept), 2) if yens_kept else 0.0,
        "profit_factor_yen_100": _pf(yens_kept),
        "stop_hit_count": stops_kept,
        "skipped_trade_count": len(skipped),
        "skipped_trade_pnl_actual": round(sum(yens_skip), 2) if yens_skip else 0.0,
        "stop_hit_skipped_count": stops_skip,
        "dynamic40_total_pnl_yen_100": round(sum(dyn_yens), 2) if dyn_yens else 0.0,
        "dynamic40_trade_count": len(dyn_kept),
        "core10_total_pnl_yen_100": round(sum(core_yens), 2) if core_yens else 0.0,
        "core10_trade_count": len(core_kept),
    }


def evaluate_session(session_meta: dict[str, Any]) -> dict[str, Any]:
    _bootstrap()
    sess_dir = Path(session_meta["session_dir"])
    universe = _load_universe(Path(session_meta["universe_path"]))
    accepted: dict[tuple[str, str], dict[str, str]] = {}
    for row in _stream_events_csv(sess_dir / "small_paper_events.csv"):
        if row.get("event_type") == "accepted":
            accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    trades: list[dict[str, Any]] = []
    for row in _stream_events_csv(sess_dir / "small_paper_events.csv"):
        if row.get("event_type") != "observer_exit" or row.get("pnl_pct") in (None, ""):
            continue
        key = (row.get("symbol", ""), row.get("entry_time", ""))
        acc = accepted.get(key, {})
        t = _enrich_entry_features(acc, row, universe)
        t["session_id"] = session_meta["session_id"]
        t["day"] = session_meta["day"]
        t["session_kind"] = session_meta["session_kind"]
        trades.append(t)

    actual_yens = [float(t["pnl_yen_100"]) for t in trades if t.get("pnl_yen_100") is not None]
    actual = {
        "trade_count": len(trades),
        "actual_total_pnl_yen_100": round(sum(actual_yens), 2) if actual_yens else 0.0,
        "profit_factor_yen_100": _pf(actual_yens),
        "stop_hit_count": sum(1 for t in trades if t.get("is_stop_hit")),
    }

    variant_rows: dict[str, dict[str, Any]] = {}
    trade_flags: list[dict[str, Any]] = []
    for variant in VARIANTS:
        blocked = {t["trade_key"] for t in trades if _guard_blocked(variant, t)}
        m = _metrics(trades, blocked_keys=blocked)
        delta = round(m["shadow_total_pnl_yen_100"] - actual["actual_total_pnl_yen_100"], 2)
        variant_rows[variant] = {
            **actual,
            **m,
            "delta_yen": delta,
            "stop_hit_reduction_count": actual["stop_hit_count"] - m["stop_hit_count"],
            "improved_vs_actual": delta > 0,
        }

    for t in trades:
        flags = {v: _guard_blocked(v, t) for v in VARIANTS}
        trade_flags.append({**t, **{f"blocked_{v}": flags[v] for v in VARIANTS}})

    return {
        "session_meta": session_meta,
        "actual": actual,
        "variants": variant_rows,
        "trade_flags": trade_flags,
    }


def _worker_job(job: dict[str, Any]) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        result = evaluate_session(job["session_meta"])
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
    by_variant: dict[str, dict[str, Any]] = {}
    for variant in VARIANTS:
        actual_total = 0.0
        shadow_total = 0.0
        delta_total = 0.0
        trade_count_actual = 0
        trade_count_shadow = 0
        skipped = 0
        skipped_pnl = 0.0
        stops_actual = 0
        stops_shadow = 0
        stop_red = 0
        improved = 0
        worsened = 0
        dyn_shadow = 0.0
        core_shadow = 0.0

        for sr in session_results:
            v = sr["variants"][variant]
            act = sr["actual"]
            actual_total += float(v["actual_total_pnl_yen_100"])
            shadow_total += float(v["shadow_total_pnl_yen_100"])
            delta_total += float(v["delta_yen"])
            trade_count_actual += int(act["trade_count"])
            trade_count_shadow += int(v["trade_count"])
            skipped += int(v["skipped_trade_count"])
            skipped_pnl += float(v["skipped_trade_pnl_actual"])
            stops_actual += int(act["stop_hit_count"])
            stops_shadow += int(v["stop_hit_count"])
            stop_red += int(v["stop_hit_reduction_count"])
            dyn_shadow += float(v["dynamic40_total_pnl_yen_100"])
            core_shadow += float(v["core10_total_pnl_yen_100"])
            if v["delta_yen"] > 0:
                improved += 1
            elif v["delta_yen"] < 0:
                worsened += 1

        by_variant[variant] = {
            "actual_total_pnl_yen_100": round(actual_total, 2),
            "shadow_total_pnl_yen_100": round(shadow_total, 2),
            "delta_yen": round(delta_total, 2),
            "profit_factor_yen_100": _pf_from_sessions(session_results, variant),
            "actual_profit_factor_yen_100": _pf_from_actual(session_results),
            "trade_count_actual": trade_count_actual,
            "trade_count_shadow": trade_count_shadow,
            "skipped_trade_count": skipped,
            "skipped_trade_pnl_actual": round(skipped_pnl, 2),
            "stop_hit_count_actual": stops_actual,
            "stop_hit_count_shadow": stops_shadow,
            "stop_hit_reduction_count": stop_red,
            "improved_session_count": improved,
            "worsened_session_count": worsened,
            "dynamic40_shadow_pnl_yen_100": round(dyn_shadow, 2),
            "core10_shadow_pnl_yen_100": round(core_shadow, 2),
        }

    am_612 = next(
        (
            sr
            for sr in session_results
            if sr["session_meta"]["day"] == "20260612" and sr["session_meta"]["session_kind"] == "am"
        ),
        None,
    )
    am_612_deltas = {}
    if am_612:
        for variant in VARIANTS:
            am_612_deltas[variant] = am_612["variants"][variant]["delta_yen"]

    best = max(VARIANTS, key=lambda v: by_variant[v]["delta_yen"])
    best_row = by_variant[best]
    pass_checks = {
        "total_pnl_improved": best_row["delta_yen"] > 0,
        "pf_improved": (best_row.get("profit_factor_yen_100") or 0)
        > (best_row.get("actual_profit_factor_yen_100") or 0),
        "am_612_improved": (am_612_deltas.get(best) or 0) > 0,
        "skipped_pnl_strongly_negative": best_row["skipped_trade_pnl_actual"] < -50000,
        "improved_ge_worsened": best_row["improved_session_count"] >= best_row["worsened_session_count"],
        "trade_count_not_too_low": best_row["trade_count_shadow"] >= best_row["trade_count_actual"] * 0.5,
    }
    adopt = all(pass_checks.values())

    return {
        "by_variant": by_variant,
        "best_variant": best,
        "am_20260612_delta_by_variant": am_612_deltas,
        "pass_checks": pass_checks,
        "adopt_candidate_ready": adopt,
    }


def _pf_from_actual(session_results: list[dict[str, Any]]) -> Optional[float]:
    yens: list[float] = []
    for sr in session_results:
        for t in sr["trade_flags"]:
            if t.get("pnl_yen_100") is not None:
                yens.append(float(t["pnl_yen_100"]))
    return _pf(yens)


def _pf_from_sessions(session_results: list[dict[str, Any]], variant: str) -> Optional[float]:
    yens: list[float] = []
    for sr in session_results:
        for t in sr["trade_flags"]:
            if _bool(t.get(f"blocked_{variant}")):
                continue
            if t.get("pnl_yen_100") is not None:
                yens.append(float(t["pnl_yen_100"]))
    return _pf(yens)


from typing import Mapping


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase350 ENTRY guard shadow validation")
    parser.add_argument("--recent-days", type=int, default=3)
    parser.add_argument("--parallel", action="store_true", default=False)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-streaming", action="store_false", dest="streaming")
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    parser.add_argument("--worker-temp-dir", type=Path, default=None)
    parser.add_argument("--keep-worker-temp", action="store_true", default=False)
    args = parser.parse_args()

    sessions = _discover_sessions(args.recent_days)
    if not sessions:
        raise SystemExit("no sessions found")

    temp_dir = args.worker_temp_dir or (
        OUT_DIR / f"_phase350_temp_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}"
    )
    temp_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    session_results: list[dict[str, Any]] = []

    if args.parallel and args.max_workers > 1 and len(sessions) > 1:
        jobs = []
        for i, sm in enumerate(sessions):
            jobs.append(
                {
                    "session_meta": sm,
                    "output_path": str(temp_dir / f"worker_{i:03d}.json"),
                }
            )
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
        for i, sm in enumerate(sessions):
            job = {"session_meta": sm, "output_path": str(temp_dir / f"seq_{i:03d}.json")}
            status = _worker_job(job)
            if status.get("ok"):
                session_results.append(
                    json.loads(Path(job["output_path"]).read_text(encoding="utf-8"))
                )

    session_results.sort(key=lambda r: r["session_meta"]["session_id"])
    agg = _aggregate(session_results)

    session_rows = []
    variant_rows = []
    trade_rows = []
    symbol_acc: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {"actual_pnl": 0.0, "skipped_pnl": 0.0, "trade_count": 0}
    )

    for sr in session_results:
        sm = sr["session_meta"]
        for variant in VARIANTS:
            v = sr["variants"][variant]
            row = {
                "session_id": sm["session_id"],
                "day": sm["day"],
                "session_kind": sm["session_kind"],
                "variant": variant,
                **v,
            }
            session_rows.append(row)
            variant_rows.append(
                {
                    "variant": variant,
                    "session_id": sm["session_id"],
                    "delta_yen": v["delta_yen"],
                    "skipped_trade_count": v["skipped_trade_count"],
                    "skipped_trade_pnl_actual": v["skipped_trade_pnl_actual"],
                }
            )
        for t in sr["trade_flags"]:
            trade_rows.append(t)
            sym = str(t["symbol"])
            for variant in VARIANTS:
                if _bool(t.get(f"blocked_{variant}")):
                    k = (variant, sym)
                    symbol_acc[k]["skipped_pnl"] += float(t.get("pnl_yen_100") or 0)
                    symbol_acc[k]["trade_count"] += 1
            symbol_acc[("actual", sym)]["actual_pnl"] += float(t.get("pnl_yen_100") or 0)

    symbol_rows = []
    for (variant, sym), vals in sorted(symbol_acc.items()):
        symbol_rows.append(
            {
                "variant": variant,
                "symbol": sym,
                "trade_count": int(vals.get("trade_count") or 0),
                "skipped_pnl_yen_100": round(vals.get("skipped_pnl", 0.0), 2),
                "actual_pnl_yen_100": round(vals.get("actual_pnl", 0.0), 2),
            }
        )

    summary = {
        "phase": 350,
        "title": "Recent 3-Day ENTRY Guard Shadow Validation",
        "recent_days": args.recent_days,
        "sessions": [s["session_id"] for s in sessions],
        "parallel": bool(args.parallel),
        "max_workers": args.max_workers,
        "streaming": bool(args.streaming),
        "wall_runtime_sec": round(time.monotonic() - t0, 2),
        "variants": list(VARIANTS),
        "aggregate": agg,
        "notes": [
            "Shadow only: blocked ENTRY removed from PnL; no replacement entries.",
            "No production / Discord / EXIT changes.",
        ],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "phase350_recent3_entry_guard_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if session_rows:
        _write_csv(
            OUT_DIR / "phase350_recent3_entry_guard_sessions.csv",
            session_rows,
            sorted({k for r in session_rows for k in r}),
        )
    if trade_rows:
        _write_csv(
            OUT_DIR / "phase350_recent3_entry_guard_trades.csv",
            trade_rows,
            sorted({k for r in trade_rows for k in r}),
        )
    _write_csv(
        OUT_DIR / "phase350_recent3_entry_guard_by_variant.csv",
        [{**agg["by_variant"][v], "variant": v} for v in VARIANTS],
        ["variant"]
        + sorted({k for v in VARIANTS for k in agg["by_variant"][v]}),
    )
    if symbol_rows:
        _write_csv(
            OUT_DIR / "phase350_recent3_entry_guard_by_symbol.csv",
            symbol_rows,
            sorted({k for r in symbol_rows for k in r}),
        )

    if not args.keep_worker_temp:
        shutil.rmtree(temp_dir, ignore_errors=True)

    best = agg["best_variant"]
    br = agg["by_variant"][best]
    print(json.dumps({"best_variant": best, **br, "pass_checks": agg["pass_checks"]}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
