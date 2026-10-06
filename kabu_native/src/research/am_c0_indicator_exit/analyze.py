"""Fixed-trade effect, full economic gate, CASE A-F. No threshold search."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.am_c0_indicator_exit import C0_OVERLAY_LOCKED, CURRENT_LOCKED
from research.am_c0_exit_ptl_guard.analyze import identity_audit, preservation_ok
from research.am_current_utility_augment.analyze import overlay_gate
from research.am_entry_profit_improvement.metrics import _pf_num
from research.am_exit_contribution_rca.analyze import L2, attach_classes, is_loss, is_win
from research.canonical_entry_performance_rebase.analyze import _f, row_key

PNL_EPS = 1e-9


def _trade_key(t: dict[str, Any]) -> str:
    return str(t.get("row_key") or row_key(t))


def join_l2_keys(path_rows: list[dict[str, Any]]) -> set[str]:
    tagged = attach_classes(path_rows)
    out = set()
    for r in tagged:
        if r.get("LOSS_CLASS") != L2:
            continue
        out.add(str(r.get("row_key") or row_key(r)))
    return out


def fixed_trade_effect(
    e0_trades: list[dict[str, Any]],
    eind_by: dict[str, dict[str, Any]],
    l2_keys: set[str],
) -> dict[str, Any]:
    rows = []
    for t in e0_trades:
        key = _trade_key(t)
        e0_pnl = float(_f(t.get("pnl_yen_100")) or 0.0)
        e1 = eind_by.get(key) or {}
        e1_pnl = float(_f(e1.get("pnl_yen_100")) if e1.get("ok") else e0_pnl)
        rec = {
            "date": t.get("date"),
            "symbol": t.get("symbol"),
            "anchor": t.get("anchor"),
            "row_key": key,
            "e0_pnl": e0_pnl,
            "e1_pnl": e1_pnl,
            "delta_pnl": float(e1_pnl) - float(e0_pnl),
            "e0_reason": t.get("exit_reason"),
            "e1_reason": e1.get("exit_reason"),
            "e0_exit_t": t.get("exit_time") if t.get("exit_time") is not None else t.get("exit_t"),
            "e1_exit_t": e1.get("exit_t"),
            "triggered": bool(e1.get("triggered")),
            "p_exit": e1.get("p_exit"),
            "BASELINE_L2": key in l2_keys,
            "e0_win": is_win(e0_pnl),
            "e0_loss": is_loss(e0_pnl),
            "e1_win": is_win(e1_pnl),
            "e1_loss": is_loss(e1_pnl),
        }
        rows.append(rec)
    e0_net = float(sum(float(r["e0_pnl"]) for r in rows))
    e1_net = float(sum(float(r["e1_pnl"]) for r in rows))
    improved = sum(1 for r in rows if float(r["delta_pnl"]) > PNL_EPS)
    worsened = sum(1 for r in rows if float(r["delta_pnl"]) < -PNL_EPS)
    unchanged = len(rows) - improved - worsened
    l2_rows = [r for r in rows if r.get("BASELINE_L2")]
    l2_imp = sum(1 for r in l2_rows if float(r["delta_pnl"]) > PNL_EPS)
    l2_wors = sum(1 for r in l2_rows if float(r["delta_pnl"]) < -PNL_EPS)
    l2_conv = sum(1 for r in l2_rows if not is_loss(r.get("e1_pnl")))
    win_prem = [r for r in rows if r.get("e0_win") and float(r["e1_pnl"]) < float(r["e0_pnl"]) - PNL_EPS]
    win_delta = float(sum(float(r["delta_pnl"]) for r in rows if r.get("e0_win")))
    loss_delta = float(sum(float(r["delta_pnl"]) for r in rows if r.get("e0_loss")))
    e0_gp = float(sum(float(r["e0_pnl"]) for r in rows if float(r["e0_pnl"]) > PNL_EPS))
    e0_gl = float(sum(-float(r["e0_pnl"]) for r in rows if float(r["e0_pnl"]) < -PNL_EPS))
    e1_gp = float(sum(float(r["e1_pnl"]) for r in rows if float(r["e1_pnl"]) > PNL_EPS))
    e1_gl = float(sum(-float(r["e1_pnl"]) for r in rows if float(r["e1_pnl"]) < -PNL_EPS))

    def _pf(gp: float, gl: float) -> Optional[float]:
        if gl <= PNL_EPS:
            return float("inf") if gp > PNL_EPS else None
        return gp / gl

    return {
        "rows": rows,
        "FIXED_TRADE_N": len(rows),
        "E0_FIXED_NET": e0_net,
        "EIND_FIXED_NET": e1_net,
        "FIXED_DELTA_NET": e1_net - e0_net,
        "E0_FIXED_PF": _pf(e0_gp, e0_gl),
        "EIND_FIXED_PF": _pf(e1_gp, e1_gl),
        "IMPROVED_TRADE_N": improved,
        "WORSENED_TRADE_N": worsened,
        "UNCHANGED_TRADE_N": unchanged,
        "WINNER_PNL_DELTA": win_delta,
        "LOSER_PNL_DELTA": loss_delta,
        "L2_BASE_N": len(l2_rows),
        "L2_IMPROVED_N": l2_imp,
        "L2_WORSENED_N": l2_wors,
        "L2_NONLOSS_CONVERTED_N": l2_conv,
        "WINNER_PREMATURE_EXIT_N": len(win_prem),
        "EXIT_TRIGGER_N": sum(1 for r in rows if r.get("triggered")),
        "SESSION_END_N": sum(1 for r in rows if not r.get("triggered")),
    }


def vs_c0(e1: dict[str, Any]) -> dict[str, Any]:
    c0 = C0_OVERLAY_LOCKED
    pos = int(e1.get("PAIRED_POS_DAYS") or 0)
    neg = int(e1.get("PAIRED_NEG_DAYS") or 0)
    c0_gap = int(c0["PAIRED_POS_DAYS"]) - int(c0["PAIRED_NEG_DAYS"])
    return {
        "DELTA_NET_VS_C0_C14": float(e1.get("OVERLAY_NET_PNL") or 0.0) - float(c0["NET"]),
        "DELTA_PF_VS_C0_C14": _pf_num(e1.get("OVERLAY_PF")) - float(c0["PF"]),
        "DELTA_DD_VS_C0_C14": float(e1.get("OVERLAY_MAX_DD") or 0.0) - float(c0["DD"]),
        "DELTA_PAIRED_MEDIAN_VS_C0_C14": (
            None
            if e1.get("PAIRED_MEDIAN_DAILY_DELTA") is None
            else float(e1.get("PAIRED_MEDIAN_DAILY_DELTA")) - float(c0["PAIRED_MEDIAN"])
        ),
        "DELTA_POS_MINUS_NEG_VS_C0_C14": float((pos - neg) - c0_gap),
        "DELTA_EX_BEST_VS_C0_C14": (
            None
            if e1.get("EX_BEST_DAY_PNL_DELTA") is None
            else float(e1.get("EX_BEST_DAY_PNL_DELTA")) - float(c0["EX_BEST"])
        ),
        "DELTA_EX_TOP3_VS_C0_C14": (
            None
            if e1.get("EX_TOP3_DAYS_PNL_DELTA") is None
            else float(e1.get("EX_TOP3_DAYS_PNL_DELTA")) - float(c0["EX_TOP3"])
        ),
    }


def eind_full_gate(
    ov_pack: dict[str, Any],
    base_pack: dict[str, Any],
    paired: dict[str, Any],
    *,
    preservation_ok_flag: bool,
    integrity_ok: bool,
    non_interference_ok: bool,
) -> dict[str, Any]:
    gate = overlay_gate(
        ov_pack,
        base_pack,
        paired,
        preservation_pass=bool(preservation_ok_flag),
        integrity_ok=bool(integrity_ok),
    )
    gates = dict(gate.get("gates") or {})
    gates["J_NON_INTERFERENCE"] = bool(non_interference_ok)
    return {
        "gates": gates,
        "EIND_FULL_PASS": bool(all(gates.values())),
        "CURRENT_PRESERVATION_PASS": bool(preservation_ok_flag),
        "NON_INTERFERENCE_PASS": bool(non_interference_ok),
    }


def decide_case(
    *,
    integrity_ok: bool,
    non_interference_ok: bool,
    preservation_ok_flag: bool,
    full_pass: bool,
    overlay_net: float,
    overlay_pf: float,
    overlay_dd: float,
    paired_median: Any,
    paired_pos: int,
    paired_neg: int,
    ex_best: Any,
    ex_top3: Any,
    vs: dict[str, Any],
) -> dict[str, Any]:
    if not non_interference_ok:
        return {
            "CASE": "F",
            "VERDICT": "AM_C0_INDICATOR_EXIT_RUNTIME_INTERFERENCE_FAILED",
            "NEXT": "RESEARCH_ISOLATION_FIX_ONLY",
        }
    if not integrity_ok or not preservation_ok_flag:
        return {
            "CASE": "E",
            "VERDICT": "AM_C0_INDICATOR_EXIT_INTEGRITY_FAILED",
            "NEXT": "AM_C0_INDICATOR_EXIT_RESEARCH_CLOSE",
        }
    current_net = float(CURRENT_LOCKED["NET"])
    current_pf = float(CURRENT_LOCKED["PF"])
    d_net = float(vs.get("DELTA_NET_VS_C0_C14") or 0.0)
    d_pf = float(vs.get("DELTA_PF_VS_C0_C14") or 0.0)
    d_med = vs.get("DELTA_PAIRED_MEDIAN_VS_C0_C14")
    d_gap = float(vs.get("DELTA_POS_MINUS_NEG_VS_C0_C14") or 0.0)
    d_exb = vs.get("DELTA_EX_BEST_VS_C0_C14")
    d_ext = vs.get("DELTA_EX_TOP3_VS_C0_C14")
    day_vs_c0 = (
        d_med is not None
        and float(d_med) > 0.0
        and d_gap > 0.0
        and d_exb is not None
        and float(d_exb) > 0.0
        and d_ext is not None
        and float(d_ext) >= 0.0
    )
    major_vs_c0 = d_net > 0.0 and d_pf > 0.0 and day_vs_c0
    beats_current = float(overlay_net) > current_net and _pf_num(overlay_pf) > current_pf
    if full_pass and major_vs_c0:
        return {
            "CASE": "A",
            "VERDICT": "AM_C0_INDICATOR_EXIT_FULL_GATE_SUPPORTED",
            "NEXT": "AM_C0_INDICATOR_EXIT_PROSPECTIVE_FREEZE_REVIEW",
        }
    if beats_current and day_vs_c0 and not full_pass:
        return {
            "CASE": "B",
            "VERDICT": "AM_C0_INDICATOR_EXIT_PARTIAL_ROBUSTNESS_EDGE",
            "NEXT": "AM_C0_INDICATOR_EXIT_RESEARCH_CLOSE",
        }
    if d_net > 0.0 and d_pf > 0.0 and not day_vs_c0:
        return {
            "CASE": "C",
            "VERDICT": "AM_C0_INDICATOR_EXIT_ECONOMIC_EDGE_NOT_DAY_ROBUST",
            "NEXT": "AM_C0_INDICATOR_EXIT_RESEARCH_CLOSE",
        }
    if (not beats_current) or d_net <= 0.0 or d_pf <= 0.0:
        return {
            "CASE": "D",
            "VERDICT": "AM_C0_INDICATOR_EXIT_NOT_SUPPORTED",
            "NEXT": "AM_C0_INDICATOR_EXIT_RESEARCH_CLOSE",
        }
    return {
        "CASE": "B",
        "VERDICT": "AM_C0_INDICATOR_EXIT_PARTIAL_ROBUSTNESS_EDGE",
        "NEXT": "AM_C0_INDICATOR_EXIT_RESEARCH_CLOSE",
    }
