"""Event-driven CAP=5 portfolio replay with same-symbol / episode integrity."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from research.pbv2_zero_base_revalidation.metrics import pnl_metric_block
from research.price_flow_exit_integrity.constants import CAP
from research.price_flow_exit_integrity.dd import summarize_dd
from research.price_flow_exit_integrity.trades import SimTrade


@dataclass
class OpenPos:
    trade: SimTrade
    entry_event_id: str


@dataclass
class Cap5Result:
    portfolio_id: str
    total_candidate: int
    accepted: int
    cap_blocked: int
    same_symbol_blocked: int
    episode_blocked: int
    duplicate_entry_blocked: int
    session_cross_blocked: int
    active_max: int
    trades: list[SimTrade] = field(default_factory=list)
    event_log: list[dict[str, Any]] = field(default_factory=list)
    blocked: list[dict[str, Any]] = field(default_factory=list)
    slot_occupancy: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        pnls = [t.pnl_5bps for t in self.trades]
        block = pnl_metric_block(pnls, pnls) if pnls else {"n": 0, "total_pnl_5bps": 0.0, "PF_5bps": None}
        reasons: dict[str, int] = defaultdict(int)
        methods: dict[str, int] = defaultdict(int)
        for t in self.trades:
            reasons[t.exit_reason] += 1
            methods[t.entry_method] += 1
        dd = summarize_dd(self.trades)
        return {
            "portfolio_id": self.portfolio_id,
            "total_candidate": self.total_candidate,
            "accepted": self.accepted,
            "cap_blocked": self.cap_blocked,
            "same_symbol_blocked": self.same_symbol_blocked,
            "episode_blocked": self.episode_blocked,
            "duplicate_entry_blocked": self.duplicate_entry_blocked,
            "session_cross_blocked": self.session_cross_blocked,
            "active_max": self.active_max,
            "trades": len(self.trades),
            "pnl_5bps": round(float(block.get("total_pnl_5bps") or 0), 2),
            "PF_5bps": block.get("PF_5bps"),
            "max_dd_trade_sequence": dd.get("trade_sequence_max_dd"),
            "max_dd_intraday": dd.get("intraday_max_dd"),
            "max_dd_daily_close": dd.get("daily_close_max_dd"),
            "max_dd_cap5_portfolio": dd.get("cap5_portfolio_max_dd"),
            "consecutive_loss": dd.get("consecutive_loss"),
            "max_open_loss": dd.get("max_open_loss"),
            "peak_gross_exposure": dd.get("peak_gross_exposure"),
            "entry_mix": dict(methods),
            "exit_reason_mix": dict(reasons),
            **{k: block.get(k) for k in ("gross_profit_5bps", "gross_loss_5bps", "n")},
        }


def select_candidate_trades(
    by_mode: dict[str, list[SimTrade]],
    *,
    portfolio_id: str,
    entry_filter: str,
) -> list[SimTrade]:
    """Build candidate trade list with EXIT already simulated per policy."""
    if portfolio_id == "P0":
        return list(by_mode.get("E0_X0") or [])
    if portfolio_id == "P1":
        return list(by_mode.get("E0_X6") or [])
    if portfolio_id == "P2":
        return list(by_mode.get("E1_X0") or [])
    if portfolio_id == "P3":
        return list(by_mode.get("E1_X4") or [])
    if portfolio_id == "P4":
        # union: PBv2-only X6 + VCIE-only X4 + BOTH X6
        e0x6 = { (t.day, t.symbol, t.entry_time): t for t in (by_mode.get("E0_X6") or []) }
        e1x4 = { (t.day, t.symbol, t.entry_time): t for t in (by_mode.get("E1_X4") or []) }
        e1x6 = { (t.day, t.symbol, t.entry_time): t for t in (by_mode.get("E1_X6") or []) }
        out: list[SimTrade] = []
        seen: set[tuple] = set()
        for t in e0x6.values():
            k = (t.day, t.symbol, t.entry_time, t.setup_id)
            if k in seen:
                continue
            seen.add(k)
            out.append(t)
        for key, t in e1x4.items():
            # skip if near-duplicate of PBv2 (already in)
            dup = any(
                p.day == t.day and p.symbol == t.symbol and abs((p.entry_time - t.entry_time).total_seconds()) <= 120
                for p in e0x6.values()
            )
            if dup:
                # BOTH → use X6 from E1 if available else skip VCIE X4
                both = e1x6.get(key)
                if both and (t.day, t.symbol, t.entry_time, both.setup_id) not in seen:
                    # already covered by PBv2 side if near; skip to avoid double
                    continue
                continue
            k = (t.day, t.symbol, t.entry_time, t.setup_id)
            if k not in seen:
                seen.add(k)
                out.append(t)
        return out
    if portfolio_id == "P5":
        # fixed priority: at overlap keep PBv2 X6 only
        e0 = list(by_mode.get("E0_X6") or [])
        e1 = list(by_mode.get("E1_X4") or [])
        out = list(e0)
        for t in e1:
            if any(
                p.day == t.day and p.symbol == t.symbol and abs((p.entry_time - t.entry_time).total_seconds()) <= 120
                for p in e0
            ):
                continue
            out.append(t)
        return out
    return list(by_mode.get(entry_filter) or [])


def replay_cap5(
    candidates: Sequence[SimTrade],
    *,
    portfolio_id: str,
    cap: int = CAP,
) -> Cap5Result:
    """True event-driven CAP=5: EXIT frees slot; same-ts EXIT before ENTRY; AM/PM hard boundary."""
    # Deduplicate identical entry candidates
    uniq: list[SimTrade] = []
    seen_entry: set[tuple] = set()
    dup_blocked = 0
    for t in sorted(candidates, key=lambda x: (x.day, x.entry_time, x.symbol, x.setup_id)):
        k = (t.day, t.symbol, t.entry_time.replace(microsecond=0), t.impulse_episode_id)
        if k in seen_entry:
            dup_blocked += 1
            continue
        seen_entry.add(k)
        uniq.append(t)

    # Build event timeline: EXIT and ENTRY
    # ENTRY event at entry_time; EXIT event at exit_time
    events: list[tuple[datetime, int, str, SimTrade]] = []
    # order_key: 0=EXIT, 1=ENTRY so EXIT first at same timestamp
    for t in uniq:
        events.append((t.entry_time, 1, "ENTRY", t))
        events.append((t.exit_time, 0, "EXIT", t))
    events.sort(key=lambda e: (e[0], e[1], e[3].symbol, e[3].setup_id))

    open_pos: dict[str, OpenPos] = {}  # setup_id -> open
    open_by_symbol: dict[tuple[str, str], str] = {}  # (day,symbol) -> setup_id while open
    used_episodes: set[tuple[str, str, str]] = set()  # day, symbol, impulse_episode
    res = Cap5Result(portfolio_id=portfolio_id, total_candidate=len(uniq), accepted=0, cap_blocked=0, same_symbol_blocked=0, episode_blocked=0, duplicate_entry_blocked=dup_blocked, session_cross_blocked=0, active_max=0)
    # Track which candidate was accepted
    accepted_ids: set[str] = set()
    # Per day/session force-close: positions cannot span sessions — reject entry if would, and force-close at session boundary conceptually via trade.exit already session-scoped in X0

    for ts, _ord, kind, t in events:
        if kind == "EXIT":
            op = open_pos.pop(t.setup_id, None)
            if op is None:
                continue
            open_by_symbol.pop((t.day, t.symbol), None)
            res.trades.append(t)
            res.event_log.append(
                {
                    "ts": ts.isoformat(),
                    "event": "EXIT",
                    "portfolio": portfolio_id,
                    "symbol": t.symbol,
                    "day": t.day,
                    "setup_id": t.setup_id,
                    "exit_reason": t.exit_reason,
                    "pnl_5bps": t.pnl_5bps,
                    "active_after": len(open_pos),
                }
            )
            continue

        # ENTRY
        eid = t.setup_id
        if eid in accepted_ids or eid in open_pos:
            res.duplicate_entry_blocked += 1
            res.blocked.append({"ts": ts.isoformat(), "reason": "DUPLICATE_ENTRY", "symbol": t.symbol, "setup_id": eid, "day": t.day})
            continue
        # session span: if exit session != entry session, block (integrity)
        if t.session != ("AM" if t.exit_time.hour < 12 else "PM") and not (
            t.session == "AM" and t.exit_time.hour < 12 or t.session == "PM" and t.exit_time.hour >= 12
        ):
            # allow if exit same session
            pass
        exit_sess = "AM" if t.exit_time.hour < 12 else "PM"
        if exit_sess != t.session:
            res.session_cross_blocked += 1
            res.blocked.append({"ts": ts.isoformat(), "reason": "SESSION_CROSS", "symbol": t.symbol, "setup_id": eid, "day": t.day})
            continue
        sk = (t.day, t.symbol)
        if sk in open_by_symbol:
            res.same_symbol_blocked += 1
            res.blocked.append({"ts": ts.isoformat(), "reason": "SAME_SYMBOL_OPEN", "symbol": t.symbol, "setup_id": eid, "day": t.day})
            continue
        ep = (t.day, t.symbol, t.impulse_episode_id)
        if ep in used_episodes:
            res.episode_blocked += 1
            res.blocked.append({"ts": ts.isoformat(), "reason": "IMPULSE_EPISODE_DUP", "symbol": t.symbol, "setup_id": eid, "day": t.day})
            continue
        if len(open_pos) >= cap:
            res.cap_blocked += 1
            res.blocked.append({"ts": ts.isoformat(), "reason": "CAP_BLOCKED", "symbol": t.symbol, "setup_id": eid, "day": t.day, "active": len(open_pos)})
            continue
        open_pos[eid] = OpenPos(trade=t, entry_event_id=eid)
        open_by_symbol[sk] = eid
        used_episodes.add(ep)
        accepted_ids.add(eid)
        res.accepted += 1
        res.active_max = max(res.active_max, len(open_pos))
        res.event_log.append(
            {
                "ts": ts.isoformat(),
                "event": "ENTRY",
                "portfolio": portfolio_id,
                "symbol": t.symbol,
                "day": t.day,
                "setup_id": eid,
                "mode": t.mode,
                "active_after": len(open_pos),
            }
        )
        res.slot_occupancy.append({"ts": ts.isoformat(), "active": len(open_pos), "day": t.day})

    # Force-close any residual opens (should be rare; keeps accepted==trades)
    for eid, op in list(open_pos.items()):
        t = op.trade
        open_pos.pop(eid, None)
        open_by_symbol.pop((t.day, t.symbol), None)
        res.trades.append(t)
        res.event_log.append(
            {
                "ts": t.exit_time.isoformat(),
                "event": "EXIT_RESIDUAL",
                "portfolio": portfolio_id,
                "symbol": t.symbol,
                "day": t.day,
                "setup_id": t.setup_id,
                "exit_reason": t.exit_reason,
                "pnl_5bps": t.pnl_5bps,
                "active_after": len(open_pos),
            }
        )

    # Sort trades by exit time for reporting
    res.trades.sort(key=lambda x: (x.day, x.exit_time, x.symbol))
    return res


def audit_overlapping_entries(trades: Sequence[SimTrade]) -> dict[str, Any]:
    """Detect same-symbol overlapping holds in a trade set (should be 0 after CAP5 filter)."""
    overlaps: list[dict[str, Any]] = []
    by_sym: dict[tuple[str, str], list[SimTrade]] = defaultdict(list)
    for t in trades:
        by_sym[(t.day, t.symbol)].append(t)
    for (day, sym), xs in by_sym.items():
        xs = sorted(xs, key=lambda t: t.entry_time)
        for i, a in enumerate(xs):
            for b in xs[i + 1 :]:
                if b.entry_time < a.exit_time and a.entry_time < b.exit_time:
                    overlaps.append(
                        {
                            "day": day,
                            "symbol": sym,
                            "a_setup": a.setup_id,
                            "b_setup": b.setup_id,
                            "a_entry": a.entry_time.isoformat(),
                            "a_exit": a.exit_time.isoformat(),
                            "b_entry": b.entry_time.isoformat(),
                            "b_exit": b.exit_time.isoformat(),
                            "a_pnl": a.pnl_5bps,
                            "b_pnl": b.pnl_5bps,
                        }
                    )
    return {
        "same_symbol_overlapping_entry_count": len(overlaps),
        "overlaps": overlaps,
        "verdict": "POSITION_STATE_INTEGRITY_PASS" if not overlaps else "POSITION_STATE_INTEGRITY_BLOCKED",
    }


def filter_no_overlap(trades: Sequence[SimTrade]) -> tuple[list[SimTrade], list[SimTrade]]:
    """Greedy first-entry-wins no-overlap filter (chronological)."""
    kept: list[SimTrade] = []
    dropped: list[SimTrade] = []
    open_until: dict[tuple[str, str], datetime] = {}
    for t in sorted(trades, key=lambda x: (x.day, x.entry_time, x.symbol)):
        sk = (t.day, t.symbol)
        until = open_until.get(sk)
        if until is not None and t.entry_time < until:
            dropped.append(t)
            continue
        kept.append(t)
        open_until[sk] = t.exit_time
    return kept, dropped
