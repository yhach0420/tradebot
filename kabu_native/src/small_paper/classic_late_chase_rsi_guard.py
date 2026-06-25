"""
Phase503: Production ENTRY guard — late_chase_cluster AND RSI14 >= threshold.

Adopts Phase502 guard C (late_chase AND rsi_over80) as runtime ENTRY reject.
late_chase_flag matches phase493 late_chase_after_rally_vwap_trap cluster with
fixed loser medians from PBv2 audit period 20260529–20260622.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

REJECT_CLASSIC_LATE_CHASE_RSI_OVER80 = "classic_late_chase_rsi_over80"
LOG_EVENT_KIND = "classic_late_chase_rsi_guard_triggered"

# Phase493 loser medians (PBv2 replay 20260529–20260622), fixed for runtime fidelity.
LATE_CHASE_CLUSTER_MEDIANS: dict[str, float] = {
    "r10": 0.88965,
    "r15_minus_r5": 0.0,
    "r30_minus_r5": 0.1468,
    "vwap_dev_pct": -0.1324,
}

RSI_PERIOD = 14
DEFAULT_RSI_THRESHOLD = 80.0


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _resample_1m_closes(
    series: Sequence[tuple[float, float]],
    *,
    until: float,
) -> list[float]:
    """Resample monotonic epoch-second ticks to 1m closes (live price ring format)."""
    if not series:
        return []
    origin = series[0][0]
    bars: dict[int, float] = {}
    for ts, px in series:
        if ts > until:
            break
        if px <= 0:
            continue
        minute_key = int((ts - origin) // 60)
        bars[minute_key] = px
    return [bars[k] for k in sorted(bars.keys())]


def _rsi14(closes: Sequence[float], period: int = RSI_PERIOD) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas[-period:]]
    losses = [max(-d, 0.0) for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss <= 1e-12:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 6)


def compute_rsi14_at_entry(
    price_ring: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
) -> Optional[float]:
    closes = _resample_1m_closes(price_ring, until=entry_ts)
    return _rsi14(closes)


def compute_late_chase_flag(
    trade: Mapping[str, Any],
    *,
    medians: Mapping[str, float] | None = None,
) -> bool:
    med = medians or LATE_CHASE_CLUSTER_MEDIANS
    r5 = _float(trade.get("entry_rise_5min_pct"))
    r10 = _float(trade.get("entry_rise_10min_pct"))
    r15 = _float(trade.get("entry_rise_15min_pct"))
    r30 = _float(trade.get("entry_rise_30min_pct"))
    vwap_dev = _float(trade.get("entry_vwap_dev_pct"))
    r15m5 = (r15 - r5) if r15 is not None and r5 is not None else None
    r30m5 = (r30 - r5) if r30 is not None and r5 is not None else None

    rally = (
        (r10 is not None and r10 > med.get("r10", 0.0))
        or (r30m5 is not None and r30m5 > med.get("r30_minus_r5", 0.0))
        or (r15m5 is not None and r15m5 > med.get("r15_minus_r5", 0.0))
    )
    return bool(
        rally
        and vwap_dev is not None
        and vwap_dev > med.get("vwap_dev_pct", 0.0)
    )


def would_block_classic_late_chase_rsi_guard(
    trade: Mapping[str, Any],
    *,
    threshold: float = DEFAULT_RSI_THRESHOLD,
) -> bool:
    late_chase = trade.get("late_chase_flag")
    if late_chase is None:
        late_chase = compute_late_chase_flag(trade)
    rsi14 = _float(trade.get("rsi14"))
    if not late_chase or rsi14 is None:
        return False
    return rsi14 >= threshold


def compute_classic_late_chase_rsi_guard_fields(
    trade: Mapping[str, Any],
    *,
    price_ring: Sequence[tuple[float, float]] | None = None,
    entry_ts: float | None = None,
    threshold: float = DEFAULT_RSI_THRESHOLD,
    enabled: bool = True,
) -> dict[str, Any]:
    rsi14 = _float(trade.get("rsi14"))
    if rsi14 is None and price_ring is not None and entry_ts is not None:
        rsi14 = compute_rsi14_at_entry(price_ring, entry_ts=entry_ts)

    late_chase_flag = trade.get("late_chase_flag")
    if late_chase_flag is None:
        late_chase_flag = compute_late_chase_flag(trade)
    else:
        late_chase_flag = bool(late_chase_flag)

    rsi_over80: Optional[bool] = None
    if rsi14 is not None:
        rsi_over80 = rsi14 >= threshold

    candidate = would_block_classic_late_chase_rsi_guard(trade={"rsi14": rsi14, "late_chase_flag": late_chase_flag}, threshold=threshold)
    blocked = bool(enabled and candidate)
    guard_pass = not blocked if enabled else True

    return {
        "rsi14": rsi14,
        "rsi_over80": rsi_over80,
        "late_chase_flag": late_chase_flag,
        "classic_late_chase_rsi_guard_pass": guard_pass,
        "classic_late_chase_rsi_guard_candidate": bool(candidate),
        "classic_late_chase_rsi_guard_blocked": blocked,
        "classic_late_chase_rsi_threshold": threshold,
    }


@dataclass
class ClassicLateChaseRsiGuardConfig:
    enabled: bool = False
    rsi_threshold: float = DEFAULT_RSI_THRESHOLD


@dataclass
class ClassicLateChaseRsiGuardCheck:
    blocked: bool
    rsi14: Optional[float] = None
    rsi_over80: Optional[bool] = None
    late_chase_flag: bool = False
    reject_reason: str = ""

    def log_fields(self, *, symbol: str) -> dict[str, Any]:
        return {
            "event_kind": LOG_EVENT_KIND,
            "symbol": symbol,
            "rsi14": self.rsi14,
            "late_chase_flag": self.late_chase_flag,
            "reject_reason": self.reject_reason or REJECT_CLASSIC_LATE_CHASE_RSI_OVER80,
        }


@dataclass
class ClassicLateChaseRsiGuardState:
    config: ClassicLateChaseRsiGuardConfig
    reject_count: int = 0
    rejected_symbols: set[str] = field(default_factory=set)

    def summary_fields(self) -> dict[str, Any]:
        return {
            "classic_late_chase_rsi_guard_enabled": self.config.enabled,
            "classic_late_chase_rsi_threshold": self.config.rsi_threshold,
            "classic_late_chase_rsi_over80": self.reject_count,
            "classic_late_chase_rsi_reject_count": self.reject_count,
            "classic_late_chase_rsi_reject_symbols": sorted(self.rejected_symbols),
        }

    def check(self, trade: Mapping[str, Any]) -> ClassicLateChaseRsiGuardCheck:
        rsi14 = _float(trade.get("rsi14"))
        late_chase_flag = bool(trade.get("late_chase_flag"))
        rsi_over80 = rsi14 is not None and rsi14 >= self.config.rsi_threshold

        if not self.config.enabled:
            return ClassicLateChaseRsiGuardCheck(
                blocked=False,
                rsi14=rsi14,
                rsi_over80=rsi_over80 if rsi14 is not None else None,
                late_chase_flag=late_chase_flag,
            )

        blocked = late_chase_flag and rsi14 is not None and rsi14 >= self.config.rsi_threshold
        return ClassicLateChaseRsiGuardCheck(
            blocked=blocked,
            rsi14=rsi14,
            rsi_over80=rsi_over80 if rsi14 is not None else None,
            late_chase_flag=late_chase_flag,
            reject_reason=REJECT_CLASSIC_LATE_CHASE_RSI_OVER80 if blocked else "",
        )


def config_from_pilot(pilot_config: Any) -> ClassicLateChaseRsiGuardConfig:
    return ClassicLateChaseRsiGuardConfig(
        enabled=bool(getattr(pilot_config, "classic_late_chase_rsi_guard_enabled", False)),
        rsi_threshold=float(
            getattr(pilot_config, "classic_late_chase_rsi_threshold", DEFAULT_RSI_THRESHOLD)
            or DEFAULT_RSI_THRESHOLD
        ),
    )


def build_classic_late_chase_rsi_guard_state(
    pilot_config: Any,
) -> Optional[ClassicLateChaseRsiGuardState]:
    cfg = config_from_pilot(pilot_config)
    if not cfg.enabled:
        return None
    return ClassicLateChaseRsiGuardState(config=cfg)
