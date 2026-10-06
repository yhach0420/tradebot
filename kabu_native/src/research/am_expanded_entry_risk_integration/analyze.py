"""Risk-arm gate, concentration, CASE A-E. No threshold search."""
from __future__ import annotations

from typing import Any

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
        str(row.get("risk_arm_id") or ""),
    )


def pick_best(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return sorted(rows, key=arm_sort_key)[0]


def net_and_pf_beat(arm: dict[str, Any], current_net: float, current_pf: float) -> bool:
    return float(arm.get("OVERLAY_NET_PNL") or 0.0) > float(current_net) and _pf_num(arm.get("OVERLAY_PF")) > float(
        current_pf
    )


def dd_nonworse(arm: dict[str, Any], current_dd: float) -> bool:
    return abs(float(arm.get("OVERLAY_MAX_DD") or 0.0)) <= abs(float(current_dd))


def profit_concentration(aug_daily: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(r.get("pnl_yen_100") or 0.0) for r in aug_daily]
    total = float(sum(pnls)) if pnls else 0.0
    ranked = sorted(pnls, reverse=True)
    best = float(ranked[0]) if ranked else 0.0
    top2 = float(sum(ranked[:2])) if ranked else 0.0
    top3 = float(sum(ranked[:3])) if ranked else 0.0
    share_best = (best / total) if total > 0 else None
    share_top2 = (top2 / total) if total > 0 else None
    return {
        "BEST_AUGMENT_DAY_PNL": best,
        "BEST_DAY_SHARE_OF_TOTAL_PROFIT": share_best,
        "TOP2_DAY_SHARE_OF_TOTAL_PROFIT": share_top2,
        "TOTAL_PNL_EX_BEST_AUGMENT_DAY": total - best,
        "TOTAL_PNL_EX_TOP3_AUGMENT_DAYS": total - top3,
    }


def decide_case(
    *,
    integrity_ok: bool,
    arms: list[dict[str, Any]],
    current_net: float,
    current_pf: float,
    current_dd: float,
) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "CASE": "E",
            "VERDICT": "AM_EXPANDED_RISK_INTEGRATION_INTEGRITY_FAILED",
            "NEXT": "STOP",
            "PRIMARY_RISK_MECHANISM": "INTEGRITY_FAILURE",
        }
    if not arms:
        return {
            "CASE": "E",
            "VERDICT": "AM_EXPANDED_RISK_INTEGRATION_INTEGRITY_FAILED",
            "NEXT": "STOP",
            "PRIMARY_RISK_MECHANISM": "NO_ARMS",
        }
    any_pass = any(bool(a.get("RISK_ARM_PASS")) for a in arms)
    all_pres_fail = all(not bool(a.get("CURRENT_PRESERVATION_PASS")) for a in arms)
    any_econ = any(net_and_pf_beat(a, current_net, current_pf) for a in arms)
    any_econ_dd = any(
        net_and_pf_beat(a, current_net, current_pf) and dd_nonworse(a, current_dd) for a in arms
    )
    all_destroy = all(
        float(a.get("OVERLAY_NET_PNL") or 0.0) <= float(current_net)
        or _pf_num(a.get("OVERLAY_PF")) <= float(current_pf)
        for a in arms
    )
    if any_pass:
        best = pick_best([a for a in arms if a.get("RISK_ARM_PASS")])
        return {
            "CASE": "A",
            "VERDICT": "AM_EXPANDED_X14_RF_RISK_INTEGRATED_AUGMENT_SUPPORTED",
            "NEXT": "AM_EXPANDED_X14_RF_AUGMENT_FREEZE_REVIEW",
            "PRIMARY_RISK_MECHANISM": str(
                (best or {}).get("PRIMARY_RISK_MECHANISM") or (best or {}).get("risk_arm_id") or "PASS_ARM"
            ),
        }
    if all_pres_fail:
        return {
            "CASE": "D",
            "VERDICT": "AM_EXPANDED_AUGMENT_PORTFOLIO_RISK_CONFLICT",
            "NEXT": "AM_AUGMENT_PORTFOLIO_INTEGRATION_REASSESSMENT",
            "PRIMARY_RISK_MECHANISM": "CURRENT_PRESERVATION_FAILURE_ALL_ARMS",
        }
    if any_econ_dd:
        return {
            "CASE": "B",
            "VERDICT": "AM_EXPANDED_X14_RF_EDGE_REMAINS_TEMPORALLY_CONCENTRATED",
            "NEXT": "AM_ENTRY_TEMPORAL_REGIME_INFORMATION_REASSESSMENT",
            "PRIMARY_RISK_MECHANISM": "DAILY_ROBUSTNESS_REMAINS_AFTER_DD_NONWORSE",
        }
    if all_destroy:
        return {
            "CASE": "C",
            "VERDICT": "AM_EXPANDED_X14_RF_RISK_FILTER_DESTROYS_ECONOMIC_EDGE",
            "NEXT": "AM_ENTRY_TEMPORAL_REGIME_INFORMATION_REASSESSMENT",
            "PRIMARY_RISK_MECHANISM": "WIN_DOMINANT_AND_OR_CORE_CONFIRMED_DESTROYS_NET_OR_PF",
        }
    return {
        "CASE": "B",
        "VERDICT": "AM_EXPANDED_X14_RF_EDGE_REMAINS_TEMPORALLY_CONCENTRATED",
        "NEXT": "AM_ENTRY_TEMPORAL_REGIME_INFORMATION_REASSESSMENT",
        "PRIMARY_RISK_MECHANISM": "EDGE_SURVIVES_WITHOUT_FULL_ROBUSTNESS",
    }


__all__ = [
    "arm_sort_key",
    "pick_best",
    "profit_concentration",
    "decide_case",
    "preservation_pass",
    "net_and_pf_beat",
    "dd_nonworse",
]
