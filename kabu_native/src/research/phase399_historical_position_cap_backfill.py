"""
Phase399: Historical Position-CAP backfill + capital_shadow_1500k (research only).

Rebuilds comparable history for 20260529-20260615 live sessions under the new
Position-CAP definition (structural EXIT-held slots). Does not modify Runtime,
YAML, or session artifacts.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import normalize_structural_trade
from research.phase269_portfolio_configuration_optimization import FIXED_SPEC
from research.phase271_leverage_attribution_and_robustness import simulate_audited
from research.phase382_capital_constrained_backtest import (
    _day_from_ts,
    _float,
    _parse_ts,
    _position_key,
    _trade_pnl_yen,
    _write_csv,
)
from research.phase383_realistic_credit_sizing_backtest import build_event_timeline
from research.phase385_cap_sensitivity_study import (
    CapScenarioState,
    cap_scenario_id,
    simulate_cap,
)
from research.phase395_position_cap_alignment import (
    CAP,
    INITIAL_EQUITY_1500K,
    LEVERAGE,
    STOP_POLICY,
    VirtualHoldCapSim,
    _cap_passing_stream,
    _match_structural,
    _norm_symbol,
    _pnl_yen_100,
    _read_events_csv,
    _read_structural_trades,
    _structural_lookup,
)
from research.structural_trades_backfill import (
    LIVE_SESSION_PREFIXES,
    _is_debug_session,
    _resolve_poll_interval_sec,
    _resolve_structural_exit_policy,
    _session_source,
)

JST = ZoneInfo("Asia/Tokyo")
EQUITY_FLOOR_1500K = 750_000.0
FIXTURE_SESSION = "20260615/live_session_122531"
FIXTURE_POSITION_CAP_ACCEPTED = 22
FIXTURE_CAPITAL_SHADOW_ACCEPTED = 22
FIXTURE_CAPITAL_SHADOW_PNL = 18_700.0

POSITION_CAP_REJECT = "reject_position_cap_backfill"

TRADE_CSV_FIELDS = [
    "day",
    "session",
    "symbol",
    "entry_time",
    "exit_time",
    "legacy_accepted",
    "position_cap_accepted",
    "position_cap_reject_reason",
    "capital_shadow_accepted",
    "capital_shadow_reject_reason",
    "pnl_yen_100",
    "exit_reason",
]

DAILY_CSV_FIELDS = [
    "day",
    "sessions",
    "legacy_trade_count",
    "position_cap_trade_count",
    "capital_shadow_trade_count",
    "legacy_pnl_yen_100",
    "position_cap_pnl_yen_100",
    "capital_shadow_pnl_yen_100",
    "position_cap_max_open",
    "capital_shadow_final_equity",
    "session_close_exit_burst_count",
]

SESSION_CSV_FIELDS = [
    "day",
    "session",
    "session_dir",
    "status",
    "source",
    "structural_source",
    "legacy_trade_count",
    "position_cap_trade_count",
    "capital_shadow_trade_count",
    "legacy_pnl_yen_100",
    "position_cap_pnl_yen_100",
    "capital_shadow_pnl_yen_100",
    "position_cap_max_open",
    "capital_shadow_max_open",
    "session_close_exit_burst_count",
    "accepted_stream_position_cap_count",
    "error",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _is_live_session_dir(session_dir: Path) -> bool:
    name = session_dir.name
    if any(name.startswith(prefix) for prefix in LIVE_SESSION_PREFIXES):
        return True
    return _session_source(session_dir) == "live"


def discover_sessions(
    *,
    small_paper_root: Path,
    start_day: str,
    end_day: str,
) -> list[Path]:
    if not small_paper_root.is_dir():
        return []
    sessions: list[Path] = []
    for day_dir in sorted(small_paper_root.iterdir()):
        if not day_dir.is_dir():
            continue
        day = day_dir.name
        if not (day.isdigit() and len(day) == 8):
            continue
        if not (start_day <= day <= end_day):
            continue
        for session_dir in sorted(day_dir.iterdir()):
            if not session_dir.is_dir():
                continue
            if not session_dir.name.startswith(LIVE_SESSION_PREFIXES):
                continue
            sessions.append(session_dir.resolve())
    return sessions


def classify_session(session_dir: Path) -> str:
    if _is_debug_session(session_dir):
        return "skipped_debug"
    if "push-replay" in session_dir.name.lower():
        return "skipped_push_replay"
    source = _session_source(session_dir)
    if source == "push-replay":
        return "skipped_push_replay"
    if not _is_live_session_dir(session_dir):
        return "skipped_not_live"
    if source and source != "live":
        return "skipped_not_live"
    if not (session_dir / "small_paper_summary.json").is_file():
        return "skipped_missing_inputs"
    if not (session_dir / "small_paper_events.csv").is_file():
        return "skipped_missing_inputs"
    return "ok"


def _normalize_trade_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        t = dict(row)
        t["symbol"] = _norm_symbol(str(t.get("symbol") or ""))
        t["exit_time"] = t.get("close_time") or t.get("exit_time")
        t["exit_reason"] = t.get("close_reason") or t.get("exit_reason")
        ep = _float(t.get("entry_price"))
        xp = _float(t.get("close_price") or t.get("exit_price"))
        if ep and xp:
            t["exit_price"] = xp
        out.append(normalize_structural_trade(t))
    out.sort(
        key=lambda t: (
            _parse_ts(t.get("entry_time")) or datetime.min.replace(tzinfo=JST),
            str(t.get("symbol") or ""),
        )
    )
    return out


def _resolve_pilot_config_local(session_dir: Path, *, repo_root: Path) -> Any:
    from small_paper.config import load_pilot_config

    cfg_meta = _load_json(session_dir / "live_session_config.json")
    cfg_path = Path(str(cfg_meta.get("config_path") or ""))
    if not cfg_path.is_file():
        summary = _load_json(session_dir / "small_paper_summary.json")
        cfg_path = Path(str(summary.get("config_path") or ""))
    if not cfg_path.is_file():
        for fallback in (
            repo_root / "configs" / "small_paper_pilot_q070_cap3.yaml",
            repo_root / "kabu_native" / "configs" / "small_paper_pilot_q070_cap3.yaml",
        ):
            if fallback.is_file():
                cfg_path = fallback
                break
    return load_pilot_config(cfg_path)


def _build_structural_trades_in_memory(session_dir: Path, *, repo_root: Path) -> list[dict[str, Any]]:
    from research.structural_observer_review import run_structural_observer_review

    config = _resolve_pilot_config_local(session_dir, repo_root=repo_root)
    poll_interval_sec = _resolve_poll_interval_sec(session_dir, config)
    structural_exit_policy = _resolve_structural_exit_policy(session_dir, config)
    review = run_structural_observer_review(
        session_dir,
        pilot_config=config,
        poll_interval_sec=poll_interval_sec,
        structural_exit_policy=structural_exit_policy,
    )
    return _normalize_trade_rows(review.get("_structural_trades") or [])


def _load_structural_trades(
    session_dir: Path,
    *,
    repo_root: Path,
    force_structural_backfill: bool,
) -> tuple[list[dict[str, Any]], str]:
    path = session_dir / "structural_trades.csv"
    if path.is_file() and not force_structural_backfill:
        return _normalize_trade_rows(_read_structural_trades(path)), "existing"
    return _build_structural_trades_in_memory(session_dir, repo_root=repo_root), "in_memory_backfill"


def _legacy_gate_accepted_keys(session_dir: Path, structural: Sequence[Mapping[str, Any]]) -> set[str]:
    events = _read_events_csv(session_dir / "small_paper_events.csv")
    struct_by_key = _structural_lookup(structural)
    keys: set[str] = set()
    for row in _cap_passing_stream(events, include_cap_rejected=False):
        struct = _match_structural(row, struct_by_key, structural)
        if struct:
            keys.add(_position_key(struct))
        else:
            keys.add(
                _position_key(
                    {
                        "symbol": _norm_symbol(row.get("symbol", "")),
                        "entry_time": row.get("entry_time"),
                    }
                )
            )
    return keys


def _legacy_virtual_hold_runtime(session_dir: Path) -> dict[str, int]:
    events = _read_events_csv(session_dir / "small_paper_events.csv")
    gate_stream = _cap_passing_stream(events, include_cap_rejected=True)
    vh = VirtualHoldCapSim()
    for row in gate_stream:
        vh.try_entry(row)
    return {
        "legacy_vh_accepted": len(vh.accepted),
        "legacy_vh_rejected_cap": len(vh.rejected_cap),
        "legacy_vh_peak_slots": vh.max_active,
    }


def _replay_position_cap_with_state(trades: Sequence[Mapping[str, Any]]) -> tuple[CapScenarioState, dict[str, dict[str, Any]]]:
    state = CapScenarioState(
        scenario_id=cap_scenario_id(CAP),
        max_concurrent_positions=CAP,
        spec=dict(FIXED_SPEC),
        initial_equity=INITIAL_EQUITY_1500K,
        equity_floor=EQUITY_FLOOR_1500K,
    )
    decisions: dict[str, dict[str, Any]] = {}
    events = build_event_timeline(trades)
    for dt, _, kind, trade in events:
        ts = dt.isoformat()
        day = _day_from_ts(ts)
        key = _position_key(trade)
        if kind == "entry":
            cap_rej_before = state.position_cap_reject_count
            was_accepted = key in state.accepted_keys
            state.try_entry(trade, ts, day)
            if key in state.accepted_keys and not was_accepted:
                decisions[key] = {"accepted": True, "reason": ""}
            elif state.position_cap_reject_count > cap_rej_before:
                decisions[key] = {"accepted": False, "reason": POSITION_CAP_REJECT}
            else:
                decisions[key] = {"accepted": False, "reason": "rejected_other"}
        else:
            state.process_exit(trade, ts, day)
    if state.open_positions and events:
        last_ts = events[-1][0].isoformat()
        last_day = _day_from_ts(last_ts)
        state._force_close_all(last_ts, last_day, reason="end_of_period")
    return state, decisions


def _position_cap_backfill(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Structural timeline position-CAP (matches Phase395/396 simulate_cap)."""
    state, entry_log = _replay_position_cap_with_state(trades)
    total_pnl = round(state.realized_pnl, 2)
    return {
        "accepted_trade_count": state.accepted_trade_count,
        "position_cap_reject_count": state.position_cap_reject_count,
        "max_concurrent_positions_observed": state.max_concurrent_positions_observed,
        "total_pnl_yen_100": total_pnl,
        "final_equity": round(state.current_equity(), 2),
        "entry_log": entry_log,
        "accepted_keys": set(state.accepted_keys),
        "_trade_log": state.trade_log,
    }


def _capital_shadow_1500k(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cap = simulate_cap(
        list(trades),
        cap=CAP,
        initial_equity=INITIAL_EQUITY_1500K,
        equity_floor=EQUITY_FLOOR_1500K,
    )
    audited = simulate_audited(
        list(trades),
        starting_equity=int(INITIAL_EQUITY_1500K),
        leverage=LEVERAGE,
        cap=CAP,
        stop_policy=STOP_POLICY,
    )
    state = audited.get("_state")
    accepted_keys = set(getattr(state, "accepted_keys", set()) or []) if state else set()
    reject_by_key: dict[str, str] = {}
    for row in audited.get("reject_log") or []:
        key = str(row.get("key") or "")
        if key:
            reject_by_key[key] = str(row.get("reason") or "rejected")
    max_open = int(getattr(state, "max_concurrent_positions_observed", 0) or 0) if state else 0
    pnl = round(float(cap.get("total_pnl_yen_100") or 0.0), 2)
    final_equity = round(float(audited.get("final_equity") or cap.get("final_equity") or INITIAL_EQUITY_1500K), 2)
    return {
        "accepted": int(audited.get("accepted_trade_count") or cap.get("accepted_trade_count") or 0),
        "accepted_keys": accepted_keys,
        "reject_by_key": reject_by_key,
        "max_open": max(max_open, int(cap.get("max_concurrent_positions_observed") or 0)),
        "pnl_yen_100": pnl,
        "final_equity": final_equity,
        "audited": audited,
        "trade_log": list(cap.get("_trade_log") or []),
    }


def _accepted_stream_position_cap_count(
    session_dir: Path,
    structural: Sequence[Mapping[str, Any]],
    *,
    force_close_time: str = "2026-06-15T15:23:00+09:00",
) -> int:
    """Supplementary: gate-accepted stream re-evaluated under Position-CAP (not Phase395/397 aggregate)."""
    from research.phase395_position_cap_alignment import FORCE_CLOSE_TIME, PositionCapSim

    events = _read_events_csv(session_dir / "small_paper_events.csv")
    struct_by_key = _structural_lookup(structural)
    fct = force_close_time if session_dir.parent.name == "20260615" else force_close_time
    pc = PositionCapSim()
    for row in _cap_passing_stream(events, include_cap_rejected=False):
        struct = _match_structural(row, struct_by_key, structural)
        if not struct:
            continue
        exit_time = str(struct.get("exit_time") or struct.get("close_time") or fct)
        pc.try_entry(row, structural_exit_time=exit_time, structural_trade=struct)
    return len(pc.accepted)


def _session_close_exit_burst(session_dir: Path) -> int:
    events = _read_events_csv(session_dir / "small_paper_events.csv")
    day = session_dir.parent.name
    prefix = f"{day[:4]}-{day[4:6]}-{day[6:8]}T15:23"
    return sum(
        1
        for row in events
        if str(row.get("event_type")) == "observer_exit"
        and str(row.get("exit_time", "")).startswith(prefix)
    )


def _build_trade_rows(
    *,
    day: str,
    session: str,
    trades: Sequence[Mapping[str, Any]],
    legacy_keys: set[str],
    pc_entry_log: Mapping[str, Mapping[str, Any]],
    pc_accepted_keys: set[str],
    capital: Mapping[str, Any],
) -> list[dict[str, Any]]:
    reject_by_key = dict(capital.get("reject_by_key") or {})
    capital_accepted = set(capital.get("accepted_keys") or [])
    rows: list[dict[str, Any]] = []
    for trade in trades:
        key = _position_key(trade)
        pc_dec = pc_entry_log.get(key) or {}
        pc_acc = bool(pc_dec.get("accepted")) or key in pc_accepted_keys
        pc_reason = str(pc_dec.get("reason") or "")
        if not pc_acc and not pc_reason:
            pc_reason = POSITION_CAP_REJECT if key not in pc_accepted_keys else ""
        cap_acc = key in capital_accepted
        cap_reason = "" if cap_acc else str(reject_by_key.get(key) or "")
        pnl = _pnl_yen_100(trade) if pc_acc or cap_acc else 0.0
        rows.append(
            {
                "day": day,
                "session": session,
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": trade.get("exit_time") or trade.get("close_time"),
                "legacy_accepted": key in legacy_keys,
                "position_cap_accepted": pc_acc,
                "position_cap_reject_reason": pc_reason,
                "capital_shadow_accepted": cap_acc,
                "capital_shadow_reject_reason": cap_reason,
                "pnl_yen_100": pnl if (pc_acc or cap_acc) else "",
                "exit_reason": trade.get("exit_reason") or trade.get("close_reason") or "",
            }
        )
    return rows


def process_session(
    session_dir: Path,
    *,
    repo_root: Path,
    force_structural_backfill: bool = False,
) -> dict[str, Any]:
    day = session_dir.parent.name
    session = session_dir.name
    status = classify_session(session_dir)
    base: dict[str, Any] = {
        "day": day,
        "session": session,
        "session_dir": str(session_dir),
        "status": status,
        "source": _session_source(session_dir),
        "structural_source": "",
        "legacy_trade_count": 0,
        "position_cap_trade_count": 0,
        "capital_shadow_trade_count": 0,
        "legacy_pnl_yen_100": 0.0,
        "position_cap_pnl_yen_100": 0.0,
        "capital_shadow_pnl_yen_100": 0.0,
        "position_cap_max_open": 0,
        "capital_shadow_max_open": 0,
        "capital_shadow_final_equity": INITIAL_EQUITY_1500K,
        "session_close_exit_burst_count": 0,
        "accepted_stream_position_cap_count": 0,
        "trade_rows": [],
        "error": "",
        "error_type": "",
        "error_message": "",
    }
    if status != "ok":
        return base

    try:
        summary = _load_json(session_dir / "small_paper_summary.json")
        base["source"] = str(summary.get("source") or base["source"] or "live")
        base["legacy_trade_count"] = int(summary.get("accepted_count") or 0)

        trades, structural_source = _load_structural_trades(
            session_dir,
            repo_root=repo_root,
            force_structural_backfill=force_structural_backfill,
        )
        base["structural_source"] = structural_source
        if not trades:
            base["status"] = "skipped_no_structural_trades"
            return base

        legacy_keys = _legacy_gate_accepted_keys(session_dir, trades)
        _legacy_virtual_hold_runtime(session_dir)

        pc = _position_cap_backfill(trades)
        capital = _capital_shadow_1500k(trades)

        legacy_pnl = round(
            sum(_pnl_yen_100(t) for t in trades if _position_key(t) in legacy_keys),
            2,
        )
        pc_pnl = round(float(pc.get("total_pnl_yen_100") or 0.0), 2)
        cap_pnl = float(capital.get("pnl_yen_100") or 0.0)

        base.update(
            {
                "position_cap_trade_count": int(pc.get("accepted_trade_count") or 0),
                "capital_shadow_trade_count": int(capital.get("accepted") or 0),
                "legacy_pnl_yen_100": legacy_pnl,
                "position_cap_pnl_yen_100": pc_pnl,
                "capital_shadow_pnl_yen_100": cap_pnl,
                "position_cap_max_open": int(pc.get("max_concurrent_positions_observed") or 0),
                "capital_shadow_max_open": int(capital.get("max_open") or 0),
                "capital_shadow_final_equity": float(capital.get("final_equity") or INITIAL_EQUITY_1500K),
                "session_close_exit_burst_count": _session_close_exit_burst(session_dir),
                "accepted_stream_position_cap_count": _accepted_stream_position_cap_count(session_dir, trades),
            }
        )
        base["trade_rows"] = _build_trade_rows(
            day=day,
            session=session,
            trades=trades,
            legacy_keys=legacy_keys,
            pc_entry_log=pc.get("entry_log") or {},
            pc_accepted_keys=set(pc.get("accepted_keys") or []),
            capital=capital,
        )
        base["status"] = "ok"
        return base
    except Exception as exc:
        base.update(
            {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
        )
        return base


def _worker_payload(
    session_dir: Path,
    *,
    repo_root: Path,
    force_structural_backfill: bool,
) -> dict[str, Any]:
    return {
        "session_dir": str(session_dir),
        "repo_root": str(repo_root),
        "force_structural_backfill": force_structural_backfill,
    }


def _worker_entry(payload: Mapping[str, Any]) -> dict[str, Any]:
    import sys
    from pathlib import Path as _Path

    repo = _Path(str(payload["repo_root"]))
    parent = repo.parent
    for p in (repo / "src", parent):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return process_session(
        _Path(str(payload["session_dir"])),
        repo_root=repo,
        force_structural_backfill=bool(payload.get("force_structural_backfill")),
    )


def _metrics_fingerprint(session_rows: Sequence[Mapping[str, Any]]) -> str:
    payload = []
    for row in sorted(session_rows, key=lambda r: (str(r.get("day")), str(r.get("session")))):
        if str(row.get("status")) != "ok":
            continue
        payload.append(
            {
                "day": row.get("day"),
                "session": row.get("session"),
                "position_cap_trade_count": row.get("position_cap_trade_count"),
                "capital_shadow_trade_count": row.get("capital_shadow_trade_count"),
                "position_cap_pnl_yen_100": row.get("position_cap_pnl_yen_100"),
                "capital_shadow_pnl_yen_100": row.get("capital_shadow_pnl_yen_100"),
            }
        )
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _equity_curve_stats(session_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    equity = INITIAL_EQUITY_1500K
    peak = equity
    max_dd_pct = 0.0
    days_below_50 = 0
    daily_pnl: dict[str, float] = {}
    for row in sorted(session_rows, key=lambda r: str(r.get("day") or "")):
        if str(row.get("status")) != "ok":
            continue
        day = str(row.get("day") or "")
        pnl = float(row.get("capital_shadow_pnl_yen_100") or 0.0)
        daily_pnl[day] = daily_pnl.get(day, 0.0) + pnl
    for day in sorted(daily_pnl):
        equity = round(equity + daily_pnl[day], 2)
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
        max_dd_pct = max(max_dd_pct, dd)
        if equity < INITIAL_EQUITY_1500K * 0.5:
            days_below_50 += 1
    return {
        "capital_shadow_final_equity": round(equity, 2),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "days_below_50pct": days_below_50,
    }


def reduce_session_results(
    session_rows: Sequence[Mapping[str, Any]],
    *,
    start_day: str,
    end_day: str,
) -> dict[str, Any]:
    trade_rows: list[dict[str, Any]] = []
    daily: dict[str, dict[str, Any]] = {}
    failed_sessions: list[dict[str, str]] = []
    counters = {
        "discovered_sessions": len(session_rows),
        "processed_sessions": 0,
        "skipped_push_replay": 0,
        "skipped_debug": 0,
        "skipped_not_live": 0,
        "skipped_missing_inputs": 0,
        "skipped_no_structural_trades": 0,
        "structural_backfilled": 0,
        "failed_sessions": 0,
    }
    ok_rows: list[dict[str, Any]] = []

    for row in session_rows:
        status = str(row.get("status") or "")
        if status == "skipped_push_replay":
            counters["skipped_push_replay"] += 1
            continue
        if status == "skipped_debug":
            counters["skipped_debug"] += 1
            continue
        if status == "skipped_not_live":
            counters["skipped_not_live"] += 1
            continue
        if status == "skipped_missing_inputs":
            counters["skipped_missing_inputs"] += 1
            continue
        if status == "skipped_no_structural_trades":
            counters["skipped_no_structural_trades"] += 1
            continue
        if status == "failed":
            counters["failed_sessions"] += 1
            failed_sessions.append(
                {
                    "session_dir": str(row.get("session_dir") or ""),
                    "error_type": str(row.get("error_type") or ""),
                    "error_message": str(row.get("error_message") or row.get("error") or ""),
                }
            )
            continue
        if status != "ok":
            continue

        counters["processed_sessions"] += 1
        ok_rows.append(dict(row))
        if str(row.get("structural_source") or "") == "in_memory_backfill":
            counters["structural_backfilled"] += 1

        day = str(row.get("day") or "")
        d = daily.setdefault(
            day,
            {
                "day": day,
                "sessions": 0,
                "legacy_trade_count": 0,
                "position_cap_trade_count": 0,
                "capital_shadow_trade_count": 0,
                "legacy_pnl_yen_100": 0.0,
                "position_cap_pnl_yen_100": 0.0,
                "capital_shadow_pnl_yen_100": 0.0,
                "position_cap_max_open": 0,
                "capital_shadow_final_equity": INITIAL_EQUITY_1500K,
                "session_close_exit_burst_count": 0,
            },
        )
        d["sessions"] += 1
        d["legacy_trade_count"] += int(row.get("legacy_trade_count") or 0)
        d["position_cap_trade_count"] += int(row.get("position_cap_trade_count") or 0)
        d["capital_shadow_trade_count"] += int(row.get("capital_shadow_trade_count") or 0)
        d["legacy_pnl_yen_100"] = round(float(d["legacy_pnl_yen_100"]) + float(row.get("legacy_pnl_yen_100") or 0.0), 2)
        d["position_cap_pnl_yen_100"] = round(
            float(d["position_cap_pnl_yen_100"]) + float(row.get("position_cap_pnl_yen_100") or 0.0), 2
        )
        d["capital_shadow_pnl_yen_100"] = round(
            float(d["capital_shadow_pnl_yen_100"]) + float(row.get("capital_shadow_pnl_yen_100") or 0.0), 2
        )
        d["position_cap_max_open"] = max(d["position_cap_max_open"], int(row.get("position_cap_max_open") or 0))
        d["capital_shadow_final_equity"] = round(
            float(d["capital_shadow_final_equity"]) + float(row.get("capital_shadow_pnl_yen_100") or 0.0),
            2,
        )
        d["session_close_exit_burst_count"] += int(row.get("session_close_exit_burst_count") or 0)
        trade_rows.extend(list(row.get("trade_rows") or []))

    daily_rows = [daily[k] for k in sorted(daily)]
    days = sorted(daily.keys())

    totals = {
        "legacy_total_trades": sum(int(r.get("legacy_trade_count") or 0) for r in ok_rows),
        "position_cap_total_trades": sum(int(r.get("position_cap_trade_count") or 0) for r in ok_rows),
        "capital_shadow_total_trades": sum(int(r.get("capital_shadow_trade_count") or 0) for r in ok_rows),
        "legacy_total_pnl_yen_100": round(sum(float(r.get("legacy_pnl_yen_100") or 0.0) for r in ok_rows), 2),
        "position_cap_total_pnl_yen_100": round(
            sum(float(r.get("position_cap_pnl_yen_100") or 0.0) for r in ok_rows), 2
        ),
        "capital_shadow_total_pnl_yen_100": round(
            sum(float(r.get("capital_shadow_pnl_yen_100") or 0.0) for r in ok_rows), 2
        ),
    }
    equity_stats = _equity_curve_stats(ok_rows)
    totals["capital_shadow_final_equity"] = equity_stats["capital_shadow_final_equity"]

    fixture = next((r for r in ok_rows if f"{r.get('day')}/{r.get('session')}" == FIXTURE_SESSION), None)
    fixture_pass = bool(
        fixture
        and int(fixture.get("position_cap_trade_count") or 0) == FIXTURE_POSITION_CAP_ACCEPTED
        and int(fixture.get("capital_shadow_trade_count") or 0) == FIXTURE_CAPITAL_SHADOW_ACCEPTED
        and float(fixture.get("capital_shadow_pnl_yen_100") or 0.0) == FIXTURE_CAPITAL_SHADOW_PNL
    )
    all_max_open_ok = all(
        int(r.get("position_cap_max_open") or 0) <= CAP and int(r.get("capital_shadow_max_open") or 0) <= CAP
        for r in ok_rows
    )

    if counters["processed_sessions"] == 0:
        verdict = "insufficient_candidate_stream"
    elif fixture_pass and all_max_open_ok:
        verdict = "historical_backfill_ready"
    else:
        verdict = "insufficient_candidate_stream"

    return {
        "trade_rows": trade_rows,
        "daily_rows": daily_rows,
        "session_rows": [dict(r) for r in session_rows],
        "failed_sessions": failed_sessions,
        "counters": counters,
        "days": days,
        "totals": totals,
        "equity_stats": equity_stats,
        "validation": {
            "fixture_session": FIXTURE_SESSION,
            "fixture_position_cap_accepted": FIXTURE_POSITION_CAP_ACCEPTED,
            "fixture_capital_shadow_accepted": FIXTURE_CAPITAL_SHADOW_ACCEPTED,
            "fixture_capital_shadow_pnl_yen_100": FIXTURE_CAPITAL_SHADOW_PNL,
            "fixture_pass": fixture_pass,
            "fixture_actual": (
                {
                    "position_cap_trade_count": fixture.get("position_cap_trade_count"),
                    "capital_shadow_trade_count": fixture.get("capital_shadow_trade_count"),
                    "capital_shadow_pnl_yen_100": fixture.get("capital_shadow_pnl_yen_100"),
                    "accepted_stream_position_cap_count": fixture.get("accepted_stream_position_cap_count"),
                }
                if fixture
                else None
            ),
            "all_max_open_le_cap": all_max_open_ok,
        },
        "metrics_fingerprint": _metrics_fingerprint(ok_rows),
        "verdict": verdict,
    }


def run_phase399_backfill(
    *,
    repo_root: Path,
    start_day: str,
    end_day: str,
    output_dir: Path,
    parallel: bool = True,
    max_workers: int = 4,
    force_structural_backfill: bool = False,
) -> dict[str, Any]:
    small_paper_root = repo_root / "results" / "small_paper"
    sessions = discover_sessions(
        small_paper_root=small_paper_root,
        start_day=start_day,
        end_day=end_day,
    )
    workers = max(1, int(max_workers)) if parallel else 1
    payloads = [
        _worker_payload(s, repo_root=repo_root, force_structural_backfill=force_structural_backfill)
        for s in sessions
    ]

    if workers <= 1:
        session_rows = [_worker_entry(p) for p in payloads]
    else:
        session_rows = []
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_worker_entry, p) for p in payloads]
            for fut in as_completed(futures):
                session_rows.append(fut.result())

    reduced = reduce_session_results(session_rows, start_day=start_day, end_day=end_day)
    summary = {
        "phase": 399,
        "generated_at": _now_iso(),
        "period_start": start_day,
        "period_end": end_day,
        "days": reduced["days"],
        **reduced["totals"],
        **reduced["equity_stats"],
        "parallel": parallel,
        "max_workers": workers,
        "force_structural_backfill": force_structural_backfill,
        "constraints": {
            "runtime_changes_forbidden": True,
            "session_artifact_mutation_forbidden": True,
            "parent_only_report_writes": True,
            "candidate_stream_limitation": (
                "Old max_concurrent rejects lack structural exits; "
                "backfill re-evaluates observed gate-accepted trades under Position-CAP."
            ),
        },
        **reduced["counters"],
        "failed_session_count": len(reduced["failed_sessions"]),
        "failed_sessions": reduced["failed_sessions"],
        "validation": reduced["validation"],
        "metrics_fingerprint": reduced["metrics_fingerprint"],
        "verdict": reduced["verdict"],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    session_export = [{k: row.get(k, "") for k in SESSION_CSV_FIELDS} for row in reduced["session_rows"]]

    _write_csv(output_dir / "phase399_historical_position_cap_backfill_trades.csv", reduced["trade_rows"], TRADE_CSV_FIELDS)
    _write_csv(output_dir / "phase399_historical_position_cap_backfill_daily.csv", reduced["daily_rows"], DAILY_CSV_FIELDS)
    _write_csv(
        output_dir / "phase399_historical_position_cap_backfill_by_session.csv",
        session_export,
        SESSION_CSV_FIELDS,
    )
    (output_dir / "phase399_historical_position_cap_backfill_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    report_path = repo_root / "docs" / "operations" / "phase399_historical_position_cap_backfill_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        _build_report_markdown(summary=summary, daily_rows=reduced["daily_rows"]),
        encoding="utf-8",
    )

    return {
        "summary": summary,
        "session_rows": reduced["session_rows"],
        "trade_rows": reduced["trade_rows"],
        "daily_rows": reduced["daily_rows"],
        "report_path": str(report_path),
    }


def _build_report_markdown(
    *,
    summary: Mapping[str, Any],
    daily_rows: Sequence[Mapping[str, Any]],
) -> str:
    val = summary.get("validation") or {}
    fixture_actual = val.get("fixture_actual") or {}
    lines = [
        "# Phase399 — Historical Position-CAP Backfill",
        "",
        f"Generated: {summary.get('generated_at')}",
        "",
        f"## Verdict: **{summary.get('verdict')}**",
        "",
        "## 重要な制限（必読）",
        "",
        "1. **過去 Runtime とは別基準** — 旧 Runtime は 5 分 virtual-hold CAP、本 backfill は structural EXIT まで拘束する Position-CAP。",
        "2. **旧 max_concurrent reject 候補は完全復元不可** — reject された候補には structural exit が無く、母集団は gate-accepted 実観測トレードに限定される。",
        "3. **連続履歴としての用途** — 6/16 以降の新 Runtime（Position-CAP Mode）と比較可能な再計算系列として使用する（Runtime 採用判定ではない）。",
        "",
        "### 再計算モデル",
        "",
        "| モデル | 説明 |",
        "|--------|------|",
        "| **A. legacy_virtual_hold_runtime** | 旧 Runtime 相当（5分 VH CAP）。`small_paper_summary.json` の accepted 件数を参照。 |",
        "| **B. position_cap_backfill** | 新 Runtime 相当。CAP=3、structural EXIT まで拘束。structural タイムラインで再評価（Phase395/396/397 一致）。 |",
        "| **C. capital_shadow_1500k** | 1.5M / lev2 / 100株 / CAP3 / fixed_stop_1p2（Phase267–274 エンジン）。 |",
        "",
        "### 明日以降の評価基準",
        "",
        "- Live Runtime: `position_cap_mode=true` → observer open ≤3 until structural EXIT",
        "- 本履歴: モデル B/C の session 集計を forward 比較のベースラインとする",
        "- 150万円資産曲線: `capital_shadow_*` 列（モデル C）",
        "",
        f"### Period: `{summary.get('period_start')}` – `{summary.get('period_end')}`",
        "",
        "### 集計サマリー",
        "",
        f"- legacy_total_trades: {summary.get('legacy_total_trades')}",
        f"- position_cap_total_trades: {summary.get('position_cap_total_trades')}",
        f"- capital_shadow_total_trades: {summary.get('capital_shadow_total_trades')}",
        f"- legacy_total_pnl_yen_100: ¥{summary.get('legacy_total_pnl_yen_100')}",
        f"- position_cap_total_pnl_yen_100: ¥{summary.get('position_cap_total_pnl_yen_100')}",
        f"- capital_shadow_final_equity: ¥{summary.get('capital_shadow_final_equity')}",
        f"- max_drawdown_pct: {summary.get('max_drawdown_pct')}%",
        f"- days_below_50pct: {summary.get('days_below_50pct')}",
        "",
        "### 20260615 PM 一致確認 (`live_session_122531`)",
        "",
        f"| 指標 | 期待 | 実績 |",
        f"|------|------|------|",
        f"| position_cap trades | {val.get('fixture_position_cap_accepted')} | {fixture_actual.get('position_cap_trade_count')} |",
        f"| capital_shadow trades | {val.get('fixture_capital_shadow_accepted')} | {fixture_actual.get('capital_shadow_trade_count')} |",
        f"| capital_shadow PnL | ¥{val.get('fixture_capital_shadow_pnl_yen_100')} | ¥{fixture_actual.get('capital_shadow_pnl_yen_100')} |",
        f"| accepted-stream position_cap (参考) | — | {fixture_actual.get('accepted_stream_position_cap_count')} |",
        f"| fixture_pass | — | `{val.get('fixture_pass')}` |",
        "",
        "### Run stats",
        "",
        f"- processed_sessions: {summary.get('processed_sessions')}",
        f"- structural_backfilled: {summary.get('structural_backfilled')}",
        f"- skipped_push_replay: {summary.get('skipped_push_replay')}",
        f"- skipped_debug: {summary.get('skipped_debug')}",
        f"- parallel / max_workers: `{summary.get('parallel')}` / `{summary.get('max_workers')}`",
        "",
        "### Daily totals",
        "",
        "| day | sessions | legacy | position_cap | capital_shadow | position_cap PnL | capital_shadow PnL |",
        "|-----|----------|--------|--------------|----------------|------------------|------------------|",
    ]
    for row in daily_rows:
        lines.append(
            f"| {row.get('day')} | {row.get('sessions')} | {row.get('legacy_trade_count')} | "
            f"{row.get('position_cap_trade_count')} | {row.get('capital_shadow_trade_count')} | "
            f"¥{row.get('position_cap_pnl_yen_100')} | ¥{row.get('capital_shadow_pnl_yen_100')} |"
        )
    failed = summary.get("failed_sessions") or []
    if failed:
        lines.extend(["", "### Failed sessions", ""])
        for f in failed:
            lines.append(f"- `{f.get('session_dir')}`: {f.get('error_type')}: {f.get('error_message')}")
    lines.extend(
        [
            "",
            "### Artifacts",
            "",
            "- `results/reports/phase399_historical_position_cap_backfill_trades.csv`",
            "- `results/reports/phase399_historical_position_cap_backfill_daily.csv`",
            "- `results/reports/phase399_historical_position_cap_backfill_summary.json`",
            "",
        ]
    )
    return "\n".join(lines)


def compare_serial_parallel(
    *,
    repo_root: Path,
    start_day: str,
    end_day: str,
    max_workers: int = 4,
    force_structural_backfill: bool = False,
) -> dict[str, Any]:
    serial = run_phase399_backfill(
        repo_root=repo_root,
        start_day=start_day,
        end_day=end_day,
        output_dir=repo_root / "results" / "reports" / "_phase399_serial_tmp",
        parallel=False,
        max_workers=1,
        force_structural_backfill=force_structural_backfill,
    )
    parallel = run_phase399_backfill(
        repo_root=repo_root,
        start_day=start_day,
        end_day=end_day,
        output_dir=repo_root / "results" / "reports" / "_phase399_parallel_tmp",
        parallel=True,
        max_workers=max_workers,
        force_structural_backfill=force_structural_backfill,
    )
    return {
        "serial_fingerprint": serial["summary"].get("metrics_fingerprint"),
        "parallel_fingerprint": parallel["summary"].get("metrics_fingerprint"),
        "match": serial["summary"].get("metrics_fingerprint") == parallel["summary"].get("metrics_fingerprint"),
    }
