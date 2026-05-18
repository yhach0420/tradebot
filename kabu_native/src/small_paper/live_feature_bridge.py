"""
Phase 47: Rolling live features from kabu PUSH for continuation_quality (no new gate formula).
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from research.continuation_quality_ranking import continuation_components
from research.research_exit_criteria import _as_float


def _price(payload: Mapping[str, Any]) -> Optional[float]:
    p = _as_float(payload.get("CurrentPrice"))
    if p is not None and p > 0:
        return float(p)
    return None


def _vwap(payload: Mapping[str, Any]) -> Optional[float]:
    v = _as_float(payload.get("VWAP"))
    if v is not None and v > 0:
        return float(v)
    return None


@dataclass(frozen=True)
class LiveFeatureBridgeConfig:
    window_ticks: int = 120
    favorable_lookback: int = 8
    min_ticks_for_complete: int = 3
    tracking_reset_sec: float = 300.0
    momentum_lookback: int = 5


@dataclass
class SymbolTickState:
    ref_price: float = 0.0
    window_start_mono: float = 0.0
    running_max: float = 0.0
    running_min: float = 0.0
    peak_mae_pct: float = 0.0
    favorable_streak: int = 0
    max_favorable_streak: int = 0
    ticks: deque[tuple[float, float, Optional[float]]] = field(default_factory=deque)
    last_mae_pct: float = 0.0


@dataclass
class LiveFeatureSnapshot:
    symbol: str
    rolling_mfe_pct: float = 0.0
    rolling_mae_pct: float = 0.0
    favorable_continuation: float = 0.0
    momentum_continuation_score: float = 0.0
    max_continuation_duration: int = 0
    adverse_shrinking: float = 0.0
    live_feature_complete: bool = False
    quality_fallback_path: bool = True
    reason_if_incomplete: str = ""
    quality_debug: dict[str, Any] = field(default_factory=dict)

    def to_payload_fields(self) -> dict[str, Any]:
        return {
            "rolling_mfe_pct": self.rolling_mfe_pct,
            "rolling_mae_pct": self.rolling_mae_pct,
            "max_favorable_excursion_pct": self.rolling_mfe_pct,
            "max_adverse_excursion_pct": self.rolling_mae_pct,
            "favorable_continuation": self.favorable_continuation,
            "momentum_continuation_score": self.momentum_continuation_score,
            "max_continuation_duration": self.max_continuation_duration,
            "bullish_continuation_score": min(1.0, max(0.0, self.rolling_mfe_pct / 0.25))
            if self.rolling_mfe_pct > 0
            else None,
            "bearish_accumulation_score": max(0.0, 1.0 - self.adverse_shrinking)
            if self.live_feature_complete
            else None,
            "adverse_shrinking": self.adverse_shrinking,
            "live_feature_complete": self.live_feature_complete,
            "quality_fallback_path": self.quality_fallback_path,
            "quality_debug": self.quality_debug,
        }


class LiveFeatureBridge:
    """Per-symbol rolling state; approximates replay MFE/MAE/duration on live PUSH."""

    def __init__(self, config: Optional[LiveFeatureBridgeConfig] = None) -> None:
        self.config = config or LiveFeatureBridgeConfig()
        self._symbols: dict[str, SymbolTickState] = {}

    def update(self, symbol: str, payload: Mapping[str, Any]) -> LiveFeatureSnapshot:
        price = _price(payload)
        if price is None:
            return LiveFeatureSnapshot(
                symbol=symbol,
                live_feature_complete=False,
                quality_fallback_path=True,
                reason_if_incomplete="missing_CurrentPrice",
                quality_debug={"reason_if_incomplete": "missing_CurrentPrice"},
            )

        cfg = self.config
        now_m = time.monotonic()
        st = self._symbols.get(symbol)
        if st is None or (now_m - st.window_start_mono) > cfg.tracking_reset_sec:
            st = SymbolTickState(
                ref_price=price,
                window_start_mono=now_m,
                running_max=price,
                running_min=price,
            )
            self._symbols[symbol] = st

        vwap = _vwap(payload)
        st.ticks.append((now_m, price, vwap))
        while len(st.ticks) > cfg.window_ticks:
            st.ticks.popleft()

        ref = st.ref_price
        if ref <= 0:
            ref = price
            st.ref_price = price

        st.running_max = max(st.running_max, price)
        st.running_min = min(st.running_min, price)

        rolling_mfe = max(0.0, (st.running_max - ref) / ref)
        rolling_mae = min(0.0, (st.running_min - ref) / ref)
        st.peak_mae_pct = min(st.peak_mae_pct, rolling_mae)
        st.last_mae_pct = rolling_mae

        recent = list(st.ticks)[-cfg.favorable_lookback :]
        recent_low = min(p for _, p, _ in recent) if recent else price
        fav_hits = sum(1 for _, p, _ in recent if p > ref or p > recent_low * 1.0001)
        favorable = fav_hits / len(recent) if recent else 0.0

        if price > ref or price > recent_low:
            st.favorable_streak += 1
        else:
            st.favorable_streak = 0
        st.max_favorable_streak = max(st.max_favorable_streak, st.favorable_streak)

        mom = self._momentum_score(st, price=price, vwap=vwap, mfe=rolling_mfe, mae=abs(rolling_mae))
        adverse = self._adverse_shrinking(st, price=price, mae_abs=abs(rolling_mae))

        complete = len(st.ticks) >= cfg.min_ticks_for_complete and ref > 0
        incomplete_reason = ""
        if not complete:
            incomplete_reason = f"insufficient_ticks_{len(st.ticks)}"

        trade_probe = {
            "momentum_continuation_score": mom,
            "favorable_continuation": favorable,
            "max_favorable_excursion_pct": rolling_mfe,
            "max_adverse_excursion_pct": rolling_mae,
            "max_continuation_duration": st.max_favorable_streak,
        }
        comps = continuation_components(trade_probe)
        fallback = not complete or (
            rolling_mfe == 0.0 and rolling_mae == 0.0 and mom <= 0.25 and favorable <= 0.15
        )

        debug = {
            "fallback_path": fallback,
            "components": comps,
            "live_feature_complete": complete,
            "reason_if_incomplete": incomplete_reason,
            "window_ticks": len(st.ticks),
            "ref_price": round(ref, 4),
        }

        return LiveFeatureSnapshot(
            symbol=symbol,
            rolling_mfe_pct=round(rolling_mfe, 6),
            rolling_mae_pct=round(rolling_mae, 6),
            favorable_continuation=round(favorable, 4),
            momentum_continuation_score=round(mom, 4),
            max_continuation_duration=st.max_favorable_streak,
            adverse_shrinking=round(adverse, 4),
            live_feature_complete=complete,
            quality_fallback_path=fallback,
            reason_if_incomplete=incomplete_reason,
            quality_debug=debug,
        )

    def enrich_payload(
        self, payload: Mapping[str, Any], snapshot: LiveFeatureSnapshot
    ) -> dict[str, Any]:
        out = dict(payload)
        out.update(snapshot.to_payload_fields())
        return out

    @staticmethod
    def trade_quality_extras(
        trade: Mapping[str, Any], snapshot: LiveFeatureSnapshot
    ) -> dict[str, Any]:
        comps = continuation_components(trade)
        return {
            "continuation_quality_score": trade.get("continuation_quality_score"),
            "quality_fallback_path": snapshot.quality_fallback_path,
            "live_feature_complete": snapshot.live_feature_complete,
            "rolling_mfe_pct": snapshot.rolling_mfe_pct,
            "rolling_mae_pct": snapshot.rolling_mae_pct,
            "momentum_continuation_score": snapshot.momentum_continuation_score,
            "favorable_continuation": snapshot.favorable_continuation,
            "max_continuation_duration": snapshot.max_continuation_duration,
            "adverse_shrinking": snapshot.adverse_shrinking,
            "quality_components_json": json.dumps(comps, ensure_ascii=False),
        }

    def _momentum_score(
        self,
        st: SymbolTickState,
        *,
        price: float,
        vwap: Optional[float],
        mfe: float,
        mae: float,
    ) -> float:
        cfg = self.config
        ticks = list(st.ticks)
        price_mom = 0.0
        if len(ticks) >= 2:
            _, p0, _ = ticks[-min(cfg.momentum_lookback, len(ticks))]
            if p0 > 0:
                price_mom = min(1.0, max(0.0, (price - p0) / p0 / 0.008))

        vwap_part = 0.0
        if vwap and vwap > 0:
            dist = (price - vwap) / vwap
            vwap_part = min(1.0, max(0.0, 0.5 + dist / 0.004))

        mfe_proxy = min(1.0, max(0.0, (mfe - 0.4 * mae) / 0.35)) if (mfe or mae) else 0.0
        return min(1.0, max(0.0, 0.40 * price_mom + 0.25 * vwap_part + 0.35 * mfe_proxy))

    @staticmethod
    def _adverse_shrinking(st: SymbolTickState, *, price: float, mae_abs: float) -> float:
        if mae_abs <= 0:
            return 1.0
        recovery = 0.0
        if st.running_min > 0 and price > st.running_min:
            recovery = min(1.0, (price - st.running_min) / max(st.ref_price - st.running_min, 1e-9))
        mae_improving = st.last_mae_pct >= st.peak_mae_pct * 0.98
        return min(1.0, max(0.0, 0.5 * recovery + 0.5 * (1.0 if mae_improving else 0.0)))
