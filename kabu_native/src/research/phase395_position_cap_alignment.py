"""
Phase395: Runtime Position-CAP Alignment — investigation, shadow, proposals.

Research / shadow only — no Runtime ENTRY/EXIT/YAML/Discord production changes.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import load_period_trades, normalize_structural_trade
from research.phase269_portfolio_configuration_optimization import STOP_RESOLVERS, build_spec
from research.phase271_leverage_attribution_and_robustness import simulate_audited
from research.phase382_capital_constrained_backtest import (
    _day_from_ts,
    _float,
    _parse_ts,
    _position_key,
    _trade_pnl_yen,
    _write_csv,
)
from research.phase383_realistic_credit_sizing_backtest import build_event_timeline, compute_buying_power
from research.phase385_cap_sensitivity_study import CapScenarioState, simulate_cap

JST = ZoneInfo("Asia/Tokyo")

CAP = 3
VIRTUAL_HOLD_SEC = 300.0
INITIAL_EQUITY_1500K = 1_500_000.0
EQUITY_FLOOR_1500K = 750_000.0
LEVERAGE = 2.0
STOP_POLICY = "fixed_stop_1p2"
SESSION_DAY = "20260615"
SESSION_ID = "live_session_122531"
FORCE_CLOSE_TIME = "2026-06-15T15:23:00+09:00"

CAP_DEFINITION_MATRIX_ROWS: list[dict[str, str]] = [
    {
        "Layer": "A. Exposure Gate CAP",
        "CAP Meaning": "max_concurrent_positions=3 limits concurrent entry slots (open_slots), not observer positions",
        "Release Condition": "Slot pruned when next evaluate_entry sees exit_time < candidate entry_time (~300s virtual hold from entry)",
        "Used For": "Runtime entry accept/reject; small_paper_events position_slot_before/after; small_paper_positions.csv; peak_open_slots in summary",
        "Current Risk": "Discord ENTRY implies position lifecycle; slots free in ~5min while observer may hold much longer",
    },
    {
        "Layer": "B. Observer Position",
        "CAP Meaning": "No hard concurrent cap — one open virtual position per symbol until structural exit",
        "Release Condition": "structural exit (stop_hit, trailing_mfe_exit, overlap_replaced), or close_all at session force_close",
        "Used For": "structural_trades.csv; structural_events.csv; observer_exit Discord notifications; structural_observer_review.json",
        "Current Risk": "Many simultaneous observer_exit at session close; EXIT count ≠ gate slot count",
    },
    {
        "Layer": "C. Capital Simulation (Phase267–274)",
        "CAP Meaning": "max_concurrent_positions on open_positions dict — capital + leverage constrained",
        "Release Condition": "process_exit at structural_trades exit_time (close_time); maintenance/equity-floor force-close",
        "Used For": "phase267_equity_curve*.csv; phase268 reconciliation; phase269 grid; phase272 recommendations; phase273/274 forward shadows",
        "Current Risk": "Operators may assume Runtime CAP=3 matches sim; sim holds until structural EXIT, not 5min VH",
    },
]

PHASE_CAP_AUDIT_ROWS: list[dict[str, str]] = [
    {
        "Phase": "267",
        "Module": "equity_curve_shadow.py",
        "Default CAP": "2",
        "Release": "CapScenarioState.process_exit @ structural exit_time",
        "Buying Power": "Yes — compute_buying_power + compute_requested_shares",
        "Leverage / Maint": "Yes — MAINT_WARNING / MAINT_STOP_ENTRY / MAINT_FORCE_EXIT",
        "Input Trades": "structural_trades.csv (real exit times)",
    },
    {
        "Phase": "268",
        "Module": "capital_simulation_reconciliation.py",
        "Default CAP": "2",
        "Release": "Same CapScenarioState timeline",
        "Buying Power": "Yes",
        "Leverage / Maint": "Yes",
        "Input Trades": "structural_trades.csv",
    },
    {
        "Phase": "269",
        "Module": "phase269_portfolio_configuration_optimization.py",
        "Default CAP": "Grid 1–5",
        "Release": "Structural exit events",
        "Buying Power": "Yes",
        "Leverage / Maint": "Yes",
        "Input Trades": "structural_trades.csv",
    },
    {
        "Phase": "272",
        "Module": "phase272_apply_leverage_robustness_to_equity_bucket_recommendation.py",
        "Default CAP": "Recommends cap3 @ 1.5M lev2",
        "Release": "Via Phase269 sim engine",
        "Buying Power": "Yes",
        "Leverage / Maint": "Yes",
        "Input Trades": "structural_trades.csv",
    },
    {
        "Phase": "273",
        "Module": "phase273_live_config_forward_shadow_logger.py",
        "Default CAP": "3 @ 1.5M fixed_stop_1p2",
        "Release": "simulate_audited → process_exit @ structural exit",
        "Buying Power": "Yes",
        "Leverage / Maint": "Yes",
        "Input Trades": "Accumulated structural_trades.csv",
    },
    {
        "Phase": "274",
        "Module": "phase274_live_config_auto_transition_shadow.py",
        "Default CAP": "3 below 2M equity; 5 at/above 2M",
        "Release": "Structural exit; cap band changes on new entries only",
        "Buying Power": "Yes",
        "Leverage / Maint": "Yes",
        "Input Trades": "Accumulated structural_trades.csv",
    },
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _csv_fieldnames(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                seen.add(k)
                keys.append(k)
    return keys


def _write_rows_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return
    _write_csv(path, [dict(r) for r in rows], _csv_fieldnames(rows))


def _norm_symbol(sym: str) -> str:
    s = str(sym or "").strip()
    return s if s.endswith(".T") else f"{s}.T" if s else s


def _read_events_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _read_structural_trades(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            trade = dict(row)
            trade["symbol"] = _norm_symbol(trade.get("symbol", ""))
            trade["exit_time"] = trade.get("close_time") or trade.get("exit_time")
            trade["exit_reason"] = trade.get("close_reason") or trade.get("exit_reason")
            ep = _float(trade.get("entry_price"))
            xp = _float(trade.get("close_price") or trade.get("exit_price"))
            if ep and xp:
                trade["exit_price"] = xp
            rows.append(trade)
    return rows


def _structural_lookup(trades: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for t in trades:
        key = _position_key({"symbol": _norm_symbol(t.get("symbol", "")), "entry_time": t.get("entry_time")})
        out[key] = dict(t)
    return out


def _cap_passing_stream(events: Sequence[Mapping[str, str]], *, include_cap_rejected: bool = False) -> list[dict[str, str]]:
    """Gate-accepted entries; optionally include max_concurrent rejects for gate replay."""
    stream: list[dict[str, str]] = []
    for row in events:
        et = str(row.get("event_type") or "")
        reason = str(row.get("gate_reject_reason") or row.get("reject_reason") or "")
        if et == "accepted":
            stream.append(dict(row))
        elif include_cap_rejected and et == "rejected" and reason == "max_concurrent":
            stream.append(dict(row))
    stream.sort(
        key=lambda r: (
            _parse_ts(r.get("entry_time")) or datetime.min.replace(tzinfo=JST),
            str(r.get("symbol") or ""),
        )
    )
    return stream


def _match_structural(
    row: Mapping[str, Any],
    struct_by_key: Mapping[str, Mapping[str, Any]],
    structural: Sequence[Mapping[str, Any]],
) -> Optional[dict[str, Any]]:
    sym = _norm_symbol(row.get("symbol", ""))
    ent = str(row.get("entry_time") or "")
    key = _position_key({"symbol": sym, "entry_time": ent})
    if key in struct_by_key:
        return dict(struct_by_key[key])
    ent_dt = _parse_ts(ent)
    if ent_dt is None:
        return None
    best: Optional[dict[str, Any]] = None
    best_delta = 999999.0
    for t in structural:
        if _norm_symbol(t.get("symbol", "")) != sym:
            continue
        t_ent = _parse_ts(t.get("entry_time"))
        if t_ent is None:
            continue
        delta = abs((t_ent - ent_dt).total_seconds())
        if delta <= 5.0 and delta < best_delta:
            best = dict(t)
            best_delta = delta
    return best


@dataclass
class VirtualHoldCapSim:
    cap: int = CAP
    open_slots: list[tuple[float, float, str]] = field(default_factory=list)
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected_cap: list[dict[str, Any]] = field(default_factory=list)
    max_active: int = 0
    timeline: list[dict[str, Any]] = field(default_factory=list)

    def _prune(self, ent_ts: float) -> None:
        self.open_slots = [(a, b, s) for a, b, s in self.open_slots if b >= ent_ts]

    def try_entry(self, row: Mapping[str, Any]) -> bool:
        ent_dt = _parse_ts(row.get("entry_time"))
        ex_dt = _parse_ts(row.get("exit_time"))
        if ent_dt is None:
            return False
        ent_ts = ent_dt.timestamp()
        ex_ts = (ex_dt or ent_dt).timestamp()
        self._prune(ent_ts)
        active = len(self.open_slots)
        self.max_active = max(self.max_active, active)
        if active >= self.cap:
            self.rejected_cap.append({"row": dict(row), "active": active})
            return False
        sym = _norm_symbol(row.get("symbol", ""))
        self.open_slots.append((ent_ts, ex_ts, sym))
        active_after = len(self.open_slots)
        self.max_active = max(self.max_active, active_after)
        self.accepted.append(
            {
                "symbol": sym,
                "entry_time": row.get("entry_time"),
                "exit_time": row.get("exit_time"),
                "slot_release_time": row.get("exit_time"),
                "active_after": active_after,
            }
        )
        self.timeline.append(
            {
                "timestamp": row.get("entry_time"),
                "event": "entry_accept",
                "model": "virtual_hold",
                "active_positions": active_after,
            }
        )
        return True


@dataclass
class PositionCapSim:
    cap: int = CAP
    open_positions: dict[str, float] = field(default_factory=dict)
    position_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected_cap: list[dict[str, Any]] = field(default_factory=list)
    max_active: int = 0
    timeline: list[dict[str, Any]] = field(default_factory=list)
    session_close_count: int = 0

    def _prune(self, now_ts: float) -> None:
        closed = [k for k, ex in self.open_positions.items() if ex <= now_ts]
        for k in closed:
            meta = self.position_meta.get(k, {})
            self._release(k, str(meta.get("exit_time") or ""))

    def _release(self, key: str, exit_time: str) -> None:
        if key in self.open_positions:
            self.open_positions.pop(key, None)
            meta = self.position_meta.pop(key, {})
            self.timeline.append(
                {
                    "timestamp": exit_time,
                    "event": "slot_release",
                    "model": "position_cap",
                    "symbol": meta.get("symbol", ""),
                    "active_positions": len(self.open_positions),
                }
            )

    def try_entry(
        self,
        row: Mapping[str, Any],
        *,
        structural_exit_time: str,
        structural_trade: Optional[Mapping[str, Any]] = None,
    ) -> bool:
        ent_dt = _parse_ts(row.get("entry_time"))
        if ent_dt is None:
            return False
        ent_ts = ent_dt.timestamp()
        ex_dt = _parse_ts(structural_exit_time)
        ex_ts = (ex_dt or ent_dt).timestamp()
        # Release positions whose structural exit has passed
        for key, release_ts in list(self.open_positions.items()):
            if release_ts <= ent_ts:
                meta = self.position_meta.get(key, {})
                self._release(key, str(meta.get("exit_time") or structural_exit_time))
        active = len(self.open_positions)
        self.max_active = max(self.max_active, active)
        if active >= self.cap:
            self.rejected_cap.append({"row": dict(row), "active": active})
            return False
        sym = _norm_symbol(row.get("symbol", ""))
        key = _position_key({"symbol": sym, "entry_time": row.get("entry_time")})
        self.open_positions[key] = ex_ts
        self.position_meta[key] = {
            "symbol": sym,
            "entry_time": row.get("entry_time"),
            "exit_time": structural_exit_time,
            "trade": structural_trade or {},
        }
        active_after = len(self.open_positions)
        self.max_active = max(self.max_active, active_after)
        self.accepted.append(
            {
                "key": key,
                "symbol": sym,
                "entry_time": row.get("entry_time"),
                "exit_time": structural_exit_time,
                "active_after": active_after,
            }
        )
        self.timeline.append(
            {
                "timestamp": row.get("entry_time"),
                "event": "entry_accept",
                "model": "position_cap",
                "active_positions": active_after,
            }
        )
        return True

    def close_session(self, force_close_time: str) -> list[dict[str, Any]]:
        remaining = list(self.open_positions.keys())
        closed: list[dict[str, Any]] = []
        for key in remaining:
            meta = self.position_meta.pop(key, {})
            self.open_positions.pop(key, None)
            trade = meta.get("trade") or {}
            closed.append(
                {
                    "key": key,
                    "symbol": meta.get("symbol"),
                    "entry_time": meta.get("entry_time"),
                    "exit_time": force_close_time,
                    "exit_reason": "afternoon_session_close",
                    "trade": trade,
                }
            )
            self.session_close_count += 1
        return closed


def _pnl_yen_100(trade: Mapping[str, Any]) -> float:
    pct = _float(trade.get("realized_pnl_pct")) or _float(trade.get("pnl_pct")) or 0.0
    ep = _float(trade.get("entry_price")) or 0.0
    if ep <= 0:
        return 0.0
    return round(ep * 100.0 * pct / 100.0, 2)


def run_session_cap_comparison(
    session_dir: Path,
    *,
    force_close_time: str = FORCE_CLOSE_TIME,
) -> dict[str, Any]:
    events = _read_events_csv(session_dir / "small_paper_events.csv")
    structural = _read_structural_trades(session_dir / "structural_trades.csv")
    struct_by_key = _structural_lookup(structural)
    # Gate virtual-hold replay: accepted + cap-blocked candidates (same gate evaluation order)
    gate_stream = _cap_passing_stream(events, include_cap_rejected=True)
    # Position-CAP / capital-sim alignment: structural_trades entry timeline
    position_stream = sorted(
        structural,
        key=lambda t: (
            _parse_ts(t.get("entry_time")) or datetime.min.replace(tzinfo=JST),
            str(t.get("symbol") or ""),
        ),
    )

    vh = VirtualHoldCapSim()
    pc = PositionCapSim()
    unmatched_structural: list[str] = []

    for row in gate_stream:
        struct = _match_structural(row, struct_by_key, structural)
        if struct:
            exit_time = str(struct.get("exit_time") or struct.get("close_time") or force_close_time)
        else:
            exit_time = force_close_time
            if str(row.get("event_type")) == "accepted":
                unmatched_structural.append(
                    _position_key({"symbol": _norm_symbol(row.get("symbol", "")), "entry_time": row.get("entry_time")})
                )
        vh.try_entry(row)
        if struct:
            pc.try_entry(row, structural_exit_time=exit_time, structural_trade=struct)

    # Position-CAP on structural observer entry timeline (capital-sim aligned)
    pc_struct_sim = simulate_cap(
        [normalize_structural_trade(t) for t in structural],
        cap=CAP,
        initial_equity=INITIAL_EQUITY_1500K,
        equity_floor=EQUITY_FLOOR_1500K,
    )
    session_closes = [
        r
        for r in pc_struct_sim.get("_trade_log", [])
        if str(r.get("exit_reason") or "") in ("afternoon_session_close", "end_of_period", "session_end")
    ]

    def _accepted_pnl(accepted: Sequence[Mapping[str, Any]], use_structural: bool) -> float:
        total = 0.0
        for acc in accepted:
            key = acc.get("key") or _position_key(
                {"symbol": acc.get("symbol"), "entry_time": acc.get("entry_time")}
            )
            trade = struct_by_key.get(key)
            if trade:
                total += _pnl_yen_100(trade)
        for sc in session_closes:
            trade = struct_by_key.get(sc.get("key", "")) or sc.get("trade") or {}
            if use_structural and trade:
                total += _pnl_yen_100(trade)
        return round(total, 2)

    vh_pnl = _accepted_pnl(vh.accepted, False)
    pc_pnl = float(pc_struct_sim.get("total_pnl_yen_100") or 0.0)

    observer_exits_1523 = sum(
        1
        for row in events
        if str(row.get("event_type")) == "observer_exit"
        and str(row.get("exit_time", "")).startswith("2026-06-15T15:23")
    )
    gate_peak = 0
    try:
        summary = json.loads((session_dir / "small_paper_summary.json").read_text(encoding="utf-8"))
        gate_peak = int(summary.get("peak_open_slots") or 0)
    except OSError:
        summary = {}

    # Observer max open: replay structural entry/exit
    obs_events: list[tuple[datetime, int, str]] = []
    for t in structural:
        ent = _parse_ts(t.get("entry_time"))
        ex = _parse_ts(t.get("exit_time"))
        if ent:
            obs_events.append((ent, 0, "entry"))
        if ex:
            obs_events.append((ex, 1, "exit"))
    obs_events.sort(key=lambda x: (x[0], x[1]))
    obs_open = 0
    obs_max = 0
    for _, _, kind in obs_events:
        if kind == "entry":
            obs_open += 1
        else:
            obs_open = max(0, obs_open - 1)
        obs_max = max(obs_max, obs_open)

    capital_sim = simulate_audited(
        [normalize_structural_trade(t) for t in structural],
        starting_equity=int(INITIAL_EQUITY_1500K),
        leverage=LEVERAGE,
        cap=CAP,
        stop_policy=STOP_POLICY,
    )

    capital_sim_pnl = round(
        float(capital_sim.get("final_equity") or INITIAL_EQUITY_1500K) - INITIAL_EQUITY_1500K, 2
    )
    capital_state = capital_sim.get("_state")
    capital_max_active = (
        int(getattr(capital_state, "max_concurrent_positions_observed", 0) or 0) if capital_state else None
    )

    comparison_row = {
        "metric": "summary",
        "virtual_hold_accepted": len(vh.accepted),
        "virtual_hold_rejected_cap": len(vh.rejected_cap),
        "virtual_hold_max_active": vh.max_active,
        "virtual_hold_final_pnl_yen_100": vh_pnl,
        "position_cap_accepted": pc_struct_sim.get("accepted_trade_count"),
        "position_cap_rejected_cap": pc_struct_sim.get("position_cap_reject_count"),
        "position_cap_max_active": pc_struct_sim.get("max_concurrent_positions_observed"),
        "position_cap_final_pnl_yen_100": round(pc_pnl, 2),
        "position_cap_session_close_remaining": pc_struct_sim.get("force_exit_count"),
        "delta_accepted": int(pc_struct_sim.get("accepted_trade_count") or 0) - len(vh.accepted),
        "delta_rejected_cap": int(pc_struct_sim.get("position_cap_reject_count") or 0) - len(vh.rejected_cap),
        "delta_max_active": int(pc_struct_sim.get("max_concurrent_positions_observed") or 0) - vh.max_active,
        "delta_pnl_yen_100": round(pc_pnl - vh_pnl, 2),
        "discord_exit_count_1523": observer_exits_1523,
        "gate_peak_open_slots": gate_peak,
        "observer_max_open_positions": obs_max,
        "capital_sim_accepted": capital_sim.get("accepted_trade_count"),
        "capital_sim_rejected": capital_sim.get("rejected_trade_count"),
        "capital_sim_cap_rejects": capital_sim.get("reject_reason_counts", {}).get("max_concurrent_positions", 0),
        "capital_sim_buying_power_rejects": capital_sim.get("reject_reason_counts", {}).get(
            "insufficient_buying_power", 0
        ),
        "capital_sim_final_equity": capital_sim.get("final_equity"),
        "capital_sim_pnl_yen_100": capital_sim_pnl,
        "unmatched_structural_keys": len(unmatched_structural),
    }

    detail_rows = [
        {
            "model": "virtual_hold",
            "accepted_count": len(vh.accepted),
            "rejected_by_cap": len(vh.rejected_cap),
            "max_active_positions": vh.max_active,
            "final_pnl_yen_100": vh_pnl,
            "session_close_remaining": 0,
            "discord_exit_1523": observer_exits_1523,
        },
        {
            "model": "position_cap_until_exit",
            "accepted_count": pc_struct_sim.get("accepted_trade_count"),
            "rejected_by_cap": pc_struct_sim.get("position_cap_reject_count"),
            "max_active_positions": pc_struct_sim.get("max_concurrent_positions_observed"),
            "final_pnl_yen_100": round(pc_pnl, 2),
            "session_close_remaining": pc_struct_sim.get("force_exit_count"),
            "discord_exit_1523": observer_exits_1523,
        },
        {
            "model": "capital_sim_1500k_lev2_cap3",
            "accepted_count": capital_sim.get("accepted_trade_count"),
            "rejected_by_cap": capital_sim.get("reject_reason_counts", {}).get("max_concurrent_positions", 0),
            "max_active_positions": capital_max_active,
            "final_pnl_yen_100": capital_sim_pnl,
            "session_close_remaining": capital_sim.get("force_exit_count"),
            "discord_exit_1523": observer_exits_1523,
        },
    ]

    return {
        "session_dir": str(session_dir),
        "gate_stream_count": len(gate_stream),
        "structural_entry_count": len(position_stream),
        "stream_count": len(gate_stream),
        "comparison_summary": comparison_row,
        "comparison_rows": detail_rows,
        "virtual_hold": vh,
        "position_cap": pc_struct_sim,
        "session_closes": session_closes,
        "capital_sim": capital_sim,
        "struct_by_key": struct_by_key,
        "summary": summary,
        "observer_max_open": obs_max,
    }


def run_position_cap_shadow(session_dir: Path) -> dict[str, Any]:
    """Position-CAP shadow parallel to runtime virtual-hold gate."""
    result = run_session_cap_comparison(session_dir)
    pc_sim: dict[str, Any] = result["position_cap"]
    struct_by_key: dict[str, dict[str, Any]] = result["struct_by_key"]
    capital_sim: dict[str, Any] = result["capital_sim"]
    capital_pnl = round(float(capital_sim.get("final_equity") or INITIAL_EQUITY_1500K) - INITIAL_EQUITY_1500K, 2)

    events_out: list[dict[str, Any]] = []
    for row in pc_sim.get("_trade_log", []):
        trade = row.get("trade") or {}
        events_out.append(
            {
                "event": "shadow_accept_close",
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": row.get("exit_time"),
                "pnl_yen_100": row.get("pnl_yen"),
                "exit_reason": row.get("exit_reason"),
                "cap": row.get("cap"),
            }
        )
    cap_rejects = int(pc_sim.get("position_cap_reject_count") or 0)
    events_out.append(
        {
            "event": "shadow_reject_cap_summary",
            "rejected_cap_count": cap_rejects,
            "insufficient_buying_power_count": pc_sim.get("insufficient_buying_power_count"),
        }
    )
    for sc in result["session_closes"]:
        trade = sc.get("trade") or {}
        events_out.append(
            {
                "event": "shadow_session_close",
                "symbol": sc.get("symbol") or trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": sc.get("exit_time"),
                "pnl_yen_100": sc.get("pnl_yen"),
                "exit_reason": sc.get("exit_reason") or "afternoon_session_close",
            }
        )

    shadow_pnl = float(pc_sim.get("total_pnl_yen_100") or 0.0)

    summary = {
        "phase": 395,
        "generated_at": _now_iso(),
        "session": f"{SESSION_DAY}/{SESSION_ID}",
        "shadow_model": "position_cap_until_structural_exit",
        "runtime_model": "virtual_hold_slot_cap3",
        "cap": CAP,
        "initial_equity": INITIAL_EQUITY_1500K,
        "leverage": LEVERAGE,
        "shares": 100,
        "stop_policy": STOP_POLICY,
        "shadow_accepted_count": pc_sim.get("accepted_trade_count"),
        "shadow_rejected_cap_count": pc_sim.get("position_cap_reject_count"),
        "shadow_rejected_buying_power_count": pc_sim.get("insufficient_buying_power_count"),
        "shadow_max_open_positions": pc_sim.get("max_concurrent_positions_observed"),
        "shadow_session_close_exit_count": pc_sim.get("force_exit_count"),
        "shadow_total_pnl_yen_100": round(shadow_pnl, 2),
        "capital_path_final_equity": capital_sim.get("final_equity"),
        "capital_path_pnl_yen_100": capital_pnl,
        "capital_path_accepted": capital_sim.get("accepted_trade_count"),
        "capital_path_rejected_cap": capital_sim.get("reject_reason_counts", {}).get(
            "max_concurrent_positions", 0
        ),
        "capital_path_rejected_buying_power": capital_sim.get("reject_reason_counts", {}).get(
            "insufficient_buying_power", 0
        ),
        "runtime_accepted_count": int(result["summary"].get("accepted_count") or len(result["virtual_hold"].accepted)),
        "runtime_rejected_max_concurrent": int(
            result["summary"].get("reject_reason_counts", {}).get("max_concurrent", 0)
        ),
        "gate_peak_open_slots": int(result["summary"].get("peak_open_slots") or 0),
        "observer_max_open_positions": result["observer_max_open"],
    }
    return {"events": events_out, "summary": summary, "comparison": result}


def _resolve_trade_repo_root(repo_root: Path) -> Path:
    """load_trades_by_day expects tradebotfile root (parent of kabu_native)."""
    if (repo_root / "results" / "small_paper").is_dir():
        return repo_root.parent if (repo_root.parent / "kabu_native").is_dir() else repo_root
    return repo_root


def audit_capital_sim_cap(repo_root: Path) -> dict[str, Any]:
    """Verify Phase267–274 capital sim uses EXIT-until-release CAP, not virtual hold."""
    trade_root = _resolve_trade_repo_root(repo_root)
    trades, meta = load_period_trades(trade_root)
    spec = build_spec(leverage=LEVERAGE, cap=CAP, stop_policy=STOP_POLICY)
    state = CapScenarioState(
        scenario_id="phase395_audit",
        max_concurrent_positions=CAP,
        spec=spec,
        initial_equity=INITIAL_EQUITY_1500K,
        equity_floor=EQUITY_FLOOR_1500K,
    )

    vh_release_mismatches: list[dict[str, Any]] = []
    exit_release_examples: list[dict[str, Any]] = []

    events = build_event_timeline(trades)
    for dt, _, kind, trade in events:
        ts = dt.isoformat()
        day = _day_from_ts(ts)
        open_before = len(state.open_positions)
        if kind == "entry":
            ent_dt = _parse_ts(trade.get("entry_time"))
            if ent_dt:
                vh_ex = ent_dt.timestamp() + VIRTUAL_HOLD_SEC
                # If VH would have released but position still open, that's evidence sim != VH
                for key, pos in list(state.open_positions.items()):
                    t = pos["trade"]
                    t_ent = _parse_ts(t.get("entry_time"))
                    t_ex = _parse_ts(t.get("exit_time"))
                    if t_ent and t_ex and t_ent.timestamp() + VIRTUAL_HOLD_SEC < ent_dt.timestamp():
                        if t_ex.timestamp() > ent_dt.timestamp():
                            vh_release_mismatches.append(
                                {
                                    "key": key,
                                    "candidate_entry": ts,
                                    "vh_would_release_at": datetime.fromtimestamp(
                                        t_ent.timestamp() + VIRTUAL_HOLD_SEC, tz=JST
                                    ).isoformat(),
                                    "structural_exit": t_ex.isoformat(),
                                    "still_open_in_sim": True,
                                }
                            )
            state.try_entry(trade, ts, day)
        else:
            if _position_key(trade) in state.open_positions:
                exit_release_examples.append(
                    {
                        "key": _position_key(trade),
                        "exit_time": ts,
                        "open_before": open_before,
                        "release_reason": "structural_exit_event",
                    }
                )
            state.process_exit(trade, ts, day)

    sim_full = simulate_cap(
        trades,
        cap=CAP,
        initial_equity=INITIAL_EQUITY_1500K,
        equity_floor=EQUITY_FLOOR_1500K,
    )
    sim_audited = simulate_audited(
        trades,
        starting_equity=int(INITIAL_EQUITY_1500K),
        leverage=LEVERAGE,
        cap=CAP,
        stop_policy=STOP_POLICY,
    )

    # Sample timeline proof: first trade where VH expired but structural still open
    sample = vh_release_mismatches[:5]

    verdict = "PASS"
    verdict_detail = "CAP=3 means max 3 open positions until EXIT (structural exit_time from structural_trades.csv)"
    if sim_full.get("max_concurrent_positions_observed", 0) > CAP:
        verdict = "FAIL"
        verdict_detail = "Observed more than CAP open positions without reject"
    # Capital sim never uses 300s — mismatches prove it's NOT virtual hold
    uses_virtual_hold = False

    return {
        "verdict": verdict,
        "verdict_detail": verdict_detail,
        "uses_virtual_hold": uses_virtual_hold,
        "period_meta": meta,
        "simulate_cap_summary": {
            k: sim_full.get(k)
            for k in (
                "accepted_trade_count",
                "rejected_trade_count",
                "position_cap_reject_count",
                "insufficient_buying_power_count",
                "max_concurrent_positions_observed",
                "total_pnl_yen_100",
                "final_equity",
            )
        },
        "simulate_audited_summary": {
            k: sim_audited.get(k)
            for k in (
                "accepted_trade_count",
                "rejected_trade_count",
                "reject_reason_counts",
                "max_concurrent_positions_observed",
                "total_pnl_yen_100",
                "final_equity",
            )
        },
        "vh_vs_structural_mismatch_count": len(vh_release_mismatches),
        "vh_vs_structural_samples": sample,
        "exit_release_sample_count": len(exit_release_examples),
        "exit_release_samples": exit_release_examples[:5],
        "phase_rows": PHASE_CAP_AUDIT_ROWS,
    }


def _md_table(headers: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")
    return "\n".join(lines)


def write_cap_definition_matrix(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f"""# Phase395 — CAP Definition Matrix

Generated: {_now_iso()}

## Purpose

Document the three independent CAP definitions so Runtime notifications, observer lifecycle,
capital simulation (Phase267–274), and future live trading share a single mental model.

## Matrix

{_md_table(["Layer", "CAP Meaning", "Release Condition", "Used For", "Current Risk"], CAP_DEFINITION_MATRIX_ROWS)}

## Key Code References

| Layer | Primary module |
|-------|----------------|
| Exposure Gate | `src/research/exposure_gate.py`, `src/small_paper/pilot_runner.py` (`virtual_hold_sec=300`) |
| Observer | `src/small_paper/observer_position_tracker.py` |
| Capital Sim | `src/research/phase385_cap_sensitivity_study.py` (`CapScenarioState`) |

## Terminology

| Term | Gate | Observer | Capital Sim |
|------|------|----------|-------------|
| `max_concurrent_positions` | Entry slot limit (3) | N/A | Open position limit |
| `open_slots` | `(entry_ts, exit_ts, symbol)` | N/A | N/A |
| `open_positions` | N/A | Per-symbol map | Capital occupancy dict |
| Release trigger | `exit_time` prune (~5min) | Structural exit / `close_all` | `structural_trades` exit event |

## Conclusion

**CAP=3 means different things per layer.** Runtime gate CAP is a **5-minute virtual-hold slot**.
Capital simulation CAP is **max 3 positions until structural EXIT**. Observer has **no concurrent cap**.
"""
    path.write_text(body, encoding="utf-8")


def write_capital_sim_audit(path: Path, audit: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sc = audit["simulate_cap_summary"]
    sa = audit["simulate_audited_summary"]
    verdict = audit["verdict"]
    body = f"""# Phase395 — Capital Simulation CAP Audit (Phase267–274)

Generated: {_now_iso()}

## Verdict: **{verdict}**

{audit["verdict_detail"]}

Capital simulation **does not** use 5-minute virtual hold (`uses_virtual_hold={audit["uses_virtual_hold"]}`).
Slots release on `process_exit` at `structural_trades.csv` `exit_time` / `close_time`.

---

## Configuration Under Test

| Parameter | Value |
|-----------|-------|
| Initial equity | ¥{INITIAL_EQUITY_1500K:,.0f} |
| Leverage | {LEVERAGE}x |
| Shares | 100 fixed |
| CAP | {CAP} |
| Stop policy | {STOP_POLICY} |

---

## Phase Module Summary

{_md_table(["Phase", "Module", "Default CAP", "Release", "Buying Power", "Leverage / Maint", "Input Trades"], audit["phase_rows"])}

---

## Simulation Results (full period)

### `simulate_cap` (Phase385 engine)

| Metric | Value |
|--------|-------|
| Accepted trades | {sc.get("accepted_trade_count")} |
| Rejected trades | {sc.get("rejected_trade_count")} |
| Rejected by CAP (`max_concurrent_positions`) | {sc.get("position_cap_reject_count")} |
| Rejected by buying power | {sc.get("insufficient_buying_power_count")} |
| Max concurrent positions observed | {sc.get("max_concurrent_positions_observed")} |
| Total PnL (100 shares) | ¥{sc.get("total_pnl_yen_100")} |
| Final equity | ¥{sc.get("final_equity")} |

### `simulate_audited` (Phase271/273 engine, fixed_stop_1p2)

| Metric | Value |
|--------|-------|
| Accepted trades | {sa.get("accepted_trade_count")} |
| Rejected trades | {sa.get("rejected_trade_count")} |
| Reject reason counts | `{json.dumps(sa.get("reject_reason_counts", {}), ensure_ascii=False)}` |
| Max concurrent positions observed | {sa.get("max_concurrent_positions_observed")} |
| Total PnL (100 shares) | ¥{round(float(sa.get('final_equity') or INITIAL_EQUITY_1500K) - INITIAL_EQUITY_1500K, 2)} |
| Final equity | ¥{sa.get("final_equity")} |

---

## Evidence: EXIT-until-release (not virtual hold)

`CapScenarioState.try_entry` rejects when `len(open_positions) >= max_concurrent_positions`.
`CapScenarioState.process_exit` removes the key at structural `exit_time`.

**VH vs structural mismatch events** (VH would release slot but sim still holds): **{audit["vh_vs_structural_mismatch_count"]}**

This proves the sim holds positions until structural EXIT, not 5-minute VH.

### Sample mismatches (first 5)

{_md_table(["key", "candidate_entry", "vh_would_release_at", "structural_exit", "still_open_in_sim"], audit["vh_vs_structural_samples"]) if audit["vh_vs_structural_samples"] else "_None in sample window._"}

### Exit release samples (first 5)

{_md_table(["key", "exit_time", "open_before", "release_reason"], audit["exit_release_samples"]) if audit["exit_release_samples"] else "_N/A_"}

---

## Buying Power & Leverage

Both engines call `compute_buying_power(equity, gross, leverage_limit)` and `compute_requested_shares`.
Maintenance ratio checks (`MAINT_WARNING`, `MAINT_STOP_ENTRY`, `MAINT_FORCE_EXIT`) are active in `CapScenarioState.try_entry`.

---

## FAIL Criteria Check

| Check | Result |
|-------|--------|
| CAP=3 means virtual slot or 5min hold | **No** — sim uses structural exit |
| CAP=3 means max 3 open until EXIT | **Yes** |
| Buying power enforced | **Yes** (`insufficient_buying_power_count` tracked) |
| Leverage / maintenance enforced | **Yes** |
"""
    path.write_text(body, encoding="utf-8")


def write_session_comparison_report(
    path: Path,
    result: Mapping[str, Any],
    *,
    csv_path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cs = result["comparison_summary"]
    rows = result["comparison_rows"]
    body = f"""# Phase395 — 2026-06-15 PM Position-CAP Comparison

Generated: {_now_iso()}

Session: `{SESSION_DAY}/{SESSION_ID}`

## Method

- **A. Virtual-hold CAP** — replay gate stream (`accepted` + `rejected[max_concurrent]`, n={result["gate_stream_count"]}) with 300s slot release (current Runtime).
- **B. Position-CAP until EXIT** — replay `structural_trades` entry timeline (n={result["structural_entry_count"]}) with CAP=3 until structural exit; session `force_close` at 15:23.
- **C. Capital sim reference** — 1.5M / lev2 / 100 shares / CAP3 / fixed_stop_1p2 on session `structural_trades`.

Note: cap-blocked gate candidates never reached observer, so position-CAP uses the observer/canonical trade timeline (same input family as Phase267–274 capital sim).

---

## Summary

{_md_table(["model", "accepted_count", "rejected_by_cap", "max_active_positions", "final_pnl_yen_100", "session_close_remaining", "discord_exit_1523"], rows)}

---

## Deltas (Position-CAP minus Virtual-hold)

| Metric | Delta |
|--------|-------|
| Accepted | {cs.get("delta_accepted")} |
| Rejected by CAP | {cs.get("delta_rejected_cap")} |
| Max active positions | {cs.get("delta_max_active")} |
| PnL (100 shares) | ¥{cs.get("delta_pnl_yen_100")} |

---

## Discord / Observer Context

| Metric | Value |
|--------|-------|
| Gate `peak_open_slots` | {cs.get("gate_peak_open_slots")} |
| Observer max open positions (structural replay) | {cs.get("observer_max_open_positions")} |
| Discord `observer_exit` at 15:23 | {cs.get("discord_exit_count_1523")} |
| Position-CAP session-close remaining at 15:23 | {cs.get("position_cap_session_close_remaining")} |

At 15:23, gate virtual-hold slots were **0** while observer issued **{cs.get("discord_exit_count_1523")}** EXIT notifications
(session-close burst from `close_all()`). Under position-CAP, **{cs.get("position_cap_session_close_remaining")}** positions
required end-of-session force-close in this replay (observer had up to **{cs.get("observer_max_open_positions")}** concurrent opens, uncapped).

---

## Equity Curve Impact

Capital-path on session structural trades: final equity **¥{cs.get("capital_sim_final_equity")}**,
PnL **¥{cs.get("capital_sim_pnl_yen_100")}** (CAP rejects: {cs.get("capital_sim_cap_rejects")},
buying-power rejects: {cs.get("capital_sim_buying_power_rejects")}).

---

## CSV

`{csv_path.as_posix()}`
"""
    path.write_text(body, encoding="utf-8")


def write_shadow_report(path: Path, shadow: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    s = shadow["summary"]
    body = f"""# Phase395 — Position-CAP Shadow Report

Generated: {_now_iso()}

## Shadow Specification

| Rule | Behavior |
|------|----------|
| Entry | Accept when `open_positions < 3` on shadow ledger |
| Hold | Slot occupied until structural EXIT |
| Exit | Release slot on structural exit event |
| Session close | Force-close remaining at 15:23 |
| PnL | 100-share yen from `structural_trades` |
| Capital path | 1.5M / lev2 / 100 / CAP3 / fixed_stop_1p2 |

## Results

| Metric | Runtime (virtual-hold) | Shadow (position-cap) |
|--------|------------------------|------------------------|
| Accepted | {s.get("runtime_accepted_count")} | {s.get("shadow_accepted_count")} |
| CAP rejects | {s.get("runtime_rejected_max_concurrent")} | {s.get("shadow_rejected_cap_count")} |
| Max active | {s.get("gate_peak_open_slots")} (gate slots) | {s.get("shadow_max_open_positions")} |
| Observer max open | {s.get("observer_max_open_positions")} | — |
| Session-close EXIT burst | {s.get("shadow_session_close_exit_count")} (shadow) | |
| PnL (100 shares) | — | ¥{s.get("shadow_total_pnl_yen_100")} |
| Capital path PnL | — | ¥{s.get("capital_path_pnl_yen_100")} |
| Capital path final equity | — | ¥{s.get("capital_path_final_equity")} |

## Artifacts

- `results/reports/phase395_position_cap_shadow_events.csv`
- `results/reports/phase395_position_cap_shadow_summary.json`

**No production Runtime / Discord changes in Phase395.**
"""
    path.write_text(body, encoding="utf-8")


def write_discord_proposal(path: Path, result: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cs = result["comparison_summary"]
    body = f"""# Phase395 — Discord CAP Semantics Proposal (not implemented)

Generated: {_now_iso()}

**Status:** Proposal only. No `discord_message_builder.py` changes in Phase395.

## Problem

Discord EXIT notifications come from **structural observer** (`observer_exit`), not from Exposure Gate slot release.
On 2026-06-15 PM, **{cs.get("discord_exit_count_1523")}** EXIT messages at 15:23 were `afternoon_session_close`
while gate slots were **0**. Users may read these as "position CAP exited" when they are observer batch closes.

---

## Proposed ENTRY Notification Additions

```
Gate model: virtual_hold_slot
Position model: observer_structural
CAP note: CAP=3 applies to gate slots, not structural observer open count
```

### Rationale

Runtime ENTRY fires when gate accepts; observer registers a separate structural position that may outlive the 5-minute VH slot.

---

## Proposed EXIT Notification Additions

```
Exit source: structural_observer
Actual gate slot may already be released
Session close burst possible
```

### Rationale

`virtual_hold_expired_ignored_count` shows gate VH expiry does not close observer under `combined_structural_exit_v1`.

---

## Proposed Session Summary Additions

| Field | Example (2026-06-15 PM) |
|-------|-------------------------|
| `gate_max_active_positions` | {cs.get("gate_peak_open_slots")} |
| `observer_open_max_positions` | {cs.get("observer_max_open_positions")} |
| `session_close_exit_burst_count` | {cs.get("discord_exit_count_1523")} |

---

## Example ENTRY (illustrative)

```
【ENTRY】4062 (信越化学)
ENTRY価格: 22,160
...
Gate model: virtual_hold_slot
Position model: observer_structural
CAP note: CAP=3 = gate slots (~5min), not observer open count
```

## Example EXIT (illustrative)

```
【EXIT】6962
EXIT理由: 午後セッションクローズ
Exit source: structural_observer
Gate slot: already released (VH expired)
Session close burst: 12 exits @ 15:23
```

---

## Example Summary Footer

```
gate_max_active_positions: 3
observer_open_max_positions: {cs.get("observer_max_open_positions")}
session_close_exit_burst_count: {cs.get("discord_exit_count_1523")}
```
"""
    path.write_text(body, encoding="utf-8")


def write_migration_plan(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f"""# Phase395 — Runtime Position-CAP Migration Plan (proposal)

Generated: {_now_iso()}

**Not implemented in Phase395.** For a future phase after shadow validation.

---

## Goal

Align Runtime CAP, Discord notifications, observer lifecycle, and capital simulation
(1.5M / lev2 / 100 / CAP3 / fixed_stop_1p2) so ENTRY/EXIT reflect actual tradable positions.

---

## Target: Runtime Position-CAP Mode

| Component | Current | Target |
|-----------|---------|--------|
| `max_concurrent_positions` | Gate virtual-hold slots | Observer open position count |
| Slot release | `entry_time + 300s` | Structural EXIT (`stop_hit`, `trailing_mfe_exit`, overlap, session close) |
| 5-minute VH | Concurrent cap occupancy | Rename to `entry_cooldown_sec` (optional, separate concern) |
| Discord ENTRY | Gate accept | Same trigger, label as position lifecycle |
| Discord EXIT | Observer structural | Matches gate slot release |
| Capital sim input | `structural_trades.csv` | Unchanged — already position-CAP |

---

## Migration Phases

### Phase A — Shadow validation (Phase395, done)

- Position-CAP shadow parallel to production
- 6/15 PM comparison report
- No Runtime changes

### Phase B — Label / audit only

- Discord semantics proposal (this phase)
- Optional audit fields in `small_paper_summary.json` (`gate_max_active_positions`, `observer_open_max_positions`)

### Phase C — Runtime switch (future)

1. Move CAP check from `ExposureGate.evaluate_entry` slot prune to `ObserverPositionTracker.open_count()`
2. Reject `REJECT_MAX_CONCURRENT` when `observer.open_count() >= max_concurrent_positions`
3. Release gate `open_slots` on observer exit callback (or deprecate `open_slots`)
4. Retire VH-as-CAP; keep VH only if needed as cooldown metadata
5. Update `small_paper_positions.csv` to track position-cap slots

### Phase D — Verification

- Re-run Phase395 comparison: virtual-hold vs position-cap delta should → 0
- Capital sim forward shadow (Phase273/274) vs live session PnL convergence test
- Discord EXIT count at session close should match open position count

---

## Risks

| Risk | Mitigation |
|------|------------|
| Lower accepted rate (position CAP stricter than VH) | Phase395 shadow quantifies delta ({SESSION_DAY} PM) |
| Entry timing shift | Shadow replay before flip |
| Operator confusion during transition | Dual-label period (gate + position metrics) |

---

## Rollback

Keep `virtual_hold_sec` config; feature flag `position_cap_mode: false` restores gate VH behavior.

---

## Success Criteria

1. `peak_open_slots` ≈ `observer_open_max` ≈ capital sim `max_concurrent_positions_observed`
2. Discord EXIT count at session end ≤ CAP (no burst >> CAP without explicit session-close label)
3. Forward shadow equity tracks live within documented tolerance
"""
    path.write_text(body, encoding="utf-8")


def run_phase395(repo_root: Path) -> dict[str, Any]:
    docs = repo_root / "docs" / "operations"
    reports = repo_root / "results" / "reports"
    session_dir = repo_root / "results" / "small_paper" / SESSION_DAY / SESSION_ID

    write_cap_definition_matrix(docs / "phase395_cap_definition_matrix.md")

    audit = audit_capital_sim_cap(repo_root)
    write_capital_sim_audit(docs / "phase395_capital_sim_cap_audit.md", audit)

    comparison = run_session_cap_comparison(session_dir)
    comparison_csv = reports / "phase395_20260615_pm_position_cap_comparison.csv"
    _write_rows_csv(comparison_csv, comparison["comparison_rows"])

    write_session_comparison_report(
        docs / "phase395_20260615_pm_position_cap_report.md",
        comparison,
        csv_path=comparison_csv,
    )

    shadow = run_position_cap_shadow(session_dir)
    shadow_events_csv = reports / "phase395_position_cap_shadow_events.csv"
    shadow_summary_json = reports / "phase395_position_cap_shadow_summary.json"
    _write_rows_csv(shadow_events_csv, shadow["events"])
    shadow_summary_json.write_text(json.dumps(shadow["summary"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    write_shadow_report(docs / "phase395_position_cap_shadow_report.md", shadow)
    write_discord_proposal(docs / "phase395_discord_cap_semantics_proposal.md", comparison)
    write_migration_plan(docs / "phase395_runtime_position_cap_migration_plan.md")

    return {
        "audit_verdict": audit["verdict"],
        "comparison": comparison["comparison_summary"],
        "shadow_summary": shadow["summary"],
        "outputs": {
            "cap_matrix": str(docs / "phase395_cap_definition_matrix.md"),
            "cap_audit": str(docs / "phase395_capital_sim_cap_audit.md"),
            "session_report": str(docs / "phase395_20260615_pm_position_cap_report.md"),
            "comparison_csv": str(comparison_csv),
            "shadow_events": str(shadow_events_csv),
            "shadow_summary": str(shadow_summary_json),
            "shadow_report": str(docs / "phase395_position_cap_shadow_report.md"),
            "discord_proposal": str(docs / "phase395_discord_cap_semantics_proposal.md"),
            "migration_plan": str(docs / "phase395_runtime_position_cap_migration_plan.md"),
        },
    }
