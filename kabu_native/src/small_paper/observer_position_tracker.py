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
from research.take_exit_shadow import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_TAKE_EXIT_SHADOW,
    TAKE_EXIT_REASON,
    _cfg_for_v1_signal as _take_exit_cfg_for_v1,
    uses_take_exit_shadow,
)
from research.fade_watch_shadow import (
    FadeWatchState,
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_WATCH_SHADOW,
    map_session_close_reason,
    uses_fade_watch_shadow,
)
from research.fade_hybrid_shadow import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_BREAKDOWN_CONFIRMED_SHADOW,
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_BREAKDOWN_SHADOW,
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_DISABLE_SHADOW,
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_HYBRID_SHADOW,
    combined_exit_or_fade_shadow_trigger,
    enter_fade_shadow_state,
    process_fade_shadow_watch_tick,
    shadow_watch_log_fields,
    uses_breakdown_confirmed_shadow,
    uses_fade_disable_shadow,
    uses_fade_hybrid_shadow,
    uses_fade_breakdown_shadow,
    uses_fade_shadow_trigger,
    uses_fade_shadow_watch,
 )
from research.structural_exit_policies import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW,
    POLICY_COMBINED_STRUCTURAL_EXIT_V2_PRICE_MOM,
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
    price_momentum_fade_ratio: float = 0.85
    live_session_end: str = "15:30"
    no_progress_exit_enabled: bool = False
    exit_shadow_monitor_enabled: bool = False
    exit_shadow_monitor_t2_enabled: bool = True
    exit_shadow_monitor_t3_enabled: bool = True

    def uses_combined_structural_exit(self) -> bool:
        return self.structural_exit_policy in (
            POLICY_COMBINED_STRUCTURAL_EXIT_V1,
            POLICY_COMBINED_STRUCTURAL_EXIT_V2_PRICE_MOM,
            POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_WATCH_SHADOW,
            "combined_structural_exit_v1_take_exit_shadow",
            POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_HYBRID_SHADOW,
            POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_BREAKDOWN_SHADOW,
            POLICY_COMBINED_STRUCTURAL_EXIT_V1_BREAKDOWN_CONFIRMED_SHADOW,
            POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_DISABLE_SHADOW,
            POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW,
        )


@dataclass
class _VirtualPosition:
    symbol: str
    position_id: str
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
    peak_pure_price_momentum: float
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
    fade_watch: Optional[FadeWatchState] = None
    entry_shadow: dict[str, Any] = field(default_factory=dict)
    market_entry_time: Optional[datetime] = None
    current_price_time: Optional[datetime] = None
    accepted_event_time: Optional[datetime] = None
    market_time_age_sec: Optional[float] = None
    price_age_sec_at_entry: Optional[float] = None
    stale_trade: bool = False
    session_id: str = ""
    session_kind: str = ""


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
    morning_session_close_count: int = 0
    afternoon_session_close_count: int = 0
    fade_watch_enter_count: int = 0
    fade_watch_exit_count: int = 0
    fade_watch_continue_count: int = 0
    structural_exit_reason_counts: Counter[str] = field(default_factory=Counter)

    @property
    def holding_count(self) -> int:
        return 0


class ObserverPositionTracker:
    """Track gate-accepted virtual holds and emit observer judgment events."""

    def __init__(
        self,
        cfg: ObserverTrackerConfig,
        *,
        board_exit_shadow: Any = None,
        exit_candidate_shadow: Any = None,
    ) -> None:
        self.cfg = cfg
        self._positions: dict[str, _VirtualPosition] = {}
        self.stats = ObserverSessionStats()
        self.board_exit_shadow = board_exit_shadow
        self.exit_candidate_shadow = exit_candidate_shadow
        self._scope: Optional[Any] = None

    def bind_session(self, scope: Any) -> None:
        """Phase663A: reset observer positions at session boundary (no AM→PM carry)."""
        self._scope = scope
        self._positions.clear()

    @property
    def session_id(self) -> str:
        return str(getattr(self._scope, "session_id", "") or "")

    @property
    def session_kind(self) -> str:
        return str(getattr(self._scope, "session_kind", "") or "")

    def open_count(self) -> int:
        return sum(1 for p in self._positions.values() if not p.closed)

    def has_open(self, symbol: str) -> bool:
        p = self._positions.get(symbol)
        return p is not None and not p.closed

    def open_symbols(self) -> list[str]:
        return sorted(sym for sym, p in self._positions.items() if not p.closed)

    def open_count_by_entry_type(self) -> tuple[int, int]:
        """Return (pbv2_open, or_overlay_open)."""
        from small_paper.or_overlay_cap import ENTRY_TYPE_OR

        pbv2 = or_open = 0
        for pos in self._positions.values():
            if pos.closed:
                continue
            et = str((pos.entry_shadow or {}).get("entry_type") or "PBV2").strip().upper()
            if et == ENTRY_TYPE_OR:
                or_open += 1
            else:
                pbv2 += 1
        return pbv2, or_open

    def open_positions(self) -> list[dict[str, Any]]:
        """Open positions with entry_type and unrealized PnL."""
        now = datetime.now(JST)
        out: list[dict[str, Any]] = []
        for sym, pos in self._positions.items():
            if pos.closed or pos.entry_price <= 0:
                continue
            px = pos.last_price if pos.last_price > 0 else pos.entry_price
            pnl = (px - pos.entry_price) / pos.entry_price * 100.0
            shadow = pos.entry_shadow or {}
            out.append(
                {
                    "symbol": sym,
                    "entry_type": shadow.get("entry_type", "PBV2"),
                    "or_reason": shadow.get("or_reason"),
                    "unrealized_pnl_pct": round(pnl, 4),
                    "hold_minutes": round(max(0.0, (now - pos.entry_time).total_seconds()) / 60.0, 1),
                }
            )
        return out

    def snapshot_open_holdings(self) -> list[dict[str, Any]]:
        """Read-only unrealized PnL for open virtual positions (Discord UX)."""
        now = datetime.now(JST)
        out: list[dict[str, Any]] = []
        for sym, pos in self._positions.items():
            if pos.closed or pos.entry_price <= 0:
                continue
            px = pos.last_price if pos.last_price > 0 else pos.entry_price
            pnl = (px - pos.entry_price) / pos.entry_price * 100.0
            hold_min = max(0.0, (now - pos.entry_time).total_seconds()) / 60.0
            shadow = pos.entry_shadow or {}
            v2 = shadow.get("entry_expectancy_score_v2")
            out.append(
                {
                    "symbol": sym,
                    "symbol_short": sym.replace(".T", ""),
                    "unrealized_pnl_pct": round(pnl, 2),
                    "entry_score_v2": v2,
                    "hold_minutes": round(hold_min, 0),
                    "entry_price": pos.entry_price,
                    "current_price": px,
                }
            )
        return sorted(out, key=lambda r: str(r.get("symbol", "")))

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
        from small_paper.observer_session_scope import observer_entry_allowed_for_scope

        if self._scope is not None and not observer_entry_allowed_for_scope(
            self._scope, trade, payload=payload
        ):
            return
        if sym in self._positions and not self._positions[sym].closed:
            return
        if sym in self._positions:
            del self._positions[sym]
        from small_paper.observer_entry_time import (
            market_time_age_sec,
            observer_entry_fields,
            resolve_market_entry_time,
            resolve_observer_entry_time,
        )
        from small_paper.realtime_board_exit_shadow import make_position_id

        now = datetime.now(JST)
        ent = resolve_observer_entry_time(trade, payload=payload, fallback_now=now)
        market_ent = resolve_market_entry_time(trade, payload=payload)
        ts_fields = observer_entry_fields(trade, payload=payload, fallback_now=now)
        accepted_evt = parse_kabu_time(
            ts_fields.get("accepted_event_time"), fallback=ent
        )
        mkt_age = market_time_age_sec(ent, market_ent)
        stale_trade = bool(ts_fields.get("stale_trade"))
        price_age: Optional[float] = None
        if trade.get("price_age_sec") is not None:
            try:
                price_age = float(trade["price_age_sec"])
            except (TypeError, ValueError):
                price_age = None
        trade_vh_ex = parse_kabu_time(trade.get("exit_time"), fallback=ent)
        if self.cfg.uses_combined_structural_exit():
            ex = self._session_end_datetime(ent)
        else:
            ex = trade_vh_ex
        stop = entry_price * (1.0 - self.cfg.hard_stop_pct / 100.0)
        take = entry_price * (1.0 + self.cfg.display_take_pct / 100.0)
        comps = continuation_components(trade)
        q = float(comps["continuation_quality"])

        position_id = make_position_id(sym, ent)
        self._positions[sym] = _VirtualPosition(
            symbol=sym,
            position_id=position_id,
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
            peak_pure_price_momentum=float(_as_float(trade.get("pure_price_momentum")) or 0.0),
            peak_favorable=float(comps["favorable_continuation"]),
            last_quality=q,
            last_hold_notify_mono=time.monotonic(),
            last_price=entry_price,
            mae_pnl_pct=0.0,
            trade_virtual_exit_time=trade_vh_ex,
            market_entry_time=market_ent,
            current_price_time=market_ent,
            accepted_event_time=accepted_evt,
            market_time_age_sec=mkt_age,
            price_age_sec_at_entry=price_age,
            stale_trade=stale_trade,
            session_id=self.session_id,
            session_kind=self.session_kind,
            entry_shadow={
                **{
                    k: trade.get(k)
                    for k in (
                    "extended_entry_shadow_flag",
                    "extended_entry_shadow_reasons",
                    "entry_rise_5min_pct",
                    "entry_rise_10min_pct",
                    "entry_vwap_dev_pct",
                    "entry_near_day_high_pct",
                    "entry_high_break_recent",
                    "entry_rolling_mfe_pct",
                    "entry_momentum_continuation_score",
                    "high_quality_low_momentum_shadow_flag",
                    "vwap_shadow_reject_candidate",
                    "vwap_shadow_reject_reason",
                    "entry_order_book_imbalance",
                    "entry_imbalance_percentile",
                    "imbalance_shadow_candidate",
                    "imbalance_shadow_tier",
                    "entry_expectancy_score",
                    "entry_expectancy_score_ge5_flag",
                    "entry_expectancy_score_ge6_flag",
                    "entry_expectancy_score_v2",
                    "entry_expectancy_score_v2_ge5_flag",
                    "entry_expectancy_score_v2_ge6_flag",
                    "limit_up_proximity_guard_shadow_blocked",
                    "limit_up_proximity_guard_shadow_reason",
                    "distance_to_limit_up_pct",
                    "day_high_near_limit",
                    "daily_limit_up_price",
                    "limit_up_proximity_prev_close_used",
                    "pullback_misread_dynamic40_guard_blocked",
                    "pullback_misread_guard_shadow_blocked",
                    "pbv2_rise5_shadow_block",
                    "pbv2_rise5_shadow_reason",
                    "pbv2_rise5_value",
                    "pbv2_rise5_threshold",
                    "pbv2_rise5_shadow_apply_pool",
                    "pbv2_flat_band_shadow_block",
                    "pbv2_flat_band_shadow_reason",
                    "pbv2_flat_band_rise5",
                    "pbv2_flat_band_rise10",
                    "pbv2_flat_band_variant",
                    "pbv2_flat_band_shadow_apply_pool",
                    "flat_band_and_rise5_shadow_block",
                    "day_high_distance_pct",
                    "entry_momentum_score",
                    "near_day_high_low_momentum_dynamic40_guard_blocked",
                    "universe_slot",
                    "universe_bucket",
                    "source_bucket",
                    "entry_type",
                    "or_reason",
                    "day_return_rank",
                    "minutes_from_open",
                    "or_o_r003_pass",
                    "live_feature_complete",
                    "bounce_from_recent_low",
                    "fall_from_recent_high",
                    "slope_5min",
                    "microsequence_ok",
                    "readiness_precision_shadow_candidate",
                    "readiness_precision_shadow_block",
                    "readiness_economics_shadow_candidate",
                    "readiness_economics_shadow_block",
                    "readiness_shadow_union_block",
                    "readiness_shadow_overlap_block",
                    "mfe_pre_entry_pct",
                    "mfe_pre_entry_source",
                    "mfe_pre_entry_window_sec",
                    "readiness_refined_h_shadow_candidate",
                    "readiness_refined_h_shadow_block",
                    "readiness_refined_h_shadow_research_only",
                    "microsequence_recovery_fail_shadow_candidate",
                    "microsequence_recovery_fail_shadow_block",
                    "microsequence_pre_entry_ok",
                    "microseq_bounce_from_recent_low",
                    "microseq_fall_from_recent_high",
                    "microseq_slope_5min",
                    "shadow_union_ihc_block",
                    "shadow_overlap_type",
                    "ihc_overlap_count",
                    "ihc_i_feature_source",
                    "ihc_h_feature_source",
                    "ihc_c_feature_source",
                    "ihc_union_feature_sources",
                    "readiness_bounce_from_recent_low_accept",
                    "readiness_bounce_from_recent_low",
                    "readiness_fall_from_recent_high",
                    "readiness_slope_5min",
                    "readiness_microsequence_ok",
                    "readiness_price_history_insufficient",
                    "readiness_same_symbol_entry_count_today",
                )
                if k in trade
            },
                **ts_fields,
            },
        )
        if self.board_exit_shadow is not None:
            self.board_exit_shadow.register_position(
                position_id=position_id,
                symbol=sym,
                entry_time=ent,
                entry_price=entry_price,
                payload=payload,
                entry_shadow=self._positions[sym].entry_shadow or {},
            )
        if self.exit_candidate_shadow is not None:
            self.exit_candidate_shadow.register_position(
                position_id=position_id,
                symbol=sym,
                entry_time=ent,
                entry_price=entry_price,
                payload=payload,
                entry_shadow=self._positions[sym].entry_shadow or {},
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
        if self._scope is not None and pos.session_id and pos.session_id != self.session_id:
            return []

        now = datetime.now(JST)
        price = _as_float(current_price) or _as_float(payload.get("CurrentPrice")) or pos.entry_price
        pos.last_price = float(price)
        pnl_pct = ((price - pos.entry_price) / pos.entry_price * 100.0) if pos.entry_price > 0 else 0.0
        pos.mae_pnl_pct = min(pos.mae_pnl_pct, pnl_pct)
        comps = continuation_components(trade)
        q = float(comps["continuation_quality"])
        mom = float(comps["momentum_continuation"])
        ppm = float(_as_float(trade.get("pure_price_momentum")) or _as_float(payload.get("pure_price_momentum")) or 0.0)
        pos.peak_quality = max(pos.peak_quality, q)
        pos.peak_pnl_pct = max(pos.peak_pnl_pct, pnl_pct)
        pos.peak_momentum = max(pos.peak_momentum, mom)
        pos.peak_pure_price_momentum = max(pos.peak_pure_price_momentum, ppm)
        pos.peak_favorable = max(pos.peak_favorable, float(comps["favorable_continuation"]))
        hold_sec = max(0.0, (now - pos.entry_time).total_seconds())

        tick = tick_from_candidate(trade, pos.entry_price, pos.peak_quality)
        tick["ts_epoch"] = now.timestamp()
        pos.rich_ticks.append(tick)

        if self.board_exit_shadow is not None:
            self.board_exit_shadow.record_holding_tick(
                symbol=symbol,
                position_id=pos.position_id,
                entry_time=pos.entry_time,
                payload=payload,
                current_price=float(price),
                entry_price=pos.entry_price,
                mfe_pct=float(pos.peak_pnl_pct),
                entry_shadow=pos.entry_shadow or {},
            )
        if self.exit_candidate_shadow is not None:
            self.exit_candidate_shadow.record_holding_tick(
                symbol=symbol,
                position_id=pos.position_id,
                entry_time=pos.entry_time,
                payload=payload,
                current_price=float(price),
                entry_price=pos.entry_price,
                mfe_pct=float(pos.peak_pnl_pct),
                entry_shadow=pos.entry_shadow or {},
            )

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
            "pure_price_momentum": round(ppm, 6),
            "peak_pure_price_momentum": round(pos.peak_pure_price_momentum, 6),
            "price_momentum_fade_ratio": self.cfg.price_momentum_fade_ratio,
            "timestamp": now.isoformat(timespec="seconds"),
            "structural_exit_policy": self.cfg.structural_exit_policy,
        }
        if self.cfg.structural_exit_policy == POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW:
            from research.structural_exit_policies import trailing_mfe_params

            peak = float(pos.peak_pnl_pct or 0.0)
            cur = float(pnl_pct or 0.0)
            imb_pct = _as_float((pos.entry_shadow or {}).get("entry_imbalance_percentile"))
            activate_pct, giveback_frac, board_tier = trailing_mfe_params(imb_pct)
            active = peak >= activate_pct
            capture = (cur / peak) if peak > 0 else 0.0
            base_ctx = {
                **base_ctx,
                "board_dynamic_trailing_tier": board_tier,
                "board_dynamic_trailing_activate_pct": activate_pct,
                "board_dynamic_trailing_giveback_frac": giveback_frac,
                "trailing_mfe_active": active,
                "trailing_mfe_threshold_reached": active,
                "trailing_mfe_peak_pnl": round(peak, 4),
                "trailing_mfe_current_pnl": round(cur, 4),
                "trailing_mfe_capture_ratio": round(capture, 4),
                "trailing_mfe_exit_triggered": False,
                "trailing_mfe_exit_reason": "",
                "trailing_mfe_hold_sec": round(hold_sec, 1),
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

            if price <= pos.stop_price:
                events.append(
                    self._close(pos, reason="stop_hit", exit_kind="stop_hit", ctx=base_ctx, structural=True)
                )
                return events

            if uses_take_exit_shadow(self.cfg.structural_exit_policy) and not pos.take_notified:
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
                        "take_as_exit": True,
                    }
                    events.append(
                        self._close(
                            pos,
                            reason=TAKE_EXIT_REASON,
                            exit_kind=TAKE_EXIT_REASON,
                            ctx=ctx,
                            structural=True,
                            take_was_not_exit=False,
                        )
                    )
                    return events

            if pos.fade_watch is not None:
                fw = process_fade_shadow_watch_tick(
                    pos.fade_watch,
                    entry_price=pos.entry_price,
                    price=float(price),
                    momentum=mom,
                    ts=now.timestamp(),
                    rich_ticks=pos.rich_ticks,
                    cfg=self.cfg,
                    policy=self.cfg.structural_exit_policy,
                )
                if fw:
                    reason, fw_log = fw
                    ctx = {**base_ctx, **fw_log, "fade_watch_exit_reason": reason}
                    self.stats.fade_watch_exit_count += 1
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
                self.stats.fade_watch_continue_count += 1
                ctx = {
                    **base_ctx,
                    **shadow_watch_log_fields(pos.fade_watch, self.cfg.structural_exit_policy),
                }
                events.append(
                    ObserverJudgmentEvent(kind=OBSERVER_HOLD, symbol=symbol, context=ctx)
                )
            else:
                if uses_fade_shadow_trigger(self.cfg.structural_exit_policy):
                    trigger = combined_exit_or_fade_shadow_trigger(
                        pos.rich_ticks,
                        pos.entry_price,
                        self.cfg,
                        take_reached=bool(pos.take_notified),
                    )
                elif uses_fade_watch_shadow(self.cfg.structural_exit_policy):
                    trigger = combined_exit_or_fade_watch_trigger(
                        pos.rich_ticks, pos.entry_price, self.cfg
                    )
                else:
                    sig_cfg = (
                        _take_exit_cfg_for_v1(self.cfg)
                        if uses_take_exit_shadow(self.cfg.structural_exit_policy)
                        else self.cfg
                    )
                    imb_pct = _as_float((pos.entry_shadow or {}).get("entry_imbalance_percentile"))
                    sig = combined_exit_signal_on_latest_tick(
                        pos.rich_ticks,
                        pos.entry_price,
                        sig_cfg,
                        entry_imbalance_percentile=imb_pct,
                        entry_ts_epoch=pos.entry_time.timestamp(),
                    )
                    trigger = ("exit", sig[0], sig[2], sig[1]) if sig else None
                if trigger:
                    kind, exit_pnl, close_px, reason = trigger
                    if kind == "fade_watch":
                        pos.fade_watch = enter_fade_shadow_state(
                            policy=self.cfg.structural_exit_policy,
                            entry_time=now.isoformat(timespec="seconds"),
                            entry_ts=now.timestamp(),
                            initial_reason=reason,
                            fade_price=float(close_px),
                            fade_momentum=mom,
                            mfe_at_fade=exit_pnl,
                            entry_price=pos.entry_price,
                            take_reached=bool(pos.take_notified),
                        )
                        self.stats.fade_watch_enter_count += 1
                        ctx = {
                            **base_ctx,
                            **shadow_watch_log_fields(pos.fade_watch, self.cfg.structural_exit_policy),
                            "fade_watch_initial_reason": reason,
                        }
                        events.append(
                            ObserverJudgmentEvent(kind=OBSERVER_HOLD, symbol=symbol, context=ctx)
                        )
                        return events
                    ctx = {**base_ctx, "unrealized_pnl_pct": round(exit_pnl, 4), "current_price": close_px}
                    if reason == "no_progress_exit":
                        from small_paper.no_progress_exit import (
                            PHASE442_POLICY_KEY,
                            required_mfe_threshold_pct,
                        )

                        req_mfe = required_mfe_threshold_pct(hold_sec)
                        ctx = {
                            **ctx,
                            "no_progress_exit_triggered": True,
                            "no_progress_exit_policy_key": PHASE442_POLICY_KEY,
                            "no_progress_required_mfe_pct": req_mfe,
                            "no_progress_hold_sec": round(hold_sec, 1),
                        }
                    if (
                        self.cfg.structural_exit_policy
                        == POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW
                        and reason == "trailing_mfe_exit"
                    ):
                        _, giveback_frac, board_tier = trailing_mfe_params(imb_pct)
                        activate_pct = float(
                            base_ctx.get("board_dynamic_trailing_activate_pct") or 0.0
                        )
                        ctx = {
                            **ctx,
                            "board_dynamic_trailing_tier": board_tier,
                            "board_dynamic_trailing_activate_pct": activate_pct,
                            "board_dynamic_trailing_giveback_frac": giveback_frac,
                            "trailing_mfe_exit_triggered": True,
                            "trailing_mfe_exit_reason": (
                                f"giveback_{int(giveback_frac * 100)}pct_after_mfe_"
                                f"{activate_pct}pct_{board_tier}"
                            ),
                        }
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
            exit_kind = reason if reason in ("morning_session_close", "afternoon_session_close") else "session_end"
            if uses_fade_watch_shadow(self.cfg.structural_exit_policy):
                reason = map_session_close_reason(reason)
                exit_kind = reason
            out.append(
                self._close(
                    pos,
                    reason=reason,
                    exit_kind=exit_kind,
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
        take_was_not_exit: bool = True,
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
                if reason == "morning_session_close":
                    self.stats.morning_session_close_count += 1
                elif reason == "afternoon_session_close":
                    self.stats.afternoon_session_close_count += 1
                elif reason == "session_end":
                    self.stats.session_end_exit_count += 1
        comps = ctx.get("components") or {}
        now = datetime.now(JST)
        full = {
            **dict(ctx),
            "entry_time": pos.entry_time.isoformat(timespec="seconds"),
            "observer_entry_time": pos.entry_time.isoformat(timespec="seconds"),
            "exit_time": now.isoformat(timespec="seconds"),
            "hold_sec": round(hold_sec, 1),
            "position_id": pos.position_id,
            "session_id": pos.session_id or self.session_id,
            "session_kind": pos.session_kind or self.session_kind,
            "exit_reason": reason,
            "structural_exit_reason": reason if structural else "",
            "exit_kind": exit_kind,
            "realized_pnl_pct": ctx.get("unrealized_pnl_pct", ctx.get("realized_pnl_pct", 0)),
            "max_favorable": ctx.get("peak_pnl_pct", pos.peak_pnl_pct),
            "max_adverse": ctx.get("mae_pct", pos.mae_pnl_pct),
            "peak_mfe_pct": round(pos.peak_pnl_pct, 4),
            "rolling_mfe_pct": round(pos.peak_pnl_pct, 4),
            "rolling_mae_pct": round(pos.mae_pnl_pct, 4),
            "continuation_breakdown": exit_kind in ("continuation_breakdown", "session_end"),
            "bearish_accumulation": comps.get("bearish_accumulation"),
            "is_structural_exit": structural and is_official_structural_exit_reason(reason),
            "structural_exit_policy": self.cfg.structural_exit_policy,
            "take_was_not_exit": take_was_not_exit,
            "stop_hit": reason == "stop_hit",
            "session_close": reason
            in ("morning_session_close", "afternoon_session_close", "session_end"),
            "overlap_replaced_review": reason == "overlap_replaced_review",
            "trailing_mfe_activated": bool(
                ctx.get("trailing_mfe_active")
                or ctx.get("trailing_mfe_threshold_reached")
                or ctx.get("trailing_mfe_exit_triggered")
            ),
            "trailing_mfe_exit": reason == "trailing_mfe_exit",
            "no_progress_exit": reason == "no_progress_exit",
        }
        if pos.market_entry_time is not None:
            full["market_entry_time"] = pos.market_entry_time.isoformat(timespec="seconds")
            full["current_price_time"] = pos.market_entry_time.isoformat(timespec="seconds")
        if pos.accepted_event_time is not None:
            full["accepted_event_time"] = pos.accepted_event_time.isoformat(timespec="seconds")
        if pos.market_time_age_sec is not None:
            full["market_time_age_sec"] = round(pos.market_time_age_sec, 1)
        if pos.price_age_sec_at_entry is not None:
            full["price_age_sec"] = round(pos.price_age_sec_at_entry, 1)
        if pos.stale_trade:
            full["stale_trade"] = True
        pnl_pct = float(full.get("realized_pnl_pct") or ctx.get("unrealized_pnl_pct") or 0.0)
        full["pnl_pct"] = round(pnl_pct, 4)
        if pos.entry_shadow:
            full["entry_type"] = pos.entry_shadow.get("entry_type", "PBV2")
            if pos.entry_shadow.get("or_reason"):
                full["or_reason"] = pos.entry_shadow.get("or_reason")
        if pos.entry_shadow:
            from small_paper.extended_entry_shadow import enrich_exit_shadow_fields
            from small_paper.vwap_shadow_reject import enrich_exit_vwap_shadow_fields

            exit_shadow = enrich_exit_shadow_fields(
                pos.entry_shadow,
                rich_ticks=pos.rich_ticks,
                entry_price=pos.entry_price,
                entry_ts=pos.entry_time.timestamp(),
            )
            full.update(exit_shadow)
            vwap_exit = enrich_exit_vwap_shadow_fields(
                pos.entry_shadow,
                pnl_pct=pnl_pct,
                exit_reason=reason,
            )
            full.update(vwap_exit)
            from small_paper.board_imbalance_shadow import enrich_exit_imbalance_shadow_fields

            imb_exit = enrich_exit_imbalance_shadow_fields(
                pos.entry_shadow,
                pnl_pct=pnl_pct,
                exit_reason=reason,
            )
            full.update(imb_exit)
            from small_paper.entry_expectancy_score_shadow import enrich_exit_entry_expectancy_fields

            score_exit = enrich_exit_entry_expectancy_fields(
                pos.entry_shadow,
                pnl_pct=pnl_pct,
                exit_reason=reason,
            )
            full.update(score_exit)
            from small_paper.limit_up_proximity_entry_guard_shadow import (
                enrich_exit_limit_up_proximity_shadow_fields,
            )

            actual_exit_price = float(
                ctx.get("current_price") or pos.last_price or pos.entry_price
            )
            limit_up_exit = enrich_exit_limit_up_proximity_shadow_fields(
                pos.entry_shadow,
                entry_price=pos.entry_price,
                exit_price=actual_exit_price,
                exit_reason=reason,
            )
            full.update(limit_up_exit)
            from small_paper.pullback_misread_entry_guard_shadow import (
                enrich_exit_pullback_misread_shadow_fields,
            )

            pb_exit = enrich_exit_pullback_misread_shadow_fields(
                pos.entry_shadow,
                entry_price=pos.entry_price,
                exit_price=actual_exit_price,
                exit_reason=reason,
            )
            full.update(pb_exit)
            from small_paper.pbv2_rise5_shadow import enrich_exit_pbv2_rise5_shadow_fields

            rise5_exit = enrich_exit_pbv2_rise5_shadow_fields(
                pos.entry_shadow,
                entry_price=pos.entry_price,
                exit_price=actual_exit_price,
                exit_reason=reason,
                peak_mfe_pct=_as_float(full.get("peak_mfe_pct")),
                peak_mae_pct=_as_float(full.get("rolling_mae_pct") or full.get("peak_mae_pct")),
            )
            full.update(rise5_exit)
            from small_paper.pbv2_flat_band_guard_shadow import enrich_exit_pbv2_flat_band_shadow_fields

            flat_exit = enrich_exit_pbv2_flat_band_shadow_fields(
                pos.entry_shadow,
                entry_price=pos.entry_price,
                exit_price=actual_exit_price,
                exit_reason=reason,
                peak_mfe_pct=_as_float(full.get("peak_mfe_pct")),
                peak_mae_pct=_as_float(full.get("rolling_mae_pct") or full.get("peak_mae_pct")),
            )
            full.update(flat_exit)
            from small_paper.flat_weak_range_forward_shadow import (
                enrich_exit_flat_weak_range_shadow_fields,
                flat_weak_range_shadow_enabled,
            )

            if flat_weak_range_shadow_enabled(self.cfg):
                fwr_exit = enrich_exit_flat_weak_range_shadow_fields(
                    pos.entry_shadow,
                    entry_price=pos.entry_price,
                    exit_price=actual_exit_price,
                    exit_reason=reason,
                )
                full.update(fwr_exit)
            from small_paper.readiness_forward_shadow import (
                enrich_exit_readiness_shadow_fields,
                readiness_shadow_any_enabled,
            )

            if readiness_shadow_any_enabled(self.cfg):
                hold_sec = (now - pos.entry_time).total_seconds() if pos.entry_time else None
                readiness_exit = enrich_exit_readiness_shadow_fields(
                    pos.entry_shadow,
                    entry_price=pos.entry_price,
                    exit_price=actual_exit_price,
                    exit_reason=reason,
                    hold_sec=hold_sec,
                )
                full.update(readiness_exit)
            from small_paper.microsequence_recovery_fail_forward_shadow import (
                enrich_exit_microsequence_recovery_fail_shadow_fields,
                microsequence_recovery_fail_shadow_enabled,
            )

            if microsequence_recovery_fail_shadow_enabled(self.cfg):
                hold_sec = (now - pos.entry_time).total_seconds() if pos.entry_time else None
                ms_c_exit = enrich_exit_microsequence_recovery_fail_shadow_fields(
                    pos.entry_shadow,
                    entry_price=pos.entry_price,
                    exit_price=actual_exit_price,
                    exit_reason=reason,
                    hold_sec=hold_sec,
                )
                full.update(ms_c_exit)
        if pos.rich_ticks:
            from small_paper.board_dynamic_trailing_shadow import (
                enrich_exit_board_dynamic_shadow_fields,
            )

            actual_exit_price = float(
                ctx.get("current_price") or pos.last_price or pos.entry_price
            )
            board_dynamic_shadow = enrich_exit_board_dynamic_shadow_fields(
                pos.entry_shadow or {},
                rich_ticks=pos.rich_ticks,
                entry_price=pos.entry_price,
                entry_ts=pos.entry_time.timestamp(),
                hard_stop_pct=self.cfg.hard_stop_pct,
                actual_exit_time=now.timestamp(),
                actual_exit_price=actual_exit_price,
                actual_pnl_pct=pnl_pct,
            )
            full.update(board_dynamic_shadow)
            from small_paper.exit_shadow_monitor import (
                ExitShadowMonitorConfig,
                enrich_exit_shadow_monitor_fields,
            )

            monitor_cfg = ExitShadowMonitorConfig(
                enabled=bool(getattr(self.cfg, "exit_shadow_monitor_enabled", False)),
                t2_enabled=bool(getattr(self.cfg, "exit_shadow_monitor_t2_enabled", True)),
                t3_enabled=bool(getattr(self.cfg, "exit_shadow_monitor_t3_enabled", True)),
            )
            exit_shadow = enrich_exit_shadow_monitor_fields(
                rich_ticks=pos.rich_ticks,
                entry_price=pos.entry_price,
                hard_stop_pct=self.cfg.hard_stop_pct,
                entry_imbalance_percentile=_as_float(
                    (pos.entry_shadow or {}).get("entry_imbalance_percentile")
                ),
                actual_exit_time=now.timestamp(),
                actual_exit_price=actual_exit_price,
                actual_pnl_pct=pnl_pct,
                monitor=monitor_cfg,
            )
            full.update(exit_shadow)
        from small_paper.post_entry_forward_shadow import enrich_exit_post_entry_shadow_fields

        post_entry_shadow = enrich_exit_post_entry_shadow_fields(
            rich_ticks=pos.rich_ticks,
            entry_price=pos.entry_price,
            entry_ts=pos.entry_time.timestamp(),
        )
        full.update(post_entry_shadow)
        if self.board_exit_shadow is not None:
            actual_exit_price = float(
                ctx.get("current_price") or pos.last_price or pos.entry_price
            )
            self.board_exit_shadow.finalize_position(
                position_id=pos.position_id,
                actual_exit_reason=reason,
                actual_exit_time=now,
                actual_exit_price=actual_exit_price,
                entry_price=pos.entry_price,
            )
        if self.exit_candidate_shadow is not None:
            actual_exit_price = float(
                ctx.get("current_price") or pos.last_price or pos.entry_price
            )
            self.exit_candidate_shadow.finalize_position(
                position_id=pos.position_id,
                actual_exit_reason=reason,
                actual_exit_time=now,
                actual_exit_price=actual_exit_price,
                entry_price=pos.entry_price,
            )
        return ObserverJudgmentEvent(kind=OBSERVER_EXIT, symbol=pos.symbol, context=full)
