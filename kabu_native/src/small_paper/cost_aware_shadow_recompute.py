"""Offline Cost-Aware Shadow replay + finalize for session artifacts (Paper only)."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from small_paper.am_pm_session_policy import AmPmSessionPolicy
from small_paper.cost_aware_entry_shadow import (
    CostAwareShadowState,
    attach_runtime_compatible_to_closed_trades,
    finalize_never_filled,
    finalize_open_positions,
    note_symbol_eval,
    run_selection_cycle,
    summarize_state,
)
from small_paper.cost_aware_price_path import (
    build_symbol_price_paths,
    last_valid_price_at_or_before,
    parse_ts,
)

JST = ZoneInfo("Asia/Tokyo")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _force_close_dt(trading_date: str, session_kind: str) -> datetime:
    policy = AmPmSessionPolicy.from_kind(session_kind)
    y, m, d = int(trading_date[:4]), int(trading_date[4:6]), int(trading_date[6:8])
    hh, mm = map(int, policy.force_close.split(":"))
    return datetime(y, m, d, hh, mm, tzinfo=JST)


def _session_kind_from_dir(session_dir: Path, summary: Mapping[str, Any]) -> str:
    am_pm = summary.get("am_pm_session")
    if isinstance(am_pm, Mapping):
        k = str(am_pm.get("kind") or "").upper()
        if k in ("AM", "PM"):
            return k
    name = session_dir.name
    # morning sessions typically start 08xx
    if "080" in name or "081" in name or "082" in name or "083" in name or "084" in name or "085" in name:
        return "AM"
    return "PM"


def replay_cost_aware_session(
    session_dir: Path,
    *,
    trading_date: str,
    is_freeze_recovery: bool = True,
) -> dict[str, Any]:
    """Full Cost-Aware pipeline: entry → price path → 30m/session finalize → runtime-compatible."""
    events_path = session_dir / "small_paper_events.jsonl"
    audit_path = session_dir / "entry_scan_audit.jsonl"
    summary_path = session_dir / "small_paper_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    session_kind = _session_kind_from_dir(session_dir, summary)
    force_close = _force_close_dt(trading_date, session_kind)

    events = _load_jsonl(events_path)
    price_paths = build_symbol_price_paths(events)

    cand_by_sym: dict[str, list[tuple[datetime, dict]]] = defaultdict(list)
    official_accepts: list[tuple[datetime, str]] = []
    official_exits: list[tuple[datetime, str, float, str]] = []  # ts, sym, price, reason
    for e in events:
        et = e.get("event_type")
        if et == "candidate":
            ts = parse_ts(e.get("event_time") or e.get("timestamp") or e.get("eval_end_ts"))
            if ts:
                cand_by_sym[str(e.get("symbol"))].append((ts, e))
        elif et == "accepted":
            ts = parse_ts(e.get("entry_time") or e.get("event_time"))
            if ts:
                official_accepts.append((ts, str(e.get("symbol"))))
        elif et == "observer_exit":
            ts = parse_ts(e.get("exit_time") or e.get("event_time"))
            try:
                px = float(e.get("exit_price") or 0)
            except (TypeError, ValueError):
                px = 0.0
            if ts:
                official_exits.append((ts, str(e.get("symbol")), px, str(e.get("exit_reason") or "")))
    for sym in cand_by_sym:
        cand_by_sym[sym].sort(key=lambda x: x[0])
    official_exits.sort(key=lambda x: x[0])

    scans: dict[str, list[dict]] = defaultdict(list)
    scan_times: dict[str, datetime] = {}
    for e in _load_jsonl(audit_path):
        if e.get("audit_type") != "entry_symbol_eval":
            continue
        sid = str(e.get("scan_id") or "")
        if not sid:
            continue
        scans[sid].append(e)
        t = parse_ts(e.get("eval_end_ts") or e.get("eval_start_ts"))
        if t and (sid not in scan_times or t < scan_times[sid]):
            scan_times[sid] = t

    def nearest_trade(sym: str, t: datetime) -> Optional[dict]:
        arr = cand_by_sym.get(sym) or []
        best = None
        for ts, row in arr:
            if ts <= t:
                best = row
            else:
                break
        return best or (arr[0][1] if arr else None)

    state = CostAwareShadowState()
    ordered = sorted(scans.keys(), key=lambda s: scan_times.get(s) or datetime.min.replace(tzinfo=JST))
    for sid in ordered:
        t = scan_times[sid]
        for ev in scans[sid]:
            trade = nearest_trade(str(ev.get("symbol")), t)
            if trade is None:
                continue
            note_symbol_eval(
                state,
                scan_id=sid,
                symbol=str(ev.get("symbol")),
                trade=trade,
                official_accept=False,
            )
        offs = [sym for ots, sym in official_accepts if abs((ots - t).total_seconds()) <= 3]
        run_selection_cycle(
            state,
            scan_id=sid,
            cycle_time=t,
            trading_date=trading_date,
            official_accepted_symbols=offs,
        )
        # feed marks for open symbols at this scan time (no future)
        for sym, pos in list(state.open_shadow.items()):
            hit = last_valid_price_at_or_before(
                price_paths.get(sym, []), asof=t, not_before=pos.entry_time
            )
            if hit:
                _pts, px, _age = hit
                pos.last_mark_price = px
                pos.last_mark_time = _pts
                if not pos.price_path or pos.price_path[-1][0] != _pts:
                    pos.price_path.append((_pts, px))

    finalize_never_filled(state)

    # Close expired with full price paths (handles deferred closes)
    # Drain by walking time: for each open, try 30m close then session finalize
    # First: attempt fixed_30m for anyone past 30m using price_paths
    from small_paper.cost_aware_entry_shadow import _close_expired

    _close_expired(state, now=force_close, trading_date=trading_date, price_paths=price_paths)

    # Force-close remaining opens at session/freeze
    finalize_n = finalize_open_positions(
        state,
        force_close_time=force_close,
        trading_date=trading_date,
        price_paths=price_paths,
        is_freeze_recovery=is_freeze_recovery,
    )

    # Attach runtime-compatible evaluation for every closed trade (shared helper).
    enriched_closed, join_stats = attach_runtime_compatible_to_closed_trades(
        state.closed_trades,
        official_exits=official_exits,
        price_paths=price_paths,
        force_close_time=force_close,
    )
    state.closed_trades = enriched_closed
    out = summarize_state(state)
    out.update(join_stats)
    out["session_kind"] = session_kind
    out["force_close_time"] = force_close.isoformat()
    out["finalize_open_count"] = finalize_n
    out["session_dir"] = str(session_dir)
    return out


def merge_cost_aware_daily(am: Mapping[str, Any], pm: Mapping[str, Any]) -> dict[str, Any]:
    def add(a, b):
        if a is None and b is None:
            return None
        return round(float(a or 0) + float(b or 0), 2)

    keys_sum = (
        "fixed_30m_raw",
        "fixed_30m_5bps_roundtrip",
        "runtime_compatible_raw",
        "runtime_compatible_5bps_roundtrip",
        "shadow_entries",
        "n_closed",
        "n_open",
        "recovery_finalize_count",
        "session_force_close_finalize_count",
        "fixed_30m_wins",
        "fixed_30m_losses",
        "fixed_30m_flats",
        "runtime_compatible_wins",
        "runtime_compatible_losses",
        "runtime_compatible_flats",
        "selection_cycles",
        "stop_risk_reject",
        "official_entry_match",
        "official_entry_mismatch",
    )
    out = {"shadow_name": "cost_aware_entry_shadow", "session": "DAILY"}
    for k in keys_sum:
        out[k] = add(am.get(k), pm.get(k))
    # PF from combined win/loss yen not available without trade list — recompute from sums if possible
    # Use AM+PM net lists not stored; approximate PF from session PFs only if both present — better None and recompute
    from small_paper.cost_aware_entry_shadow import _pf_from_yen

    # rebuild from closed trades if present
    trades = list(am.get("closed_trades") or []) + list(pm.get("closed_trades") or [])
    net30 = [t["net_pnl_yen_100"] for t in trades if isinstance(t.get("net_pnl_yen_100"), (int, float))]
    rtnet = [
        t["runtime_compatible_net_yen"]
        for t in trades
        if isinstance(t.get("runtime_compatible_net_yen"), (int, float)) and not t.get("runtime_compatible_na")
    ]
    out["fixed_30m_pf_5bps"] = _pf_from_yen(net30) if net30 else None
    out["runtime_compatible_pf_5bps"] = _pf_from_yen(rtnet) if rtnet else None
    out["shadow_pf_5bps_30m"] = out["fixed_30m_pf_5bps"]
    out["gross_pnl_30m"] = out["fixed_30m_raw"]
    out["pnl_after_5bps_30m"] = out["fixed_30m_5bps_roundtrip"]
    out["runtime_compatible_pnl"] = out["runtime_compatible_raw"]
    out["n_open"] = int(am.get("n_open") or 0) + int(pm.get("n_open") or 0)
    out["status"] = (
        "RUNNING_PNL_COMPLETE"
        if out["n_open"] == 0
        and out.get("fixed_30m_raw") is not None
        and out.get("runtime_compatible_raw") is not None
        else "PARTIAL_PIPELINE"
    )
    out["closed_trades"] = trades
    return out
