"""
Phase 145: Review-only what-if for AM/PM rescreening, limit status, session close.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.mfe_mae_exit_review import as_float, load_structural_trades, parse_ts
from research.replay_fidelity_review import _norm_session_id
from universe.am_pm_universe import (
    NEAR_LIMIT_PCT,
    _norm,
    build_limit_diagnostics,
    compare_am_pm,
    limit_status_from_prices,
)
from universe.core10_dynamic40 import (
    build_am_universe,
    build_pm_universe,
    universe_am_path,
    universe_pm_path,
    write_universe_csv,
)
from universe.core_watchlist import load_core_watchlist, resolve_core_symbol_source_path
from universe.daily_features import features_csv_path, generate_features_csv, load_features_csv
from universe.dynamic_build import load_dynamic_config, resolve_symbol_master
from universe.hero_backtest import load_session_activity, load_symbol_set_from_csv

JST = ZoneInfo("Asia/Tokyo")
PNL_EPS = 0.0001

# Phase 116 production shadow times (what-if reference; no YAML change).
MORNING_FORCE_CLOSE = time(11, 25)
AFTERNOON_FORCE_CLOSE = time(15, 23)
MORNING_EARLY = time(11, 20)
MORNING_LATE = time(11, 30)
AFTERNOON_EARLY = time(15, 18)
AFTERNOON_LATE = time(15, 28)
POST_CLOSE_OFFSETS_SEC = (30, 60, 180)


@dataclass
class PushTick:
    ts: float
    price: float
    bid_qty: Optional[float] = None
    ask_qty: Optional[float] = None


def _day_stamp(trade_date: str) -> str:
    return trade_date.replace("-", "")


def _trade_date_from_stamp(stamp: str) -> str:
    s = stamp.replace("-", "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def discover_review_days(small_paper_root: Path) -> list[str]:
    out: list[str] = []
    if not small_paper_root.is_dir():
        return out
    for d in sorted(small_paper_root.iterdir()):
        if not d.is_dir() or not d.name.isdigit() or len(d.name) != 8:
            continue
        for sess in d.glob("live_full_session_*"):
            if (sess / "structural_trades.csv").is_file():
                out.append(_trade_date_from_stamp(d.name))
                break
    return out


def find_live_session(small_paper_root: Path, day_stamp: str) -> Optional[Path]:
    day_dir = small_paper_root / day_stamp
    if not day_dir.is_dir():
        return None
    live = sorted(day_dir.glob("live_full_session_*"))
    return live[0] if live else None


def _ensure_features(
    *,
    trade_date: str,
    reports_dir: Path,
    repo_root: Path,
    symbol_meta: Mapping[str, Mapping[str, Any]],
    master_symbols: Sequence[str],
    generate: bool,
) -> tuple[Path, list[dict[str, str]]]:
    stamp = _day_stamp(trade_date)
    path = features_csv_path(reports_dir, stamp)
    if path.is_file():
        return path, load_features_csv(path)
    if not generate:
        return path, []
    td = date.fromisoformat(trade_date)
    summary = generate_features_csv(
        symbols=master_symbols[:1200],
        trade_date=td,
        symbol_meta=symbol_meta,
        out_path=path,
    )
    if not summary.get("row_count"):
        return path, []
    return path, load_features_csv(path)


def _symbol_sets_from_universe(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    return {_norm(str(r.get("symbol") or "")) for r in rows if _norm(str(r.get("symbol") or ""))}


def _events_by_symbol(session_dir: Path) -> dict[str, dict[str, int]]:
    path = session_dir / "small_paper_events.csv"
    if not path.is_file():
        return {}
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"candidate": 0, "accepted": 0})
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = _norm(str(row.get("symbol") or ""))
            et = str(row.get("event_type") or "")
            if sym and et in ("candidate", "accepted"):
                out[sym][et] += 1
    return dict(out)


def _pnl_by_symbol(session_dir: Path) -> dict[str, float]:
    path = session_dir / "structural_trades.csv"
    if not path.is_file():
        return {}
    agg: dict[str, float] = defaultdict(float)
    for row in load_structural_trades(path):
        sym = _norm(str(row.get("symbol") or ""))
        if sym:
            agg[sym] += float(row.get("realized_pnl_pct") or 0)
    return dict(agg)


def _post_pm_pnl_proxy(
    symbols: Sequence[str],
    *,
    push_day_dir: Path,
    trade_date: str,
) -> dict[str, Optional[float]]:
    """PnL proxy from PM session start (12:33) to last push tick before 15:23."""
    start_ts = parse_ts(f"{trade_date}T12:33:00+09:00")
    end_ts = parse_ts(f"{trade_date}T15:23:00+09:00")
    out: dict[str, Optional[float]] = {}
    for sym in symbols:
        ticks = load_push_ticks(push_day_dir, sym)
        if not ticks:
            out[sym] = None
            continue
        in_sess = [t for t in ticks if start_ts <= t.ts <= end_ts]
        if len(in_sess) < 2:
            out[sym] = None
            continue
        p0 = in_sess[0].price
        p1 = in_sess[-1].price
        if p0 <= 0:
            out[sym] = None
        else:
            out[sym] = round((p1 - p0) / p0 * 100.0, 4)
    return out


def analyze_am_pm_rescreening(
    *,
    trade_dates: Sequence[str],
    reports_dir: Path,
    repo_root: Path,
    small_paper_root: Path,
    push_root: Path,
    generate_features: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any], str, list[str]]:
    source_info = resolve_core_symbol_source_path(repo_root)
    core_symbols, _ = load_core_watchlist(repo_root)
    cfg = load_dynamic_config(repo_root / "kabu_native" / "configs" / "universe_dynamic_trial.yaml")
    _, master_entries = resolve_symbol_master(repo_root, cfg.symbol_master_paths)
    symbol_meta: dict[str, dict[str, Any]] = {}
    master_symbols: list[str] = []
    for e in master_entries:
        sym = f"{e.parsed.code}.T"
        master_symbols.append(sym)
        symbol_meta[sym] = {
            "exchange": e.parsed.exchange,
            "symbol_key": e.parsed.symbol_key,
            "market": e.market,
        }

    rows: list[dict[str, Any]] = []
    daily: dict[str, Any] = {}
    overlap_rates: list[float] = []
    churn_rates: list[float] = []

    for trade_date in trade_dates:
        stamp = _day_stamp(trade_date)
        push_dir = push_root / trade_date
        feat_path, features = _ensure_features(
            trade_date=trade_date,
            reports_dir=reports_dir,
            repo_root=repo_root,
            symbol_meta=symbol_meta,
            master_symbols=master_symbols,
            generate=generate_features,
        )
        session_dir = find_live_session(small_paper_root, stamp)

        am_csv = universe_am_path(reports_dir, stamp)
        pm_csv = universe_pm_path(reports_dir, stamp)

        def _load_univ_csv(path: Path) -> list[dict[str, Any]]:
            if not path.is_file():
                return []
            with path.open(encoding="utf-8", newline="") as f:
                return [dict(r) for r in csv.DictReader(f)]

        am_rows = _load_univ_csv(am_csv)
        pm_rows = _load_univ_csv(pm_csv)
        if features and (not am_rows or not pm_rows):
            am_rows = build_am_universe(
                core_symbols=core_symbols, feature_rows=features, symbol_meta=symbol_meta
            )
            pm_rows = build_pm_universe(
                core_symbols=core_symbols,
                feature_rows=features,
                symbol_meta=symbol_meta,
                push_day_dir=push_dir,
            )
            if am_rows:
                write_universe_csv(am_csv, am_rows)
            if pm_rows:
                write_universe_csv(pm_csv, pm_rows)

        am_set = _symbol_sets_from_universe(am_rows)
        pm_set = _symbol_sets_from_universe(pm_rows)
        cmp = compare_am_pm(am_rows, pm_rows) if am_rows and pm_rows else {}
        overlap = int(cmp.get("overlap_count") or 0)
        added = list(cmp.get("added_symbols") or [])
        removed = list(cmp.get("removed_symbols") or [])
        if am_set:
            overlap_rates.append(overlap / len(am_set))
        if cmp.get("churn_rate") is not None:
            churn_rates.append(float(cmp["churn_rate"]))

        events: dict[str, dict[str, int]] = {}
        pnl_map: dict[str, float] = {}
        if session_dir:
            events = _events_by_symbol(session_dir)
            pnl_map = _pnl_by_symbol(session_dir)

        post_pm = _post_pm_pnl_proxy(added, push_day_dir=push_dir, trade_date=trade_date)

        def _bucket_stats(symbols: Sequence[str], label: str) -> dict[str, Any]:
            cand = acc = 0
            pnls: list[float] = []
            post_pnls: list[float] = []
            for sym in symbols:
                ev = events.get(sym, {})
                cand += ev.get("candidate", 0)
                acc += ev.get("accepted", 0)
                if sym in pnl_map:
                    pnls.append(pnl_map[sym])
                if sym in post_pm and post_pm[sym] is not None:
                    post_pnls.append(float(post_pm[sym]))
            return {
                f"{label}_symbol_count": len(symbols),
                f"{label}_candidate_count": cand,
                f"{label}_accepted_count": acc,
                f"{label}_structural_pnl_proxy_sum": round(sum(pnls), 4) if pnls else None,
                f"{label}_structural_pnl_proxy_avg": round(sum(pnls) / len(pnls), 4) if pnls else None,
                f"{label}_post_pm_pnl_proxy_avg": (
                    round(sum(post_pnls) / len(post_pnls), 4) if post_pnls else None
                ),
                f"{label}_post_pm_pnl_n": len(post_pnls),
            }

        stayed = sorted(am_set & pm_set)
        row = {
            "trade_date": trade_date,
            "features_available": bool(features),
            "features_path": str(feat_path) if feat_path.is_file() else "",
            "am_universe_count": len(am_set),
            "pm_universe_count": len(pm_set),
            "overlap_count": overlap,
            "overlap_rate": round(overlap / max(len(am_set), 1), 4),
            "pm_added_count": len(added),
            "am_removed_count": len(removed),
            "churn_rate": cmp.get("churn_rate"),
            "push_dir_exists": push_dir.is_dir(),
            "session_dir": str(session_dir) if session_dir else "",
            **_bucket_stats(stayed, "stayed"),
            **_bucket_stats(added, "pm_added"),
            **_bucket_stats(removed, "am_removed"),
            **_bucket_stats(sorted(am_set), "am_all"),
        }
        rows.append(row)
        daily[trade_date] = {
            "comparison": cmp,
            "added_symbols": added,
            "removed_symbols": removed,
        }

    notes: list[str] = []
    valid = [r for r in rows if r.get("features_available")]
    added_pnl = [
        float(r["pm_added_structural_pnl_proxy_avg"])
        for r in valid
        if r.get("pm_added_structural_pnl_proxy_avg") is not None
    ]
    stayed_pnl = [
        float(r["stayed_structural_pnl_proxy_avg"])
        for r in valid
        if r.get("stayed_structural_pnl_proxy_avg") is not None
    ]
    if not valid:
        return rows, daily, "need_intraday_liquidity_data", ["no features CSV for any review day"]

    if len(valid) < 2:
        notes.append(f"features on {len(valid)}/{len(trade_dates)} days only — multi-day PM comparison incomplete")
        if added_pnl and stayed_pnl and sum(added_pnl) / len(added_pnl) > sum(stayed_pnl) / len(stayed_pnl) + 0.05:
            notes.append("single-day signal: PM-added symbols higher structural pnl vs stayed (20260521)")
        return rows, daily, "need_intraday_liquidity_data", notes

    avg_overlap = sum(float(r["overlap_rate"] or 0) for r in valid) / len(valid)
    avg_churn = sum(float(r["churn_rate"] or 0) for r in valid) / len(valid)
    avg_added = sum(int(r["pm_added_count"] or 0) for r in valid) / len(valid)
    notes.append(f"days={len(valid)} avg_overlap_rate={avg_overlap:.1%} avg_churn={avg_churn:.1%}")

    added_cand = sum(int(r.get("pm_added_candidate_count") or 0) for r in valid)
    stayed_cand = sum(int(r.get("stayed_candidate_count") or 0) for r in valid)

    if avg_overlap >= 0.85 and avg_churn < 0.12:
        notes.append("high overlap low churn — PM rescreen adds little new universe")
        return rows, daily, "am_pm_rescreening_not_needed", notes

    push_limited = any(not r.get("push_dir_exists") for r in valid)
    if push_limited or avg_added > 0 and all((r.get("pm_added_post_pm_pnl_n") or 0) == 0 for r in valid):
        notes.append("PM push coverage too thin for post_pm_pnl_proxy on added symbols")
        return rows, daily, "need_intraday_liquidity_data", notes

    if added_cand > stayed_cand * 0.25 and added_pnl and stayed_pnl:
        if sum(added_pnl) / len(added_pnl) > sum(stayed_pnl) / len(stayed_pnl) + 0.05:
            notes.append("PM-added symbols show higher avg structural pnl proxy vs stayed")
            return rows, daily, "am_pm_rescreening_worthwhile", notes

    if avg_churn >= 0.25 and avg_added >= 10:
        notes.append("material universe churn from PM rescreen — review pilot with full push")
        return rows, daily, "am_pm_rescreening_worthwhile", notes

    notes.append("churn present but session PnL/candidate lift not proven on available days")
    return rows, daily, "am_pm_rescreening_not_needed", notes


_TICK_CACHE: dict[tuple[str, str], list[PushTick]] = {}


def push_jsonl_path(push_day_dir: Path, symbol: str) -> Optional[Path]:
    stem = symbol.replace(".T", "")
    path = push_day_dir / f"{stem}.T.jsonl"
    if path.is_file():
        return path
    path = push_day_dir / f"{symbol}.jsonl"
    return path if path.is_file() else None


def load_push_ticks(push_day_dir: Path, symbol: str) -> list[PushTick]:
    cache_key = (str(push_day_dir), _norm(symbol))
    if cache_key in _TICK_CACHE:
        return _TICK_CACHE[cache_key]
    path = push_jsonl_path(push_day_dir, symbol)
    if path is None:
        _TICK_CACHE[cache_key] = []
        return []
    ticks: list[PushTick] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec = str(row.get("recorded_at") or "")
            if not rec:
                continue
            dt = datetime.fromisoformat(rec.replace("Z", "+00:00")).astimezone(JST)
            payload = row.get("payload") or row
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            if not isinstance(payload, Mapping):
                continue
            cur = as_float(payload.get("CurrentPrice")) or as_float(payload.get("CalcPrice"))
            if cur is None or cur <= 0:
                continue
            ticks.append(
                PushTick(
                    ts=dt.timestamp(),
                    price=float(cur),
                    bid_qty=as_float(payload.get("BidQty")),
                    ask_qty=as_float(payload.get("AskQty")),
                )
            )
    ticks.sort(key=lambda t: t.ts)
    _TICK_CACHE[cache_key] = ticks
    return ticks


def _price_at_ts(ticks: Sequence[PushTick], target_ts: float) -> Optional[float]:
    if not ticks:
        return None
    best: Optional[PushTick] = None
    best_d = 1e18
    for t in ticks:
        d = abs(t.ts - target_ts)
        if d < best_d:
            best_d = d
            best = t
    if best is None or best_d > 120:
        return None
    return best.price


def _pnl_pct(entry: float, exit_p: float) -> Optional[float]:
    if entry <= 0 or exit_p <= 0:
        return None
    return round((exit_p - entry) / entry * 100.0, 4)


def _path_extrema(
    ticks: Sequence[PushTick],
    start_ts: float,
    end_ts: float,
    entry: float,
) -> tuple[Optional[float], Optional[float]]:
    window = [t for t in ticks if start_ts <= t.ts <= end_ts]
    if not window or entry <= 0:
        return None, None
    pnls = [(t.price - entry) / entry * 100.0 for t in window]
    return round(max(pnls), 4), round(min(pnls), 4)


def analyze_session_close(
    session_dirs: Sequence[Path],
    *,
    push_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, list[str]]:
    """What-if forced exits at AM/PM session close times using push prices.

    Current full-session pilot rarely holds through lunch (max hold ~3 min).
    When no positions are open at the boundary, use counterfactual pnl for
    trades entered before force-close vs actual structural exit.
    """
    trade_rows: list[dict[str, Any]] = []
    scenario_acc: dict[str, list[float]] = defaultdict(list)
    open_at_boundary_count = 0

    boundaries = (
        ("morning", MORNING_FORCE_CLOSE, MORNING_EARLY, MORNING_LATE, time(13, 0)),
        ("afternoon", AFTERNOON_FORCE_CLOSE, AFTERNOON_EARLY, AFTERNOON_LATE, time(15, 30)),
    )

    for session_dir in session_dirs:
        session_dir = Path(session_dir)
        st_path = session_dir / "structural_trades.csv"
        if not st_path.is_file():
            continue
        sid = _norm_session_id(
            str(session_dir.relative_to(session_dir.parent.parent))
            if session_dir.parent.parent
            else session_dir.name
        )
        parts = sid.split("/")
        day_stamp = parts[0] if parts else ""
        trade_date = _trade_date_from_stamp(day_stamp) if day_stamp.isdigit() else ""
        push_dir = push_root / trade_date

        for row in load_structural_trades(st_path):
            sym = _norm(str(row.get("symbol") or ""))
            ent_ts = parse_ts(str(row.get("entry_time") or ""))
            close_ts = parse_ts(str(row.get("close_time") or ""))
            entry_p = float(row.get("entry_price") or 0)
            close_p = float(row.get("close_price") or 0)
            actual_pnl = float(row.get("realized_pnl_pct") or 0)
            if not sym or ent_ts <= 0 or close_ts <= 0 or entry_p <= 0:
                continue

            if not push_jsonl_path(push_dir, sym):
                continue
            ticks = load_push_ticks(push_dir, sym)

            for sess_name, force_t, early_t, late_t, horizon_t in boundaries:
                force_dt = datetime.combine(
                    datetime.fromtimestamp(ent_ts, tz=JST).date(), force_t, tzinfo=JST
                )
                force_ts = force_dt.timestamp()
                if ent_ts >= force_ts:
                    continue

                was_open_at_boundary = ent_ts < force_ts < close_ts
                if was_open_at_boundary:
                    open_at_boundary_count += 1

                p_force = _price_at_ts(ticks, force_ts)
                p_early = _price_at_ts(
                    ticks,
                    datetime.combine(force_dt.date(), early_t, tzinfo=JST).timestamp(),
                )
                p_late = _price_at_ts(
                    ticks,
                    datetime.combine(force_dt.date(), late_t, tzinfo=JST).timestamp(),
                )
                p_actual = close_p if close_p > 0 else _price_at_ts(ticks, close_ts)

                pnl_force = _pnl_pct(entry_p, p_force) if p_force else None
                pnl_early = _pnl_pct(entry_p, p_early) if p_early else None
                pnl_late = _pnl_pct(entry_p, p_late) if p_late else None
                pnl_hold = _pnl_pct(entry_p, p_actual) if p_actual else actual_pnl

                horizon_ts = datetime.combine(force_dt.date(), horizon_t, tzinfo=JST).timestamp()
                end_path = min(max(close_ts, force_ts + 1), horizon_ts)
                best, worst = _path_extrema(ticks, force_ts, end_path, entry_p)

                post: dict[str, Optional[float]] = {}
                for off in POST_CLOSE_OFFSETS_SEC:
                    px = _price_at_ts(ticks, force_ts + off)
                    post[f"pnl_at_force_plus_{off}s"] = _pnl_pct(entry_p, px) if px else None

                delta_vs_actual = (
                    round((pnl_force or 0) - (pnl_hold or actual_pnl), 4)
                    if pnl_force is not None and pnl_hold is not None
                    else None
                )
                left_on_table = (
                    round((best or 0) - (pnl_force or 0), 4)
                    if best is not None and pnl_force is not None
                    else None
                )

                tr = {
                    "session_id": sid,
                    "trade_date": trade_date,
                    "symbol": sym,
                    "entry_time": row.get("entry_time"),
                    "close_time": row.get("close_time"),
                    "close_reason": row.get("close_reason"),
                    "session_bucket": row.get("session_bucket"),
                    "hold_duration_sec": row.get("hold_duration_sec"),
                    "session_close_kind": sess_name,
                    "force_close_time": force_t.strftime("%H:%M"),
                    "was_open_at_boundary": was_open_at_boundary,
                    "analysis_mode": (
                        "open_at_boundary" if was_open_at_boundary else "counterfactual_pre_entry"
                    ),
                    "actual_pnl_pct": round(actual_pnl, 4),
                    "pnl_if_force_close": pnl_force,
                    "pnl_if_close_early": pnl_early,
                    "pnl_if_close_late": pnl_late,
                    "pnl_if_hold_to_actual": pnl_hold,
                    "delta_force_vs_actual": delta_vs_actual,
                    "post_force_best_pnl_pct": best,
                    "post_force_worst_pnl_pct": worst,
                    "left_on_table_best_minus_force": left_on_table,
                    "push_ticks_available": bool(ticks),
                    **post,
                }
                trade_rows.append(tr)

                if pnl_force is not None:
                    scenario_acc["current_force_close"].append(pnl_force)
                if pnl_early is not None:
                    scenario_acc["close_earlier"].append(pnl_early)
                if pnl_late is not None:
                    scenario_acc["close_later"].append(pnl_late)
                if pnl_hold is not None:
                    scenario_acc["hold_to_actual"].append(pnl_hold)

    scenario_rows: list[dict[str, Any]] = []
    for sid, pnls in scenario_acc.items():
        scenario_rows.append(
            {
                "scenario_id": sid,
                "trade_count": len(pnls),
                "total_pnl_proxy": round(sum(pnls), 4),
                "avg_pnl_proxy": round(sum(pnls) / len(pnls), 4) if pnls else None,
            }
        )

    notes: list[str] = []
    with_push = [r for r in trade_rows if r.get("push_ticks_available")]
    if not trade_rows:
        return trade_rows, scenario_rows, "need_more_session_close_data", ["no pre-boundary trades with push"]

    notes.append(f"open_at_boundary={open_at_boundary_count} (current pilot max hold ~3min)")
    if open_at_boundary_count == 0:
        notes.append(
            "counterfactual mode: actual short structural exit vs pnl if held to force-close time"
        )

    if len(with_push) < len(trade_rows) * 0.15:
        notes.append(f"push price coverage {len(with_push)}/{len(trade_rows)}")
        return trade_rows, scenario_rows, "need_more_session_close_data", notes

    left_vals = [float(r["left_on_table_best_minus_force"]) for r in with_push if r.get("left_on_table_best_minus_force") is not None]
    deltas = [float(r["delta_force_vs_actual"]) for r in with_push if r.get("delta_force_vs_actual") is not None]
    avg_left = sum(left_vals) / len(left_vals) if left_vals else 0.0
    avg_delta = sum(deltas) / len(deltas) if deltas else 0.0
    notes.append(
        f"counterfactual_trades={len(trade_rows)} push_ok={len(with_push)} "
        f"avg_left_on_table={avg_left:.3f}% avg_delta_force_vs_actual={avg_delta:.3f}%"
    )

    morning = [r for r in with_push if r.get("session_close_kind") == "morning"]
    afternoon = [r for r in with_push if r.get("session_close_kind") == "afternoon"]

    def _missed(rlist: Sequence[Mapping[str, Any]]) -> float:
        vals = [float(r["left_on_table_best_minus_force"]) for r in rlist if r.get("left_on_table_best_minus_force") is not None]
        return sum(vals) / len(vals) if vals else 0.0

    if open_at_boundary_count == 0 and len(with_push) < 30:
        return trade_rows, scenario_rows, "need_more_session_close_data", notes + [
            "no live open-through-lunch positions; need AM/PM shadow session with longer holds"
        ]

    if avg_left > 0.15 and avg_delta < -0.05:
        notes.append("force close exits before meaningful post-close upside on average")
        return trade_rows, scenario_rows, "session_close_too_early", notes
    if avg_left < 0.03 and avg_delta > 0.05:
        notes.append("force close retains more pnl than holding to actual on average")
        return trade_rows, scenario_rows, "session_close_reasonable", notes + ["may cut losers earlier"]
    if _missed(morning) > 0.2 and _missed(afternoon) < 0.08:
        return trade_rows, scenario_rows, "session_close_too_early", notes + ["morning close leaves more on table"]
    if _missed(afternoon) > 0.2:
        return trade_rows, scenario_rows, "session_close_too_early", notes + ["afternoon close leaves upside"]

    return trade_rows, scenario_rows, "session_close_reasonable", notes


def analyze_limit_status(
    *,
    trade_dates: Sequence[str],
    reports_dir: Path,
    repo_root: Path,
    small_paper_root: Path,
    push_root: Path,
    generate_features: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, list[str]]:
    cfg = load_dynamic_config(repo_root / "kabu_native" / "configs" / "universe_dynamic_trial.yaml")
    _, master_entries = resolve_symbol_master(repo_root, cfg.symbol_master_paths)
    symbol_meta: dict[str, dict[str, Any]] = {}
    master_symbols: list[str] = []
    for e in master_entries:
        sym = f"{e.parsed.code}.T"
        master_symbols.append(sym)
        symbol_meta[sym] = {
            "exchange": e.parsed.exchange,
            "symbol_key": e.parsed.symbol_key,
            "market": e.market,
        }

    per_sym_rows: list[dict[str, Any]] = []
    scenario_rows: list[dict[str, Any]] = []
    limit_src_ok = False

    for trade_date in trade_dates:
        stamp = _day_stamp(trade_date)
        push_dir = push_root / trade_date
        _, features = _ensure_features(
            trade_date=trade_date,
            reports_dir=reports_dir,
            repo_root=repo_root,
            symbol_meta=symbol_meta,
            master_symbols=master_symbols,
            generate=generate_features,
        )
        feat_by = {_norm(r["symbol"]): r for r in features}
        session_dir = find_live_session(small_paper_root, stamp)
        events = _events_by_symbol(session_dir) if session_dir else {}
        pnl_map = _pnl_by_symbol(session_dir) if session_dir else {}

        watch_syms: set[str] = set()
        if push_dir.is_dir():
            for p in push_dir.glob("*.jsonl"):
                watch_syms.add(_norm(p.stem))
        if not watch_syms and features:
            vol_path = reports_dir / f"universe_vol_liq_dynamic50_{stamp}.csv"
            watch_syms = load_symbol_set_from_csv(vol_path) if vol_path.is_file() else set()

        if not watch_syms:
            continue

        lim_rows = build_limit_diagnostics(
            sorted(watch_syms),
            feature_by_sym=feat_by,
            symbol_meta=symbol_meta,
            push_day_dir=push_dir,
        )
        for lim in lim_rows:
            sym = _norm(str(lim.get("symbol") or ""))
            if str(lim.get("limit_price_source") or "") == "proxy_jpx_tier_abs_yen":
                limit_src_ok = True
            ev = events.get(sym, {})
            per_sym_rows.append(
                {
                    "trade_date": trade_date,
                    "symbol": sym,
                    **{k: lim.get(k) for k in lim if k != "symbol"},
                    "candidate_count": ev.get("candidate", 0),
                    "accepted_count": ev.get("accepted", 0),
                    "structural_pnl_proxy": round(pnl_map.get(sym, 0.0), 4) if sym in pnl_map else None,
                    "spread_bps_proxy": lim.get("distance_to_limit_up_pct"),
                    "liquidity_thin": lim.get("board_liquidity_thin"),
                }
            )

    def _agg(filter_fn) -> dict[str, Any]:
        subset = [r for r in per_sym_rows if filter_fn(r)]
        cand = sum(int(r.get("candidate_count") or 0) for r in subset)
        acc = sum(int(r.get("accepted_count") or 0) for r in subset)
        pnls = [float(r["structural_pnl_proxy"]) for r in subset if r.get("structural_pnl_proxy") is not None]
        return {
            "symbol_rows": len(subset),
            "candidate_count": cand,
            "accepted_count": acc,
            "pnl_proxy_sum": round(sum(pnls), 4) if pnls else None,
            "pnl_proxy_avg": round(sum(pnls) / len(pnls), 4) if pnls else None,
        }

    scenarios = [
        ("warning_only", lambda r: True),
        ("exclude_limit_up_down", lambda r: not (r.get("is_limit_up") or r.get("is_limit_down"))),
        (
            "downgrade_near_limit",
            lambda r: not (
                r.get("is_limit_up")
                or r.get("is_limit_down")
                or (
                    r.get("near_limit_up")
                    and str(r.get("liquidity_thin")).lower() in ("true", "1")
                )
            ),
        ),
        ("no_change", lambda r: True),
    ]
    for sid, fn in scenarios:
        scenario_rows.append({"scenario_id": sid, **_agg(fn)})

    limit_hits = [r for r in per_sym_rows if r.get("is_limit_up") or r.get("is_limit_down")]
    near_hits = [r for r in per_sym_rows if r.get("near_limit_up") or r.get("near_limit_down")]
    notes: list[str] = []

    if not per_sym_rows:
        return per_sym_rows, scenario_rows, "need_limit_price_source", ["no push watchlist symbols with limit diagnostics"]

    if not limit_src_ok:
        return per_sym_rows, scenario_rows, "need_limit_price_source", ["missing limit price proxy"]

    notes.append(
        f"symbols={len(per_sym_rows)} limit_up/down={len(limit_hits)} near_limit={len(near_hits)}"
    )

    if not limit_hits and not near_hits:
        notes.append("no limit/near-limit flags in push-covered symbols — exclusion low value")
        return per_sym_rows, scenario_rows, "warning_only_sufficient", notes

    ex = _agg(lambda r: not (r.get("is_limit_up") or r.get("is_limit_down")))
    all_a = _agg(lambda r: True)
    if ex.get("pnl_proxy_avg") is not None and all_a.get("pnl_proxy_avg") is not None:
        if float(ex["pnl_proxy_avg"]) > float(all_a["pnl_proxy_avg"]) + 0.1:
            notes.append("excluding limit up/down improves avg structural pnl proxy")
            return per_sym_rows, scenario_rows, "limit_exclusion_promising", notes

    if len(limit_hits) <= 2 and len(near_hits) > len(limit_hits) * 5:
        return per_sym_rows, scenario_rows, "limit_signal_noisy", notes + ["near_limit dominates; official prices needed"]

    return per_sym_rows, scenario_rows, "warning_only_sufficient", notes


def run_remaining_issues_review(
    *,
    repo_root: Path,
    reports_dir: Path,
    small_paper_root: Path,
    push_root: Path,
    trade_dates: Optional[Sequence[str]] = None,
    generate_features: bool = True,
) -> dict[str, Any]:
    repo_root = Path(repo_root)
    dates = list(trade_dates or discover_review_days(small_paper_root))
    session_dirs = []
    for td in dates:
        sd = find_live_session(small_paper_root, _day_stamp(td))
        if sd:
            session_dirs.append(sd)

    am_pm_rows, am_pm_daily, am_pm_verdict, am_pm_notes = analyze_am_pm_rescreening(
        trade_dates=dates,
        reports_dir=reports_dir,
        repo_root=repo_root,
        small_paper_root=small_paper_root,
        push_root=push_root,
        generate_features=generate_features,
    )
    limit_rows, limit_scenarios, limit_verdict, limit_notes = analyze_limit_status(
        trade_dates=dates,
        reports_dir=reports_dir,
        repo_root=repo_root,
        small_paper_root=small_paper_root,
        push_root=push_root,
        generate_features=generate_features,
    )
    close_rows, close_scenarios, close_verdict, close_notes = analyze_session_close(
        session_dirs, push_root=push_root
    )

    return {
        "trade_dates": dates,
        "session_count": len(session_dirs),
        "am_pm_rescreening": {
            "verdict": am_pm_verdict,
            "verdict_notes": am_pm_notes,
            "rows": am_pm_rows,
            "daily": am_pm_daily,
        },
        "limit_status": {
            "verdict": limit_verdict,
            "verdict_notes": limit_notes,
            "rows": limit_rows,
            "scenarios": limit_scenarios,
        },
        "session_close": {
            "verdict": close_verdict,
            "verdict_notes": close_notes,
            "rows": close_rows,
            "scenarios": close_scenarios,
            "policy_reference": {
                "morning_force_close": MORNING_FORCE_CLOSE.strftime("%H:%M"),
                "afternoon_force_close": AFTERNOON_FORCE_CLOSE.strftime("%H:%M"),
                "note": "Production pilot structural_trades have no morning_session_close yet; what-if uses open-through-boundary trades.",
            },
        },
    }
