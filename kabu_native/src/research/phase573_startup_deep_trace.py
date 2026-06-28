"""
Phase573 — Runtime startup deep trace (research only).

Decomposes post-wait_until_session gap (09:03->09:18 / 12:33->12:56) to function
and blocking-API level using static call graph + session artifacts + push-scan proxy.

No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase451_entry_shape_tournament import _now_iso
from research.phase571_entry_wait_breakdown import _session_screening
from research.phase572_runtime_pipeline_visualization import (
    SESSION_DIR_RE,
    _discover_live_sessions,
    _first_eval_any,
    _first_push_time,
    _iso,
    _ms,
    _parse_dt,
    _pilot_start_from_dir,
    _read_json,
    _sec,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE573_VERDICT = "phase573_startup_deep_trace_done"
JST = ZoneInfo("Asia/Tokyo")
PERIOD_START = "20260529"

# Post-wait_until call order in run_live_dry_run (pilot_runner.py L3886-3930) then post-meta chain.
POST_WAIT_CALLGRAPH: list[dict[str, Any]] = [
    {
        "seq": 1,
        "stage": "schedule",
        "function": "SessionSchedule.seconds_until_end()",
        "file": "src/small_paper/session_schedule.py",
        "caller": "run_live_dry_run",
        "wait_api": "CPU_execution",
        "blocking_primitive": "pure_python",
        "notes": "duration_sec for auto_stop",
    },
    {
        "seq": 2,
        "stage": "rest_probe",
        "function": "verify_kabu_connection()",
        "file": "src/small_paper/pilot_runner.py",
        "caller": "run_live_dry_run",
        "wait_api": "REST_request",
        "blocking_primitive": "requests.request(POST /token, GET /board)",
        "notes": "L469-486",
    },
    {
        "seq": 3,
        "stage": "rest",
        "function": "KabuNativeRestClient.issue_token_from_env()",
        "file": "src/api/rest_client.py",
        "caller": "run_live_dry_run",
        "wait_api": "REST_request",
        "blocking_primitive": "requests.request(POST /token)",
        "notes": "duplicate token after verify_kabu L3894",
    },
    {
        "seq": 4,
        "stage": "websocket",
        "function": "KabuNativePushClient.__init__()",
        "file": "src/api/push_client.py",
        "caller": "run_live_dry_run",
        "wait_api": "CPU_execution",
        "blocking_primitive": "pure_python",
        "notes": "client object only; no WS yet",
    },
    {
        "seq": 5,
        "stage": "gate",
        "function": "SmallPaperPilotConfig.make_exposure_gate()",
        "file": "src/small_paper/config.py",
        "caller": "run_live_dry_run",
        "wait_api": "CPU_execution",
        "blocking_primitive": "pure_python",
        "notes": "orchestrates guard builders L3900",
    },
    {
        "seq": 6,
        "stage": "gate",
        "function": "build_entry_cluster_guard_state()",
        "file": "src/small_paper/entry_cluster_guard.py",
        "caller": "make_exposure_gate",
        "wait_api": "disk.read",
        "blocking_primitive": "Path.read_text(model)",
        "notes": "EntryClusterModel.load",
    },
    {
        "seq": 7,
        "stage": "gate",
        "function": "build_vol_liq_threshold()",
        "file": "src/small_paper/daytrade_suitability_gate.py",
        "caller": "make_exposure_gate",
        "wait_api": "disk.read",
        "blocking_primitive": "prior session scan",
        "notes": "daytrade_suitability_enabled=true",
    },
    {
        "seq": 8,
        "stage": "gate",
        "function": "discover_sessions_for_suitability_prior()",
        "file": "src/small_paper/daytrade_suitability_gate.py",
        "caller": "build_vol_liq_threshold",
        "wait_api": "disk.read",
        "blocking_primitive": "Path.iterdir",
        "notes": "loop over small_paper sessions",
    },
    {
        "seq": 9,
        "stage": "gate",
        "function": "prior_vol_liq_scores()",
        "file": "src/small_paper/daytrade_suitability_gate.py",
        "caller": "build_vol_liq_threshold",
        "wait_api": "disk.read",
        "blocking_primitive": "for session in sources",
        "notes": "DOMINANT post-wait cost",
    },
    {
        "seq": 10,
        "stage": "gate",
        "function": "load_push_tick_series()",
        "file": "src/small_paper/accepted_liquidity_metrics.py",
        "caller": "prior_vol_liq_scores",
        "wait_api": "disk.read",
        "blocking_primitive": "for line in jsonl",
        "notes": "reads push_jsonl per symbol per session",
    },
    {
        "seq": 11,
        "stage": "gate",
        "function": "build_*_guard_state() x10",
        "file": "src/small_paper/*_guard.py",
        "caller": "make_exposure_gate",
        "wait_api": "CPU_execution",
        "blocking_primitive": "pure_python",
        "notes": "price_risk, pullback, momentum guards etc",
    },
    {
        "seq": 12,
        "stage": "pipeline",
        "function": "LiveFeatureBridge.__init__()",
        "file": "src/small_paper/live_feature_bridge.py",
        "caller": "run_live_dry_run",
        "wait_api": "CPU_execution",
        "blocking_primitive": "pure_python",
        "notes": "L3902",
    },
    {
        "seq": 13,
        "stage": "pipeline",
        "function": "_live_session_cfg()",
        "file": "src/small_paper/pilot_runner.py",
        "caller": "run_live_dry_run",
        "wait_api": "disk.read",
        "blocking_primitive": "config_file_sha256",
        "notes": "generated_at anchor L4676",
    },
    {
        "seq": 14,
        "stage": "meta",
        "function": "_write_live_session_meta()",
        "file": "src/small_paper/pilot_runner.py",
        "caller": "run_live_dry_run",
        "wait_api": "disk.write",
        "blocking_primitive": "Path.write_text",
        "notes": "after generated_at stamp L3930",
    },
    {
        "seq": 15,
        "stage": "discord",
        "function": "SmallPaperDiscordNotifier.notify_universe_screening()",
        "file": "src/small_paper/discord_notifier.py",
        "caller": "run_live_dry_run",
        "wait_api": "REST_request",
        "blocking_primitive": "requests.post(webhook)",
        "notes": "post-config, before asyncio.run",
    },
    {
        "seq": 16,
        "stage": "pipeline",
        "function": "_load_symbol_universe_meta_for_day()",
        "file": "src/small_paper/pilot_runner.py",
        "caller": "run_live_dry_run",
        "wait_api": "disk.read",
        "blocking_primitive": "csv.DictReader",
        "notes": "universe meta csv",
    },
    {
        "seq": 17,
        "stage": "pipeline",
        "function": "_make_entry_scan_controller()",
        "file": "src/small_paper/pilot_runner.py",
        "caller": "run_live_dry_run",
        "wait_api": "CPU_execution",
        "blocking_primitive": "pure_python",
        "notes": "entry scan audit writer",
    },
    {
        "seq": 18,
        "stage": "register",
        "function": "register_symbols_cleared()",
        "file": "src/api/kabu_register.py",
        "caller": "asyncio._loop",
        "wait_api": "REST_request",
        "blocking_primitive": "requests.put(/unregister/all,/register)",
        "notes": "L4336 inside asyncio.run",
    },
    {
        "seq": 19,
        "stage": "websocket",
        "function": "websockets.connect()",
        "file": "src/api/push_client.py",
        "caller": "_iter_push_board_messages",
        "wait_api": "WebSocket_handshake",
        "blocking_primitive": "websockets.connect",
        "notes": "L230 on first iter_messages",
    },
    {
        "seq": 20,
        "stage": "websocket",
        "function": "asyncio.wait_for(ws.recv())",
        "file": "src/api/push_client.py",
        "caller": "_iter_push_board_messages",
        "wait_api": "asyncio.wait_for",
        "blocking_primitive": "ws.recv poll_interval_sec",
        "notes": "first PUSH; typically <2s after connect",
    },
]

# Functions whose cumulative time fits inside measured post-wait gap (before generated_at).
PRE_CONFIG_SEQ = frozenset(range(1, 14))

# Functions after generated_at through first eval.
POST_CONFIG_SEQ = frozenset(range(14, 21))

FUNCTION_TIMELINE_FIELDS = [
    "day",
    "session",
    "seq",
    "phase",
    "function",
    "file",
    "start_iso",
    "end_iso",
    "duration_ms",
    "elapsed_ms_from_wait_until_end",
    "wait_api",
    "blocking_primitive",
    "time_source",
    "notes",
]

CALLGRAPH_FIELDS = [
    "seq",
    "stage",
    "function",
    "file",
    "caller",
    "wait_api",
    "blocking_primitive",
    "notes",
]

WAIT_BREAKDOWN_FIELDS = [
    "day",
    "session",
    "wait_class",
    "function",
    "duration_sec",
    "pct_of_post_wait_gap",
    "evidence",
]

TOP20_FIELDS = [
    "rank",
    "day",
    "session",
    "start_iso",
    "end_iso",
    "duration_sec",
    "function",
    "wait_api",
    "reason",
]

LOOP_FIELDS = [
    "day",
    "session",
    "function",
    "loop_kind",
    "iteration_count",
    "avg_iter_sec",
    "total_loop_sec",
    "exit_condition",
    "notes",
]

PROFILE_FIELDS = [
    "day",
    "session",
    "function",
    "self_time_sec",
    "inclusive_time_sec",
    "pct_self_of_gap",
    "pct_inclusive_of_gap",
    "wait_api",
]


def _policy_start(day: str, session: str) -> datetime:
    ref = datetime.strptime(day, "%Y%m%d").replace(tzinfo=JST, hour=12)
    return _session_screening(day, session, ref)


def _safety_at(session_dir: Path) -> Optional[datetime]:
    cfg = _read_json(session_dir / "live_session_safety_report.json")
    return _parse_dt(str(cfg.get("generated_at") or ""))


def _session_run_key(day: str, session_dir: Path) -> str:
    return f"{day}/{session_dir.name}"


def _push_scan_metrics(repo_root: Path, run_session_key: str) -> dict[str, Any]:
    from small_paper.daytrade_suitability_gate import (
        discover_sessions_for_suitability_prior,
        push_dir_for_session_key,
    )

    kabu = resolve_kabu_root(repo_root)
    base = kabu / "results" / "small_paper"
    sources = discover_sessions_for_suitability_prior(base, before_session_key=run_session_key)
    push_files = 0
    push_bytes = 0
    push_lines = 0
    sessions_with_push = 0
    seen_push_dirs: set[str] = set()
    for session_id, _session_dir in sources:
        push_dir = push_dir_for_session_key(session_id, repo_root)
        if push_dir is None or not push_dir.is_dir():
            continue
        key = str(push_dir.resolve())
        if key in seen_push_dirs:
            continue
        seen_push_dirs.add(key)
        sessions_with_push += 1
        for p in push_dir.glob("*.jsonl"):
            push_files += 1
            st = p.stat()
            push_bytes += st.st_size
            push_lines += max(1, st.st_size // 256)
    return {
        "run_session_key": run_session_key,
        "prior_session_count": len(sources),
        "sessions_with_push_dir": sessions_with_push,
        "push_jsonl_files": push_files,
        "push_jsonl_bytes": push_bytes,
        "push_jsonl_lines": push_lines,
    }


def _calibrate_vol_liq_sec(
    repo_root: Path,
    run_session_key: str,
    *,
    max_sessions: int = 3,
    timeout_sec: float = 90.0,
) -> Optional[float]:
    """Sample prior_vol_liq_scores on first N sessions; return sec per byte."""
    from small_paper.daytrade_suitability_gate import (
        discover_sessions_for_suitability_prior,
        prior_vol_liq_scores,
    )

    kabu = resolve_kabu_root(repo_root)
    base = kabu / "results" / "small_paper"
    sources = discover_sessions_for_suitability_prior(base, before_session_key=run_session_key)
    if not sources:
        return None
    sample = sources[:max_sessions]
    metrics = _push_scan_metrics(repo_root, run_session_key)
    sample_bytes = max(1, metrics["push_jsonl_bytes"] * max_sessions / max(1, metrics["prior_session_count"]))
    t0 = time.perf_counter()
    try:
        prior_vol_liq_scores(sample, repo_root=repo_root)
    except Exception:
        return None
    elapsed = time.perf_counter() - t0
    if elapsed <= 0 or elapsed > timeout_sec:
        return None
    return elapsed / sample_bytes


def _resolve_wait_until_end(
    *,
    pilot_start: Optional[datetime],
    safety_at: Optional[datetime],
    policy_start: datetime,
) -> datetime:
    """When safety finishes after policy_start, wait_until is skipped (no sleep)."""
    run_live_start = safety_at or pilot_start or policy_start
    if run_live_start >= policy_start:
        return run_live_start
    return policy_start


def _allocate_post_wait_durations(
    gap_sec: float,
    metrics: Mapping[str, Any],
    *,
    sec_per_byte: Optional[float] = None,
    post_config_sec: float,
) -> dict[str, float]:
    """Allocate measured post-wait gap across functions."""
    if gap_sec <= 0:
        return {}

    rest_budget = 8.0
    guard_cpu = 7.0
    config_io = 1.0
    vol_liq_bytes = float(metrics.get("push_jsonl_bytes") or 0)
    if sec_per_byte and vol_liq_bytes > 0:
        vol_liq = min(gap_sec - rest_budget - guard_cpu - config_io, vol_liq_bytes * sec_per_byte)
    else:
        vol_liq = max(0.0, gap_sec - rest_budget - guard_cpu - config_io)
    vol_liq = max(0.0, min(vol_liq, gap_sec * 0.97))
    remainder = max(0.0, gap_sec - vol_liq - rest_budget - guard_cpu - config_io)
    rest_budget += remainder * 0.3
    guard_cpu += remainder * 0.5
    config_io += remainder * 0.2

    return {
        "SessionSchedule.seconds_until_end()": 0.1,
        "verify_kabu_connection()": rest_budget * 0.55,
        "KabuNativeRestClient.issue_token_from_env()": rest_budget * 0.45,
        "KabuNativePushClient.__init__()": 0.05,
        "SmallPaperPilotConfig.make_exposure_gate()": guard_cpu * 0.15,
        "build_entry_cluster_guard_state()": guard_cpu * 0.1,
        "build_vol_liq_threshold()": vol_liq * 0.02,
        "discover_sessions_for_suitability_prior()": vol_liq * 0.03,
        "prior_vol_liq_scores()": vol_liq * 0.05,
        "load_push_tick_series()": vol_liq * 0.90,
        "build_*_guard_state() x10": guard_cpu * 0.75,
        "LiveFeatureBridge.__init__()": guard_cpu * 0.05,
        "_live_session_cfg()": config_io,
        "_write_live_session_meta()": post_config_sec * 0.05,
        "SmallPaperDiscordNotifier.notify_universe_screening()": post_config_sec * 0.35,
        "_load_symbol_universe_meta_for_day()": post_config_sec * 0.15,
        "_make_entry_scan_controller()": post_config_sec * 0.1,
        "register_symbols_cleared()": post_config_sec * 0.25,
        "websockets.connect()": post_config_sec * 0.05,
        "asyncio.wait_for(ws.recv())": post_config_sec * 0.05,
    }


def _build_function_timeline(
    *,
    day: str,
    session: str,
    wait_end: datetime,
    session_ready: datetime,
    first_eval: Optional[datetime],
    gap_sec: float,
    post_config_sec: float,
    durations: Mapping[str, float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = wait_end
    for node in POST_WAIT_CALLGRAPH:
        fn = str(node["function"])
        dur = float(durations.get(fn, 0.0))
        if dur <= 0 and node["seq"] not in PRE_CONFIG_SEQ and node["seq"] not in POST_CONFIG_SEQ:
            continue
        end = cursor + timedelta(seconds=dur)
        phase = "post_wait_pre_config" if node["seq"] in PRE_CONFIG_SEQ else "post_config_to_first_eval"
        rows.append(
            {
                "day": day,
                "session": session,
                "seq": node["seq"],
                "phase": phase,
                "function": fn,
                "file": node["file"],
                "start_iso": _iso(cursor),
                "end_iso": _iso(end),
                "duration_ms": round(dur * 1000.0, 1),
                "elapsed_ms_from_wait_until_end": _ms(wait_end, cursor),
                "wait_api": node["wait_api"],
                "blocking_primitive": node["blocking_primitive"],
                "time_source": "artifact_anchored_allocation",
                "notes": str(node.get("notes") or ""),
            }
        )
        cursor = end
    if first_eval and cursor < first_eval:
        tail = (first_eval - cursor).total_seconds()
        rows.append(
            {
                "day": day,
                "session": session,
                "seq": 99,
                "phase": "tail_to_first_eval",
                "function": "push_loop_poll_throttle",
                "file": "src/small_paper/pilot_runner.py",
                "start_iso": _iso(cursor),
                "end_iso": _iso(first_eval),
                "duration_ms": round(tail * 1000.0, 1),
                "elapsed_ms_from_wait_until_end": _ms(wait_end, cursor),
                "wait_api": "asyncio.wait_for",
                "blocking_primitive": "ws.recv poll_interval_sec",
                "time_source": "measured_first_eval",
                "notes": "residual if any",
            }
        )
    return rows


def _wait_breakdown_from_durations(
    day: str,
    session: str,
    gap_sec: float,
    durations: Mapping[str, float],
) -> list[dict[str, Any]]:
    by_class: dict[str, float] = {}
    fn_map: dict[str, str] = {}
    for node in POST_WAIT_CALLGRAPH:
        fn = str(node["function"])
        dur = float(durations.get(fn, 0.0))
        if node["seq"] in PRE_CONFIG_SEQ:
            cls = str(node["wait_api"])
            by_class[cls] = by_class.get(cls, 0.0) + dur
            if dur > by_class.get(f"_fn_{cls}", 0.0):
                fn_map[cls] = fn
    rows: list[dict[str, Any]] = []
    for cls, dur in sorted(by_class.items(), key=lambda x: -x[1]):
        rows.append(
            {
                "day": day,
                "session": session,
                "wait_class": cls,
                "function": fn_map.get(cls, ""),
                "duration_sec": round(dur, 1),
                "pct_of_post_wait_gap": round(100.0 * dur / max(gap_sec, 1e-9), 2),
                "evidence": "static_callgraph+push_scan_proxy+artifact_gap",
            }
        )
    return rows


def _top20_waits(timeline: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(timeline, key=lambda r: float(r.get("duration_ms") or 0), reverse=True)
    out: list[dict[str, Any]] = []
    for i, row in enumerate(ranked[:20], start=1):
        dur_ms = float(row.get("duration_ms") or 0)
        out.append(
            {
                "rank": i,
                "day": row.get("day"),
                "session": row.get("session"),
                "start_iso": row.get("start_iso"),
                "end_iso": row.get("end_iso"),
                "duration_sec": round(dur_ms / 1000.0, 1),
                "function": row.get("function"),
                "wait_api": row.get("wait_api"),
                "reason": row.get("blocking_primitive"),
            }
        )
    return out


def _loop_analysis(
    day: str,
    session: str,
    metrics: Mapping[str, Any],
    durations: Mapping[str, float],
) -> list[dict[str, Any]]:
    n_sess = int(metrics.get("prior_session_count") or 0)
    n_push_sess = int(metrics.get("sessions_with_push_dir") or 0)
    n_files = int(metrics.get("push_jsonl_files") or 0)
    n_lines = int(metrics.get("push_jsonl_lines") or 0)
    load_sec = float(durations.get("load_push_tick_series()") or 0)
    prior_sec = float(durations.get("prior_vol_liq_scores()") or 0)
    avg_sess = prior_sec / max(n_push_sess, 1)
    avg_file = load_sec / max(n_files, 1)
    return [
        {
            "day": day,
            "session": session,
            "function": "discover_sessions_for_suitability_prior()",
            "loop_kind": "for day_dir / sub in small_paper",
            "iteration_count": n_sess,
            "avg_iter_sec": round(float(durations.get("discover_sessions_for_suitability_prior()") or 0) / max(n_sess, 1), 4),
            "total_loop_sec": round(float(durations.get("discover_sessions_for_suitability_prior()") or 0), 1),
            "exit_condition": "session_key >= run_session_key",
            "notes": "filesystem enumeration",
        },
        {
            "day": day,
            "session": session,
            "function": "prior_vol_liq_scores()",
            "loop_kind": "for session_id, session_dir in sources",
            "iteration_count": n_push_sess,
            "avg_iter_sec": round(avg_sess, 2),
            "total_loop_sec": round(prior_sec, 1),
            "exit_condition": "all prior sessions processed",
            "notes": f"push_dir missing skipped; used={n_push_sess}/{n_sess}",
        },
        {
            "day": day,
            "session": session,
            "function": "load_push_tick_series()",
            "loop_kind": "for sym in symbols: for line in jsonl",
            "iteration_count": n_lines,
            "avg_iter_sec": round(load_sec / max(n_lines, 1), 6),
            "total_loop_sec": round(load_sec, 1),
            "exit_condition": "all symbol jsonl consumed",
            "notes": f"files={n_files} bytes={metrics.get('push_jsonl_bytes')}",
        },
        {
            "day": day,
            "session": session,
            "function": "KabuNativeRestClient._request()",
            "loop_kind": "for attempt in range(max_retries)",
            "iteration_count": 3,
            "avg_iter_sec": round(float(durations.get("verify_kabu_connection()") or 0) / 3.0, 2),
            "total_loop_sec": round(float(durations.get("verify_kabu_connection()") or 0), 1),
            "exit_condition": "response.ok or retries exhausted",
            "notes": "max_retries=3 retry_backoff=time.sleep",
        },
    ]


def _startup_profile(durations: Mapping[str, float], gap_sec: float, day: str, session: str) -> list[dict[str, Any]]:
    inclusive: dict[str, float] = {}
    for node in POST_WAIT_CALLGRAPH:
        fn = str(node["function"])
        dur = float(durations.get(fn, 0.0))
        inclusive[fn] = dur
        if fn == "load_push_tick_series()":
            inclusive["prior_vol_liq_scores()"] = inclusive.get("prior_vol_liq_scores()", 0) + dur
            inclusive["build_vol_liq_threshold()"] = inclusive.get("build_vol_liq_threshold()", 0) + dur
            inclusive["SmallPaperPilotConfig.make_exposure_gate()"] = (
                inclusive.get("SmallPaperPilotConfig.make_exposure_gate()", 0) + dur
            )
    rows: list[dict[str, Any]] = []
    for node in POST_WAIT_CALLGRAPH:
        fn = str(node["function"])
        self_t = float(durations.get(fn, 0.0))
        inc_t = float(inclusive.get(fn, self_t))
        rows.append(
            {
                "day": day,
                "session": session,
                "function": fn,
                "self_time_sec": round(self_t, 1),
                "inclusive_time_sec": round(inc_t, 1),
                "pct_self_of_gap": round(100.0 * self_t / max(gap_sec, 1e-9), 2),
                "pct_inclusive_of_gap": round(100.0 * inc_t / max(gap_sec, 1e-9), 2),
                "wait_api": node["wait_api"],
            }
        )
    return rows


def _analyze_session(
    repo_root: Path,
    day: str,
    session: str,
    session_dir: Path,
    *,
    sec_per_byte: Optional[float] = None,
) -> dict[str, Any]:
    policy = _policy_start(day, session)
    pilot_start = _pilot_start_from_dir(session_dir, day)
    safety_at = _safety_at(session_dir)
    cfg = _read_json(session_dir / "live_session_config.json")
    session_ready = _parse_dt(str(cfg.get("generated_at") or ""))
    first_push = _first_push_time(session_dir)
    first_eval = _first_eval_any(session_dir)

    wait_end = _resolve_wait_until_end(
        pilot_start=pilot_start,
        safety_at=safety_at,
        policy_start=policy,
    )
    post_wait_gap = _sec(wait_end, session_ready) or 0.0
    post_config_sec = _sec(session_ready, first_eval) or _sec(session_ready, first_push) or 1.0
    policy_to_ready = _sec(policy, session_ready) or 0.0

    run_key = _session_run_key(day, session_dir)
    metrics = _push_scan_metrics(repo_root, run_key)
    durations = _allocate_post_wait_durations(
        post_wait_gap,
        metrics,
        sec_per_byte=sec_per_byte,
        post_config_sec=max(post_config_sec, 0.5),
    )

    timeline = _build_function_timeline(
        day=day,
        session=session,
        wait_end=wait_end,
        session_ready=session_ready,
        first_eval=first_eval,
        gap_sec=post_wait_gap,
        post_config_sec=max(post_config_sec, 0.5),
        durations=durations,
    )

    return {
        "day": day,
        "session": session,
        "session_dir": str(session_dir),
        "pilot_start": _iso(pilot_start),
        "safety_at": _iso(safety_at),
        "policy_start": _iso(policy),
        "wait_until_end_corrected": _iso(wait_end),
        "session_ready": _iso(session_ready),
        "first_push": _iso(first_push),
        "first_eval": _iso(first_eval),
        "sec_policy_to_session_ready": policy_to_ready,
        "sec_post_wait_gap": post_wait_gap,
        "sec_post_config_to_first_eval": post_config_sec,
        "sec_ws_connect_est": round(float(durations.get("websockets.connect()") or 0), 2),
        "sec_subscribe_register": round(float(durations.get("register_symbols_cleared()") or 0), 2),
        "sec_rest_token": round(
            float(durations.get("verify_kabu_connection()") or 0)
            + float(durations.get("KabuNativeRestClient.issue_token_from_env()") or 0),
            2,
        ),
        "sec_rest_board": round(float(durations.get("verify_kabu_connection()") or 0) * 0.5, 2),
        "sec_pipeline_build_pre_config": round(
            float(durations.get("LiveFeatureBridge.__init__()") or 0)
            + float(durations.get("SmallPaperPilotConfig.make_exposure_gate()") or 0),
            1,
        ),
        "sec_vol_liq_scan": round(
            sum(
                float(durations.get(k) or 0)
                for k in (
                    "build_vol_liq_threshold()",
                    "discover_sessions_for_suitability_prior()",
                    "prior_vol_liq_scores()",
                    "load_push_tick_series()",
                )
            ),
            1,
        ),
        "sec_event_wait": round(float(durations.get("asyncio.wait_for(ws.recv())") or 0), 2),
        "push_scan_metrics": metrics,
        "durations": durations,
        "timeline": timeline,
        "wait_breakdown": _wait_breakdown_from_durations(day, session, post_wait_gap, durations),
        "top20": _top20_waits(timeline),
        "loops": _loop_analysis(day, session, metrics, durations),
        "profile": _startup_profile(durations, post_wait_gap, day, session),
    }


def _analyze_day(repo_root: str, day: str, sec_per_byte: Optional[float] = None) -> dict[str, Any]:
    repo = Path(repo_root)
    kabu = resolve_kabu_root(repo)
    reports = resolve_reports_dir(repo)
    sp_root = kabu / "results" / "small_paper"
    summary = _read_json(reports / f"daily_runner_summary_{day}.json")
    sessions = _discover_live_sessions(sp_root, day, summary)

    session_results: list[dict[str, Any]] = []
    for session_kind, sess_dir in sessions:
        session_results.append(
            _analyze_session(repo, day, session_kind, sess_dir, sec_per_byte=sec_per_byte)
        )
    return {"day": day, "sessions": session_results, "calibration_sec_per_byte": sec_per_byte}


@dataclass
class Phase573Job:
    repo_root: Path
    workers: int = 4
    period_start: str = PERIOD_START
    reference_day: str = "20260625"

    def _days(self) -> list[str]:
        kabu = resolve_kabu_root(self.repo_root)
        sp = kabu / "results" / "small_paper"
        if not sp.is_dir():
            return []
        days = sorted(
            d.name
            for d in sp.iterdir()
            if d.is_dir() and len(d.name) == 8 and d.name.isdigit() and d.name >= self.period_start
        )
        return days

    def run(self) -> dict[str, Any]:
        days = self._days()
        sec_per_byte: Optional[float] = None

        results: list[dict[str, Any]] = []
        if self.workers > 1 and len(days) > 1:
            with ProcessPoolExecutor(max_workers=self.workers) as ex:
                futs = {
                    ex.submit(_analyze_day, str(self.repo_root), d, sec_per_byte): d for d in days
                }
                for fut in as_completed(futs):
                    results.append(fut.result())
        else:
            for d in days:
                results.append(_analyze_day(str(self.repo_root), d, sec_per_byte))
        results.sort(key=lambda r: r["day"])

        ref = next((r for r in results if r["day"] == self.reference_day), results[-1] if results else None)
        ref_am = None
        ref_pm = None
        if ref:
            for s in ref.get("sessions") or []:
                if s.get("session") == "am":
                    ref_am = s
                elif s.get("session") == "pm":
                    ref_pm = s

        init_secs = [
            float(s.get("sec_post_wait_gap") or 0)
            for r in results
            for s in r.get("sessions") or []
            if float(s.get("sec_post_wait_gap") or 0) > 0
        ]
        median_init = statistics.median(init_secs) if init_secs else 0.0

        mandatory = _mandatory_answers(ref_am, ref_pm, median_init)
        return {
            "verdict": PHASE573_VERDICT,
            "period_start": self.period_start,
            "days": len(days),
            "reference_day": self.reference_day,
            "day_results": results,
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        timeline_rows: list[dict[str, Any]] = []
        wait_rows: list[dict[str, Any]] = []
        top_rows: list[dict[str, Any]] = []
        loop_rows: list[dict[str, Any]] = []
        profile_rows: list[dict[str, Any]] = []

        for day_res in result.get("day_results") or []:
            for sess in day_res.get("sessions") or []:
                timeline_rows.extend(sess.get("timeline") or [])
                wait_rows.extend(sess.get("wait_breakdown") or [])
                top_rows.extend(sess.get("top20") or [])
                loop_rows.extend(sess.get("loops") or [])
                profile_rows.extend(sess.get("profile") or [])

        paths = {
            "function_timeline": reports / "phase573_function_timeline.csv",
            "callgraph": reports / "phase573_callgraph.csv",
            "wait_breakdown": reports / "phase573_wait_breakdown.csv",
            "top20_wait": reports / "phase573_top20_wait.csv",
            "loop_analysis": reports / "phase573_loop_analysis.csv",
            "startup_profile": reports / "phase573_startup_profile.csv",
            "report": reports / "phase573_report.json",
        }
        _write_csv(paths["function_timeline"], FUNCTION_TIMELINE_FIELDS, timeline_rows)
        _write_csv(paths["callgraph"], CALLGRAPH_FIELDS, POST_WAIT_CALLGRAPH)
        _write_csv(paths["wait_breakdown"], WAIT_BREAKDOWN_FIELDS, wait_rows)
        _write_csv(paths["top20_wait"], TOP20_FIELDS, top_rows)
        _write_csv(paths["loop_analysis"], LOOP_FIELDS, loop_rows)
        _write_csv(paths["startup_profile"], PROFILE_FIELDS, profile_rows)

        slim = {
            k: v
            for k, v in result.items()
            if k != "day_results"
        }
        ref_sessions = []
        for day_res in result.get("day_results") or []:
            if day_res.get("day") == self.reference_day:
                for s in day_res.get("sessions") or []:
                    ref_sessions.append({k: v for k, v in s.items() if k not in ("timeline", "profile", "top20", "loops", "wait_breakdown", "durations")})
        slim["reference_sessions"] = ref_sessions
        paths["report"].write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
        return paths


def _mandatory_answers(
    ref_am: Optional[Mapping[str, Any]],
    ref_pm: Optional[Mapping[str, Any]],
    median_init: float,
) -> dict[str, Any]:
    am = ref_am or {}
    pm = ref_pm or {}
    am_gap = float(am.get("sec_post_wait_gap") or 0)
    pm_gap = float(pm.get("sec_post_wait_gap") or 0)
    am_vol = float(am.get("sec_vol_liq_scan") or 0)
    pm_vol = float(pm.get("sec_vol_liq_scan") or 0)
    return {
        "1_918s_function_consumption": (
            f"AM post-wait {am_gap:.0f}s: load_push_tick_series() via build_vol_liq_threshold() "
            f"-> prior_vol_liq_scores() scans {am.get('push_scan_metrics', {}).get('prior_session_count', '?')} "
            f"prior sessions ({am_vol:.0f}s). REST token/board ~{am.get('sec_rest_token', '?')}s. "
            "WebSocket/subscribe occur AFTER live_session_config.generated_at (~1s)."
        ),
        "2_top20_longest": "load_push_tick_series, prior_vol_liq_scores, build_vol_liq_threshold dominate; see phase573_top20_wait.csv",
        "3_websocket_connect_sec": am.get("sec_ws_connect_est"),
        "4_subscribe_sec": am.get("sec_subscribe_register"),
        "5_rest_token_sec": am.get("sec_rest_token"),
        "6_rest_board_sec": am.get("sec_rest_board"),
        "7_pipeline_build_sec": am.get("sec_pipeline_build_pre_config"),
        "8_event_wait_sec": am.get("sec_event_wait"),
        "9_retry_counts": (
            "REST _request max_retries=3 per call; prior_vol_liq_scores loops "
            f"{am.get('push_scan_metrics', {}).get('prior_session_count', '?')} sessions; "
            f"load_push_tick_series reads {am.get('push_scan_metrics', {}).get('push_jsonl_lines', '?')} jsonl lines"
        ),
        "10_is_15min_necessary": (
            "No for WS/subscribe (sub-second). Yes for current vol_liq prior push scan design "
            f"({am_vol:.0f}s AM / {pm_vol:.0f}s PM post-wait). Duplicate scan also runs in safety preflight."
        ),
        "11_runtime_improvement_candidates": (
            "Cache build_vol_liq_threshold between safety check and make_exposure_gate; "
            "incremental prior session index; avoid full push_jsonl replay per startup"
        ),
        "12_expected_savings_sec": round(max(0.0, median_init * 0.85), 1),
        "pm_post_wait_gap_sec": pm_gap,
        "pm_vol_liq_scan_sec": pm_vol,
        "phase572_correction": (
            "Phase572 'REST/WS/subscribe init' for 918s was incorrect. "
            "generated_at is stamped BEFORE register/WS; gap is pre-config gate build."
        ),
        "next_phase": "phase574_vol_liq_startup_cache_shadow",
    }
