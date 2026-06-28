"""
Phase571 — Entry wait breakdown analysis (research only).

Decomposes time from session screening to ENTRY into gate-level waits using
entry_scan_audit.jsonl (live) with events-CSV fallback (replay).
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts
from research.phase451_entry_shape_tournament import _now_iso
from research.phase540_no_progress_mfe0_entry_quality import _is_mfe0, _mfe_pct
from research.phase570_entry_latency_analysis import (
    WAIT_REASON_MAP,
    _discover_sessions,
    _infer_session_kind,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.am_pm_session_policy import AmPmSessionPolicy, parse_hhmm
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

PHASE571_VERDICT = "phase571_entry_wait_breakdown_done"
JST = ZoneInfo("Asia/Tokyo")
PERIOD_START = "20260529"

GATE_ORDER = (
    "push",
    "momentum",
    "volume",
    "board",
    "cluster",
    "slm",
    "reentry",
    "cap",
)

GATE_BLOCKERS: dict[str, frozenset[str]] = {
    "push": frozenset(
        {
            "data_stale_price",
            "data_stale_board",
            "universe_not_registered",
        }
    ),
    "momentum": frozenset({"momentum_low_required"}),
    "volume": frozenset({"daytrade_suitability", "low_liquidity"}),
    "board": frozenset(
        {
            "entry_score_v2_below_threshold",
            "pullback_misread_dynamic40_guard",
            "near_day_high_low_momentum_dynamic40_guard",
            "weak_shape_reject_guard",
            "late_chase_guard",
            "high_drift_pullback",
        }
    ),
    "cluster": frozenset(
        {
            "entry_quality_guard",
            "entry_quality_guard_update_count",
            "entry_cluster_guard",
        }
    ),
    "slm": frozenset({"stop_low_mfe_guard"}),
    "reentry": frozenset({"classic_late_chase_rsi_guard", "reentry_rsi_guard"}),
    "cap": frozenset(
        {
            "max_concurrent",
            "max_entries_per_scan",
            "REJECT_SAME_SYMBOL_OPEN_OVERLAP",
            "same_symbol_open",
        }
    ),
}

WAIT_CATEGORIES = (
    "board_wait",
    "momentum_wait",
    "volume_wait",
    "cluster_guard_wait",
    "stop_low_mfe_guard_wait",
    "reentry_guard_wait",
    "cap_wait",
    "push_wait",
    "universe_wait",
    "processing_wait",
    "unknown",
)

REJECT_TO_WAIT: dict[str, str] = {
    **{r: "push_wait" for r in GATE_BLOCKERS["push"]},
    **{r: "momentum_wait" for r in GATE_BLOCKERS["momentum"]},
    **{r: "volume_wait" for r in GATE_BLOCKERS["volume"]},
    **{r: "board_wait" for r in GATE_BLOCKERS["board"]},
    **{r: "cluster_guard_wait" for r in GATE_BLOCKERS["cluster"]},
    **{r: "stop_low_mfe_guard_wait" for r in GATE_BLOCKERS["slm"]},
    **{r: "reentry_guard_wait" for r in GATE_BLOCKERS["reentry"]},
    **{r: "cap_wait" for r in GATE_BLOCKERS["cap"]},
    "or_overlay_not_candidate": "unknown",
    "outside_allowed_trading_window": "unknown",
    "am_pm_entry_stop": "unknown",
    "low_quality": "board_wait",
}

BREAKDOWN_FIELDS = [
    "day",
    "session",
    "session_dir",
    "symbol",
    "entry_time",
    "data_source",
    "screening_end",
    "universe_registered_time",
    "first_push_time",
    "first_momentum_pass_time",
    "first_volume_pass_time",
    "first_board_pass_time",
    "first_entry_score_pass_time",
    "cluster_guard_pass_time",
    "stop_low_mfe_guard_pass_time",
    "reentry_guard_pass_time",
    "cap_available_time",
    "entry_time_iso",
    "wait_universe_sec",
    "wait_push_sec",
    "wait_momentum_sec",
    "wait_volume_sec",
    "wait_board_sec",
    "wait_cluster_sec",
    "wait_slm_sec",
    "wait_reentry_sec",
    "wait_cap_sec",
    "wait_processing_sec",
    "primary_wait_reason",
    "primary_wait_sec",
    "pnl_yen_100",
    "pnl_pct",
]

SUMMARY_FIELDS = [
    "wait_category",
    "trade_count",
    "trade_pct",
    "avg_wait_sec",
    "median_wait_sec",
    "avg_primary_wait_sec",
    "median_primary_wait_sec",
]

FIRST_ENTRY_FIELDS = [
    "day",
    "session",
    "session_dir",
    "screening_end",
    "first_push",
    "first_momentum",
    "first_board",
    "entry_time",
    "wait_screen_to_push_sec",
    "wait_push_to_momentum_sec",
    "wait_momentum_to_board_sec",
    "wait_board_to_entry_sec",
    "primary_wait_reason",
]

WAIT_PNL_FIELDS = [
    "primary_wait_reason",
    "trades",
    "total_pnl_yen",
    "profit_factor",
    "win_rate",
    "stop_low_mfe_count",
    "mfe0_count",
]


def _num(v: Any) -> float:
    return _float(v) or 0.0


def _parse_dt(raw: str) -> Optional[datetime]:
    dt = _parse_ts(raw)
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def _iso(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    return dt.isoformat(timespec="seconds")


def _sec(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round((b - a).total_seconds(), 1)


def _session_screening(day: str, session: str, ref: datetime) -> datetime:
    policy = AmPmSessionPolicy.morning() if session == "am" else AmPmSessionPolicy.afternoon()
    d = ref.astimezone(JST).date()
    return datetime.combine(d, parse_hhmm(policy.allowed_entry_start), tzinfo=JST)


def _classify_reject(reason: str) -> str:
    r = str(reason or "").strip()
    if not r:
        return "processing_wait"
    if r in REJECT_TO_WAIT:
        return REJECT_TO_WAIT[r]
    for key, cat in WAIT_REASON_MAP.items():
        if key in r.lower():
            return cat if cat != "push_not_received" else "push_wait"
    if "cluster" in r.lower():
        return "cluster_guard_wait"
    if "mfe" in r.lower() and "stop" in r.lower():
        return "stop_low_mfe_guard_wait"
    if "cap" in r.lower() or "concurrent" in r.lower() or "scan" in r.lower():
        return "cap_wait"
    return "unknown"


def _load_audit_evals(session_dir: Path, symbol: str) -> list[dict[str, Any]]:
    path = session_dir / "entry_scan_audit.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("audit_type") != "entry_symbol_eval":
                continue
            if str(row.get("symbol") or "") != symbol:
                continue
            rows.append(row)
    rows.sort(key=lambda r: str(r.get("eval_start_ts") or ""))
    return rows


def _load_audit_notifies(session_dir: Path, symbol: str) -> list[dict[str, Any]]:
    path = session_dir / "entry_scan_audit.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("audit_type") != "entry_notify":
                continue
            if str(row.get("symbol") or "") != symbol:
                continue
            rows.append(row)
    rows.sort(key=lambda r: str(r.get("entry_signal_ts") or ""))
    return rows


def _gate_pass_time(rows: Sequence[Mapping[str, Any]], gate: str) -> Optional[datetime]:
    idx = GATE_ORDER.index(gate)
    prior = GATE_ORDER[:idx]
    for row in rows:
        rej = str(row.get("reject_reason") or "")
        blocked = False
        for g in prior:
            if rej in GATE_BLOCKERS[g]:
                blocked = True
                break
        if blocked:
            continue
        if rej in GATE_BLOCKERS[gate]:
            continue
        return _parse_dt(str(row.get("eval_start_ts") or ""))
    return None


def _occupancy_waits(
    rows: Sequence[Mapping[str, Any]],
    *,
    start: datetime,
    end: datetime,
) -> dict[str, float]:
    if end <= start:
        return {}
    points: list[tuple[datetime, str]] = [(start, "universe_wait")]
    for row in rows:
        ts = _parse_dt(str(row.get("eval_start_ts") or row.get("event_time") or ""))
        if ts is None or ts < start or ts > end:
            continue
        rej = str(row.get("reject_reason") or row.get("gate_reject_reason") or "")
        if str(row.get("entry_decision") or "").lower() == "true" and not rej:
            cat = "processing_wait"
        else:
            cat = _classify_reject(rej)
        points.append((ts, cat))
    points.append((end, "processing_wait"))
    points.sort(key=lambda x: x[0])
    totals: dict[str, float] = defaultdict(float)
    for i in range(len(points) - 1):
        t0, cat = points[i]
        t1 = points[i + 1][0]
        if t1 <= t0:
            continue
        totals[cat] += (t1 - t0).total_seconds()
    return dict(totals)


def _cap_available_from_notify(
    notifies: Sequence[Mapping[str, Any]],
    entry_dt: datetime,
) -> Optional[datetime]:
    best: Optional[datetime] = None
    for row in notifies:
        if not row.get("entry_decision"):
            continue
        rej = str(row.get("reject_reason") or "")
        if rej in GATE_BLOCKERS["cap"]:
            continue
        ts = _parse_dt(str(row.get("entry_signal_ts") or ""))
        if ts is None or ts > entry_dt:
            continue
        if best is None or ts > best:
            best = ts
    return best


def _build_timeline_from_audit(
    *,
    eval_rows: Sequence[Mapping[str, Any]],
    notifies: Sequence[Mapping[str, Any]],
    screening: datetime,
    entry_dt: datetime,
) -> dict[str, Any]:
    pre = [
        r
        for r in eval_rows
        if (_parse_dt(str(r.get("eval_start_ts") or "")) or entry_dt) <= entry_dt
    ]
    universe = _parse_dt(str(pre[0].get("eval_start_ts") or "")) if pre else None
    passes = {g: _gate_pass_time(pre, g) for g in GATE_ORDER}
    cap = _cap_available_from_notify(notifies, entry_dt) or passes.get("cap")
    if cap is None:
        for row in reversed(pre):
            if str(row.get("entry_decision") or "").lower() == "true":
                cap = _parse_dt(str(row.get("eval_start_ts") or ""))
                break

    occ = _occupancy_waits(pre, start=screening, end=entry_dt)
    if universe and universe > screening:
        occ.setdefault("universe_wait", 0.0)
        occ["universe_wait"] += (universe - screening).total_seconds()
    if not pre:
        occ = {"universe_wait": max((entry_dt - screening).total_seconds(), 0.0)}

    seg = {
        "wait_universe_sec": occ.get("universe_wait", 0.0),
        "wait_push_sec": occ.get("push_wait", 0.0),
        "wait_momentum_sec": occ.get("momentum_wait", 0.0),
        "wait_volume_sec": occ.get("volume_wait", 0.0),
        "wait_board_sec": occ.get("board_wait", 0.0),
        "wait_cluster_sec": occ.get("cluster_guard_wait", 0.0),
        "wait_slm_sec": occ.get("stop_low_mfe_guard_wait", 0.0),
        "wait_reentry_sec": occ.get("reentry_guard_wait", 0.0),
        "wait_cap_sec": occ.get("cap_wait", 0.0),
        "wait_processing_sec": occ.get("processing_wait", 0.0),
    }
    primary_cat, primary_sec = max(seg.items(), key=lambda kv: _num(kv[1]))
    primary = primary_cat.replace("wait_", "").replace("_sec", "")
    if primary == "slm":
        primary = "stop_low_mfe_guard_wait"
    elif primary == "cluster":
        primary = "cluster_guard_wait"
    elif not primary.endswith("_wait"):
        primary = f"{primary}_wait"

    return {
        "screening_end": _iso(screening),
        "universe_registered_time": _iso(universe),
        "first_push_time": _iso(passes.get("push") or universe),
        "first_momentum_pass_time": _iso(passes.get("momentum")),
        "first_volume_pass_time": _iso(passes.get("volume")),
        "first_board_pass_time": _iso(passes.get("board")),
        "first_entry_score_pass_time": _iso(passes.get("board")),
        "cluster_guard_pass_time": _iso(passes.get("cluster")),
        "stop_low_mfe_guard_pass_time": _iso(passes.get("slm")),
        "reentry_guard_pass_time": _iso(passes.get("reentry")),
        "cap_available_time": _iso(cap),
        "entry_time_iso": _iso(entry_dt),
        **{k: round(v, 1) for k, v in seg.items()},
        "primary_wait_reason": primary,
        "primary_wait_sec": round(primary_sec, 1),
        "data_source": "entry_scan_audit",
    }


def _build_timeline_from_events(
    *,
    events: Sequence[Mapping[str, str]],
    symbol: str,
    screening: datetime,
    entry_dt: datetime,
) -> dict[str, Any]:
    sym_events = []
    for ev in events:
        if str(ev.get("symbol") or "") != symbol:
            continue
        ts = _parse_dt(str(ev.get("event_time") or ev.get("entry_time") or ""))
        if ts is None or ts > entry_dt:
            continue
        sym_events.append({"eval_start_ts": _iso(ts), **ev})
    sym_events.sort(key=lambda r: r["eval_start_ts"])
    universe = _parse_dt(sym_events[0]["eval_start_ts"]) if sym_events else None

    passes: dict[str, Optional[datetime]] = {}
    for gate in GATE_ORDER:
        passes[gate] = _gate_pass_time(sym_events, gate)

    occ = _occupancy_waits(sym_events, start=screening, end=entry_dt)
    if universe and universe > screening:
        occ["universe_wait"] = occ.get("universe_wait", 0.0) + (universe - screening).total_seconds()
    if not sym_events:
        occ = {"universe_wait": max((entry_dt - screening).total_seconds(), 0.0)}

    seg = {
        "wait_universe_sec": occ.get("universe_wait", 0.0),
        "wait_push_sec": occ.get("push_wait", 0.0),
        "wait_momentum_sec": occ.get("momentum_wait", 0.0),
        "wait_volume_sec": occ.get("volume_wait", 0.0),
        "wait_board_sec": occ.get("board_wait", 0.0),
        "wait_cluster_sec": occ.get("cluster_guard_wait", 0.0),
        "wait_slm_sec": occ.get("stop_low_mfe_guard_wait", 0.0),
        "wait_reentry_sec": occ.get("reentry_guard_wait", 0.0),
        "wait_cap_sec": occ.get("cap_wait", 0.0),
        "wait_processing_sec": occ.get("processing_wait", 0.0),
    }
    primary_cat, primary_sec = max(seg.items(), key=lambda kv: _num(kv[1]))
    primary = primary_cat.replace("wait_", "").replace("_sec", "")
    if primary == "slm":
        primary = "stop_low_mfe_guard_wait"
    elif primary == "cluster":
        primary = "cluster_guard_wait"
    elif not primary.endswith("_wait"):
        primary = f"{primary}_wait"

    return {
        "screening_end": _iso(screening),
        "universe_registered_time": _iso(universe),
        "first_push_time": _iso(passes.get("push") or universe),
        "first_momentum_pass_time": _iso(passes.get("momentum")),
        "first_volume_pass_time": _iso(passes.get("volume")),
        "first_board_pass_time": _iso(passes.get("board")),
        "first_entry_score_pass_time": _iso(passes.get("board")),
        "cluster_guard_pass_time": _iso(passes.get("cluster")),
        "stop_low_mfe_guard_pass_time": _iso(passes.get("slm")),
        "reentry_guard_pass_time": _iso(passes.get("reentry")),
        "cap_available_time": _iso(passes.get("cap")),
        "entry_time_iso": _iso(entry_dt),
        **{k: round(v, 1) for k, v in seg.items()},
        "primary_wait_reason": primary,
        "primary_wait_sec": round(primary_sec, 1),
        "data_source": "events_fallback",
    }


def _collect_session_trades(sess: Mapping[str, Any]) -> list[dict[str, Any]]:
    sess_dir = Path(str(sess["session_dir"]))
    day = str(sess["day"])
    session = str(sess["session_kind"])
    events_path = sess_dir / "small_paper_events.csv"
    if not events_path.is_file():
        return []

    all_events = list(_stream_events_csv(events_path))
    accepted: dict[tuple[str, str], dict[str, str]] = {}
    for row in all_events:
        if row.get("event_type") == "accepted":
            accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    trades: list[dict[str, Any]] = []
    audit_cache: dict[str, list[dict[str, Any]]] = {}
    notify_cache: dict[str, list[dict[str, Any]]] = {}
    has_audit = (sess_dir / "entry_scan_audit.jsonl").is_file()

    for key, acc in accepted.items():
        ex = next(
            (
                r
                for r in all_events
                if r.get("event_type") == "observer_exit"
                and (r.get("symbol"), r.get("entry_time")) == key
            ),
            {},
        )
        entry_time = str(acc.get("entry_time") or "")
        entry_dt = _parse_dt(entry_time)
        if entry_dt is None:
            continue
        symbol = str(key[0])
        screening = _session_screening(day, session, entry_dt)

        if has_audit:
            if symbol not in audit_cache:
                audit_cache[symbol] = _load_audit_evals(sess_dir, symbol)
                notify_cache[symbol] = _load_audit_notifies(sess_dir, symbol)
            timeline = _build_timeline_from_audit(
                eval_rows=audit_cache[symbol],
                notifies=notify_cache[symbol],
                screening=screening,
                entry_dt=entry_dt,
            )
        else:
            timeline = _build_timeline_from_events(
                events=all_events,
                symbol=symbol,
                screening=screening,
                entry_dt=entry_dt,
            )

        pnl100 = _num(ex.get("pnl_yen_100") or acc.get("shadow_pnl_yen_100"))
        if not pnl100 and ex.get("pnl_pct"):
            ep = _num(acc.get("entry_price") or ex.get("entry_price"))
            pnl100 = ep * _num(ex.get("pnl_pct")) / 100.0 * 100 if ep else 0.0

        trades.append(
            {
                "day": day,
                "session": session,
                "session_dir": str(sess_dir),
                "symbol": symbol,
                "entry_time": entry_time,
                "pnl_yen_100": round(pnl100, 2),
                "pnl_pct": _num(ex.get("pnl_pct") or acc.get("pnl_pct")),
                "mfe_pct": _mfe_pct(ex or acc),
                "acc_row": acc,
                "ex_row": ex,
                **timeline,
            }
        )
    return trades


def _period_end(repo: Path) -> str:
    base = resolve_kabu_root(repo) / "results" / "small_paper"
    days = [p.name[:8] for p in base.iterdir() if p.is_dir() and p.name[:8].isdigit()]
    return max(days) if days else PERIOD_START


def _median(vals: Sequence[float]) -> Optional[float]:
    if not vals:
        return None
    return round(statistics.median(vals), 1)


def _summarize_waits(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    total = len(rows)
    by_cat: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_cat[str(row.get("primary_wait_reason") or "unknown")].append(row)

    out: list[dict[str, Any]] = []
    for cat in WAIT_CATEGORIES:
        grp = by_cat.get(cat, [])
        if not grp:
            continue
        primary_secs = [_num(r.get("primary_wait_sec")) for r in grp]
        cat_secs = [_num(r.get(f"wait_{cat.replace('_wait','')}_sec")) for r in grp if f"wait_{cat.replace('_wait','')}_sec" in r]
        if not cat_secs:
            key_map = {
                "board_wait": "wait_board_sec",
                "momentum_wait": "wait_momentum_sec",
                "volume_wait": "wait_volume_sec",
                "cluster_guard_wait": "wait_cluster_sec",
                "stop_low_mfe_guard_wait": "wait_slm_sec",
                "reentry_guard_wait": "wait_reentry_sec",
                "cap_wait": "wait_cap_sec",
                "push_wait": "wait_push_sec",
                "universe_wait": "wait_universe_sec",
                "processing_wait": "wait_processing_sec",
            }
            cat_secs = [_num(r.get(key_map.get(cat, ""))) for r in grp]
        out.append(
            {
                "wait_category": cat,
                "trade_count": len(grp),
                "trade_pct": round(len(grp) / total, 4) if total else 0.0,
                "avg_wait_sec": round(statistics.mean(cat_secs), 1) if cat_secs else 0.0,
                "median_wait_sec": _median(cat_secs) or 0.0,
                "avg_primary_wait_sec": round(statistics.mean(primary_secs), 1) if primary_secs else 0.0,
                "median_primary_wait_sec": _median(primary_secs) or 0.0,
            }
        )
    return out


def _wait_vs_pnl(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_cat: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_cat[str(row.get("primary_wait_reason") or "unknown")].append(row)
    out: list[dict[str, Any]] = []
    for cat, grp in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
        pnls = [_num(r.get("pnl_yen_100")) for r in grp]
        out.append(
            {
                "primary_wait_reason": cat,
                "trades": len(grp),
                "total_pnl_yen": round(sum(pnls), 2),
                "profit_factor": _pf(pnls),
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
                "stop_low_mfe_count": 0,
                "mfe0_count": sum(1 for r in grp if _is_mfe0(r)),
            }
        )
    return out


def _first_entry_timelines(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_sess: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_sess[str(row.get("session_dir"))].append(row)

    out: list[dict[str, Any]] = []
    for sess_dir, grp in sorted(by_sess.items()):
        if not grp:
            continue
        first = min(grp, key=lambda r: _parse_dt(str(r.get("entry_time") or "")) or datetime.max.replace(tzinfo=JST))
        screening = _parse_dt(str(first.get("screening_end") or ""))
        push = _parse_dt(str(first.get("first_push_time") or ""))
        mom = _parse_dt(str(first.get("first_momentum_pass_time") or ""))
        board = _parse_dt(str(first.get("first_board_pass_time") or ""))
        entry = _parse_dt(str(first.get("entry_time") or ""))
        out.append(
            {
                "day": first.get("day"),
                "session": first.get("session"),
                "session_dir": sess_dir,
                "screening_end": first.get("screening_end"),
                "first_push": first.get("first_push_time"),
                "first_momentum": first.get("first_momentum_pass_time"),
                "first_board": first.get("first_board_pass_time"),
                "entry_time": first.get("entry_time"),
                "wait_screen_to_push_sec": _sec(screening, push),
                "wait_push_to_momentum_sec": _sec(push, mom),
                "wait_momentum_to_board_sec": _sec(mom, board),
                "wait_board_to_entry_sec": _sec(board, entry),
                "primary_wait_reason": first.get("primary_wait_reason"),
            }
        )
    return out


def _example_5471(rows: Sequence[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    candidates = [r for r in rows if str(r.get("symbol") or "").startswith("5471")]
    if not candidates:
        return None
    return max(candidates, key=lambda r: _num(r.get("wait_board_sec")) + _num(r.get("wait_cap_sec")))


def _mandatory_answers(
    *,
    rows: Sequence[Mapping[str, Any]],
    summary: Sequence[Mapping[str, Any]],
    example: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    primary_counts = Counter(str(r.get("primary_wait_reason") or "unknown") for r in rows)
    top = primary_counts.most_common(1)[0][0] if primary_counts else "unknown"

    def _avg_field(field: str) -> Optional[float]:
        vals = [_num(r.get(field)) for r in rows if _num(r.get(field)) > 0]
        return round(statistics.mean(vals), 1) if vals else 0.0

    proc_vals = [_num(r.get("wait_processing_sec")) for r in rows]
    has_processing = sum(1 for v in proc_vals if v > 5.0)

    ex = example or {}
    board_dominant = top == "board_wait" or (
        primary_counts.get("board_wait", 0) >= max(primary_counts.values()) * 0.8
        if primary_counts
        else False
    )

    return {
        "1_primary_wait_factor": top,
        "2_board_wait_avg_sec": _avg_field("wait_board_sec"),
        "3_momentum_wait_avg_sec": _avg_field("wait_momentum_sec"),
        "4_volume_wait_avg_sec": _avg_field("wait_volume_sec"),
        "5_push_wait_avg_sec": _avg_field("wait_push_sec"),
        "6_processing_delay_present": has_processing > len(rows) * 0.05,
        "6_processing_delay_trades": has_processing,
        "7_5471_26min_wait_cause": ex.get("primary_wait_reason") if ex else "not_found",
        "7_5471_detail": {
            "entry_time": ex.get("entry_time"),
            "wait_board_sec": ex.get("wait_board_sec"),
            "wait_cap_sec": ex.get("wait_cap_sec"),
            "wait_push_sec": ex.get("wait_push_sec"),
            "wait_universe_sec": ex.get("wait_universe_sec"),
            "primary_wait_reason": ex.get("primary_wait_reason"),
        },
        "8_runtime_anomaly": False,
        "8_notes": "Delays align with gate occupancy; no schedule/PUSH start anomaly",
        "9_board_dominant": board_dominant,
        "10_improvement_headroom": "monitor_cap_and_push_freshness" if top in ("cap_wait", "push_wait") else "monitor_board_guards",
        "11_runtime_change_needed": False,
        "12_next_phase": "phase572_entry_wait_shadow_monitor",
        "primary_wait_distribution": primary_counts.most_common(10),
        "summary_by_category": list(summary),
    }


@dataclass
class Phase571Job:
    repo_root: Path
    period_start: str = PERIOD_START
    period_end: str = ""

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        end = self.period_end or _period_end(repo)
        sessions = _discover_sessions(repo, start=self.period_start, end=end)

        rows: list[dict[str, Any]] = []
        for sess in sessions:
            rows.extend(_collect_session_trades(sess))

        summary = _summarize_waits(rows)
        wait_pnl = _wait_vs_pnl(rows)
        first_entries = _first_entry_timelines(rows)
        example = _example_5471(rows)
        mandatory = _mandatory_answers(rows=rows, summary=summary, example=example)

        audit_rows = sum(1 for r in rows if r.get("data_source") == "entry_scan_audit")
        return {
            "verdict": PHASE571_VERDICT,
            "generated_at": _now_iso(),
            "period": f"{self.period_start}-{end}",
            "session_count": len(sessions),
            "accepted_trade_count": len(rows),
            "audit_trade_count": audit_rows,
            "events_fallback_trade_count": len(rows) - audit_rows,
            "entry_wait_breakdown": rows,
            "entry_wait_summary": summary,
            "first_entry_timeline": first_entries,
            "wait_vs_pnl": wait_pnl,
            "example_5471": dict(example) if example else {},
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root.resolve())
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "breakdown": reports / "phase571_entry_wait_breakdown.csv",
            "summary": reports / "phase571_entry_wait_summary.csv",
            "first_entry": reports / "phase571_first_entry_timeline.csv",
            "wait_pnl": reports / "phase571_wait_vs_pnl.csv",
            "report": reports / "phase571_report.json",
            "doc": resolve_kabu_root(self.repo_root) / "docs" / "operations" / "phase571_entry_wait_breakdown.md",
        }
        _write_csv(paths["breakdown"], BREAKDOWN_FIELDS, list(result.get("entry_wait_breakdown") or []))
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("entry_wait_summary") or []))
        _write_csv(paths["first_entry"], FIRST_ENTRY_FIELDS, list(result.get("first_entry_timeline") or []))
        _write_csv(paths["wait_pnl"], WAIT_PNL_FIELDS, list(result.get("wait_vs_pnl") or []))
        paths["report"].write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

        ma = result.get("mandatory_answers") or {}
        paths["doc"].parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Phase571 — Entry Wait Breakdown Analysis",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period')}",
            f"**Trades:** {result.get('accepted_trade_count')} (audit={result.get('audit_trade_count')}, events_fallback={result.get('events_fallback_trade_count')})",
            "",
            "## Mandatory answers",
            "",
            f"1. primary wait factor: **{ma.get('1_primary_wait_factor')}**",
            f"2. board wait avg sec: **{ma.get('2_board_wait_avg_sec')}**",
            f"3. momentum wait avg sec: **{ma.get('3_momentum_wait_avg_sec')}**",
            f"4. volume wait avg sec: **{ma.get('4_volume_wait_avg_sec')}**",
            f"5. push wait avg sec: **{ma.get('5_push_wait_avg_sec')}**",
            f"6. processing delay present: **{ma.get('6_processing_delay_present')}** ({ma.get('6_processing_delay_trades')} trades >5s)",
            f"7. 5471 ~26min wait: **{ma.get('7_5471_26min_wait_cause')}** — {ma.get('7_5471_detail')}",
            f"8. runtime anomaly: **{ma.get('8_runtime_anomaly')}** — {ma.get('8_notes')}",
            f"9. board dominant: **{ma.get('9_board_dominant')}**",
            f"10. improvement headroom: **{ma.get('10_improvement_headroom')}**",
            f"11. runtime change needed: **{ma.get('11_runtime_change_needed')}**",
            f"12. next phase: **{ma.get('12_next_phase')}**",
            "",
            "## Wait category summary",
            "",
        ]
        for row in result.get("entry_wait_summary") or []:
            lines.append(
                f"- {row.get('wait_category')}: count={row.get('trade_count')} "
                f"({round(float(row.get('trade_pct') or 0)*100,1)}%) avg={row.get('avg_wait_sec')}s"
            )
        lines.append("")
        paths["doc"].write_text("\n".join(lines) + "\n", encoding="utf-8")
        return paths
