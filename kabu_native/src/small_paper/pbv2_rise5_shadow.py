"""
Phase635: PBv2-only entry_rise_5min_pct shadow guard (no ENTRY block).

Records counterfactual outcomes if PBv2 accepted trades with rise5 above threshold
had been blocked. OR accepted trades are never evaluated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from small_paper.or_overlay_cap import ENTRY_TYPE_OR, entry_type_from_trade

APPLY_POOL_PBV2_ONLY = "PBV2_ONLY"
SHADOW_REASON_RISE5_ABOVE_THRESHOLD = "entry_rise_5min_pct_above_threshold"

ENTRY_FIELD_KEYS = (
    "pbv2_rise5_shadow_block",
    "pbv2_rise5_shadow_reason",
    "pbv2_rise5_value",
    "pbv2_rise5_threshold",
    "pbv2_rise5_shadow_apply_pool",
)

EXIT_EXTRA_FIELD_KEYS = (
    "shadow_blocked_pnl_yen_100",
    "shadow_blocked_mfe",
    "shadow_blocked_mae",
    "pbv2_rise5_shadow_pnl_yen_100",
    "pbv2_rise5_shadow_delta_yen",
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


def rise5_shadow_enabled(config: Any) -> bool:
    return bool(getattr(config, "pbv2_rise5_shadow_enabled", False))


def _shadow_threshold(config: Any) -> float:
    return float(getattr(config, "pbv2_rise5_shadow_threshold_pct", 1.84) or 1.84)


def _shadow_apply_pool(config: Any) -> str:
    return str(getattr(config, "pbv2_rise5_shadow_apply_pool", APPLY_POOL_PBV2_ONLY) or APPLY_POOL_PBV2_ONLY)


def shadow_applies_to_trade(trade: Mapping[str, Any], *, apply_pool: str) -> bool:
    pool = str(apply_pool or APPLY_POOL_PBV2_ONLY).strip().upper()
    if pool == APPLY_POOL_PBV2_ONLY:
        return entry_type_from_trade(trade) != ENTRY_TYPE_OR
    return True


def would_block_pbv2_rise5_shadow(
    fields: Mapping[str, Any],
    *,
    threshold: float,
    apply_pool: str = APPLY_POOL_PBV2_ONLY,
) -> bool:
    if "pbv2_rise5_shadow_block" in fields:
        return _bool(fields.get("pbv2_rise5_shadow_block"))
    if not shadow_applies_to_trade(fields, apply_pool=apply_pool):
        return False
    rise5 = _float(
        fields.get("entry_rise_5min_pct")
        if fields.get("pbv2_rise5_value") is None
        else fields.get("pbv2_rise5_value")
    )
    if rise5 is None:
        return False
    return float(rise5) > float(threshold)


def compute_pbv2_rise5_shadow_fields(config: Any, trade: Mapping[str, Any]) -> dict[str, Any]:
    """Shadow-only rise5 cap at PBv2 accept (does not block actual entry)."""
    if not rise5_shadow_enabled(config):
        return {
            "pbv2_rise5_shadow_block": False,
            "pbv2_rise5_shadow_reason": "",
            "pbv2_rise5_value": _float(trade.get("entry_rise_5min_pct")),
            "pbv2_rise5_threshold": _shadow_threshold(config),
            "pbv2_rise5_shadow_apply_pool": _shadow_apply_pool(config),
        }
    threshold = _shadow_threshold(config)
    apply_pool = _shadow_apply_pool(config)
    applies = shadow_applies_to_trade(trade, apply_pool=apply_pool)
    rise5 = _float(trade.get("entry_rise_5min_pct"))
    blocked = False
    reason = ""
    if applies:
        if rise5 is None:
            reason = "rise5_missing_fail_open"
        elif float(rise5) > float(threshold):
            blocked = True
            reason = SHADOW_REASON_RISE5_ABOVE_THRESHOLD
    return {
        "pbv2_rise5_shadow_block": blocked,
        "pbv2_rise5_shadow_reason": reason,
        "pbv2_rise5_value": rise5,
        "pbv2_rise5_threshold": threshold,
        "pbv2_rise5_shadow_apply_pool": apply_pool,
        "entry_type": trade.get("entry_type") or "PBV2",
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


def enrich_exit_pbv2_rise5_shadow_fields(
    entry_shadow: Mapping[str, Any],
    *,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
    peak_mfe_pct: Optional[float] = None,
    peak_mae_pct: Optional[float] = None,
) -> dict[str, Any]:
    """Counterfactual shadow PnL if blocked ENTRY had not occurred (logging only)."""
    from replay.pnl_yen import compute_pnl_yen_100

    blocked = _bool(entry_shadow.get("pbv2_rise5_shadow_block"))
    actual_yen = round(compute_pnl_yen_100(entry_price, exit_price), 2)
    shadow_yen = 0.0 if blocked else actual_yen
    mfe = _float(peak_mfe_pct)
    mae = _float(peak_mae_pct)
    return {
        "pbv2_rise5_shadow_block": blocked,
        "pbv2_rise5_shadow_reason": entry_shadow.get("pbv2_rise5_shadow_reason", ""),
        "pbv2_rise5_value": entry_shadow.get("pbv2_rise5_value"),
        "pbv2_rise5_threshold": entry_shadow.get("pbv2_rise5_threshold"),
        "shadow_blocked_pnl_yen_100": actual_yen if blocked else 0.0,
        "shadow_blocked_mfe": mfe if blocked else None,
        "shadow_blocked_mae": mae if blocked else None,
        "pbv2_rise5_shadow_pnl_yen_100": shadow_yen,
        "pbv2_rise5_shadow_delta_yen": round(shadow_yen - actual_yen, 2),
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


@dataclass
class PbV2Rise5ShadowCounters:
    pbv2_rise5_shadow_target_count: int = 0
    pbv2_rise5_shadow_block_count: int = 0
    pbv2_rise5_shadow_kept_count: int = 0
    actual_total_pnl_yen_100: float = 0.0
    shadow_total_pnl_yen_100: float = 0.0
    skipped_trade_pnl_actual: float = 0.0
    blocked_winners: int = 0
    blocked_losers: int = 0
    stop_hit_count_actual: int = 0
    stop_hit_count_shadow: int = 0
    threshold_pct: float = 1.84
    _buckets: dict[str, _BucketStats] = field(default_factory=dict)

    def _bucket(self, name: str) -> _BucketStats:
        if name not in self._buckets:
            self._buckets[name] = _BucketStats()
        return self._buckets[name]

    def record_accept(self, fields: Mapping[str, Any]) -> None:
        applies = shadow_applies_to_trade(
            fields,
            apply_pool=str(fields.get("pbv2_rise5_shadow_apply_pool") or APPLY_POOL_PBV2_ONLY),
        )
        if not applies:
            return
        self.pbv2_rise5_shadow_target_count += 1
        bucket = self._bucket(_session_bucket_from_fields(fields))
        bucket.target_count += 1
        if _bool(fields.get("pbv2_rise5_shadow_block")):
            self.pbv2_rise5_shadow_block_count += 1
            bucket.block_count += 1
        else:
            self.pbv2_rise5_shadow_kept_count += 1

    def record_exit(self, row: Mapping[str, Any]) -> None:
        applies = shadow_applies_to_trade(
            row,
            apply_pool=str(row.get("pbv2_rise5_shadow_apply_pool") or APPLY_POOL_PBV2_ONLY),
        )
        if not applies:
            return
        delta = _float(row.get("pbv2_rise5_shadow_delta_yen")) or 0.0
        shadow_yen = _float(row.get("pbv2_rise5_shadow_pnl_yen_100")) or 0.0
        actual_yen = round(shadow_yen - delta, 2)
        blocked = _bool(row.get("pbv2_rise5_shadow_block"))
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
        if _bool(row.get("stop_hit")):
            self.stop_hit_count_actual += 1
            if not blocked:
                self.stop_hit_count_shadow += 1

    def summary_fields(self) -> dict[str, Any]:
        delta = round(self.shadow_total_pnl_yen_100 - self.actual_total_pnl_yen_100, 2)
        out: dict[str, Any] = {
            "pbv2_rise5_shadow_enabled": True,
            "pbv2_rise5_shadow_threshold_pct": self.threshold_pct,
            "pbv2_rise5_shadow_target_count": self.pbv2_rise5_shadow_target_count,
            "pbv2_rise5_shadow_block_count": self.pbv2_rise5_shadow_block_count,
            "pbv2_rise5_shadow_kept_count": self.pbv2_rise5_shadow_kept_count,
            "pbv2_rise5_shadow_actual_total_pnl_yen_100": self.actual_total_pnl_yen_100,
            "pbv2_rise5_shadow_total_pnl_yen_100": self.shadow_total_pnl_yen_100,
            "pbv2_rise5_shadow_delta_yen": delta,
            "pbv2_rise5_shadow_blocked_pnl_yen_100": self.skipped_trade_pnl_actual,
            "pbv2_rise5_shadow_blocked_winners": self.blocked_winners,
            "pbv2_rise5_shadow_blocked_losers": self.blocked_losers,
            "pbv2_rise5_shadow_net_effect_yen": delta,
            "pbv2_rise5_shadow_improved_vs_actual": delta > 0,
        }
        for name in ("AM", "PM", "lunch", "unknown"):
            b = self._buckets.get(name)
            if b is None or b.target_count == 0:
                continue
            key = name.lower()
            out[f"pbv2_rise5_shadow_{key}_target_count"] = b.target_count
            out[f"pbv2_rise5_shadow_{key}_block_count"] = b.block_count
            out[f"pbv2_rise5_shadow_{key}_delta_yen"] = round(b.shadow_pnl - b.actual_pnl, 2)
            out[f"pbv2_rise5_shadow_{key}_blocked_winners"] = b.blocked_winners
            out[f"pbv2_rise5_shadow_{key}_blocked_losers"] = b.blocked_losers
        return out


def build_pbv2_rise5_shadow_counters(config: Any) -> PbV2Rise5ShadowCounters:
    return PbV2Rise5ShadowCounters(threshold_pct=_shadow_threshold(config))


def format_pbv2_rise5_shadow_discord_lines(summary: Mapping[str, Any]) -> list[str]:
    if not summary.get("pbv2_rise5_shadow_enabled"):
        return []
    lines = ["[PBv2 Rise5 Shadow]"]
    if summary.get("pbv2_rise5_shadow_threshold_pct") is not None:
        lines.append(f"threshold: {summary.get('pbv2_rise5_shadow_threshold_pct')}%")
    lines.append(f"block_count: {summary.get('pbv2_rise5_shadow_block_count', 0)}")
    lines.append(f"blocked_pnl: {summary.get('pbv2_rise5_shadow_blocked_pnl_yen_100', 0)}")
    lines.append(f"net_effect: {summary.get('pbv2_rise5_shadow_net_effect_yen', 0)}")
    return lines
