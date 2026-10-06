"""Arm economic gate, X14 vs BASE, CASE A-E. No threshold search."""
from __future__ import annotations

from typing import Any

from research.am_entry_information_expansion import (
    ARM_BASE_LOGIT,
    ARM_BASE_RF,
    ARM_X14_LOGIT,
    ARM_X14_RF,
)
from research.am_entry_profit_improvement.metrics import _pf_num
from research.am_utility_augment_execution_risk.analyze import preservation_pass


def arm_sort_key(row: dict[str, Any]) -> tuple:
    ext = row.get("EX_TOP3_DAYS_PNL_DELTA")
    exb = row.get("EX_BEST_DAY_PNL_DELTA")
    med = row.get("PAIRED_MEDIAN_DAILY_DELTA")
    pf = _pf_num(row.get("OVERLAY_PF"))
    pf_s = 1e18 if pf == float("inf") else pf
    dd = abs(float(row.get("OVERLAY_MAX_DD") or 0.0))
    net = float(row.get("OVERLAY_NET_PNL") or 0.0)
    return (
        -float(ext) if ext is not None else 1e18,
        -float(exb) if exb is not None else 1e18,
        -float(med) if med is not None else 1e18,
        -pf_s,
        dd,
        -net,
        str(row.get("architecture_id") or ""),
    )


def pick_best(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return sorted(rows, key=arm_sort_key)[0]


def net_and_pf_beat_current(arm: dict[str, Any], current_net: float, current_pf: float) -> bool:
    return float(arm.get("OVERLAY_NET_PNL") or 0.0) > float(current_net) and _pf_num(arm.get("OVERLAY_PF")) > float(current_pf)


def expansion_delta(x14: dict[str, Any] | None, base: dict[str, Any] | None) -> dict[str, Any]:
    if not x14 or not base:
        return {
            "DELTA_NET_X14_VS_BASE": None,
            "DELTA_PF_X14_VS_BASE": None,
            "DELTA_FILL_X14_VS_BASE": None,
            "DELTA_PAIRED_MEDIAN_X14_VS_BASE": None,
        }

    def _d(a: Any, b: Any) -> Any:
        try:
            return float(a) - float(b)
        except (TypeError, ValueError):
            return None

    return {
        "DELTA_NET_X14_VS_BASE": _d(x14.get("OVERLAY_NET_PNL"), base.get("OVERLAY_NET_PNL")),
        "DELTA_PF_X14_VS_BASE": _d(_pf_num(x14.get("OVERLAY_PF")), _pf_num(base.get("OVERLAY_PF"))),
        "DELTA_FILL_X14_VS_BASE": _d(x14.get("AUGMENT_FILL_N"), base.get("AUGMENT_FILL_N")),
        "DELTA_PAIRED_MEDIAN_X14_VS_BASE": _d(x14.get("PAIRED_MEDIAN_DAILY_DELTA"), base.get("PAIRED_MEDIAN_DAILY_DELTA")),
    }


def decide_case(
    *,
    integrity_ok: bool,
    preservation_all: bool,
    arms: list[dict[str, Any]],
    current_net: float,
    current_pf: float,
) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "CASE": "E",
            "VERDICT": "AM_ENTRY_INFORMATION_EXPANSION_INTEGRITY_FAILED",
            "NEXT": "STOP",
        }
    by = {str(a.get("architecture_id")): a for a in arms}
    x14s = [by.get(ARM_X14_LOGIT), by.get(ARM_X14_RF)]
    bases = [by.get(ARM_BASE_LOGIT), by.get(ARM_BASE_RF)]
    x14_pass = any(bool(a.get("ARM_PASS")) for a in x14s if a)
    base_pass = any(bool(a.get("ARM_PASS")) for a in bases if a)
    x14_econ = any(net_and_pf_beat_current(a, current_net, current_pf) for a in x14s if a)
    any_econ = any(net_and_pf_beat_current(a, current_net, current_pf) for a in arms)
    if x14_pass:
        return {
            "CASE": "A",
            "VERDICT": "AM_EXPANDED_PROFITABLE_FILL_AUGMENT_SUPPORTED",
            "NEXT": "AM_EXPANDED_ENTRY_POLICY_FREEZE_REVIEW",
        }
    if base_pass:
        return {
            "CASE": "B",
            "VERDICT": "AM_PROFITABLE_FILL_TARGET_SUPPORTED_WITHOUT_EXPANSION",
            "NEXT": "AM_PROFITABLE_FILL_POLICY_FREEZE_REVIEW",
        }
    if x14_econ:
        return {
            "CASE": "C",
            "VERDICT": "AM_EXPANDED_ENTRY_PARTIAL_ECONOMIC_EDGE",
            "NEXT": "AM_EXPANDED_ENTRY_RISK_INTEGRATION_REASSESSMENT",
        }
    if not any_econ:
        return {
            "CASE": "D",
            "VERDICT": "AM_PROFITABLE_FILL_INFORMATION_EXPANSION_NOT_ECONOMIC",
            "NEXT": "AM_RAW_EVENT_INCREMENTAL_SELECTION_REASSESSMENT",
        }
    return {
        "CASE": "C",
        "VERDICT": "AM_EXPANDED_ENTRY_PARTIAL_ECONOMIC_EDGE",
        "NEXT": "AM_EXPANDED_ENTRY_RISK_INTEGRATION_REASSESSMENT",
    }


__all__ = ["arm_sort_key", "pick_best", "expansion_delta", "decide_case", "preservation_pass", "net_and_pf_beat_current"]
