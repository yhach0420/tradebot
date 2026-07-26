"""
Shared structure-only EXIT rules (no virtual hold, horizons, or hold_max_*).
Used by Phase 59 design review and Phase 60 official structural observer review.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from research.continuation_quality_ranking import continuation_components
from research.research_exit_criteria import _as_float

POLICY_STRUCTURAL_OBSERVER_V1 = "structural_observer_v1"
POLICY_COMBINED_STRUCTURAL_EXIT_V1 = "combined_structural_exit_v1"
POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW = (
    "combined_structural_exit_v1_trailing_mfe_shadow"
)
POLICY_COMBINED_STRUCTURAL_EXIT_V2_PRICE_MOM = "combined_structural_exit_v2_price_mom"

try:
    from research.fade_watch_shadow import (
        FADE_WATCH_EXIT_REASONS as _FADE_WATCH_EXIT_REASONS,
        POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_WATCH_SHADOW,
        uses_fade_watch_shadow,
    )
    from research.fade_hybrid_shadow import (
        FADE_HYBRID_EXIT_REASONS as _FADE_HYBRID_EXIT_REASONS,
        POLICY_COMBINED_STRUCTURAL_EXIT_V1_BREAKDOWN_CONFIRMED_SHADOW,
        POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_BREAKDOWN_SHADOW,
        POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_DISABLE_SHADOW,
        POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_HYBRID_SHADOW,
        is_fade_hybrid_review_reason,
        uses_breakdown_confirmed_shadow,
        uses_fade_breakdown_shadow,
        uses_fade_disable_shadow,
        uses_fade_hybrid_shadow,
    )
except ImportError:  # pragma: no cover
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_WATCH_SHADOW = (
        "combined_structural_exit_v1_fade_watch_shadow"
    )
    _FADE_WATCH_EXIT_REASONS = frozenset()
    uses_fade_watch_shadow = lambda _p: False  # type: ignore[assignment,misc]
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_HYBRID_SHADOW = (
        "combined_structural_exit_v1_fade_hybrid_shadow"
    )
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_BREAKDOWN_CONFIRMED_SHADOW = (
        "combined_structural_exit_v1_breakdown_confirmed_shadow"
    )
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_BREAKDOWN_SHADOW = (
        "combined_structural_exit_v1_fade_breakdown_shadow"
    )
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_DISABLE_SHADOW = (
        "combined_structural_exit_v1_fade_disable_shadow"
    )
    _FADE_HYBRID_EXIT_REASONS = frozenset()
    is_fade_hybrid_review_reason = lambda _r: False  # type: ignore[assignment,misc]
    uses_fade_hybrid_shadow = lambda _p: False  # type: ignore[assignment,misc]
    uses_breakdown_confirmed_shadow = lambda _p: False  # type: ignore[assignment,misc]
    uses_fade_breakdown_shadow = lambda _p: False  # type: ignore[assignment,misc]
    uses_fade_disable_shadow = lambda _p: False  # type: ignore[assignment,misc]

POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_SWITCH_BLOCK_SHADOW = (
    "combined_structural_exit_v1_fade_switch_block_shadow"
)
POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_FIRST_SWITCH_BLOCK_SHADOW = (
    "combined_structural_exit_v1_fade_first_switch_block_shadow"
)

STRUCTURE_EXIT_REASONS = frozenset(
    {
        "quality_decay_exit",
        "momentum_fade_exit",
        "price_momentum_fade_exit",
        "favorable_fade_exit",
        "vwap_break_exit",
        "mfe_giveback_exit",
        "trailing_mfe_exit",
        "no_progress_exit",
        *_FADE_WATCH_EXIT_REASONS,
        *_FADE_HYBRID_EXIT_REASONS,
    }
)

OFFICIAL_STRUCTURAL_EXIT_REASONS = frozenset(
    {
        "stop_hit",
        "take_exit",
        "session_end",
        "overlap_replaced_review",
        "no_progress_exit",
        *STRUCTURE_EXIT_REASONS,
        "morning_session_close",
        "afternoon_session_close",
        "recovery_session_close",
    }
)

FORBIDDEN_OFFICIAL_EXIT_REASONS = frozenset(
    {
        "virtual_hold_expired",
        "live_virtual_hold",
    }
)


def is_virtual_hold_exit_reason(reason: str) -> bool:
    r = str(reason or "").strip().lower()
    return "virtual_hold" in r or r == "live_virtual_hold"


def is_official_structural_exit_reason(reason: str) -> bool:
    r = str(reason or "").strip()
    if not r or is_virtual_hold_exit_reason(r):
        return False
    return r in OFFICIAL_STRUCTURAL_EXIT_REASONS

VWAP_BREAK_PEAK_PNL = 0.10
TRAILING_GIVEBACK_PCT = 0.18
LOWER_HIGH_TICKS = 3
PRICE_MOM_NORM_SCALE = 0.008

# Phase332 production: board-dynamic trailing-MFE (entry_imbalance_percentile tier).
# Legacy fixed params retained for shadow counterfactual comparison only.
LEGACY_TRAILING_MFE_ACTIVATE_PCT = 0.80
LEGACY_TRAILING_MFE_GIVEBACK_FRAC = 0.50
# Back-compat aliases (pre-Phase332 scripts).
TRAILING_MFE_ACTIVATE_PCT = LEGACY_TRAILING_MFE_ACTIVATE_PCT
TRAILING_MFE_GIVEBACK_FRAC = LEGACY_TRAILING_MFE_GIVEBACK_FRAC


def trailing_mfe_params(
    entry_imbalance_percentile: Optional[float] = None,
) -> tuple[float, float, str]:
    """Board-dynamic trailing activate/giveback for production EXIT."""
    from small_paper.board_dynamic_trailing_shadow import trailing_params_for_board_tier

    return trailing_params_for_board_tier(entry_imbalance_percentile)


def trailing_mfe_exit_triggered(
    *,
    peak_pnl: float,
    pnl: float,
    entry_imbalance_percentile: Optional[float] = None,
) -> bool:
    activate, giveback, _ = trailing_mfe_params(entry_imbalance_percentile)
    return peak_pnl >= activate and pnl <= peak_pnl * giveback


def _normalized_price_momentum(ppm: float) -> float:
    return min(1.0, max(0.0, float(ppm) / PRICE_MOM_NORM_SCALE))


def pure_price_momentum_from_prices(
    prices: Sequence[float],
    *,
    lookback: int = 5,
) -> float:
    """5-tick lookback (price - p0) / p0; used when replaying stored events."""
    if len(prices) < 2:
        return 0.0
    p0 = prices[-min(lookback, len(prices))]
    cur = prices[-1]
    if p0 <= 0:
        return 0.0
    return (cur - p0) / p0


def _pnl_pct(entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0
    return round((price - entry) / entry * 100.0, 4)


def _price_momentum_fade_ratio(cfg: Any) -> float:
    return float(getattr(cfg, "price_momentum_fade_ratio", 0.85) or 0.85)


def tick_from_candidate(trade: Mapping[str, Any], entry_price: float, entry_quality: float) -> dict[str, Any]:
    comps = continuation_components(trade)
    px = _as_float(trade.get("current_price")) or entry_price
    ent_raw = str(trade.get("entry_time") or "")
    ppm = _as_float(trade.get("pure_price_momentum"))
    return {
        "ts": ent_raw,
        "ts_epoch": 0.0,
        "price": float(px),
        "pnl_pct": _pnl_pct(entry_price, float(px)),
        "quality": float(comps["continuation_quality"]),
        "momentum": float(comps["momentum_continuation"]),
        "favorable": float(comps["favorable_continuation"]),
        "pure_price_momentum": float(ppm if ppm is not None else 0.0),
    }


def _lower_high_on_ticks(ticks: Sequence[Mapping[str, Any]]) -> bool:
    if len(ticks) < LOWER_HIGH_TICKS:
        return False
    prices = [float(t.get("price") or 0) for t in ticks[-LOWER_HIGH_TICKS:]]
    return all(prices[i] > prices[i + 1] for i in range(len(prices) - 1))


def simulate_structural_policy(
    ticks: Sequence[Mapping[str, Any]],
    entry_price: float,
    policy: str,
    cfg: Any,
    *,
    allow_session_end: bool = True,
    entry_imbalance_percentile: Optional[float] = None,
    entry_ts_epoch: Optional[float] = None,
) -> Optional[tuple[float, str]]:
    """Return (pnl_pct, exit_reason) when policy fires; None if still open and session_end disallowed."""
    if not ticks:
        return None
    entry = entry_price
    stop = entry * (1.0 - cfg.hard_stop_pct / 100.0)
    peak_q = peak_pnl = peak_mom = peak_fav = peak_ppm_n = 0.0
    ppm_ratio = _price_momentum_fade_ratio(cfg)

    for t in ticks:
        px = float(t.get("price") or entry)
        pnl = float(t.get("pnl_pct") or 0)
        q = float(t.get("quality") or 0)
        mom = float(t.get("momentum") or 0)
        fav = float(t.get("favorable") or 0)
        ppm = float(t.get("pure_price_momentum") or 0)
        ppm_n = _normalized_price_momentum(ppm)
        peak_q = max(peak_q, q)
        peak_pnl = max(peak_pnl, pnl)
        peak_mom = max(peak_mom, mom)
        peak_fav = max(peak_fav, fav)
        peak_ppm_n = max(peak_ppm_n, ppm_n)

        if px <= stop:
            return pnl, "stop_hit"

        elapsed = 0.0
        if entry_ts_epoch is not None:
            elapsed = max(0.0, float(t.get("ts_epoch") or 0) - float(entry_ts_epoch))

        if policy == POLICY_STRUCTURAL_OBSERVER_V1:
            continue

        if policy == "stop_only_exit":
            continue
        if policy == "quality_decay_exit" and q <= peak_q - cfg.take_quality_drop:
            return pnl, "quality_decay_exit"
        if policy == "momentum_fade_exit" and peak_mom > 0 and mom < peak_mom * cfg.momentum_weaken_ratio:
            return pnl, "momentum_fade_exit"
        if policy == "favorable_fade_exit" and peak_fav > 0 and fav < peak_fav * cfg.favorable_fade_ratio:
            return pnl, "favorable_fade_exit"
        if policy == "vwap_break_exit" and peak_pnl > VWAP_BREAK_PEAK_PNL and pnl < 0:
            return pnl, "vwap_break_exit"
        if policy == "mfe_giveback_exit" and peak_pnl > 0 and pnl <= peak_pnl - TRAILING_GIVEBACK_PCT:
            return pnl, "mfe_giveback_exit"
        if policy == "lower_high_exit" and _lower_high_on_ticks(ticks[: ticks.index(t) + 1]):
            return pnl, "lower_high_exit"
        if policy == POLICY_COMBINED_STRUCTURAL_EXIT_V1:
            if q <= peak_q - cfg.take_quality_drop:
                return pnl, "quality_decay_exit"
            if peak_mom > 0 and mom < peak_mom * cfg.momentum_weaken_ratio:
                return pnl, "momentum_fade_exit"
            if peak_fav > 0 and fav < peak_fav * cfg.favorable_fade_ratio:
                return pnl, "favorable_fade_exit"
            if peak_pnl > VWAP_BREAK_PEAK_PNL and pnl < 0:
                return pnl, "vwap_break_exit"
            if peak_pnl > 0 and pnl <= peak_pnl - TRAILING_GIVEBACK_PCT:
                return pnl, "mfe_giveback_exit"
        if policy == POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW:
            if bool(getattr(cfg, "no_progress_exit_enabled", False)):
                from small_paper.no_progress_exit import no_progress_exit_triggered

                if no_progress_exit_triggered(elapsed, peak_pnl, pnl):
                    return pnl, "no_progress_exit"
            # Phase332: board-dynamic trailing (stop_hit handled above; no fade exits).
            if trailing_mfe_exit_triggered(
                peak_pnl=peak_pnl,
                pnl=pnl,
                entry_imbalance_percentile=entry_imbalance_percentile,
            ):
                return pnl, "trailing_mfe_exit"
        if policy == POLICY_COMBINED_STRUCTURAL_EXIT_V2_PRICE_MOM:
            if q <= peak_q - cfg.take_quality_drop:
                return pnl, "quality_decay_exit"
            if peak_ppm_n > 0 and ppm_n < peak_ppm_n * ppm_ratio:
                return pnl, "price_momentum_fade_exit"
            if peak_fav > 0 and fav < peak_fav * cfg.favorable_fade_ratio:
                return pnl, "favorable_fade_exit"
            if peak_pnl > VWAP_BREAK_PEAK_PNL and pnl < 0:
                return pnl, "vwap_break_exit"
            if peak_pnl > 0 and pnl <= peak_pnl - TRAILING_GIVEBACK_PCT:
                return pnl, "mfe_giveback_exit"

    last_pnl = float(ticks[-1].get("pnl_pct") or 0)
    if allow_session_end:
        return last_pnl, "session_end"
    return None


def combined_exit_signal_on_latest_tick(
    rich_ticks: Sequence[Mapping[str, Any]],
    entry_price: float,
    cfg: Any,
    *,
    entry_imbalance_percentile: Optional[float] = None,
    entry_ts_epoch: Optional[float] = None,
) -> Optional[tuple[float, str, float]]:
    """Incremental combined check; does not treat hold as session_end."""
    policy = str(
        getattr(cfg, "structural_exit_policy", None) or POLICY_COMBINED_STRUCTURAL_EXIT_V1
    )
    result = simulate_structural_policy(
        rich_ticks,
        entry_price,
        policy,
        cfg,
        allow_session_end=False,
        entry_imbalance_percentile=entry_imbalance_percentile,
        entry_ts_epoch=entry_ts_epoch,
    )
    if result is None:
        return None
    pnl, reason = result
    return pnl, reason, float(rich_ticks[-1].get("price") or entry_price)
