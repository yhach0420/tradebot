"""
Phase525: Production re-entry RSI guard (E_rsi_gt_60 from Phase524 live validation).

When the most recent exit on the same symbol was stop_hit, the next ENTRY requires
RSI14 > threshold. First entries are unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from small_paper.canonical_summary import is_stop_exit
from small_paper.classic_late_chase_rsi_guard import compute_rsi14_at_entry

REJECT_REENTRY_RSI_GUARD_BELOW60 = "reentry_rsi_guard_below60"
LOG_EVENT_KIND = "reentry_rsi_guard_triggered"

DEFAULT_RSI_THRESHOLD = 60.0


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def resolved_exit_reason(row: Mapping[str, Any]) -> str:
    reason = str(row.get("exit_reason") or "").strip()
    structural = str(row.get("structural_exit_reason") or "").strip()
    if reason == "overlap_replaced_review":
        return structural
    return structural or reason


def is_reentry_after_stop_hit(row: Mapping[str, Any]) -> bool:
    """True when resolved exit is stop_hit (incl. overlap_replaced_review structural stop)."""
    if resolved_exit_reason(row) == "stop_hit":
        return True
    return bool(row.get("stop_hit")) or is_stop_exit(row)


def would_block_reentry_rsi_guard(
    trade: Mapping[str, Any],
    *,
    threshold: float = DEFAULT_RSI_THRESHOLD,
    reentry_after_stop: bool,
) -> bool:
    if not reentry_after_stop:
        return False
    rsi14 = _float(trade.get("rsi14"))
    if rsi14 is None:
        return True
    return rsi14 <= threshold


def compute_reentry_rsi_guard_fields(
    trade: Mapping[str, Any],
    *,
    price_ring: Sequence[tuple[float, float]] | None = None,
    entry_ts: float | None = None,
    threshold: float = DEFAULT_RSI_THRESHOLD,
    enabled: bool = True,
    reentry_after_stop: bool = False,
) -> dict[str, Any]:
    rsi14 = _float(trade.get("rsi14"))
    if rsi14 is None and price_ring is not None and entry_ts is not None:
        rsi14 = compute_rsi14_at_entry(price_ring, entry_ts=entry_ts)

    candidate = would_block_reentry_rsi_guard(
        trade={"rsi14": rsi14},
        threshold=threshold,
        reentry_after_stop=reentry_after_stop,
    )
    blocked = bool(enabled and candidate)
    guard_pass = not blocked if enabled else True

    return {
        "rsi14": rsi14,
        "reentry_rsi_guard_pass": guard_pass,
        "reentry_rsi_guard_candidate": bool(candidate),
        "reentry_rsi_guard_blocked": blocked,
        "reentry_rsi_guard_after_stop": reentry_after_stop,
        "reentry_rsi_guard_threshold": threshold,
    }


@dataclass
class ReentryRsiGuardConfig:
    enabled: bool = False
    rsi_threshold: float = DEFAULT_RSI_THRESHOLD


@dataclass
class ReentryRsiGuardCheck:
    blocked: bool
    rsi14: Optional[float] = None
    is_reentry_after_stop: bool = False
    reject_reason: str = ""

    def log_fields(self, *, symbol: str) -> dict[str, Any]:
        return {
            "event_kind": LOG_EVENT_KIND,
            "symbol": symbol,
            "rsi14": self.rsi14,
            "reentry_after_stop": self.is_reentry_after_stop,
            "reject_reason": self.reject_reason or REJECT_REENTRY_RSI_GUARD_BELOW60,
        }


@dataclass
class ReentryRsiGuardState:
    config: ReentryRsiGuardConfig
    reject_count: int = 0
    rejected_symbols: set[str] = field(default_factory=set)
    symbol_last_exit_was_stop: dict[str, bool] = field(default_factory=dict)

    def summary_fields(self) -> dict[str, Any]:
        return {
            "reentry_rsi_guard_enabled": self.config.enabled,
            "reentry_rsi_guard_threshold": self.config.rsi_threshold,
            "reentry_rsi_guard_reject_count": self.reject_count,
            "reentry_rsi_guard_reject_symbols": sorted(self.rejected_symbols),
        }

    def is_reentry_after_stop(self, symbol: str) -> bool:
        sym = str(symbol or "").strip()
        if not sym:
            return False
        return bool(self.symbol_last_exit_was_stop.get(sym))

    def record_exit(self, row: Mapping[str, Any]) -> None:
        sym = str(row.get("symbol") or "").strip()
        if not sym:
            return
        self.symbol_last_exit_was_stop[sym] = is_reentry_after_stop_hit(row)

    def check(self, trade: Mapping[str, Any]) -> ReentryRsiGuardCheck:
        sym = str(trade.get("symbol") or "")
        reentry_after_stop = self.is_reentry_after_stop(sym)
        rsi14 = _float(trade.get("rsi14"))

        if not self.config.enabled:
            return ReentryRsiGuardCheck(
                blocked=False,
                rsi14=rsi14,
                is_reentry_after_stop=reentry_after_stop,
            )

        if not reentry_after_stop:
            return ReentryRsiGuardCheck(
                blocked=False,
                rsi14=rsi14,
                is_reentry_after_stop=False,
            )

        blocked = rsi14 is None or rsi14 <= self.config.rsi_threshold
        return ReentryRsiGuardCheck(
            blocked=blocked,
            rsi14=rsi14,
            is_reentry_after_stop=True,
            reject_reason=REJECT_REENTRY_RSI_GUARD_BELOW60 if blocked else "",
        )


def config_from_pilot(pilot_config: Any) -> ReentryRsiGuardConfig:
    return ReentryRsiGuardConfig(
        enabled=bool(getattr(pilot_config, "reentry_rsi_guard_enabled", False)),
        rsi_threshold=float(
            getattr(pilot_config, "reentry_rsi_guard_threshold", DEFAULT_RSI_THRESHOLD)
            or DEFAULT_RSI_THRESHOLD
        ),
    )


def build_reentry_rsi_guard_state(pilot_config: Any) -> Optional[ReentryRsiGuardState]:
    cfg = config_from_pilot(pilot_config)
    if not cfg.enabled:
        return None
    return ReentryRsiGuardState(config=cfg)
