"""
Phase 153b: Shadow entry gate for low price / high tick-ratio risk (review only).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from research.low_price_risk_review import jpx_tick_size_yen, tick_ratio_pct
from research.research_exit_criteria import _as_float

REJECT_ENTRY_PRICE_RISK_GUARD = "entry_price_risk_guard"
APPLY_MODE_REJECT_ENTRY = "reject_entry"
LOG_EVENT_KIND = "entry_price_risk_guard_triggered"


@dataclass
class EntryPriceRiskGuardConfig:
    enabled: bool = False
    min_entry_price: float = 50.0
    max_tick_ratio_pct: float = 5.0
    apply_mode: str = APPLY_MODE_REJECT_ENTRY
    shadow_only: bool = True


@dataclass
class EntryPriceRiskGuardCheck:
    blocked: bool
    current_price: float
    tick_size_yen: float
    tick_ratio_pct: float
    min_entry_price: float
    max_tick_ratio_pct: float
    reject_reason: str = ""
    trigger: str = ""
    price_source: str = ""
    shadow_missing_price_bypassed: bool = False
    universe_close_price_used: bool = False

    def log_fields(self, *, symbol: str) -> dict[str, Any]:
        return {
            "event_kind": LOG_EVENT_KIND,
            "symbol": symbol,
            "current_price": self.current_price,
            "tick_size": self.tick_size_yen,
            "tick_ratio_pct": self.tick_ratio_pct,
            "min_entry_price": self.min_entry_price,
            "max_tick_ratio_pct": self.max_tick_ratio_pct,
            "reject_reason": self.reject_reason or REJECT_ENTRY_PRICE_RISK_GUARD,
            "trigger": self.trigger,
            "price_source": self.price_source,
            "shadow_missing_price_bypassed": self.shadow_missing_price_bypassed,
            "universe_close_price_used": self.universe_close_price_used,
        }


@dataclass
class EntryPriceRiskGuardState:
    config: EntryPriceRiskGuardConfig
    reject_count: int = 0

    def summary_fields(self) -> dict[str, Any]:
        return {
            "entry_price_risk_guard_enabled": self.config.enabled,
            "entry_price_risk_guard_shadow": self.config.shadow_only,
            "entry_price_risk_guard_min_entry_price": self.config.min_entry_price,
            "entry_price_risk_guard_max_tick_ratio_pct": self.config.max_tick_ratio_pct,
            "rejected_by_entry_price_risk_guard": self.reject_count,
        }

    def check(self, trade: Mapping[str, Any]) -> EntryPriceRiskGuardCheck:
        cfg = self.config
        if not cfg.enabled:
            return EntryPriceRiskGuardCheck(
                blocked=False,
                current_price=0.0,
                tick_size_yen=0.0,
                tick_ratio_pct=0.0,
                min_entry_price=cfg.min_entry_price,
                max_tick_ratio_pct=cfg.max_tick_ratio_pct,
            )

        price, source, used_close = _entry_price_from_trade(trade)
        if price <= 0:
            # Phase168: in shadow-only mode, "missing_price" should not hard-reject
            # legitimate candidates (live stream may temporarily omit price fields).
            bypass = bool(cfg.shadow_only)
            return EntryPriceRiskGuardCheck(
                blocked=not bypass,
                current_price=price,
                tick_size_yen=0.0,
                tick_ratio_pct=0.0,
                min_entry_price=cfg.min_entry_price,
                max_tick_ratio_pct=cfg.max_tick_ratio_pct,
                reject_reason=REJECT_ENTRY_PRICE_RISK_GUARD if not bypass else "",
                trigger="missing_price",
                price_source=source,
                shadow_missing_price_bypassed=bypass,
                universe_close_price_used=used_close,
            )

        tick = jpx_tick_size_yen(price)
        tr = tick_ratio_pct(price)
        blocked = False
        trigger = ""
        if price < cfg.min_entry_price:
            blocked = True
            trigger = "price_below_min"
        elif tr > cfg.max_tick_ratio_pct:
            blocked = True
            trigger = "tick_ratio_above_max"

        return EntryPriceRiskGuardCheck(
            blocked=blocked,
            current_price=round(price, 4),
            tick_size_yen=tick,
            tick_ratio_pct=tr,
            min_entry_price=cfg.min_entry_price,
            max_tick_ratio_pct=cfg.max_tick_ratio_pct,
            reject_reason=REJECT_ENTRY_PRICE_RISK_GUARD if blocked else "",
            trigger=trigger,
            price_source=source,
            shadow_missing_price_bypassed=False,
            universe_close_price_used=used_close,
        )


def _entry_price_from_trade(trade: Mapping[str, Any]) -> tuple[float, str, bool]:
    """
    Return (price, source, used_close_price).

    Phase168 policy:
      1) live trade/current payload current_price
      2) live payload CurrentPrice
      3) candidate entry_price
      4) universe CSV close_price (fallback)
      5) missing_price
    """
    # 1) and 2)
    for key in ("current_price", "CurrentPrice"):
        v = _as_float(trade.get(key))
        if v and v > 0:
            return float(v), key, False
    # 3)
    v = _as_float(trade.get("entry_price"))
    if v and v > 0:
        return float(v), "entry_price", False
    # 4) universe close_price (often string)
    v = _as_float(trade.get("close_price"))
    if v and v > 0:
        return float(v), "close_price", True
    return 0.0, "", False


def config_from_pilot(pilot_config: Any) -> EntryPriceRiskGuardConfig:
    return EntryPriceRiskGuardConfig(
        enabled=bool(getattr(pilot_config, "entry_price_risk_guard_enabled", False)),
        min_entry_price=float(getattr(pilot_config, "entry_price_risk_guard_min_entry_price", 50.0)),
        max_tick_ratio_pct=float(
            getattr(pilot_config, "entry_price_risk_guard_max_tick_ratio_pct", 5.0)
        ),
        apply_mode=str(
            getattr(pilot_config, "entry_price_risk_guard_apply_mode", APPLY_MODE_REJECT_ENTRY)
        ),
        shadow_only=bool(getattr(pilot_config, "entry_price_risk_guard_shadow", False)),
    )


def build_entry_price_risk_guard_state(pilot_config: Any) -> Optional[EntryPriceRiskGuardState]:
    cfg = config_from_pilot(pilot_config)
    if not cfg.enabled:
        return None
    return EntryPriceRiskGuardState(config=cfg)
