"""Fixed-trade EXIT effect, identity audit, CASE A-E. No threshold search."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.am_c0_exit_ptl_guard import C0_OVERLAY_LOCKED, CURRENT_LOCKED, EXIT_REASON
from research.am_current_utility_augment.analyze import overlay_gate
from research.am_entry_profit_improvement.metrics import _pf_num
from research.am_exit_contribution_rca.analyze import L2, attach_classes, is_loss, is_win
from research.canonical_entry_performance_rebase.analyze import _f, row_key
from research.am_utility_augment_execution_risk.analyze import preservation_pass

PNL_EPS = 1e-9


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(np.median(xs))


def _trade_key(t: dict[str, Any]) -> str:
    return str(t.get("row_key") or row_key(t))


def _cand_key(c: dict[str, Any]) -> tuple:
    return (str(c.get("date") or ""), float(c.get("t0") or 0.0), str(c.get("row_key") or ""))


def identity_audit(
    tagged_e0: list[dict[str, Any]],
    tagged_e1: list[dict[str, Any]],
    e0_overlay: dict[str, Any],
    e1_overlay: dict[str, Any],
    c0_keys: set[str],
) -> dict[str, Any]:
    n = min(len(tagged_e0), len(tagged_e1))
    b0 = b1 = conf = 0
    for i in range(n):
        a = tagged_e0[i]
        b = tagged_e1[i]
        if not _close_score(a.get("B0_JOINT_SCORE"), b.get("B0_JOINT_SCORE")):
            b0 += 1
        if not _close_score(a.get("B1_JOINT_SCORE"), b.get("B1_JOINT_SCORE")):
            b1 += 1
        if bool(a.get("_aug_eligible")) != bool(b.get("_aug_eligible")):
            conf += 1
        if str(a.get("_row_key") or row_key(a)) != str(b.get("_row_key") or row_key(b)):
            conf += 1
    e1_keys = {str(r.get("_row_key") or row_key(r)) for r in tagged_e1 if r.get("_aug_eligible")}
    conf += len(set(str(k) for k in c0_keys) ^ e1_keys)
    e0_c = {_cand_key(c) for c in (e0_overlay.get("augment_candidates") or [])}
    e1_c = {_cand_key(c) for c in (e1_overlay.get("augment_candidates") or [])}
    return {
        "C0_CANDIDATE_DECISION_MISMATCH_N": len(e0_c ^ e1_c),
        "B0_SCORE_MISMATCH_N": b0,
        "B1_SCORE_MISMATCH_N": b1,
        "C0_CONFIRMATION_MISMATCH_N": conf,
        "ENTRY_POLICY_CHANGE_N": 0,
        "CURRENT_ENTRY_POLICY_CHANGE_N": 0,
        "TAGGED_LEN_MISMATCH_N": abs(len(tagged_e0) - len(tagged_e1)),
    }


def _close_score(a: Any, b: Any) -> bool:
    fa, fb = _f(a), _f(b)
    if fa is None and fb is None:
        return True
    if fa is None or fb is None:
        return False
    return abs(float(fa) - float(fb)) <= 1e-12


def current_trade_counts(baseline: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    base_n = sum(1 for t in (baseline.get("trades") or []) if str(t.get("arm") or "CURRENT") == "CURRENT")
    ov_n = sum(1 for t in (overlay.get("trades") or []) if str(t.get("arm") or "") == "CURRENT")
    return {
        "CURRENT_BASELINE_TRADE_N": base_n,
        "CURRENT_NEW_ARCH_TRADE_N": ov_n,
        "CURRENT_ENTRY_MISMATCH_N": abs(int(base_n) - int(ov_n)),
    }


def fixed_trade_effect(
    e0_trades: list[dict[str, Any]],
    e1_by_key: dict[str, dict[str, Any]],
    l2_keys: set[str],
) -> dict[str, Any]:
    rows = []
    for t in e0_trades:
        key = _trade_key(t)
        e0_pnl = float(_f(t.get("pnl_yen_100")) or 0.0)
        e1 = e1_by_key.get(key) or {}
        triggered = bool(e1.get("ptl_triggered"))
        e1_pnl = float(_f(e1.get("pnl_yen_100") if triggered else t.get("pnl_yen_100")) or 0.0)
        e1_reason = str(e1.get("exit_reason") if triggered else t.get("exit_reason") or "")
        rec = {
            "date": t.get("date"),
            "symbol": t.get("symbol"),
            "anchor": t.get("anchor"),
            "row_key": key,
            "e0_pnl": e0_pnl,
            "e1_pnl": e1_pnl,
            "delta_pnl": float(e1_pnl) - float(e0_pnl),
            "e0_reason": t.get("exit_reason"),
            "e1_reason": e1_reason,
            "e0_exit_t": t.get("exit_time") if t.get("exit_time") is not None else t.get("exit_t"),
            "e1_exit_t": e1.get("exit_t") if triggered else (t.get("exit_time") if t.get("exit_time") is not None else t.get("exit_t")),
            "ptl_triggered": triggered,
            "ptl_holding_sec": e1.get("ptl_holding_sec") if triggered else None,
            "profit_armed": e1.get("profit_armed"),
            "BASELINE_L2": key in l2_keys,
            "e0_win": is_win(e0_pnl),
            "e0_loss": is_loss(e0_pnl),
            "e1_win": is_win(e1_pnl),
            "e1_loss": is_loss(e1_pnl),
        }
        rows.append(rec)

    e0_net = float(sum(float(r["e0_pnl"]) for r in rows))
    e1_net = float(sum(float(r["e1_pnl"]) for r in rows))
    e0_gl = float(sum(-float(r["e0_pnl"]) for r in rows if float(r["e0_pnl"]) < -PNL_EPS))
    e1_gl = float(sum(-float(r["e1_pnl"]) for r in rows if float(r["e1_pnl"]) < -PNL_EPS))
    l2_rows = [r for r in rows if r.get("BASELINE_L2")]
    ptl_n = sum(1 for r in rows if r.get("ptl_triggered"))
    l2_ptl = sum(1 for r in l2_rows if r.get("ptl_triggered"))
    l2_conv = sum(1 for r in l2_rows if not is_loss(r.get("e1_pnl")))
    l2_remain = sum(1 for r in l2_rows if is_loss(r.get("e1_pnl")))
    l2_reduced = sum(
        1
        for r in l2_rows
        if is_loss(r.get("e1_pnl")) and float(r["e1_pnl"]) > float(r["e0_pnl"]) + PNL_EPS
    )
    win_cut = [r for r in rows if r.get("e0_win") and r.get("ptl_triggered")]
    win_lost = float(sum(float(r["e0_pnl"]) - float(r["e1_pnl"]) for r in win_cut))
    loser_trig = [r for r in rows if r.get("e0_loss") and r.get("ptl_triggered")]
    loser_saved = float(sum(float(r["e1_pnl"]) - float(r["e0_pnl"]) for r in loser_trig))
    win_delta = float(sum(float(r["delta_pnl"]) for r in rows if r.get("e0_win")))
    loss_delta = float(sum(float(r["delta_pnl"]) for r in rows if r.get("e0_loss")))
    return {
        "rows": rows,
        "FIXED_TRADE_N": len(rows),
        "E0_FIXED_NET": e0_net,
        "E1_FIXED_NET": e1_net,
        "DELTA_FIXED_NET": e1_net - e0_net,
        "E0_GROSS_LOSS": e0_gl,
        "E1_GROSS_LOSS": e1_gl,
        "L2_BASE_N": len(l2_rows),
        "PTL_TRIGGER_N": ptl_n,
        "L2_PTL_TRIGGER_N": l2_ptl,
        "L2_CONVERTED_TO_NONLOSS_N": l2_conv,
        "L2_REMAIN_LOSS_N": l2_remain,
        "BASELINE_L2_REDUCED_LOSS_N": l2_reduced,
        "BASE_WIN_PREMATURE_EXIT_N": len(win_cut),
        "BASE_WIN_PNL_DELTA": win_delta,
        "TOTAL_WIN_PNL_DELTA": win_delta,
        "TOTAL_LOSS_PNL_DELTA": loss_delta,
        "WINNER_EARLY_CUT_N": len(win_cut),
        "WINNER_PNL_LOST_BY_GUARD": win_lost,
        "LOSER_PNL_SAVED_BY_GUARD": loser_saved,
        "NET_PNL_EFFECT_OF_GUARD": e1_net - e0_net,
    }


def e1_augment_exit_metrics(aug_trades: list[dict[str, Any]], fixed: dict[str, Any]) -> dict[str, Any]:
    ptl = [t for t in aug_trades if str(t.get("exit_reason") or "") == EXIT_REASON]
    pnls = [float(_f(t.get("pnl_yen_100")) or 0.0) for t in ptl]
    holds = []
    for t in ptl:
        ft = _f(t.get("fill_time") if t.get("fill_time") is not None else t.get("fill_t"))
        et = _f(t.get("exit_time") if t.get("exit_time") is not None else t.get("exit_t"))
        if ft is not None and et is not None:
            holds.append(float(et) - float(ft))
    win_n = sum(1 for p in pnls if p > PNL_EPS)
    loss_n = sum(1 for p in pnls if p < -PNL_EPS)
    return {
        "PTL_TRIGGER_N": len(ptl),
        "PTL_TRIGGER_WIN_N": win_n,
        "PTL_TRIGGER_LOSS_N": loss_n,
        "PTL_TRIGGER_FLAT_N": len(pnls) - win_n - loss_n,
        "PTL_NET_PNL": float(sum(pnls)) if pnls else 0.0,
        "PTL_MEDIAN_HOLDING_SEC": _median(holds),
        "BASELINE_L2_TARGETED_N": int(fixed.get("L2_BASE_N") or 0),
        "BASELINE_L2_AVOIDED_N": int(fixed.get("L2_CONVERTED_TO_NONLOSS_N") or 0),
        "BASELINE_L2_REDUCED_LOSS_N": int(fixed.get("BASELINE_L2_REDUCED_LOSS_N") or 0),
        "WINNER_EARLY_CUT_N": int(fixed.get("WINNER_EARLY_CUT_N") or 0),
        "WINNER_PNL_LOST_BY_GUARD": float(fixed.get("WINNER_PNL_LOST_BY_GUARD") or 0.0),
        "LOSER_PNL_SAVED_BY_GUARD": float(fixed.get("LOSER_PNL_SAVED_BY_GUARD") or 0.0),
        "NET_PNL_EFFECT_OF_GUARD": float(fixed.get("NET_PNL_EFFECT_OF_GUARD") or 0.0),
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


def decide_case(
    *,
    integrity_ok: bool,
    preservation_ok: bool,
    full_pass: bool,
    overlay_net: float,
    overlay_pf: float,
    overlay_dd: float,
    paired_median: Any,
    paired_pos: int,
    paired_neg: int,
    ex_best: Any,
    ex_top3: Any,
    winner_early_cut_n: int,
    l2_converted_n: int,
    l2_ptl_n: int,
    l2_reduced_n: int,
) -> dict[str, Any]:
    if not integrity_ok or not preservation_ok:
        return {
            "CASE": "E",
            "VERDICT": "AM_C0_EXIT_PTL_GUARD_INTEGRITY_OR_PORTFOLIO_FAILED",
            "NEXT": "STOP",
        }
    if full_pass:
        return {
            "CASE": "A",
            "VERDICT": "AM_C0_EXIT_PTL_GUARD_FULL_GATE_SUPPORTED",
            "NEXT": "AM_C0_EXIT_PTL_GUARD_FREEZE_REVIEW",
        }
    current_net = float(CURRENT_LOCKED["NET"])
    current_pf = float(CURRENT_LOCKED["PF"])
    current_dd = float(CURRENT_LOCKED["DD"])
    net_le = float(overlay_net) <= current_net
    pf_le = float(overlay_pf) <= current_pf
    if int(winner_early_cut_n) > 0 and (net_le or pf_le):
        return {
            "CASE": "C",
            "VERDICT": "AM_C0_EXIT_PTL_GUARD_DESTROYS_EDGE",
            "NEXT": "AM_EXIT_CONTINUATION_ARCHITECTURE_REASSESSMENT",
        }
    if net_le or pf_le:
        return {
            "CASE": "C",
            "VERDICT": "AM_C0_EXIT_PTL_GUARD_DESTROYS_EDGE",
            "NEXT": "AM_EXIT_CONTINUATION_ARCHITECTURE_REASSESSMENT",
        }
    beat_npd = (
        float(overlay_net) > current_net
        and _pf_num(overlay_pf) > current_pf
        and abs(float(overlay_dd)) <= abs(current_dd)
    )
    med = _f(paired_median)
    day_clear = med is not None and float(med) > 0.0 and int(paired_pos) > int(paired_neg)
    exb = _f(ex_best)
    ext = _f(ex_top3)
    tail_ok = exb is not None and float(exb) > 0.0 and ext is not None and float(ext) >= 0.0
    if beat_npd and day_clear and not tail_ok:
        return {
            "CASE": "B",
            "VERDICT": "AM_C0_EXIT_PTL_GUARD_PARTIAL_EDGE",
            "NEXT": "AM_EXIT_CONTINUATION_ARCHITECTURE_REASSESSMENT",
        }
    l2_helped = int(l2_converted_n) > 0 or int(l2_ptl_n) > 0 or int(l2_reduced_n) > 0
    if l2_helped and not day_clear:
        return {
            "CASE": "D",
            "VERDICT": "AM_C0_EXIT_PTL_GUARD_MECHANISM_VALID_NOT_SUFFICIENT",
            "NEXT": "AM_EXIT_CONTINUATION_ARCHITECTURE_REASSESSMENT",
        }
    if beat_npd:
        return {
            "CASE": "B",
            "VERDICT": "AM_C0_EXIT_PTL_GUARD_PARTIAL_EDGE",
            "NEXT": "AM_EXIT_CONTINUATION_ARCHITECTURE_REASSESSMENT",
        }
    return {
        "CASE": "D",
        "VERDICT": "AM_C0_EXIT_PTL_GUARD_MECHANISM_VALID_NOT_SUFFICIENT",
        "NEXT": "AM_EXIT_CONTINUATION_ARCHITECTURE_REASSESSMENT",
    }


def e1_full_gate(
    ov_pack: dict[str, Any],
    base_pack: dict[str, Any],
    paired: dict[str, Any],
    *,
    preservation_ok: bool,
    integrity_ok: bool,
) -> dict[str, Any]:
    gate = overlay_gate(
        ov_pack,
        base_pack,
        paired,
        preservation_pass=bool(preservation_ok),
        integrity_ok=bool(integrity_ok),
    )
    gates = dict(gate.get("gates") or {})
    return {
        "gates": gates,
        "E1_FULL_PASS": bool(gate.get("AUGMENT_OVERLAY_PASS")),
        "CURRENT_PRESERVATION_PASS": bool(preservation_ok),
    }


def join_l2_keys(path_rows: list[dict[str, Any]]) -> set[str]:
    tagged = attach_classes(path_rows)
    out = set()
    for r in tagged:
        if r.get("LOSS_CLASS") != L2:
            continue
        out.add(str(r.get("row_key") or row_key(r)))
    return out


def preservation_ok(pres: dict[str, Any]) -> bool:
    return bool(preservation_pass(pres))
