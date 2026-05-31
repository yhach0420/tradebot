#!/usr/bin/env python3
"""
Phase215: PM entry timing attribution review (30-min buckets).

Review only — PM entries from Phase213b D cohort across all sessions.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, time
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase215_pm_entry_timing_attribution_review.json"
PUSH_ROOT = REPO / "kabu_native/data/push_jsonl"
BASE = REPO / "kabu_native/results/small_paper"

JST = ZoneInfo("Asia/Tokyo")
PM_ENTRY_START = time(12, 33)
PM_ENTRY_END = time(15, 18)

TIMING_BUCKETS: tuple[tuple[str, time, time], ...] = (
    ("13:00-13:30", time(13, 0), time(13, 30)),
    ("13:30-14:00", time(13, 30), time(14, 0)),
    ("14:00-14:30", time(14, 0), time(14, 30)),
    ("14:30-15:00", time(14, 30), time(15, 0)),
)

FOCUS_SYMBOLS = ("6203.T", "6659.T", "9348.T", "4888.T")


def _load_phase213c_module() -> Any:
    path = REPO / "kabu_native/scripts/run_phase213c_board_imbalance_cohort_stability_review.py"
    name = "phase213c_loader_p215"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    sys.path[:0] = [str(REPO), str(REPO / "kabu_native" / "src")]
    spec.loader.exec_module(mod)
    return mod


def _parse_ts(ts: str) -> float:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _entry_dt(entry_time: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(entry_time.replace("Z", "+00:00")).astimezone(JST)
    except (TypeError, ValueError):
        return None


def _is_pm_entry(entry_time: str) -> bool:
    dt = _entry_dt(entry_time)
    if dt is None:
        return False
    t = dt.time()
    return PM_ENTRY_START <= t <= PM_ENTRY_END


def _timing_bucket(entry_time: str) -> str:
    dt = _entry_dt(entry_time)
    if dt is None:
        return "unknown"
    t = dt.time()
    if t < time(13, 0):
        return "pm_pre_1300"
    if time(13, 0) <= t < time(13, 30):
        return "13:00-13:30"
    if time(13, 30) <= t < time(14, 0):
        return "13:30-14:00"
    if time(14, 0) <= t < time(14, 30):
        return "14:00-14:30"
    if time(14, 30) <= t <= time(15, 0):
        return "14:30-15:00"
    return "pm_outside_buckets"


def _push_dir_for_day(day_stamp: str) -> Optional[Path]:
    y = f"{day_stamp[:4]}-{day_stamp[4:6]}-{day_stamp[6:8]}"
    p = PUSH_ROOT / y
    return p if p.is_dir() else None


def _push_path(push_dir: Path, symbol: str) -> Path:
    p = push_dir / f"{symbol}.jsonl"
    if p.is_file():
        return p
    return push_dir / f"{symbol.replace('.T', '')}.jsonl"


def _load_price_ticks(push_dir: Path, symbol: str, ts_lo: float, ts_hi: float) -> list[tuple[float, float]]:
    path = _push_path(push_dir, symbol)
    if not path.is_file():
        return []
    out: list[tuple[float, float]] = []
    last_before: Optional[tuple[float, float]] = None
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(str(rec.get("recorded_at") or ""))
            payload = rec.get("payload") or {}
            try:
                px = float(payload.get("CurrentPrice") or 0)
            except (TypeError, ValueError):
                px = 0.0
            if px <= 0:
                continue
            if ts < ts_lo:
                last_before = (ts, px)
                continue
            if ts > ts_hi:
                break
            if last_before is not None:
                out.append(last_before)
                last_before = None
            out.append((ts, px))
    if last_before is not None and not out:
        out.append(last_before)
    out.sort(key=lambda x: x[0])
    return out


def _price_at_offset(
    series: list[tuple[float, float]],
    entry_ts: float,
    entry_px: float,
    offset_sec: float,
    *,
    end_ts: float,
) -> Optional[float]:
    if entry_px <= 0:
        return None
    target = min(entry_ts + offset_sec, end_ts)
    times = [s[0] for s in series]
    i = bisect_right(times, target) - 1
    if i < 0:
        return entry_px
    return series[i][1]


def _return_pct(entry_px: float, px: Optional[float]) -> Optional[float]:
    if px is None or entry_px <= 0:
        return None
    return round((px - entry_px) / entry_px * 100.0, 4)


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _mean(vals: list[float]) -> Optional[float]:
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def _bucket_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "win_rate": None,
            "stop_hit_rate": None,
            "trailing_mfe_exit_rate": None,
            "avg_r30_sec_pct": None,
            "avg_r60_sec_pct": None,
            "r30_coverage": 0.0,
            "r60_coverage": 0.0,
        }
    pnls = [float(r["pnl_pct"]) for r in rows]
    pf = _pf(pnls)
    stop = sum(1 for r in rows if r.get("stop_hit"))
    trail = sum(1 for r in rows if r.get("trailing_mfe_exit"))
    r30s = [float(r["r30_sec"]) for r in rows if r.get("r30_sec") is not None]
    r60s = [float(r["r60_sec"]) for r in rows if r.get("r60_sec") is not None]
    n = len(rows)
    return {
        "trade_count": n,
        "profit_factor": pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 4),
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4),
        "stop_hit_rate": round(stop / n, 4),
        "trailing_mfe_exit_rate": round(trail / n, 4),
        "avg_r30_sec_pct": _mean(r30s),
        "avg_r60_sec_pct": _mean(r60s),
        "r30_coverage": round(len(r30s) / n, 4),
        "r60_coverage": round(len(r60s) / n, 4),
    }


def _entry_px_map(session_rel: str, mod: Any) -> dict[tuple[str, str], float]:
    sdir = BASE / session_rel
    out: dict[tuple[str, str], float] = {}
    csv_path = sdir / "structural_trades.csv"
    if csv_path.is_file():
        for row in mod.load_structural_trades(csv_path):
            sym = str(row.get("symbol") or "")
            ent = str(row.get("entry_time") or "")
            px = mod._float(row.get("entry_price"))
            if sym and ent and px and px > 0:
                out[(sym, ent)] = float(px)
    for ev in mod._load_events(sdir):
        if ev.get("event_type") != "accepted":
            continue
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or "")
        if not sym or not ent or (sym, ent) in out:
            continue
        px = mod._float(ev.get("current_price")) or mod._float(ev.get("entry_price"))
        if px and px > 0:
            out[(sym, ent)] = float(px)
    return out


def _build_pm_cohort(mod: Any) -> list[dict[str, Any]]:
    p71 = mod._load_phase71()
    book_cache: dict[tuple[str, str], list[Any]] = {}
    tick_cache: dict[tuple[str, str], list[tuple[float, float]]] = {}
    px_cache: dict[str, dict[tuple[str, str], float]] = {}
    pm_rows: list[dict[str, Any]] = []

    for session_rel in mod.ALL_SESSIONS:
        px_cache[session_rel] = _entry_px_map(session_rel, mod)
        trades, _ = mod._load_session_trades(session_rel, p71)
        if not trades:
            continue
        enriched = mod._enrich_trades(session_rel, trades, book_cache)
        seen: set[tuple[str, str]] = set()
        for r in enriched:
            if not r.get("in_phase213b_D_cohort"):
                continue
            key = (str(r.get("symbol") or ""), str(r.get("entry_time") or ""))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            if not _is_pm_entry(key[1]):
                continue
            sym, ent = key
            entry_ts = _parse_ts(ent)
            day = str(r.get("day_stamp") or "")
            push_dir = mod._push_dir_for_day(day) or mod._push_dir(session_rel)
            tick_key = (day, sym)
            if push_dir and tick_key not in tick_cache:
                tick_cache[tick_key] = _load_price_ticks(
                    push_dir, sym, entry_ts - 120.0, entry_ts + 120.0
                )
            ticks = tick_cache.get(tick_key, [])
            entry_px = px_cache[session_rel].get(key) or 0.0
            if entry_px <= 0 and ticks:
                i = bisect_right([t[0] for t in ticks], entry_ts) - 1
                if i >= 0:
                    entry_px = ticks[i][1]
            end_ts = entry_ts + 3600.0
            reason = str(r.get("exit_reason") or "")
            row = {
                **r,
                "entry_price": entry_px,
                "timing_bucket": _timing_bucket(ent),
                "stop_hit": reason == "stop_hit",
                "trailing_mfe_exit": reason == "trailing_mfe_exit",
                "r30_sec": _return_pct(
                    entry_px, _price_at_offset(ticks, entry_ts, entry_px, 30.0, end_ts=end_ts)
                ),
                "r60_sec": _return_pct(
                    entry_px, _price_at_offset(ticks, entry_ts, entry_px, 60.0, end_ts=end_ts)
                ),
            }
            pm_rows.append(row)
    return pm_rows


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    mod = _load_phase213c_module()
    pm_rows = _build_pm_cohort(mod)

    bucket_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in pm_rows:
        bucket_rows[str(r.get("timing_bucket") or "unknown")].append(r)

    main_buckets = [label for label, _, _ in TIMING_BUCKETS]
    in_scope = [r for r in pm_rows if r.get("timing_bucket") in main_buckets]

    bucket_pf: dict[str, Any] = {}
    for label in main_buckets:
        bucket_pf[label] = _bucket_metrics(bucket_rows.get(label, []))

    focus: dict[str, Any] = {}
    for sym in FOCUS_SYMBOLS:
        sym_rows = [r for r in in_scope if r.get("symbol") == sym]
        by_bucket = {
            label: _bucket_metrics([r for r in sym_rows if r.get("timing_bucket") == label])
            for label in main_buckets
        }
        focus[sym] = {
            "pm_in_scope_trade_count": len(sym_rows),
            "overall": _bucket_metrics(sym_rows),
            "by_timing_bucket": by_bucket,
        }

    pf_rank = sorted(
        [(label, bucket_pf[label]["profit_factor"]) for label in main_buckets],
        key=lambda x: (x[1] is None, -(x[1] or -1)),
    )

    report = {
        "phase": 215,
        "mode": "pm_entry_timing_attribution_review",
        "constraints": {
            "review_only": True,
            "hard_reject_forbidden": True,
            "production_yaml_changes_forbidden": True,
        },
        "cohort": {
            "definition": "Phase213b D (low_liq + vwap + imbalance top 20%)",
            "pm_filter": "entry_time JST 12:33-15:18 (AmPmSessionPolicy afternoon window)",
            "timing_buckets_in_scope": list(main_buckets),
            "pm_trade_count_all": len(pm_rows),
            "pm_trade_count_in_timing_buckets": len(in_scope),
            "pm_pre_1300_count": len(bucket_rows.get("pm_pre_1300", [])),
            "pm_outside_buckets_count": len(bucket_rows.get("pm_outside_buckets", [])),
        },
        "pm_overall_in_buckets": _bucket_metrics(in_scope),
        "timing_bucket_comparison": bucket_pf,
        "pf_rank_best_to_worst": [
            {"bucket": b, "profit_factor": pf} for b, pf in pf_rank
        ],
        "focus_symbols": focus,
        "excluded_buckets": {
            "pm_pre_1300": _bucket_metrics(bucket_rows.get("pm_pre_1300", [])),
            "pm_outside_buckets": _bucket_metrics(bucket_rows.get("pm_outside_buckets", [])),
        },
        "notes": [
            "30-min buckets apply to PM entries 13:00-15:00 JST.",
            "12:33-13:00 PM entries reported under excluded_buckets.pm_pre_1300.",
            "r30/r60 from push_jsonl price at entry+30s/60s.",
            "stop_hit / trailing_mfe from structural exit_reason.",
        ],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} pm_n={len(in_scope)} buckets={ {k: bucket_pf[k]['trade_count'] for k in main_buckets} }")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
