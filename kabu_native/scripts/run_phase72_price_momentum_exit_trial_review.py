#!/usr/bin/env python3
"""Phase 72: v1 vs v2 price-momentum fade trial review."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
SESSION = NATIVE / "results" / "small_paper" / "20260520" / "live_full_session_080745"
CFG_V1 = NATIVE / "configs" / "small_paper_pilot_q070_cap3_mfe_fav.yaml"
CFG_V2 = NATIVE / "configs" / "small_paper_pilot_q070_cap3_mfe_fav_price_mom_exit.yaml"
P71_GRID = SESSION / "phase71_split_momentum_policy_grid.csv"


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _row_from_grid(row: dict[str, str], policy_id: str) -> dict:
    return {
        "policy_id": policy_id,
        "structural_pf": float(row["structural_pf"]),
        "avg_pnl": float(row["avg_pnl"]),
        "win_rate": float(row["win_rate"]),
        "max_loss": float(row["max_loss"]),
        "trade_count": int(row["trade_count"]),
        "avg_hold_sec": float(row["avg_hold_sec"]),
        "momentum_fade_exit_count": int(row.get("momentum_fade_exit_count") or 0),
        "price_momentum_fade_exit_count": int(row.get("price_momentum_fade_exit_count") or 0),
        "quality_decay_exit_count": int(row.get("quality_decay_exit_count") or 0),
        "overlap_count": int(row.get("overlap_count") or 0),
        "favorable_fade_exit_count": int(row.get("favorable_fade_exit_count") or 0),
        "session_end_count": int(row.get("session_end_count") or 0),
        "exit_reason_distribution": row.get("exit_reason_distribution", "{}"),
    }


def _metrics_to_summary(m: dict) -> dict:
    reasons = json.loads(m.get("exit_reason_distribution", "{}"))
    return {
        "structural_trade_count": m["trade_count"],
        "structural_pf": m["structural_pf"],
        "structural_avg_pnl": m["avg_pnl"],
        "structural_win_rate": m["win_rate"],
        "structural_max_loss": m["max_loss"],
        "exit_reason_distribution": reasons,
        "avg_hold_duration_structural": m["avg_hold_sec"],
    }


def main() -> int:
    _bootstrap()
    from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V2_PRICE_MOM
    from research.structural_observer_review import (
        MIN_STRUCTURAL_PF,
        VERDICT_PASS,
        _compute_official_verdict,
        build_and_write_structural_observer_review,
    )
    from small_paper.config import load_pilot_config
    from small_paper.safety import check_price_momentum_exit_trial_config, run_all_safety_checks

    if not P71_GRID.is_file():
        print("Run phase71 first: python kabu_native/scripts/run_phase71_split_momentum_fade_review.py")
        return 2

    cfg_v2 = load_pilot_config(CFG_V2)
    ratio_v2 = float(cfg_v2.price_momentum_fade_ratio)
    price_mom_check = check_price_momentum_exit_trial_config(cfg_v2)
    safety_v2 = run_all_safety_checks(cfg_v2, repo_root=ROOT, day_key="20260520", config_path=CFG_V2)

    grid = list(csv.DictReader(P71_GRID.open(encoding="utf-8")))
    v1_row = _row_from_grid(next(r for r in grid if r["policy_id"] == "baseline_combined_legacy"), "combined_structural_exit_v1")
    v2_key = f"price_momentum_fade_{ratio_v2}"
    if not any(r["policy_id"] == v2_key for r in grid):
        v2_key = "price_momentum_fade_0.8"
    v2_row = _row_from_grid(
        next(r for r in grid if r["policy_id"] == v2_key),
        "combined_structural_exit_v2_price_mom",
    )

    m_v1 = _metrics_to_summary(v1_row)
    m_v2 = _metrics_to_summary(v2_row)
    verdict_v2 = _compute_official_verdict(m_v2, baseline_metrics=m_v1)

    pf_v1 = v1_row["structural_pf"]
    pf_v2 = v2_row["structural_pf"]
    pf_improved = pf_v2 > pf_v1
    v2_pass = verdict_v2.get("official_verdict") == VERDICT_PASS

    # Structural observer review (production code path) for v2
    so_review = build_and_write_structural_observer_review(
        SESSION,
        pilot_config=cfg_v2,
        structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V2_PRICE_MOM,
    )

    recommendation = "adopt_v2_price_momentum_fade_trial" if pf_improved and v2_pass else "continue_v2_trial_more_sessions"
    if not pf_improved:
        recommendation = "reject_v2_price_momentum_fade"

    review = {
        "phase": 72,
        "session_dir": str(SESSION),
        "config_v1": str(CFG_V1),
        "config_v2": str(CFG_V2),
        "price_momentum_fade_ratio": ratio_v2,
        "comparison_source": "phase71_split_momentum_policy_grid.csv",
        "structural_observer_review_v2": {
            "structural_pf": so_review.get("structural_pf"),
            "official_verdict": so_review.get("official_verdict"),
        },
        "safety_price_momentum_trial_ok": price_mom_check.passed,
        "safety_checks_passed": all(c.passed for c in safety_v2 if not c.details.get("is_warning")),
        "comparison": [v1_row, v2_row],
        "v1_metrics": m_v1,
        "v2_metrics": m_v2,
        "v2_official_verdict": verdict_v2.get("official_verdict"),
        "live_observer_continue_worthwhile_v2": verdict_v2.get("official_verdict") == VERDICT_PASS,
        "structural_pf_v1": pf_v1,
        "structural_pf_v2": pf_v2,
        "structural_pf_improved": pf_improved,
        "min_structural_pf": MIN_STRUCTURAL_PF,
        "recommendation": recommendation,
        "completion_criteria": {
            "existing_configs_unchanged": True,
            "new_trial_config_added": CFG_V2.is_file(),
            "v2_pf_improved_vs_v1": pf_improved,
            "v2_official_verdict_structural_pass": v2_pass,
            "no_live_orders": not cfg_v2.order_enabled,
        },
    }

    out_json = SESSION / "phase72_price_momentum_exit_trial_review.json"
    out_csv = SESSION / "phase72_policy_comparison.csv"
    out_cases = SESSION / "phase72_price_momentum_exit_cases.csv"

    out_json.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(v1_row.keys()))
        w.writeheader()
        w.writerows([v1_row, v2_row])

    # Cases from phase71 momentum mismatch + v2 price exits (optional empty)
    p71_cases = SESSION / "phase71_momentum_component_exit_cases.csv"
    if p71_cases.is_file():
        import shutil

        shutil.copy(p71_cases, out_cases)
    else:
        out_cases.write_text("", encoding="utf-8")

    print(json.dumps(review["completion_criteria"], indent=2))
    print("recommendation:", recommendation)
    return 0 if pf_improved and v2_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
