"""Arm gate, increment vs A0, CASE A-F. No threshold search."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.am_entry_profit_improvement.metrics import _pf_num
from research.am_entry_temporal_regime_information import A0, A1, A2, A3, A4, A5
from research.canonical_entry_performance_rebase.analyze import _f


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


def _d(a: Any, b: Any) -> Any:
    try:
        return float(a) - float(b)
    except (TypeError, ValueError):
        return None


def increment_vs_a0(arm: dict[str, Any], a0: dict[str, Any]) -> dict[str, Any]:
    pos_a = int(arm.get("PAIRED_POS_DAYS") or 0) - int(arm.get("PAIRED_NEG_DAYS") or 0)
    pos_0 = int(a0.get("PAIRED_POS_DAYS") or 0) - int(a0.get("PAIRED_NEG_DAYS") or 0)
    return {
        "DELTA_NET_VS_X14_BASE": _d(arm.get("OVERLAY_NET_PNL"), a0.get("OVERLAY_NET_PNL")),
        "DELTA_PF_VS_X14_BASE": _d(_pf_num(arm.get("OVERLAY_PF")), _pf_num(a0.get("OVERLAY_PF"))),
        "DELTA_DD_VS_X14_BASE": _d(arm.get("OVERLAY_MAX_DD"), a0.get("OVERLAY_MAX_DD")),
        "DELTA_PAIRED_MEDIAN_VS_X14_BASE": _d(
            arm.get("PAIRED_MEDIAN_DAILY_DELTA"), a0.get("PAIRED_MEDIAN_DAILY_DELTA")
        ),
        "DELTA_POS_MINUS_NEG_DAYS_VS_X14_BASE": float(pos_a - pos_0),
        "DELTA_EX_BEST_VS_X14_BASE": _d(arm.get("EX_BEST_DAY_PNL_DELTA"), a0.get("EX_BEST_DAY_PNL_DELTA")),
        "DELTA_EX_TOP3_VS_X14_BASE": _d(arm.get("EX_TOP3_DAYS_PNL_DELTA"), a0.get("EX_TOP3_DAYS_PNL_DELTA")),
    }


def increment_sort_key(row: dict[str, Any]) -> tuple:
    ext = row.get("DELTA_EX_TOP3_VS_X14_BASE")
    exb = row.get("DELTA_EX_BEST_VS_X14_BASE")
    med = row.get("DELTA_PAIRED_MEDIAN_VS_X14_BASE")
    pf = row.get("DELTA_PF_VS_X14_BASE")
    dd = abs(float(row.get("OVERLAY_MAX_DD") or 0.0))
    net = row.get("DELTA_NET_VS_X14_BASE")
    return (
        -float(ext) if ext is not None else 1e18,
        -float(exb) if exb is not None else 1e18,
        -float(med) if med is not None else 1e18,
        -float(pf) if pf is not None else 1e18,
        dd,
        -float(net) if net is not None else 1e18,
        str(row.get("architecture_id") or ""),
    )


def _pos_delta(v: Any) -> bool:
    try:
        return v is not None and float(v) > 0.0
    except (TypeError, ValueError):
        return False


def bundle_incremental(arms: list[dict[str, Any]], ids: tuple[str, ...]) -> bool:
    want = set(ids)
    for a in arms:
        if str(a.get("architecture_id")) not in want:
            continue
        if (
            _pos_delta(a.get("DELTA_NET_VS_X14_BASE"))
            or _pos_delta(a.get("DELTA_PAIRED_MEDIAN_VS_X14_BASE"))
            or _pos_delta(a.get("DELTA_EX_BEST_VS_X14_BASE"))
            or _pos_delta(a.get("DELTA_EX_TOP3_VS_X14_BASE"))
        ):
            return True
    return False


def net_pf_dd_beat(arm: dict[str, Any], current_net: float, current_pf: float, current_dd: float) -> bool:
    return (
        float(arm.get("OVERLAY_NET_PNL") or 0.0) > float(current_net)
        and _pf_num(arm.get("OVERLAY_PF")) > float(current_pf)
        and abs(float(arm.get("OVERLAY_MAX_DD") or 0.0)) <= abs(float(current_dd))
    )


def _no_increment(arms: list[dict[str, Any]]) -> bool:
    keys = (
        "DELTA_NET_VS_X14_BASE",
        "DELTA_PF_VS_X14_BASE",
        "DELTA_PAIRED_MEDIAN_VS_X14_BASE",
        "DELTA_POS_MINUS_NEG_DAYS_VS_X14_BASE",
        "DELTA_EX_BEST_VS_X14_BASE",
        "DELTA_EX_TOP3_VS_X14_BASE",
    )
    for a in arms:
        for k in keys:
            if _pos_delta(a.get(k)):
                return False
    return True


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
            "CASE": "F",
            "VERDICT": "AM_TEMPORAL_REGIME_INFORMATION_INTEGRITY_FAILED",
            "NEXT": "STOP",
        }
    if not arms:
        return {
            "CASE": "F",
            "VERDICT": "AM_TEMPORAL_REGIME_INFORMATION_INTEGRITY_FAILED",
            "NEXT": "STOP",
        }
    by = {str(a.get("architecture_id")): a for a in arms}
    a0 = by.get(A0) or {}
    others = [a for a in arms if str(a.get("architecture_id")) != A0]
    any_pass = any(bool(a.get("ARM_PASS")) for a in arms)
    all_pres_fail = all(not bool(a.get("CURRENT_PRESERVATION_PASS")) for a in arms)
    any_b = any(net_pf_dd_beat(a, current_net, current_pf, current_dd) for a in arms)
    all_worse = bool(others) and all(
        float(a.get("OVERLAY_NET_PNL") or 0.0) < float(a0.get("OVERLAY_NET_PNL") or 0.0) for a in others
    )
    if any_pass:
        return {
            "CASE": "A",
            "VERDICT": "AM_TEMPORAL_REGIME_X14_AUGMENT_SUPPORTED",
            "NEXT": "AM_TEMPORAL_REGIME_X14_AUGMENT_FREEZE_REVIEW",
        }
    if all_pres_fail:
        return {
            "CASE": "E",
            "VERDICT": "AM_TEMPORAL_REGIME_PORTFOLIO_CONFLICT",
            "NEXT": "AM_AUGMENT_PORTFOLIO_INTEGRATION_REASSESSMENT",
        }
    if any_b:
        return {
            "CASE": "B",
            "VERDICT": "AM_TEMPORAL_REGIME_EDGE_PARTIAL_NOT_DAY_ROBUST",
            "NEXT": "AM_RAW_EVENT_INCREMENTAL_SELECTION_REASSESSMENT",
        }
    if all_worse:
        return {
            "CASE": "C",
            "VERDICT": "AM_TEMPORAL_REGIME_INFORMATION_NOT_SUFFICIENT",
            "NEXT": "AM_RAW_EVENT_INCREMENTAL_SELECTION_REASSESSMENT",
        }
    if _no_increment(others):
        return {
            "CASE": "D",
            "VERDICT": "AM_X14_EDGE_NOT_EXPLAINED_BY_SNAPSHOT_REGIME",
            "NEXT": "AM_RAW_EVENT_INCREMENTAL_SELECTION_REASSESSMENT",
        }
    return {
        "CASE": "C",
        "VERDICT": "AM_TEMPORAL_REGIME_INFORMATION_NOT_SUFFICIENT",
        "NEXT": "AM_RAW_EVENT_INCREMENTAL_SELECTION_REASSESSMENT",
    }


def a0_trade_diagnostics(
    trades: list[dict[str, Any]],
    rows_by_key: dict[str, dict[str, Any]],
    features: list[str],
) -> list[dict[str, Any]]:
    by_cls: dict[str, list[dict[str, Any]]] = {"WIN": [], "LOSS": [], "FLAT": []}
    for t in trades:
        pnl = float(t.get("pnl_yen_100") or 0.0)
        if pnl > 1e-12:
            lab = "WIN"
        elif pnl < -1e-12:
            lab = "LOSS"
        else:
            lab = "FLAT"
        rec = dict(t)
        src = rows_by_key.get(str(t.get("row_key") or "")) or {}
        for f in features:
            rec[f] = src.get(f)
        by_cls[lab].append(rec)
    out = []
    for f in features:
        win_xs = [float(v) for v in (_f(r.get(f)) for r in by_cls["WIN"]) if v is not None]
        loss_xs = [float(v) for v in (_f(r.get(f)) for r in by_cls["LOSS"]) if v is not None]
        day_win: dict[str, list[float]] = {}
        day_loss: dict[str, list[float]] = {}
        for r in by_cls["WIN"]:
            v = _f(r.get(f))
            if v is None:
                continue
            day_win.setdefault(str(r.get("date") or ""), []).append(float(v))
        for r in by_cls["LOSS"]:
            v = _f(r.get(f))
            if v is None:
                continue
            day_loss.setdefault(str(r.get("date") or ""), []).append(float(v))
        pos = neg = tie = 0
        for d in set(day_win) | set(day_loss):
            if d not in day_win or d not in day_loss:
                continue
            dw = float(np.median(day_win[d]))
            dl = float(np.median(day_loss[d]))
            if dw > dl + 1e-12:
                pos += 1
            elif dl > dw + 1e-12:
                neg += 1
            else:
                tie += 1
        out.append(
            {
                "feature": f,
                "WIN_N": len(by_cls["WIN"]),
                "LOSS_N": len(by_cls["LOSS"]),
                "FLAT_N": len(by_cls["FLAT"]),
                "WIN_MEDIAN": float(np.median(win_xs)) if win_xs else None,
                "LOSS_MEDIAN": float(np.median(loss_xs)) if loss_xs else None,
                "DAYS_WIN_GT_LOSS": pos,
                "DAYS_LOSS_GT_WIN": neg,
                "DAYS_TIE": tie,
            }
        )
    return out


TEMPORAL_ARMS = (A1, A4, A5)
REGIME_ARMS = (A2, A4, A5)
CORE_ARMS = (A3, A5)
