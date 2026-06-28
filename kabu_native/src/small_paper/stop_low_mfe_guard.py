"""
Phase557: stop_low_mfe entry guard (G554_022).

PBv2 only. Reject when volume_acceleration_5m > threshold. Missing -> pass (default).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from storage.intraday_recorder import PushMinuteBarBuilder, parse_kabu_time

REJECT_STOP_LOW_MFE_GUARD = "stop_low_mfe_guard"
LOG_EVENT_KIND = "stop_low_mfe_guard_triggered"
DEFAULT_THRESHOLD = 0.009
PHASE557_RUNTIME_VERDICT = "phase557_stop_low_mfe_guard_runtime_ready"


def _float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is_or_entry(trade: Mapping[str, Any]) -> bool:
    et = str(trade.get("entry_type") or trade.get("entry_pool") or "").upper()
    if et in ("OR", "OR_OVERLAY") or "OR" in et:
        return True
    pool = str(trade.get("cap_pool") or trade.get("universe_bucket") or "").upper()
    return pool == "OR" or pool.startswith("OR_")


def volume_acceleration_5m(volumes: Sequence[float]) -> Optional[float]:
    """Causal: uses completed minute volumes ending at entry minute (no future bars)."""
    if len(volumes) < 10:
        return None
    ei = len(volumes) - 1
    recent = sum(float(v) for v in volumes[ei - 4 : ei + 1])
    prior = sum(float(v) for v in volumes[ei - 9 : ei - 4])
    if prior <= 0:
        return None
    return round((recent - prior) / prior, 6)


@dataclass
class StopLowMfeGuardConfig:
    enabled: bool = False
    threshold: float = DEFAULT_THRESHOLD
    missing_policy: str = "pass"
    pbv2_only: bool = True


@dataclass
class StopLowMfeGuardCheck:
    blocked: bool
    volume_acceleration_5m: Optional[float] = None
    threshold: Optional[float] = None
    missing: bool = False
    reject_reason: str = ""

    def log_fields(self, *, symbol: str) -> dict[str, Any]:
        return {
            "event_kind": LOG_EVENT_KIND,
            "symbol": symbol,
            "volume_acceleration_5m": self.volume_acceleration_5m,
            "stop_low_mfe_guard_volume_accel_threshold": self.threshold,
            "missing": self.missing,
            "reject_reason": self.reject_reason or REJECT_STOP_LOW_MFE_GUARD,
        }


@dataclass
class StopLowMfeGuardState:
    config: StopLowMfeGuardConfig
    reject_count: int = 0
    missing_count: int = 0
    rejected_symbols: set[str] = field(default_factory=set)
    blocked_loss_yen: float = 0.0
    blocked_winner_yen: float = 0.0
    blocked_big_winner_count: int = 0
    _builders: dict[str, PushMinuteBarBuilder] = field(default_factory=dict)

    def ingest_push(self, symbol: str, payload: Mapping[str, Any]) -> None:
        builder = self._builders.setdefault(symbol, PushMinuteBarBuilder())
        now = datetime.now()
        from zoneinfo import ZoneInfo

        builder.ingest_push_payload(
            payload,
            recorded_at=parse_kabu_time(payload.get("CurrentPriceTime"), fallback=now.replace(tzinfo=ZoneInfo("Asia/Tokyo"))),
        )

    def reset_session(self) -> None:
        self._builders.clear()

    def _minute_volumes(self, symbol: str) -> list[float]:
        builder = self._builders.get(symbol)
        if builder is None:
            return []
        return builder.snapshot_minute_volumes()

    def compute_volume_acceleration(self, trade: Mapping[str, Any]) -> Optional[float]:
        precomputed = _float(trade.get("volume_acceleration_5m"))
        if precomputed is not None:
            return precomputed
        sym = str(trade.get("symbol") or "")
        return volume_acceleration_5m(self._minute_volumes(sym))

    def check(self, trade: Mapping[str, Any]) -> StopLowMfeGuardCheck:
        cfg = self.config
        if not cfg.enabled:
            return StopLowMfeGuardCheck(blocked=False)
        if cfg.pbv2_only and _is_or_entry(trade):
            return StopLowMfeGuardCheck(blocked=False)
        accel = self.compute_volume_acceleration(trade)
        if accel is None:
            self.missing_count += 1
            if cfg.missing_policy == "reject":
                return StopLowMfeGuardCheck(
                    blocked=True,
                    missing=True,
                    threshold=cfg.threshold,
                    reject_reason=REJECT_STOP_LOW_MFE_GUARD,
                )
            return StopLowMfeGuardCheck(blocked=False, missing=True, threshold=cfg.threshold)
        blocked = accel > cfg.threshold
        return StopLowMfeGuardCheck(
            blocked=blocked,
            volume_acceleration_5m=accel,
            threshold=cfg.threshold,
            reject_reason=REJECT_STOP_LOW_MFE_GUARD if blocked else "",
        )

    def summary_fields(self) -> dict[str, Any]:
        net_shadow = round(-self.blocked_loss_yen - self.blocked_winner_yen, 2)
        return {
            "stop_low_mfe_guard_enabled": self.config.enabled,
            "stop_low_mfe_guard_threshold": self.config.threshold,
            "stop_low_mfe_guard_missing_policy": self.config.missing_policy,
            "stop_low_mfe_guard_pbv2_only": self.config.pbv2_only,
            "stop_low_mfe_guard_reject_count": self.reject_count,
            "stop_low_mfe_guard_missing_count": self.missing_count,
            "stop_low_mfe_guard_blocked_loss": round(self.blocked_loss_yen, 2),
            "stop_low_mfe_guard_blocked_winner": round(self.blocked_winner_yen, 2),
            "stop_low_mfe_guard_blocked_big_winner": self.blocked_big_winner_count,
            "stop_low_mfe_guard_net_shadow": net_shadow,
            "stop_low_mfe_guard_volume_accel_threshold": self.config.threshold,
            "stop_low_mfe_guard_reject_symbols": sorted(self.rejected_symbols),
        }


def config_from_pilot(pilot_config: Any) -> StopLowMfeGuardConfig:
    return StopLowMfeGuardConfig(
        enabled=bool(getattr(pilot_config, "stop_low_mfe_guard_enabled", False)),
        threshold=float(getattr(pilot_config, "stop_low_mfe_guard_threshold", DEFAULT_THRESHOLD)),
        missing_policy=str(getattr(pilot_config, "stop_low_mfe_guard_missing_policy", "pass") or "pass"),
        pbv2_only=bool(getattr(pilot_config, "stop_low_mfe_guard_pbv2_only", True)),
    )


def build_stop_low_mfe_guard_state(pilot_config: Any) -> Optional[StopLowMfeGuardState]:
    cfg = config_from_pilot(pilot_config)
    if not cfg.enabled:
        return None
    return StopLowMfeGuardState(config=cfg)


def compute_stop_low_mfe_guard_fields(
    trade: Mapping[str, Any],
    *,
    guard: StopLowMfeGuardState,
) -> dict[str, Any]:
    accel = guard.compute_volume_acceleration(trade)
    return {
        "volume_acceleration_5m": accel,
        "stop_low_mfe_guard_volume_accel_threshold": guard.config.threshold,
    }
