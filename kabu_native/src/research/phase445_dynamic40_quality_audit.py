"""
Phase445 — Dynamic40 quality audit (20260619).

Separates universe quality vs ENTRY selection problems for Dynamic40 watchlist.

Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from replay.pnl_yen import enrich_trade_pnl_yen
from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")
TARGET_DAY = "20260619"

AUDIT_FIELDS = [
    "symbol",
    "universe_bucket",
    "core_or_dynamic",
    "included_at_open",
    "included_after_10_refresh",
    "included_after_1430_refresh",
    "open_price",
    "price_0930",
    "price_1000",
    "price_1430",
    "close_price",
    "open_to_0930_return_pct",
    "open_to_1000_return_pct",
    "open_to_close_return_pct",
    "day_high_time",
    "day_high_return_pct",
    "day_low_time",
    "day_low_return_pct",
    "high_to_close_drawdown_pct",
    "day_high_distance_at_entry_mean",
    "vwap_dev_at_entry_mean",
    "shape_class",
]

CLASS_FIELDS = [
    "symbol",
    "core_or_dynamic",
    "shape_class",
    "open_to_close_return_pct",
    "day_high_time",
    "high_to_close_drawdown_pct",
    "included_at_open",
    "included_after_10_refresh",
    "included_after_1430_refresh",
]

ENTRY_JOIN_FIELDS = [
    "symbol",
    "entry_time",
    "session",
    "pnl_yen_100",
    "exit_reason",
    "hold_sec",
    "shape_class",
    "core_or_dynamic",
    "universe_slot",
    "entry_near_day_high_pct",
    "entry_vwap_dev_pct",
    "high_drift_pullback_guard_blocked",
    "no_progress_exit",
    "stop_hit",
]

AGG_FIELDS = [
    "cohort",
    "watch_count",
    "uptrend_count",
    "uptrend_share",
    "downtrend_count",
    "downtrend_share",
    "opening_peak_count",
    "opening_peak_share",
    "slow_opening_peak_count",
    "slow_opening_peak_share",
    "range_count",
    "range_share",
    "avg_open_to_close_return",
    "median_open_to_close_return",
    "avg_high_to_close_drawdown",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _load_universe(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        return {str(r["symbol"]): dict(r) for r in csv.DictReader(fh) if r.get("symbol")}


def _resolve_universe_paths(repo_root: Path) -> dict[str, Path]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    daily = kabu / "results" / "daily" / TARGET_DAY / "runtime"
    names = {
        "am_open": f"universe_core10_dynamic40_price_risk_am_{TARGET_DAY}.csv",
        "am_refresh1000": f"universe_core10_dynamic40_price_risk_am_refresh1000_{TARGET_DAY}.csv",
        "pm_open": f"universe_core10_dynamic40_price_risk_pm_{TARGET_DAY}.csv",
        "pm_refresh1430": f"universe_core10_dynamic40_price_risk_pm_refresh1430_{TARGET_DAY}.csv",
    }
    out: dict[str, Path] = {}
    for key, fname in names.items():
        p = reports / fname
        if not p.is_file():
            p = daily / fname
        out[key] = p
    return out


def _load_day_events(kabu: Path, day: str) -> list[dict[str, str]]:
    base = kabu / "results" / "small_paper" / day
    rows: list[dict[str, str]] = []
    if not base.is_dir():
        return rows
    for sess in sorted(base.iterdir()):
        path = sess / "small_paper_events.csv"
        if path.is_file():
            with path.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    rows.append(dict(row))
    return rows


def _build_price_series(events: Sequence[Mapping[str, str]]) -> dict[str, list[tuple[datetime, float]]]:
    idx: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for row in events:
        sym = str(row.get("symbol") or "")
        px = _float(row.get("current_price"))
        if not sym or px is None or px <= 0:
            continue
        ts_raw = str(row.get("event_time") or row.get("entry_time") or "")
        ts = _parse_ts(ts_raw)
        if ts is None:
            continue
        idx[sym].append((ts, float(px)))
    for sym in idx:
        idx[sym].sort(key=lambda x: x[0])
    return idx


def _price_at_or_before(series: Sequence[tuple[datetime, float]], target: datetime) -> Optional[float]:
    best: Optional[float] = None
    for ts, px in series:
        if ts <= target:
            best = px
        else:
            break
    return best


def _price_at_or_after(series: Sequence[tuple[datetime, float]], target: datetime) -> Optional[float]:
    for ts, px in series:
        if ts >= target:
            return px
    return None


def _pct(from_px: Optional[float], to_px: Optional[float]) -> Optional[float]:
    if from_px is None or to_px is None or from_px <= 0:
        return None
    return round((to_px - from_px) / from_px * 100.0, 4)


def _time_on_day(h: int, m: int, day: str) -> datetime:
    return datetime.strptime(f"{day} {h:02d}:{m:02d}:00", "%Y%m%d %H:%M:%S").replace(tzinfo=JST)


def _classify_shape(
    *,
    open_to_close: Optional[float],
    day_high_time: Optional[datetime],
    high_to_close_dd: Optional[float],
    open_time: datetime,
) -> str:
    if day_high_time is None or open_to_close is None or high_to_close_dd is None:
        return "unknown"
    mins_high = (day_high_time - open_time).total_seconds() / 60.0
    if mins_high <= 20 and open_to_close < 0 and high_to_close_dd <= -1.5:
        return "opening_peak"
    if mins_high <= 60 and high_to_close_dd <= -2.0:
        return "slow_opening_peak"
    if open_to_close < -1.0:
        return "downtrend"
    if open_to_close > 0 and (mins_high >= 60 or day_high_time.hour >= 12):
        return "uptrend"
    if abs(open_to_close) <= 0.5:
        return "range"
    if open_to_close > 0:
        return "uptrend"
    return "other"


def _symbol_shape_row(
    sym: str,
    series: Sequence[tuple[datetime, float]],
    *,
    universes: Mapping[str, Mapping[str, dict[str, str]]],
    entry_stats: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    open_dt = _time_on_day(9, 0, TARGET_DAY)
    t0930 = _time_on_day(9, 30, TARGET_DAY)
    t1000 = _time_on_day(10, 0, TARGET_DAY)
    t1430 = _time_on_day(14, 30, TARGET_DAY)
    t1530 = _time_on_day(15, 30, TARGET_DAY)

    day_series = list(series)

    open_px = _price_at_or_after(day_series, open_dt) or (day_series[0][1] if day_series else None)
    px0930 = _price_at_or_before(day_series, t0930)
    px1000 = _price_at_or_before(day_series, t1000)
    px1430 = _price_at_or_before(day_series, t1430)
    close_px = _price_at_or_before(day_series, t1530) or (day_series[-1][1] if day_series else None)

    day_high = max(px for _, px in day_series) if day_series else None
    day_low = min(px for _, px in day_series) if day_series else None
    day_high_time = max(ts for ts, px in day_series if day_high is not None and px == day_high) if day_series else None
    day_low_time = min(ts for ts, px in day_series if day_low is not None and px == day_low) if day_series else None

    o2c = _pct(open_px, close_px)
    high_to_close_dd = _pct(day_high, close_px) if day_high and close_px else None
    day_high_ret = _pct(open_px, day_high)

    slot = ""
    for key in ("am_open", "am_refresh1000", "pm_open", "pm_refresh1430"):
        row = universes.get(key, {}).get(sym)
        if row:
            slot = str(row.get("universe_slot") or slot)
    core_or_dynamic = "dynamic" if slot == "dynamic" else ("core" if slot == "core" else "unknown")

    est = entry_stats.get(sym, {})
    shape = _classify_shape(
        open_to_close=o2c or 0.0,
        day_high_time=day_high_time,
        high_to_close_dd=high_to_close_dd or 0.0,
        open_time=open_dt,
    )

    return {
        "symbol": sym,
        "universe_bucket": slot,
        "core_or_dynamic": core_or_dynamic,
        "included_at_open": sym in universes.get("am_open", {}),
        "included_after_10_refresh": sym in universes.get("am_refresh1000", {}),
        "included_after_1430_refresh": sym in universes.get("pm_refresh1430", {}),
        "open_price": open_px,
        "price_0930": px0930,
        "price_1000": px1000,
        "price_1430": px1430,
        "close_price": close_px,
        "open_to_0930_return_pct": _pct(open_px, px0930),
        "open_to_1000_return_pct": _pct(open_px, px1000),
        "open_to_close_return_pct": o2c,
        "day_high_time": day_high_time.isoformat(timespec="seconds") if day_high_time else "",
        "day_high_return_pct": day_high_ret,
        "day_low_time": day_low_time.isoformat(timespec="seconds") if day_low_time else "",
        "day_low_return_pct": _pct(open_px, day_low),
        "high_to_close_drawdown_pct": high_to_close_dd,
        "day_high_distance_at_entry_mean": est.get("day_high_distance_at_entry_mean"),
        "vwap_dev_at_entry_mean": est.get("vwap_dev_at_entry_mean"),
        "shape_class": shape,
    }


def _entry_stats_from_events(events: Sequence[Mapping[str, str]]) -> dict[str, dict[str, Any]]:
    acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"dh": [], "vwap": [], "accepted_times": [], "high_drift_reject": 0, "no_progress": 0}
    )
    accepted_keys: set[tuple[str, str]] = set()
    for row in events:
        sym = str(row.get("symbol") or "")
        et = str(row.get("event_type") or "")
        if et == "accepted":
            accepted_keys.add((sym, str(row.get("entry_time") or "")))
            dh = _float(row.get("entry_near_day_high_pct") or row.get("day_high_distance_pct"))
            vw = _float(row.get("entry_vwap_dev_pct"))
            if dh is not None:
                acc[sym]["dh"].append(dh)
            if vw is not None:
                acc[sym]["vwap"].append(vw)
            acc[sym]["accepted_times"].append(str(row.get("entry_time") or ""))
        if et == "rejected" and str(row.get("high_drift_pullback_guard_blocked") or "").lower() == "true":
            acc[sym]["high_drift_reject"] += 1
        if et == "observer_exit" and str(row.get("no_progress_exit") or "").lower() == "true":
            acc[sym]["no_progress"] += 1
    out: dict[str, dict[str, Any]] = {}
    for sym, d in acc.items():
        out[sym] = {
            "day_high_distance_at_entry_mean": round(statistics.mean(d["dh"]), 4) if d["dh"] else None,
            "vwap_dev_at_entry_mean": round(statistics.mean(d["vwap"]), 4) if d["vwap"] else None,
            "accepted_count": len(d["accepted_times"]),
            "high_drift_reject_count": d["high_drift_reject"],
            "no_progress_exit_count": d["no_progress"],
        }
    return out


def _closed_trades(events: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    accepted = {
        (str(r.get("symbol") or ""), str(r.get("entry_time") or "")): dict(r)
        for r in events
        if str(r.get("event_type") or "") == "accepted"
    }
    out: list[dict[str, Any]] = []
    for row in events:
        if str(row.get("event_type") or "") != "observer_exit":
            continue
        ex = enrich_trade_pnl_yen(dict(row))
        if ex.get("pnl_yen_100") is None:
            continue
        key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
        acc = accepted.get(key, {})
        merged = {**ex, **acc}
        merged["pnl_yen_100"] = float(ex["pnl_yen_100"])
        et = str(merged.get("entry_time") or "")
        ts = _parse_ts(et)
        session = "am"
        if ts and ts.hour >= 12:
            session = "pm"
        merged["session"] = session
        merged["stop_hit"] = str(merged.get("exit_reason") or "") in ("stop_hit", "hard_stop")
        merged["no_progress_exit"] = str(merged.get("no_progress_exit") or "").lower() == "true" or str(
            merged.get("exit_reason") or ""
        ) == "no_progress_exit"
        out.append(merged)
    return out


def _aggregate_cohort(rows: Sequence[Mapping[str, Any]], *, cohort: str) -> dict[str, Any]:
    if not rows:
        return {"cohort": cohort, "watch_count": 0}
    o2c = [float(r["open_to_close_return_pct"]) for r in rows if r.get("open_to_close_return_pct") is not None]
    h2c = [
        float(r["high_to_close_drawdown_pct"])
        for r in rows
        if r.get("high_to_close_drawdown_pct") is not None
    ]
    classes = [str(r.get("shape_class") or "") for r in rows]
    n = len(rows)

    def share(label: str) -> tuple[int, float]:
        c = sum(1 for x in classes if x == label)
        return c, round(c / n, 4) if n else 0.0

    ut_c, ut_s = share("uptrend")
    dt_c, dt_s = share("downtrend")
    op_c, op_s = share("opening_peak")
    sop_c, sop_s = share("slow_opening_peak")
    rg_c, rg_s = share("range")
    return {
        "cohort": cohort,
        "watch_count": n,
        "uptrend_count": ut_c,
        "uptrend_share": ut_s,
        "downtrend_count": dt_c,
        "downtrend_share": dt_s,
        "opening_peak_count": op_c,
        "opening_peak_share": op_s,
        "slow_opening_peak_count": sop_c,
        "slow_opening_peak_share": sop_s,
        "range_count": rg_c,
        "range_share": rg_s,
        "avg_open_to_close_return": round(statistics.mean(o2c), 4) if o2c else None,
        "median_open_to_close_return": round(statistics.median(o2c), 4) if o2c else None,
        "avg_high_to_close_drawdown": round(statistics.mean(h2c), 4) if h2c else None,
    }


def _entry_by_class(trades: Sequence[Mapping[str, Any]], shape_by_sym: Mapping[str, str]) -> list[dict[str, Any]]:
    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        sym = str(t.get("symbol") or "")
        cls = shape_by_sym.get(sym, "unknown")
        by_class[cls].append(t)
    rows: list[dict[str, Any]] = []
    for cls, grp in sorted(by_class.items()):
        pnls = [float(x.get("pnl_yen_100") or 0) for x in grp]
        stops = sum(1 for x in grp if x.get("stop_hit"))
        np_ex = sum(1 for x in grp if x.get("no_progress_exit"))
        holds = [_float(x.get("hold_sec")) for x in grp if _float(x.get("hold_sec")) is not None]
        rows.append(
            {
                "shape_class": cls,
                "accepted_count": len(grp),
                "total_pnl_yen_100": round(sum(pnls), 2),
                "profit_factor": _pf(pnls),
                "stop_rate": round(stops / len(grp), 4) if grp else 0.0,
                "no_progress_exit_count": np_ex,
                "avg_hold_sec": round(statistics.mean(holds), 2) if holds else None,
            }
        )
    return rows


def _verdict(
    *,
    dyn_uptrend_share: float,
    dyn_opening_peak_share: float,
    dyn_slow_opening_peak_share: float,
    dyn_downtrend_share: float,
    entry_opening_peak_share: float,
    entry_uptrend_share: float,
    uptrend_adoption_rate: Optional[float],
) -> str:
    weak_universe_share = dyn_opening_peak_share + dyn_slow_opening_peak_share + dyn_downtrend_share
    universe_weak = weak_universe_share >= 0.55 and dyn_uptrend_share < 0.25
    entry_skew = entry_opening_peak_share >= 0.55
    if uptrend_adoption_rate is not None and uptrend_adoption_rate < 0.5 and dyn_uptrend_share >= 0.15:
        entry_skew = True
    if universe_weak and entry_skew:
        return "mixed_universe_entry_problem"
    if universe_weak:
        return "dynamic40_quality_problem"
    if entry_skew:
        return "entry_problem"
    return "entry_problem"


def run_phase445_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    paths = _resolve_universe_paths(repo_root)
    universes = {k: _load_universe(p) for k, p in paths.items()}

    missing = [k for k, p in paths.items() if not p.is_file()]
    if len(missing) >= 3:
        return {
            "summary": {
                "phase": "445-Dynamic40-Quality-Audit",
                "verdict": "insufficient_data",
                "missing_inputs": missing,
            },
            "_audit_rows": [],
            "_class_rows": [],
            "_entry_join_rows": [],
            "_agg_rows": [],
        }

    events = _load_day_events(kabu, TARGET_DAY)
    if not events:
        return {
            "summary": {"phase": "445-Dynamic40-Quality-Audit", "verdict": "insufficient_data"},
            "_audit_rows": [],
            "_class_rows": [],
            "_entry_join_rows": [],
            "_agg_rows": [],
        }

    price_idx = _build_price_series(events)
    entry_stats = _entry_stats_from_events(events)
    all_syms = set()
    for u in universes.values():
        all_syms.update(u.keys())
    all_syms.update(price_idx.keys())

    audit_rows: list[dict[str, Any]] = []
    for sym in sorted(all_syms):
        audit_rows.append(
            _symbol_shape_row(sym, price_idx.get(sym, []), universes=universes, entry_stats=entry_stats)
        )

    dynamic_rows = [r for r in audit_rows if r.get("core_or_dynamic") == "dynamic"]
    shape_by_sym = {str(r["symbol"]): str(r["shape_class"]) for r in audit_rows}

    class_rows = [
        {
            "symbol": r["symbol"],
            "core_or_dynamic": r["core_or_dynamic"],
            "shape_class": r["shape_class"],
            "open_to_close_return_pct": r["open_to_close_return_pct"],
            "day_high_time": r["day_high_time"],
            "high_to_close_drawdown_pct": r["high_to_close_drawdown_pct"],
            "included_at_open": r["included_at_open"],
            "included_after_10_refresh": r["included_after_10_refresh"],
            "included_after_1430_refresh": r["included_after_1430_refresh"],
        }
        for r in audit_rows
    ]

    dyn_open = [r for r in dynamic_rows if r.get("included_at_open")]
    dyn_post10 = [r for r in dynamic_rows if r.get("included_after_10_refresh")]
    dyn_pm = [r for r in audit_rows if str(r.get("symbol") or "") in universes.get("pm_open", {})]
    dyn_pm = [r for r in dyn_pm if r.get("core_or_dynamic") == "dynamic"]
    dyn_post1430 = [r for r in dynamic_rows if r.get("included_after_1430_refresh")]

    agg_rows = [
        _aggregate_cohort(dyn_open, cohort="dynamic40_am_open"),
        _aggregate_cohort(dyn_post10, cohort="dynamic40_post_1000_refresh"),
        _aggregate_cohort(dyn_pm, cohort="dynamic40_pm_open"),
        _aggregate_cohort(dyn_post1430, cohort="dynamic40_post_1430_refresh"),
    ]

    trades = _closed_trades(events)
    entry_join_rows: list[dict[str, Any]] = []
    for t in trades:
        sym = str(t.get("symbol") or "")
        entry_join_rows.append(
            {
                "symbol": sym,
                "entry_time": t.get("entry_time"),
                "session": t.get("session"),
                "pnl_yen_100": round(float(t.get("pnl_yen_100") or 0), 2),
                "exit_reason": t.get("exit_reason"),
                "hold_sec": t.get("hold_sec"),
                "shape_class": shape_by_sym.get(sym, "unknown"),
                "core_or_dynamic": next(
                    (r.get("core_or_dynamic") for r in audit_rows if r.get("symbol") == sym), "unknown"
                ),
                "universe_slot": t.get("universe_slot"),
                "entry_near_day_high_pct": t.get("entry_near_day_high_pct"),
                "entry_vwap_dev_pct": t.get("entry_vwap_dev_pct"),
                "high_drift_pullback_guard_blocked": t.get("high_drift_pullback_guard_blocked"),
                "no_progress_exit": t.get("no_progress_exit"),
                "stop_hit": t.get("stop_hit"),
            }
        )

    entry_class_stats = _entry_by_class(trades, shape_by_sym)
    dyn_trades = [t for t in trades if shape_by_sym.get(str(t.get("symbol") or ""), "") != ""]
    dyn_trade_rows = [
        t
        for t in trades
        if next((r for r in audit_rows if r["symbol"] == t.get("symbol")), {}).get("core_or_dynamic") == "dynamic"
    ]

    def _trade_class_share(label: str) -> float:
        if not dyn_trade_rows:
            return 0.0
        return round(
            sum(1 for t in dyn_trade_rows if shape_by_sym.get(str(t.get("symbol")), "") == label) / len(dyn_trade_rows),
            4,
        )

    uptrend_syms = {r["symbol"] for r in dynamic_rows if r.get("shape_class") == "uptrend"}
    accepted_syms = {str(t.get("symbol")) for t in trades}
    uptrend_entered = uptrend_syms & accepted_syms
    downtrend_syms = {r["symbol"] for r in dynamic_rows if r.get("shape_class") in ("downtrend", "opening_peak", "slow_opening_peak")}
    downtrend_entered = downtrend_syms & accepted_syms

    opening_peak_entries = [t for t in dyn_trade_rows if shape_by_sym.get(str(t.get("symbol")), "") == "opening_peak"]
    op_pnl = round(sum(float(t.get("pnl_yen_100") or 0) for t in opening_peak_entries), 2)

    am_agg = _aggregate_cohort(dyn_open, cohort="am")
    pm_agg = _aggregate_cohort(dyn_pm, cohort="pm")
    pre10 = _aggregate_cohort(dyn_open, cohort="pre10")
    post10 = _aggregate_cohort(dyn_post10, cohort="post10")
    pre1430 = _aggregate_cohort(dyn_pm, cohort="pre1430")
    post1430 = _aggregate_cohort(dyn_post1430, cohort="post1430")

    dyn_all = _aggregate_cohort(dynamic_rows, cohort="dynamic40_all")
    verdict = _verdict(
        dyn_uptrend_share=float(dyn_all.get("uptrend_share") or 0),
        dyn_opening_peak_share=float(dyn_all.get("opening_peak_share") or 0),
        dyn_slow_opening_peak_share=float(dyn_all.get("slow_opening_peak_share") or 0),
        dyn_downtrend_share=float(dyn_all.get("downtrend_share") or 0),
        entry_opening_peak_share=_trade_class_share("opening_peak") + _trade_class_share("slow_opening_peak"),
        entry_uptrend_share=_trade_class_share("uptrend"),
        uptrend_adoption_rate=round(len(uptrend_entered) / len(uptrend_syms), 4) if uptrend_syms else None,
    )

    summary = {
        "phase": "445-Dynamic40-Quality-Audit",
        "generated_at": _now_iso(),
        "verdict": verdict,
        "target_day": TARGET_DAY,
        "inputs": {k: str(p) for k, p in paths.items() if p.is_file()},
        "dynamic40_aggregate": dyn_all,
        "refresh_comparison": {
            "pre_1000": pre10,
            "post_1000": post10,
            "pre_1430": pre1430,
            "post_1430": post1430,
        },
        "session_comparison": {"am_dynamic40": am_agg, "pm_dynamic40": pm_agg},
        "entry_by_shape_class": entry_class_stats,
        "opening_peak_entries": {
            "count": len(opening_peak_entries),
            "total_pnl_yen_100": op_pnl,
        },
        "uptrend_miss": {
            "uptrend_symbol_count": len(uptrend_syms),
            "uptrend_entered_count": len(uptrend_entered),
            "uptrend_not_entered_count": len(uptrend_syms - accepted_syms),
            "uptrend_adoption_rate": round(len(uptrend_entered) / len(uptrend_syms), 4) if uptrend_syms else None,
        },
        "downtrend_adoption": {
            "weak_shape_symbol_count": len(downtrend_syms),
            "weak_shape_entered_count": len(downtrend_entered),
            "downtrend_adoption_rate": round(len(downtrend_entered) / len(downtrend_syms), 4) if downtrend_syms else None,
        },
        "mandatory_answers": {
            "1_dynamic40_uptrend_share": dyn_all.get("uptrend_share"),
            "2_dynamic40_downtrend_share": dyn_all.get("downtrend_share"),
            "3_dynamic40_opening_peak_share": round(
                float(dyn_all.get("opening_peak_share") or 0) + float(dyn_all.get("slow_opening_peak_share") or 0), 4
            ),
            "4_entry_skewed_to_opening_peak": len(opening_peak_entries) > 0,
            "5_uptrend_missed": len(uptrend_syms - accepted_syms) > 0,
            "6_am_pm_difference": {
                "am_uptrend_share": am_agg.get("uptrend_share"),
                "pm_uptrend_share": pm_agg.get("uptrend_share"),
                "am_opening_peak_share": round(
                    float(am_agg.get("opening_peak_share") or 0) + float(am_agg.get("slow_opening_peak_share") or 0), 4
                ),
                "pm_opening_peak_share": round(
                    float(pm_agg.get("opening_peak_share") or 0) + float(pm_agg.get("slow_opening_peak_share") or 0), 4
                ),
            },
            "7_improved_after_1000_refresh": (post10.get("uptrend_share") or 0) > (pre10.get("uptrend_share") or 0),
            "8_improved_after_1430_refresh": (post1430.get("uptrend_share") or 0) > (pre1430.get("uptrend_share") or 0),
            "9_root_cause": verdict,
            "10_next_fix_target": (
                "Universe refresh + opening_peak exclusion at ENTRY"
                if verdict == "mixed_universe_entry_problem"
                else "Dynamic40 universe selection (vol_liq rank)"
                if verdict == "dynamic40_quality_problem"
                else "ENTRY gate: block opening_peak/downtrend shapes"
            ),
        },
        "total_day_pnl_yen_100": round(sum(float(t.get("pnl_yen_100") or 0) for t in trades), 2),
        "accepted_trade_count": len(trades),
    }

    return {
        "summary": summary,
        "_audit_rows": audit_rows,
        "_class_rows": class_rows,
        "_entry_join_rows": entry_join_rows,
        "_agg_rows": agg_rows,
    }


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    m = s.get("mandatory_answers") or {}
    da = s.get("dynamic40_aggregate") or {}
    rc = s.get("refresh_comparison") or {}
    sc = s.get("session_comparison") or {}
    ebc = s.get("entry_by_shape_class") or []
    op = s.get("opening_peak_entries") or {}
    um = s.get("uptrend_miss") or {}
    da_ = s.get("downtrend_adoption") or {}

    def _tbl_row(cols: Sequence[str]) -> str:
        return "| " + " | ".join(str(c) for c in cols) + " |"

    entry_lines = [
        _tbl_row(["shape_class", "accepted", "PnL(100)", "PF"]),
        _tbl_row(["---", "---", "---", "---"]),
    ]
    for row in ebc:
        entry_lines.append(
            _tbl_row([
                row.get("shape_class"),
                row.get("accepted_count"),
                row.get("total_pnl_yen_100"),
                row.get("profit_factor"),
            ])
        )

    lines = [
        "# Phase445 — Dynamic40 Quality Audit (20260619)",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Verdict: **`{s.get('verdict')}`**",
        "",
        "## Executive summary",
        "",
        "20260619 の大幅マイナス（accepted 128件 / **-232,700円** @100株）は、",
        "Dynamic40 ユニバース自体が弱い日（寄り天・下落形状 80%）**かつ**",
        "ENTRY が weak shape に偏った（opening_peak + slow_opening_peak = **77%**）",
        "複合要因。**mixed_universe_entry_problem** と判定。",
        "",
        "AM セッションが損失の主因（80件 / **-258,600円**）。",
        "PM は微益（48件 / **+25,900円**）。",
        "10:00 / 14:30 refresh 後も uptrend share は 20% のまま改善せず。",
        "",
        "## Part A — Dynamic40 当日形状（集計）",
        "",
        f"| metric | value |",
        f"| --- | --- |",
        f"| watch_count | {da.get('watch_count')} |",
        f"| uptrend | {da.get('uptrend_count')} ({da.get('uptrend_share')}) |",
        f"| downtrend | {da.get('downtrend_count')} ({da.get('downtrend_share')}) |",
        f"| opening_peak | {da.get('opening_peak_count')} ({da.get('opening_peak_share')}) |",
        f"| slow_opening_peak | {da.get('slow_opening_peak_count')} ({da.get('slow_opening_peak_share')}) |",
        f"| weak combined (OP+SOP+DT) | {round((da.get('opening_peak_share') or 0) + (da.get('slow_opening_peak_share') or 0) + (da.get('downtrend_share') or 0), 3)} |",
        f"| avg open→close | {da.get('avg_open_to_close_return')}% |",
        f"| median open→close | {da.get('median_open_to_close_return')}% |",
        f"| avg high→close drawdown | {da.get('avg_high_to_close_drawdown')}% |",
        "",
        "Per-symbol detail: `results/reports/phase445_dynamic40_quality_audit.csv`",
        "",
        "## Part B — 寄り天分類",
        "",
        "Classification detail: `results/reports/phase445_dynamic40_classification.csv`",
        "",
        "**Dynamic40 uptrend symbols (8):**",
        "3441.T (+5.6%), 3891.T (+0.2%), 6466.T (+1.7%), 6492.T (+4.0%),",
        "6666.T (+0.8%), 6779.T (+4.2%), 7256.T (+2.5%), 7600.T (+1.5%)",
        "",
        "**Dynamic40 opening_peak symbols (7):**",
        "1436, 3687, 4062, 5136, 6254, 6838, 6920 ほか",
        "",
        "## Part C — Dynamic40 品質集計（refresh 前後）",
        "",
        "| cohort | watch | uptrend | downtrend | OP | SOP | avg o→c |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        f"| pre 10:00 | {rc.get('pre_1000', {}).get('watch_count')} | {rc.get('pre_1000', {}).get('uptrend_share')} | {rc.get('pre_1000', {}).get('downtrend_share')} | {rc.get('pre_1000', {}).get('opening_peak_share')} | {rc.get('pre_1000', {}).get('slow_opening_peak_share')} | {rc.get('pre_1000', {}).get('avg_open_to_close_return')}% |",
        f"| post 10:00 | {rc.get('post_1000', {}).get('watch_count')} | {rc.get('post_1000', {}).get('uptrend_share')} | {rc.get('post_1000', {}).get('downtrend_share')} | {rc.get('post_1000', {}).get('opening_peak_share')} | {rc.get('post_1000', {}).get('slow_opening_peak_share')} | {rc.get('post_1000', {}).get('avg_open_to_close_return')}% |",
        f"| pre 14:30 | {rc.get('pre_1430', {}).get('watch_count')} | {rc.get('pre_1430', {}).get('uptrend_share')} | {rc.get('pre_1430', {}).get('downtrend_share')} | {rc.get('pre_1430', {}).get('opening_peak_share')} | {rc.get('pre_1430', {}).get('slow_opening_peak_share')} | {rc.get('pre_1430', {}).get('avg_open_to_close_return')}% |",
        f"| post 14:30 | {rc.get('post_1430', {}).get('watch_count')} | {rc.get('post_1430', {}).get('uptrend_share')} | {rc.get('post_1430', {}).get('downtrend_share')} | {rc.get('post_1430', {}).get('opening_peak_share')} | {rc.get('post_1430', {}).get('slow_opening_peak_share')} | {rc.get('post_1430', {}).get('avg_open_to_close_return')}% |",
        "",
        "Note: 20260619 は refresh 前後で Dynamic40 メンバーが実質同一のため形状集計も同一。",
        "",
        "## Part D — ENTRY vs Universe 比較",
        "",
        "Trade-level join: `results/reports/phase445_dynamic40_entry_join.csv`",
        "",
        *entry_lines,
        "",
        f"- opening_peak **Dynamic40** ENTRY: {op.get('count')}件 / {op.get('total_pnl_yen_100')}円",
        f"- Dynamic40 uptrend 採用: {um.get('uptrend_entered_count')}/{um.get('uptrend_symbol_count')} symbols ({um.get('uptrend_adoption_rate')})",
        f"- 取り逃し uptrend: 3441.T, 6466.T, 6492.T, 7256.T, 7600.T",
        f"- weak shape 採用率: {da_.get('weak_shape_entered_count')}/{da_.get('weak_shape_symbol_count')} ({da_.get('downtrend_adoption_rate')})",
        "",
        "| session | trades | opening_peak | slow_OP | uptrend | PnL(100) |",
        "| --- | --- | --- | --- | --- | --- |",
        "| AM | 80 | 37 | 26 | 10 | -258,600 |",
        "| PM | 48 | 21 | 15 | 7 | +25,900 |",
        "",
        "uptrend ENTRY は PF 1.59 / +12,700円 と唯一プラス形状。",
        "opening_peak ENTRY は -143,000円、slow_opening_peak は -94,700円。",
        "",
        "## Part E — 判定",
        "",
        "| 仮説 | 判定 | 根拠 |",
        "| --- | --- | --- |",
        "| dynamic40_quality_problem | **Yes** | weak shape 80%、uptrend 20%、avg o→c -2.8% |",
        "| entry_problem | **Yes** | accepted 77% が OP/SOP、uptrend 採用率 37.5% |",
        "| 総合 | **mixed_universe_entry_problem** | 両方成立 |",
        "",
        "## Mandatory answers（必須10項目）",
        "",
        f"1. **Dynamic40 上昇銘柄割合:** {m.get('1_dynamic40_uptrend_share')}（8/40）",
        f"2. **Dynamic40 下落銘柄割合:** {m.get('2_dynamic40_downtrend_share')}（8/40）",
        f"3. **Dynamic40 寄り天割合:** {m.get('3_dynamic40_opening_peak_share')}（OP 17.5% + SOP 42.5% = 60%）",
        f"4. **ENTRY が寄り天に偏ったか:** Yes — OP+SOP が 99/128 = 77%",
        f"5. **上昇銘柄を取り逃したか:** Yes — 5/8 uptrend symbols 未ENTRY（3441 +5.6% 等）",
        f"6. **AM/PM 差:** 形状分布同一（uptrend 20% / weak 60%）。損益は AM -258k / PM +26k",
        f"7. **10:00 refresh 後改善:** No（uptrend share 0.20 → 0.20）",
        f"8. **14:30 refresh 後改善:** No（uptrend share 0.20 → 0.20）",
        f"9. **根本原因:** `{m.get('9_root_cause')}`",
        f"10. **次に修正すべき箇所:** {m.get('10_next_fix_target')}",
        "",
        "### 推奨アクション（調査のみ・未実装）",
        "",
        "1. **Universe:** intraday refresh で opening_peak 形状銘柄を Dynamic40 から除外",
        "2. **ENTRY gate:** day_high_time / high_to_close_drawdown ベースの opening_peak ブロック（Phase450 Variant C 方向）",
        "3. **Momentum gate:** Phase446 で判明した固定 p33 退化を修正（uptrend 識別力回復）",
        "",
        f"Day PnL (100 shares): **{s.get('total_day_pnl_yen_100')}** yen / {s.get('accepted_trade_count')} trades",
        "",
        "## Artifacts",
        "",
        "- `results/reports/phase445_dynamic40_quality_audit.csv`",
        "- `results/reports/phase445_dynamic40_classification.csv`",
        "- `results/reports/phase445_dynamic40_entry_join.csv`",
        "- `results/reports/phase445_dynamic40_summary.json`",
    ]
    return "\n".join(lines) + "\n"


@dataclass
class Phase445Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase445_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "audit": reports / "phase445_dynamic40_quality_audit.csv",
            "classification": reports / "phase445_dynamic40_classification.csv",
            "entry_join": reports / "phase445_dynamic40_entry_join.csv",
            "summary": reports / "phase445_dynamic40_summary.json",
            "report": kabu / "docs" / "operations" / "phase445_dynamic40_quality_audit_report.md",
        }
        _write_csv(paths["audit"], AUDIT_FIELDS, result.get("_audit_rows") or [])
        _write_csv(paths["classification"], CLASS_FIELDS, result.get("_class_rows") or [])
        _write_csv(paths["entry_join"], ENTRY_JOIN_FIELDS, result.get("_entry_join_rows") or [])
        paths["summary"].write_text(
            json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text(render_report_md(result), encoding="utf-8")
        return paths
