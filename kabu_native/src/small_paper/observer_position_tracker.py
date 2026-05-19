"""
Observer-only virtual position lifecycle for Discord judgment events (no orders).
Phase 61: optional combined_structural_exit_v1 (structure-only EXIT; no virtual_hold Discord).
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from research.continuation_quality_ranking import continuation_components
from research.research_exit_criteria import _as_float
from research.structural_exit_policies import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    POLICY_STRUCTURAL_OBSERVER_V1,
    combined_exit_signal_on_latest_tick,
    is_official_structural_exit_reason,
    is_virtual_hold_exit_reason,
    tick_from_candidate,
)
from storage.intraday_recorder import parse_kabu_time

JST = ZoneInfo("Asia/Tokyo")

OBSERVER_HOLD = "hold"
OBSERVER_TAKE = "take"
OBSERVER_EXIT = "exit"


@dataclass
class ObserverTrackerConfig:
    hold_min: float = 15.0
    hold_quality_delta: float = 0.03
    take_quality_drop: float = 0.08
    hard_stop_pct: float = 1.20
    display_take_pct: float = 4.0
    favorable_fade_ratio: float = 0.85
    momentum_weaken_ratio: float = 0.85
    structural_exit_policy: str = POLICY_STRUCTURAL_OBSERVER_V1
    live_session_end: str = "15:30"

    def uses_combined_structural_exit(self) -> bool:
        return self.structural_exit_policy == POLICY_COMBINED_STRUCTURAL_EXIT_V1


@dataclass
class _VirtualPosition:
    symbol: str
    profile: str
    entry_price: float
    stop_price: float
    take_price: float
    entry_time: datetime
    exit_time: datetime
    quality_tier: str
    peak_quality: float
    peak_pnl_pct: float
    peak_momentum: float
    peak_favorable: float
    last_quality: float
    last_hold_notify_mono: float
    last_price: float
    mae_pnl_pct: float = 0.0
    rich_ticks: list[dict[str, Any]] = field(default_factory=list)
    take_notified: bool = False
    closed: bool = False
    trade_virtual_exit_time: Optional[datetime] = None
    virtual_hold_ignore_logged: bool = False


@dataclass
class ObserverJudgmentEvent:
    kind: str
    symbol: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class ObserverSessionStats:
    entry_count: int = 0
    exit_count: int = 0
    hold_notify_count: int = 0
    take_count: int = 0
    hold_durations_sec: list[float] = field(default_factory=list)
    structural_exit_count: int = 0
    official_exit_count: int = 0
    virtual_hold_expired_ignored_count: int = 0
    session_end_exit_count: int = 0
    structural_exit_reason_counts: Counter[str] = field(default_factory=Counter)

    @property
    def holding_count(self) -> int:
        return 0


class ObserverPositionTracker:
    """Track gate-accepted virtual holds and emit observer judgment events."""

    def __init__(self, cfg: ObserverTrackerConfig) -> None:
        self.cfg = cfg
        self._positions: dict[str, _VirtualPosition] = {}
        self.stats = ObserverSessionStats()

    def open_count(self) -> int:
        return sum(1 for p in self._positions.values() if not p.closed)

    def has_open(self, symbol: str) -> bool:
        p = self._positions.get(symbol)
        return p is not None and not p.closed

    def _session_end_datetime(self, entry: datetime) -> datetime:
        from small_paper.session_schedule import parse_hhmm

        end_clock = parse_hhmm(self.cfg.live_session_end or "15:30")
        ex = entry.replace(
            hour=end_clock.hour,
            minute=end_clock.minute,
            second=getattr(end_clock, "second", 0),
            microsecond=0,
        )
        if ex <= entry:
            ex = entry + timedelta(hours=6)
        return ex

    def close_for_overlap(
        self,
        *,
        symbol: str,
        trade: Mapping[str, Any],
        payload: Mapping[str, Any],
        current_price: float,
        session_bucket: str,
    ) -> list[ObserverJudgmentEvent]:
        pos = self._positions.get(symbol)
        if pos is None or pos.closed:
            return []
        now = datetime.now(JST)
        comps = continuation_components(trade)
        q = float(comps["continuation_quality"])
        pnl_pct = ((current_price - pos.entry_price) / pos.entry_price * 100.0) if pos.entry_price > 0 else 0.0
        ctx = {
            "symbol": symbol,
            "profile": pos.profile,
            "session_bucket": session_bucket,
            "continuation_quality": q,
            "quality_tier": pos.quality_tier,
            "components": comps,
            "current_price": current_price,
            "entry_price": pos.entry_price,
            "unrealized_pnl_pct": round(pnl_pct, 4),
            "hold_duration_sec": round(max(0.0, (now - pos.entry_time).total_seconds()), 1),
            "peak_pnl_pct": round(pos.peak_pnl_pct, 4),
            "mfe_pct": round(pos.peak_pnl_pct, 4),
            "mae_pct": round(pos.mae_pnl_pct, 4),
            "timestamp": now.isoformat(timespec="seconds"),
        }
        return [
            self._close(
                pos,
                reason="overlap_replaced_review",
                exit_kind="overlap_replaced_review",
                ctx=ctx,
                structural=True,
            )
        ]

    def register_entry(
        self,
        *,
        trade: Mapping[str, Any],
        payload: Mapping[str, Any],
        quality_tier: str,
        entry_price: float,
    ) -> None:
        sym = str(trade.get("symbol") or "")
        if not sym:
            return
        if sym in self._positions and not self._positions[sym].closed:
            return
        if sym in self._positions:
            del self._positions[sym]
        ent = parse_kabu_time(trade.get("entry_time"), fallback=datetime.now(JST))
        trade_vh_ex = parse_kabu_time(trade.get("exit_time"), fallback=ent)
        if self.cfg.uses_combined_structural_exit():
            ex = self._session_end_datetime(ent)
        else:
            ex = trade_vh_ex
        stop = entry_price * (1.0 - self.cfg.hard_stop_pct / 100.0)
        take = entry_price * (1.0 + self.cfg.display_take_pct / 100.0)
        comps = continuation_components(trade)
        q = float(comps["continuation_quality"])
        self._positions[sym] = _VirtualPosition(
            symbol=sym,
            profile=str(trade.get("profile", "")),
            entry_price=entry_price,
            stop_price=stop,
            take_price=take,
            entry_time=ent,
            exit_time=ex,
            quality_tier=quality_tier,
            peak_quality=q,
            peak_pnl_pct=0.0,
            peak_momentum=float(comps["momentum_continuation"]),
            peak_favorable=float(comps["favorable_continuation"]),
            last_quality=q,
            last_hold_notify_mono=time.monotonic(),
            last_price=entry_price,
            mae_pnl_pct=0.0,
            trade_virtual_exit_time=trade_vh_ex,
        )
        self.stats.entry_count += 1

    def on_tick(
        self,
        *,
        symbol: str,
        trade: Mapping[str, Any],
        payload: Mapping[str, Any],
        current_price: Optional[float],
        session_bucket: str,
    ) -> list[ObserverJudgmentEvent]:
        pos = self._positions.get(symbol)
        if pos is None or pos.closed:
            return []

        now = datetime.now(JST)
        price = _as_float(current_price) or _as_float(payload.get("CurrentPrice")) or pos.entry_price
        pos.last_price = float(price)
        pnl_pct = ((price - pos.entry_price) / pos.entry_price * 100.0) if pos.entry_price > 0 else 0.0
        pos.mae_pnl_pct = min(pos.mae_pnl_pct, pnl_pct)
        comps = continuation_components(trade)
        q = float(comps["continuation_quality"])
        mom = float(comps["momentum_continuation"])
        pos.peak_quality = max(pos.peak_quality, q)
        pos.peak_pnl_pct = max(pos.peak_pnl_pct, pnl_pct)
        pos.peak_momentum = max(pos.peak_momentum, mom)
        pos.peak_favorable = max(pos.peak_favorable, float(comps["favorable_continuation"]))
        hold_sec = max(0.0, (now - pos.entry_time).total_seconds())

        tick = tick_from_candidate(trade, pos.entry_price, pos.peak_quality)
        tick["ts_epoch"] = now.timestamp()
        pos.rich_ticks.append(tick)

        base_ctx = {
            "symbol": symbol,
            "profile": pos.profile,
            "session_bucket": session_bucket,
            "continuation_quality": q,
            "quality_tier": pos.quality_tier,
            "components": comps,
            "current_price": price,
            "entry_price": pos.entry_price,
            "unrealized_pnl_pct": round(pnl_pct, 4),
            "hold_duration_sec": round(hold_sec, 1),
            "peak_pnl_pct": round(pos.peak_pnl_pct, 4),
            "mfe_pct": round(pos.peak_pnl_pct, 4),
            "mae_pct": round(pos.mae_pnl_pct, 4),
            "momentum_continuation": mom,
            "timestamp": now.isoformat(timespec="seconds"),
            "structural_exit_policy": self.cfg.structural_exit_policy,
        }

        events: list[ObserverJudgmentEvent] = []

        if self.cfg.uses_combined_structural_exit():
            if (
                pos.trade_virtual_exit_time
                and now >= pos.trade_virtual_exit_time
                and not pos.virtual_hold_ignore_logged
            ):
                self.stats.virtual_hold_expired_ignored_count += 1
                pos.virtual_hold_ignore_logged = True
            sig = combined_exit_signal_on_latest_tick(pos.rich_ticks, pos.entry_price, self.cfg)
            if sig:
                exit_pnl, reason, close_px = sig
                ctx = {**base_ctx, "unrealized_pnl_pct": round(exit_pnl, 4), "current_price": close_px}
                events.append(
                    self._close(
                        pos,
                        reason=reason,
                        exit_kind=reason,
                        ctx=ctx,
                        structural=True,
                    )
                )
                return events
        else:
            if now >= pos.exit_time:
                events.append(
                    self._close(
                        pos,
                        reason="virtual_hold_expired",
                        exit_kind="continuation_breakdown",
                        ctx=base_ctx,
                        structural=False,
                    )
                )
                return events

            if price <= pos.stop_price:
                events.append(
                    self._close(pos, reason="stop_hit", exit_kind="stop_hit", ctx=base_ctx, structural=True)
                )
                return events

        if not pos.take_notified:
            take_reason = self._take_reason(pos, comps, q, price, pnl_pct)
            if take_reason:
                pos.take_notified = True
                self.stats.take_count += 1
                ctx = {
                    **base_ctx,
                    "take_reason": take_reason,
                    "continuation_weakening": mom < pos.peak_momentum * self.cfg.momentum_weaken_ratio,
                    "favorable_fade": comps["favorable_continuation"]
                    < pos.peak_favorable * self.cfg.favorable_fade_ratio,
                    "quality_deterioration": q <= pos.peak_quality - self.cfg.take_quality_drop,
                }
                events.append(ObserverJudgmentEvent(kind=OBSERVER_TAKE, symbol=symbol, context=ctx))

        if pos.closed:
            return events

        hold_reason = self._hold_reason(pos, q)
        if hold_reason:
            pos.last_hold_notify_mono = time.monotonic()
            pos.last_quality = q
            self.stats.hold_notify_count += 1
            ctx = {
                **base_ctx,
                "hold_reason": hold_reason,
                "continuation_persistence": comps["continuation_persistence"],
                "bullish_continuation": comps["bullish_continuation"],
                "bearish_accumulation": comps["bearish_accumulation"],
            }
            events.append(ObserverJudgmentEvent(kind=OBSERVER_HOLD, symbol=symbol, context=ctx))

        return events

    def close_all(self, *, reason: str = "session_end") -> list[ObserverJudgmentEvent]:
        out: list[ObserverJudgmentEvent] = []
        for sym, pos in list(self._positions.items()):
            if pos.closed:
                continue
            now = datetime.now(JST)
            price = pos.last_price or pos.entry_price
            pnl_pct = ((price - pos.entry_price) / pos.entry_price * 100.0) if pos.entry_price > 0 else 0.0
            ctx = {
                "symbol": sym,
                "profile": pos.profile,
                "current_price": price,
                "entry_price": pos.entry_price,
                "unrealized_pnl_pct": round(pnl_pct, 4),
                "realized_pnl_pct": round(pnl_pct, 4),
                "exit_reason": reason,
                "hold_duration_sec": round(max(0.0, (now - pos.entry_time).total_seconds()), 1),
                "peak_pnl_pct": round(pos.peak_pnl_pct, 4),
                "mfe_pct": round(pos.peak_pnl_pct, 4),
                "mae_pct": round(pos.mae_pnl_pct, 4),
                "timestamp": now.isoformat(timespec="seconds"),
                "structural_exit_policy": self.cfg.structural_exit_policy,
            }
            out.append(
                self._close(
                    pos,
                    reason=reason,
                    exit_kind="session_end",
                    ctx=ctx,
                    structural=True,
                )
            )
        return out

    def _take_reason(
        self,
        pos: _VirtualPosition,
        comps: Mapping[str, float],
        q: float,
        price: float,
        pnl_pct: float,
    ) -> str:
        if price >= pos.take_price:
            return "display_take_target_reached"
        if q <= pos.peak_quality - self.cfg.take_quality_drop:
            return "quality_deterioration"
        if comps["favorable_continuation"] < pos.peak_favorable * self.cfg.favorable_fade_ratio:
            return "favorable_fade"
        if comps["momentum_continuation"] < pos.peak_momentum * self.cfg.momentum_weaken_ratio:
            return "continuation_weakening"
        if pnl_pct >= self.cfg.display_take_pct * 0.9:
            return "unrealized_pnl_near_take"
        return ""

    def _hold_reason(self, pos: _VirtualPosition, q: float) -> str:
        elapsed_min = (time.monotonic() - pos.last_hold_notify_mono) / 60.0
        if q >= pos.last_quality + self.cfg.hold_quality_delta:
            return "continuation_quality_rising"
        if elapsed_min >= self.cfg.hold_min:
            return "periodic_hold_update"
        return ""

    def _close(
        self,
        pos: _VirtualPosition,
        *,
        reason: str,
        exit_kind: str,
        ctx: Mapping[str, Any],
        structural: bool,
    ) -> ObserverJudgmentEvent:
        if not pos.closed:
            pos.closed = True
            self.stats.exit_count += 1
            hold_sec = max(0.0, (datetime.now(JST) - pos.entry_time).total_seconds())
            self.stats.hold_durations_sec.append(hold_sec)
            if structural and is_official_structural_exit_reason(reason):
                self.stats.structural_exit_count += 1
                self.stats.official_exit_count += 1
                self.stats.structural_exit_reason_counts[reason] += 1
                if reason == "session_end":
                    self.stats.session_end_exit_count += 1
        comps = ctx.get("components") or {}
        full = {
            **dict(ctx),
            "exit_reason": reason,
            "realized_pnl_pct": ctx.get("unrealized_pnl_pct", ctx.get("realized_pnl_pct", 0)),
            "max_favorable": ctx.get("peak_pnl_pct", pos.peak_pnl_pct),
            "max_adverse": ctx.get("mae_pct", pos.mae_pnl_pct),
            "continuation_breakdown": exit_kind in ("continuation_breakdown", "session_end"),
            "bearish_accumulation": comps.get("bearish_accumulation"),
            "is_structural_exit": structural and is_official_structural_exit_reason(reason),
            "structural_exit_policy": self.cfg.structural_exit_policy,
            "take_was_not_exit": True,
        }
        return ObserverJudgmentEvent(kind=OBSERVER_EXIT, symbol=pos.symbol, context=full)
