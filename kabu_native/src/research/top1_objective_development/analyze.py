"""Top1 OOF gate A-J, Exact success bar, CASE A-D. Gates frozen."""
from __future__ import annotations

from typing import Any, Optional

from research.canonical_entry_performance_rebase.analyze import _f
from research.entry_rank_shape_audit.oof import top1_delta_stats
from research.top1_objective_development import (
    MAX_POS_DELTA_SHARE,
    POS_DAYS_DENOM,
    POS_DAYS_MIN,
)


def _pnl(pack: dict[str, Any] | None) -> Optional[float]:
    if not pack:
        return None
    v = pack.get("PnL")
    if v is None:
        v = pack.get("pnl")
    return _f(v)


def _dd(pack: dict[str, Any] | None) -> Optional[float]:
    if not pack:
        return None
    return _f(pack.get("maxDD"))


def max_positive_day_contribution(deltas: list[float]) -> Optional[float]:
    pos = [float(v) for v in deltas if v > 0]
    if not pos:
        return None
    s = float(sum(pos))
    if s <= 0:
        return None
    return float(max(pos) / s)


def top1_oof_gate(
    *,
    model: dict[str, Any],
    current: dict[str, Any],
    paired: dict[str, Any],
    integrity_ok: bool,
) -> dict[str, Any]:
    t1 = _f(model.get("TOP1_UPLIFT"))
    cur = _f(current.get("TOP1_UPLIFT"))
    stats = top1_delta_stats(paired)
    deltas = []
    for r in paired.get("days") or []:
        v = _f(r.get("DELTA_TOP1_UPLIFT"))
        if v is not None:
            deltas.append(float(v))
    contrib = max_positive_day_contribution(deltas)
    n_days = int(stats.get("n_days") or 0)
    pos = int(stats.get("positive_days") or 0)
    neg = int(stats.get("negative_days") or 0)
    mean = stats.get("mean")
    med = stats.get("median")
    ex1 = stats.get("ex_best_day")
    ex3 = stats.get("ex_top3_days")
    checks = {
        "A_TOP1_UPLIFT_GT_0": bool(t1 is not None and t1 > 0),
        "B_TOP1_GT_CURRENT": bool(t1 is not None and cur is not None and t1 > cur),
        "C_DELTA_MEAN_GT_0": bool(mean is not None and float(mean) > 0),
        "D_DELTA_MEDIAN_GT_0": bool(med is not None and float(med) > 0),
        "E_POS_DAYS_GE_12_OF_18": bool(n_days == int(POS_DAYS_DENOM) and pos >= int(POS_DAYS_MIN)),
        "F_POS_DAYS_GT_NEG": bool(pos > neg),
        "G_EX_BEST_DAY_DELTA_GT_0": bool(ex1 is not None and float(ex1) > 0),
        "H_EX_TOP3_DAYS_DELTA_GT_0": bool(ex3 is not None and float(ex3) > 0),
        "I_MAX_DAY_CONTRIB_LE_50PCT": bool(contrib is not None and float(contrib) <= float(MAX_POS_DELTA_SHARE)),
        "J_TARGET_INTEGRITY": bool(integrity_ok),
    }
    fail = [k for k, v in checks.items() if not v]
    return {
        **checks,
        "fail": fail,
        "TOP1_OOF_GATE_PASS": len(fail) == 0,
        "TOP1_MAX_DAY_CONTRIBUTION": contrib,
        "top1_delta": stats,
        "n_paired_days": n_days,
    }


def rank1_exact_success(
    oof: dict[str, Any] | None,
    a0: dict[str, Any] | None,
    a2: dict[str, Any] | None,
    *,
    gate_pass: bool,
) -> dict[str, Any]:
    """Precommitted historical bar. RANK1_ONLY does not require 70% A2 trade count."""
    oof_pnl = _pnl(oof)
    a0_pnl = _pnl(a0)
    a2_pnl = _pnl(a2)
    oof_pf = _f((oof or {}).get("PF"))
    a0_pf = _f((a0 or {}).get("PF"))
    oof_dd = _dd(oof)
    a0_dd = _dd(a0)
    med_o = _f((oof or {}).get("median_daily_pnl"))
    med_a0 = _f((a0 or {}).get("median_daily_pnl"))
    pos_o = _f((oof or {}).get("positive_day_rate"))
    pos_a0 = _f((a0 or {}).get("positive_day_rate"))
    ex_d_o = _pnl(((oof or {}).get("exclude") or {}).get("ex_top3_days") or {})
    ex_d_a0 = _pnl(((a0 or {}).get("exclude") or {}).get("ex_top3_days") or {})
    beats_a2 = bool(oof_pnl is not None and a2_pnl is not None and float(oof_pnl) > float(a2_pnl))
    beats_a0 = bool(oof_pnl is not None and a0_pnl is not None and float(oof_pnl) >= float(a0_pnl))
    pf_ok = bool(oof_pf is not None and a0_pf is not None and float(oof_pf) >= float(a0_pf))
    dd_ok = bool(oof_dd is not None and a0_dd is not None and float(oof_dd) >= float(a0_dd))
    day_ok = bool(
        (med_o is not None and med_a0 is not None and float(med_o) >= float(med_a0))
        or (pos_o is not None and pos_a0 is not None and float(pos_o) > float(pos_a0))
    )
    tail_ok = bool(ex_d_o is not None and ex_d_a0 is not None and float(ex_d_o) >= float(ex_d_a0))
    candidate = bool(gate_pass and beats_a2 and beats_a0 and pf_ok and dd_ok and day_ok and tail_ok)
    return {
        "OOF_PNL_GT_A2": beats_a2,
        "OOF_PNL_GE_A0": beats_a0,
        "OOF_PF_GE_A0": pf_ok,
        "OOF_MAXDD_GE_A0": dd_ok,
        "OOF_DAILY_VS_A0": day_ok,
        "OOF_EX_TOP3_DAYS_GE_A0": tail_ok,
        "TRADE_COUNT_VS_A2_NOT_REQUIRED": True,
        "OOF_GATE": gate_pass,
        "HISTORICAL_ENTRY_CANDIDATE": candidate,
        "note": (
            "RANK1_ONLY is precommitted to one admit per clock. "
            "70% A2 trade-count retention is not applied. Runtime implementation forbidden."
        ),
    }


def fill_edge_vs_current(model_f6: Any, current_f6: Any) -> Optional[bool]:
    a = _f(model_f6)
    b = _f(current_f6)
    if a is None or b is None:
        return None
    return bool(float(a) > float(b))


def decide(
    *,
    integrity_ok: bool,
    gate_pass: bool,
    exact_ran: bool,
    success: dict[str, Any] | None,
    fill_remains: Optional[bool],
) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "CASE": None,
            "VERDICT": "TOP1_DEVELOPMENT_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "note": "Integrity failed (C14/FEATURE_ORDER/TARGET/A0-A2/OOF worker). Top1 not adopted.",
        }
    if not gate_pass:
        return {
            "CASE": "A",
            "VERDICT": "TOP1_OBJECTIVE_NOT_REPRODUCED",
            "NEXT_RESEARCH": "ENTRY_MODEL_ARCHITECTURE_REDESIGN",
            "note": "Top1 OOF gate A-J failed. Top1 line closed. Exact not run. Execution-aware not started.",
        }
    if not exact_ran:
        return {
            "CASE": None,
            "VERDICT": "TOP1_DEVELOPMENT_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "note": "OOF gate passed but RANK1_ONLY Exact did not complete.",
        }
    s = success or {}
    if s.get("HISTORICAL_ENTRY_CANDIDATE"):
        return {
            "CASE": "D",
            "VERDICT": "TOP1_HISTORICAL_ENTRY_CANDIDATE",
            "NEXT_RESEARCH": "NONE",
            "note": "HISTORICAL_DEVELOPMENT_CANDIDATE_ONLY. TRUE_OOS=false. Runtime implementation forbidden this run.",
        }
    if fill_remains is True:
        return {
            "CASE": "C",
            "VERDICT": "TOP1_SIGNAL_PORTFOLIO_EXIT_MISMATCH",
            "NEXT_RESEARCH": "SEPARATE_CAUSAL_AUDIT",
            "note": "OOF Rank1-only Exact failed the historical bar; Rank1 fill-to-600 remains above CURRENT. No C4 auto-start.",
        }
    return {
        "CASE": "B",
        "VERDICT": "TOP1_SIGNAL_EXECUTION_MISMATCH",
        "NEXT_RESEARCH": "EXECUTION_AWARE_RESEARCH_ELIGIBLE",
        "note": (
            "OOF Rank1-only Exact failed the historical bar and Rank1 fill-to-600 did not remain above CURRENT. "
            "Execution-aware research may be considered later. Not started this run."
        ),
    }
