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

STRUCTURE_EXIT_REASONS = frozenset(
    {
        "quality_decay_exit",
        "momentum_fade_exit",
        "favorable_fade_exit",
        "vwap_break_exit",
        "mfe_giveback_exit",
    }
)

OFFICIAL_STRUCTURAL_EXIT_REASONS = frozenset(
    {
        "stop_hit",
        "session_end",
        "overlap_replaced_review",
        *STRUCTURE_EXIT_REASONS,
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


def _pnl_pct(entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0
    return round((price - entry) / entry * 100.0, 4)


def tick_from_candidate(trade: Mapping[str, Any], entry_price: float, entry_quality: float) -> dict[str, Any]:
    comps = continuation_components(trade)
    px = _as_float(trade.get("current_price")) or entry_price
    ent_raw = str(trade.get("entry_time") or "")
    return {
        "ts": ent_raw,
        "ts_epoch": 0.0,
        "price": float(px),
        "pnl_pct": _pnl_pct(entry_price, float(px)),
        "quality": float(comps["continuation_quality"]),
        "momentum": float(comps["momentum_continuation"]),
        "favorable": float(comps["favorable_continuation"]),
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
) -> Optional[tuple[float, str]]:
    """Return (pnl_pct, exit_reason) when policy fires; None if still open and session_end disallowed."""
    if not ticks:
        return None
    entry = entry_price
    stop = entry * (1.0 - cfg.hard_stop_pct / 100.0)
    peak_q = peak_pnl = peak_mom = peak_fav = 0.0

    for t in ticks:
        px = float(t.get("price") or entry)
        pnl = float(t.get("pnl_pct") or 0)
        q = float(t.get("quality") or 0)
        mom = float(t.get("momentum") or 0)
        fav = float(t.get("favorable") or 0)
        peak_q = max(peak_q, q)
        peak_pnl = max(peak_pnl, pnl)
        peak_mom = max(peak_mom, mom)
        peak_fav = max(peak_fav, fav)

        if px <= stop:
            return pnl, "stop_hit"

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

    last_pnl = float(ticks[-1].get("pnl_pct") or 0)
    if allow_session_end:
        return last_pnl, "session_end"
    return None


def combined_exit_signal_on_latest_tick(
    rich_ticks: Sequence[Mapping[str, Any]],
    entry_price: float,
    cfg: Any,
) -> Optional[tuple[float, str, float]]:
    """Incremental combined check; does not treat hold as session_end."""
    result = simulate_structural_policy(
        rich_ticks,
        entry_price,
        POLICY_COMBINED_STRUCTURAL_EXIT_V1,
        cfg,
        allow_session_end=False,
    )
    if result is None:
        return None
    pnl, reason = result
    return pnl, reason, float(rich_ticks[-1].get("price") or entry_price)
