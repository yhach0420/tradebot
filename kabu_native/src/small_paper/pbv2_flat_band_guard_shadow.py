"""
Phase650: PBv2 flat-band guard shadow (no ENTRY block).

Records counterfactual outcomes if PBv2 accepted trades matching flat_plus_overheat
had been blocked. OR accepted trades are never evaluated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from small_paper.or_overlay_cap import ENTRY_TYPE_OR, entry_type_from_trade

APPLY_POOL_PBV2_ONLY = "PBV2_ONLY"
VARIANT_FLAT_PLUS_OVERHEAT = "flat_plus_overheat"
REASON_FLAT_BAND_NARROW = "flat_band_narrow"
REASON_OVERHEAT_RISE5 = "overheat_rise5"
REASON_RISE5_MISSING = "rise5_missing_fail_open"

ENTRY_FIELD_KEYS = (
    "pbv2_flat_band_shadow_block",
    "pbv2_flat_band_shadow_reason",
    "pbv2_flat_band_rise5",
    "pbv2_flat_band_rise10",
    "pbv2_flat_band_variant",
    "pbv2_flat_band_shadow_apply_pool",
    "flat_band_and_rise5_shadow_block",
)

EXIT_EXTRA_FIELD_KEYS = (
    "pbv2_flat_band_shadow_blocked_pnl_yen_100",
    "pbv2_flat_band_shadow_blocked_mfe",
    "pbv2_flat_band_shadow_blocked_mae",
    "pbv2_flat_band_shadow_pnl_yen_100",
    "pbv2_flat_band_shadow_delta_yen",
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


def flat_band_shadow_enabled(config: Any) -> bool:
    return bool(getattr(config, "pbv2_flat_band_shadow_enabled", False))


def _shadow_apply_pool(config: Any) -> str:
    return str(
        getattr(config, "pbv2_flat_band_shadow_apply_pool", APPLY_POOL_PBV2_ONLY) or APPLY_POOL_PBV2_ONLY
    )


def _rise5_flat_min(config: Any) -> float:
    return float(getattr(config, "pbv2_flat_band_shadow_rise5_flat_min_pct", 0.0) or 0.0)


def _rise5_flat_max(config: Any) -> float:
    return float(getattr(config, "pbv2_flat_band_shadow_rise5_flat_max_pct", 0.5) or 0.5)


def _rise10_flat_min(config: Any) -> float:
    return float(getattr(config, "pbv2_flat_band_shadow_rise10_flat_min_pct", -0.5) or -0.5)


def _rise10_flat_max(config: Any) -> float:
    return float(getattr(config, "pbv2_flat_band_shadow_rise10_flat_max_pct", 0.5) or 0.5)


def _overheat_rise5_pct(config: Any) -> float:
    return float(getattr(config, "pbv2_flat_band_shadow_overheat_rise5_pct", 2.0) or 2.0)


def shadow_applies_to_trade(trade: Mapping[str, Any], *, apply_pool: str) -> bool:
    pool = str(apply_pool or APPLY_POOL_PBV2_ONLY).strip().upper()
    if pool == APPLY_POOL_PBV2_ONLY:
        return entry_type_from_trade(trade) != ENTRY_TYPE_OR
    return True


def is_flat_band_narrow(
    rise5: Optional[float],
    rise10: Optional[float],
    *,
    rise5_min: float,
    rise5_max: float,
    rise10_min: float,
    rise10_max: float,
) -> bool:
    if rise5 is None or rise10 is None:
        return False
    return rise5_min <= float(rise5) < rise5_max and rise10_min <= float(rise10) <= rise10_max


def is_overheat_rise5(rise5: Optional[float], *, threshold: float) -> bool:
    if rise5 is None:
        return False
    return float(rise5) > float(threshold)


def evaluate_flat_plus_overheat(
    trade: Mapping[str, Any],
    *,
    rise5_min: float,
    rise5_max: float,
    rise10_min: float,
    rise10_max: float,
    overheat_threshold: float,
) -> tuple[bool, str, bool, bool]:
    rise5 = _float(trade.get("entry_rise_5min_pct"))
    rise10 = _float(trade.get("entry_rise_10min_pct"))
    if rise5 is None:
        return False, REASON_RISE5_MISSING, False, False
    flat = is_flat_band_narrow(
        rise5,
        rise10,
        rise5_min=rise5_min,
        rise5_max=rise5_max,
        rise10_min=rise10_min,
        rise10_max=rise10_max,
    )
    overheat = is_overheat_rise5(rise5, threshold=overheat_threshold)
    if flat and overheat:
        return True, f"{REASON_FLAT_BAND_NARROW}+{REASON_OVERHEAT_RISE5}", True, True
    if flat:
        return True, REASON_FLAT_BAND_NARROW, True, False
    if overheat:
        return True, REASON_OVERHEAT_RISE5, False, True
    return False, "", False, False


def compute_pbv2_flat_band_shadow_fields(
    config: Any,
    trade: Mapping[str, Any],
    *,
    rise5_shadow_block: Optional[bool] = None,
) -> dict[str, Any]:
    """Shadow-only flat-band guard at PBv2 accept (does not block actual entry)."""
    rise5 = _float(trade.get("entry_rise_5min_pct"))
    rise10 = _float(trade.get("entry_rise_10min_pct"))
    apply_pool = _shadow_apply_pool(config)
    base = {
        "pbv2_flat_band_shadow_block": False,
        "pbv2_flat_band_shadow_reason": "",
        "pbv2_flat_band_rise5": rise5,
        "pbv2_flat_band_rise10": rise10,
        "pbv2_flat_band_variant": VARIANT_FLAT_PLUS_OVERHEAT,
        "pbv2_flat_band_shadow_apply_pool": apply_pool,
        "flat_band_and_rise5_shadow_block": False,
    }
    if not flat_band_shadow_enabled(config):
        return base

    applies = shadow_applies_to_trade(trade, apply_pool=apply_pool)
    if not applies:
        return base

    blocked, reason, _flat_hit, _overheat_hit = evaluate_flat_plus_overheat(
        trade,
        rise5_min=_rise5_flat_min(config),
        rise5_max=_rise5_flat_max(config),
        rise10_min=_rise10_flat_min(config),
        rise10_max=_rise10_flat_max(config),
        overheat_threshold=_overheat_rise5_pct(config),
    )
    rise5_block = rise5_shadow_block
    if rise5_block is None:
        rise5_block = _bool(trade.get("pbv2_rise5_shadow_block"))
    overlap = bool(blocked and rise5_block)
    return {
        **base,
        "pbv2_flat_band_shadow_block": blocked,
        "pbv2_flat_band_shadow_reason": reason,
        "flat_band_and_rise5_shadow_block": overlap,
    }


def _session_bucket_from_fields(fields: Mapping[str, Any]) -> str:
    mins = _float(fields.get("minutes_from_open"))
    if mins is None:
        return "unknown"
    if mins < 150:
        return "AM"
    if mins >= 210:
        return "PM"
    return "lunch"


def enrich_exit_pbv2_flat_band_shadow_fields(
    entry_shadow: Mapping[str, Any],
    *,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
    peak_mfe_pct: Optional[float] = None,
    peak_mae_pct: Optional[float] = None,
) -> dict[str, Any]:
    """Counterfactual shadow PnL if flat-band blocked ENTRY had not occurred."""
    from replay.pnl_yen import compute_pnl_yen_100

    blocked = _bool(entry_shadow.get("pbv2_flat_band_shadow_block"))
    actual_yen = round(compute_pnl_yen_100(entry_price, exit_price), 2)
    shadow_yen = 0.0 if blocked else actual_yen
    mfe = _float(peak_mfe_pct)
    mae = _float(peak_mae_pct)
    return {
        "pbv2_flat_band_shadow_block": blocked,
        "pbv2_flat_band_shadow_reason": entry_shadow.get("pbv2_flat_band_shadow_reason", ""),
        "pbv2_flat_band_rise5": entry_shadow.get("pbv2_flat_band_rise5"),
        "pbv2_flat_band_rise10": entry_shadow.get("pbv2_flat_band_rise10"),
        "pbv2_flat_band_variant": entry_shadow.get("pbv2_flat_band_variant"),
        "flat_band_and_rise5_shadow_block": entry_shadow.get("flat_band_and_rise5_shadow_block"),
        "pbv2_flat_band_shadow_blocked_pnl_yen_100": actual_yen if blocked else 0.0,
        "pbv2_flat_band_shadow_blocked_mfe": mfe if blocked else None,
        "pbv2_flat_band_shadow_blocked_mae": mae if blocked else None,
        "pbv2_flat_band_shadow_pnl_yen_100": shadow_yen,
        "pbv2_flat_band_shadow_delta_yen": round(shadow_yen - actual_yen, 2),
        "stop_hit": exit_reason == "stop_hit",
    }


@dataclass
class _BucketStats:
    target_count: int = 0
    block_count: int = 0
    actual_pnl: float = 0.0
    shadow_pnl: float = 0.0
    blocked_winners: int = 0
    blocked_losers: int = 0
    flat_blocks: int = 0
    overheat_blocks: int = 0
    overlap_with_rise5_shadow: int = 0


@dataclass
class PbV2FlatBandShadowCounters:
    pbv2_flat_band_shadow_target_count: int = 0
    pbv2_flat_band_shadow_block_count: int = 0
    pbv2_flat_band_shadow_kept_count: int = 0
    actual_total_pnl_yen_100: float = 0.0
    shadow_total_pnl_yen_100: float = 0.0
    skipped_trade_pnl_actual: float = 0.0
    blocked_winners: int = 0
    blocked_losers: int = 0
    flat_blocks: int = 0
    overheat_blocks: int = 0
    overlap_with_rise5_shadow: int = 0
    variant: str = VARIANT_FLAT_PLUS_OVERHEAT
    _buckets: dict[str, _BucketStats] = field(default_factory=dict)

    def _bucket(self, name: str) -> _BucketStats:
        if name not in self._buckets:
            self._buckets[name] = _BucketStats()
        return self._buckets[name]

    def _reason_flags(self, reason: str) -> tuple[bool, bool]:
        r = str(reason or "")
        flat = REASON_FLAT_BAND_NARROW in r
        overheat = REASON_OVERHEAT_RISE5 in r
        return flat, overheat

    def record_accept(self, fields: Mapping[str, Any]) -> None:
        applies = shadow_applies_to_trade(
            fields,
            apply_pool=str(fields.get("pbv2_flat_band_shadow_apply_pool") or APPLY_POOL_PBV2_ONLY),
        )
        if not applies:
            return
        self.pbv2_flat_band_shadow_target_count += 1
        bucket = self._bucket(_session_bucket_from_fields(fields))
        bucket.target_count += 1
        blocked = _bool(fields.get("pbv2_flat_band_shadow_block"))
        if blocked:
            self.pbv2_flat_band_shadow_block_count += 1
            bucket.block_count += 1
            reason = str(fields.get("pbv2_flat_band_shadow_reason") or "")
            flat_hit, overheat_hit = self._reason_flags(reason)
            if flat_hit:
                self.flat_blocks += 1
                bucket.flat_blocks += 1
            if overheat_hit:
                self.overheat_blocks += 1
                bucket.overheat_blocks += 1
            if _bool(fields.get("flat_band_and_rise5_shadow_block")):
                self.overlap_with_rise5_shadow += 1
                bucket.overlap_with_rise5_shadow += 1
        else:
            self.pbv2_flat_band_shadow_kept_count += 1

    def record_exit(self, row: Mapping[str, Any]) -> None:
        applies = shadow_applies_to_trade(
            row,
            apply_pool=str(row.get("pbv2_flat_band_shadow_apply_pool") or APPLY_POOL_PBV2_ONLY),
        )
        if not applies:
            return
        delta = _float(row.get("pbv2_flat_band_shadow_delta_yen")) or 0.0
        shadow_yen = _float(row.get("pbv2_flat_band_shadow_pnl_yen_100")) or 0.0
        actual_yen = round(shadow_yen - delta, 2)
        blocked = _bool(row.get("pbv2_flat_band_shadow_block"))
        self.actual_total_pnl_yen_100 = round(self.actual_total_pnl_yen_100 + actual_yen, 2)
        self.shadow_total_pnl_yen_100 = round(self.shadow_total_pnl_yen_100 + shadow_yen, 2)
        bucket = self._bucket(_session_bucket_from_fields(row))
        bucket.actual_pnl = round(bucket.actual_pnl + actual_yen, 2)
        bucket.shadow_pnl = round(bucket.shadow_pnl + shadow_yen, 2)
        if blocked:
            self.skipped_trade_pnl_actual = round(self.skipped_trade_pnl_actual + actual_yen, 2)
            if actual_yen > 0:
                self.blocked_winners += 1
                bucket.blocked_winners += 1
            elif actual_yen < 0:
                self.blocked_losers += 1
                bucket.blocked_losers += 1

    def summary_fields(self) -> dict[str, Any]:
        delta = round(self.shadow_total_pnl_yen_100 - self.actual_total_pnl_yen_100, 2)
        return {
            "pbv2_flat_band_shadow_enabled": True,
            "pbv2_flat_band_variant": self.variant,
            "pbv2_flat_band_shadow_target_count": self.pbv2_flat_band_shadow_target_count,
            "pbv2_flat_band_shadow_block_count": self.pbv2_flat_band_shadow_block_count,
            "pbv2_flat_band_shadow_kept_count": self.pbv2_flat_band_shadow_kept_count,
            "pbv2_flat_band_shadow_actual_total_pnl_yen_100": self.actual_total_pnl_yen_100,
            "pbv2_flat_band_shadow_total_pnl_yen_100": self.shadow_total_pnl_yen_100,
            "pbv2_flat_band_shadow_delta_yen": delta,
            "pbv2_flat_band_shadow_blocked_pnl_yen_100": self.skipped_trade_pnl_actual,
            "pbv2_flat_band_shadow_blocked_winners": self.blocked_winners,
            "pbv2_flat_band_shadow_blocked_losers": self.blocked_losers,
            "pbv2_flat_band_shadow_net_effect_yen": delta,
            "pbv2_flat_band_shadow_improved_vs_actual": delta > 0,
            "pbv2_flat_band_shadow_flat_blocks": self.flat_blocks,
            "pbv2_flat_band_shadow_overheat_blocks": self.overheat_blocks,
            "pbv2_flat_band_shadow_overlap_with_rise5_shadow": self.overlap_with_rise5_shadow,
        }


def build_pbv2_flat_band_shadow_counters(config: Any) -> PbV2FlatBandShadowCounters:
    return PbV2FlatBandShadowCounters(variant=VARIANT_FLAT_PLUS_OVERHEAT)


def format_pbv2_flat_band_shadow_discord_lines(summary: Mapping[str, Any]) -> list[str]:
    if not summary.get("pbv2_flat_band_shadow_enabled"):
        return []
    lines = ["[PBv2 Flat-band Shadow]"]
    lines.append(f"variant: {summary.get('pbv2_flat_band_variant', VARIANT_FLAT_PLUS_OVERHEAT)}")
    lines.append(f"target: {summary.get('pbv2_flat_band_shadow_target_count', 0)}")
    lines.append(f"block_count: {summary.get('pbv2_flat_band_shadow_block_count', 0)}")
    lines.append(
        "blocked W/L: "
        f"{summary.get('pbv2_flat_band_shadow_blocked_winners', 0)}/"
        f"{summary.get('pbv2_flat_band_shadow_blocked_losers', 0)}"
    )
    lines.append(f"net_effect: {summary.get('pbv2_flat_band_shadow_net_effect_yen', 0)}")
    lines.append(f"flat_blocks: {summary.get('pbv2_flat_band_shadow_flat_blocks', 0)}")
    lines.append(f"overheat_blocks: {summary.get('pbv2_flat_band_shadow_overheat_blocks', 0)}")
    lines.append(
        "overlap_with_rise5_shadow: "
        f"{summary.get('pbv2_flat_band_shadow_overlap_with_rise5_shadow', 0)}"
    )
    return lines
