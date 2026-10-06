"""Loss mechanism classification and aggregations. Diagnostic only. No policy."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.am_entry_profit_improvement.metrics import _pf_num
from research.canonical_entry_performance_rebase.analyze import _f

L1 = "L1_ENTRY_NEVER_PROFITABLE"
L2 = "L2_PROFIT_TO_LOSS_BEFORE_EXIT"
L3 = "L3_RECOVERED_AFTER_EXIT"
L4 = "L4_LOST_BUT_HAD_PARTIAL_RECOVERY"
PNL_EPS = 1e-9


def is_loss(pnl: Any) -> bool:
    p = _f(pnl)
    return p is not None and float(p) < -PNL_EPS


def is_win(pnl: Any) -> bool:
    p = _f(pnl)
    return p is not None and float(p) > PNL_EPS


def classify_loss(row: dict[str, Any]) -> Optional[str]:
    pnl = _f(row.get("realized_pnl_yen_100"))
    if pnl is None or float(pnl) >= -PNL_EPS:
        return None
    mfe_end = _f(row.get("MFE_TO_EVAL_END_YEN_100"))
    mfe_pre = _f(row.get("MFE_PRE_EXIT_YEN_100"))
    post_max = _f(row.get("POST_EXIT_MAX_PNL_YEN_100"))
    if mfe_end is not None and float(mfe_end) <= 0.0:
        return L1
    if mfe_pre is not None and float(mfe_pre) > 0.0:
        return L2
    if (mfe_pre is None or float(mfe_pre) <= 0.0) and post_max is not None and float(post_max) > 0.0:
        return L3
    if mfe_end is not None and float(mfe_end) > float(pnl):
        return L4
    return L1


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(np.median(xs))


def _pct(xs: list[float], q: float) -> Optional[float]:
    if not xs:
        return None
    return float(np.percentile(xs, q))


def _rate(num: int, den: int) -> Optional[float]:
    if den <= 0:
        return None
    return float(num) / float(den)


def _share(part: float, total: float) -> Optional[float]:
    if abs(total) <= PNL_EPS:
        return None
    return float(part) / float(total)


def attach_classes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        rec = dict(r)
        rec["LOSS_CLASS"] = classify_loss(rec)
        mfe = _f(rec.get("MFE_PRE_EXIT_YEN_100"))
        pnl = _f(rec.get("realized_pnl_yen_100"))
        if is_win(pnl) and mfe is not None and float(mfe) > 0.0 and pnl is not None:
            rec["REALIZED_CAPTURE_RATIO"] = float(pnl) / float(mfe)
        else:
            rec["REALIZED_CAPTURE_RATIO"] = None
        out.append(rec)
    return out


def population_pack(rows: list[dict[str, Any]], *, name: str) -> dict[str, Any]:
    pnls = [float(_f(r.get("realized_pnl_yen_100")) or 0.0) for r in rows]
    win_n = sum(1 for p in pnls if p > PNL_EPS)
    loss_n = sum(1 for p in pnls if p < -PNL_EPS)
    flat_n = len(pnls) - win_n - loss_n
    gp = sum(p for p in pnls if p > 0)
    gl = sum(-p for p in pnls if p < 0)
    losses = [r for r in rows if is_loss(r.get("realized_pnl_yen_100"))]
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in losses:
        by[str(r.get("LOSS_CLASS") or L1)].append(r)

    def g_loss(cls: str) -> float:
        return float(sum(-float(x.get("realized_pnl_yen_100") or 0.0) for x in by.get(cls) or []))

    l1_n = len(by.get(L1) or [])
    l2_n = len(by.get(L2) or [])
    l3_n = len(by.get(L3) or [])
    l4_n = len(by.get(L4) or [])
    l1_g = g_loss(L1)
    l2_g = g_loss(L2)
    l3_g = g_loss(L3)
    l4_g = g_loss(L4)
    mfes = [float(v) for r in rows if (v := _f(r.get("MFE_PRE_EXIT_YEN_100"))) is not None]
    maes = [float(v) for r in rows if (v := _f(r.get("MAE_PRE_EXIT_YEN_100"))) is not None]
    gbs = [float(v) for r in rows if (v := _f(r.get("GIVEBACK_FROM_MFE_TO_EXIT"))) is not None]
    win_cap = [
        float(v)
        for r in rows
        if is_win(r.get("realized_pnl_yen_100")) and (v := _f(r.get("REALIZED_CAPTURE_RATIO"))) is not None
    ]
    win_gb = [
        float(v)
        for r in rows
        if is_win(r.get("realized_pnl_yen_100")) and (v := _f(r.get("GIVEBACK_FROM_MFE_TO_EXIT"))) is not None
    ]
    loss_gb = [
        float(v)
        for r in rows
        if is_loss(r.get("realized_pnl_yen_100")) and (v := _f(r.get("GIVEBACK_FROM_MFE_TO_EXIT"))) is not None
    ]
    return {
        "population": name,
        "TRADE_N": len(rows),
        "WIN_N": win_n,
        "LOSS_N": loss_n,
        "FLAT_N": flat_n,
        "NET_PNL": float(sum(pnls)),
        "GROSS_PROFIT": float(gp),
        "GROSS_LOSS": float(gl),
        "PF": _pf_num(gp / gl) if gl > PNL_EPS else (float("inf") if gp > PNL_EPS else None),
        "LOSS_RATE": _rate(loss_n, len(rows)),
        "ENTRY_NEVER_PROFITABLE_N": l1_n,
        "PROFIT_TO_LOSS_BEFORE_EXIT_N": l2_n,
        "RECOVERED_AFTER_EXIT_N": l3_n,
        "PARTIAL_RECOVERY_N": l4_n,
        "L1_GROSS_LOSS": l1_g,
        "L2_GROSS_LOSS": l2_g,
        "L3_GROSS_LOSS": l3_g,
        "L4_GROSS_LOSS": l4_g,
        "L1_GROSS_LOSS_SHARE": _share(l1_g, gl),
        "L2_GROSS_LOSS_SHARE": _share(l2_g, gl),
        "L3_GROSS_LOSS_SHARE": _share(l3_g, gl),
        "L4_GROSS_LOSS_SHARE": _share(l4_g, gl),
        "ENTRY_NEVER_PROFITABLE_RATE": _rate(l1_n, len(rows)),
        "PROFIT_TO_LOSS_RATE": _rate(l2_n, len(rows)),
        "PROFIT_TO_LOSS_AMONG_LOSS_RATE": _rate(l2_n, loss_n),
        "ENTRY_NEVER_PROFITABLE_AMONG_LOSS_RATE": _rate(l1_n, loss_n),
        "MEDIAN_MFE": _median(mfes),
        "MEDIAN_MAE": _median(maes),
        "MEDIAN_GIVEBACK": _median(gbs),
        "P75_GIVEBACK": _pct(gbs, 75),
        "P90_GIVEBACK": _pct(gbs, 90),
        "TOTAL_MFE_PRE_EXIT": float(sum(mfes)) if mfes else 0.0,
        "TOTAL_GIVEBACK_FROM_MFE": float(sum(gbs)) if gbs else 0.0,
        "LOSER_TOTAL_GIVEBACK": float(sum(loss_gb)) if loss_gb else 0.0,
        "WINNER_TOTAL_GIVEBACK": float(sum(win_gb)) if win_gb else 0.0,
        "WIN_MEDIAN_CAPTURE_RATIO": _median(win_cap),
        "WIN_MEDIAN_GIVEBACK": _median(win_gb),
        "WIN_MEDIAN_MFE": _median(
            [float(v) for r in rows if is_win(r.get("realized_pnl_yen_100")) and (v := _f(r.get("MFE_PRE_EXIT_YEN_100"))) is not None]
        ),
        "WIN_MEDIAN_HOLDING_SEC": _median(
            [float(v) for r in rows if is_win(r.get("realized_pnl_yen_100")) and (v := _f(r.get("HOLDING_SEC"))) is not None]
        ),
    }


def exit_reason_rows(rows: list[dict[str, Any]], *, population: str) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[str(r.get("exit_reason") or "UNKNOWN")].append(r)
    out = []
    for reason, grp in sorted(by.items()):
        pack = population_pack(grp, name=f"{population}|{reason}")
        pack["population"] = population
        pack["exit_reason"] = reason
        out.append(pack)
    return out


def decide_case(*, integrity_ok: bool, c0_loss: dict[str, Any]) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "CASE": "D",
            "VERDICT": "AM_EXIT_CONTRIBUTION_RCA_INTEGRITY_FAILED",
            "NEXT": "STOP",
            "PRIMARY_LOSS_MECHANISM": None,
            "EXIT_CONTRIBUTION_SUPPORTED": False,
            "Q8": None,
        }
    l1 = float(c0_loss.get("L1_GROSS_LOSS") or 0.0)
    l2 = float(c0_loss.get("L2_GROSS_LOSS") or 0.0)
    l3 = float(c0_loss.get("L3_GROSS_LOSS") or 0.0)
    l4 = float(c0_loss.get("L4_GROSS_LOSS") or 0.0)
    exit_g = l2 + l3
    buckets = [(l1, L1), (l2, L2), (l3, L3), (l4, L4)]
    primary = max(buckets, key=lambda x: x[0])[1]
    if l1 > exit_g and l1 >= l4:
        return {
            "CASE": "A",
            "VERDICT": "AM_C0_LOSS_PRIMARILY_ENTRY_ORIGIN",
            "NEXT": "AM_C0_FUTURE_OOS_WHEN_AVAILABLE",
            "PRIMARY_LOSS_MECHANISM": primary,
            "EXIT_CONTRIBUTION_SUPPORTED": False,
            "Q8": "ENTRY_DOMINANT",
        }
    if exit_g > l1:
        return {
            "CASE": "B",
            "VERDICT": "AM_C0_EXIT_CONTRIBUTION_SUPPORTED",
            "NEXT": "AM_EXIT_ARCHITECTURE_PRECOMMIT",
            "PRIMARY_LOSS_MECHANISM": primary,
            "EXIT_CONTRIBUTION_SUPPORTED": True,
            "Q8": "EXIT_DOMINANT",
        }
    return {
        "CASE": "C",
        "VERDICT": "AM_C0_LOSS_MECHANISM_MIXED",
        "NEXT": "AM_EXIT_ARCHITECTURE_PRECOMMIT",
        "PRIMARY_LOSS_MECHANISM": primary,
        "EXIT_CONTRIBUTION_SUPPORTED": True,
        "Q8": "MIXED",
    }
