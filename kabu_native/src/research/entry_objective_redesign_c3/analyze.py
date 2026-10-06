"""C3 OOF ranking gate, paired days, Exact success bar. No B/UNIFORM10 gate. No occupancy."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.entry_objective_redesign_c3 import (
    A0_DD_TOL,
    A0_PF_TOL,
    A0_PNL_TOL,
    A0_TRADE_TOL,
    A2_DD_TOL,
    A2_PF_TOL,
    A2_PNL_TOL,
    A2_TRADE_TOL,
    EXPECTED_A0_MAXDD,
    EXPECTED_A0_PF,
    EXPECTED_A0_PNL,
    EXPECTED_A0_TRADES,
    EXPECTED_A2_MAXDD,
    EXPECTED_A2_PF,
    EXPECTED_A2_PNL,
    EXPECTED_A2_TRADES,
    MIN_TRADE_RETENTION_VS_A2,
)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _pf(v: Any) -> float:
    if v is None:
        return 0.0
    if v == "Infinity" or v == float("inf"):
        return 9.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parity_pack(pack: dict[str, Any], *, expected: dict[str, Any], tols: dict[str, float]) -> dict[str, Any]:
    trades = int(pack.get("trades") or 0)
    pnl = float(pack.get("PnL") if pack.get("PnL") is not None else pack.get("pnl") or 0.0)
    pf = _pf(pack.get("PF"))
    dd = float(pack.get("maxDD") or 0.0)
    ok = (
        abs(trades - int(expected["trades"])) <= int(tols["trades"])
        and abs(pnl - float(expected["PnL"])) <= float(tols["PnL"])
        and abs(pf - float(expected["PF"])) <= float(tols["PF"])
        and abs(dd - float(expected["maxDD"])) <= float(tols["maxDD"])
    )
    return {
        "ok": ok,
        "observed": {"trades": trades, "PnL": pnl, "PF": pack.get("PF"), "maxDD": dd},
        "expected": dict(expected),
    }


def a0_parity(pack: dict[str, Any]) -> dict[str, Any]:
    return parity_pack(
        pack,
        expected={
            "trades": EXPECTED_A0_TRADES,
            "PnL": EXPECTED_A0_PNL,
            "PF": EXPECTED_A0_PF,
            "maxDD": EXPECTED_A0_MAXDD,
        },
        tols={"trades": A0_TRADE_TOL, "PnL": A0_PNL_TOL, "PF": A0_PF_TOL, "maxDD": A0_DD_TOL},
    )


def a2_parity(pack: dict[str, Any]) -> dict[str, Any]:
    return parity_pack(
        pack,
        expected={
            "trades": EXPECTED_A2_TRADES,
            "PnL": EXPECTED_A2_PNL,
            "PF": EXPECTED_A2_PF,
            "maxDD": EXPECTED_A2_MAXDD,
        },
        tols={"trades": A2_TRADE_TOL, "PnL": A2_PNL_TOL, "PF": A2_PF_TOL, "maxDD": A2_DD_TOL},
    )


def _day_map(days: list[dict[str, Any]], field: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in days or []:
        v = _f(r.get(field))
        if v is None:
            continue
        out[str(r.get("date"))] = float(v)
    return out


def paired_daily(current: dict[str, Any], c3: dict[str, Any]) -> dict[str, Any]:
    cur3 = _day_map(current.get("daily_top3") or [], "TOP3_UPLIFT")
    new3 = _day_map(c3.get("daily_top3") or [], "TOP3_UPLIFT")
    cur1 = _day_map(current.get("daily_top1") or [], "TOP1_UPLIFT")
    new1 = _day_map(c3.get("daily_top1") or [], "TOP1_UPLIFT")
    cur5 = _day_map(current.get("daily_top5") or [], "TOP5_UPLIFT")
    new5 = _day_map(c3.get("daily_top5") or [], "TOP5_UPLIFT")
    days = sorted(set(cur3) | set(new3))
    rows = []
    deltas = []
    for d in days:
        a = cur3.get(d)
        b = new3.get(d)
        delta = (b - a) if a is not None and b is not None else None
        if delta is not None:
            deltas.append(delta)
        rows.append(
            {
                "date": d,
                "CURRENT_TOP3_UPLIFT": a,
                "C3_TOP3_UPLIFT": b,
                "DELTA_TOP3_UPLIFT": delta,
                "CURRENT_TOP1_UPLIFT": cur1.get(d),
                "C3_TOP1_UPLIFT": new1.get(d),
                "DELTA_TOP1_UPLIFT": (new1[d] - cur1[d]) if d in new1 and d in cur1 else None,
                "CURRENT_TOP5_UPLIFT": cur5.get(d),
                "C3_TOP5_UPLIFT": new5.get(d),
                "DELTA_TOP5_UPLIFT": (new5[d] - cur5[d]) if d in new5 and d in cur5 else None,
            }
        )
    pos = sum(1 for v in deltas if v > 0)
    neg = sum(1 for v in deltas if v < 0)
    ordered = sorted(deltas, reverse=True)
    rest_best = ordered[1:] if len(ordered) > 1 else []
    rest_top3 = ordered[3:] if len(ordered) > 3 else []
    return {
        "days": rows,
        "TOP3_DELTA_MEAN": float(np.mean(deltas)) if deltas else None,
        "TOP3_DELTA_MEDIAN": float(np.median(deltas)) if deltas else None,
        "TOP3_DELTA_POSITIVE_DAYS": pos,
        "TOP3_DELTA_NEGATIVE_DAYS": neg,
        "TOP3_DELTA_EX_BEST_DAY": float(np.mean(rest_best)) if rest_best else None,
        "TOP3_DELTA_EX_TOP3_DAYS": float(np.mean(rest_top3)) if rest_top3 else None,
        "n_paired_days": len(deltas),
    }


def ranking_gate(current: dict[str, Any], c3: dict[str, Any], paired: dict[str, Any]) -> dict[str, Any]:
    c3_t3 = _f(c3.get("TOP3_UPLIFT"))
    cur_t3 = _f(current.get("TOP3_UPLIFT"))
    c3_t1 = _f(c3.get("TOP1_UPLIFT"))
    cur_t1 = _f(current.get("TOP1_UPLIFT"))
    c3_t5 = _f(c3.get("TOP5_UPLIFT"))
    cur_t5 = _f(current.get("TOP5_UPLIFT"))
    c3_sp = _f(c3.get("MEAN_DAILY_SPEARMAN"))
    dmean = _f(paired.get("TOP3_DELTA_MEAN"))
    dpos = int(paired.get("TOP3_DELTA_POSITIVE_DAYS") or 0)
    dneg = int(paired.get("TOP3_DELTA_NEGATIVE_DAYS") or 0)
    ex1 = _f(paired.get("TOP3_DELTA_EX_BEST_DAY"))
    ex3 = _f(paired.get("TOP3_DELTA_EX_TOP3_DAYS"))
    checks = {
        "A_C3_TOP3_GT_0": bool(c3_t3 is not None and c3_t3 > 0),
        "B_C3_TOP3_GT_CURRENT": bool(c3_t3 is not None and cur_t3 is not None and c3_t3 > cur_t3),
        "C_DELTA_MEAN_GT_0": bool(dmean is not None and dmean > 0),
        "D_POS_DAYS_GE_NEG": bool(dpos >= dneg),
        "E_TOP1_GE_CURRENT": bool(c3_t1 is not None and cur_t1 is not None and c3_t1 >= cur_t1),
        "F_TOP5_GE_CURRENT": bool(c3_t5 is not None and cur_t5 is not None and c3_t5 >= cur_t5),
        "G_TOP1_ABS_GE_0": bool(c3_t1 is not None and c3_t1 >= 0),
        "H_TOP5_ABS_GE_0": bool(c3_t5 is not None and c3_t5 >= 0),
        "I_SPEARMAN_GE_0": bool(c3_sp is not None and c3_sp >= 0),
        "J_EX_BEST_DAY_DELTA_GT_0": bool(ex1 is not None and ex1 > 0),
        "K_EX_TOP3_DAYS_DELTA_GE_0": bool(ex3 is not None and ex3 >= 0),
    }
    fail = [k for k, v in checks.items() if not v]
    return {
        **checks,
        "fail": fail,
        "OOF_RANKING_GATE_PASS": len(fail) == 0,
        "core_edge_fail": any(k in fail for k in ("A_C3_TOP3_GT_0", "B_C3_TOP3_GT_CURRENT", "C_DELTA_MEAN_GT_0")),
    }


def _pnl(pack: dict[str, Any]) -> Optional[float]:
    v = pack.get("PnL")
    if v is None:
        v = pack.get("pnl")
    return _f(v)


def _dd(pack: dict[str, Any]) -> Optional[float]:
    return _f(pack.get("maxDD"))


def exact_success(c3: dict[str, Any], a0: dict[str, Any], a2: dict[str, Any], *, gate_pass: bool) -> dict[str, Any]:
    c3_pnl = _pnl(c3) if c3 else None
    a0_pnl = _pnl(a0) if a0 else None
    a2_pnl = _pnl(a2) if a2 else None
    c3_pf = _pf(c3.get("PF")) if c3 else None
    a0_pf = _pf(a0.get("PF")) if a0 else None
    c3_dd = _dd(c3) if c3 else None
    a0_dd = _dd(a0) if a0 else None
    c3_tr = int(c3.get("trades") or 0) if c3 else 0
    a2_tr = int(a2.get("trades") or 0) if a2 else 0
    med_c3 = _f((c3 or {}).get("median_daily_pnl"))
    med_a0 = _f((a0 or {}).get("median_daily_pnl"))
    pos_c3 = _f((c3 or {}).get("positive_day_rate"))
    pos_a0 = _f((a0 or {}).get("positive_day_rate"))
    ex_tr_c3 = _pnl(((c3 or {}).get("exclude") or {}).get("ex_top3_trades") or {})
    ex_tr_a0 = _pnl(((a0 or {}).get("exclude") or {}).get("ex_top3_trades") or {})
    ex_d_c3 = _pnl(((c3 or {}).get("exclude") or {}).get("ex_top3_days") or {})
    ex_d_a0 = _pnl(((a0 or {}).get("exclude") or {}).get("ex_top3_days") or {})
    beats_a2 = bool(c3_pnl is not None and a2_pnl is not None and c3_pnl > a2_pnl)
    beats_a0_pnl = bool(c3_pnl is not None and a0_pnl is not None and c3_pnl >= a0_pnl)
    pf_ok = bool(c3_pf is not None and a0_pf is not None and c3_pf >= a0_pf)
    dd_ok = bool(c3_dd is not None and a0_dd is not None and c3_dd >= a0_dd)
    day_ok = bool(
        (med_c3 is not None and med_a0 is not None and med_c3 >= med_a0)
        or (pos_c3 is not None and pos_a0 is not None and pos_c3 > pos_a0)
    )
    n_ok = bool(a2_tr > 0 and c3_tr >= MIN_TRADE_RETENTION_VS_A2 * a2_tr)
    tail_tr_ok = bool(ex_tr_c3 is not None and ex_tr_a0 is not None and ex_tr_c3 >= ex_tr_a0)
    tail_d_ok = bool(ex_d_c3 is not None and ex_d_a0 is not None and ex_d_c3 >= ex_d_a0)
    candidate = bool(
        gate_pass and beats_a2 and beats_a0_pnl and pf_ok and dd_ok and day_ok and n_ok and tail_tr_ok and tail_d_ok
    )
    return {
        "C3_PNL_GT_A2": beats_a2,
        "C3_PNL_GE_A0": beats_a0_pnl,
        "C3_PF_GE_A0": pf_ok,
        "C3_MAXDD_GE_A0": dd_ok,
        "C3_DAILY_VS_A0": day_ok,
        "C3_TRADES_GE_70PCT_A2": n_ok,
        "C3_EX_TOP3_TRADES_GE_A0": tail_tr_ok,
        "C3_EX_TOP3_DAYS_GE_A0": tail_d_ok,
        "OOF_GATE": gate_pass,
        "HISTORICAL_ENTRY_CANDIDATE": candidate,
        "C3_VS_A2_PNL": (c3_pnl - a2_pnl) if c3_pnl is not None and a2_pnl is not None else None,
        "C3_VS_A0_PNL": (c3_pnl - a0_pnl) if c3_pnl is not None and a0_pnl is not None else None,
    }


def abc_line(pack: dict[str, Any] | None) -> str:
    if not pack:
        return "n/a"
    return f"{pack.get('trades')} / {pack.get('PnL') if pack.get('PnL') is not None else pack.get('pnl')} / {pack.get('PF')} / {pack.get('maxDD')}"


def verdict(
    *,
    integrity_ok: bool,
    gate: dict[str, Any],
    exact_ran: bool,
    success: dict[str, Any] | None,
) -> tuple[str, str]:
    if not integrity_ok:
        return (
            "C3_INTEGRITY_FAILED",
            "A0/A2 parity, extract, or TARGET V4 integrity failed. Exact C3 not used as a candidate.",
        )
    if not gate.get("OOF_RANKING_GATE_PASS"):
        if gate.get("core_edge_fail"):
            return (
                "C3_NO_TOP_OF_RANK_EDGE",
                "OOF Top3 concentration did not beat CURRENT on the executable-at-t0 universe. Exact Dual-Lane not run.",
            )
        return (
            "C3_TOP_EDGE_NOT_STABLE",
            "Some Top-of-rank improvement appeared, but precommitted paired/robustness/Top1-Top5 gates failed. Exact Dual-Lane not run.",
        )
    if not exact_ran:
        return (
            "C3_TOP_EDGE_SUPPORTED_PORTFOLIO_FAIL",
            "OOF ranking gate passed but Exact Dual-Lane was not run. Integrity stop.",
        )
    s = success or {}
    if s.get("HISTORICAL_ENTRY_CANDIDATE"):
        return (
            "C3_HISTORICAL_ENTRY_CANDIDATE",
            "HISTORICAL_DEVELOPMENT_CANDIDATE_ONLY. TRUE_OOS=false NEW_FORWARD_N=0. Not a Runtime candidate. Do not implement C3 this run.",
        )
    if s.get("C3_PNL_GT_A2") and not s.get("HISTORICAL_ENTRY_CANDIDATE"):
        return (
            "C3_SCORE_IMPROVES_A2_BUT_FAILS_A0",
            "C3 beat A2 (same eligibility) but did not clear the A0 historical candidate bar. Not adopted.",
        )
    return (
        "C3_TOP_EDGE_SUPPORTED_PORTFOLIO_FAIL",
        "OOF ranking gate passed; Exact Dual-Lane did not beat A2. Not a historical ENTRY candidate.",
    )


def slim_pack(pack: dict[str, Any]) -> dict[str, Any]:
    return {
        k: pack.get(k)
        for k in (
            "trades",
            "PnL",
            "PF",
            "maxDD",
            "avg_trade",
            "median_trade",
            "positive_day_rate",
            "median_daily_pnl",
            "AM",
            "PM",
            "first_entry",
            "re_entry",
        )
    }
