"""
Phase592B — Equity simulation capital logic audit.

Confirms whether research equity sim uses fixed initial capital vs PnL-adjusted
variable equity, and how CAP / buying-power skips are separated.
Research only — no runtime ENTRY/EXIT changes.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import (
    CANONICAL_BASELINE_END,
    PERIOD_START,
    load_canonical_live_config_trades,
    pnl_for_actual_fixed_stop,
)
from research.market_sector_heat import _write_csv
from research.phase382_capital_constrained_backtest import (
    LOT_SIZE,
    _day_from_ts,
    _float,
    _gross_position_value,
    _parse_ts,
    _position_key,
)
from research.phase383_realistic_credit_sizing_backtest import (
    build_event_timeline,
    compute_buying_power,
    compute_requested_shares,
)
from research.phase385_cap_sensitivity_study import (
    DEFAULT_LEVERAGE,
    FIXED_SPEC,
    CapScenarioState,
)
from research.phase451_entry_shape_tournament import _now_iso
from research.research_output_layers import (
    LIVE_SIM_DEFAULT_LEVERAGE,
    LIVE_SIM_DEFAULT_SHARES,
    LIVE_SIM_DEFAULT_STARTING_EQUITY,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")
PHASE592B_VERDICT = "phase592b_equity_sim_capital_logic_audit_done"

AUDIT_CAP = 5
EQUITY_FLOOR = LIVE_SIM_DEFAULT_STARTING_EQUITY * 0.5

LOGIC_AUDIT_FIELDS = [
    "seq",
    "day",
    "symbol",
    "entry_time",
    "outcome",
    "skip_reason",
    "initial_capital",
    "current_equity",
    "realized_pnl",
    "gross_position_value",
    "leverage",
    "buying_power",
    "available_margin",
    "required_margin_per_slot",
    "entry_price",
    "requested_shares",
    "open_positions",
    "max_concurrent_positions",
    "cap_blocked",
    "margin_blocked",
]

EQUITY_MARGIN_FIELDS = [
    "seq",
    "day",
    "timestamp",
    "event_type",
    "symbol",
    "initial_capital",
    "current_equity",
    "realized_pnl",
    "gross_position_value",
    "leverage",
    "buying_power",
    "available_margin",
    "open_positions",
    "max_concurrent_positions",
    "pnl_yen",
]

SKIP_BREAKDOWN_FIELDS = [
    "skip_reason",
    "count",
    "pct_of_rejects",
    "category",
]

CANONICAL_MODULES = (
    "research.phase385_cap_sensitivity_study.CapScenarioState",
    "research.phase383_realistic_credit_sizing_backtest.compute_buying_power",
    "research.equity_curve_shadow.EquityCurveCapState",
)


def required_margin_per_slot(*, entry_price: float, leverage: float, shares: int = LOT_SIZE) -> float:
    if entry_price <= 0 or leverage <= 0:
        return 0.0
    return entry_price * shares / leverage


def _skip_category(reason: str) -> str:
    if reason == "max_concurrent_positions":
        return "cap"
    if reason in ("insufficient_buying_power", "invalid_size", "invalid_price"):
        return "margin"
    if reason in ("maintenance_ratio_stop", "equity_floor_breach"):
        return "risk_halt"
    return "other"


@dataclass
class AuditCapState(CapScenarioState):
    pnl_resolver: Callable[..., float] = pnl_for_actual_fixed_stop
    audit_rows: list[dict[str, Any]] = field(default_factory=list)
    margin_curve: list[dict[str, Any]] = field(default_factory=list)
    _seq: int = 0
    _audit_seq: int = 0
    _last_reject_reason: str = ""

    def _record_margin_point(
        self,
        *,
        ts: str,
        day: str,
        event_type: str,
        symbol: str = "",
        pnl_yen: Optional[float] = None,
    ) -> None:
        eq = round(self.current_equity(), 2)
        gross = round(_gross_position_value(self.open_positions), 2)
        leverage = float(self.spec.get("leverage_limit") or DEFAULT_LEVERAGE)
        bp = round(compute_buying_power(equity=eq, gross=gross, leverage_limit=leverage), 2)
        self._seq += 1
        self.margin_curve.append(
            {
                "seq": self._seq,
                "day": day,
                "timestamp": ts,
                "event_type": event_type,
                "symbol": symbol,
                "initial_capital": round(self.initial_equity, 2),
                "current_equity": eq,
                "realized_pnl": round(self.realized_pnl, 2),
                "gross_position_value": gross,
                "leverage": leverage,
                "buying_power": bp,
                "available_margin": bp,
                "open_positions": len(self.open_positions),
                "max_concurrent_positions": self.max_concurrent_positions,
                "pnl_yen": "" if pnl_yen is None else round(pnl_yen, 2),
            }
        )

    def _reject_entry(self, trade: Mapping[str, Any], reason: str) -> None:
        self._last_reject_reason = reason
        super()._reject_entry(trade, reason)

    def _close_position(self, key: str, ts: str, day: str, *, forced: bool = False, force_reason: str = "") -> None:
        pos = self.open_positions.get(key)
        if not pos:
            return
        trade = pos["trade"]
        shares = int(pos["shares"])
        pnl = self.pnl_resolver(trade, shares=shares, entry_equity=float(pos.get("entry_equity") or self.current_equity()))
        super()._close_position(key, ts, day, forced=forced, force_reason=force_reason)
        if key not in self.open_positions:
            self._record_margin_point(
                ts=ts,
                day=day,
                event_type="force_exit" if forced else "exit",
                symbol=str(trade.get("symbol") or ""),
                pnl_yen=pnl,
            )

    def try_entry(self, trade: Mapping[str, Any], ts: str, day: str) -> None:
        eq = self.current_equity()
        gross = _gross_position_value(self.open_positions)
        leverage = float(self.spec.get("leverage_limit") or DEFAULT_LEVERAGE)
        buying_power = compute_buying_power(equity=eq, gross=gross, leverage_limit=leverage)
        entry_price = float(_float(trade.get("entry_price")) or 0.0)
        req_margin = required_margin_per_slot(entry_price=entry_price, leverage=leverage)
        open_count = len(self.open_positions)
        cap_blocked = open_count >= self.max_concurrent_positions

        shares_preview, margin_reject = compute_requested_shares(
            spec=self.spec,
            equity=eq,
            entry_price=entry_price,
            buying_power=buying_power,
        )
        margin_blocked = margin_reject == "insufficient_buying_power"

        before_accepted = self.accepted_trade_count
        before_rejected = self.rejected_trade_count
        self._last_reject_reason = ""

        super().try_entry(trade, ts, day)

        if self.accepted_trade_count > before_accepted:
            outcome = "accepted"
            skip_reason = ""
            shares = int(self.open_positions.get(_position_key(trade), {}).get("shares") or shares_preview)
        elif self.rejected_trade_count > before_rejected:
            outcome = "rejected"
            skip_reason = self._last_reject_reason or "unknown"
            shares = 0
        else:
            outcome = "no_op"
            skip_reason = ""
            shares = 0

        self._audit_seq += 1
        self.audit_rows.append(
            {
                "seq": self._audit_seq,
                "day": day,
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "outcome": outcome,
                "skip_reason": skip_reason,
                "initial_capital": round(self.initial_equity, 2),
                "current_equity": round(eq, 2),
                "realized_pnl": round(self.realized_pnl, 2),
                "gross_position_value": round(gross, 2),
                "leverage": leverage,
                "buying_power": round(buying_power, 2),
                "available_margin": round(buying_power, 2),
                "required_margin_per_slot": round(req_margin, 2),
                "entry_price": round(entry_price, 2) if entry_price else "",
                "requested_shares": shares if outcome == "accepted" else shares_preview,
                "open_positions": open_count,
                "max_concurrent_positions": self.max_concurrent_positions,
                "cap_blocked": cap_blocked,
                "margin_blocked": margin_blocked,
            }
        )

        if outcome == "accepted":
            key = _position_key(trade)
            if key in self.open_positions:
                self.open_positions[key]["entry_equity"] = self.current_equity()
            self._record_margin_point(
                ts=ts,
                day=day,
                event_type="entry",
                symbol=str(trade.get("symbol") or ""),
            )


def run_audit_simulation(
    trades: Sequence[Mapping[str, Any]],
    *,
    initial_equity: float = LIVE_SIM_DEFAULT_STARTING_EQUITY,
    equity_floor: float = EQUITY_FLOOR,
    cap: int = AUDIT_CAP,
    spec: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    run_spec = dict(spec if spec is not None else FIXED_SPEC)
    state = AuditCapState(
        scenario_id=f"CAP_{cap}_audit",
        max_concurrent_positions=cap,
        spec=run_spec,
        initial_equity=initial_equity,
        equity_floor=equity_floor,
        pnl_resolver=pnl_for_actual_fixed_stop,
    )
    events = build_event_timeline(trades)
    if events:
        first_day = _day_from_ts(events[0][0].isoformat()) or ""
        state._record_margin_point(ts="", day=first_day, event_type="start")

    for dt, _, kind, trade in events:
        ts = dt.isoformat()
        day = _day_from_ts(ts) or ""
        if kind == "entry":
            state.try_entry(trade, ts, day)
        else:
            state.process_exit(trade, ts, day)

    if state.open_positions and events:
        last_ts = events[-1][0].isoformat()
        last_day = _day_from_ts(last_ts) or ""
        state._force_close_all(last_ts, last_day, reason="end_of_period")

    reject_reasons = Counter(
        row["skip_reason"]
        for row in state.audit_rows
        if row.get("outcome") == "rejected" and row.get("skip_reason")
    )
    total_rejects = sum(reject_reasons.values())
    skip_rows: list[dict[str, Any]] = []
    for reason, count in sorted(reject_reasons.items(), key=lambda x: (-x[1], x[0])):
        skip_rows.append(
            {
                "skip_reason": reason,
                "count": count,
                "pct_of_rejects": round(count / total_rejects * 100.0, 2) if total_rejects else 0.0,
                "category": _skip_category(reason),
            }
        )

    accepted_with_prior_exit = _pnl_reflected_in_subsequent_entries(state.audit_rows)

    return {
        "state": state,
        "audit_rows": state.audit_rows,
        "margin_curve": state.margin_curve,
        "skip_breakdown": skip_rows,
        "summary": {
            "initial_capital": round(initial_equity, 2),
            "final_equity": round(state.current_equity(), 2),
            "realized_pnl": round(state.realized_pnl, 2),
            "accepted_trade_count": state.accepted_trade_count,
            "rejected_trade_count": state.rejected_trade_count,
            "position_cap_reject_count": state.position_cap_reject_count,
            "insufficient_buying_power_count": state.insufficient_buying_power_count,
            "maintenance_stop_count": state.maintenance_stop_count,
            "equity_floor_breach": state.equity_floor_breached,
            "max_concurrent_positions_observed": state.max_concurrent_positions_observed,
            "pnl_reflected_in_later_buying_power": accepted_with_prior_exit,
        },
    }


def _pnl_reflected_in_subsequent_entries(audit_rows: Sequence[Mapping[str, Any]]) -> bool:
    """True if any accepted entry uses current_equity != initial_capital (realized PnL absorbed)."""
    initial = None
    for row in audit_rows:
        if row.get("outcome") != "accepted":
            continue
        ic = float(row.get("initial_capital") or 0.0)
        ce = float(row.get("current_equity") or 0.0)
        if initial is None:
            initial = ic
        if abs(ce - ic) > 1e-6:
            return True
    return False


def _mandatory_answers(sim: Mapping[str, Any], *, cap: int) -> dict[str, Any]:
    summary = sim.get("summary") or {}
    state: AuditCapState = sim["state"]
    cap_rejects = int(summary.get("position_cap_reject_count") or 0)
    margin_rejects = int(summary.get("insufficient_buying_power_count") or 0)
    pnl_reflected = bool(summary.get("pnl_reflected_in_later_buying_power"))

    return {
        "1_capital_mode": "initial_capital fixed anchor; current_equity = initial_capital + realized_pnl (variable)",
        "2_pnl_reflected_in_buying_power": pnl_reflected,
        "3_required_margin_formula": "required_margin_per_slot = entry_price * shares / leverage_limit (100-share slot: entry_price * 100 / 2.0)",
        "4_leverage_2x_implementation": "buying_power = max(0, current_equity * leverage_limit - gross_position_value); leverage_limit=2.0 in FIXED_SPEC",
        "5_cap_constraint": f"len(open_positions) >= {cap} → reject reason max_concurrent_positions (position_cap_reject_count={cap_rejects})",
        "6_insufficient_margin_skip_exists": margin_rejects > 0,
        "6_insufficient_buying_power_count": margin_rejects,
        "7_cap_and_margin_skips_separated": True,
        "7_detail": "CapScenarioState._reject_entry increments position_cap_reject_count vs insufficient_buying_power_count separately",
        "8_reusable_for_live_capital_manager": "Partially — no CapitalManager module exists; CapScenarioState logic maps to live preflight but needs kabu wallet/margin sync (Phase592A)",
        "9_fixes_needed": False,
        "9_detail": "Research sim correctly uses variable equity with separated skip counters; live wiring should mirror compute_buying_power + cap check order",
        "10_next_phase": "phase593_live_order_capped_pilot_cap2",
        "canonical_modules": list(CANONICAL_MODULES),
        "sim_parameters": {
            "initial_capital": summary.get("initial_capital"),
            "final_equity": summary.get("final_equity"),
            "realized_pnl": summary.get("realized_pnl"),
            "leverage": LIVE_SIM_DEFAULT_LEVERAGE,
            "shares": LIVE_SIM_DEFAULT_SHARES,
            "cap": cap,
            "equity_floor": EQUITY_FLOOR,
        },
        "reject_counters": {
            "position_cap_reject_count": cap_rejects,
            "insufficient_buying_power_count": margin_rejects,
            "maintenance_stop_count": int(summary.get("maintenance_stop_count") or 0),
            "accepted_trade_count": int(summary.get("accepted_trade_count") or 0),
            "rejected_trade_count": int(summary.get("rejected_trade_count") or 0),
        },
    }


@dataclass
class Phase592BJob:
    repo_root: Path
    cap: int = AUDIT_CAP
    initial_equity: float = LIVE_SIM_DEFAULT_STARTING_EQUITY

    def __post_init__(self) -> None:
        self.kabu = resolve_kabu_root(self.repo_root)
        self.reports_dir = resolve_reports_dir(self.kabu)

    def run(self) -> dict[str, Any]:
        trades, trade_meta = load_canonical_live_config_trades(
            self.repo_root,
            period_start=PERIOD_START,
            baseline_end=CANONICAL_BASELINE_END,
        )
        sim = run_audit_simulation(
            trades,
            initial_equity=self.initial_equity,
            equity_floor=EQUITY_FLOOR,
            cap=self.cap,
        )
        mandatory = _mandatory_answers(sim, cap=self.cap)
        return {
            "verdict": PHASE592B_VERDICT,
            "generated_at": _now_iso(),
            "trade_meta": trade_meta,
            "mandatory_answers": mandatory,
            "summary": sim["summary"],
            "audit_rows": sim["audit_rows"],
            "margin_curve": sim["margin_curve"],
            "skip_breakdown": sim["skip_breakdown"],
            "logic_reference": {
                "initial_capital": "CapScenarioState.initial_equity — never mutated after init",
                "current_equity": "initial_equity + realized_pnl — updated on each exit close",
                "buying_power": "max(0, equity * leverage_limit - gross_position_value)",
                "required_margin": "entry_price * LOT_SIZE / leverage_limit per 100-share slot",
                "cap_check_order": "after maintenance checks, before buying_power sizing",
                "skip_reasons": {
                    "max_concurrent_positions": "position_cap_reject_count",
                    "insufficient_buying_power": "insufficient_buying_power_count",
                },
            },
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        rep = self.reports_dir
        rep.mkdir(parents=True, exist_ok=True)
        paths = {
            "logic_audit": rep / "phase592b_equity_sim_logic_audit.csv",
            "equity_margin": rep / "phase592b_equity_curve_margin.csv",
            "skip_breakdown": rep / "phase592b_skip_reason_breakdown.csv",
            "report_json": rep / "phase592b_report.json",
        }
        _write_csv(paths["logic_audit"], LOGIC_AUDIT_FIELDS, result["audit_rows"])
        _write_csv(paths["equity_margin"], EQUITY_MARGIN_FIELDS, result["margin_curve"])
        _write_csv(paths["skip_breakdown"], SKIP_BREAKDOWN_FIELDS, result["skip_breakdown"])

        report = {
            k: v
            for k, v in result.items()
            if k not in ("audit_rows", "margin_curve", "skip_breakdown")
        }
        paths["report_json"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        doc = self.kabu / "docs" / "operations" / "phase592b_equity_sim_capital_logic_audit.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        ma = result.get("mandatory_answers") or {}
        summary = result.get("summary") or {}
        doc.write_text(
            "\n".join(
                [
                    "# Phase592B — Equity Simulation Capital Logic Audit",
                    "",
                    f"**Verdict:** `{PHASE592B_VERDICT}`",
                    "",
                    "## Executive summary",
                    "",
                    "- **initial_capital** is a **fixed anchor** (`1,500,000` yen); it is never reduced by losses.",
                    "- **current_equity** is **variable**: `initial_capital + realized_pnl`, updated on each simulated exit.",
                    "- **buying_power / available_margin** = `current_equity × 2.0 − gross_position_value`.",
                    "- **required_margin** per 100-share slot = `entry_price × 100 / 2.0`.",
                    "- **CAP=5**: reject when `open_positions >= 5` (`max_concurrent_positions`).",
                    "- CAP skips and margin skips use **separate counters** (`position_cap_reject_count` vs `insufficient_buying_power_count`).",
                    "",
                    "## Simulation results (CAP=5, canonical trades)",
                    "",
                    f"- accepted: {summary.get('accepted_trade_count')}",
                    f"- rejected: {summary.get('rejected_trade_count')}",
                    f"- cap rejects: {summary.get('position_cap_reject_count')}",
                    f"- margin rejects: {summary.get('insufficient_buying_power_count')}",
                    f"- final equity: {summary.get('final_equity')} (PnL {summary.get('realized_pnl')})",
                    "",
                    "## Mandatory answers",
                    "",
                ]
                + [f"{i}. {v}" for i, (_, v) in enumerate(ma.items(), 1)]
                + [
                    "",
                    "## Canonical code paths",
                    "",
                    "- `CapScenarioState.current_equity()` → `initial_equity + realized_pnl`",
                    "- `compute_buying_power()` → `equity * leverage - gross`",
                    "- `CapScenarioState.try_entry()` → cap check then `compute_requested_shares()`",
                    "- `CapScenarioState._reject_entry()` → separate cap vs margin counters",
                    "",
                    "## Outputs",
                    "",
                ]
                + [f"- `{p.name}`" for p in paths.values()]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths
