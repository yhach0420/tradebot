"""C2 gates and verdict. No PnL in ranking selection."""
from __future__ import annotations

from typing import Any, Optional

from research.uniform10_entry_rebuild_v2 import (
    EXPECTED_B0_MAXDD,
    EXPECTED_B0_PF,
    EXPECTED_B0_PNL,
    EXPECTED_B0_TRADES,
)


def b0_match(b: dict[str, Any]) -> bool:
    trades = int(b.get("trades") or 0)
    pnl = float(b.get("PnL") if b.get("PnL") is not None else b.get("pnl") or 0.0)
    pf = b.get("PF")
    dd = float(b.get("maxDD") or 0.0)
    if trades != int(EXPECTED_B0_TRADES):
        return False
    if abs(pnl - float(EXPECTED_B0_PNL)) > 1.0:
        return False
    try:
        if abs(float(pf) - float(EXPECTED_B0_PF)) > 1e-5:
            return False
    except (TypeError, ValueError):
        return False
    if abs(dd - float(EXPECTED_B0_MAXDD)) > 1.0:
        return False
    return True


def ranking_gate(*, new_m: dict[str, Any], cur_m: dict[str, Any], rank: dict[str, Any], rob: dict[str, Any]) -> dict[str, Any]:
    sp = new_m.get("MEAN_DAILY_SPEARMAN")
    cur = cur_m.get("MEAN_DAILY_SPEARMAN")
    pos = int(new_m.get("positive_day_count") or 0)
    neg = int(new_m.get("negative_day_count") or 0)
    beats = bool(sp is not None and cur is not None and float(sp) > float(cur) + 1e-6)
    pos_sp = bool(sp is not None and float(sp) > 0)
    day_ok = bool(pos >= 8 and pos > neg and not rob.get("RANKING_NOT_ROBUST"))
    mono = bool(rank.get("monotonic_ok"))
    passed = bool(pos_sp and beats and mono and day_ok)
    return {
        "OOF_MEAN_DAILY_SPEARMAN_POSITIVE": pos_sp,
        "BEATS_CURRENT": beats,
        "MONOTONIC_OK": mono,
        "DAY_STABLE": day_ok,
        "RANKING_GATE_PASS": passed,
        "OOF_MEAN_DAILY_SPEARMAN": sp,
        "CURRENT_SCORE_OOF_SPEARMAN": cur,
        "NEW_SCORE_OOF_SPEARMAN": sp,
    }


def portfolio_gate(*, c2: dict[str, Any], tail: dict[str, Any]) -> dict[str, Any]:
    pf = c2.get("PF")
    try:
        pf_ok = pf is not None and pf != "Infinity" and float(pf) > 1.0
    except (TypeError, ValueError):
        pf_ok = False
    med = c2.get("median_daily_pnl")
    med_ok = med is not None and float(med) >= 0
    pnl = float(c2.get("PnL") if c2.get("PnL") is not None else c2.get("pnl") or 0.0)
    ex3t = float((tail.get("ex_top3_trades") or {}).get("pnl") or 0.0)
    ex3d = float((tail.get("ex_top3_days") or {}).get("pnl") or 0.0)
    exs = float((tail.get("ex_top_symbol") or {}).get("pnl") or 0.0)
    trade_dep = bool(pnl > 0 and ex3t <= 0)
    day_dep = bool(pnl > 0 and ex3d <= 0)
    top_share = (pnl - exs) / pnl if pnl else 0.0
    sym_dep = bool(top_share > 0.50 or (pnl > 0 and exs <= 0))
    passed = bool(pf_ok and med_ok and not trade_dep and not day_dep and not sym_dep)
    return {
        "PF_GT_1": pf_ok,
        "MEDIAN_DAILY_PNL_GE_0": med_ok,
        "extreme_single_trade_dependence": trade_dep,
        "extreme_single_day_dependence": day_dep,
        "extreme_single_symbol_dependence": sym_dep,
        "top_symbol_pnl_share": top_share,
        "PORTFOLIO_GATE_PASS": passed,
    }


def verdict(*, rebase_stop: bool, ranking: dict[str, Any], portfolio_ran: bool, port: Optional[dict[str, Any]]) -> tuple[str, str]:
    if rebase_stop:
        return (
            "C2_REBUILD_INTEGRITY_FAILED",
            "STOP. Unexpected TARGET missing is material. Do not search models. Do not start C Runtime. B remains closed.",
        )
    if not ranking.get("RANKING_GATE_PASS"):
        if not ranking.get("OOF_MEAN_DAILY_SPEARMAN_POSITIVE") or not ranking.get("BEATS_CURRENT"):
            return (
                "C2_NO_OOF_RANKING_EDGE",
                "Do not start Runtime C. Nested OOF ranking did not beat CURRENT ENTRY with a stable positive Spearman. No further mark-source search. B remains closed.",
            )
        return (
            "C2_OOF_RANKING_EDGE_NOT_STABLE",
            "Do not start Runtime C. OOF Spearman is not day-stable or TopK is not monotonic enough. Do not chase PnL.",
        )
    if not portfolio_ran:
        return (
            "C2_OOF_RANKING_SUPPORTED_PORTFOLIO_FAIL",
            "Ranking gate passed but exact portfolio was not run. Integrity stop.",
        )
    if not (port or {}).get("PORTFOLIO_GATE_PASS"):
        return (
            "C2_OOF_RANKING_SUPPORTED_PORTFOLIO_FAIL",
            "OOF ranking passed; Dual-Lane exact portfolio failed PF/median-day/tail gates. Do not create a Runtime candidate.",
        )
    return (
        "C2_HISTORICAL_CANDIDATE_SUPPORTED",
        "HISTORICAL_DEVELOPMENT_CANDIDATE_ONLY. TRUE_OOS=false NEW_FORWARD_N=0. Not a Runtime candidate. Not formal certification. Do not implement into Runtime this run. Await explicit next instruction.",
    )
