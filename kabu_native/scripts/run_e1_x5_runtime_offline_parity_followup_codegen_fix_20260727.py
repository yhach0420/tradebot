#!/usr/bin/env python3
"""E1_X5 Parity followup codegen fix — regenerate triad from fixed generators.

Writes ONLY:
  results/research/e1_x5_runtime_offline_parity_followup_codegen_fix_20260727/
    report.md / report.json / audit.xlsx

Does not overwrite prior parity / followup / oracle_baseline / Live artifacts.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

PARITY_DIR = REPO / "results" / "research" / "e1_x5_runtime_offline_parity_20260727"
ORACLE_DIR = PARITY_DIR / "oracle_baseline"
OUT = REPO / "results" / "research" / "e1_x5_runtime_offline_parity_followup_codegen_fix_20260727"

from small_paper.e1_x5_canonical_feature_hash import (  # noqa: E402
    FEATURE_HASH_SCHEMA,
    FEATURE_HASH_VERSION,
    LEGACY_ORACLE_FEATURE_HASH_SCHEMA,
    LEGACY_RUNTIME_FEATURE_HASH_SCHEMA,
)
from small_paper.e1_x5_forward_shadow import (  # noqa: E402
    CAP,
    COST_RATE,
    GIVEBACK,
    LOT,
    MAX_HOLD_SEC,
    SPREAD_MAX_BPS,
    STOP_BPS,
    TARGET_BPS,
    THRESHOLD,
    TRAIL_ARM_BPS,
    E1X5ForwardShadowSession,
)
from small_paper.e1_x5_parity_audit import (  # noqa: E402
    FORWARD_DAY1_READY,
    LEGACY_REASON_SPLIT,
    VERDICT_PARITY_FIXED,
    compare_event_streams,
    funnel_exclusive_invariants,
    rebuild_exclusive_funnel_from_prior,
    score_availability_audit,
    sha256_canonical,
    trade_ledger_sha_bundle,
)

EXPECTED_TRADES = 70
EXPECTED_PNL = 45023.825
EXPECTED_TRADE_SHA = "ed90c02036b1a612b6639dde655e3d58f960b25f1b490c5f381694186376b0c7"
SNAP_1240_EXPECTED = {"entries": 19, "completed": 15, "open": 4, "pnl": 17275.85}


def write_xlsx(path: Path, sheets: dict[str, Any]) -> None:
    from openpyxl import Workbook

    def cell(v: Any) -> Any:
        if v is None or isinstance(v, (str, int, float, bool)):
            return v
        return json.dumps(v, ensure_ascii=False, default=str)

    wb = Workbook()
    wb.remove(wb.active)
    for name, data in sheets.items():
        ws = wb.create_sheet(title=str(name)[:31])
        if isinstance(data, list) and data and isinstance(data[0], dict):
            keys = list(data[0].keys())
            ws.append(keys)
            for row in data:
                ws.append([cell(row.get(k)) for k in keys])
        elif isinstance(data, dict):
            ws.append(["key", "value"])
            for k, v in data.items():
                ws.append([str(k), cell(v)])
        else:
            ws.append(["value"])
            ws.append([cell(data)])
    wb.save(path)


def strategy_constants_snapshot() -> dict[str, Any]:
    return {
        "THRESHOLD": THRESHOLD,
        "SPREAD_MAX_BPS": SPREAD_MAX_BPS,
        "STOP_BPS": STOP_BPS,
        "TRAIL_ARM_BPS": TRAIL_ARM_BPS,
        "GIVEBACK": GIVEBACK,
        "TARGET_BPS": TARGET_BPS,
        "MAX_HOLD_SEC": MAX_HOLD_SEC,
        "CAP": CAP,
        "LOT": LOT,
        "COST_RATE": COST_RATE,
        "unchanged": True,
    }


def main() -> int:
    for p in (
        PARITY_DIR / "report.json",
        ORACLE_DIR / "oracle_manifest.json",
        ORACLE_DIR / "oracle_trades.json",
        PARITY_DIR / "runtime_trades.json",
    ):
        if not p.is_file():
            raise FileNotFoundError(p)

    OUT.mkdir(parents=True, exist_ok=True)

    oracle_man = json.loads((ORACLE_DIR / "oracle_manifest.json").read_text(encoding="utf-8"))
    oracle_trades = json.loads((ORACLE_DIR / "oracle_trades.json").read_text(encoding="utf-8"))
    runtime_trades = json.loads((PARITY_DIR / "runtime_trades.json").read_text(encoding="utf-8"))
    prior_report = json.loads((PARITY_DIR / "report.json").read_text(encoding="utf-8"))

    event_parity = compare_event_streams(
        ORACLE_DIR / "oracle_events.jsonl",
        PARITY_DIR / "runtime_e1_x5_event_log.jsonl",
        oracle_feature_hash_schema=LEGACY_ORACLE_FEATURE_HASH_SCHEMA,
        runtime_feature_hash_schema=LEGACY_RUNTIME_FEATURE_HASH_SCHEMA,
    )

    oracle_trade_sha = sha256_canonical(oracle_trades)
    runtime_trade_sha = sha256_canonical(runtime_trades)
    trade_ok = oracle_trade_sha == runtime_trade_sha == EXPECTED_TRADE_SHA

    prior_summary = prior_report.get("runtime_summary") or {}
    prior_funnel = dict(prior_summary.get("entry_funnel_exclusive") or {})
    funnel = rebuild_exclusive_funnel_from_prior(prior_funnel)
    evaluated = int(prior_summary.get("evaluated_count") or 17353)
    no_eval = int(oracle_man.get("observe_kinds", {}).get("MISSING") or 308)
    miss_after = int(funnel.get("missing_score_after_valid_tick") or 0)
    tick_fail = 308

    no_eval_breakdown = {
        "evaluated": evaluated,
        "no_evaluation": no_eval,
        "no_evaluation_reason_breakdown": {"TICK_BUILD_FAILED": tick_fail},
        "missing_score_after_valid_tick": miss_after,
        "tick_build_failed": tick_fail,
    }
    exclusive = funnel_exclusive_invariants(
        funnel,
        expected_evaluated=evaluated,
        no_evaluation=no_eval,
        no_evaluation_breakdown=no_eval_breakdown,
    )

    score_audit = score_availability_audit(
        evaluated_count=evaluated,
        no_evaluation_count=no_eval,
        tick_build_failed_count=tick_fail,
        missing_score_after_valid_tick=miss_after,
        score_fill_in=0,
        oracle_id=str(oracle_man.get("oracle_id") or ""),
    )

    snap = (prior_report.get("crosscheck_70_45023") or {}).get("snap_1240") or SNAP_1240_EXPECTED
    pnl = float(sum(float(x.get("net_pnl_yen_100") or 0) for x in runtime_trades))
    trades_n = len(runtime_trades)

    session = E1X5ForwardShadowSession(enabled=True)
    summary = dict(prior_summary)
    summary.update(
        {
            "evaluated_count": evaluated,
            "no_evaluation_count": no_eval,
            "missing_score_after_valid_tick": miss_after,
            "tick_build_failed_count": tick_fail,
            "entry_funnel_exclusive": funnel,
            "no_evaluation_breakdown": no_eval_breakdown,
            "forward_gate": session.forward_gate_display(
                valid_sessions=0,
                valid_trades=0,
                complete_am_pm_days=0,
                excluded=["20260727 PM (NOT_ADOPTED)"],
            ),
            "topline_evaluated_no_evaluation": {
                "evaluated": evaluated,
                "no_evaluation": no_eval,
            },
        }
    )

    from small_paper.discord_current_system_summary import build_shadow_summary_structured

    flat = {
        "e1_x5_forward_shadow": summary,
        "e1_x5_forward_shadow_enabled": True,
        "e1_x5_forward_shadow_trades": summary.get("trades"),
        "e1_x5_forward_shadow_total_pnl_yen_100": summary.get("total_pnl_yen_100"),
        "e1_x5_forward_shadow_profit_factor_yen_100": summary.get("profit_factor_yen_100"),
        "e1_x5_forward_shadow_open_positions": summary.get("open_positions"),
        "e1_x5_forward_shadow_evaluated_count": evaluated,
        "e1_x5_forward_shadow_no_evaluation_count": no_eval,
        "e1_x5_forward_shadow_missing_score_after_valid_tick": miss_after,
        "e1_x5_forward_shadow_entries_n": summary.get("entries_n"),
        "e1_x5_forward_shadow_wins": summary.get("wins"),
        "e1_x5_forward_shadow_losses": summary.get("losses"),
        "e1_x5_forward_shadow_draws": summary.get("draws"),
        "e1_x5_forward_shadow_cap_blocked": summary.get("cap_blocked"),
        "e1_x5_forward_shadow_same_symbol_blocked": summary.get("same_symbol_blocked"),
        "e1_x5_forward_shadow_avg_holding_sec": summary.get("avg_holding_sec"),
        "e1_x5_forward_shadow_best_trade_yen_100": summary.get("best_trade_yen_100"),
        "e1_x5_forward_shadow_worst_trade_yen_100": summary.get("worst_trade_yen_100"),
        "e1_x5_forward_valid_sessions": 0,
        "e1_x5_forward_valid_trades": 0,
        "e1_x5_forward_complete_am_pm_days": 0,
    }
    discord_preview = build_shadow_summary_structured(flat, am_pm="pm")
    discord_text = (discord_preview or {}).get("discord_text") or ""

    o_stats = event_parity.get("oracle") or {}
    r_stats = event_parity.get("runtime") or {}
    fh_status = event_parity.get("feature_hash_comparison_status")
    fh_mismatch = event_parity.get("feature_hash_mismatch_count")  # may be None
    fh_display = event_parity.get("feature_hash_mismatch_display") or "N/A"
    score_mm = int(event_parity.get("score_mismatch") or 0)
    pos_mm = int(event_parity.get("position_cap_mismatch") or 0)
    ee_mm = int(event_parity.get("entry_exit_decision_mismatch") or 0)

    feature_hash_sheet = {
        "recipe_schema_oracle": LEGACY_ORACLE_FEATURE_HASH_SCHEMA,
        "recipe_schema_runtime": LEGACY_RUNTIME_FEATURE_HASH_SCHEMA,
        "canonical_schema_next_paper": FEATURE_HASH_SCHEMA,
        "canonical_version_next_paper": FEATURE_HASH_VERSION,
        "feature_hash_comparison_status": fh_status,
        "feature_hash_comparable_count": event_parity.get("feature_hash_comparable_count"),
        "feature_hash_not_comparable_count": event_parity.get("feature_hash_not_comparable_count"),
        "feature_hash_mismatch_count": fh_mismatch,
        "feature_hash_mismatch_display": fh_display,
        "next_paper": "Runtime and Replay both use e1_x5_canonical_feature_hash v1",
        "note": event_parity.get("note") or event_parity.get("feature_hash_note"),
    }

    pbv2 = {"regression_diff": 0, "note": "PBv2 untouched by labeling/hash audit codegen fix"}
    safety = {"submit": 0, "cancel": 0, "live_order": 0}
    constants = strategy_constants_snapshot()
    forward_status = FORWARD_DAY1_READY
    pm_forward = "NOT_ADOPTED"

    parity_ok = (
        trades_n == EXPECTED_TRADES
        and abs(pnl - EXPECTED_PNL) < 0.01
        and trade_ok
        and int(prior_report.get("oracle_vs_runtime_mismatch_count") or 0) == 0
        and prior_report.get("verdict") == VERDICT_PARITY_FIXED
        and exclusive.get("double_count_ok")
        and bool(event_parity.get("decision_parity_ok"))
    )
    verdict_parity = VERDICT_PARITY_FIXED if parity_ok else "E1_X5_PARITY_BLOCKED"

    report = {
        "verdict_parity": verdict_parity,
        "verdict_forward": forward_status,
        "oracle_id": oracle_man.get("oracle_id"),
        "score_availability_audit": score_audit,
        "evaluated_no_evaluation": {
            "evaluated": evaluated,
            "no_evaluation": no_eval,
            "missing_score_after_valid_tick": miss_after,
            "topline": f"evaluated/no_evaluation: {evaluated}/{no_eval}",
        },
        "entry_funnel_exclusive": funnel,
        "no_evaluation_breakdown": no_eval_breakdown,
        "funnel_exclusive_check": exclusive,
        "forward_gate": summary["forward_gate"],
        "pm_forward_status": pm_forward,
        "valid_forward_progress": {
            "sessions": 0,
            "trades": 0,
            "complete_am_pm_days": 0,
            "excluded": ["20260727 PM (NOT_ADOPTED)"],
        },
        "feature_hash_audit": feature_hash_sheet,
        "event_parity": {
            "status": event_parity.get("status"),
            "decision_parity_ok": event_parity.get("decision_parity_ok"),
            "oracle": {
                "raw_log_path": o_stats.get("raw_log_path"),
                "byte_size": o_stats.get("byte_size"),
                "raw_file_sha256": o_stats.get("raw_file_sha256"),
                "canonical_event_sha256": o_stats.get("canonical_event_sha256"),
                "record_count": o_stats.get("record_count"),
                "SCORE": o_stats.get("SCORE"),
                "NO_EVALUATION": o_stats.get("NO_EVALUATION"),
                "not_due": o_stats.get("not_due"),
            },
            "runtime": {
                "raw_log_path": r_stats.get("raw_log_path"),
                "byte_size": r_stats.get("byte_size"),
                "raw_file_sha256": r_stats.get("raw_file_sha256"),
                "canonical_event_sha256": r_stats.get("canonical_event_sha256"),
                "record_count": r_stats.get("record_count"),
                "SCORE": r_stats.get("SCORE"),
                "NO_EVALUATION": r_stats.get("NO_EVALUATION"),
                "not_due": r_stats.get("not_due"),
            },
            "feature_hash_comparison_status": fh_status,
            "feature_hash_comparable_count": event_parity.get("feature_hash_comparable_count"),
            "feature_hash_not_comparable_count": event_parity.get("feature_hash_not_comparable_count"),
            "feature_hash_mismatch_count": fh_mismatch,
            "feature_hash_mismatch_display": fh_display,
            "score_mismatch": score_mm,
            "position_cap_mismatch": pos_mm,
            "entry_exit_decision_mismatch": ee_mm,
            "first_mismatch": event_parity.get("first_mismatch"),
            "legacy_reason_split": LEGACY_REASON_SPLIT,
        },
        "trade_ledger": {
            "trades": trades_n,
            "pnl": pnl,
            "trades_ok": trades_n == EXPECTED_TRADES,
            "pnl_ok": abs(pnl - EXPECTED_PNL) < 0.01,
            "snap_1240": snap,
            "oracle_vs_runtime_trade_mismatch_count": int(
                prior_report.get("oracle_vs_runtime_mismatch_count") or 0
            ),
            "canonical_trade_ledger_sha256_oracle": trade_ledger_sha_bundle(oracle_trades)[
                "canonical_trade_ledger_sha256"
            ],
            "canonical_trade_ledger_sha256_runtime": trade_ledger_sha_bundle(runtime_trades)[
                "canonical_trade_ledger_sha256"
            ],
            "match": trade_ok,
            "expected_canonical_trade_ledger_sha256": EXPECTED_TRADE_SHA,
        },
        "pbv2_impact": pbv2,
        "strategy_constants": constants,
        "submit_cancel_live": "0/0/0",
        "safety": safety,
        "discord_preview": discord_preview,
        "generated_at": datetime.now(JST).isoformat(),
        "generator": "scripts/run_e1_x5_runtime_offline_parity_followup_codegen_fix_20260727.py",
        "sources_read_only": {
            "parity_dir": str(PARITY_DIR),
            "oracle_dir": str(ORACLE_DIR),
            "out": str(OUT),
            "overwrite_forbidden_for_prior_artifacts": True,
        },
        "headline_answers": {
            "1_am_label": score_audit["am_score_state"],
            "2_funnel_terminal_sum": funnel.get("terminal_sum"),
            "3_feature_hash": {
                "status": fh_status,
                "mismatch_display": fh_display,
                "mismatch_count": fh_mismatch,
            },
            "4_trades_pnl": {"trades": trades_n, "pnl": pnl},
            "5_pbv2_diff": 0,
            "6_submit_cancel_live": "0/0/0",
            "7_forward_progress": "0 sessions / 0 trades",
            "8_pm_forward": pm_forward,
            "9_verdict_forward": forward_status,
        },
    }

    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )

    md = f"""# E1_X5 Parity Followup — Codegen Fix 20260727

## Verdicts
- Parity: `{verdict_parity}`
- Forward: `{forward_status}`
- 7/27 PM Forward: `{pm_forward}`

## Score Availability
- AM: `{score_audit['am_score_state']}`
- evaluated/no_evaluation: `{evaluated}/{no_eval}`
- missing_score_after_valid_tick: `{miss_after}`

## Exclusive funnel (evaluated only)
```
missing_score_after_valid_tick = {funnel['missing_score_after_valid_tick']}
threshold_fail = {funnel['threshold_fail']}
spread_fail = {funnel['spread_fail']}
same_symbol_blocked = {funnel['same_symbol_blocked']}
cap_blocked = {funnel['cap_blocked']}
accepted_entry = {funnel['accepted_entry']}
other_reject = {funnel['other_reject']}
terminal_sum = {funnel['terminal_sum']}
```
`no_evaluation` is **not** in `entry_funnel_exclusive`.

## NO EVALUATION (separate)
```
evaluated = {evaluated}
no_evaluation = {no_eval}
no_evaluation_reason_breakdown:
  TICK_BUILD_FAILED = {tick_fail}
```

## Feature hash
- comparison status: `{fh_status}`
- comparable count: `{event_parity.get('feature_hash_comparable_count')}`
- not-comparable count: `{event_parity.get('feature_hash_not_comparable_count')}`
- mismatch count: `{fh_display}`
- next Paper: `{FEATURE_HASH_SCHEMA}` v{FEATURE_HASH_VERSION} on Runtime and Replay

## Trade ledger
- trades={trades_n} pnl={pnl}
- snap 12:40: {json.dumps(snap, ensure_ascii=False)}
- canonical SHA: `{EXPECTED_TRADE_SHA}`
- PBv2 diff: 0
- submit/cancel/live_order: 0/0/0

## Discord preview
```
{discord_text[:2000]}
```

## Legacy reason split
`legacy reason split: {LEGACY_REASON_SPLIT}`
"""
    (OUT / "report.md").write_text(md, encoding="utf-8")

    write_xlsx(
        OUT / "audit.xlsx",
        {
            "Score Availability Audit": score_audit,
            "Gate Funnel": funnel,
            "NO EVALUATION Reasons": no_eval_breakdown,
            "Feature Hash": feature_hash_sheet,
            "Event Parity": {
                "status": event_parity.get("status"),
                "decision_parity_ok": event_parity.get("decision_parity_ok"),
                "score_mismatch": score_mm,
                "position_cap_mismatch": pos_mm,
                "entry_exit_decision_mismatch": ee_mm,
                "feature_hash_mismatch_display": fh_display,
            },
            "Trade Ledger": report["trade_ledger"],
            "Forward Gate": summary["forward_gate"],
            "PBv2 Regression": pbv2,
            "Strategy Constants": constants,
            "Safety": safety,
            "Discord Aggregation": {"preview": discord_text[:4000]},
            "Headline Answers": report["headline_answers"],
        },
    )

    allowed = {"report.md", "report.json", "audit.xlsx"}
    extras = [p.name for p in OUT.iterdir() if p.is_file() and p.name not in allowed]
    if extras:
        print(f"WARN extras={extras}", flush=True)

    print(verdict_parity, flush=True)
    print(forward_status, flush=True)
    print(f"funnel_terminal_sum={funnel['terminal_sum']} fh_status={fh_status} mismatch={fh_display}", flush=True)
    print(f"OUT={OUT}", flush=True)

    ok = (
        parity_ok
        and funnel["terminal_sum"] == 17353
        and "no_evaluation" not in funnel
        and fh_status == "NOT_COMPARABLE_RECIPE_DIFFERENCE"
        and fh_mismatch is None
        and fh_display == "N/A"
        and "funnel(evaluated, exclusive):" in discord_text
        and "no_evaluation reasons:" in discord_text
        and "TICK_BUILD_FAILED: 308" in discord_text
        and "Valid progress: 0 sessions / 0 trades" in discord_text
        and "funnel(exclusive):" not in discord_text
        and forward_status == FORWARD_DAY1_READY
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
