"""
Phase 123: What-if replay for conditional fade extension vs current / unconditional +60s.
"""

from __future__ import annotations

import statistics
from typing import Any, Callable, Mapping, Optional, Sequence

from research.fade_extension_conditions import IMPROVE_EPS, build_fade_cluster_rows

MFE_THRESHOLDS = (0.10, 0.15, 0.20, 0.25)
PRIMARY_SCENARIO = "C_mfe015_overlap_false"


def _matches_rule(
    row: Mapping[str, Any],
    *,
    mfe_threshold: float,
    require_overlap_false: bool,
) -> bool:
    mfe = row.get("mfe_pct")
    if mfe is None:
        return False
    if float(mfe) <= mfe_threshold:
        return False
    if require_overlap_false and bool(row.get("overlap_replaced")):
        return False
    return True


def _hybrid_pnl(row: Mapping[str, Any], *, selected: bool) -> float:
    baseline = float(row.get("pnl_at_exit") or 0)
    hold60 = float(row.get("hold60_pnl") or baseline)
    return hold60 if selected else baseline


def _summarize_policy(
    rows: Sequence[Mapping[str, Any]],
    *,
    scenario_id: str,
    scenario_label: str,
    selector: Optional[Callable[[Mapping[str, Any]], bool]] = None,
) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"scenario_id": scenario_id, "scenario_label": scenario_label, "trade_count": 0}

    pnls: list[float] = []
    worsened = 0
    loss_exp = 0
    selected_rows: list[Mapping[str, Any]] = []
    selected_improved = 0
    selected_delta_sum = 0.0
    non_selected_delta_sum = 0.0

    for r in rows:
        baseline = float(r.get("pnl_at_exit") or 0)
        hold60 = float(r.get("hold60_pnl") or baseline)
        selected = selector(r) if selector else True
        pnl = hold60 if selected else baseline
        delta = pnl - baseline
        pnls.append(pnl)
        if pnl < baseline - 1e-9:
            worsened += 1
        if pnl < baseline and pnl < 0:
            loss_exp += 1
        if selected:
            selected_rows.append(r)
            d = hold60 - baseline
            selected_delta_sum += d
            if d > IMPROVE_EPS:
                selected_improved += 1
        else:
            non_selected_delta_sum += delta

    wins = sum(1 for p in pnls if p > 0)
    sel_n = len(selected_rows)

    return {
        "scenario_id": scenario_id,
        "scenario_label": scenario_label,
        "trade_count": n,
        "total_pnl": round(sum(pnls), 4),
        "avg_pnl": round(statistics.mean(pnls), 4),
        "win_rate": round(wins / n, 4),
        "worsened_rate": round(worsened / n, 4),
        "worsened_count": worsened,
        "loss_expansion_rate": round(loss_exp / n, 4),
        "loss_expansion_count": loss_exp,
        "selected_trade_count": sel_n,
        "selected_rate": round(sel_n / n, 4) if n else None,
        "selected_precision": round(selected_improved / sel_n, 4) if sel_n else None,
        "selected_total_delta": round(selected_delta_sum, 4),
        "non_selected_delta": round(non_selected_delta_sum, 4),
        "delta_vs_current_total": round(sum(pnls) - sum(float(r.get("pnl_at_exit") or 0) for r in rows), 4),
    }


def build_scenarios(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    n = len(rows)
    a_pnls = [float(r.get("pnl_at_exit") or 0) for r in rows]
    scenarios.append(
        {
            "scenario_id": "A_current",
            "scenario_label": "current_exit",
            "trade_count": n,
            "total_pnl": round(sum(a_pnls), 4),
            "avg_pnl": round(statistics.mean(a_pnls), 4) if a_pnls else None,
            "win_rate": round(sum(1 for p in a_pnls if p > 0) / n, 4) if n else None,
            "worsened_rate": 0.0,
            "worsened_count": 0,
            "loss_expansion_rate": 0.0,
            "loss_expansion_count": 0,
            "selected_trade_count": n,
            "selected_rate": 1.0 if n else None,
            "selected_precision": None,
            "selected_total_delta": 0.0,
            "non_selected_delta": 0.0,
            "delta_vs_current_total": 0.0,
        }
    )

    scenarios.append(
        _summarize_policy(
            rows,
            scenario_id="B_all_fade_60s",
            scenario_label="unconditional_fade_plus_60s",
            selector=lambda _r: True,
        )
    )

    for mfe in MFE_THRESHOLDS:
        for overlap in (False, True):
            tag = f"{mfe:.2f}".replace(".", "")
            sid = f"C_mfe{tag}_overlap_{'false' if overlap else 'any'}"
            label = f"conditional_mfe_gt_{mfe}_overlap_{'false' if overlap else 'any'}"
            scenarios.append(
                _summarize_policy(
                    rows,
                    scenario_id=sid,
                    scenario_label=label,
                    selector=lambda r, m=mfe, o=overlap: _matches_rule(
                        r, mfe_threshold=m, require_overlap_false=o
                    ),
                )
            )

    return scenarios


def build_selected_details(
    rows: Sequence[Mapping[str, Any]],
    *,
    mfe_threshold: float = 0.15,
    require_overlap_false: bool = True,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        selected = _matches_rule(
            r, mfe_threshold=mfe_threshold, require_overlap_false=require_overlap_false
        )
        baseline = float(r.get("pnl_at_exit") or 0)
        hold60 = float(r.get("hold60_pnl") or baseline)
        delta = round(hold60 - baseline, 4)
        out.append(
            {
                **{k: v for k, v in r.items()},
                "selected_for_extension": selected,
                "baseline_pnl": baseline,
                "hold60_pnl": hold60,
                "hold60_delta": delta,
                "hybrid_pnl": hold60 if selected else baseline,
                "hybrid_delta_vs_baseline": delta if selected else 0.0,
                "improved_if_extended": delta > IMPROVE_EPS,
                "worsened_if_extended": delta < -IMPROVE_EPS,
                "loss_expanded_if_extended": hold60 < baseline and hold60 < 0,
                "rule_mfe_threshold": mfe_threshold,
                "rule_overlap_false": require_overlap_false,
            }
        )
    return out


def determine_verdict(
    scenarios: Sequence[Mapping[str, Any]],
    *,
    primary_id: str = PRIMARY_SCENARIO,
) -> tuple[str, list[str], Optional[dict[str, Any]]]:
    notes: list[str] = []
    by_id = {s["scenario_id"]: s for s in scenarios}
    a = by_id.get("A_current") or {}
    b = by_id.get("B_all_fade_60s") or {}
    c = by_id.get(primary_id) or {}

    a_total = float(a.get("total_pnl") or 0)
    b_total = float(b.get("total_pnl") or 0)
    c_total = float(c.get("total_pnl") or 0)
    b_worsened = float(b.get("worsened_rate") or 0)
    c_worsened = float(c.get("worsened_rate") or 0)

    conditional = [s for s in scenarios if str(s.get("scenario_id", "")).startswith("C_")]
    best_cond = max(conditional, key=lambda s: float(s.get("total_pnl") or -1e9)) if conditional else None

    notes.append(
        f"A_total={a_total:.4f} B_total={b_total:.4f} primary_C_total={c_total:.4f} "
        f"B_worsened={b_worsened:.1%} primary_C_worsened={c_worsened:.1%} "
        f"primary_selected={c.get('selected_trade_count')}"
    )
    if best_cond:
        notes.append(
            f"best_conditional={best_cond.get('scenario_id')} total={best_cond.get('total_pnl')} "
            f"worsened={best_cond.get('worsened_rate')}"
        )

    if c_total <= a_total + 0.5:
        if b_total > a_total + 3.0:
            return "unconditional_extension_better_but_risky", notes, best_cond
        return "current_exit_best", notes, best_cond

    if c_total > a_total + 0.5 and c_worsened < b_worsened - 0.01:
        return "conditional_extension_promising", notes + [
            f"primary={primary_id} precision={c.get('selected_precision')}"
        ], best_cond

    if b_total > c_total + 1.0 and b_total > a_total + 3.0:
        return "unconditional_extension_better_but_risky", notes + [
            "unconditional higher total_pnl with higher risk"
        ], best_cond

    if c_total > a_total:
        return "conditional_rule_too_weak", notes, best_cond

    return "current_exit_best", notes, best_cond


def analyze_conditional_fade_extension(session_dirs: Sequence[Any]) -> dict[str, Any]:
    from pathlib import Path

    rows = build_fade_cluster_rows([Path(p) for p in session_dirs])
    scenarios = build_scenarios(rows)
    verdict, notes, best_cond = determine_verdict(scenarios)
    selected_details = build_selected_details(rows, mfe_threshold=0.15, require_overlap_false=True)

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "fade_trade_count": len(rows),
        "primary_rule": {
            "exit_reasons": ["momentum_fade_exit", "price_momentum_fade_exit"],
            "mfe_pct_gt": 0.15,
            "overlap_replaced": False,
        },
        "best_conditional_scenario": best_cond,
        "scenarios": scenarios,
        "selected_trade_details": selected_details,
    }
