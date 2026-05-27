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
REJECT_MAX_CONCURRENT = "max_concurrent"
REJECT_RISK_CLUSTER = "risk_cluster_block"
REJECT_DAILY_LOSS = "daily_loss_guard"
REJECT_WRONG_PROFILE = "wrong_profile"
REJECT_OUTSIDE_ALLOWED_TRADING_WINDOW = "outside_allowed_trading_window"
REJECT_SYMBOL_COOLDOWN = "symbol_cooloff"
REJECT_DAYTRADE_SUITABILITY = "daytrade_suitability"
REJECT_ENTRY_PRICE_RISK_GUARD = "entry_price_risk_guard"

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
    reject_below_quality: bool = True
    low_quality_log_only: bool = True
    order_enabled: bool = False
    discord_enabled: bool = False
    daily_loss_guard_pct: float = -2.5
    risk_cluster_consecutive_losses: int = 5
    min_above_median_quality: float = 0.42
    allowed_trading_windows: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ExposureGateConfig":
        cg = data.get("candidate_gates") or {}
        return cls(
            profile=str(data.get("profile", "momentum_volume_v13_combined")),
            min_continuation_quality=float(data.get("min_continuation_quality", 0.55)),
            max_concurrent_positions=int(data.get("max_concurrent_positions", 3)),
            reject_below_quality=bool(data.get("reject_below_quality", True)),
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
    ) -> None:
        self.config = config
        self.state = ExposureGateState()
        self._allowed_windows = allowed_windows
        self.symbol_cooloff = symbol_cooloff
        self.daytrade_suitability = daytrade_suitability
        self.entry_price_risk_guard = entry_price_risk_guard

    def evaluate_entry(self, trade: Mapping[str, Any]) -> GateDecision:
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

        if self.config.reject_below_quality and q < self.config.min_continuation_quality:
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
            )

        if day and self.state.day_pnl.get(day, 0.0) <= self.config.daily_loss_guard_pct:
            return GateDecision(
                accept=False,
                reason=REJECT_DAILY_LOSS,
                continuation_quality_score=q,
                quality_tier=tier,
            )

        ent = _parse_ts(str(trade.get("entry_time") or ""))
        ex = _parse_ts(str(trade.get("exit_time") or "")) or ent + 3600
        self.state.open_slots = [
            (a, b, sym) for a, b, sym in self.state.open_slots if b >= ent
        ]
        if len(self.state.open_slots) >= self.config.max_concurrent_positions:
            return GateDecision(
                accept=False,
                reason=REJECT_MAX_CONCURRENT,
                continuation_quality_score=q,
                quality_tier=tier,
            )

        return GateDecision(
            accept=True,
            reason="",
            continuation_quality_score=q,
            quality_tier=tier,
        )

    def record_accepted(self, trade: Mapping[str, Any]) -> None:
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
