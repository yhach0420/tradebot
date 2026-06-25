"""
Phase 39: Exposure / quality gate for small-paper simulation (no new EXIT logic).

Rejects low-quality entries and enforces concurrent position cap before live wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from research.continuation_quality_ranking import continuation_quality_score
from research.research_exit_criteria import _as_float

REJECT_LOW_QUALITY = "low_quality"
REJECT_ENTRY_SCORE_V2_BELOW = "entry_score_v2_below_threshold"
REJECT_MOMENTUM_LOW_REQUIRED = "momentum_low_required"
REJECT_MAX_CONCURRENT = "max_concurrent"
REJECT_RISK_CLUSTER = "risk_cluster_block"
REJECT_DAILY_LOSS = "daily_loss_guard"
REJECT_WRONG_PROFILE = "wrong_profile"
REJECT_OUTSIDE_ALLOWED_TRADING_WINDOW = "outside_allowed_trading_window"
REJECT_SYMBOL_COOLDOWN = "symbol_cooloff"
REJECT_DAYTRADE_SUITABILITY = "daytrade_suitability"
REJECT_ENTRY_PRICE_RISK_GUARD = "entry_price_risk_guard"
REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD = "pullback_misread_dynamic40_guard"
REJECT_HIGH_DRIFT_PULLBACK = "high_drift_pullback"
REJECT_WEAK_SHAPE = "weak_shape_reject"
REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD = (
    "near_day_high_low_momentum_dynamic40_guard"
)
REJECT_LATE_CHASE_GUARD = "late_chase_guard"
REJECT_CLASSIC_LATE_CHASE_RSI_OVER80 = "classic_late_chase_rsi_over80"
REJECT_REENTRY_RSI_GUARD_BELOW60 = "reentry_rsi_guard_below60"
REJECT_ENTRY_QUALITY_GUARD_SPREAD = "entry_quality_guard_spread"
REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT = "entry_quality_guard_update_count"
REJECT_ENTRY_CLUSTER_GUARD = "entry_cluster_guard"

QUALITY_TIER_TOP = "top_quartile"
QUALITY_TIER_ABOVE = "above_median"
QUALITY_TIER_BELOW = "below_median"


def _parse_ts(ts: str) -> float:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def quality_tier(score: float, *, min_top: float, min_above: float) -> str:
    if score >= min_top:
        return QUALITY_TIER_TOP
    if score >= min_above:
        return QUALITY_TIER_ABOVE
    return QUALITY_TIER_BELOW


@dataclass
class ExposureGateConfig:
    profile: str = "momentum_volume_v13_combined"
    min_continuation_quality: float = 0.55
    max_concurrent_positions: int = 3
    position_cap_mode: bool = False
    reject_below_quality: bool = True
    low_quality_log_only: bool = True
    order_enabled: bool = False
    discord_enabled: bool = False
    daily_loss_guard_pct: float = -2.5
    risk_cluster_consecutive_losses: int = 5
    min_above_median_quality: float = 0.42
    allowed_trading_windows: tuple[tuple[str, str], ...] = ()
    entry_score_v2_min: int = 0
    momentum_score_cutoff_max: float = 0.2546

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ExposureGateConfig":
        cg = data.get("candidate_gates") or {}
        return cls(
            profile=str(data.get("profile", "momentum_volume_v13_combined")),
            min_continuation_quality=float(data.get("min_continuation_quality", 0.55)),
            max_concurrent_positions=int(data.get("max_concurrent_positions", 3)),
            position_cap_mode=bool(data.get("position_cap_mode", False)),
            reject_below_quality=bool(data.get("reject_below_quality", True)),
            entry_score_v2_min=int(data.get("entry_score_v2_min", 0) or 0),
            momentum_score_cutoff_max=float(data.get("momentum_score_cutoff_max", 0.2546)),
            low_quality_log_only=bool(data.get("low_quality_log_only", True)),
            order_enabled=bool(data.get("order_enabled", False)),
            discord_enabled=bool(data.get("discord_enabled", False)),
            daily_loss_guard_pct=float(data.get("daily_loss_guard_pct", -2.5)),
            risk_cluster_consecutive_losses=int(
                data.get("risk_cluster_consecutive_losses", 5)
            ),
            min_above_median_quality=float(data.get("min_above_median_quality", 0.42)),
        )


@dataclass
class ExposureGateState:
    open_slots: list[tuple[float, float, str]] = field(default_factory=list)
    day_pnl: dict[str, float] = field(default_factory=dict)
    consecutive_losses: int = 0
    risk_cluster_blocked: bool = False


@dataclass
class GateDecision:
    accept: bool
    reason: str = ""
    continuation_quality_score: float = 0.0
    quality_tier: str = ""
    symbol_cooloff_reason: str = ""
    prior_avg_pnl: Optional[float] = None
    prior_trades: int = 0
    daytrade_suitability_score: Optional[float] = None
    daytrade_suitability_threshold: Optional[float] = None
    atr_pct: Optional[float] = None
    intraday_range_pct: Optional[float] = None
    trading_value: Optional[float] = None
    turnover_proxy: Optional[float] = None
    entry_price_risk_guard_tick_size: Optional[float] = None
    entry_price_risk_guard_tick_ratio_pct: Optional[float] = None
    entry_price_risk_guard_trigger: str = ""
    entry_price_risk_guard_price_source: str = ""
    entry_price_risk_guard_price: Optional[float] = None
    entry_price_risk_guard_shadow_missing_price_bypassed: bool = False
    entry_price_risk_guard_universe_close_price_used: bool = False
    pullback_misread_dynamic40_entry_rise_5min_pct: Optional[float] = None
    pullback_misread_dynamic40_entry_vwap_dev_pct: Optional[float] = None
    pullback_misread_dynamic40_universe_slot: str = ""
    pullback_misread_dynamic40_universe_bucket: str = ""
    high_drift_pullback_entry_rise_5min_pct: Optional[float] = None
    high_drift_pullback_entry_rise_10min_pct: Optional[float] = None
    high_drift_pullback_entry_rise_15min_pct: Optional[float] = None
    high_drift_pullback_day_high_distance_pct: Optional[float] = None
    high_drift_pullback_universe_slot: str = ""
    high_drift_pullback_universe_bucket: str = ""
    weak_shape_class: str = ""
    weak_shape_day_high_minutes_from_open: Optional[float] = None
    weak_shape_minutes_since_day_high_update: Optional[float] = None
    weak_shape_day_high_distance_pct: Optional[float] = None
    near_day_high_low_momentum_dynamic40_day_high_distance_pct: Optional[float] = None
    near_day_high_low_momentum_dynamic40_entry_momentum_score: Optional[float] = None
    near_day_high_low_momentum_dynamic40_universe_slot: str = ""
    near_day_high_low_momentum_dynamic40_universe_bucket: str = ""
    late_chase_entry_rise_10min_pct: Optional[float] = None
    late_chase_day_high_distance_pct: Optional[float] = None
    classic_late_chase_rsi_rsi14: Optional[float] = None
    classic_late_chase_rsi_late_chase_flag: bool = False
    reentry_rsi_rsi14: Optional[float] = None
    reentry_rsi_after_stop: bool = False
    entry_quality_spread_bps: Optional[float] = None
    entry_quality_update_count: Optional[int] = None
    cluster_guard_status: str = ""
    cluster_id: int = -1
    new_subcluster_id: int = -1
    liquidity_burst: Optional[float] = None
    entry_cluster_guard_via_exception: bool = False
    entry_expectancy_score_v2: Optional[int] = None
    entry_score_v2_threshold: Optional[int] = None
    entry_score_v2_gate_pass: Optional[bool] = None


def _entry_score_v2_fields(trade: Mapping[str, Any]) -> dict[str, Any]:
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    return compute_entry_expectancy_score_fields(trade=trade)


def _entry_score_v2_int(trade: Mapping[str, Any]) -> Optional[int]:
    raw = trade.get("entry_expectancy_score_v2")
    if raw is None or raw == "":
        fields = _entry_score_v2_fields(trade)
        raw = fields.get("entry_expectancy_score_v2")
    try:
        if raw is None or raw == "":
            return None
        return int(raw)
    except (TypeError, ValueError):
        return None


class ExposureGate:
    """Chronological entry gate for historical trade replay."""

    def __init__(
        self,
        config: ExposureGateConfig,
        *,
        allowed_windows: Optional[Sequence[Any]] = None,
        symbol_cooloff: Optional[Any] = None,
        daytrade_suitability: Optional[Any] = None,
        entry_price_risk_guard: Optional[Any] = None,
        pullback_misread_dynamic40_guard: Optional[Any] = None,
        high_drift_pullback_guard: Optional[Any] = None,
        weak_shape_reject_guard: Optional[Any] = None,
        near_day_high_low_momentum_dynamic40_guard: Optional[Any] = None,
        late_chase_guard: Optional[Any] = None,
        classic_late_chase_rsi_guard: Optional[Any] = None,
        reentry_rsi_guard: Optional[Any] = None,
        entry_quality_guard: Optional[Any] = None,
        entry_cluster_guard: Optional[Any] = None,
    ) -> None:
        self.config = config
        self.state = ExposureGateState()
        self._allowed_windows = allowed_windows
        self.symbol_cooloff = symbol_cooloff
        self.daytrade_suitability = daytrade_suitability
        self.entry_price_risk_guard = entry_price_risk_guard
        self.pullback_misread_dynamic40_guard = pullback_misread_dynamic40_guard
        self.high_drift_pullback_guard = high_drift_pullback_guard
        self.weak_shape_reject_guard = weak_shape_reject_guard
        self.near_day_high_low_momentum_dynamic40_guard = (
            near_day_high_low_momentum_dynamic40_guard
        )
        self.late_chase_guard = late_chase_guard
        self.classic_late_chase_rsi_guard = classic_late_chase_rsi_guard
        self.reentry_rsi_guard = reentry_rsi_guard
        self.entry_quality_guard = entry_quality_guard
        self.entry_cluster_guard = entry_cluster_guard

    def evaluate_entry(
        self,
        trade: Mapping[str, Any],
        *,
        observer_open_count: Optional[int] = None,
        observer_symbol_open: bool = False,
        max_concurrent_positions: Optional[int] = None,
    ) -> GateDecision:
        profile = str(trade.get("profile", ""))
        if profile != self.config.profile:
            return GateDecision(
                accept=False,
                reason=REJECT_WRONG_PROFILE,
                continuation_quality_score=0.0,
                quality_tier="",
            )

        if self._allowed_windows is not None:
            from small_paper.allowed_trading_windows import (
                is_in_allowed_trading_window,
            )

            if not is_in_allowed_trading_window(
                str(trade.get("entry_time") or ""),
                self._allowed_windows,
            ):
                q_pre = continuation_quality_score(trade)
                return GateDecision(
                    accept=False,
                    reason=REJECT_OUTSIDE_ALLOWED_TRADING_WINDOW,
                    continuation_quality_score=q_pre,
                    quality_tier=quality_tier(
                        q_pre,
                        min_top=self.config.min_continuation_quality,
                        min_above=self.config.min_above_median_quality,
                    ),
                )

        if self.symbol_cooloff is not None:
            sym = str(trade.get("symbol") or "")
            chk = self.symbol_cooloff.check(sym)
            if chk.blocked:
                q_pre = continuation_quality_score(trade)
                return GateDecision(
                    accept=False,
                    reason=REJECT_SYMBOL_COOLDOWN,
                    continuation_quality_score=q_pre,
                    quality_tier=quality_tier(
                        q_pre,
                        min_top=self.config.min_continuation_quality,
                        min_above=self.config.min_above_median_quality,
                    ),
                    symbol_cooloff_reason=chk.reason or REJECT_SYMBOL_COOLDOWN,
                    prior_avg_pnl=chk.prior_avg_pnl,
                    prior_trades=chk.prior_trades,
                )

        if self.entry_price_risk_guard is not None:
            gr = self.entry_price_risk_guard.check(trade)
            if gr.blocked:
                self.entry_price_risk_guard.reject_count += 1
                q_pre = continuation_quality_score(trade)
                return GateDecision(
                    accept=False,
                    reason=REJECT_ENTRY_PRICE_RISK_GUARD,
                    continuation_quality_score=q_pre,
                    quality_tier=quality_tier(
                        q_pre,
                        min_top=self.config.min_continuation_quality,
                        min_above=self.config.min_above_median_quality,
                    ),
                    entry_price_risk_guard_tick_size=gr.tick_size_yen,
                    entry_price_risk_guard_tick_ratio_pct=gr.tick_ratio_pct,
                    entry_price_risk_guard_trigger=gr.trigger,
                    entry_price_risk_guard_price_source=getattr(gr, "price_source", "") or "",
                    entry_price_risk_guard_price=getattr(gr, "current_price", None),
                    entry_price_risk_guard_shadow_missing_price_bypassed=bool(
                        getattr(gr, "shadow_missing_price_bypassed", False)
                    ),
                    entry_price_risk_guard_universe_close_price_used=bool(
                        getattr(gr, "universe_close_price_used", False)
                    ),
                )

        if self.pullback_misread_dynamic40_guard is not None:
            pb = self.pullback_misread_dynamic40_guard.check(trade)
            if pb.blocked:
                self.pullback_misread_dynamic40_guard.reject_count += 1
                sym = str(trade.get("symbol") or "")
                if sym:
                    self.pullback_misread_dynamic40_guard.rejected_symbols.add(sym)
                q_pre = continuation_quality_score(trade)
                return GateDecision(
                    accept=False,
                    reason=REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD,
                    continuation_quality_score=q_pre,
                    quality_tier=quality_tier(
                        q_pre,
                        min_top=self.config.min_continuation_quality,
                        min_above=self.config.min_above_median_quality,
                    ),
                    pullback_misread_dynamic40_entry_rise_5min_pct=pb.entry_rise_5min_pct,
                    pullback_misread_dynamic40_entry_vwap_dev_pct=pb.entry_vwap_dev_pct,
                    pullback_misread_dynamic40_universe_slot=pb.universe_slot,
                    pullback_misread_dynamic40_universe_bucket=pb.universe_bucket,
                )

        if self.high_drift_pullback_guard is not None:
            hd = self.high_drift_pullback_guard.check(trade)
            if hd.blocked:
                self.high_drift_pullback_guard.reject_count += 1
                sym = str(trade.get("symbol") or "")
                if sym:
                    self.high_drift_pullback_guard.rejected_symbols.add(sym)
                q_pre = continuation_quality_score(trade)
                return GateDecision(
                    accept=False,
                    reason=REJECT_HIGH_DRIFT_PULLBACK,
                    continuation_quality_score=q_pre,
                    quality_tier=quality_tier(
                        q_pre,
                        min_top=self.config.min_continuation_quality,
                        min_above=self.config.min_above_median_quality,
                    ),
                    high_drift_pullback_entry_rise_5min_pct=hd.entry_rise_5min_pct,
                    high_drift_pullback_entry_rise_10min_pct=hd.entry_rise_10min_pct,
                    high_drift_pullback_entry_rise_15min_pct=hd.entry_rise_15min_pct,
                    high_drift_pullback_day_high_distance_pct=hd.day_high_distance_pct,
                    high_drift_pullback_universe_slot=hd.universe_slot,
                    high_drift_pullback_universe_bucket=hd.universe_bucket,
                )

        if self.weak_shape_reject_guard is not None:
            ws = self.weak_shape_reject_guard.check(trade)
            if ws.blocked:
                self.weak_shape_reject_guard.reject_count += 1
                sym = str(trade.get("symbol") or "")
                if sym:
                    self.weak_shape_reject_guard.rejected_symbols.add(sym)
                q_pre = continuation_quality_score(trade)
                return GateDecision(
                    accept=False,
                    reason=REJECT_WEAK_SHAPE,
                    continuation_quality_score=q_pre,
                    quality_tier=quality_tier(
                        q_pre,
                        min_top=self.config.min_continuation_quality,
                        min_above=self.config.min_above_median_quality,
                    ),
                    weak_shape_class=ws.shape_class,
                    weak_shape_day_high_minutes_from_open=ws.day_high_minutes_from_open,
                    weak_shape_minutes_since_day_high_update=ws.minutes_since_day_high_update,
                    weak_shape_day_high_distance_pct=ws.day_high_distance_pct,
                )

        if self.near_day_high_low_momentum_dynamic40_guard is not None:
            nd = self.near_day_high_low_momentum_dynamic40_guard.check(trade)
            if nd.blocked:
                self.near_day_high_low_momentum_dynamic40_guard.reject_count += 1
                sym = str(trade.get("symbol") or "")
                if sym:
                    self.near_day_high_low_momentum_dynamic40_guard.rejected_symbols.add(
                        sym
                    )
                q_pre = continuation_quality_score(trade)
                return GateDecision(
                    accept=False,
                    reason=REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD,
                    continuation_quality_score=q_pre,
                    quality_tier=quality_tier(
                        q_pre,
                        min_top=self.config.min_continuation_quality,
                        min_above=self.config.min_above_median_quality,
                    ),
                    near_day_high_low_momentum_dynamic40_day_high_distance_pct=nd.day_high_distance_pct,
                    near_day_high_low_momentum_dynamic40_entry_momentum_score=nd.entry_momentum_score,
                    near_day_high_low_momentum_dynamic40_universe_slot=nd.universe_slot,
                    near_day_high_low_momentum_dynamic40_universe_bucket=nd.universe_bucket,
                )

        if self.daytrade_suitability is not None:
            ds = self.daytrade_suitability.check(trade)
            if ds.blocked:
                q_pre = continuation_quality_score(trade)
                return GateDecision(
                    accept=False,
                    reason=REJECT_DAYTRADE_SUITABILITY,
                    continuation_quality_score=q_pre,
                    quality_tier=quality_tier(
                        q_pre,
                        min_top=self.config.min_continuation_quality,
                        min_above=self.config.min_above_median_quality,
                    ),
                    daytrade_suitability_score=ds.score,
                    daytrade_suitability_threshold=ds.threshold,
                    atr_pct=ds.atr_pct,
                    intraday_range_pct=ds.intraday_range_pct,
                    trading_value=ds.trading_value,
                    turnover_proxy=ds.turnover_proxy,
                )

        q = continuation_quality_score(trade)
        tier = quality_tier(
            q,
            min_top=self.config.min_continuation_quality,
            min_above=self.config.min_above_median_quality,
        )
        day = str(trade.get("trade_date", ""))[:10]

        v2_threshold = int(self.config.entry_score_v2_min or 0)
        v2_score = _entry_score_v2_int(trade)
        v2_pass = v2_score is not None and v2_score >= v2_threshold
        v2_ctx = {
            "entry_expectancy_score_v2": v2_score,
            "entry_score_v2_threshold": v2_threshold if v2_threshold > 0 else None,
            "entry_score_v2_gate_pass": v2_pass if v2_threshold > 0 else None,
        }

        if v2_threshold > 0:
            from small_paper.entry_expectancy_score_shadow import (
                board_mid_or_high_required_for_v2,
                momentum_score_cutoff_pass,
            )

            if not momentum_score_cutoff_pass(
                trade, cutoff=self.config.momentum_score_cutoff_max
            ):
                return GateDecision(
                    accept=False,
                    reason=REJECT_MOMENTUM_LOW_REQUIRED,
                    continuation_quality_score=q,
                    quality_tier=tier,
                    **v2_ctx,
                )
            if not board_mid_or_high_required_for_v2(trade):
                return GateDecision(
                    accept=False,
                    reason=REJECT_ENTRY_SCORE_V2_BELOW,
                    continuation_quality_score=q,
                    quality_tier=tier,
                    **v2_ctx,
                )
            if not v2_pass:
                return GateDecision(
                    accept=False,
                    reason=REJECT_ENTRY_SCORE_V2_BELOW,
                    continuation_quality_score=q,
                    quality_tier=tier,
                    **v2_ctx,
                )

            if self.late_chase_guard is not None:
                lc = self.late_chase_guard.check(trade)
                if lc.blocked:
                    self.late_chase_guard.reject_count += 1
                    sym = str(trade.get("symbol") or "")
                    if sym:
                        self.late_chase_guard.rejected_symbols.add(sym)
                    return GateDecision(
                        accept=False,
                        reason=REJECT_LATE_CHASE_GUARD,
                        continuation_quality_score=q,
                        quality_tier=tier,
                        late_chase_entry_rise_10min_pct=lc.entry_rise_10min_pct,
                        late_chase_day_high_distance_pct=lc.day_high_distance_pct,
                        **v2_ctx,
                    )

            if self.classic_late_chase_rsi_guard is not None:
                cr = self.classic_late_chase_rsi_guard.check(trade)
                if cr.blocked:
                    self.classic_late_chase_rsi_guard.reject_count += 1
                    sym = str(trade.get("symbol") or "")
                    if sym:
                        self.classic_late_chase_rsi_guard.rejected_symbols.add(sym)
                    return GateDecision(
                        accept=False,
                        reason=REJECT_CLASSIC_LATE_CHASE_RSI_OVER80,
                        continuation_quality_score=q,
                        quality_tier=tier,
                        classic_late_chase_rsi_rsi14=cr.rsi14,
                        classic_late_chase_rsi_late_chase_flag=cr.late_chase_flag,
                        **v2_ctx,
                    )

            if self.reentry_rsi_guard is not None:
                rr = self.reentry_rsi_guard.check(trade)
                if rr.blocked:
                    self.reentry_rsi_guard.reject_count += 1
                    sym = str(trade.get("symbol") or "")
                    if sym:
                        self.reentry_rsi_guard.rejected_symbols.add(sym)
                    return GateDecision(
                        accept=False,
                        reason=REJECT_REENTRY_RSI_GUARD_BELOW60,
                        continuation_quality_score=q,
                        quality_tier=tier,
                        reentry_rsi_rsi14=rr.rsi14,
                        reentry_rsi_after_stop=rr.is_reentry_after_stop,
                        **v2_ctx,
                    )

            if self.entry_quality_guard is not None:
                eq = self.entry_quality_guard.check(trade)
                if eq.blocked:
                    self.entry_quality_guard.reject_count += 1
                    sym = str(trade.get("symbol") or "")
                    if sym:
                        self.entry_quality_guard.rejected_symbols.add(sym)
                    if eq.reject_reason == REJECT_ENTRY_QUALITY_GUARD_SPREAD:
                        self.entry_quality_guard.spread_reject_count += 1
                    elif eq.reject_reason == REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT:
                        self.entry_quality_guard.update_reject_count += 1
                    return GateDecision(
                        accept=False,
                        reason=eq.reject_reason,
                        continuation_quality_score=q,
                        quality_tier=tier,
                        entry_quality_spread_bps=eq.spread_bps,
                        entry_quality_update_count=eq.update_count_before_entry,
                        **v2_ctx,
                    )

            if self.entry_cluster_guard is not None:
                cg = self.entry_cluster_guard.check(trade)
                if cg.blocked:
                    self.entry_cluster_guard.record_reject(trade, cg)
                    return GateDecision(
                        accept=False,
                        reason=REJECT_ENTRY_CLUSTER_GUARD,
                        continuation_quality_score=q,
                        quality_tier=tier,
                        cluster_guard_status=cg.cluster_guard_status,
                        cluster_id=cg.cluster_id,
                        new_subcluster_id=cg.new_subcluster_id,
                        liquidity_burst=cg.liquidity_burst,
                        entry_cluster_guard_via_exception=False,
                        **v2_ctx,
                    )
                trade_dict = dict(trade) if not isinstance(trade, dict) else trade
                self.entry_cluster_guard.record_accept(trade_dict, cg)
                v2_ctx = {
                    **v2_ctx,
                    "cluster_guard_status": cg.cluster_guard_status,
                    "cluster_id": cg.cluster_id,
                    "new_subcluster_id": cg.new_subcluster_id,
                    "liquidity_burst": cg.liquidity_burst,
                    "entry_cluster_guard_via_exception": cg.via_exception,
                }
        elif self.config.reject_below_quality and q < self.config.min_continuation_quality:
            return GateDecision(
                accept=False,
                reason=REJECT_LOW_QUALITY,
                continuation_quality_score=q,
                quality_tier=tier,
            )

        if self.state.risk_cluster_blocked:
            return GateDecision(
                accept=False,
                reason=REJECT_RISK_CLUSTER,
                continuation_quality_score=q,
                quality_tier=tier,
                **v2_ctx,
            )

        if day and self.state.day_pnl.get(day, 0.0) <= self.config.daily_loss_guard_pct:
            return GateDecision(
                accept=False,
                reason=REJECT_DAILY_LOSS,
                continuation_quality_score=q,
                quality_tier=tier,
                **v2_ctx,
            )

        if self.config.position_cap_mode and observer_open_count is not None:
            cap = (
                int(max_concurrent_positions)
                if max_concurrent_positions is not None
                else self.config.max_concurrent_positions
            )
            if (
                not observer_symbol_open
                and observer_open_count >= cap
            ):
                return GateDecision(
                    accept=False,
                    reason=REJECT_MAX_CONCURRENT,
                    continuation_quality_score=q,
                    quality_tier=tier,
                    **v2_ctx,
                )
        else:
            ent = _parse_ts(str(trade.get("entry_time") or ""))
            ex = _parse_ts(str(trade.get("exit_time") or "")) or ent + 3600
            self.state.open_slots = [
                (a, b, sym) for a, b, sym in self.state.open_slots if b >= ent
            ]
            if len(self.state.open_slots) >= (
                int(max_concurrent_positions)
                if max_concurrent_positions is not None
                else self.config.max_concurrent_positions
            ):
                return GateDecision(
                    accept=False,
                    reason=REJECT_MAX_CONCURRENT,
                    continuation_quality_score=q,
                    quality_tier=tier,
                    **v2_ctx,
                )

        return GateDecision(
            accept=True,
            reason="",
            continuation_quality_score=q,
            quality_tier=tier,
            **v2_ctx,
        )

    def record_accepted(self, trade: Mapping[str, Any]) -> None:
        if not self.config.position_cap_mode:
            ent = _parse_ts(str(trade.get("entry_time") or ""))
            ex = _parse_ts(str(trade.get("exit_time") or "")) or ent + 3600
            sym = str(trade.get("symbol", ""))
            self.state.open_slots.append((ent, ex, sym))

        pnl = _as_float(trade.get("pnl_pct")) or 0.0
        day = str(trade.get("trade_date", ""))[:10]
        if day:
            self.state.day_pnl[day] = self.state.day_pnl.get(day, 0.0) + pnl

        if pnl < 0:
            self.state.consecutive_losses += 1
            if self.state.consecutive_losses >= self.config.risk_cluster_consecutive_losses:
                self.state.risk_cluster_blocked = True
        else:
            self.state.consecutive_losses = 0
            self.state.risk_cluster_blocked = False


def run_exposure_gate_simulation(
    trades: Sequence[Mapping[str, Any]],
    config: ExposureGateConfig,
    *,
    allowed_windows: Optional[Sequence[Any]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Process trades in entry-time order; return (accepted, rejects)."""
    gate = ExposureGate(config, allowed_windows=allowed_windows)
    focus = [
        t
        for t in trades
        if str(t.get("profile")) == config.profile
    ]
    ordered = sorted(focus, key=lambda t: _parse_ts(str(t.get("entry_time") or "")))

    accepted: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []

    for t in ordered:
        row = dict(t)
        decision = gate.evaluate_entry(t)
        row["continuation_quality_score"] = round(decision.continuation_quality_score, 4)
        row["quality_tier"] = decision.quality_tier
        row["gate_accept"] = decision.accept
        row["gate_reject_reason"] = decision.reason

        if decision.accept:
            gate.record_accepted(t)
            accepted.append(row)
        else:
            rejects.append(row)

    return accepted, rejects
