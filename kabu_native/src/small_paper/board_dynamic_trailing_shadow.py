"""
Phase332: Board-dynamic trailing_mfe production + legacy-fixed shadow counterfactual.

Production EXIT uses board-dynamic activate/giveback by entry_imbalance_percentile.
Shadow fields simulate pre-Phase332 fixed trailing (0.8% / 50%) for comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

# Phase330/331 fixed board split — not tuned per session.
BOARD_SPLIT_PERCENTILE = 47.62
BOARD_HIGH_ACTIVATE_PCT = 1.0
BOARD_HIGH_GIVEBACK_FRAC = 0.60
BOARD_LOW_ACTIVATE_PCT = 0.6
BOARD_LOW_GIVEBACK_FRAC = 0.40

# Pre-Phase332 fixed trailing (shadow counterfactual only).
LEGACY_FIXED_ACTIVATE_PCT = 0.80
LEGACY_FIXED_GIVEBACK_FRAC = 0.50

PRODUCTION_FIELD_KEYS = (
    "board_dynamic_trailing_tier",
    "board_dynamic_trailing_activate_pct",
    "board_dynamic_trailing_giveback_frac",
)

SHADOW_FIELD_KEYS = (
    "shadow_board_dynamic_tier",
    "shadow_board_dynamic_activate_pct",
    "shadow_board_dynamic_giveback_frac",
    "shadow_exit_reason",
    "shadow_exit_price",
    "shadow_exit_time",
    "shadow_pnl_pct",
    "shadow_pnl_yen_100",
    "actual_vs_shadow_delta_yen",
    "actual_vs_shadow_delta_pct",
)

SUMMARY_FIELD_KEYS = (
    "board_dynamic_shadow_enabled",
    "board_dynamic_shadow_exit_count",
    "board_dynamic_shadow_improved_count",
    "board_dynamic_shadow_total_delta_yen",
    "board_dynamic_shadow_stop_hit_count",
    "board_dynamic_shadow_trailing_mfe_count",
    "board_dynamic_shadow_session_close_count",
)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def board_tier_from_percentile(imb_pct: Optional[float]) -> str:
    if imb_pct is not None and imb_pct >= BOARD_SPLIT_PERCENTILE:
        return "board_high"
    return "board_low"


def trailing_params_for_board_tier(imb_pct: Optional[float]) -> tuple[float, float, str]:
    tier = board_tier_from_percentile(imb_pct)
    if tier == "board_high":
        return BOARD_HIGH_ACTIVATE_PCT, BOARD_HIGH_GIVEBACK_FRAC, tier
    return BOARD_LOW_ACTIVATE_PCT, BOARD_LOW_GIVEBACK_FRAC, tier


def _pnl_pct(entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0
    return round((price - entry) / entry * 100.0, 4)


def _tick_ts(tick: Mapping[str, Any]) -> float:
    ts = _float(tick.get("ts_epoch"))
    if ts is not None and ts > 0:
        return ts
    raw = str(tick.get("ts") or "").strip()
    if not raw:
        return 0.0
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return 0.0


def _tick_price(tick: Mapping[str, Any], *, entry_price: float) -> float:
    px = _float(tick.get("price"))
    if px is not None and px > 0:
        return px
    pnl = _float(tick.get("pnl_pct"))
    if pnl is not None and entry_price > 0:
        return entry_price * (1.0 + pnl / 100.0)
    return entry_price


def simulate_board_dynamic_shadow_exit(
    rich_ticks: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    hard_stop_pct: float,
    entry_imbalance_percentile: Optional[float],
    cutoff_ts: Optional[float] = None,
    activate_pct: Optional[float] = None,
    giveback_frac: Optional[float] = None,
    tier_label: Optional[str] = None,
) -> dict[str, Any]:
    """Counterfactual trailing exit on stored tick path."""
    from replay.pnl_yen import compute_pnl_yen_100

    if activate_pct is None or giveback_frac is None or tier_label is None:
        activate_pct, giveback_frac, tier = trailing_params_for_board_tier(
            entry_imbalance_percentile
        )
    else:
        tier = tier_label
    entry = float(entry_price)
    stop = entry * (1.0 - float(hard_stop_pct) / 100.0)
    peak_pnl = 0.0

    usable = [t for t in rich_ticks if _tick_ts(t) > 0 or _float(t.get("price")) is not None]
    if not usable:
        return {
            "shadow_board_dynamic_tier": tier,
            "shadow_board_dynamic_activate_pct": activate_pct,
            "shadow_board_dynamic_giveback_frac": giveback_frac,
            "shadow_exit_reason": "no_ticks",
            "shadow_exit_price": entry,
            "shadow_exit_time": "",
            "shadow_pnl_pct": 0.0,
            "shadow_pnl_yen_100": 0.0,
        }

    last_ts = 0.0
    last_px = entry
    for tick in usable:
        ts = _tick_ts(tick)
        if cutoff_ts is not None and ts > cutoff_ts:
            break
        px = _tick_price(tick, entry_price=entry)
        pnl = _pnl_pct(entry, px)
        peak_pnl = max(peak_pnl, pnl)
        last_ts = ts
        last_px = px

        if px <= stop:
            exit_time = datetime.fromtimestamp(ts, tz=JST).isoformat(timespec="seconds") if ts > 0 else ""
            return {
                "shadow_board_dynamic_tier": tier,
                "shadow_board_dynamic_activate_pct": activate_pct,
                "shadow_board_dynamic_giveback_frac": giveback_frac,
                "shadow_exit_reason": "stop_hit",
                "shadow_exit_price": round(px, 4),
                "shadow_exit_time": exit_time,
                "shadow_pnl_pct": pnl,
                "shadow_pnl_yen_100": round(compute_pnl_yen_100(entry, px), 2),
            }

        if peak_pnl >= activate_pct and pnl <= peak_pnl * giveback_frac:
            exit_time = datetime.fromtimestamp(ts, tz=JST).isoformat(timespec="seconds") if ts > 0 else ""
            return {
                "shadow_board_dynamic_tier": tier,
                "shadow_board_dynamic_activate_pct": activate_pct,
                "shadow_board_dynamic_giveback_frac": giveback_frac,
                "shadow_exit_reason": "trailing_mfe_exit",
                "shadow_exit_price": round(px, 4),
                "shadow_exit_time": exit_time,
                "shadow_pnl_pct": pnl,
                "shadow_pnl_yen_100": round(compute_pnl_yen_100(entry, px), 2),
            }

    exit_time = (
        datetime.fromtimestamp(last_ts, tz=JST).isoformat(timespec="seconds") if last_ts > 0 else ""
    )
    final_pnl = _pnl_pct(entry, last_px)
    return {
        "shadow_board_dynamic_tier": tier,
        "shadow_board_dynamic_activate_pct": activate_pct,
        "shadow_board_dynamic_giveback_frac": giveback_frac,
        "shadow_exit_reason": "session_close",
        "shadow_exit_price": round(last_px, 4),
        "shadow_exit_time": exit_time,
        "shadow_pnl_pct": final_pnl,
        "shadow_pnl_yen_100": round(compute_pnl_yen_100(entry, last_px), 2),
    }


def simulate_legacy_fixed_trailing_exit(
    rich_ticks: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    hard_stop_pct: float,
    cutoff_ts: Optional[float] = None,
) -> dict[str, Any]:
    """Counterfactual pre-Phase332 fixed trailing (0.8% activate / 50% giveback)."""
    return simulate_board_dynamic_shadow_exit(
        rich_ticks,
        entry_price=entry_price,
        hard_stop_pct=hard_stop_pct,
        entry_imbalance_percentile=None,
        cutoff_ts=cutoff_ts,
        activate_pct=LEGACY_FIXED_ACTIVATE_PCT,
        giveback_frac=LEGACY_FIXED_GIVEBACK_FRAC,
        tier_label="legacy_fixed",
    )


def enrich_exit_board_dynamic_shadow_fields(
    entry_shadow: Mapping[str, Any],
    *,
    rich_ticks: Sequence[Mapping[str, Any]],
    entry_price: float,
    entry_ts: float,
    hard_stop_pct: float,
    actual_exit_time: float,
    actual_exit_price: float,
    actual_pnl_pct: float,
) -> dict[str, Any]:
    """Compute legacy-fixed shadow exit and delta vs actual board-dynamic (logging only)."""
    from replay.pnl_yen import compute_pnl_yen_100

    shadow = simulate_legacy_fixed_trailing_exit(
        rich_ticks,
        entry_price=entry_price,
        hard_stop_pct=hard_stop_pct,
        cutoff_ts=actual_exit_time if actual_exit_time > 0 else None,
    )
    actual_yen = round(compute_pnl_yen_100(entry_price, actual_exit_price), 2)
    shadow_yen = float(shadow.get("shadow_pnl_yen_100") or 0.0)
    shadow_pct = float(shadow.get("shadow_pnl_pct") or 0.0)
    return {
        **shadow,
        "actual_vs_shadow_delta_yen": round(shadow_yen - actual_yen, 2),
        "actual_vs_shadow_delta_pct": round(shadow_pct - float(actual_pnl_pct), 4),
    }


@dataclass
class BoardDynamicTrailingShadowCounters:
    board_dynamic_shadow_exit_count: int = 0
    board_dynamic_shadow_improved_count: int = 0
    board_dynamic_shadow_total_delta_yen: float = 0.0
    board_dynamic_shadow_stop_hit_count: int = 0
    board_dynamic_shadow_trailing_mfe_count: int = 0
    board_dynamic_shadow_session_close_count: int = 0

    def record_exit(self, row: Mapping[str, Any]) -> None:
        if row.get("shadow_exit_reason") in (None, "", "no_ticks"):
            return
        self.board_dynamic_shadow_exit_count += 1
        delta = _float(row.get("actual_vs_shadow_delta_yen")) or 0.0
        self.board_dynamic_shadow_total_delta_yen = round(
            self.board_dynamic_shadow_total_delta_yen + delta, 2
        )
        if delta > 0:
            self.board_dynamic_shadow_improved_count += 1
        reason = str(row.get("shadow_exit_reason") or "")
        if reason == "stop_hit":
            self.board_dynamic_shadow_stop_hit_count += 1
        elif reason == "trailing_mfe_exit":
            self.board_dynamic_shadow_trailing_mfe_count += 1
        elif reason == "session_close":
            self.board_dynamic_shadow_session_close_count += 1

    def summary_fields(self) -> dict[str, Any]:
        return {
            "board_dynamic_shadow_enabled": True,
            "board_dynamic_shadow_exit_count": self.board_dynamic_shadow_exit_count,
            "board_dynamic_shadow_improved_count": self.board_dynamic_shadow_improved_count,
            "board_dynamic_shadow_total_delta_yen": self.board_dynamic_shadow_total_delta_yen,
            "board_dynamic_shadow_stop_hit_count": self.board_dynamic_shadow_stop_hit_count,
            "board_dynamic_shadow_trailing_mfe_count": self.board_dynamic_shadow_trailing_mfe_count,
            "board_dynamic_shadow_session_close_count": self.board_dynamic_shadow_session_close_count,
        }
