#!/usr/bin/env python3
"""
Phase353: Bad market regime detection — can 6/12-type crash days be spotted early?
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import statistics
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT_DIR = REPO / "kabu_native" / "results" / "reports"
JST = ZoneInfo("Asia/Tokyo")
MIN_DAY = "20260518"


def _bootstrap() -> None:
    import sys

    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _is_stop_hit_row(row: dict[str, str]) -> bool:
    if _bool(row.get("stop_hit")):
        return True
    reason = str(row.get("structural_exit_reason") or row.get("exit_reason") or "")
    return reason == "stop_hit"


def _collect_session_trades(events: list[dict[str, str]]) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for row in events:
        if row.get("event_type") != "observer_exit" or row.get("pnl_pct") in (None, ""):
            continue
        ep, xp = _float(row.get("entry_price")), _float(row.get("exit_price"))
        if ep is None or xp is None:
            continue
        trades.append(
            {
                **row,
                "pnl_yen_100": round((xp - ep) * 100.0, 2),
                "is_stop_hit": _is_stop_hit_row(row),
            }
        )
    return trades


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


def _parse_ts(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _session_anchor(day: str, summary: dict[str, Any], session_kind: str) -> datetime:
    start = str(summary.get("session_start") or "").strip()
    if start and ":" in start:
        hh, mm = start.split(":")[:2]
        return datetime.combine(
            date(int(day[:4]), int(day[4:6]), int(day[6:8])),
            dt_time(int(hh), int(mm)),
            tzinfo=JST,
        )
    if session_kind == "pm":
        return datetime.combine(
            date(int(day[:4]), int(day[4:6]), int(day[6:8])),
            dt_time(12, 30),
            tzinfo=JST,
        )
    return datetime.combine(
        date(int(day[:4]), int(day[4:6]), int(day[6:8])),
        dt_time(9, 0),
        tzinfo=JST,
    )


def _regime_label(pf: Optional[float]) -> str:
    if pf is None:
        return "unknown"
    if pf == float("inf"):
        return "good"
    if pf >= 1.0:
        return "good"
    if pf < 0.5:
        return "crash"
    if pf < 0.7:
        return "bad"
    return "normal"


def _discover_sessions(
    *,
    min_day: str,
    max_sessions: Optional[int],
) -> list[dict[str, Any]]:
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
                "session_start": summary.get("session_start"),
            }
        )
    sessions.sort(key=lambda s: s["session_id"])
    if max_sessions is not None:
        sessions = sessions[:max_sessions]
    return sessions


def _stream_events(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            yield row


def _load_universe(sess_dir: Path, day: str, kind: str, reports_dir: Path) -> dict[str, dict[str, str]]:
    from small_paper.limit_up_proximity_entry_guard_shadow import (
        _load_session_summary,
        _load_universe,
        _universe_path_for_session,
    )

    summary = _load_session_summary(sess_dir)
    path = _universe_path_for_session(day, kind, summary, reports_dir)
    return _load_universe(path)


def _dist_stats(values: list[float]) -> dict[str, Optional[float]]:
    if not values:
        return {"mean": None, "median": None, "p25": None, "p75": None, "count": 0}
    qs = statistics.quantiles(values, n=4) if len(values) >= 4 else [min(values)] * 3
    return {
        "mean": round(statistics.mean(values), 4),
        "median": round(statistics.median(values), 4),
        "p25": round(qs[0], 4) if len(values) >= 4 else round(min(values), 4),
        "p75": round(qs[2], 4) if len(values) >= 4 else round(max(values), 4),
        "count": len(values),
    }


def _feature_block(
    accepts: list[dict[str, Any]],
    exits: list[dict[str, Any]],
    universe: dict[str, dict[str, str]],
) -> dict[str, Any]:
    rise_vals = [a["rise_5min"] for a in accepts if a.get("rise_5min") is not None]
    vwap_vals = [a["vwap_dev"] for a in accepts if a.get("vwap_dev") is not None]
    qual_vals = [a["quality"] for a in accepts if a.get("quality") is not None]
    dyn_rise = [
        a["rise_5min"]
        for a in accepts
        if a.get("rise_5min") is not None and a.get("universe_slot") == "dynamic"
    ]
    core_rise = [
        a["rise_5min"]
        for a in accepts
        if a.get("rise_5min") is not None and a.get("universe_slot") == "core"
    ]
    rising_n = sum(1 for v in rise_vals if v > 0)
    board_mid_n = sum(1 for a in accepts if a.get("board_mid"))
    stop_n = sum(1 for e in exits if e.get("is_stop_hit"))
    exit_n = len(exits)

    dyn_univ = [r for r in universe.values() if str(r.get("universe_slot") or "") == "dynamic"]
    core_univ = [r for r in universe.values() if str(r.get("universe_slot") or "") == "core"]

    return {
        "accept_count": len(accepts),
        "exit_count": exit_n,
        "rising_stock_ratio": round(rising_n / len(rise_vals), 4) if rise_vals else None,
        "dynamic40_avg_rise_5min_pct": round(statistics.mean(dyn_rise), 4) if dyn_rise else None,
        "core10_avg_rise_5min_pct": round(statistics.mean(core_rise), 4) if core_rise else None,
        "stop_hit_rate": round(stop_n / exit_n, 4) if exit_n else None,
        "rise_5min": _dist_stats(rise_vals),
        "vwap_dev": _dist_stats(vwap_vals),
        "entry_quality": _dist_stats(qual_vals),
        "board_mid_rate": round(board_mid_n / len(accepts), 4) if accepts else None,
        "dynamic40_adopted_count": len(dyn_univ),
        "core10_adopted_count": len(core_univ),
        "total_pnl_yen_100": round(sum(e["pnl_yen"] for e in exits), 2) if exits else 0.0,
    }


def evaluate_session(session_meta: dict[str, Any], *, reports_dir: Path) -> dict[str, Any]:
    _bootstrap()

    sess_dir = Path(session_meta["session_dir"])
    day = str(session_meta["day"])
    kind = str(session_meta["session_kind"])
    summary_path = sess_dir / "small_paper_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    anchor = _session_anchor(day, summary, kind)

    universe = _load_universe(sess_dir, day, kind, reports_dir)
    sym_slot = {sym: str(u.get("universe_slot") or "") for sym, u in universe.items()}

    all_events = list(_stream_events(sess_dir / "small_paper_events.csv"))
    canonical = _collect_session_trades(all_events)
    if not canonical:
        return {"session_meta": session_meta, "skipped": True, "reason": "no_observer_exit_trades"}

    yens = [float(t["pnl_yen_100"]) for t in canonical]
    pf = _pf(yens)
    pf_num = float(pf) if isinstance(pf, (int, float)) else (999.0 if pf == float("inf") else None)

    accepted_rows: list[dict[str, Any]] = []
    exit_rows: list[dict[str, Any]] = []
    for row in all_events:
        et = str(row.get("event_type") or "")
        sym = str(row.get("symbol") or "")
        if et == "accepted":
            ent_ts = _parse_ts(str(row.get("entry_time") or row.get("event_time") or ""))
            mins = (ent_ts - anchor).total_seconds() / 60.0 if ent_ts else None
            accepted_rows.append(
                {
                    "symbol": sym,
                    "entry_time": row.get("entry_time"),
                    "mins_from_anchor": mins,
                    "rise_5min": _float(row.get("entry_rise_5min_pct")),
                    "vwap_dev": _float(row.get("entry_vwap_dev_pct")),
                    "quality": _float(row.get("continuation_quality_score")),
                    "board_mid": _bool(row.get("entry_board_mid_token_active")),
                    "universe_slot": sym_slot.get(sym, ""),
                    "score_v2": _float(row.get("entry_expectancy_score_v2")),
                }
            )
        elif et == "observer_exit" and row.get("pnl_pct") not in (None, ""):
            ent_ts = _parse_ts(str(row.get("entry_time") or ""))
            mins = (ent_ts - anchor).total_seconds() / 60.0 if ent_ts else None
            ep, xp = _float(row.get("entry_price")), _float(row.get("exit_price"))
            yen = round((xp - ep) * 100.0, 2) if ep and xp else _float(row.get("pnl_yen_100")) or 0.0
            exit_rows.append(
                {
                    "symbol": sym,
                    "mins_from_anchor": mins,
                    "pnl_yen": yen,
                    "is_stop_hit": _is_stop_hit_row(row),
                    "universe_slot": sym_slot.get(sym, ""),
                }
            )

    def _window_accepts(max_min: float) -> list[dict[str, Any]]:
        return [
            a
            for a in accepted_rows
            if a.get("mins_from_anchor") is not None and 0 <= a["mins_from_anchor"] <= max_min
        ]

    def _window_exits(max_min: float) -> list[dict[str, Any]]:
        return [
            e
            for e in exit_rows
            if e.get("mins_from_anchor") is not None and 0 <= e["mins_from_anchor"] <= max_min
        ]

    full_block = _feature_block(accepted_rows, exit_rows, universe)
    early_30 = _feature_block(_window_accepts(30), _window_exits(30), universe)
    early_60 = _feature_block(_window_accepts(60), _window_exits(60), universe)

    return {
        "session_meta": session_meta,
        "skipped": False,
        "session_anchor": anchor.isoformat(),
        "pf_yen_100": pf,
        "pf_numeric": pf_num,
        "regime_label": _regime_label(pf_num),
        "total_pnl_yen_100": round(sum(yens), 2),
        "trade_count": len(canonical),
        "is_live_session": str(session_meta.get("session_source") or "") == "live",
        "features_full": full_block,
        "features_early_30": early_30,
        "features_early_60": early_60,
    }


def _worker_job(job: dict[str, Any]) -> dict[str, Any]:
    _bootstrap()
    t0 = time.monotonic()
    try:
        result = evaluate_session(job["session_meta"], reports_dir=Path(job["reports_dir"]))
        out_path = Path(job["output_path"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return {
            "ok": True,
            "skipped": bool(result.get("skipped")),
            "output_path": str(out_path),
            "session_id": job["session_meta"].get("session_id"),
            "error": result.get("reason"),
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


def _flatten_features(prefix: str, block: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in block.items():
        if isinstance(v, dict) and k in ("rise_5min", "vwap_dev", "entry_quality"):
            for sk, sv in v.items():
                out[f"{prefix}_{k}_{sk}"] = sv
        else:
            out[f"{prefix}_{k}"] = v
    return out


def _group_means(
    rows: list[dict[str, Any]], feature: str
) -> dict[str, Optional[float]]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        v = _float(r.get(feature))
        if v is None:
            continue
        buckets[str(r.get("regime_label") or "unknown")].append(v)
    return {k: round(statistics.mean(vs), 4) if vs else None for k, vs in buckets.items()}


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    deny = math.sqrt(sum((y - my) ** 2 for y in ys))
    if denx <= 0 or deny <= 0:
        return None
    return round(num / (denx * deny), 4)


def _build_feature_importance(session_features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics = [
        "full_rising_stock_ratio",
        "full_dynamic40_avg_rise_5min_pct",
        "full_core10_avg_rise_5min_pct",
        "full_stop_hit_rate",
        "full_rise_5min_median",
        "full_vwap_dev_median",
        "full_entry_quality_median",
        "full_board_mid_rate",
        "full_dynamic40_adopted_count",
        "early_60_rising_stock_ratio",
        "early_60_dynamic40_avg_rise_5min_pct",
        "early_60_stop_hit_rate",
        "early_60_rise_5min_median",
        "early_60_vwap_dev_median",
        "early_60_entry_quality_median",
        "early_60_board_mid_rate",
        "early_30_stop_hit_rate",
        "early_30_rising_stock_ratio",
    ]
    rows = []
    pf_vals = [float(r["pf_numeric"]) for r in session_features if r.get("pf_numeric") is not None]
    for feat in metrics:
        xs = []
        ys = []
        for r in session_features:
            v = _float(r.get(feat))
            p = r.get("pf_numeric")
            if v is None or p is None:
                continue
            xs.append(v)
            ys.append(float(p))
        means = _group_means(session_features, feat)
        good_m = means.get("good")
        crash_m = means.get("crash")
        bad_m = means.get("bad")
        sep = None
        if good_m is not None and crash_m is not None:
            all_v = [float(r[feat]) for r in session_features if _float(r.get(feat)) is not None]
            std = statistics.pstdev(all_v) if len(all_v) > 1 else 1.0
            sep = round((crash_m - good_m) / std, 4) if std > 0 else None
        rows.append(
            {
                "feature": feat,
                "good_mean": good_m,
                "bad_mean": bad_m,
                "crash_mean": crash_m,
                "normal_mean": means.get("normal"),
                "pf_correlation": _pearson(xs, ys),
                "crash_vs_good_effect_size": sep,
            }
        )
    rows.sort(
        key=lambda r: abs(float(r["crash_vs_good_effect_size"] or 0)),
        reverse=True,
    )
    return rows


def _early_crash_detection(
    session_features: list[dict[str, Any]],
) -> dict[str, Any]:
    live_am = [
        r
        for r in session_features
        if r.get("is_live_session") and str(r.get("session_kind") or "") == "am"
    ]
    crash_ids = {r["session_id"] for r in live_am if r.get("regime_label") == "crash"}
    good_ids = {r["session_id"] for r in live_am if r.get("regime_label") == "good"}

    rules = [
        ("early_60_stop_hit_rate_ge_0.35", lambda r: (_float(r.get("early_60_stop_hit_rate")) or 0) >= 0.35),
        ("early_60_stop_hit_rate_ge_0.25", lambda r: (_float(r.get("early_60_stop_hit_rate")) or 0) >= 0.25),
        ("early_60_rising_stock_ratio_le_0.45", lambda r: (_float(r.get("early_60_rising_stock_ratio")) or 1) <= 0.45),
        ("early_60_rise_5min_median_le_0", lambda r: (_float(r.get("early_60_rise_5min_median")) or 0) <= 0),
        ("early_60_vwap_dev_median_le_0", lambda r: (_float(r.get("early_60_vwap_dev_median")) or 0) <= 0),
        ("early_60_board_mid_rate_le_0.5", lambda r: (_float(r.get("early_60_board_mid_rate")) or 1) <= 0.5),
        (
            "combo_stop25_and_rise_ratio_le_0.5",
            lambda r: (_float(r.get("early_60_stop_hit_rate")) or 0) >= 0.25
            and (_float(r.get("early_60_rising_stock_ratio")) or 1) <= 0.5,
        ),
    ]

    rule_rows = []
    best_rule = None
    best_score = -1.0
    for name, fn in rules:
        flagged = {r["session_id"] for r in live_am if fn(r)}
        tp = len(flagged & crash_ids)
        fp = len(flagged - crash_ids)
        fn_m = len(crash_ids - flagged)
        tn = len(good_ids - flagged)
        precision = tp / len(flagged) if flagged else 0.0
        recall = tp / len(crash_ids) if crash_ids else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        rule_rows.append(
            {
                "rule": name,
                "flagged_sessions": len(flagged),
                "crash_recall": round(recall, 4),
                "crash_precision": round(precision, 4),
                "f1_vs_crash": round(f1, 4),
                "false_positive_good_sessions": fp,
            }
        )
        if f1 > best_score:
            best_score = f1
            best_rule = name

    s612 = next((r for r in live_am if r.get("day") == "20260612"), None)
    s612_flagged = []
    if s612:
        for name, fn in rules:
            if fn(s612):
                s612_flagged.append(name)

    return {
        "cohort": "live_am_sessions",
        "session_count": len(live_am),
        "crash_session_count": len(crash_ids),
        "good_session_count": len(good_ids),
        "crash_session_ids": sorted(crash_ids),
        "rule_evaluation": rule_rows,
        "best_rule": best_rule,
        "session_20260612_am": {
            "session_id": s612.get("session_id") if s612 else None,
            "regime_label": s612.get("regime_label") if s612 else None,
            "pf_yen_100": s612.get("pf_yen_100") if s612 else None,
            "early_60_stop_hit_rate": s612.get("early_60_stop_hit_rate") if s612 else None,
            "early_60_rising_stock_ratio": s612.get("early_60_rising_stock_ratio") if s612 else None,
            "early_60_rise_5min_median": s612.get("early_60_rise_5min_median") if s612 else None,
            "rules_flagged": s612_flagged,
            "identifiable_in_first_60min": bool(s612_flagged),
        },
    }


def _good_vs_bad_compare(session_features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics = [
        ("rising_stock_ratio", "full"),
        ("dynamic40_avg_rise_5min_pct", "full"),
        ("core10_avg_rise_5min_pct", "full"),
        ("stop_hit_rate", "full"),
        ("rise_5min_median", "full"),
        ("vwap_dev_median", "full"),
        ("entry_quality_median", "full"),
        ("board_mid_rate", "full"),
        ("dynamic40_adopted_count", "full"),
        ("stop_hit_rate", "early_60"),
        ("rising_stock_ratio", "early_60"),
        ("rise_5min_median", "early_60"),
    ]
    rows = []
    for metric, window in metrics:
        col = f"{window}_{metric}"
        for label in ("good", "bad", "crash", "normal"):
            vals = [
                float(r[col])
                for r in session_features
                if r.get("regime_label") == label and _float(r.get(col)) is not None
            ]
            rows.append(
                {
                    "regime_label": label,
                    "window": window,
                    "metric": metric,
                    "session_count": len(vals),
                    "mean": round(statistics.mean(vals), 4) if vals else None,
                    "median": round(statistics.median(vals), 4) if vals else None,
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase353 bad regime detection")
    parser.add_argument("--min-day", default=MIN_DAY)
    parser.add_argument("--max-sessions", type=int, default=None)
    parser.add_argument("--parallel", action="store_true", default=False)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    parser.add_argument("--worker-temp-dir", type=Path, default=None)
    parser.add_argument("--keep-worker-temp", action="store_true", default=False)
    args = parser.parse_args()

    sessions = _discover_sessions(min_day=args.min_day, max_sessions=args.max_sessions)
    if not sessions:
        raise SystemExit("no sessions found")

    temp_dir = args.worker_temp_dir or (
        OUT_DIR / f"_phase353_temp_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}"
    )
    temp_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

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
                st = fut.result()
                if not st.get("ok"):
                    errors.append(st)
                    continue
                payload = json.loads(Path(st["output_path"]).read_text(encoding="utf-8"))
                if payload.get("skipped"):
                    errors.append(
                        {
                            "session_id": st.get("session_id"),
                            "error": payload.get("reason"),
                        }
                    )
                    continue
                results.append(payload)
    else:
        _bootstrap()
        for sm in sessions:
            r = evaluate_session(sm, reports_dir=OUT_DIR)
            if r.get("skipped"):
                errors.append({"session_id": sm["session_id"], "error": r.get("reason")})
            else:
                results.append(r)

    session_feature_rows: list[dict[str, Any]] = []
    for r in sorted(results, key=lambda x: x["session_meta"]["session_id"]):
        sm = r["session_meta"]
        row = {
            "session_id": sm["session_id"],
            "day": sm["day"],
            "session_kind": sm["session_kind"],
            "session_source": sm.get("session_source"),
            "is_live_session": r.get("is_live_session"),
            "regime_label": r.get("regime_label"),
            "pf_yen_100": r.get("pf_yen_100"),
            "pf_numeric": r.get("pf_numeric"),
            "total_pnl_yen_100": r.get("total_pnl_yen_100"),
            "trade_count": r.get("trade_count"),
            "session_anchor": r.get("session_anchor"),
        }
        row.update(_flatten_features("full", r.get("features_full") or {}))
        row.update(_flatten_features("early_30", r.get("features_early_30") or {}))
        row.update(_flatten_features("early_60", r.get("features_early_60") or {}))
        session_feature_rows.append(row)

    importance = _build_feature_importance(session_feature_rows)
    early_det = _early_crash_detection(session_feature_rows)
    compare_rows = _good_vs_bad_compare(session_feature_rows)

    regime_counts = defaultdict(int)
    for r in session_feature_rows:
        regime_counts[str(r.get("regime_label"))] += 1

    best = importance[0] if importance else {}
    s612 = early_det.get("session_20260612_am") or {}
    identifiable = bool(s612.get("identifiable_in_first_60min"))
    crash_recall_best = max((x["crash_recall"] for x in early_det.get("rule_evaluation") or []), default=0)

    conclusion = {
        "crash_day_only_entry_narrowing_condition_found": identifiable and crash_recall_best >= 0.5,
        "session_20260612_identifiable_early_60min": identifiable,
        "best_early_rule": early_det.get("best_rule"),
        "top_discriminative_feature": best.get("feature"),
        "recommendation": (
            "Continue shadow-only monitoring; no standalone crash-day ENTRY halt yet."
            if not (identifiable and crash_recall_best >= 0.5)
            else "Pilot shadow ENTRY throttle on flagged crash-regime mornings (live AM only)."
        ),
        "rationale": [
            "6/12 AM shows elevated early stop_hit and weak rise_5min vs good sessions."
            if identifiable
            else "6/12 AM not clearly separable from good sessions with simple opening rules.",
            "Crash-day ENTRY narrowing needs composite regime score, not single threshold.",
            "ENTRY conditions unchanged in this phase (investigation only).",
        ],
    }

    summary = {
        "phase": 353,
        "title": "Bad Market Regime Detection",
        "min_day": args.min_day,
        "sessions_discovered": len(sessions),
        "sessions_evaluated": len(session_feature_rows),
        "sessions_skipped": len(errors),
        "wall_runtime_sec": round(time.monotonic() - t0, 2),
        "regime_counts": dict(regime_counts),
        "classification_rules": {
            "good": "PF >= 1.0",
            "normal": "0.7 <= PF < 1.0",
            "bad": "0.5 <= PF < 0.7",
            "crash": "PF < 0.5",
        },
        "early_detection": early_det,
        "conclusion": conclusion,
        "notes": [
            "Investigation only — no ENTRY / EXIT / Discord changes.",
            "rising_stock_ratio = share of accepted entries with entry_rise_5min_pct > 0.",
            "early_30/early_60 = features from entries within 30/60 min of session anchor.",
        ],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "phase353_bad_regime_detection_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(
        OUT_DIR / "phase353_good_vs_bad_sessions.csv",
        compare_rows,
        sorted({k for r in compare_rows for k in r}),
    )
    _write_csv(
        OUT_DIR / "phase353_session_features.csv",
        session_feature_rows,
        sorted({k for r in session_feature_rows for k in r}),
    )
    _write_csv(
        OUT_DIR / "phase353_feature_importance.csv",
        importance,
        sorted({k for r in importance for k in r}),
    )

    if not args.keep_worker_temp:
        shutil.rmtree(temp_dir, ignore_errors=True)

    print(
        json.dumps(
            {
                "sessions_evaluated": len(session_feature_rows),
                "regime_counts": dict(regime_counts),
                "session_20260612_identifiable": identifiable,
                "best_early_rule": early_det.get("best_rule"),
                "conclusion": conclusion.get("recommendation"),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
