#!/usr/bin/env python3
"""Phase 129: Trading logic improvement roadmap (review / plan only)."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def build_plan() -> dict[str, Any]:
    return {
        "phase": 129,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "title": "Trading Logic Improvement Roadmap",
        "constraints": {
            "production_pilot_yaml_unchanged": True,
            "auto_order_forbidden": True,
            "start_from_shadow_review_whatif": True,
            "symbol_fixed_exclusion_forbidden": True,
            "time_band_fixed_exclusion_forbidden": True,
            "universe": "Core10 + Dynamic40 / AM-PM maintained",
            "baseline_exit_policy": "combined_structural_exit_v1 unchanged",
            "no_implementation_in_phase129": True,
        },
        "prioritized_roadmap": [
            {
                "priority": 1,
                "issue_id": "fade_range_hold_exit",
                "title": "Fade / range-hold exit",
                "next_phase": "Phase130_range_hold_exit_review",
                "next_phase_script": "kabu_native/scripts/run_phase130_range_hold_exit_review.py",
            },
            {
                "priority": 2,
                "issue_id": "overlap_replaced",
                "title": "overlap_replaced validity",
                "next_phase": "Phase131_overlap_replacement_review",
                "next_phase_script": "kabu_native/scripts/run_phase131_overlap_replacement_review.py",
            },
            {
                "priority": 3,
                "issue_id": "cap_sensitivity",
                "title": "max_concurrent cap=3 optimality",
                "next_phase": "Phase132_cap_sensitivity_review",
                "next_phase_script": "kabu_native/scripts/run_phase132_cap_sensitivity_review.py",
            },
            {
                "priority": 4,
                "issue_id": "pm_dynamic40_selection",
                "title": "PM Dynamic40 selection quality",
                "next_phase": "Phase133_pm_dynamic_selection_review",
                "next_phase_script": "kabu_native/scripts/run_phase133_pm_dynamic_selection_review.py",
            },
            {
                "priority": 5,
                "issue_id": "stop_limit_handling",
                "title": "Stop high / stop low / near-limit handling",
                "next_phase": "Phase134_limit_status_policy_review",
                "next_phase_script": "kabu_native/scripts/run_phase134_limit_status_policy_review.py",
            },
            {
                "priority": 6,
                "issue_id": "session_close",
                "title": "Session close forced exit (11:25 / 15:23)",
                "next_phase": "Phase135_session_close_whatif_review",
                "next_phase_script": "kabu_native/scripts/run_phase135_session_close_whatif_review.py",
            },
        ],
        "issues": _issues(),
        "phase130_start_spec": _phase130_spec(),
        "prior_work_summary": {
            "fade_phases": {
                "phase121": "fixed +60s hold what-if → fade_exit_needs_revision",
                "phase126": "state fade_watch on fade subset → +0.55 delta, 22.4% worsened",
                "phase127": "shadow policy replay all trades → -1.15 delta (too broad trigger)",
                "phase128": "trigger restriction → mfe>0.15 best (+3.07 delta) but precision 37.5%",
            },
            "key_insight": "fade_watch helps when mfe>0.15 and reacceleration; hurts on low-pnl/low-mfe fades. Next: distinguish range_hold vs breakdown before exit.",
        },
    }


def _issues() -> list[dict[str, Any]]:
    sessions_glob = "kabu_native/results/small_paper/**/structural_trades.csv"
    events_glob = "kabu_native/results/small_paper/**/small_paper_events.csv"

    return [
        {
            "issue_id": "fade_range_hold_exit",
            "priority": 1,
            "title": "Fade exits fire too early; range vs breakdown not distinguished",
            "hypothesis": (
                "momentum_fade_exit and price_momentum_fade_exit treat sideways consolidation "
                "as breakdown. Event-driven range_hold (no fixed-second wait) can retain trades "
                "that would reaccelerate, while immediate exit on true breakdown preserves capital."
            ),
            "required_data": [
                "small_paper_events.csv / jsonl per session (price, momentum, rolling_mfe/mae)",
                "structural_trades.csv with fade exit reasons",
                "Phase127 shadow replay pairs (baseline vs fade_watch)",
                "Phase128 improved_vs_worsened.csv (251 fade_watch trades)",
            ],
            "existing_files": [
                "kabu_native/src/research/state_based_fade_exit_review.py",
                "kabu_native/src/research/fade_watch_shadow.py",
                "kabu_native/src/research/fade_watch_trigger_review.py",
                "kabu_native/scripts/run_phase121_fade_exit_replay.py",
                "kabu_native/scripts/run_phase126_state_based_fade_exit_review.py",
                "kabu_native/scripts/run_phase127_fade_watch_shadow.py",
                "kabu_native/scripts/run_phase128_fade_watch_trigger_review.py",
                "kabu_native/results/reports/phase126_state_based_fade_exit_review.json",
                "kabu_native/results/reports/phase127_fade_watch_shadow_test_report.json",
                "kabu_native/results/reports/phase128_fade_watch_trigger_review.json",
                "kabu_native/results/reports/phase128_improved_vs_worsened.csv",
                "kabu_native/configs/small_paper_pilot_q070_cap3_fade_watch_shadow.yaml",
            ],
            "missing_data": [
                "Explicit range_hold vs breakdown labels at fade time (to be derived in Phase130)",
                "VWAP distance at fade (mostly missing in live events, 0% in Phase126)",
                "Volume surge post-fade (sparse in push_jsonl)",
            ],
            "what_if_method": (
                "Replay fade trades from event stream after fade trigger. Classify each path as "
                "range_hold (post_low intact, small MFE giveback, price above fade support, "
                "momentum down but no price breakdown) vs breakdown (post_low break, large giveback, "
                "fade_price breach, lower-low). Compare: A=current immediate fade exit, "
                "B=hold through range_hold until breakdown or session end, C=breakdown-only immediate exit. "
                "No fixed +30/+60/+120s timers. Restrict fade_watch trigger to mfe>0.15 per Phase128."
            ),
            "success_criteria": [
                "total_pnl improves vs combined_structural_exit_v1 baseline",
                "worsened_rate <= 35% on fade subset (Phase126 achieved 22.4% on restricted sim)",
                "no fixed-second wait in exit logic",
                "range_hold and breakdown separable with >=60% post-hoc precision on hold paths",
                "restricted trigger (mfe>0.15) combined with range_hold beats unrestricted fade_watch",
            ],
            "output_files": [
                "kabu_native/results/reports/phase130_range_hold_exit_review.json",
                "kabu_native/results/reports/phase130_range_hold_trade_paths.csv",
                "kabu_native/results/reports/phase130_range_hold_rule_candidates.csv",
            ],
            "implementation_risk": "medium",
            "implementation_risk_notes": (
                "Shadow-only extension of fade_watch_shadow; production v1 unchanged. "
                "Risk: over-holding breakdown paths if range classifier too permissive."
            ),
            "next_phase": "Phase130_range_hold_exit_review",
        },
        {
            "issue_id": "overlap_replaced",
            "priority": 2,
            "title": "overlap_replaced_review validity unknown",
            "hypothesis": (
                "Closing the old position on same-symbol re-entry may discard trades that would "
                "have continued profitably; replace may be correct when new signal quality is higher."
            ),
            "required_data": [
                "structural_trades.csv with overlap_replaced_review close_reason",
                "small_paper_events.csv accepted/candidate timeline per symbol",
                "post-exit price path for replaced positions (30/60/180s horizons)",
            ],
            "existing_files": [
                "kabu_native/scripts/run_phase74_entry_churn_overlap_review.py",
                "kabu_native/scripts/run_phase76_overlap_position_management_review.py",
                "kabu_native/src/research/mfe_mae_exit_review.py",
                "kabu_native/src/research/structural_observer_review.py",
            ],
            "missing_data": [
                "Unified overlap counterfactual CSV across latest 4+ sessions",
                "keep_old / replace_new / hold_both scenario metrics on same session set as Phase128",
            ],
            "what_if_method": (
                "For each overlap_replaced_review exit, track post-exit PnL if old position had "
                "been held vs new entry only vs both. Scenarios: A=current replace, B=keep_old, "
                "C=replace_new, D=hold_both (cap-aware). Use event price timeline; no symbol blacklist."
            ),
            "success_criteria": [
                "post-exit PnL distribution for replaced symbols quantified",
                "replace_was_correct_rate >= 55% or keep_old wins on total_pnl proxy",
                "clear rule for quality-delta threshold (Phase76 used 0.05) validated on 4 sessions",
            ],
            "output_files": [
                "kabu_native/results/reports/phase131_overlap_replacement_review.json",
                "kabu_native/results/reports/phase131_overlap_post_exit_paths.csv",
                "kabu_native/results/reports/phase131_overlap_scenario_comparison.csv",
            ],
            "implementation_risk": "medium",
            "implementation_risk_notes": (
                "Overlap policy affects cap utilization and churn; must stay review-only until "
                "validated. Cannot hold_both without cap what-if interaction (Phase132 link)."
            ),
            "next_phase": "Phase131_overlap_replacement_review",
        },
        {
            "issue_id": "cap_sensitivity",
            "priority": 3,
            "title": "cap=3 may reject profitable opportunities",
            "hypothesis": (
                "max_concurrent_positions=3 rejects high-quality candidates that would improve "
                "total_pnl without excessive overlap noise; cap=5 or 7 may help if quality gate holds."
            ),
            "required_data": [
                "small_paper_events.csv with gate_reject_reason=max_concurrent",
                "accepted trade lifecycles and virtual-hold PnL proxy",
                "overlap_replaced counts per cap scenario",
            ],
            "existing_files": [
                "kabu_native/src/research/exposure_cap_whatif_review.py",
                "kabu_native/src/research/small_paper_gate_diagnosis.py",
                "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml",
            ],
            "missing_data": [
                "Phase132 cap=7 scenario (Phase53 only tested 3/4/5)",
                "Joint cap + overlap counterfactual on same 4 sessions as Phase128",
            ],
            "what_if_method": (
                "Replay acceptance with ExposureGate at cap=3/5/7, fixed q>=0.70, same windows. "
                "Measure: accepted_count, rejected_max_concurrent_count, total_pnl proxy, PF, "
                "overlap_replaced_count, symbol concentration. Extend Phase53 exposure_cap_whatif_review."
            ),
            "success_criteria": [
                "cap=5 or 7 improves total_pnl proxy vs cap=3 without PF drop below 1.1",
                "rejected_max_concurrent high-quality (q>0.72) would-be PnL quantified",
                "overlap and churn do not increase disproportionately",
            ],
            "output_files": [
                "kabu_native/results/reports/phase132_cap_sensitivity_review.json",
                "kabu_native/results/reports/phase132_cap_scenario_comparison.csv",
                "kabu_native/results/reports/phase132_rejected_opportunity.csv",
            ],
            "implementation_risk": "high",
            "implementation_risk_notes": (
                "Cap increase raises exposure and overlap frequency; requires Phase131 overlap "
                "validation first. Production YAML unchanged; shadow config only."
            ),
            "next_phase": "Phase132_cap_sensitivity_review",
        },
        {
            "issue_id": "pm_dynamic40_selection",
            "priority": 4,
            "title": "PM Dynamic40 selection may miss AM-informed candidates",
            "hypothesis": (
                "Afternoon Dynamic40 universe underweights AM session volume/range/trading_value "
                "signals; PM-only additions and AM-dropped symbols have measurable performance gaps."
            ),
            "required_data": [
                "Core10 + Dynamic40 universe CSVs per day (AM/PM)",
                "small_paper_events candidate/accepted by session_bucket",
                "AM screening vs PM screening window metrics (09:00-09:03, 12:25-12:32)",
            ],
            "existing_files": [
                "kabu_native/scripts/run_phase117_core10_dynamic40_design.py",
                "kabu_native/scripts/run_phase118_core10_dynamic40_pipeline.py",
                "kabu_native/scripts/run_phase114_am_pm_universe_design.py",
                "kabu_native/scripts/run_phase115_am_pm_shadow_pipeline.py",
                "kabu_native/src/universe/core10_dynamic40_shadow.py",
                "kabu_native/src/universe/am_pm_universe.py",
                "kabu_native/src/universe/am_pm_shadow_universe.py",
            ],
            "missing_data": [
                "PM-only symbol accepted/reject rate vs AM baseline",
                "Performance of symbols dropped from AM Core10/Dynamic40 in PM session",
                "Feature importance for PM rescreen (volume, range, trading_value)",
            ],
            "what_if_method": (
                "Compare current PM Dynamic40 vs counterfactual scoring that blends AM session "
                "accumulated volume/range/trading_value. Measure candidate_rate, accepted_rate, "
                "avg_pnl for PM-only symbols. No symbol fixed exclusion; universe size cap 50 maintained."
            ),
            "success_criteria": [
                "PM-only additions show accepted_rate and avg_pnl above session baseline",
                "AM-dropped symbols PM performance documented (keep vs drop decision evidence)",
                "at least one AM feature (volume or trading_value) improves PM selection precision",
            ],
            "output_files": [
                "kabu_native/results/reports/phase133_pm_dynamic_selection_review.json",
                "kabu_native/results/reports/phase133_pm_symbol_outcomes.csv",
                "kabu_native/results/reports/phase133_pm_feature_sensitivity.csv",
            ],
            "implementation_risk": "medium",
            "implementation_risk_notes": (
                "Universe generation change affects all PM entries; shadow pipeline first. "
                "Core10 list unchanged; Dynamic40 scoring only."
            ),
            "next_phase": "Phase133_pm_dynamic_selection_review",
        },
        {
            "issue_id": "stop_limit_handling",
            "priority": 5,
            "title": "Stop-limit / near-limit symbols may be untradeable",
            "hypothesis": (
                "Entries near daily limit up/down have thin boards and failed exits; "
                "warning-only is insufficient for some symbols."
            ),
            "required_data": [
                "daily_limit_up_price, daily_limit_down_price, current_price per candidate",
                "is_limit_up, is_limit_down, near_limit flags",
                "post-entry slippage / tick density / exit success",
            ],
            "existing_files": [
                "kabu_native/src/universe/am_pm_universe.py",
                "kabu_native/scripts/run_phase114_am_pm_universe_design.py",
                "kabu_native/scripts/run_phase105_register_limit_aware_universe.py",
            ],
            "missing_data": [
                "Live session limit flag join to accepted trades",
                "Liquidity proxy (tick count, spread) at entry for limit-near symbols",
                "Counterfactual: warning vs exclude vs quality_downgrade",
            ],
            "what_if_method": (
                "Join limit status at entry time to structural trades. Classify outcomes for "
                "is_limit_*, near_limit_*. Compare policies: A=current (warn only), B=exclude entry, "
                "C=downgrade quality tier. Measure trade_count impact and PnL/MAE on remaining set."
            ),
            "success_criteria": [
                "limit-near trades show higher MAE or lower exit fill rate vs control",
                "exclude or downgrade policy improves net PnL or reduces tail losses",
                "no symbol-fixed blacklist; rule-based only",
            ],
            "output_files": [
                "kabu_native/results/reports/phase134_limit_status_policy_review.json",
                "kabu_native/results/reports/phase134_limit_trade_outcomes.csv",
                "kabu_native/results/reports/phase134_limit_policy_comparison.csv",
            ],
            "implementation_risk": "low",
            "implementation_risk_notes": (
                "Entry gate only; exit policy unchanged. Risk of over-excluding volatile winners."
            ),
            "next_phase": "Phase134_limit_status_policy_review",
        },
        {
            "issue_id": "session_close",
            "priority": 6,
            "title": "Forced session close at 11:25 / 15:23 may cut profits",
            "hypothesis": (
                "AM force_close 11:25 and PM force_close 15:23 exit positions that would remain "
                "profitable until natural session end; day-trade no-overnight constraint must hold."
            ),
            "required_data": [
                "structural_trades.csv with morning_session_close / afternoon_session_close / session_end",
                "AM/PM session policy times from Phase116",
                "post-force-close price path to 11:30 / 15:30",
            ],
            "existing_files": [
                "kabu_native/src/small_paper/am_pm_session_policy.py",
                "kabu_native/scripts/run_phase116_am_pm_session_policy.py",
                "kabu_native/src/research/fade_watch_shadow.py",
                "kabu_native/results/reports/phase127_fade_watch_shadow_test_report.json",
            ],
            "missing_data": [
                "Count of positions force-closed with positive unrealized PnL at close",
                "What-if hold to session_end (same day, no overnight) PnL delta",
            ],
            "what_if_method": (
                "For trades closed by morning_session_close or afternoon_session_close, replay "
                "holding until session_end (11:30 AM bucket / 15:30 PM bucket) using event prices. "
                "Compare forced close PnL vs extended same-day hold. No overnight carry."
            ),
            "success_criteria": [
                "forced_close_profit_cut_count and total_pnl_delta quantified",
                "if extended hold wins, propose event-driven pre-close exit (not time-only extension)",
                "day-trade no-overnight invariant preserved",
            ],
            "output_files": [
                "kabu_native/results/reports/phase135_session_close_whatif_review.json",
                "kabu_native/results/reports/phase135_session_close_trade_paths.csv",
                "kabu_native/results/reports/phase135_session_close_scenarios.csv",
            ],
            "implementation_risk": "low",
            "implementation_risk_notes": (
                "Timing change affects AM/PM bucket PnL attribution; review-only. "
                "Regulatory day-trade constraint non-negotiable."
            ),
            "next_phase": "Phase135_session_close_whatif_review",
        },
    ]


def _phase130_spec() -> dict[str, Any]:
    return {
        "phase": 130,
        "name": "range_hold_exit_review",
        "status": "ready_to_start",
        "script_planned": "kabu_native/scripts/run_phase130_range_hold_exit_review.py",
        "module_planned": "kabu_native/src/research/range_hold_exit_review.py",
        "purpose": (
            "Verify whether momentum_fade_exit / price_momentum_fade_exit incorrectly exit "
            "sideways consolidation (range_hold) vs true breakdown."
        ),
        "constraints": {
            "no_fixed_second_wait": True,
            "state_transition_only": True,
            "production_exit_unchanged": True,
            "review_only": True,
            "trigger_restriction_from_phase128": "mfe_pct > 0.15 recommended entry gate for fade_watch paths",
        },
        "classification": {
            "range_hold": [
                "post_fade_low not broken (price stays above recent support)",
                "MFE giveback <= 25% of peak MFE since fade",
                "price maintains high zone (no lower-low sequence)",
                "momentum weakened but no price breakdown (pure_price_momentum stable or recovering)",
            ],
            "breakdown": [
                "recent low breached",
                "MFE giveback > 25%",
                "price closes below fade_price",
                "lower-low sequence detected",
            ],
        },
        "scenarios": {
            "A": "current_immediate_fade_exit (combined_structural_exit_v1 baseline)",
            "B": "range_hold_continue_until_breakdown_or_session_end",
            "C": "breakdown_immediate_exit_only (skip exit on range_hold classification)",
        },
        "data_sources": [
            "kabu_native/results/small_paper/**/small_paper_events.csv",
            "kabu_native/results/reports/phase128_improved_vs_worsened.csv",
            "Phase127 A/B replay trades (fade_watch_entered flag)",
        ],
        "outputs": [
            "kabu_native/results/reports/phase130_range_hold_exit_review.json",
            "kabu_native/results/reports/phase130_range_hold_trade_paths.csv",
            "kabu_native/results/reports/phase130_range_hold_rule_candidates.csv",
        ],
        "verdict_options": {
            "A": "range_hold_exit_promising",
            "B": "current_fade_exit_best",
            "C": "state_signals_too_noisy",
            "D": "need_more_features",
        },
        "success_criteria": [
            "scenario B total_pnl > A on fade subset with mfe>0.15",
            "worsened_rate <= 35%",
            "range_hold precision >= 60% post-hoc on paths labeled hold-worthy",
            "median hold after fade event-driven (not clustered at 30/60/120s)",
        ],
        "implementation_notes": [
            "Reuse state_based_fade_exit_review tick stream and process_fade_watch_tick patterns",
            "Add range_hold classifier before fade_watch enter decision",
            "Do not modify fade_watch_shadow.py until Phase130 verdict A",
        ],
    }


def _render_md(plan: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Phase 129: Trading Logic Improvement Roadmap",
        "",
        f"Generated: {plan['generated_at']}",
        "",
        "## Global constraints",
        "",
    ]
    for k, v in plan["constraints"].items():
        lines.append(f"- **{k}**: {v}")
    lines.extend(["", "## Priority order", ""])
    for item in plan["prioritized_roadmap"]:
        lines.append(
            f"{item['priority']}. **{item['title']}** → `{item['next_phase']}`"
        )
    lines.extend(["", "## Issues", ""])
    for issue in plan["issues"]:
        lines.extend(
            [
                f"### Priority {issue['priority']}: {issue['title']}",
                "",
                f"**Issue ID:** `{issue['issue_id']}`",
                "",
                f"**Hypothesis:** {issue['hypothesis']}",
                "",
                "**Required data:**",
            ]
        )
        for d in issue["required_data"]:
            lines.append(f"- {d}")
        lines.extend(["", "**Existing files:**"])
        for f in issue["existing_files"][:8]:
            lines.append(f"- `{f}`")
        if len(issue["existing_files"]) > 8:
            lines.append(f"- … (+{len(issue['existing_files']) - 8} more)")
        lines.extend(["", "**Missing data:**"])
        for d in issue["missing_data"]:
            lines.append(f"- {d}")
        lines.extend(
            [
                "",
                f"**What-if method:** {issue['what_if_method']}",
                "",
                "**Success criteria:**",
            ]
        )
        for c in issue["success_criteria"]:
            lines.append(f"- {c}")
        lines.extend(
            [
                "",
                f"**Outputs:** {', '.join('`' + o + '`' for o in issue['output_files'])}",
                "",
                f"**Implementation risk:** {issue['implementation_risk']} — {issue['implementation_risk_notes']}",
                "",
                "---",
                "",
            ]
        )

    p130 = plan["phase130_start_spec"]
    lines.extend(
        [
            "## Phase 130 — Start specification (range_hold_exit_review)",
            "",
            f"**Purpose:** {p130['purpose']}",
            "",
            "### Classification",
            "",
            "**range_hold:**",
        ]
    )
    for r in p130["classification"]["range_hold"]:
        lines.append(f"- {r}")
    lines.extend(["", "**breakdown:**"])
    for r in p130["classification"]["breakdown"]:
        lines.append(f"- {r}")
    lines.extend(["", "### Scenarios", ""])
    for sid, desc in p130["scenarios"].items():
        lines.append(f"- **{sid}:** {desc}")
    lines.extend(["", "### Outputs", ""])
    for o in p130["outputs"]:
        lines.append(f"- `{o}`")
    lines.extend(["", "### Verdict options", ""])
    for k, v in p130["verdict_options"].items():
        lines.append(f"- **{k}:** `{v}`")
    lines.extend(["", "### Prior work context", ""])
    for k, v in plan["prior_work_summary"]["fade_phases"].items():
        lines.append(f"- **{k}:** {v}")
    lines.append(f"\n**Key insight:** {plan['prior_work_summary']['key_insight']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    _bootstrap()
    plan = build_plan()
    json_path = REPORTS / "phase129_trading_logic_improvement_plan.json"
    md_path = REPORTS / "phase129_trading_logic_improvement_plan.md"

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_md(plan), encoding="utf-8")

    print(
        json.dumps(
            {
                "phase": 129,
                "issues": len(plan["issues"]),
                "json": _rel(json_path),
                "md": _rel(md_path),
                "next": plan["prioritized_roadmap"][0]["next_phase"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
