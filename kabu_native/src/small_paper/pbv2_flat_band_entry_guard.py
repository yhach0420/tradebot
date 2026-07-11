"""
Phase669: PBv2 flat-band production ENTRY guard (mainline reject).

Uses the same evaluate_flat_plus_overheat logic and thresholds as pbv2_flat_band_guard_shadow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from small_paper.pbv2_flat_band_guard_shadow import (
    APPLY_POOL_PBV2_ONLY,
    VARIANT_FLAT_PLUS_OVERHEAT,
    evaluate_flat_plus_overheat,
    shadow_applies_to_trade,
)

REJECT_FLAT_BAND_MAINLINE = "flat_band_mainline"
LOG_EVENT_KIND = "flat_band_mainline_reject"


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def flat_band_mainline_enabled(config: Any) -> bool:
    return bool(getattr(config, "pbv2_flat_band_mainline_enabled", False))


def _thresholds_from_config(config: Any) -> dict[str, float]:
    return {
        "rise5_min": float(getattr(config, "pbv2_flat_band_shadow_rise5_flat_min_pct", 0.0) or 0.0),
        "rise5_max": float(getattr(config, "pbv2_flat_band_shadow_rise5_flat_max_pct", 0.5) or 0.5),
        "rise10_min": float(getattr(config, "pbv2_flat_band_shadow_rise10_flat_min_pct", -0.5) or -0.5),
        "rise10_max": float(getattr(config, "pbv2_flat_band_shadow_rise10_flat_max_pct", 0.5) or 0.5),
        "overheat_threshold": float(
            getattr(config, "pbv2_flat_band_shadow_overheat_rise5_pct", 2.0) or 2.0
        ),
    }


def would_block_flat_band_mainline(config: Any, trade: Mapping[str, Any]) -> tuple[bool, str]:
    """Same block predicate as shadow flat_plus_overheat variant."""
    if not flat_band_mainline_enabled(config):
        return False, ""
    apply_pool = str(
        getattr(config, "pbv2_flat_band_shadow_apply_pool", APPLY_POOL_PBV2_ONLY) or APPLY_POOL_PBV2_ONLY
    )
    if not shadow_applies_to_trade(trade, apply_pool=apply_pool):
        return False, ""
    th = _thresholds_from_config(config)
    blocked, reason, _, _ = evaluate_flat_plus_overheat(
        trade,
        rise5_min=th["rise5_min"],
        rise5_max=th["rise5_max"],
        rise10_min=th["rise10_min"],
        rise10_max=th["rise10_max"],
        overheat_threshold=th["overheat_threshold"],
    )
    return blocked, reason


def compute_flat_band_mainline_fields(config: Any, trade: Mapping[str, Any]) -> dict[str, Any]:
    rise5 = _float(trade.get("entry_rise_5min_pct"))
    rise10 = _float(trade.get("entry_rise_10min_pct"))
    apply_pool = str(
        getattr(config, "pbv2_flat_band_shadow_apply_pool", APPLY_POOL_PBV2_ONLY) or APPLY_POOL_PBV2_ONLY
    )
    blocked, reason = would_block_flat_band_mainline(config, trade)
    return {
        "pbv2_flat_band_shadow_block": blocked,
        "pbv2_flat_band_shadow_reason": reason,
        "pbv2_flat_band_rise5": rise5,
        "pbv2_flat_band_rise10": rise10,
        "pbv2_flat_band_variant": VARIANT_FLAT_PLUS_OVERHEAT,
        "pbv2_flat_band_shadow_apply_pool": apply_pool,
        "flat_band_mainline_block": blocked,
        "reject_reason": REJECT_FLAT_BAND_MAINLINE if blocked else "",
    }


@dataclass
class PbV2FlatBandEntryGuardConfig:
    enabled: bool = False
    apply_pool: str = APPLY_POOL_PBV2_ONLY
    rise5_min: float = 0.0
    rise5_max: float = 0.5
    rise10_min: float = -0.5
    rise10_max: float = 0.5
    overheat_threshold: float = 2.0


@dataclass
class PbV2FlatBandEntryGuardCheck:
    blocked: bool
    reason: str = ""
    rise5: Optional[float] = None
    rise10: Optional[float] = None
    reject_reason: str = ""

    def log_fields(self, *, symbol: str) -> dict[str, Any]:
        return {
            "event_kind": LOG_EVENT_KIND,
            "symbol": symbol,
            "pbv2_flat_band_shadow_reason": self.reason,
            "pbv2_flat_band_rise5": self.rise5,
            "pbv2_flat_band_rise10": self.rise10,
            "pbv2_flat_band_variant": VARIANT_FLAT_PLUS_OVERHEAT,
            "reject_reason": self.reject_reason or REJECT_FLAT_BAND_MAINLINE,
        }


@dataclass
class PbV2FlatBandEntryGuardState:
    config: PbV2FlatBandEntryGuardConfig
    reject_count: int = 0
    rejected_symbols: set[str] = field(default_factory=set)

    def summary_fields(self) -> dict[str, Any]:
        return {
            "pbv2_flat_band_mainline_enabled": self.config.enabled,
            "pbv2_flat_band_mainline_reject_count": self.reject_count,
            "pbv2_flat_band_mainline_reject_symbols": sorted(self.rejected_symbols),
        }

    def check(self, trade: Mapping[str, Any]) -> PbV2FlatBandEntryGuardCheck:
        if not self.config.enabled:
            return PbV2FlatBandEntryGuardCheck(blocked=False)
        if not shadow_applies_to_trade(trade, apply_pool=self.config.apply_pool):
            return PbV2FlatBandEntryGuardCheck(blocked=False)
        rise5 = _float(trade.get("entry_rise_5min_pct"))
        rise10 = _float(trade.get("entry_rise_10min_pct"))
        blocked, reason, _, _ = evaluate_flat_plus_overheat(
            trade,
            rise5_min=self.config.rise5_min,
            rise5_max=self.config.rise5_max,
            rise10_min=self.config.rise10_min,
            rise10_max=self.config.rise10_max,
            overheat_threshold=self.config.overheat_threshold,
        )
        return PbV2FlatBandEntryGuardCheck(
            blocked=blocked,
            reason=reason,
            rise5=rise5,
            rise10=rise10,
            reject_reason=REJECT_FLAT_BAND_MAINLINE if blocked else "",
        )


def config_from_pilot(pilot_config: Any) -> PbV2FlatBandEntryGuardConfig:
    th = _thresholds_from_config(pilot_config)
    return PbV2FlatBandEntryGuardConfig(
        enabled=flat_band_mainline_enabled(pilot_config),
        apply_pool=str(
            getattr(pilot_config, "pbv2_flat_band_shadow_apply_pool", APPLY_POOL_PBV2_ONLY)
            or APPLY_POOL_PBV2_ONLY
        ),
        rise5_min=th["rise5_min"],
        rise5_max=th["rise5_max"],
        rise10_min=th["rise10_min"],
        rise10_max=th["rise10_max"],
        overheat_threshold=th["overheat_threshold"],
    )


def build_pbv2_flat_band_entry_guard_state(pilot_config: Any) -> Optional[PbV2FlatBandEntryGuardState]:
    cfg = config_from_pilot(pilot_config)
    if not cfg.enabled:
        return None
    return PbV2FlatBandEntryGuardState(config=cfg)
