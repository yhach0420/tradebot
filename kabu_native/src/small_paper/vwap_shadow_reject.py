"""
Phase186: VWAP shadow reject candidate logging (no hard reject).

Fixed threshold from Phase185 review — entry_vwap_dev_pct >= 2.5%.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from small_paper.extended_entry_shadow import VWAP_DEV_PCT_MIN

VWAP_SHADOW_REJECT_MIN = VWAP_DEV_PCT_MIN  # 2.5 — fixed; not tuned per session

VWAP_SHADOW_FIELD_KEYS = (
    "vwap_shadow_reject_candidate",
    "vwap_shadow_reject_reason",
    "entry_vwap_dev_pct",
)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def entry_vwap_dev_pct_from_payload(entry_px: float, payload: Mapping[str, Any]) -> Optional[float]:
    vwap = _float(payload.get("VWAP"))
    if vwap and vwap > 0 and entry_px > 0:
        return round((entry_px - vwap) / vwap * 100.0, 4)
    return None


def vwap_shadow_reject_enabled(config: Any) -> bool:
    return bool(getattr(config, "vwap_shadow_reject_enabled", True))


def compute_vwap_shadow_reject_fields(
    *,
    payload: Mapping[str, Any],
    entry_px: float,
    entry_vwap_dev_pct: Optional[float] = None,
) -> dict[str, Any]:
    """Shadow-only VWAP reject candidate at accept (does not block entry)."""
    dev = entry_vwap_dev_pct
    if dev is None:
        dev = entry_vwap_dev_pct_from_payload(entry_px, payload)
    candidate = dev is not None and dev >= VWAP_SHADOW_REJECT_MIN
    reason = "vwap_dev_ge_2p5" if candidate else ""
    return {
        "vwap_shadow_reject_candidate": candidate,
        "vwap_shadow_reject_reason": reason,
        "entry_vwap_dev_pct": dev,
    }


def enrich_exit_vwap_shadow_fields(
    entry_vwap_shadow: Mapping[str, Any],
    *,
    pnl_pct: float,
    exit_reason: str,
) -> dict[str, Any]:
    """Merge entry VWAP shadow flags with exit outcome (logging only)."""
    return {
        "vwap_shadow_reject_candidate": bool(entry_vwap_shadow.get("vwap_shadow_reject_candidate")),
        "vwap_shadow_reject_reason": entry_vwap_shadow.get("vwap_shadow_reject_reason", ""),
        "entry_vwap_dev_pct": entry_vwap_shadow.get("entry_vwap_dev_pct"),
        "pnl_pct": round(float(pnl_pct), 4),
        "stop_hit": exit_reason == "stop_hit",
        "trailing_mfe_exit": exit_reason == "trailing_mfe_exit",
    }


@dataclass
class VwapShadowRejectCounters:
    vwap_shadow_reject_candidate_count: int = 0
    vwap_shadow_candidate_total_pnl: float = 0.0
    _candidate_win_pnl: float = 0.0
    _candidate_loss_pnl: float = 0.0
    vwap_shadow_candidate_stop_hit_count: int = 0
    vwap_shadow_candidate_trailing_mfe_count: int = 0

    def record_accept(self, fields: Mapping[str, Any]) -> None:
        if fields.get("vwap_shadow_reject_candidate"):
            self.vwap_shadow_reject_candidate_count += 1

    def record_exit(self, row: Mapping[str, Any]) -> None:
        if not row.get("vwap_shadow_reject_candidate"):
            return
        pnl = _float(row.get("pnl_pct")) or 0.0
        self.vwap_shadow_candidate_total_pnl = round(
            self.vwap_shadow_candidate_total_pnl + pnl, 4
        )
        if pnl > 0:
            self._candidate_win_pnl = round(self._candidate_win_pnl + pnl, 4)
        elif pnl < 0:
            self._candidate_loss_pnl = round(self._candidate_loss_pnl + pnl, 4)
        reason = str(row.get("exit_reason") or "")
        if bool(row.get("stop_hit")) or reason == "stop_hit":
            self.vwap_shadow_candidate_stop_hit_count += 1
        if bool(row.get("trailing_mfe_exit")) or reason == "trailing_mfe_exit":
            self.vwap_shadow_candidate_trailing_mfe_count += 1

    def summary_fields(self) -> dict[str, Any]:
        gl = abs(self._candidate_loss_pnl)
        if gl <= 0:
            pf: Optional[float] = None if self._candidate_win_pnl <= 0 else float("inf")
        else:
            pf = round(self._candidate_win_pnl / gl, 4)
        return {
            "vwap_shadow_reject_candidate_count": self.vwap_shadow_reject_candidate_count,
            "vwap_shadow_candidate_total_pnl": self.vwap_shadow_candidate_total_pnl,
            "vwap_shadow_candidate_pf": pf,
            "vwap_shadow_candidate_stop_hit_count": self.vwap_shadow_candidate_stop_hit_count,
            "vwap_shadow_candidate_trailing_mfe_count": self.vwap_shadow_candidate_trailing_mfe_count,
        }
