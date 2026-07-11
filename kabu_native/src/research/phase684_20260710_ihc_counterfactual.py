"""Phase684 — 7/10 I/H/C shadow counterfactual reconstruction (research only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.market_sector_heat import _write_csv
from small_paper.ihc_shadow_counterfactual import (
    DEFAULT_SHADOW_CFG,
    SCENARIOS,
    audit_namespace_presence,
    build_daily_shadow_summary,
    enrich_trades_with_shadow,
    load_session_canonical_trades,
    missing_feature_audit,
    scenario_metrics,
)

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase684_20260710_ihc_counterfactual"

AM_DIR = NATIVE_ROOT / "results" / "small_paper" / "20260710" / "live_session_084821"
PM_DIR = NATIVE_ROOT / "results" / "small_paper" / "20260710" / "live_session_122525"

CANONICAL = {
    "AM": {"count": 36, "pnl": 8600.0},
    "PM": {"count": 38, "pnl": -36900.0},
    "DAILY": {"count": 74, "pnl": -28300.0},
}


def _scenario_table_row(
    label: str,
    am: Mapping[str, Any],
    pm: Mapping[str, Any],
    daily: Mapping[str, Any],
    *,
    actual_daily_pnl: float,
) -> dict[str, Any]:
    return {
        "scenario": label,
        "am_counterfactual_pnl": am.get("counterfactual_total_pnl_yen"),
        "pm_counterfactual_pnl": pm.get("counterfactual_total_pnl_yen"),
        "daily_counterfactual_pnl": daily.get("counterfactual_total_pnl_yen"),
        "delta_vs_actual": round(
            float(daily.get("counterfactual_total_pnl_yen") or 0) - actual_daily_pnl,
            2,
        ),
        "blocks": daily.get("blocked_count"),
        "lost_winners": daily.get("blocked_winner_count"),
        "avoided_losers": daily.get("blocked_loser_count"),
    }


def _exit_cause_blocks(trades: Sequence[Mapping[str, Any]], exit_reason: str) -> dict[str, int]:
    subset = [t for t in trades if str(t.get("exit_reason") or "") == exit_reason]
    return {
        "total": len(subset),
        "I_block": sum(1 for t in subset if t.get("I_block")),
        "H_block": sum(1 for t in subset if t.get("H_block")),
        "C_block": sum(1 for t in subset if t.get("C_block")),
        "IHC_union_block": sum(1 for t in subset if t.get("IHC_union_block")),
    }


def _saved_flag_mismatches(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in trades:
        for lane, saved_key, block_key in (
            ("I", "I_block_saved", "I_block"),
            ("H", "H_block_saved", "H_block"),
            ("C", "C_block_saved", "C_block"),
        ):
            if not t.get(saved_key):
                continue
            saved_val = t.get(f"readiness_{'precision' if lane == 'I' else 'economics' if lane == 'H' else ''}_shadow_block".replace("__", "_"))
            if lane == "C":
                saved_val = t.get("microsequence_recovery_fail_shadow_block")
            if saved_val is None:
                continue
            if bool(saved_val) != bool(t.get(block_key)):
                rows.append(
                    {
                        "position_id": t.get("position_id"),
                        "lane": lane,
                        "saved": bool(saved_val),
                        "recomputed": bool(t.get(block_key)),
                    }
                )
    return rows


def run_audit(*, write_outputs: bool = True) -> dict[str, Any]:
    am_audit = audit_namespace_presence(AM_DIR)
    pm_audit = audit_namespace_presence(PM_DIR)

    am_trades, am_meta = load_session_canonical_trades(
        AM_DIR, session_label="AM", expected_count=36, expected_pnl=8600.0
    )
    pm_trades, pm_meta = load_session_canonical_trades(
        PM_DIR, session_label="PM", expected_count=38, expected_pnl=-36900.0
    )

    am_idx = __import__("small_paper.ihc_shadow_counterfactual", fromlist=["build_session_price_index"]).build_session_price_index(AM_DIR)
    pm_idx = __import__("small_paper.ihc_shadow_counterfactual", fromlist=["build_session_price_index"]).build_session_price_index(PM_DIR)

    am_enriched = enrich_trades_with_shadow(am_trades, price_idx=am_idx, config=DEFAULT_SHADOW_CFG)
    pm_enriched = enrich_trades_with_shadow(pm_trades, price_idx=pm_idx, config=DEFAULT_SHADOW_CFG)
    daily_trades = am_enriched + pm_enriched

    daily_pnl = round(sum(float(t.get("pnl_yen_100") or 0) for t in daily_trades), 2)
    if len(daily_trades) != CANONICAL["DAILY"]["count"] or daily_pnl != CANONICAL["DAILY"]["pnl"]:
        raise ValueError(
            f"Daily canonical mismatch: trades={len(daily_trades)} pnl={daily_pnl}"
        )

    am_scenarios = {
        name: scenario_metrics(am_enriched, block_pred=pred, actual_total_pnl=am_meta["total_pnl_yen_100"])
        for name, pred in SCENARIOS
        if name != "actual"
    }
    pm_scenarios = {
        name: scenario_metrics(pm_enriched, block_pred=pred, actual_total_pnl=pm_meta["total_pnl_yen_100"])
        for name, pred in SCENARIOS
        if name != "actual"
    }
    daily_scenarios = {
        name: scenario_metrics(daily_trades, block_pred=pred, actual_total_pnl=daily_pnl)
        for name, pred in SCENARIOS
        if name != "actual"
    }

    table_rows = [
        {
            "scenario": "Actual",
            "am_counterfactual_pnl": 8600,
            "pm_counterfactual_pnl": -36900,
            "daily_counterfactual_pnl": -28300,
            "delta_vs_actual": 0.0,
            "blocks": 0,
            "lost_winners": 0,
            "avoided_losers": 0,
        },
    ]
    for key in ("I_only", "H_only", "C_only", "I_OR_H", "I_OR_H_OR_C"):
        table_rows.append(
            _scenario_table_row(
                key.replace("_only", "").replace("_OR_", " OR "),
                am_scenarios[key],
                pm_scenarios[key],
                daily_scenarios[key],
                actual_daily_pnl=daily_pnl,
            )
        )

    missing = missing_feature_audit(daily_trades)
    partial_coverage = any(r["not_evaluable_count"] > 0 for r in missing)
    logging_gap = not (am_audit["saved_flags_usable"] or pm_audit["saved_flags_usable"])

    i_union = daily_scenarios["I_OR_H_OR_C"]
    positive_scenarios = [
        name
        for name, m in daily_scenarios.items()
        if float(m.get("counterfactual_total_pnl_yen") or 0) > 0
    ]

    blocked_winners_pnl = round(
        sum(float(t.get("pnl_yen_100") or 0) for t in daily_trades if t.get("IHC_union_block") and float(t.get("pnl_yen_100") or 0) > 0),
        2,
    )
    blocked_big_winners = [t for t in daily_trades if t.get("IHC_union_block") and t.get("is_big_winner")]

    report: dict[str, Any] = {
        "phase": 684,
        "date": "20260710",
        "phase683_namespace": {
            "AM_phase683_post": am_audit["phase683_namespace_saved"],
            "PM_phase683_post": pm_audit["phase683_namespace_saved"],
            "AM_saved_flags_usable": am_audit["saved_flags_usable"],
            "PM_saved_flags_usable": pm_audit["saved_flags_usable"],
            "reconstruction_from_saved_flags_only": False,
            "recomputed_items": [
                "readiness_bounce_from_recent_low_accept (price ring at entry)",
                "microseq_bounce_from_recent_low",
                "microseq_fall_from_recent_high",
                "microseq_slope_5min",
                "I_block / H_block / C_block / union flags",
            ],
        },
        "canonical_verification": {
            "AM": am_meta,
            "PM": pm_meta,
            "DAILY": {"trade_count": len(daily_trades), "total_pnl_yen_100": daily_pnl},
        },
        "scenario_table": table_rows,
        "am_scenarios": am_scenarios,
        "pm_scenarios": pm_scenarios,
        "daily_scenarios": daily_scenarios,
        "cause_analysis": {
            "PM_no_progress_exit": _exit_cause_blocks(pm_enriched, "no_progress_exit"),
            "AM_stop_hit": _exit_cause_blocks(am_enriched, "stop_hit"),
            "PM_stop_hit": _exit_cause_blocks(pm_enriched, "stop_hit"),
            "IHC_blocked_winner_total_pnl": blocked_winners_pnl,
            "IHC_blocked_big_winner_count": len(blocked_big_winners),
            "IHC_blocked_big_winner_pnl": round(sum(float(t.get("pnl_yen_100") or 0) for t in blocked_big_winners), 2),
            "positive_counterfactual_scenarios": positive_scenarios,
        },
        "missing_feature_audit": missing,
        "saved_flag_mismatches": _saved_flag_mismatches(daily_trades),
        "am_summary": build_daily_shadow_summary(am_enriched, actual_total_pnl=am_meta["total_pnl_yen_100"]),
        "pm_summary": build_daily_shadow_summary(pm_enriched, actual_total_pnl=pm_meta["total_pnl_yen_100"]),
        "daily_summary": build_daily_shadow_summary(daily_trades, actual_total_pnl=daily_pnl),
    }

    if logging_gap:
        verdict = "SHADOW_LOGGING_GAP_FOUND"
        if partial_coverage:
            verdict = "PARTIAL_FEATURE_COVERAGE"
    elif partial_coverage:
        verdict = "PARTIAL_FEATURE_COVERAGE"
    else:
        verdict = "IHC_20260710_COUNTERFACTUAL_READY"

    report["verdict"] = verdict

    if write_outputs:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "phase684_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (REPORT_DIR / "phase684_20260710_am_summary.json").write_text(
            json.dumps(report["am_summary"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (REPORT_DIR / "phase684_20260710_pm_summary.json").write_text(
            json.dumps(report["pm_summary"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (REPORT_DIR / "phase684_20260710_daily_summary.json").write_text(
            json.dumps(report["daily_summary"], ensure_ascii=False, indent=2), encoding="utf-8"
        )

        summary_fields = [
            "scenario",
            "am_counterfactual_pnl",
            "pm_counterfactual_pnl",
            "daily_counterfactual_pnl",
            "delta_vs_actual",
            "blocks",
            "lost_winners",
            "avoided_losers",
        ]
        _write_csv(REPORT_DIR / "phase684_20260710_counterfactual_summary.csv", summary_fields, table_rows)

        trade_fields = [
            "session",
            "position_id",
            "symbol",
            "entry_time",
            "exit_time",
            "pnl_yen_100",
            "exit_reason",
            "hold_sec",
            "is_winner",
            "is_loser",
            "is_big_winner",
            "is_early_stop_300s",
            "is_stop_hit",
            "live_feature_complete",
            "entry_expectancy_score_v2",
            "readiness_bounce_from_recent_low_accept",
            "microseq_bounce_from_recent_low",
            "microseq_fall_from_recent_high",
            "microseq_slope_5min",
            "I_block",
            "H_block",
            "C_block",
            "IH_union_block",
            "IC_union_block",
            "HC_union_block",
            "IHC_union_block",
            "overlap_type",
            "readiness_bounce_source",
            "microseq_source",
        ]
        shadow_rows = [{k: t.get(k) for k in trade_fields} for t in daily_trades]
        _write_csv(REPORT_DIR / "phase684_20260710_shadow_trades.csv", trade_fields, shadow_rows)

        miss_fields = ["lane", "evaluable_count", "not_evaluable_count", "missing_feature_counts", "missing_trade_ids"]
        _write_csv(REPORT_DIR / "phase684_missing_feature_audit.csv", miss_fields, missing)

        persistence = {
            "finalize_session_ihc_shadow_summary_keys": list(report["daily_summary"].keys()),
            "lane_summary_keys": list(report["daily_summary"]["readiness_precision_shadow"].keys()),
            "portfolio_keys": list(report["daily_summary"]["shadow_ihc_portfolio"].keys()),
            "discord_sample": __import__(
                "small_paper.ihc_shadow_counterfactual",
                fromlist=["format_entry_shadow_discord_lines"],
            ).format_entry_shadow_discord_lines(report["daily_summary"]),
        }
        (REPORT_DIR / "phase684_daily_summary_persistence_test.json").write_text(
            json.dumps(persistence, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        _write_decision_md(report, table_rows)

    return report


def _write_decision_md(report: Mapping[str, Any], table_rows: Sequence[Mapping[str, Any]]) -> None:
  ds = report["daily_scenarios"]
  lines = [
    "# Phase684 Decision — 2026-07-10 I/H/C Counterfactual",
    "",
    f"**Verdict:** `{report.get('verdict')}`",
    "",
    "## Scenario Table",
    "",
    "| Scenario | AM | PM | 7/10 Total | Δ vs Actual | Blocks | Lost Winners | Avoided Losers |",
    "|----------|----|----|------------|-------------|--------|--------------|----------------|",
  ]
  for row in table_rows:
    sc = row["scenario"]
    delta_cell = "" if sc == "Actual" else f"{row['delta_vs_actual']:+,.0f}"
    lines.append(
      f"| {sc} | {row['am_counterfactual_pnl']:+,.0f} | {row['pm_counterfactual_pnl']:+,.0f} | "
      f"{row['daily_counterfactual_pnl']:+,.0f} | {delta_cell} | "
      f"{row['blocks']} | {row['lost_winners']} | {row['avoided_losers']} |"
    )
  lines.extend(
    [
      "",
      "## Phase683 Namespace",
      "",
      f"- AM Phase683 post-run: **{report['phase683_namespace']['AM_phase683_post']}**",
      f"- PM Phase683 post-run: **{report['phase683_namespace']['PM_phase683_post']}**",
      f"- Saved shadow flags usable: AM={report['phase683_namespace']['AM_saved_flags_usable']}, PM={report['phase683_namespace']['PM_saved_flags_usable']}",
      "- Reconstruction used entry-time price ring for H accept bounce and C microseq (no post-entry data).",
      "",
      "## Key Answers",
      "",
      f"1. I only → AM {report['am_scenarios']['I_only']['counterfactual_total_pnl_yen']:+,.0f} / PM {report['pm_scenarios']['I_only']['counterfactual_total_pnl_yen']:+,.0f} / Total {ds['I_only']['counterfactual_total_pnl_yen']:+,.0f}",
      f"2. H only → AM {report['am_scenarios']['H_only']['counterfactual_total_pnl_yen']:+,.0f} / PM {report['pm_scenarios']['H_only']['counterfactual_total_pnl_yen']:+,.0f} / Total {ds['H_only']['counterfactual_total_pnl_yen']:+,.0f}",
      f"3. C only → AM {report['am_scenarios']['C_only']['counterfactual_total_pnl_yen']:+,.0f} / PM {report['pm_scenarios']['C_only']['counterfactual_total_pnl_yen']:+,.0f} / Total {ds['C_only']['counterfactual_total_pnl_yen']:+,.0f}",
      f"4. I OR H → Total {ds['I_OR_H']['counterfactual_total_pnl_yen']:+,.0f}",
      f"5. I OR H OR C → Total {ds['I_OR_H_OR_C']['counterfactual_total_pnl_yen']:+,.0f}",
      f"6. Actual -28,300 turns positive: **{', '.join(report['cause_analysis']['positive_counterfactual_scenarios']) or 'none'}**",
      "",
      "## Caution",
      "",
      "7/10 alone must NOT drive mainline promotion. Shadow remained observation-only; this is counterfactual reconstruction on one day.",
    ]
  )
  (REPORT_DIR / "phase684_decision.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    out = run_audit()
    print(json.dumps({"verdict": out["verdict"], "daily_I_OR_H_OR_C": out["daily_scenarios"]["I_OR_H_OR_C"]}, ensure_ascii=False, indent=2))
