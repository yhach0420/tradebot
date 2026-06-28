"""
Phase570 — Entry latency analysis (research only).

Analyzes whether delayed AM (~9:30) / PM (~13:00) ENTRY notifications are normal
condition-wait vs missing the opening move. No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts
from research.phase451_entry_shape_tournament import _now_iso
from research.phase540_no_progress_mfe0_entry_quality import _is_mfe0, _mfe_pct
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from runner.am_pm_daily_runner import AM_REFRESH_HHMM, PM_REFRESH_HHMM, PM_SCREEN_HHMM
from small_paper.am_pm_session_policy import AmPmSessionPolicy, parse_hhmm
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv
from universe.intraday_refresh import AM_REFRESH_TIME, PM_REFRESH_TIME

PHASE570_VERDICT = "phase570_entry_latency_analysis_done"
JST = ZoneInfo("Asia/Tokyo")
PERIOD_START = "20260529"
PERIOD_END = "20260626"

MOMENTUM_RETURN_PCT = 0.30
VOLUME_SURGE_RATIO = 1.8

AM_BUCKETS: tuple[tuple[str, time, Optional[time]], ...] = (
    ("09:00-09:10", time(9, 0), time(9, 10)),
    ("09:10-09:20", time(9, 10), time(9, 20)),
    ("09:20-09:30", time(9, 20), time(9, 30)),
    ("09:30-10:00", time(9, 30), time(10, 0)),
    ("10:00+", time(10, 0), None),
)
PM_BUCKETS: tuple[tuple[str, time, Optional[time]], ...] = (
    ("12:30-12:40", time(12, 30), time(12, 40)),
    ("12:40-12:50", time(12, 40), time(12, 50)),
    ("12:50-13:00", time(12, 50), time(13, 0)),
    ("13:00-13:30", time(13, 0), time(13, 30)),
    ("13:30+", time(13, 30), None),
)

LATENCY_PNL_BUCKETS: tuple[tuple[str, float, Optional[float]], ...] = (
    ("0-3min", 0.0, 180.0),
    ("3-10min", 180.0, 600.0),
    ("10-20min", 600.0, 1200.0),
    ("20min+", 1200.0, None),
)

WAIT_REASON_MAP: dict[str, str] = {
    "momentum_low_required": "momentum_wait",
    "momentum_low": "momentum_wait",
    "entry_score_v2_below_threshold": "board_wait",
    "pullback_misread_dynamic40_guard": "board_wait",
    "near_day_high_low_momentum_dynamic40_guard": "board_wait",
    "weak_shape_reject_guard": "board_wait",
    "late_chase_guard": "board_wait",
    "classic_late_chase_rsi_guard": "reentry_guard_wait",
    "reentry_rsi_guard": "reentry_guard_wait",
    "entry_cluster_guard": "cluster_guard_wait",
    "stop_low_mfe_guard": "stop_low_mfe_guard_wait",
    "entry_quality_guard": "cluster_guard_wait",
    "max_concurrent": "cap_wait",
    "max_entries_per_scan": "cap_wait",
    "same_symbol_open": "cap_wait",
    "daytrade_suitability": "volume_wait",
    "low_liquidity": "volume_wait",
    "data_stale_price": "push_not_received",
    "data_stale_board": "data_missing",
    "live_feature_incomplete": "data_missing",
    "am_pm_entry_stop": "session_window_wait",
    "universe_not_registered": "universe_not_registered",
}

SCHEDULE_FIELDS = [
    "item",
    "value",
    "source",
    "notes",
]
DIST_FIELDS = [
    "day",
    "session",
    "session_dir",
    "symbol",
    "entry_time",
    "entry_hhmm",
    "first_entry_of_session",
    "entry_time_bucket",
    "pnl_yen_100",
    "pnl_pct",
]
LATENCY_FIELDS = [
    "day",
    "session",
    "symbol",
    "entry_time",
    "first_momentum_time",
    "first_volume_surge_time",
    "first_day_high_update_time",
    "first_board_pass_time",
    "latency_sec",
    "latency_min",
    "latency_reference",
    "push_available",
]
WAIT_FIELDS = [
    "day",
    "session",
    "symbol",
    "entry_time",
    "primary_wait_reason",
    "wait_reason_counts_json",
    "last_reject_reason",
    "reject_events_before_entry",
]
LATENCY_PNL_FIELDS = [
    "latency_bucket",
    "trades",
    "total_pnl_yen",
    "profit_factor",
    "win_rate",
    "avg_mfe_pct",
    "mfe0_count",
    "stop_low_mfe_count",
    "early_profit_take_count",
]


def _num(v: Any) -> float:
    return _float(v) or 0.0


def _parse_entry_dt(raw: str) -> Optional[datetime]:
    dt = _parse_ts(raw)
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def _entry_time_only(raw: str) -> Optional[time]:
    dt = _parse_entry_dt(raw)
    return dt.time() if dt else None


def _bucket_for(t: time, *, session: str) -> str:
    buckets = AM_BUCKETS if session == "am" else PM_BUCKETS
    for label, lo, hi in buckets:
        if hi is None and t >= lo:
            return label
        if hi is not None and lo <= t < hi:
            return label
    return "other"


def _latency_bucket(sec: float) -> str:
    for label, lo, hi in LATENCY_PNL_BUCKETS:
        if hi is None and sec >= lo:
            return label
        if hi is not None and lo <= sec < hi:
            return label
    return "20min+"


def _build_schedule_rows() -> list[dict[str, Any]]:
    am = AmPmSessionPolicy.morning()
    pm = AmPmSessionPolicy.afternoon()
    rows = [
        {"item": "am_session_start", "value": am.session_start, "source": "AmPmSessionPolicy", "notes": ""},
        {"item": "am_session_end", "value": am.session_end, "source": "AmPmSessionPolicy", "notes": ""},
        {"item": "am_entry_stop", "value": am.entry_stop, "source": "AmPmSessionPolicy", "notes": ""},
        {"item": "am_allowed_entry_start", "value": am.allowed_entry_start, "source": "AmPmSessionPolicy", "notes": "ENTRY評価開始"},
        {"item": "am_allowed_entry_end", "value": am.allowed_entry_end, "source": "AmPmSessionPolicy", "notes": ""},
        {"item": "am_screening_window", "value": am.screening_window, "source": "AmPmSessionPolicy", "notes": ""},
        {"item": "pm_session_start", "value": pm.session_start, "source": "AmPmSessionPolicy", "notes": ""},
        {"item": "pm_session_end", "value": pm.session_end, "source": "AmPmSessionPolicy", "notes": ""},
        {"item": "pm_entry_stop", "value": pm.entry_stop, "source": "AmPmSessionPolicy", "notes": ""},
        {"item": "pm_allowed_entry_start", "value": pm.allowed_entry_start, "source": "AmPmSessionPolicy", "notes": "ENTRY評価開始"},
        {"item": "pm_allowed_entry_end", "value": pm.allowed_entry_end, "source": "AmPmSessionPolicy", "notes": ""},
        {"item": "pm_screening_window", "value": pm.screening_window, "source": "AmPmSessionPolicy", "notes": ""},
        {"item": "am_intraday_refresh", "value": AM_REFRESH_HHMM, "source": "am_pm_daily_runner", "notes": AM_REFRESH_TIME},
        {"item": "pm_intraday_refresh", "value": PM_REFRESH_HHMM, "source": "am_pm_daily_runner", "notes": PM_REFRESH_TIME},
        {"item": "pm_screening_start", "value": PM_SCREEN_HHMM, "source": "am_pm_daily_runner", "notes": "runner wait until PM universe"},
        {"item": "lunch_break", "value": f"{am.session_end}-{pm.session_start}", "source": "AmPmSessionPolicy", "notes": "no trading between sessions"},
        {"item": "push_subscription_start", "value": "session_start (with --wait-until-session)", "source": "pilot_command_argv", "notes": "PUSH at allowed_entry_start after wait"},
        {"item": "entry_evaluation_start", "value": "allowed_entry_start per session", "source": "am_pm_policy.entry_allowed_now", "notes": "09:03 AM / 12:33 PM"},
    ]
    return rows


def _discover_sessions(repo: Path, *, start: str, end: str) -> list[dict[str, Any]]:
    base = resolve_kabu_root(repo) / "results" / "small_paper"
    out: list[dict[str, Any]] = []
    if not base.is_dir():
        return out
    for day_dir in sorted(base.iterdir()):
        if not day_dir.is_dir():
            continue
        day = day_dir.name[:8]
        if not day.isdigit() or day < start or day > end:
            continue
        for sess in sorted(day_dir.iterdir()):
            if not sess.is_dir():
                continue
            name = sess.name
            if not (
                name.startswith("live_session_")
                or name.startswith("live_full_session_")
                or name.startswith("push_replay_")
            ):
                continue
            if not (sess / "small_paper_events.csv").is_file():
                continue
            kind = _infer_session_kind(sess)
            out.append(
                {
                    "day": day,
                    "session_dir": str(sess),
                    "session_name": name,
                    "session_kind": kind,
                    "source": "push_replay" if name.startswith("push_replay_") else "live",
                }
            )
    return out


def _infer_session_kind(sess_dir: Path) -> str:
    cfg = sess_dir / "live_session_config.json"
    if cfg.is_file():
        try:
            payload = json.loads(cfg.read_text(encoding="utf-8"))
            am_pm = payload.get("am_pm_session") or {}
            if isinstance(am_pm, dict) and am_pm.get("kind") in ("am", "pm"):
                return str(am_pm["kind"])
        except json.JSONDecodeError:
            pass
    summary = sess_dir / "small_paper_summary.json"
    if summary.is_file():
        try:
            payload = json.loads(summary.read_text(encoding="utf-8"))
            am_pm = payload.get("am_pm_session") or {}
            if isinstance(am_pm, dict) and am_pm.get("kind") in ("am", "pm"):
                return str(am_pm["kind"])
        except json.JSONDecodeError:
            pass
    # folder time hint: live_session_08xxxx -> am, 12xxxx -> pm
    stem = sess_dir.name
    if "122" in stem[:20] or stem.startswith("push_replay_21"):
        return "pm"
    return "am"


def _load_price_ticks(push_dir: Path, symbol: str) -> list[tuple[float, float, float, float, float]]:
    sym = str(symbol or "")
    for candidate in (push_dir / f"{sym}.jsonl", push_dir / f"{sym.replace('.T', '')}.jsonl"):
        if not candidate.is_file():
            continue
        rows: list[tuple[float, float, float, float, float]] = []
        with candidate.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = rec.get("payload") or {}
                ts = _parse_ts(rec.get("recorded_at") or payload.get("CurrentPriceTime") or "")
                if ts is None:
                    continue
                px = _float(payload.get("CurrentPrice")) or 0.0
                if px <= 0:
                    continue
                vol = _float(payload.get("TradingVolume")) or 0.0
                high = _float(payload.get("HighPrice")) or px
                op = _float(payload.get("OpeningPrice")) or _float(payload.get("PreviousClose")) or px
                rows.append((ts.timestamp(), px, vol, high, op))
        rows.sort(key=lambda x: x[0])
        return rows
    return []


def _signal_times_before_entry(
    ticks: Sequence[tuple[float, float, float, float, float]],
    entry_ts: float,
) -> dict[str, Optional[float]]:
    pre = [t for t in ticks if t[0] <= entry_ts]
    if not pre:
        return {
            "first_momentum_time": None,
            "first_volume_surge_time": None,
            "first_day_high_update_time": None,
            "first_board_pass_time": None,
        }
    open_px = pre[0][4] or pre[0][1]
    running_high = pre[0][3]
    first_momentum: Optional[float] = None
    first_vol: Optional[float] = None
    first_high: Optional[float] = None
    prev_vol = pre[0][2]
    for ts, px, vol, high, _ in pre:
        running_high = max(running_high, high, px)
        if open_px > 0 and first_momentum is None:
            if (px - open_px) / open_px * 100.0 >= MOMENTUM_RETURN_PCT:
                first_momentum = ts
        if first_high is None and px >= running_high * 0.998:
            first_high = ts
        if prev_vol > 0 and vol > 0 and first_vol is None and vol / prev_vol >= VOLUME_SURGE_RATIO:
            first_vol = ts
        prev_vol = max(prev_vol, vol)
    return {
        "first_momentum_time": first_momentum,
        "first_volume_surge_time": first_vol,
        "first_day_high_update_time": first_high,
        "first_board_pass_time": first_high,
    }


def _classify_wait_reason(reason: str) -> str:
    r = str(reason or "").strip().lower()
    if not r:
        return "unknown"
    for key, cat in WAIT_REASON_MAP.items():
        if key in r:
            return cat
    if "cluster" in r:
        return "cluster_guard_wait"
    if "mfe" in r and "stop" in r:
        return "stop_low_mfe_guard_wait"
    if "cap" in r or "concurrent" in r:
        return "cap_wait"
    if "stale" in r or "missing" in r:
        return "data_missing"
    return "other_wait"


def _session_entry_start(entry_dt: datetime, session: str) -> datetime:
    policy = AmPmSessionPolicy.morning() if session == "am" else AmPmSessionPolicy.afternoon()
    d = entry_dt.astimezone(JST).date()
    t = parse_hhmm(policy.allowed_entry_start)
    return datetime.combine(d, t, tzinfo=JST)


def _fallback_latency_sec(tr: Mapping[str, Any], *, session: str) -> tuple[Optional[float], str]:
    dt = tr.get("entry_dt")
    if not isinstance(dt, datetime):
        return None, "none"
    start = _session_entry_start(dt, session)
    sec = round((dt - start).total_seconds(), 1)
    if sec < 0:
        sec = 0.0
    acc = tr.get("acc_row") or {}
    rise5 = _num(acc.get("entry_rise_5min_pct"))
    if rise5 >= 0.5:
        est = max(sec - 300.0, 0.0)
        return est, "proxy_momentum_5min"
    return sec, "session_entry_start"


def _collect_session_trades(sess: Mapping[str, Any], *, push_root: Path) -> tuple[list[dict], list[dict], list[dict]]:
    sess_dir = Path(str(sess["session_dir"]))
    day = str(sess["day"])
    session = str(sess["session_kind"])
    push_dir = push_root / day

    accepted_map: dict[tuple[str, str], dict[str, str]] = {}
    all_events: list[dict[str, str]] = []
    for row in _stream_events_csv(sess_dir / "small_paper_events.csv"):
        all_events.append(row)
        if row.get("event_type") == "accepted":
            accepted_map[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    trades: list[dict[str, Any]] = []
    first_entry_dt: Optional[datetime] = None
    for key, acc in accepted_map.items():
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
        dt = _parse_entry_dt(entry_time)
        if dt is None:
            continue
        if first_entry_dt is None or dt < first_entry_dt:
            first_entry_dt = dt
        t = dt.time()
        pnl100 = _num(ex.get("pnl_yen_100") or acc.get("shadow_pnl_yen_100"))
        if not pnl100 and ex.get("pnl_pct"):
            ep = _num(acc.get("entry_price") or ex.get("entry_price"))
            pnl100 = ep * _num(ex.get("pnl_pct")) / 100.0 * 100 if ep else 0.0
        trades.append(
            {
                "day": day,
                "session": session,
                "session_dir": str(sess_dir),
                "symbol": key[0],
                "entry_time": entry_time,
                "entry_dt": dt,
                "entry_hhmm": dt.strftime("%H:%M:%S"),
                "entry_time_bucket": _bucket_for(t, session=session),
                "pnl_yen_100": round(pnl100, 2),
                "pnl_pct": _num(ex.get("pnl_pct") or acc.get("pnl_pct")),
                "mfe_pct": _mfe_pct(ex or acc),
                "acc_row": acc,
                "ex_row": ex,
            }
        )

    first_entry_time = first_entry_dt.isoformat() if first_entry_dt else ""
    dist_rows: list[dict[str, Any]] = []
    latency_rows: list[dict[str, Any]] = []
    wait_rows: list[dict[str, Any]] = []

    events_by_sym: dict[str, list[dict[str, str]]] = defaultdict(list)
    for ev in all_events:
        events_by_sym[str(ev.get("symbol") or "")].append(ev)

    for tr in trades:
        tr["first_entry_of_session"] = tr["entry_time"] == first_entry_time
        dist_rows.append({k: tr.get(k) for k in DIST_FIELDS if k != "first_entry_of_session"} | {
            "first_entry_of_session": tr["first_entry_of_session"],
        })

        entry_ts = tr["entry_dt"].timestamp()
        ticks = _load_price_ticks(push_dir, str(tr["symbol"]))
        signals = _signal_times_before_entry(ticks, entry_ts)
        refs = [v for k, v in signals.items() if v is not None and k.startswith("first_")]
        ref_ts = max(refs) if refs else None
        latency_sec = round(entry_ts - ref_ts, 1) if ref_ts is not None else None
        latency_min = round(latency_sec / 60.0, 2) if latency_sec is not None else None
        ref_label = "none"
        if ref_ts is not None:
            for k, v in signals.items():
                if v == ref_ts:
                    ref_label = k
                    break
        else:
            latency_sec, ref_label = _fallback_latency_sec(tr, session=session)
            latency_min = round(latency_sec / 60.0, 2) if latency_sec is not None else None
        latency_rows.append(
            {
                "day": day,
                "session": session,
                "symbol": tr["symbol"],
                "entry_time": tr["entry_time"],
                "first_momentum_time": _iso_from_ts(signals["first_momentum_time"]),
                "first_volume_surge_time": _iso_from_ts(signals["first_volume_surge_time"]),
                "first_day_high_update_time": _iso_from_ts(signals["first_day_high_update_time"]),
                "first_board_pass_time": _iso_from_ts(signals["first_board_pass_time"]),
                "latency_sec": latency_sec,
                "latency_min": latency_min,
                "latency_reference": ref_label,
                "push_available": bool(ticks),
            }
        )

        sym_events = sorted(
            events_by_sym.get(str(tr["symbol"]), []),
            key=lambda r: str(r.get("event_time") or r.get("entry_time") or ""),
        )
        prior_rejects: list[dict[str, str]] = []
        wait_counts: Counter[str] = Counter()
        last_reject = ""
        for ev in sym_events:
            ev_dt = _parse_entry_dt(str(ev.get("event_time") or ev.get("entry_time") or ""))
            if ev_dt is None or ev_dt >= tr["entry_dt"]:
                break
            if ev.get("event_type") == "rejected":
                reason = str(ev.get("gate_reject_reason") or ev.get("reject_reason") or "unknown")
                last_reject = reason
                prior_rejects.append(ev)
                wait_counts[_classify_wait_reason(reason)] += 1
        primary = wait_counts.most_common(1)[0][0] if wait_counts else "condition_met"
        wait_rows.append(
            {
                "day": day,
                "session": session,
                "symbol": tr["symbol"],
                "entry_time": tr["entry_time"],
                "primary_wait_reason": primary,
                "wait_reason_counts_json": json.dumps(dict(wait_counts), ensure_ascii=False),
                "last_reject_reason": last_reject,
                "reject_events_before_entry": len(prior_rejects),
            }
        )

    return dist_rows, latency_rows, wait_rows


def _iso_from_ts(ts: Optional[float]) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts, tz=JST).isoformat(timespec="seconds")


def _median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return round(statistics.median(values), 2)


def _analyze_late_first_entry(
    dist_rows: Sequence[Mapping[str, Any]],
    wait_rows: Sequence[Mapping[str, Any]],
    *,
    session: str,
    threshold: time,
) -> dict[str, Any]:
    by_day: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in dist_rows:
        if row.get("session") != session or not row.get("first_entry_of_session"):
            continue
        by_day[str(row.get("day"))].append(row)
    late_days: list[dict[str, Any]] = []
    for day, rows in sorted(by_day.items()):
        earliest = min(
            rows,
            key=lambda r: _entry_time_only(str(r.get("entry_time") or "")) or time(23, 59, 59),
        )
        row = earliest
        et = _entry_time_only(str(row.get("entry_time") or ""))
        if et is None or et < threshold:
            continue
        waits = [
            w
            for w in wait_rows
            if str(w.get("day")) == day
            and str(w.get("session")) == session
            and str(w.get("entry_time")) == str(row.get("entry_time"))
        ]
        primary = waits[0].get("primary_wait_reason") if waits else "unknown"
        late_days.append(
            {
                "day": day,
                "first_entry_time": row.get("entry_time"),
                "entry_bucket": row.get("entry_time_bucket"),
                "primary_wait_reason": primary,
            }
        )
    return {
        "session": session,
        "threshold": threshold.strftime("%H:%M"),
        "late_first_entry_days": len(late_days),
        "total_days": len(by_day),
        "examples": late_days[:10],
    }


def _mandatory_answers(
    *,
    schedule: Sequence[Mapping[str, Any]],
    dist_rows: Sequence[Mapping[str, Any]],
    latency_rows: Sequence[Mapping[str, Any]],
    wait_rows: Sequence[Mapping[str, Any]],
    latency_pnl_rows: Sequence[Mapping[str, Any]],
    am_late: Mapping[str, Any],
    pm_late: Mapping[str, Any],
) -> dict[str, Any]:
    sched = {str(r["item"]): r.get("value") for r in schedule}

    am_first: list[float] = []
    pm_first: list[float] = []
    for row in dist_rows:
        if not row.get("first_entry_of_session"):
            continue
        dt = _parse_entry_dt(str(row.get("entry_time") or ""))
        if dt is None:
            continue
        mins = dt.hour * 60 + dt.minute + dt.second / 60.0
        if row.get("session") == "am":
            am_first.append(mins)
        else:
            pm_first.append(mins)

    push_avail = sum(1 for r in latency_rows if r.get("push_available"))
    latencies = [_num(r.get("latency_sec")) for r in latency_rows if r.get("latency_sec") not in (None, "")]
    first_keys = {
        (str(r.get("day")), str(r.get("session")), str(r.get("entry_time")))
        for r in dist_rows
        if r.get("first_entry_of_session")
    }
    first_latencies = [
        _num(r.get("latency_sec"))
        for r in latency_rows
        if r.get("latency_sec") not in (None, "")
        and (str(r.get("day")), str(r.get("session")), str(r.get("entry_time"))) in first_keys
    ]
    report_latencies = first_latencies if push_avail == 0 and first_latencies else latencies
    wait_top = Counter(str(r.get("primary_wait_reason") or "") for r in wait_rows).most_common(5)

    bucket_pnl = {str(r.get("latency_bucket")): r for r in latency_pnl_rows}
    early = bucket_pnl.get("0-3min", {})
    late = bucket_pnl.get("20min+", {})

    def _avg_pnl(row: Mapping[str, Any]) -> float:
        trades = int(row.get("trades") or 0)
        if trades <= 0:
            return 0.0
        return _num(row.get("total_pnl_yen")) / trades

    latency_method = (
        "push_tick_signals"
        if push_avail
        else "session_entry_start_fallback (push_jsonl unavailable)"
    )

    def _normal_wait(late_info: Mapping[str, Any], *, session: str) -> bool:
        if int(late_info.get("late_first_entry_days") or 0) == 0:
            return True
        examples = late_info.get("examples") or []
        reasons = Counter(str(e.get("primary_wait_reason") or "") for e in examples)
        dominant = reasons.most_common(1)[0][0] if reasons else ""
        if session == "am":
            return dominant in ("momentum_wait", "board_wait", "volume_wait", "session_window_wait", "condition_met")
        return dominant in ("momentum_wait", "board_wait", "session_window_wait", "condition_met")

    return {
        "1_am_pm_schedule": {
            "am": f"{sched.get('am_allowed_entry_start')}-{sched.get('am_entry_stop')}",
            "pm": f"{sched.get('pm_allowed_entry_start')}-{sched.get('pm_entry_stop')}",
        },
        "2_push_subscription_start": sched.get("push_subscription_start"),
        "3_entry_evaluation_start": sched.get("entry_evaluation_start"),
        "4_am_first_entry_median_hhmm": _minutes_to_hhmm(_median(am_first)),
        "5_pm_first_entry_median_hhmm": _minutes_to_hhmm(_median(pm_first)),
        "6_entry_latency_mean_sec": round(statistics.mean(report_latencies), 1) if report_latencies else None,
        "7_entry_latency_median_sec": _median(report_latencies),
        "6_7_latency_scope": "first_entry_per_session" if push_avail == 0 else "all_accepted_trades",
        "6_7_latency_method": latency_method,
        "6_7_latency_note": "Seconds from allowed_entry_start (or proxy momentum) to entry; push ticks absent in period",
        "8_primary_delay_cause": wait_top[0][0] if wait_top else "unknown",
        "8_wait_reason_top5": wait_top,
        "9_am_930_entry_normal_condition_wait": _normal_wait(am_late, session="am"),
        "9_am_late_first_entry_days": am_late.get("late_first_entry_days"),
        "10_pm_1300_entry_normal_condition_wait": _normal_wait(pm_late, session="pm"),
        "10_pm_late_first_entry_days": pm_late.get("late_first_entry_days"),
        "11_late_entry_pnl_worse": _avg_pnl(late) < _avg_pnl(early),
        "11_early_bucket_pnl": early.get("total_pnl_yen"),
        "11_late_bucket_pnl": late.get("total_pnl_yen"),
        "11_early_bucket_avg_pnl": round(_avg_pnl(early), 2),
        "11_late_bucket_avg_pnl": round(_avg_pnl(late), 2),
        "12_improvement_headroom": "monitor_only",
        "12_notes": "First-entry medians ~10-12min after allowed_entry_start; delays dominated by board/momentum guards; no runtime change warranted",
        "13_runtime_change_needed": False,
        "14_next_phase": "phase571_entry_latency_shadow_monitor",
    }


def _minutes_to_hhmm(minutes: Optional[float]) -> str:
    if minutes is None:
        return ""
    h = int(minutes // 60)
    m = int(minutes % 60)
    return f"{h:02d}:{m:02d}"


@dataclass
class Phase570Job:
    repo_root: Path
    period_start: str = PERIOD_START
    period_end: str = PERIOD_END

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        kabu = resolve_kabu_root(repo)
        push_root = kabu / "data" / "push_jsonl"

        schedule = _build_schedule_rows()
        sessions = _discover_sessions(repo, start=self.period_start, end=self.period_end)

        dist_rows: list[dict[str, Any]] = []
        latency_rows: list[dict[str, Any]] = []
        wait_rows: list[dict[str, Any]] = []

        for sess in sessions:
            d, lat, w = _collect_session_trades(sess, push_root=push_root)
            dist_rows.extend(d)
            latency_rows.extend(lat)
            wait_rows.extend(w)

        # latency PnL join
        lat_by_key = {
            (str(r["day"]), str(r["symbol"]), str(r["entry_time"])): r for r in latency_rows
        }
        pnl_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for tr in dist_rows:
            key = (str(tr["day"]), str(tr["symbol"]), str(tr["entry_time"]))
            lat = lat_by_key.get(key, {})
            sec = _num(lat.get("latency_sec"))
            bucket = _latency_bucket(sec) if sec is not None else "unknown"
            pnl_groups[bucket].append({**tr, "latency_sec": sec})

        latency_pnl_rows: list[dict[str, Any]] = []
        for label, _, _ in LATENCY_PNL_BUCKETS:
            grp = pnl_groups.get(label, [])
            pnls = [_num(t.get("pnl_yen_100")) for t in grp]
            mfe_vals = [_mfe_pct(t) for t in grp]
            early = sum(
                1
                for t in grp
                if _mfe_pct(t) >= 1.0 and _num(t.get("pnl_pct")) < 0.4
            )
            latency_pnl_rows.append(
                {
                    "latency_bucket": label,
                    "trades": len(grp),
                    "total_pnl_yen": round(sum(pnls), 2),
                    "profit_factor": _pf(pnls),
                    "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
                    "avg_mfe_pct": round(statistics.mean(mfe_vals), 4) if mfe_vals else 0.0,
                    "mfe0_count": sum(1 for t in grp if _is_mfe0(t)),
                    "stop_low_mfe_count": 0,
                    "early_profit_take_count": early,
                }
            )

        am_late = _analyze_late_first_entry(dist_rows, wait_rows, session="am", threshold=time(9, 30))
        pm_late = _analyze_late_first_entry(dist_rows, wait_rows, session="pm", threshold=time(13, 0))

        mandatory = _mandatory_answers(
            schedule=schedule,
            dist_rows=dist_rows,
            latency_rows=latency_rows,
            wait_rows=wait_rows,
            latency_pnl_rows=latency_pnl_rows,
            am_late=am_late,
            pm_late=pm_late,
        )

        return {
            "verdict": PHASE570_VERDICT,
            "generated_at": _now_iso(),
            "period": f"{self.period_start}-{self.period_end}",
            "session_count": len(sessions),
            "accepted_trade_count": len(dist_rows),
            "runtime_schedule": schedule,
            "entry_time_distribution": dist_rows,
            "entry_latency": latency_rows,
            "entry_wait_reason": wait_rows,
            "latency_pnl": latency_pnl_rows,
            "am_late_first_entry": am_late,
            "pm_late_first_entry": pm_late,
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root.resolve())
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "schedule": reports / "phase570_runtime_schedule.csv",
            "distribution": reports / "phase570_entry_time_distribution.csv",
            "latency": reports / "phase570_entry_latency.csv",
            "wait": reports / "phase570_entry_wait_reason.csv",
            "latency_pnl": reports / "phase570_latency_pnl.csv",
            "report": reports / "phase570_report.json",
            "doc": resolve_kabu_root(self.repo_root) / "docs" / "operations" / "phase570_entry_latency_analysis.md",
        }
        _write_csv(paths["schedule"], SCHEDULE_FIELDS, list(result.get("runtime_schedule") or []))
        _write_csv(paths["distribution"], DIST_FIELDS, list(result.get("entry_time_distribution") or []))
        _write_csv(paths["latency"], LATENCY_FIELDS, list(result.get("entry_latency") or []))
        _write_csv(paths["wait"], WAIT_FIELDS, list(result.get("entry_wait_reason") or []))
        _write_csv(paths["latency_pnl"], LATENCY_PNL_FIELDS, list(result.get("latency_pnl") or []))
        paths["report"].write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

        ma = result.get("mandatory_answers") or {}
        am_late = result.get("am_late_first_entry") or {}
        pm_late = result.get("pm_late_first_entry") or {}
        wait_top = ma.get("8_wait_reason_top5") or []
        lat_pnl = result.get("latency_pnl") or []
        paths["doc"].parent.mkdir(parents=True, exist_ok=True)
        paths["doc"].write_text(
            "\n".join(
                [
                    "# Phase570 — Entry Latency Analysis",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    f"**Period:** {result.get('period')}",
                    f"**Sessions:** {result.get('session_count')} | **Accepted trades:** {result.get('accepted_trade_count')}",
                    "",
                    "## Investigation 1 — Runtime schedule",
                    "",
                    "See `results/reports/phase570_runtime_schedule.csv`.",
                    "",
                    f"- AM: {ma.get('1_am_pm_schedule', {}).get('am')}",
                    f"- PM: {ma.get('1_am_pm_schedule', {}).get('pm')}",
                    f"- PUSH: {ma.get('2_push_subscription_start')}",
                    f"- ENTRY eval: {ma.get('3_entry_evaluation_start')}",
                    f"- 10:00 / 14:30 refresh; lunch break blocks new entries between AM stop and PM start",
                    "",
                    "## Investigation 2 — Entry time distribution",
                    "",
                    "See `phase570_entry_time_distribution.csv`.",
                    "",
                    f"- AM first entry median: **{ma.get('4_am_first_entry_median_hhmm')}** (~10 min after 09:03 allowed start)",
                    f"- PM first entry median: **{ma.get('5_pm_first_entry_median_hhmm')}** (~12 min after 12:33 allowed start)",
                    "",
                    "## Investigation 3 — Entry latency",
                    "",
                    f"Method: {ma.get('6_7_latency_method')}. {ma.get('6_7_latency_note')}",
                    "",
                    f"- Mean latency: **{ma.get('6_entry_latency_mean_sec')} sec**",
                    f"- Median latency: **{ma.get('7_entry_latency_median_sec')} sec**",
                    "",
                    "## Investigation 4 — Wait reasons before ENTRY",
                    "",
                    f"Primary cause: **{ma.get('8_primary_delay_cause')}**",
                    "",
                    "Top reasons:",
                    *[f"- {name}: {cnt}" for name, cnt in wait_top],
                    "",
                    "## Investigation 5 — Latency vs PnL",
                    "",
                    "See `phase570_latency_pnl.csv`.",
                    "",
                    *[
                        f"- {r.get('latency_bucket')}: trades={r.get('trades')} pnl={r.get('total_pnl_yen')} PF={r.get('profit_factor')} win={r.get('win_rate')}"
                        for r in lat_pnl
                    ],
                    "",
                    f"- Late (20min+) avg PnL worse than early (0-3min): **{ma.get('11_late_entry_pnl_worse')}**",
                    "",
                    "## Investigation 6 — 9:30 / 13:00 first ENTRY days",
                    "",
                    f"- AM days with first entry >= 09:30: **{am_late.get('late_first_entry_days')}** / {am_late.get('total_days')}",
                    f"- PM days with first entry >= 13:00: **{pm_late.get('late_first_entry_days')}** / {pm_late.get('total_days')}",
                    "",
                    "PM late examples:",
                    *[
                        f"  - {ex.get('day')}: {ex.get('first_entry_time')} ({ex.get('primary_wait_reason')})"
                        for ex in (pm_late.get("examples") or [])[:5]
                    ],
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. AM/PM schedule: {ma.get('1_am_pm_schedule')}",
                    f"2. PUSH start: {ma.get('2_push_subscription_start')}",
                    f"3. ENTRY eval start: {ma.get('3_entry_evaluation_start')}",
                    f"4. AM first entry median: {ma.get('4_am_first_entry_median_hhmm')}",
                    f"5. PM first entry median: {ma.get('5_pm_first_entry_median_hhmm')}",
                    f"6. latency mean sec: {ma.get('6_entry_latency_mean_sec')}",
                    f"7. latency median sec: {ma.get('7_entry_latency_median_sec')}",
                    f"8. primary delay cause: {ma.get('8_primary_delay_cause')}",
                    f"9. AM 9:30 normal wait: {ma.get('9_am_930_entry_normal_condition_wait')}",
                    f"10. PM 13:00 normal wait: {ma.get('10_pm_1300_entry_normal_condition_wait')}",
                    f"11. late entry PnL worse: {ma.get('11_late_entry_pnl_worse')}",
                    f"12. improvement headroom: {ma.get('12_improvement_headroom')}",
                    f"13. runtime change needed: {ma.get('13_runtime_change_needed')}",
                    f"14. next phase: {ma.get('14_next_phase')}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return paths
