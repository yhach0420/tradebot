"""Confirm-period portfolio replay (CAP=5, no row-level PnL as confirm economics).

Build-period row labels may rank candidates; confirm applies ONE frozen spec via
portfolio simulation only. Confirm results must never reselect candidates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from research.e1_x6_provisional.cost_contract import LOT, net_pnl_yen
from research.e1_x6_provisional.util import sha256_obj, summarize_pnls
from small_paper.e1_x5_forward_shadow import (
    MAX_HOLD_SEC,
    STOP_BPS,
    TARGET_BPS,
    TRAIL_ARM_BPS,
    GIVEBACK,
    _bps,
)


CAP = 5


@dataclass
class PortfolioEvent:
    ts: datetime
    symbol: str
    signal: bool  # candidate ENTRY signal at this sample
    bid: float
    ask: float
    mid: Optional[float] = None
    x5_accept: bool = False
    event_id: str = ""


@dataclass
class _Pos:
    symbol: str
    entry_time: datetime
    entry_ask: float
    mfe_bps: float = 0.0
    mae_bps: float = 0.0
    trail_active: bool = False


@dataclass
class PortfolioReplayResult:
    completed_trades: list[dict[str, Any]] = field(default_factory=list)
    signal_ledger: list[dict[str, Any]] = field(default_factory=list)
    decision_ledger: list[dict[str, Any]] = field(default_factory=list)
    cap_blocked: int = 0
    duplicate_open_symbol_reject: int = 0
    exit_reason_counts: dict[str, int] = field(default_factory=dict)
    open_at_end_n: int = 0
    open_at_end_symbols: list[str] = field(default_factory=list)
    noise_audit: dict[str, int] = field(
        default_factory=lambda: {
            "X5_KEEP": 0,
            "X5_REMOVED": 0,
            "X6_ADDED": 0,
            "BOTH_REJECT": 0,
        }
    )

    def metrics(self) -> dict[str, Any]:
        pnls = [float(t["net_pnl_yen_100"]) for t in self.completed_trades]
        m = summarize_pnls(pnls)
        m["exit_reason_counts"] = dict(self.exit_reason_counts)
        m["cap_blocked"] = self.cap_blocked
        m["duplicate_open_symbol_reject"] = self.duplicate_open_symbol_reject
        m["noise_audit"] = dict(self.noise_audit)
        m["open_at_end_n"] = int(self.open_at_end_n)
        m["open_at_end_symbols"] = list(self.open_at_end_symbols)
        m["signal_ledger_sha256"] = sha256_obj(self.signal_ledger)
        m["portfolio_decision_ledger_sha256"] = sha256_obj(self.decision_ledger)
        m["completed_trade_ledger_sha256"] = sha256_obj(self.completed_trades)
        return m


def _exit_reason(pos: _Pos, bid: float, now: datetime) -> Optional[str]:
    ret = _bps(pos.entry_ask, bid)
    pos.mfe_bps = max(pos.mfe_bps, ret)
    pos.mae_bps = min(pos.mae_bps, ret)
    if ret <= STOP_BPS + 1e-12:
        return "STOP"
    if ret >= TARGET_BPS - 1e-12:
        return "TARGET"
    hold = (now - pos.entry_time).total_seconds()
    if hold >= MAX_HOLD_SEC - 1e-9:
        return "MAX_HOLD"
    if pos.mfe_bps >= TRAIL_ARM_BPS - 1e-9:
        pos.trail_active = True
    if pos.trail_active:
        floor = pos.mfe_bps * (1.0 - GIVEBACK)
        if ret <= floor + 1e-12:
            return "TRAILING"
    if hold >= 60.0 and pos.mfe_bps <= 0.0 + 1e-12 and ret <= 0.0 + 1e-12:
        # lightweight no_progress proxy for research confirm (aligned intent)
        return "NO_PROGRESS"
    return None


def replay_portfolio(
    events: Sequence[PortfolioEvent],
    *,
    cap: int = CAP,
) -> PortfolioReplayResult:
    """Independent CAP portfolio: one open per symbol; holding signals are not new trades."""
    out = PortfolioReplayResult()
    positions: dict[str, _Pos] = {}

    for ev in events:
        # EXIT path first on every quote for open symbols
        if ev.symbol in positions:
            pos = positions[ev.symbol]
            reason = _exit_reason(pos, ev.bid, ev.ts)
            if reason:
                econ = net_pnl_yen(pos.entry_ask, ev.bid)
                trade = {
                    "symbol": ev.symbol,
                    "entry_time": pos.entry_time.isoformat(),
                    "exit_time": ev.ts.isoformat(),
                    "entry_ask": pos.entry_ask,
                    "exit_bid": ev.bid,
                    "exit_reason": reason,
                    "holding_sec": (ev.ts - pos.entry_time).total_seconds(),
                    "lot": LOT,
                    **econ,
                }
                out.completed_trades.append(trade)
                out.exit_reason_counts[reason] = out.exit_reason_counts.get(reason, 0) + 1
                out.decision_ledger.append(
                    {
                        "ts": ev.ts.isoformat(),
                        "symbol": ev.symbol,
                        "decision": "EXIT",
                        "reason": reason,
                        "event_id": ev.event_id,
                    }
                )
                del positions[ev.symbol]

        x6 = bool(ev.signal)
        x5 = bool(ev.x5_accept)
        if x5 and x6:
            out.noise_audit["X5_KEEP"] += 1
        elif x5 and not x6:
            out.noise_audit["X5_REMOVED"] += 1
        elif (not x5) and x6:
            out.noise_audit["X6_ADDED"] += 1
        else:
            out.noise_audit["BOTH_REJECT"] += 1

        out.signal_ledger.append(
            {
                "ts": ev.ts.isoformat(),
                "symbol": ev.symbol,
                "signal": x6,
                "x5_accept": x5,
                "event_id": ev.event_id,
            }
        )

        if not x6:
            continue

        if ev.symbol in positions:
            # continuous 5s signal while holding → not a new trade
            out.decision_ledger.append(
                {
                    "ts": ev.ts.isoformat(),
                    "symbol": ev.symbol,
                    "decision": "HOLD_SIGNAL_IGNORED",
                    "reason": "SAME_SYMBOL_OPEN",
                    "event_id": ev.event_id,
                }
            )
            out.duplicate_open_symbol_reject += 1
            continue

        if len(positions) >= cap:
            out.cap_blocked += 1
            out.decision_ledger.append(
                {
                    "ts": ev.ts.isoformat(),
                    "symbol": ev.symbol,
                    "decision": "REJECT",
                    "reason": "CAP5_BLOCKED",
                    "event_id": ev.event_id,
                }
            )
            continue

        positions[ev.symbol] = _Pos(
            symbol=ev.symbol, entry_time=ev.ts, entry_ask=float(ev.ask)
        )
        out.decision_ledger.append(
            {
                "ts": ev.ts.isoformat(),
                "symbol": ev.symbol,
                "decision": "ENTRY",
                "reason": "CANDIDATE_SIGNAL",
                "event_id": ev.event_id,
                "entry_ask": float(ev.ask),
            }
        )

    out.open_at_end_n = len(positions)
    out.open_at_end_symbols = sorted(positions.keys())
    return out


def select_candidate_build_only(
    ranked: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Pick exactly one candidate from build ranking; confirm must not re-rank."""
    if not ranked:
        raise ValueError("no candidates to select")
    # Already sorted by build expectancy / support / id
    selected = dict(ranked[0])
    selected["selection_basis"] = {
        "rule": "build_only_rank_key=(-has_support, -build_expectancy_proxy, -build_support, candidate_id)",
        "build_support": selected.get("build_support"),
        "build_expectancy_proxy": selected.get("build_expectancy_proxy"),
        "confirm_not_used": True,
    }
    return selected


def assert_no_confirm_reselection(selected_before_confirm: str, selected_after: str) -> None:
    if selected_before_confirm != selected_after:
        raise AssertionError(
            f"confirm reselection forbidden: before={selected_before_confirm} after={selected_after}"
        )
