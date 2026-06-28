"""
Phase590: Volume gate (daytrade_suitability) relaxation shadow — logging only.

Production ENTRY decisions remain V100 threshold. V90/V80 are counterfactual shadows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from small_paper.daytrade_suitability import volatility_liquidity_score
from small_paper.daytrade_suitability_gate import REJECT_DAYTRADE_SUITABILITY

RELAXATION_V90 = 0.90
RELAXATION_V80 = 0.80

SHADOW_EVAL_FIELDS = (
    "timestamp",
    "symbol",
    "vol_liq_score",
    "threshold_v100",
    "threshold_v90",
    "threshold_v80",
    "pass_v100",
    "pass_v90",
    "pass_v80",
    "current_reject_reason",
    "shadow_rescue_v90",
    "shadow_rescue_v80",
)

MONITOR_OK = "ok"
MONITOR_WATCH = "watch"
MONITOR_ALERT = "alert"


def shadow_enabled(config: Any) -> bool:
    if not bool(getattr(config, "daytrade_suitability_enabled", False)):
        return False
    return bool(getattr(config, "volume_gate_relaxation_shadow_enabled", True))


def _float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def vol_liq_score_from_trade(trade: Mapping[str, Any]) -> Optional[float]:
    v = _float(trade.get("volatility_liquidity_score"))
    if v is not None:
        return v
    tv = _float(trade.get("trading_value") or trade.get("trading_value_jpy"))
    atr = _float(trade.get("atr_pct"))
    return volatility_liquidity_score(atr, tv)


def compute_volume_shadow_eval(
    *,
    trade: Mapping[str, Any],
    threshold_v100: Optional[float],
    symbol: str,
    timestamp: str,
    current_reject_reason: str = "",
) -> Optional[dict[str, Any]]:
    if threshold_v100 is None or threshold_v100 <= 0:
        return None
    score = vol_liq_score_from_trade(trade)
    th100 = float(threshold_v100)
    th90 = round(th100 * RELAXATION_V90, 6)
    th80 = round(th100 * RELAXATION_V80, 6)
    if score is None:
        return {
            "timestamp": timestamp,
            "symbol": symbol,
            "vol_liq_score": None,
            "threshold_v100": th100,
            "threshold_v90": th90,
            "threshold_v80": th80,
            "pass_v100": False,
            "pass_v90": False,
            "pass_v80": False,
            "current_reject_reason": current_reject_reason or "missing_vol_liq_score",
            "shadow_rescue_v90": False,
            "shadow_rescue_v80": False,
        }
    s = float(score)
    p100 = s >= th100
    p90 = s >= th90
    p80 = s >= th80
    return {
        "timestamp": timestamp,
        "symbol": symbol,
        "vol_liq_score": round(s, 6),
        "threshold_v100": th100,
        "threshold_v90": th90,
        "threshold_v80": th80,
        "pass_v100": p100,
        "pass_v90": p90,
        "pass_v80": p80,
        "current_reject_reason": current_reject_reason or ("pass" if p100 else REJECT_DAYTRADE_SUITABILITY),
        "shadow_rescue_v90": (not p100) and p90,
        "shadow_rescue_v80": (not p100) and p80,
    }


@dataclass
class VolumeGateRelaxationShadowState:
    eval_count: int = 0
    pass_v100_count: int = 0
    pass_v90_count: int = 0
    pass_v80_count: int = 0
    rescue_v90_count: int = 0
    rescue_v80_count: int = 0
    daytrade_reject_count: int = 0
    eval_rows: list[dict[str, Any]] = field(default_factory=list)

    def record(self, row: Mapping[str, Any]) -> None:
        self.eval_count += 1
        if row.get("pass_v100"):
            self.pass_v100_count += 1
        if row.get("pass_v90"):
            self.pass_v90_count += 1
        if row.get("pass_v80"):
            self.pass_v80_count += 1
        if row.get("shadow_rescue_v90"):
            self.rescue_v90_count += 1
        if row.get("shadow_rescue_v80"):
            self.rescue_v80_count += 1
        if str(row.get("current_reject_reason") or "") == REJECT_DAYTRADE_SUITABILITY:
            self.daytrade_reject_count += 1
        self.eval_rows.append(dict(row))


def record_volume_gate_shadow_eval(
    state: VolumeGateRelaxationShadowState,
    *,
    trade: Mapping[str, Any],
    threshold: Optional[float],
    symbol: str,
    timestamp: str,
    reject_reason: str,
) -> Optional[dict[str, Any]]:
    row = compute_volume_shadow_eval(
        trade=trade,
        threshold_v100=threshold,
        symbol=symbol,
        timestamp=timestamp,
        current_reject_reason=reject_reason,
    )
    if row is None:
        return None
    state.record(row)
    return row


def volume_shadow_summary_fields(
    state: Optional[VolumeGateRelaxationShadowState],
    *,
    replay_v90_pnl: Optional[float] = None,
    replay_v80_pnl: Optional[float] = None,
    baseline_pnl: Optional[float] = None,
    replay_v90_big_loser: Optional[int] = None,
    replay_v80_big_loser: Optional[int] = None,
) -> dict[str, Any]:
    if state is None or state.eval_count <= 0:
        return {
            "volume_gate_relaxation_shadow_enabled": False,
            "volume_shadow_monitor_status": MONITOR_WATCH,
        }
    v90_pnl = replay_v90_pnl
    v80_pnl = replay_v80_pnl
    base = baseline_pnl
    status = MONITOR_OK
    if state.rescue_v80_count > state.rescue_v90_count * 2 and state.eval_count > 100:
        status = MONITOR_WATCH
    return {
        "volume_gate_relaxation_shadow_enabled": True,
        "volume_shadow_eval_count": state.eval_count,
        "volume_shadow_pass_v100_count": state.pass_v100_count,
        "volume_shadow_pass_v90_count": state.pass_v90_count,
        "volume_shadow_pass_v80_count": state.pass_v80_count,
        "volume_shadow_v90_rescued_count": state.rescue_v90_count,
        "volume_shadow_v80_rescued_count": state.rescue_v80_count,
        "volume_shadow_v90_pnl": v90_pnl,
        "volume_shadow_v80_pnl": v80_pnl,
        "volume_shadow_v90_delta": (
            round(float(v90_pnl) - float(base), 2) if v90_pnl is not None and base is not None else None
        ),
        "volume_shadow_v80_delta": (
            round(float(v80_pnl) - float(base), 2) if v80_pnl is not None and base is not None else None
        ),
        "volume_shadow_v90_big_loser": replay_v90_big_loser,
        "volume_shadow_v80_big_loser": replay_v80_big_loser,
        "volume_shadow_monitor_status": status,
        "rejected_by_daytrade_suitability_shadow_note": (
            "production V100 only; V90/V80 are shadow thresholds"
        ),
    }
