"""
Phase355: Production ENTRY guard — pullback misread on Dynamic40 only.

Reject when universe is Dynamic40 AND:
  entry_rise_5min_pct < 0 AND entry_vwap_dev_pct < 0
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from small_paper.pullback_misread_entry_guard_shadow import would_block_pullback_misread_guard

REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD = "pullback_misread_dynamic40_guard"
LOG_EVENT_KIND = "pullback_misread_dynamic40_guard_triggered"

DYNAMIC40_SOURCE_BUCKETS = frozenset(
    {
        "vol_liq_dynamic40",
        "dynamic40",
        "am_vol_liq_dynamic50",
        "pm_vol_liq_dynamic50",
        "opening_dynamic50",
    }
)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def is_dynamic40_universe(trade: Mapping[str, Any]) -> bool:
    """True only for Dynamic40 slot — Core10 must never match."""
    slot = str(trade.get("universe_slot") or "").lower()
    if slot == "core":
        return False
    if slot == "dynamic":
        return True
    bucket = str(
        trade.get("universe_bucket") or trade.get("source_bucket") or ""
    ).lower()
    if bucket in DYNAMIC40_SOURCE_BUCKETS:
        return True
    if bucket == "dynamic40":
        return True
    return False


def attach_universe_fields(trade: dict[str, Any], meta: Mapping[str, Any]) -> None:
    if not meta:
        return
    slot = str(meta.get("universe_slot") or "")
    bucket = str(meta.get("source_bucket") or "")
    trade["universe_slot"] = slot
    trade["source_bucket"] = bucket
    trade["universe_bucket"] = "dynamic40" if slot == "dynamic" else (
        "core10" if slot == "core" else bucket
    )


def load_symbol_universe_meta(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {str(r.get("symbol") or ""): dict(r) for r in csv.DictReader(f)}


def compute_pullback_misread_guard_fields(trade: Mapping[str, Any]) -> dict[str, Any]:
    rise5 = _float(trade.get("entry_rise_5min_pct"))
    vwap_dev = _float(trade.get("entry_vwap_dev_pct"))
    dyn40 = is_dynamic40_universe(trade)
    cond = would_block_pullback_misread_guard(
        {"entry_rise_5min_pct": rise5, "entry_vwap_dev_pct": vwap_dev}
    )
    blocked = dyn40 and cond
    return {
        "entry_rise_5min_pct": rise5,
        "entry_vwap_dev_pct": vwap_dev,
        "pullback_misread_dynamic40_guard_candidate": bool(cond),
        "pullback_misread_dynamic40_guard_blocked": blocked,
        "pullback_misread_guard_shadow_blocked": blocked,
        "universe_slot": trade.get("universe_slot"),
        "universe_bucket": trade.get("universe_bucket"),
    }


@dataclass
class PullbackMisreadDynamic40GuardConfig:
    enabled: bool = True


@dataclass
class PullbackMisreadDynamic40GuardCheck:
    blocked: bool
    entry_rise_5min_pct: Optional[float] = None
    entry_vwap_dev_pct: Optional[float] = None
    universe_slot: str = ""
    universe_bucket: str = ""
    reject_reason: str = ""

    def log_fields(self, *, symbol: str) -> dict[str, Any]:
        return {
            "event_kind": LOG_EVENT_KIND,
            "symbol": symbol,
            "entry_rise_5min_pct": self.entry_rise_5min_pct,
            "entry_vwap_dev_pct": self.entry_vwap_dev_pct,
            "universe_slot": self.universe_slot,
            "universe_bucket": self.universe_bucket,
            "reject_reason": self.reject_reason or REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD,
        }


@dataclass
class PullbackMisreadDynamic40GuardState:
    config: PullbackMisreadDynamic40GuardConfig
    reject_count: int = 0
    rejected_symbols: set[str] = field(default_factory=set)

    def summary_fields(self) -> dict[str, Any]:
        return {
            "pullback_misread_dynamic40_guard_enabled": self.config.enabled,
            "pullback_misread_dynamic40_reject_count": self.reject_count,
            "pullback_misread_dynamic40_reject_symbols": sorted(self.rejected_symbols),
        }

    def check(self, trade: Mapping[str, Any]) -> PullbackMisreadDynamic40GuardCheck:
        if not self.config.enabled:
            return PullbackMisreadDynamic40GuardCheck(blocked=False)

        if not is_dynamic40_universe(trade):
            return PullbackMisreadDynamic40GuardCheck(
                blocked=False,
                entry_rise_5min_pct=_float(trade.get("entry_rise_5min_pct")),
                entry_vwap_dev_pct=_float(trade.get("entry_vwap_dev_pct")),
                universe_slot=str(trade.get("universe_slot") or ""),
                universe_bucket=str(trade.get("universe_bucket") or ""),
            )

        rise5 = _float(trade.get("entry_rise_5min_pct"))
        vwap_dev = _float(trade.get("entry_vwap_dev_pct"))
        blocked = would_block_pullback_misread_guard(
            {"entry_rise_5min_pct": rise5, "entry_vwap_dev_pct": vwap_dev}
        )
        return PullbackMisreadDynamic40GuardCheck(
            blocked=blocked,
            entry_rise_5min_pct=rise5,
            entry_vwap_dev_pct=vwap_dev,
            universe_slot=str(trade.get("universe_slot") or ""),
            universe_bucket=str(trade.get("universe_bucket") or ""),
            reject_reason=REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD if blocked else "",
        )


def config_from_pilot(pilot_config: Any) -> PullbackMisreadDynamic40GuardConfig:
    return PullbackMisreadDynamic40GuardConfig(
        enabled=bool(
            getattr(pilot_config, "enable_pullback_misread_dynamic40_guard", True)
        ),
    )


def build_pullback_misread_dynamic40_guard_state(
    pilot_config: Any,
) -> Optional[PullbackMisreadDynamic40GuardState]:
    cfg = config_from_pilot(pilot_config)
    if not cfg.enabled:
        return None
    return PullbackMisreadDynamic40GuardState(config=cfg)


def resolve_universe_meta_path(
    *,
    day_compact: str,
    session_kind: str,
    reports_dir: Path,
    universe_csv_path: Optional[str] = None,
) -> Optional[Path]:
    if universe_csv_path:
        p = Path(universe_csv_path)
        if p.is_file():
            return p
    if session_kind == "pm":
        pm = reports_dir / f"universe_core10_dynamic40_price_risk_pm_refresh1430_{day_compact}.csv"
        if pm.is_file():
            return pm
    am = reports_dir / f"universe_core10_dynamic40_price_risk_am_refresh1000_{day_compact}.csv"
    if am.is_file():
        return am
    return None
