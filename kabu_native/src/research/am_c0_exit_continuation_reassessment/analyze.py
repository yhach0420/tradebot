"""Fixed-trade continuation effect, 600→750 diagnostic, CASE A-E. No threshold search."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.am_c0_exit_continuation_reassessment import (
    C0_OVERLAY_LOCKED,
    CONT_EXIT_600,
    CURRENT_LOCKED,
    E0,
    E1,
    E2,
)
from research.am_c0_exit_ptl_guard.analyze import _trade_key
from research.am_current_utility_augment.analyze import overlay_gate
from research.am_entry_profit_improvement.metrics import _pf_num
from research.am_exit_contribution_rca.analyze import L2, L3, attach_classes, is_loss, is_win
from research.canonical_entry_performance_rebase.analyze import _f, row_key
from research.am_utility_augment_execution_risk.analyze import preservation_pass

PNL_EPS = 1e-9


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(np.median(xs))


def join_class_keys(path_rows: list[dict[str, Any]], cls: str) -> set[str]:
    tagged = attach_classes(path_rows)
    out = set()
    for r in tagged:
        if r.get("LOSS_CLASS") != cls:
            continue
        out.add(str(r.get("row_key") or row_key(r)))
    return out


def _gross(pnls: list[float]) -> tuple[float, float, Any]:
    gp = float(sum(p for p in pnls if p > 0))
    gl = float(sum(-p for p in pnls if p < 0))
    pf = _pf_num(gp / gl) if gl > PNL_EPS else (float("inf") if gp > PNL_EPS else None)
    return gp, gl, pf


def extend_diag_row(e0: dict[str, Any], got: dict[str, Any], *, arm_id: str) -> dict[str, Any]:
    prefix = "e1" if arm_id == E1 else "e2"
    e0_pnl = float(_f(e0.get("pnl_yen_100")) or 0.0)
    final_pnl = float(_f(got.get(f"{prefix}_pnl_yen_100")) or 0.0)
    delta = final_pnl - e0_pnl
    return {
        "arm_id": arm_id,
        "date": e0.get("date"),
        "symbol": e0.get("symbol"),
        "row_key": _trade_key(e0),
        "PNL_AT_600": got.get("pnl_at_600") if got.get("pnl_at_600") is not None else e0_pnl,
        "FINAL_EXIT_TIME": got.get(f"{prefix}_exit_t"),
        "FINAL_EXIT_REASON": got.get(f"{prefix}_exit_reason"),
        "FINAL_PNL": final_pnl,
        "DELTA_PNL_VS_BASE_CONT_EXIT_600": delta,
        "ever_profitable_pre600": got.get("ever_profitable_pre600"),
        "IMPROVED": delta > PNL_EPS,
        "WORSENED": delta < -PNL_EPS,
        "UNCHANGED": abs(delta) <= PNL_EPS,
        "WINNER_CREATED": (not is_win(e0_pnl)) and is_win(final_pnl),
        "LOSS_AVOIDED": is_loss(e0_pnl) and (not is_loss(final_pnl)),
        "LOSS_WORSENED": is_loss(e0_pnl) and is_loss(final_pnl) and final_pnl < e0_pnl - PNL_EPS,
    }


def diag_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    deltas = [float(r.get("DELTA_PNL_VS_BASE_CONT_EXIT_600") or 0.0) for r in rows]
    return {
        "FORCED_EXTEND_N": len(rows),
        "IMPROVED_N": sum(1 for r in rows if r.get("IMPROVED")),
        "WORSENED_N": sum(1 for r in rows if r.get("WORSENED")),
        "UNCHANGED_N": sum(1 for r in rows if r.get("UNCHANGED")),
        "MEDIAN_DELTA_PNL": _median(deltas),
        "TOTAL_DELTA_PNL": float(sum(deltas)) if deltas else 0.0,
        "WINNER_CREATED_N": sum(1 for r in rows if r.get("WINNER_CREATED")),
        "LOSS_AVOIDED_N": sum(1 for r in rows if r.get("LOSS_AVOIDED")),
        "LOSS_WORSENED_N": sum(1 for r in rows if r.get("LOSS_WORSENED")),
    }


def fixed_arm(
    e0_trades: list[dict[str, Any]],
    by_key: dict[str, dict[str, Any]],
    *,
    arm_id: str,
    l2_keys: set[str],
    l3_keys: set[str],
) -> dict[str, Any]:
    rows = []
    diag_rows = []
    force_key = "e1_forced" if arm_id == E1 else ("e2_forced" if arm_id == E2 else None)
    prefix = "e1" if arm_id == E1 else "e2"
    for t in e0_trades:
        key = _trade_key(t)
        e0_pnl = float(_f(t.get("pnl_yen_100")) or 0.0)
        e0_reason = str(t.get("exit_reason") or "")
        got = by_key.get(key) or {}
        forced = bool(got.get(force_key)) if force_key else False
        if arm_id == E0 or not forced:
            pnl = e0_pnl
            reason = e0_reason
            et = t.get("exit_time") if t.get("exit_time") is not None else t.get("exit_t")
        else:
            pnl = float(_f(got.get(f"{prefix}_pnl_yen_100")) or 0.0)
            reason = str(got.get(f"{prefix}_exit_reason") or "")
            et = got.get(f"{prefix}_exit_t")
            diag_rows.append(extend_diag_row(t, got, arm_id=arm_id))
        rec = {
            "arm_id": arm_id,
            "date": t.get("date"),
            "symbol": t.get("symbol"),
            "row_key": key,
            "e0_pnl": e0_pnl,
            "pnl": pnl,
            "delta_pnl": float(pnl) - float(e0_pnl),
            "e0_reason": e0_reason,
            "exit_reason": reason,
            "exit_t": et,
            "forced_extend": forced,
            "ever_profitable_pre600": got.get("ever_profitable_pre600"),
            "BASELINE_L2": key in l2_keys,
            "BASELINE_L3": key in l3_keys,
            "e0_win": is_win(e0_pnl),
            "e0_loss": is_loss(e0_pnl),
        }
        rows.append(rec)
    pnls = [float(r["pnl"]) for r in rows]
    gp, gl, pf = _gross(pnls)
    e0_pnls = [float(r["e0_pnl"]) for r in rows]
    e0_gp, e0_gl, e0_pf = _gross(e0_pnls)
    forced = [r for r in rows if r.get("forced_extend")]
    cont600_n = sum(1 for r in rows if str(r.get("e0_reason") or "") == CONT_EXIT_600)
    dpack = diag_pack(diag_rows)
    return {
        "arm_id": arm_id,
        "rows": rows,
        "diag_rows": diag_rows,
        "FIXED_TRADE_N": len(rows),
        "FIXED_NET": float(sum(pnls)),
        "FIXED_GROSS_PROFIT": gp,
        "FIXED_GROSS_LOSS": gl,
        "FIXED_PF": pf,
        "E0_FIXED_NET": float(sum(e0_pnls)),
        "E0_FIXED_GROSS_PROFIT": e0_gp,
        "E0_FIXED_GROSS_LOSS": e0_gl,
        "E0_FIXED_PF": e0_pf,
        "CONT_EXIT_600_BASE_N": cont600_n,
        "FORCED_EXTEND_N": len(forced),
        "L2_FORCED_EXTEND_N": sum(1 for r in forced if r.get("BASELINE_L2")),
        "L3_FORCED_EXTEND_N": sum(1 for r in forced if r.get("BASELINE_L3")),
        "FORCED_EXTEND_WIN_N": sum(1 for r in forced if is_win(r.get("pnl"))),
        "FORCED_EXTEND_LOSS_N": sum(1 for r in forced if is_loss(r.get("pnl"))),
        "FORCED_EXTEND_FLAT_N": sum(1 for r in forced if (not is_win(r.get("pnl"))) and (not is_loss(r.get("pnl")))),
        "DELTA_NET_VS_E0": float(sum(pnls)) - float(sum(e0_pnls)),
        "WINNER_PNL_DELTA": float(sum(float(r["delta_pnl"]) for r in rows if r.get("e0_win"))),
        "LOSER_PNL_DELTA": float(sum(float(r["delta_pnl"]) for r in rows if r.get("e0_loss"))),
        **{k: v for k, v in dpack.items() if k != "FORCED_EXTEND_N"},
    }


def vs_c0(e1: dict[str, Any]) -> dict[str, Any]:
    c0 = C0_OVERLAY_LOCKED
    pos = int(e1.get("PAIRED_POS_DAYS") or 0)
    neg = int(e1.get("PAIRED_NEG_DAYS") or 0)
    c0_gap = int(c0["PAIRED_POS_DAYS"]) - int(c0["PAIRED_NEG_DAYS"])
    return {
        "DELTA_NET_VS_C0": float(e1.get("OVERLAY_NET_PNL") or 0.0) - float(c0["NET"]),
        "DELTA_PF_VS_C0": _pf_num(e1.get("OVERLAY_PF")) - float(c0["PF"]),
        "DELTA_DD_VS_C0": float(e1.get("OVERLAY_MAX_DD") or 0.0) - float(c0["DD"]),
        "DELTA_PAIRED_MEDIAN_VS_C0": (
            None
            if e1.get("PAIRED_MEDIAN_DAILY_DELTA") is None
            else float(e1.get("PAIRED_MEDIAN_DAILY_DELTA")) - float(c0["PAIRED_MEDIAN"])
        ),
        "DELTA_POS_MINUS_NEG_VS_C0": float((pos - neg) - c0_gap),
        "DELTA_EX_BEST_VS_C0": (
            None
            if e1.get("EX_BEST_DAY_PNL_DELTA") is None
            else float(e1.get("EX_BEST_DAY_PNL_DELTA")) - float(c0["EX_BEST"])
        ),
        "DELTA_EX_TOP3_VS_C0": (
            None
            if e1.get("EX_TOP3_DAYS_PNL_DELTA") is None
            else float(e1.get("EX_TOP3_DAYS_PNL_DELTA")) - float(c0["EX_TOP3"])
        ),
    }


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


def beats_current_npd(arm: dict[str, Any]) -> bool:
    return (
        float(arm.get("OVERLAY_NET_PNL") or 0.0) > float(CURRENT_LOCKED["NET"])
        and _pf_num(arm.get("OVERLAY_PF")) > float(CURRENT_LOCKED["PF"])
        and abs(float(arm.get("OVERLAY_MAX_DD") or 0.0)) <= abs(float(CURRENT_LOCKED["DD"]))
    )


def day_clear(arm: dict[str, Any]) -> bool:
    med = _f(arm.get("PAIRED_MEDIAN_DAILY_DELTA"))
    return med is not None and float(med) > 0.0 and int(arm.get("PAIRED_POS_DAYS") or 0) > int(arm.get("PAIRED_NEG_DAYS") or 0)


def tail_ok(arm: dict[str, Any]) -> bool:
    exb = _f(arm.get("EX_BEST_DAY_PNL_DELTA"))
    ext = _f(arm.get("EX_TOP3_DAYS_PNL_DELTA"))
    return exb is not None and float(exb) > 0.0 and ext is not None and float(ext) >= 0.0


def destroys_current_edge(arm: dict[str, Any]) -> bool:
    return float(arm.get("OVERLAY_NET_PNL") or 0.0) <= float(CURRENT_LOCKED["NET"]) or _pf_num(
        arm.get("OVERLAY_PF")
    ) <= float(CURRENT_LOCKED["PF"])


def improves_vs_c0(arm: dict[str, Any]) -> bool:
    vs = vs_c0(arm)
    net = vs.get("DELTA_NET_VS_C0")
    pf = vs.get("DELTA_PF_VS_C0")
    med = vs.get("DELTA_PAIRED_MEDIAN_VS_C0")
    gap = vs.get("DELTA_POS_MINUS_NEG_VS_C0")
    if net is not None and float(net) > 1e-9:
        return True
    if pf is not None and float(pf) > 1e-12:
        return True
    if abs(float(arm.get("OVERLAY_MAX_DD") or 0.0)) + 1e-9 < abs(float(C0_OVERLAY_LOCKED["DD"])):
        return True
    if med is not None and float(med) > 1e-9:
        return True
    if gap is not None and float(gap) > 1e-9:
        return True
    return False


def decide_case(
    *,
    integrity_ok: bool,
    preservation_all: bool,
    arms: list[dict[str, Any]],
) -> dict[str, Any]:
    if (not integrity_ok) or (not preservation_all):
        return {
            "CASE": "E",
            "VERDICT": "AM_C0_EXIT_CONTINUATION_INTEGRITY_FAILED",
            "NEXT": "STOP",
        }
    chal = [a for a in arms if str(a.get("architecture_id")) in {E1, E2}]
    passed = [a for a in chal if bool(a.get("ARM_PASS"))]
    if passed:
        best = pick_best(passed)
        return {
            "CASE": "A",
            "VERDICT": "AM_C0_EXIT_CONTINUATION_FULL_GATE_SUPPORTED",
            "NEXT": "AM_C0_EXIT_CONTINUATION_FREEZE_REVIEW",
            "BEST_PASS_ARM": (best or {}).get("architecture_id"),
        }
    day_tail = [
        a
        for a in chal
        if beats_current_npd(a) and day_clear(a) and (not tail_ok(a))
    ]
    if day_tail:
        return {
            "CASE": "B",
            "VERDICT": "AM_C0_EXIT_CONTINUATION_DAY_ROBUST_TAIL_REMAINS",
            "NEXT": "AM_EXIT_RESEARCH_FINAL_DECISION",
        }
    partial = [a for a in chal if improves_vs_c0(a) and (not destroys_current_edge(a))]
    if partial:
        return {
            "CASE": "C",
            "VERDICT": "AM_C0_EXIT_CONTINUATION_PARTIAL_SUPPORT",
            "NEXT": "AM_EXIT_RESEARCH_FINAL_DECISION",
        }
    return {
        "CASE": "D",
        "VERDICT": "AM_C0_EXIT_CONTINUATION_NOT_SUPPORTED",
        "NEXT": "AM_EXIT_RESEARCH_FINAL_DECISION",
    }


def e_full_gate(
    ov_pack: dict[str, Any],
    base_pack: dict[str, Any],
    paired: dict[str, Any],
    *,
    preservation_ok_flag: bool,
    integrity_ok: bool,
) -> dict[str, Any]:
    gate = overlay_gate(
        ov_pack,
        base_pack,
        paired,
        preservation_pass=bool(preservation_ok_flag),
        integrity_ok=bool(integrity_ok),
    )
    return {
        "gates": dict(gate.get("gates") or {}),
        "ARM_PASS": bool(gate.get("AUGMENT_OVERLAY_PASS")),
    }


def preservation_ok(pres: dict[str, Any]) -> bool:
    return bool(preservation_pass(pres)) and int(pres.get("CURRENT_ENTRY_MISMATCH_N") or 0) == 0
