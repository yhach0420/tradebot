"""
Phase 10: combined opening gate (A/C) + BF confirm=2 (B) verification.
"""

from __future__ import annotations

from typing import Any, Optional

from replay.entry_quality import EnrichedTrade, analyze_scenario
from replay.sweep_runner import (
    BASELINE_BF_CONFIRM,
    BASELINE_FAIL_BUFFER_PCT,
    BASELINE_FAIL_WINDOW_MIN,
    BASELINE_HARD_STOP_PCT,
    SweepParams,
    apply_trade_floor,
    summarize_sweep,
)

# Phase 8/9 candidate B exit tuning
CANDIDATE_B_FAIL_BUFFER_PCT = 0.12
CANDIDATE_B_BF_CONFIRM = 2

PHASE10_MIN_TRADES_ABSOLUTE = 40
PHASE10_MIN_TRADES_RATIO = 0.50


def phase13_scenarios() -> list[SweepParams]:
    b_exit = dict(
        fail_window_min=BASELINE_FAIL_WINDOW_MIN,
        fail_buffer_pct=CANDIDATE_B_FAIL_BUFFER_PCT,
        bf_confirm_count=CANDIDATE_B_BF_CONFIRM,
        hard_stop_pct=BASELINE_HARD_STOP_PCT,
    )
    return [
        SweepParams(
            sweep_id="baseline",
            sweep_group="phase13",
            fail_buffer_pct=BASELINE_FAIL_BUFFER_PCT,
            bf_confirm_count=BASELINE_BF_CONFIRM,
            market_session_control=False,
            fail_window_min=BASELINE_FAIL_WINDOW_MIN,
            hard_stop_pct=BASELINE_HARD_STOP_PCT,
        ),
        SweepParams(
            sweep_id="B_bf_confirm_2",
            sweep_group="phase13",
            market_session_control=False,
            **b_exit,
        ),
        SweepParams(
            sweep_id="market_session_plus_B",
            sweep_group="phase13",
            market_session_control=True,
            **b_exit,
        ),
    ]


def phase10_scenarios() -> list[SweepParams]:
    b_exit = dict(
        fail_window_min=BASELINE_FAIL_WINDOW_MIN,
        fail_buffer_pct=CANDIDATE_B_FAIL_BUFFER_PCT,
        bf_confirm_count=CANDIDATE_B_BF_CONFIRM,
        hard_stop_pct=BASELINE_HARD_STOP_PCT,
    )
    return [
        SweepParams(
            sweep_id="baseline",
            sweep_group="phase10",
            fail_buffer_pct=BASELINE_FAIL_BUFFER_PCT,
            bf_confirm_count=BASELINE_BF_CONFIRM,
            market_session_control=False,
            fail_window_min=BASELINE_FAIL_WINDOW_MIN,
            hard_stop_pct=BASELINE_HARD_STOP_PCT,
        ),
        SweepParams(
            sweep_id="B",
            sweep_group="phase10",
            market_session_control=False,
            **b_exit,
        ),
        SweepParams(
            sweep_id="A_plus_B",
            sweep_group="phase10",
            market_session_control=True,
            **b_exit,
        ),
        SweepParams(
            sweep_id="C_plus_B",
            sweep_group="phase10",
            market_session_control=True,
            **b_exit,
        ),
    ]


def summarize_phase10(trades: list[EnrichedTrade], params: SweepParams) -> dict[str, Any]:
    core = summarize_sweep(trades, params)
    quality = analyze_scenario(trades, params.sweep_id)
    core["mfe_reach_0.3pct"] = quality.get("mfe_reach_0.3pct")
    core["mfe_reach_0.5pct"] = quality.get("mfe_reach_0.5pct")
    core["breakout_continuation_rate"] = quality.get("breakout_continuation_rate")
    core["median_hold_min"] = quality.get("median_hold_min")
    core["avg_hold_min"] = quality.get("avg_hold_min")
    core["avg_mfe_pct"] = quality.get("avg_mfe_pct")
    return core


def pick_shadow_candidate(
    rows: list[dict[str, Any]],
    *,
    baseline_trades: int,
) -> dict[str, Any]:
    """Select one common rule set for paper_trade shadow."""
    floor = max(PHASE10_MIN_TRADES_ABSOLUTE, int(baseline_trades * PHASE10_MIN_TRADES_RATIO))
    eligible = [
        r
        for r in rows
        if not r.get("excluded_low_trades") and int(r.get("trades") or 0) >= floor
    ]
    if not eligible:
        return {"shadow_candidate_id": None, "reason": "no_eligible_scenario", "trade_floor": floor}

    combined = [r for r in eligible if r.get("sweep_id") in ("A_plus_B", "C_plus_B")]
    b_only = next((r for r in eligible if r.get("sweep_id") == "B"), None)

    def score(r: dict[str, Any]) -> tuple[float, float, float, float]:
        pf = r.get("profit_factor")
        pf_v = float(pf) if pf is not None else 0.0
        mfe03 = float(r.get("mfe_reach_0.3pct") or 0.0)
        return (
            float(r.get("total_pnl_pct") or 0.0),
            pf_v,
            float(r.get("avg_pnl_pct") or 0.0),
            mfe03,
        )

    ranked_all = sorted(eligible, key=score, reverse=True)
    ranked_combined = sorted(combined, key=score, reverse=True) if combined else []

    shadow_id: Optional[str] = None
    reason_parts: list[str] = []

    if ranked_combined:
        best_combined = ranked_combined[0]
        alt = ranked_combined[1] if len(ranked_combined) > 1 else None
        a_trades = int(best_combined.get("trades") or 0)
        if (
            best_combined.get("sweep_id") == "A_plus_B"
            and alt
            and alt.get("sweep_id") == "C_plus_B"
            and a_trades < int(alt.get("trades") or 0) * 0.88
        ):
            shadow_id = "C_plus_B"
            reason_parts.append("09:30 gate too few trades; prefer 09:15")
        else:
            shadow_id = str(best_combined.get("sweep_id"))
            reason_parts.append("best combined gate+BF among eligible")

    if shadow_id is None and b_only:
        shadow_id = "B"
        reason_parts.append("no combined eligible; fallback to B")

    if shadow_id is None and ranked_all:
        shadow_id = str(ranked_all[0].get("sweep_id"))
        reason_parts.append("fallback highest score")

    baseline = next((r for r in rows if r.get("sweep_id") == "baseline"), None)
    shadow_row = next((r for r in rows if r.get("sweep_id") == shadow_id), None)
    improves_vs_baseline = False
    improves_vs_b = False
    if shadow_row and baseline:
        improves_vs_baseline = float(shadow_row.get("total_pnl_pct") or 0) > float(
            baseline.get("total_pnl_pct") or 0
        )
    if shadow_row and b_only and shadow_id != "B":
        improves_vs_b = float(shadow_row.get("total_pnl_pct") or 0) > float(
            b_only.get("total_pnl_pct") or 0
        )

    return {
        "shadow_candidate_id": shadow_id,
        "trade_floor": floor,
        "reason": "; ".join(reason_parts) if reason_parts else "",
        "improves_total_pnl_vs_baseline": improves_vs_baseline,
        "improves_total_pnl_vs_B": improves_vs_b,
        "ranked_eligible": [r.get("sweep_id") for r in ranked_all],
        "ranked_combined": [r.get("sweep_id") for r in ranked_combined],
    }


def build_phase10_report(
    rows: list[dict[str, Any]],
    *,
    meta: dict[str, Any],
    shadow: dict[str, Any],
) -> dict[str, Any]:
    baseline = next((r for r in rows if r.get("sweep_id") == "baseline"), {})
    b_row = next((r for r in rows if r.get("sweep_id") == "B"), {})
    ab = next((r for r in rows if r.get("sweep_id") == "A_plus_B"), {})
    cb = next((r for r in rows if r.get("sweep_id") == "C_plus_B"), {})

    def beat(base: dict, cand: dict) -> bool:
        if cand.get("excluded_low_trades"):
            return False
        return float(cand.get("total_pnl_pct") or 0) > float(base.get("total_pnl_pct") or 0)

    return {
        "meta": meta,
        "rows": rows,
        "shadow_selection": shadow,
        "comparison": {
            "A_plus_B_beats_baseline": beat(baseline, ab),
            "A_plus_B_beats_B": beat(b_row, ab),
            "C_plus_B_beats_baseline": beat(baseline, cb),
            "C_plus_B_beats_B": beat(b_row, cb),
            "use_opening_gate": bool(
                shadow.get("shadow_candidate_id") in ("A_plus_B", "C_plus_B")
            ),
        },
    }
