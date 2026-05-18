"""
Phase 28: Real-market microstructure tracking during position (Logic Lab).

Fixed global thresholds — no per-symbol/day/time tuning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from research.momentum_early_move import EarlyMoveRuntime, _as_float, _pct_change, _vwap_distance_pct
from research.state_persistence_engine import StatePersistenceEngine
from research.bullish_continuation_engine import BullishContinuationEngine
from research.continuation_momentum_engine import ContinuationMomentumEngine
from research.duration_weighted_engine import DurationWeightedEngine
from research.state_transition_engine import StateTransitionEngine

# Global structural thresholds
SPREAD_EXPANSION_SEVERE = 1.40
IMB_COLLAPSE_DELTA = 0.05
IMB_QUEUE_WEAK = 0.44
VWAP_RECLAIM_FAIL = -0.04
ADV_PERSISTENCE_PCT = -0.06
FAV_PERSISTENCE_MIN = 0.04
FAKE_BREAKOUT_FAV_MAX = 0.03
NOISE_ADV_TOLERANCE = -0.08


@dataclass
class StructureCooldownState:
    """Per symbol-day re-entry guard after structural break."""

    structure_broken: bool = False
    last_exit_reason: str = ""
    reentry_blocked_count: int = 0

    def record_structure_exit(self, reason: str) -> None:
        if reason in (
            "structure_break_exit",
            "microstructure_noise_exit",
            "fake_breakout_exit",
            "recovery_early_cut",
            "recovery_or_cut_fail",
        ):
            self.structure_broken = True
            self.last_exit_reason = reason

    def try_release(self, *, vwap_dist: Optional[float], imbalance: Optional[float]) -> bool:
        if not self.structure_broken:
            return True
        if vwap_dist is not None and float(vwap_dist) > 0.05:
            if imbalance is None or float(imbalance) >= 0.48:
                self.structure_broken = False
                return True
        return False

    def block_entry(self) -> bool:
        if self.structure_broken:
            self.reentry_blocked_count += 1
            return True
        return False


@dataclass
class MicrostructureRuntime(EarlyMoveRuntime):
    """Early move + spread/imbalance microstructure path."""

    entry_spread_bps: Optional[float] = None
    spread_expansion_ratio: float = 1.0
    imbalance_collapse_streak: int = 0
    max_imbalance_collapse_streak: int = 0
    adverse_persistence_count: int = 0
    favorable_persistence_count: int = 0
    below_vwap_seen: bool = False
    vwap_reclaim_achieved: bool = False
    momentum_negative_streak: int = 0
    breakout_at_entry: bool = False
    fake_breakout_score: float = 0.0
    structure_break_score: float = 0.0
    v7_recovery_check_done: bool = False
    v7_recovery_hold_at_60: bool = False
    v7_judgment: str = ""
    v7_recovery_meta_60s: dict = field(default_factory=dict)
    v7_delayed_imb_suppressed: bool = False
    reclaim_persist_ticks: int = 0
    reclaim_failure_ticks: int = 0
    reclaim_first_sec: Optional[float] = None
    favorable_spike_seen: bool = False
    favorable_fade_ticks: int = 0
    imb_weak_sustained_ticks: int = 0
    post_recovery_low_fav: float = 999.0
    state_engine: StatePersistenceEngine = field(default_factory=StatePersistenceEngine)
    transition_engine: StateTransitionEngine = field(default_factory=StateTransitionEngine)
    duration_engine: DurationWeightedEngine = field(default_factory=DurationWeightedEngine)
    continuation_engine: BullishContinuationEngine = field(
        default_factory=BullishContinuationEngine
    )
    continuation_momentum_engine: ContinuationMomentumEngine = field(
        default_factory=ContinuationMomentumEngine
    )

    def update(
        self,
        *,
        ts_sec: float,
        price: float,
        board_imbalance: Optional[float],
        vwap: Optional[float],
        volume_delta_30s: Optional[float],
        minute_trading_value: Optional[float],
        spread_bps: Optional[float] = None,
    ) -> None:
        super().update(
            ts_sec=ts_sec,
            price=price,
            board_imbalance=board_imbalance,
            vwap=vwap,
            volume_delta_30s=volume_delta_30s,
            minute_trading_value=minute_trading_value,
        )
        if spread_bps is not None:
            if self.entry_spread_bps is None:
                self.entry_spread_bps = float(spread_bps)
            elif self.entry_spread_bps > 0:
                self.spread_expansion_ratio = max(
                    self.spread_expansion_ratio,
                    float(spread_bps) / self.entry_spread_bps,
                )

        fav = _pct_change(price, self.entry_price)
        mom = self.current_momentum_pct(price)
        if mom < 0:
            self.momentum_negative_streak += 1
            self.adverse_persistence_count += 1
        else:
            self.momentum_negative_streak = 0
        if mom >= FAV_PERSISTENCE_MIN:
            self.favorable_persistence_count += 1

        elapsed = ts_sec - self.entry_ts_sec
        vwap_dist = _vwap_distance_pct(price, vwap)
        if vwap_dist is not None and vwap_dist < VWAP_RECLAIM_FAIL:
            self.below_vwap_seen = True
        if self.below_vwap_seen and vwap_dist is not None and vwap_dist > 0.02:
            if not self.vwap_reclaim_achieved:
                self.vwap_reclaim_achieved = True
                self.reclaim_first_sec = elapsed
            self.reclaim_persist_ticks += 1
        elif self.vwap_reclaim_achieved and vwap_dist is not None:
            if vwap_dist < VWAP_RECLAIM_FAIL:
                self.reclaim_failure_ticks += 1
            elif vwap_dist > 0.02:
                self.reclaim_persist_ticks += 1

        if self.max_favorable_pct >= FAV_PERSISTENCE_MIN + 0.01:
            self.favorable_spike_seen = True
        if self.favorable_spike_seen and mom < FAV_PERSISTENCE_MIN * 0.5:
            self.favorable_fade_ticks += 1

        if self.recovered_after_adverse:
            self.post_recovery_low_fav = min(self.post_recovery_low_fav, fav)

        if self.entry_imbalance is not None and board_imbalance is not None:
            if float(board_imbalance) < float(self.entry_imbalance) - IMB_COLLAPSE_DELTA:
                self.imbalance_collapse_streak += 1
            else:
                self.imbalance_collapse_streak = 0
            self.max_imbalance_collapse_streak = max(
                self.max_imbalance_collapse_streak,
                self.imbalance_collapse_streak,
            )
            if float(board_imbalance) < float(self.entry_imbalance) - IMB_COLLAPSE_DELTA:
                self.imb_weak_sustained_ticks += 1

        self.state_engine.update_from_runtime(self, price=price, vwap_dist=vwap_dist)
        self.transition_engine.update_from_persistence(self.state_engine)
        self.duration_engine.update_from_runtime(self)
        self.continuation_engine.update_from_runtime(self)
        self.continuation_momentum_engine.update_from_runtime(self)

    def reclaim_persistent(self) -> bool:
        return self.reclaim_persist_ticks >= 5 and self.reclaim_failure_ticks <= 2

    def favorable_persistent(self) -> bool:
        return self.favorable_persistence_count >= 5 or self.max_favorable_pct >= 0.08

    def favorable_faded(self) -> bool:
        return (
            self.favorable_spike_seen
            and self.max_favorable_pct >= FAV_PERSISTENCE_MIN
            and self.favorable_fade_ticks >= 3
            and self.favorable_persistence_count < 3
        )

    def recovery_then_trend(self) -> bool:
        return (
            self.recovered_after_adverse
            and self.reclaim_persistent()
            and self.max_favorable_pct >= 0.06
        )

    def recovery_then_fail(self) -> bool:
        return (
            self.had_adverse_flush
            and self.recovered_after_adverse
            and not self.reclaim_persistent()
            and self.max_adverse_pct <= ADV_PERSISTENCE_PCT
        )

    @classmethod
    def from_entry_snap(
        cls,
        *,
        entry_price: float,
        entry_ts_sec: float,
        entry_snap: Mapping[str, Any],
    ) -> "MicrostructureRuntime":
        rt = cls(
            entry_price=entry_price,
            entry_ts_sec=entry_ts_sec,
            entry_imbalance=_as_float(entry_snap.get("board_imbalance_entry")),
            entry_vwap_dist_pct=_as_float(entry_snap.get("vwap_distance_pct")),
            entry_momentum_pct=_as_float(entry_snap.get("price_momentum_pct")),
            entry_minute_tv=_as_float(entry_snap.get("minute_trading_value")),
        )
        rt.entry_spread_bps = _as_float(entry_snap.get("spread_bps"))
        rt.breakout_at_entry = bool(entry_snap.get("breakout_event"))
        return rt

    def compute_scores(self) -> None:
        score = 0.0
        if self.breakout_at_entry:
            score += 0.25
        if self.below_vwap_seen and not self.vwap_reclaim_achieved:
            score += 0.30
        if self.max_favorable_pct < FAKE_BREAKOUT_FAV_MAX:
            score += 0.20
        if self.entry_imbalance is not None and self._last_imb is not None:
            if float(self._last_imb) < float(self.entry_imbalance) - IMB_COLLAPSE_DELTA:
                score += 0.15
        if self.spread_expansion_ratio >= SPREAD_EXPANSION_SEVERE:
            score += 0.10
        self.fake_breakout_score = min(1.0, score)

        sb = 0.0
        if self.below_vwap_seen and not self.vwap_reclaim_achieved:
            sb += 0.35
        if self.imbalance_collapse_streak >= 3:
            sb += 0.25
        if self.max_adverse_pct <= ADV_PERSISTENCE_PCT and self.max_favorable_pct < FAV_PERSISTENCE_MIN:
            sb += 0.25
        if self.momentum_negative_streak >= 4:
            sb += 0.15
        self.structure_break_score = min(1.0, sb)

    def finalize(self) -> dict[str, Any]:
        self.compute_scores()
        base = super().finalize()
        base.update(
            {
                "entry_spread_bps": self.entry_spread_bps,
                "spread_expansion_ratio": self.spread_expansion_ratio,
                "imbalance_collapse_max_streak": self.max_imbalance_collapse_streak,
                "adverse_persistence_count": self.adverse_persistence_count,
                "favorable_persistence_count": self.favorable_persistence_count,
                "vwap_reclaim_achieved": self.vwap_reclaim_achieved,
                "below_vwap_seen": self.below_vwap_seen,
                "fake_breakout_score": self.fake_breakout_score,
                "structure_break_score": self.structure_break_score,
                "breakout_at_entry": self.breakout_at_entry,
                "noise_reversal": self.had_adverse_flush and self.recovered_after_adverse,
                "v7_judgment": self.v7_judgment,
                "v7_recovery_hold_at_60": self.v7_recovery_hold_at_60,
                "v7_recovery_meta_60s": self.v7_recovery_meta_60s,
                "v7_delayed_imb_suppressed": self.v7_delayed_imb_suppressed,
                "adverse_cut_count": 1 if self.v7_judgment == "adverse_cut" else 0,
                "reclaim_persist_ticks": self.reclaim_persist_ticks,
                "reclaim_failure_ticks": self.reclaim_failure_ticks,
                "reclaim_persistent": self.reclaim_persistent(),
                "reclaim_failure_persistent": self.reclaim_failure_ticks >= 3
                and not self.reclaim_persistent(),
                "favorable_persistent": self.favorable_persistent(),
                "favorable_fade": self.favorable_faded(),
                "imbalance_persistence_ticks": self.imb_weak_sustained_ticks,
                "recovery_then_trend": self.recovery_then_trend(),
                "recovery_then_fail": self.recovery_then_fail(),
                **self.state_engine.finalize_dict(),
                **self.transition_engine.finalize_dict(),
                **self.duration_engine.finalize_dict(),
                **self.continuation_engine.finalize_dict(),
                **self.continuation_momentum_engine.finalize_dict(),
            }
        )
        return base
