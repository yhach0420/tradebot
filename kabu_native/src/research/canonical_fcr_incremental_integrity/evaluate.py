"""Matched common-anchor opportunity eval + incremental labels."""
from __future__ import annotations

from typing import Any, Sequence

from research.canonical_fcr_exact_method.loader import Tick
from research.canonical_fcr_exact_method.opportunity import path_metrics
from research.canonical_fcr_incremental_integrity.candidates import ArmRow
from research.canonical_fcr_incremental_integrity.constants import COST_BPS, LOT


def evaluate_arm(rows: Sequence[ArmRow], streams: dict[str, list[Tick]], *, horizon: float = 180.0) -> dict[str, Any]:
    metrics = []
    by_sym: dict[str, float] = {}
    for r in rows:
        m = path_metrics(streams[r.stream_key], r.entry_idx, float(r.entry_execution_price), max_sec=horizon)
        if not m.get("evaluable"):
            continue
        metrics.append({**m, "symbol": r.symbol, "day": r.day, "cid": r.reclaim_candidate_id})
        by_sym[r.symbol] = by_sym.get(r.symbol, 0.0) + m["terminal_pnl_yen"]
    n = len(metrics)
    if not n:
        return {
            "n": 0, "pnl": 0.0, "pf": None, "mean": None,
            "never_rate": None, "early_adverse_rate": None, "stop_rate": None,
            "stop_5m_rate": None, "noprogress_rate": None, "winner_rate": None,
            "avg_mfe": None, "avg_mae": None, "top1_symbol_share": None, "top3_symbol_share": None,
            "cost_bps": COST_BPS, "lot": LOT,
        }
    pnls = [m["terminal_pnl_yen"] for m in metrics]
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    pf = (wins / losses) if losses > 0 else (float("inf") if wins > 0 else None)
    pos = sorted([v for v in by_sym.values() if v > 0], reverse=True)
    tot_pos = sum(pos) or 1.0
    return {
        "n": n,
        "pnl": sum(pnls),
        "pf": round(pf, 4) if isinstance(pf, float) and pf != float("inf") else pf,
        "mean": sum(pnls) / n,
        "never_rate": sum(1 for m in metrics if m["never_profitable"]) / n,
        "early_adverse_rate": sum(1 for m in metrics if m["early_adverse"]) / n,
        "stop_rate": sum(1 for m in metrics if m["stop_path"]) / n,
        "stop_5m_rate": sum(1 for m in metrics if m["stop_5m_path"]) / n,
        "noprogress_rate": sum(1 for m in metrics if m["no_progress"]) / n,
        "winner_rate": sum(1 for m in metrics if m["winner"]) / n,
        "avg_mfe": sum(m["mfe"] for m in metrics) / n,
        "avg_mae": sum(m["mae"] for m in metrics) / n,
        "top1_symbol_share": (pos[0] / tot_pos) if pos else 0.0,
        "top3_symbol_share": (sum(pos[:3]) / tot_pos) if pos else 0.0,
        "cost_bps": COST_BPS,
        "lot": LOT,
        "ids": [m["cid"] for m in metrics],
        "rows": metrics,
    }


def matched_increment(parent: dict[str, Any], child: dict[str, Any], *, lineage_ok: bool, anchor_ok: bool) -> dict[str, Any]:
    """Formal MATCHED_COMMON_ANCHOR_INCREMENTAL only."""
    p_ids = set(parent.get("ids") or [])
    c_ids = set(child.get("ids") or [])
    retained = c_ids
    removed = p_ids - c_ids
    child_without = c_ids - p_ids

    def _subset_stats(ids: set[str], src: dict[str, Any]) -> dict[str, Any]:
        rows = [r for r in (src.get("rows") or []) if r["cid"] in ids]
        if not rows:
            return {"n": 0, "pnl": 0.0, "pf": None, "mean": None}
        pnls = [r["terminal_pnl_yen"] for r in rows]
        wins = sum(p for p in pnls if p > 0)
        losses = -sum(p for p in pnls if p < 0)
        pf = (wins / losses) if losses > 0 else (float("inf") if wins > 0 else None)
        return {
            "n": len(rows),
            "pnl": sum(pnls),
            "pf": round(pf, 4) if isinstance(pf, float) and pf != float("inf") else pf,
            "mean": sum(pnls) / len(rows),
        }

    if not lineage_ok or not anchor_ok:
        return {"label": "INCREMENT_NOT_EVALUABLE", "reason": "lineage_or_anchor_blocked"}
    if (parent.get("n") or 0) == 0 and (child.get("n") or 0) == 0:
        return {"label": "INCREMENT_NOT_EVALUABLE", "reason": "parent_and_child_zero"}
    if (child.get("n") or 0) == 0:
        return {
            "label": "INCREMENT_NOT_EVALUABLE",
            "reason": "child_zero",
            "parent_n": parent.get("n"),
            "child_n": 0,
            "removed_n": len(removed),
            "retained_n": 0,
            "child_without_parent": len(child_without),
        }

    ret_s = _subset_stats(retained, child)
    rem_s = _subset_stats(removed, parent)

    def d(k):
        if parent.get(k) is None or child.get(k) is None:
            return None
        return child[k] - parent[k]

    pf_imp = isinstance(parent.get("pf"), (int, float)) and isinstance(child.get("pf"), (int, float)) and (child["pf"] or 0) > (parent["pf"] or 0)
    mean_imp = d("mean") is not None and d("mean") > 0
    quality = (d("never_rate") is not None and d("never_rate") < 0) or (
        d("early_adverse_rate") is not None and d("early_adverse_rate") < 0
    )
    winner_ok = (child.get("winner_rate") or 0) > 0 and not (d("winner_rate") is not None and d("winner_rate") < -0.05)
    if pf_imp and mean_imp and quality and winner_ok:
        label = "INCREMENT_POSITIVE"
    elif pf_imp or mean_imp or quality:
        label = "INCREMENT_MIXED"
    else:
        label = "INCREMENT_NEGATIVE"

    return {
        "label": label,
        "mode": "MATCHED_COMMON_ANCHOR_INCREMENTAL",
        "parent_n": parent.get("n"),
        "child_n": child.get("n"),
        "retained_n": len(retained),
        "removed_n": len(removed),
        "child_without_parent": len(child_without),
        "retained": ret_s,
        "removed": rem_s,
        "parent_pf": parent.get("pf"),
        "child_pf": child.get("pf"),
        "parent_mean": parent.get("mean"),
        "child_mean": child.get("mean"),
        "never_delta": d("never_rate"),
        "early_adverse_delta": d("early_adverse_rate"),
        "stop_delta": d("stop_rate"),
        "stop_5m_delta": d("stop_5m_rate"),
        "noprogress_delta": d("noprogress_rate"),
        "winner_delta": d("winner_rate"),
        "mfe_delta": d("avg_mfe"),
        "mae_delta": d("avg_mae"),
        "top1_delta": d("top1_symbol_share"),
        # explicitly NOT based on total loss reduction alone
        "not_total_loss_only": True,
    }


def train_gate(
    f5: dict[str, Any],
    *,
    integrity_ok: bool,
    nesting_ok: bool,
    lineage_ok: bool,
    anchor_ok: bool,
    state_ok: bool,
    spread_ok: bool,
    stride_ok: bool,
    one_impulse_ok: bool,
) -> tuple[bool, str, list[str]]:
    codes: list[str] = []
    if not stride_ok:
        return False, "STRIDE1_EVENT_PARITY_BLOCKED", ["NO_TRAIN_CANONICAL_FCR_CANDIDATE"]
    if not nesting_ok or not lineage_ok or not anchor_ok or not state_ok:
        return False, "INTEGRITY_BLOCKED", ["NO_TRAIN_CANONICAL_FCR_CANDIDATE", "FCR_INCREMENTAL_INTEGRITY_BLOCKED"]
    if not spread_ok:
        codes += ["F5_SPEC_CONFORMANCE_BLOCKED", "NO_TRAIN_CANONICAL_FCR_CANDIDATE"]
        return False, "F5_SPEC_CONFORMANCE_BLOCKED", codes
    if not one_impulse_ok:
        return False, "ONE_IMPULSE_BLOCKED", ["NO_TRAIN_CANONICAL_FCR_CANDIDATE"]
    n = f5.get("n") or 0
    if n < 30:
        codes += ["NO_TRAIN_CANONICAL_FCR_CANDIDATE", "CURRENT_F5_SPEC_NO_TRAIN_EDGE", "CANONICAL_FCR_CURRENT_SPEC_REJECTED"]
        return False, "n<30", codes
    if (f5.get("pnl") or 0) <= 0 or (f5.get("mean") or 0) <= 0:
        codes += ["NO_TRAIN_CANONICAL_FCR_CANDIDATE", "CURRENT_F5_SPEC_NO_TRAIN_EDGE", "CANONICAL_FCR_CURRENT_SPEC_REJECTED"]
        return False, "pnl", codes
    pf = f5.get("pf")
    if pf is None or (isinstance(pf, float) and pf <= 1):
        codes += ["NO_TRAIN_CANONICAL_FCR_CANDIDATE", "CURRENT_F5_SPEC_NO_TRAIN_EDGE", "CANONICAL_FCR_CURRENT_SPEC_REJECTED"]
        return False, "pf", codes
    if (f5.get("top1_symbol_share") or 0) >= 0.40:
        codes += ["NO_TRAIN_CANONICAL_FCR_CANDIDATE", "CURRENT_F5_SPEC_NO_TRAIN_EDGE", "CANONICAL_FCR_CURRENT_SPEC_REJECTED"]
        return False, "symbol", codes
    return True, "TRAIN_PASS", ["CANONICAL_FCR_ENTRY_CANDIDATE"]
