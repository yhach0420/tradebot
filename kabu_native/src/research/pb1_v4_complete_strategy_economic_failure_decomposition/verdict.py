"""Mechanism verdict from numbers. Does not create V2."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_complete_strategy_economic_failure_decomposition import EXPAND_BPS, SMALL_EDGE_BPS
from research.pb1_v4_complete_strategy_economic_failure_decomposition import CASE_READY, NEXT_REPAIR, NEXT_SEALED, NEXT_STOP
from research.pb1_v4_complete_strategy_economic_failure_decomposition.slices import by_key, group_stats


def _frac(n: int, d: int) -> float:
    return float(n) / float(d) if d else 0.0


def mechanism_verdict(
    *,
    rows: list[dict[str, Any]],
    waterfall: dict[str, Any],
    buckets: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    tax: dict[str, Any],
) -> dict[str, Any]:
    n = len(rows)
    cls = by_key(rows, "entry_class")
    n_a = int((cls.get("A_IMMEDIATE_WRONG_DIRECTION") or {}).get("n") or 0)
    n_b = int((cls.get("B_SMALL_EDGE_NEVER_EXPANDED") or {}).get("n") or 0)
    n_c = int((cls.get("C_FAVORABLE_THEN_FULL_GIVEBACK") or {}).get("n") or 0)
    n_d = int((cls.get("D_FAVORABLE_AND_EXIT_CAPTURED") or {}).get("n") or 0)
    g = group_stats(rows)
    med_mfe = (g.get("MFE_bps") or {}).get("median")
    med_real = (g.get("realized_bps") or {}).get("median")
    mean_bps = (g.get("net_bps") or {}).get("mean")
    mean_yen = float(g.get("net_pnl_yen") or 0.0) / n if n else 0.0
    high_px = [b for b in buckets if b.get("bucket") in {"10000_20000", "ge_20000"}]
    high_n = sum(int(b.get("n") or 0) for b in high_px)
    high_net = sum(float(b.get("net_pnl_yen") or 0.0) for b in high_px)
    total_net = float(g.get("net_pnl_yen") or 0.0)
    asf_rec = [r for r in rows if str(r.get("THESIS_LOST_REASON") or "") in {"ACCEPTED_STRUCTURAL_FAILURE", "REPEATED_OR_RECROSS"}]
    late = [
        r
        for r in asf_rec
        if float(r.get("MFE_bps") or 0.0) >= float(EXPAND_BPS)
        and r.get("minutes_MFE_to_THESIS_LOST") is not None
        and int(r["minutes_MFE_to_THESIS_LOST"]) >= 10
    ]
    blocked_net = float(sum(float(r.get("net_pnl_yen") or 0.0) for r in blocked))
    flags = {
        "A_ENTRY_DIRECTIONAL_EDGE_ABSENT": bool(
            n
            and (med_real is not None and float(med_real) <= 0)
            and (med_mfe is not None and float(med_mfe) < float(EXPAND_BPS))
            and _frac(n_a + n_b, n) >= 0.50
        ),
        "B_ENTRY_EDGE_EXISTS_BUT_EXIT_DESTROYS_IT": bool(
            n and (med_mfe is not None and float(med_mfe) >= float(EXPAND_BPS)) and (med_real is not None and float(med_real) <= 0)
        ),
        "C_EXIT_DEATH_FAMILIES_TOO_LATE": bool(asf_rec) and _frac(len(late), max(len(asf_rec), 1)) >= 0.40,
        "D_FIXED_100_SHARE_EXPOSURE_DOMINATES_YEN_RESULT": bool(n)
        and _frac(high_n, n) < 0.35
        and total_net < 0
        and (high_net / total_net if total_net else 0) >= 0.50,
        "E_EXECUTION_TAX_MATERIAL_BUT_SECONDARY": bool(total_net < 0 and float(g.get("gross_pnl_yen") or 0) < 0)
        and int(tax.get("gross_positive_but_net_negative_n") or 0) > 0,
        "F_PORTFOLIO_CONSTRAINTS_MATERIAL": abs(blocked_net) >= 0.15 * abs(total_net) if total_net else False,
    }
    true_flags = [k for k, v in flags.items() if v]
    if len(true_flags) >= 2:
        conclusion = "G_MULTIPLE_FAILURE_MECHANISMS"
    elif true_flags:
        conclusion = true_flags[0]
    else:
        conclusion = "G_MULTIPLE_FAILURE_MECHANISMS"
    repair = conclusion in {"B_ENTRY_EDGE_EXISTS_BUT_EXIT_DESTROYS_IT", "C_EXIT_DEATH_FAMILIES_TOO_LATE"} and not flags["A_ENTRY_DIRECTIONAL_EDGE_ABSENT"]
    return {
        "VERDICT": CASE_READY,
        "CONCLUSION": conclusion,
        "flags": flags,
        "true_flags": true_flags,
        "evidence": {
            "trade_n": n,
            "class_n": {k: (cls.get(k) or {}).get("n") for k in (
                "A_IMMEDIATE_WRONG_DIRECTION",
                "B_SMALL_EDGE_NEVER_EXPANDED",
                "C_FAVORABLE_THEN_FULL_GIVEBACK",
                "D_FAVORABLE_AND_EXIT_CAPTURED",
                "E_LATE_REVERSAL_AFTER_VALID_MOVE",
            )},
            "median_MFE_bps": med_mfe,
            "median_realized_gross_bps": med_real,
            "mean_net_bps": mean_bps,
            "mean_net_yen": mean_yen,
            "high_px_n": high_n,
            "high_px_net_pnl_yen": high_net,
            "asf_recross_n": len(asf_rec),
            "asf_recross_late_n": len(late),
            "blocked_counterfactual_net_pnl_yen": blocked_net,
            "SMALL_EDGE_BPS_a_priori": float(SMALL_EDGE_BPS),
            "EXPAND_BPS_a_priori": float(EXPAND_BPS),
        },
        "causal_repair_clear": repair,
        "NEXT": NEXT_REPAIR if repair else NEXT_STOP,
        "FUTURE_VALIDATION": NEXT_SEALED,
        "do_not_build_v2_this_task": True,
        "waterfall": waterfall,
        "n_captured": n_d,
        "n_giveback": n_c,
    }
