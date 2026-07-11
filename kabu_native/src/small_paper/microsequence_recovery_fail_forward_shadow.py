"""
Phase681: Microsequence recovery-fail (rule C) forward shadow (no ENTRY block).

C: bounce>=0.2182 AND fall<=-0.1735 AND slope_5min<=0.1152 (pre-entry only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from research.phase631_profit_source_attribution import _num
from small_paper.microsequence_pre_entry import compute_microsequence_pre_entry_features

BIG_WINNER_YEN = 5000.0
EARLY_STOP_SEC = 300.0
DEFAULT_BOUNCE_MIN = 0.2182
DEFAULT_FALL_MAX = -0.1735
DEFAULT_SLOPE_MAX = 0.1152

ENTRY_FIELD_KEYS = (
    "microsequence_recovery_fail_shadow_candidate",
    "microsequence_recovery_fail_shadow_block",
    "microsequence_pre_entry_ok",
    "microseq_bounce_from_recent_low",
    "microseq_fall_from_recent_high",
    "microseq_slope_5min",
)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _bool(val: Any) -> bool:
    return str(val or "").lower() in ("true", "1", "yes")


def microsequence_recovery_fail_shadow_enabled(config: Any) -> bool:
    return bool(getattr(config, "microsequence_recovery_fail_shadow_enabled", False))


def evaluate_microsequence_recovery_fail(config: Any, trade: Mapping[str, Any]) -> bool:
    if not microsequence_recovery_fail_shadow_enabled(config):
        return False
    if not bool(trade.get("microsequence_pre_entry_ok") or trade.get("microsequence_ok")):
        return False
    bounce_min = float(getattr(config, "microsequence_recovery_fail_bounce_min", DEFAULT_BOUNCE_MIN))
    fall_max = float(getattr(config, "microsequence_recovery_fail_fall_from_high_max", DEFAULT_FALL_MAX))
    slope_max = float(getattr(config, "microsequence_recovery_fail_slope_5min_max", DEFAULT_SLOPE_MAX))
    bounce = _float(trade.get("microseq_bounce_from_recent_low"))
    fall = _float(trade.get("microseq_fall_from_recent_high"))
    slope = _float(trade.get("microseq_slope_5min"))
    if bounce is None or fall is None or slope is None:
        return False
    return bounce >= bounce_min and fall <= fall_max and slope <= slope_max


def compute_microsequence_recovery_fail_shadow_fields(
    config: Any,
    trade: Mapping[str, Any],
    *,
    price_ring: Optional[Sequence[tuple[float, float]]] = None,
    entry_ts: Optional[float] = None,
) -> dict[str, Any]:
    entry_px = _float(trade.get("current_price")) or _float(trade.get("CurrentPrice")) or 0.0
    pre = (
        compute_microsequence_pre_entry_features(list(price_ring or []), entry_ts=entry_ts, entry_px=entry_px)
        if entry_ts is not None and entry_px > 0
        else {
            "microsequence_pre_entry_ok": False,
            "bounce_from_recent_low": None,
            "fall_from_recent_high": None,
            "slope_5min": None,
        }
    )
    base = {
        "microsequence_recovery_fail_shadow_candidate": False,
        "microsequence_recovery_fail_shadow_block": False,
        "microsequence_pre_entry_ok": pre.get("microsequence_pre_entry_ok"),
        "microseq_bounce_from_recent_low": pre.get("bounce_from_recent_low"),
        "microseq_fall_from_recent_high": pre.get("fall_from_recent_high"),
        "microseq_slope_5min": pre.get("slope_5min"),
    }
    if not microsequence_recovery_fail_shadow_enabled(config):
        return base
    trade_aug = {**trade, **base}
    blocked = evaluate_microsequence_recovery_fail(config, trade_aug)
    return {
        **base,
        "microsequence_recovery_fail_shadow_candidate": True,
        "microsequence_recovery_fail_shadow_block": blocked,
    }


def enrich_exit_microsequence_recovery_fail_shadow_fields(
    entry_shadow: Mapping[str, Any],
    *,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
    hold_sec: Optional[float] = None,
) -> dict[str, Any]:
    from replay.pnl_yen import compute_pnl_yen_100

    blocked = _bool(entry_shadow.get("microsequence_recovery_fail_shadow_block"))
    actual_yen = round(compute_pnl_yen_100(entry_price, exit_price), 2)
    shadow_yen = 0.0 if blocked else actual_yen
    stop_hit = exit_reason == "stop_hit"
    hs = _float(hold_sec)
    early = bool(stop_hit and hs is not None and hs <= EARLY_STOP_SEC)
    return {
        "microsequence_recovery_fail_shadow_block": blocked,
        "microsequence_c_shadow_block": blocked,
        "actual_pnl_yen_100": actual_yen,
        "shadow_pnl_yen_100_c": shadow_yen,
        "delta_yen_c": round(shadow_yen - actual_yen, 2),
        "hold_sec": hs,
        "exit_reason": exit_reason,
        "is_stop_hit": stop_hit,
        "is_early_stop_300s": early,
        "is_winner": actual_yen > 0,
        "is_big_winner": actual_yen >= BIG_WINNER_YEN,
        "microsequence_c_blocked_early_stop": bool(blocked and early),
        "microsequence_c_blocked_winner": bool(blocked and actual_yen > 0),
        "microsequence_c_blocked_big_winner": bool(blocked and actual_yen >= BIG_WINNER_YEN),
    }


@dataclass
class _LaneStats:
    block_count: int = 0
    delta_yen: float = 0.0
    blocked_early_stop: int = 0
    blocked_stop_hit: int = 0
    blocked_winners: int = 0
    blocked_big_winners: int = 0
    lost_profit_yen: float = 0.0
    avoided_loss_yen: float = 0.0

    def record(self, *, blocked: bool, actual_yen: float, early: bool, stop_hit: bool) -> None:
        if blocked:
            self.block_count += 1
            self.delta_yen = round(self.delta_yen - actual_yen, 2)
            if early:
                self.blocked_early_stop += 1
            if stop_hit:
                self.blocked_stop_hit += 1
            if actual_yen > 0:
                self.blocked_winners += 1
                self.lost_profit_yen = round(self.lost_profit_yen + actual_yen, 2)
            elif actual_yen < 0:
                self.avoided_loss_yen = round(self.avoided_loss_yen + abs(actual_yen), 2)
            if actual_yen >= BIG_WINNER_YEN:
                self.blocked_big_winners += 1

    def net_delta_yen(self) -> float:
        return round(self.avoided_loss_yen - self.lost_profit_yen, 2)


@dataclass
class MicrosequenceRecoveryFailForwardShadowCounters:
    microsequence_c_shadow_target_count: int = 0
    microsequence_c: _LaneStats = field(default_factory=_LaneStats)

    def record_accept(self, fields: Mapping[str, Any]) -> None:
        if not _bool(fields.get("microsequence_recovery_fail_shadow_candidate")):
            return
        self.microsequence_c_shadow_target_count += 1

    def record_exit(self, row: Mapping[str, Any]) -> None:
        if not _bool(row.get("microsequence_recovery_fail_shadow_candidate")):
            return
        actual = _float(row.get("actual_pnl_yen_100")) or 0.0
        blocked = _bool(row.get("microsequence_recovery_fail_shadow_block"))
        self.microsequence_c.record(
            blocked=blocked,
            actual_yen=actual,
            early=_bool(row.get("is_early_stop_300s")),
            stop_hit=_bool(row.get("is_stop_hit")),
        )

    def summary_fields(self) -> dict[str, Any]:
        c = self.microsequence_c
        return {
            "microsequence_recovery_fail_shadow_enabled": True,
            "microsequence_c_shadow_block_count": c.block_count,
            "microsequence_c_shadow_delta_yen": c.delta_yen,
            "microsequence_c_shadow_blocked_early_stop": c.blocked_early_stop,
            "microsequence_c_shadow_blocked_stop_hit": c.blocked_stop_hit,
            "microsequence_c_shadow_blocked_winners": c.blocked_winners,
            "microsequence_c_shadow_blocked_big_winners": c.blocked_big_winners,
            "microsequence_c_shadow_lost_profit_yen": c.lost_profit_yen,
            "microsequence_c_shadow_avoided_loss_yen": c.avoided_loss_yen,
            "microsequence_c_shadow_net_delta_yen": c.net_delta_yen(),
        }


def build_microsequence_recovery_fail_forward_shadow_counters(
    config: Any,
) -> Optional[MicrosequenceRecoveryFailForwardShadowCounters]:
    if not microsequence_recovery_fail_shadow_enabled(config):
        return None
    return MicrosequenceRecoveryFailForwardShadowCounters()
