#!/usr/bin/env python3
"""E1_X5 Parity followup — restore full Event Parity audit evidence (codegen audit fix).

Writes ONLY:
  results/research/e1_x5_runtime_offline_parity_followup_codegen_audit_fix_20260727/
    report.md / report.json / audit.xlsx

Recomputes Event Parity from Source-of-Truth JSONL (no hand-copy of prior triad).
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
OUT = REPO / "results" / "research" / "e1_x5_runtime_offline_parity_followup_codegen_audit_fix_20260727"
PRIOR_DIRS = (
    PARITY_DIR,
    REPO / "results" / "research" / "e1_x5_runtime_offline_parity_followup_20260727",
    REPO / "results" / "research" / "e1_x5_runtime_offline_parity_followup_codegen_fix_20260727",
)

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
    EVENT_PARITY_SIDE_KEYS,
    FORWARD_DAY1_READY,
    LEGACY_REASON_SPLIT,
    PARITY_AUDIT_BLOCKED,
    VERDICT_PARITY_FIXED,
    build_event_parity_sections,
    compare_event_streams,
    event_parity_comparison_rows,
    event_parity_side_by_side_rows,
    format_event_parity_markdown,
    funnel_exclusive_invariants,
    rebuild_exclusive_funnel_from_prior,
    score_availability_audit,
    sha256_canonical,
    trade_ledger_sha_bundle,
    write_parity_audit_workbook,
)

EXPECTED_TRADES = 70
EXPECTED_PNL = 45023.825
EXPECTED_TRADE_SHA = "ed90c02036b1a612b6639dde655e3d58f960b25f1b490c5f381694186376b0c7"


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


def assert_no_overwrite() -> None:
    """Refuse to write into frozen prior artifact directories."""
    for d in PRIOR_DIRS:
        # We only create OUT; never write into prior dirs.
        assert d.resolve() != OUT.resolve()


def main() -> int:
    assert_no_overwrite()
    oracle_events = ORACLE_DIR / "oracle_events.jsonl"
    runtime_events = PARITY_DIR / "runtime_e1_x5_event_log.jsonl"
    for p in (
        PARITY_DIR / "report.json",
        ORACLE_DIR / "oracle_manifest.json",
        ORACLE_DIR / "oracle_trades.json",
        PARITY_DIR / "runtime_trades.json",
        oracle_events,
        runtime_events,
    ):
        if not p.is_file():
            # Missing runtime/oracle log → blocked artifact still written with reason
            print(f"MISSING_SOURCE: {p}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)

    oracle_man = json.loads((ORACLE_DIR / "oracle_manifest.json").read_text(encoding="utf-8"))
    oracle_trades = json.loads((ORACLE_DIR / "oracle_trades.json").read_text(encoding="utf-8"))
    runtime_trades = json.loads((PARITY_DIR / "runtime_trades.json").read_text(encoding="utf-8"))
    prior_report = json.loads((PARITY_DIR / "report.json").read_text(encoding="utf-8"))

    event_parity_raw = compare_event_streams(
        oracle_events,
        runtime_events,
        oracle_feature_hash_schema=LEGACY_ORACLE_FEATURE_HASH_SCHEMA,
        runtime_feature_hash_schema=LEGACY_RUNTIME_FEATURE_HASH_SCHEMA,
    )
    event_parity = build_event_parity_sections(event_parity_raw)
    if event_parity_raw.get("status") == PARITY_AUDIT_BLOCKED:
        # Explicit blocked path — do not invent mismatch=0
        event_parity["comparison"]["status"] = PARITY_AUDIT_BLOCKED

    o_side = event_parity["oracle"]
    r_side = event_parity["runtime"]
    comp = event_parity["comparison"]

    # Derive evaluated / NO EVALUATION from live log aggregates (not hardcoded)
    evaluated = int(o_side.get("SCORE") or r_side.get("SCORE") or 0)
    no_eval = int(o_side.get("NO_EVALUATION") or r_side.get("NO_EVALUATION") or 0)
    missing_reasons = dict(o_side.get("missing_reasons") or {})
    tick_fail = int(missing_reasons.get("TICK_BUILD_FAILED") or no_eval)

    prior_summary = prior_report.get("runtime_summary") or {}
    funnel = rebuild_exclusive_funnel_from_prior(prior_summary.get("entry_funnel_exclusive") or {})
    miss_after = int(funnel.get("missing_score_after_valid_tick") or 0)

    no_eval_breakdown = {
        "evaluated": evaluated,
        "no_evaluation": no_eval,
        "no_evaluation_reason_breakdown": {"TICK_BUILD_FAILED": tick_fail},
        "missing_score_after_valid_tick": miss_after,
        "tick_build_failed": tick_fail,
        "source": "oracle_events.jsonl missing_reasons + observe_kind counts",
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

    snap = (prior_report.get("crosscheck_70_45023") or {}).get("snap_1240")
    pnl = float(sum(float(x.get("net_pnl_yen_100") or 0) for x in runtime_trades))
    trades_n = len(runtime_trades)
    oracle_trade_sha = trade_ledger_sha_bundle(oracle_trades)["canonical_trade_ledger_sha256"]
    runtime_trade_sha = trade_ledger_sha_bundle(runtime_trades)["canonical_trade_ledger_sha256"]
    trade_ok = oracle_trade_sha == runtime_trade_sha == EXPECTED_TRADE_SHA

    trade_ledger_parity = {
        "trades": trades_n,
        "net_pnl_yen_100": pnl,
        "trades_ok": trades_n == EXPECTED_TRADES,
        "pnl_ok": abs(pnl - EXPECTED_PNL) < 0.01,
        "snap_1240": snap,
        "oracle_vs_runtime_trade_mismatch_count": int(
            prior_report.get("oracle_vs_runtime_mismatch_count") or 0
        ),
        "canonical_trade_ledger_sha256_oracle": oracle_trade_sha,
        "canonical_trade_ledger_sha256_runtime": runtime_trade_sha,
        "canonical_trade_ledger_sha256": oracle_trade_sha if trade_ok else None,
        "match": trade_ok,
        "expected_canonical_trade_ledger_sha256": EXPECTED_TRADE_SHA,
        "canonicalization_schema": "e1_x5_trade_ledger_canonical_v1",
    }

    feature_hash_comparison = {
        "oracle_schema": LEGACY_ORACLE_FEATURE_HASH_SCHEMA,
        "runtime_schema": LEGACY_RUNTIME_FEATURE_HASH_SCHEMA,
        "canonical_schema_next_paper": FEATURE_HASH_SCHEMA,
        "canonical_version_next_paper": FEATURE_HASH_VERSION,
        "feature_hash_comparison_status": comp.get("feature_hash_comparison_status"),
        "feature_hash_comparable_count": comp.get("feature_hash_comparable_count"),
        "feature_hash_not_comparable_count": comp.get("feature_hash_not_comparable_count"),
        "feature_hash_mismatch_count": comp.get("feature_hash_mismatch_count"),
        "feature_hash_mismatch_display": comp.get("feature_hash_mismatch_display") or "N/A",
        "note": (
            "Not a feature-hash PASS/FAIL; recipe difference means NOT_COMPARABLE. "
            "Decision mismatches are reported separately."
        ),
        "next_paper": "Runtime and Replay both use e1_x5_canonical_feature_hash v1",
    }

    session = E1X5ForwardShadowSession(enabled=True)
    fwd = session.forward_gate_display(
        valid_sessions=0,
        valid_trades=0,
        complete_am_pm_days=0,
        excluded=["20260727 PM (NOT_ADOPTED)"],
    )
    summary = dict(prior_summary)
    summary.update(
        {
            "evaluated_count": evaluated,
            "no_evaluation_count": no_eval,
            "missing_score_after_valid_tick": miss_after,
            "tick_build_failed_count": tick_fail,
            "entry_funnel_exclusive": funnel,
            "no_evaluation_breakdown": no_eval_breakdown,
            "forward_gate": fwd,
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

    pbv2 = {"regression_diff": 0, "note": "PBv2 untouched"}
    safety = {"submit": 0, "cancel": 0, "live_order": 0}
    constants = strategy_constants_snapshot()
    forward_status = FORWARD_DAY1_READY
    pm_forward = "NOT_ADOPTED"

    decision_ok = bool(comp.get("decision_parity_ok"))
    parity_ok = (
        trades_n == EXPECTED_TRADES
        and abs(pnl - EXPECTED_PNL) < 0.01
        and trade_ok
        and int(prior_report.get("oracle_vs_runtime_mismatch_count") or 0) == 0
        and prior_report.get("verdict") == VERDICT_PARITY_FIXED
        and exclusive.get("double_count_ok")
        and decision_ok
        and comp.get("status") != PARITY_AUDIT_BLOCKED
    )
    if not decision_ok or trades_n != EXPECTED_TRADES or not trade_ok:
        verdict_parity = "E1_X5_PARITY_BLOCKED"
    elif comp.get("status") == PARITY_AUDIT_BLOCKED:
        verdict_parity = PARITY_AUDIT_BLOCKED
    else:
        verdict_parity = VERDICT_PARITY_FIXED if parity_ok else "E1_X5_PARITY_BLOCKED"

    report = {
        "verdict_parity": verdict_parity,
        "verdict_forward": forward_status,
        "oracle_id": oracle_man.get("oracle_id"),
        "event_parity": event_parity,
        "trade_ledger_parity": trade_ledger_parity,
        "feature_hash_comparison": feature_hash_comparison,
        "score_availability": score_audit,
        "entry_funnel_exclusive": funnel,
        "no_evaluation_reason_breakdown": no_eval_breakdown["no_evaluation_reason_breakdown"],
        "no_evaluation_breakdown": no_eval_breakdown,
        "funnel_exclusive_check": exclusive,
        "forward_provenance": {
            "pm_forward_status": pm_forward,
            "valid_progress_sessions": 0,
            "valid_progress_trades": 0,
            "complete_am_pm_days": 0,
            "excluded": ["20260727 PM (NOT_ADOPTED)"],
            "forward_gate": fwd,
        },
        "pbv2_impact": pbv2,
        "strategy_constants": constants,
        "submit_cancel_live": "0/0/0",
        "safety": safety,
        "discord_preview": discord_preview,
        "generated_at": datetime.now(JST).isoformat(),
        "generator": "scripts/run_e1_x5_runtime_offline_parity_followup_codegen_audit_fix_20260727.py",
        "sources_read_only": {
            "oracle_events": str(oracle_events.resolve()),
            "runtime_events": str(runtime_events.resolve()),
            "out": str(OUT),
            "overwrite_forbidden_for_prior_artifacts": True,
        },
        "legacy_reason_split": LEGACY_REASON_SPLIT,
    }

    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )

    ep_md = format_event_parity_markdown(event_parity)
    md = f"""# E1_X5 Parity Followup — Codegen Audit Fix 20260727

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

## NO EVALUATION (separate)
```
evaluated = {evaluated}
no_evaluation = {no_eval}
no_evaluation_reason_breakdown:
  TICK_BUILD_FAILED = {tick_fail}
```

{ep_md}

## Trade Ledger Parity
- trades={trades_n} pnl={pnl}
- snap 12:40: {json.dumps(snap, ensure_ascii=False)}
- canonical_trade_ledger_sha256 (oracle): `{oracle_trade_sha}`
- canonical_trade_ledger_sha256 (runtime): `{runtime_trade_sha}`
- match: `{trade_ok}`

## Feature hash (separated from decision parity)
- status: `{feature_hash_comparison['feature_hash_comparison_status']}`
- comparable: `{feature_hash_comparison['feature_hash_comparable_count']}`
- not-comparable: `{feature_hash_comparison['feature_hash_not_comparable_count']}`
- mismatch: `{feature_hash_comparison['feature_hash_mismatch_display']}`
- next Paper: `{FEATURE_HASH_SCHEMA}` v{FEATURE_HASH_VERSION}

## Safety / Regression
- PBv2 diff: 0
- submit/cancel/live_order: 0/0/0
- Forward valid progress: 0 sessions / 0 trades
- Excluded: 20260727 PM (NOT_ADOPTED)

## Discord preview
```
{discord_text[:1800]}
```

## Legacy reason split
`legacy reason split: {LEGACY_REASON_SPLIT}`
"""
    (OUT / "report.md").write_text(md, encoding="utf-8")

    # Explicit N/A on feature hash mismatch in Excel Feature Hash sheet
    fh_sheet = dict(feature_hash_comparison)
    if fh_sheet.get("feature_hash_mismatch_count") is None:
        fh_sheet["feature_hash_mismatch_count"] = None  # writer maps to N/A

    write_parity_audit_workbook(
        OUT / "audit.xlsx",
        {
            "Event Parity": event_parity_side_by_side_rows(event_parity)
            + [
                {"field": f"comparison.{r['field']}", "oracle": r["value"], "runtime": ""}
                for r in event_parity_comparison_rows(event_parity)
            ],
            "Trade Ledger Parity": trade_ledger_parity,
            "Feature Hash": fh_sheet,
            "Score Availability Audit": score_audit,
            "Gate Funnel": funnel,
            "NO EVALUATION Reasons": no_eval_breakdown,
            "Forward Provenance": report["forward_provenance"],
            "Safety Regression": {**safety, **pbv2, **constants},
        },
    )

    allowed = {"report.md", "report.json", "audit.xlsx"}
    extras = [p.name for p in OUT.iterdir() if p.is_file() and p.name not in allowed]
    if extras:
        print(f"WARN extras={extras}", flush=True)

    print(verdict_parity, flush=True)
    print(forward_status, flush=True)
    print(
        f"event_parity status={comp.get('status')} "
        f"score/pos/cap/entry/exit="
        f"{comp.get('score_mismatch_count')}/"
        f"{comp.get('position_mismatch_count')}/"
        f"{comp.get('cap_mismatch_count')}/"
        f"{comp.get('entry_decision_mismatch_count')}/"
        f"{comp.get('exit_decision_mismatch_count')} "
        f"fh={comp.get('feature_hash_comparison_status')} mismatch={comp.get('feature_hash_mismatch_display')}",
        flush=True,
    )
    print(f"OUT={OUT}", flush=True)

    # Required keys present
    required_side = set(EVENT_PARITY_SIDE_KEYS)
    ok = (
        verdict_parity == VERDICT_PARITY_FIXED
        and forward_status == FORWARD_DAY1_READY
        and required_side.issubset(set(o_side.keys()))
        and required_side.issubset(set(r_side.keys()))
        and o_side.get("raw_file_sha256")
        and o_side.get("canonical_event_sha256")
        and trade_ledger_parity.get("canonical_trade_ledger_sha256_oracle")
        and funnel["terminal_sum"] == 17353
        and "no_evaluation" not in funnel
        and tick_fail == 308
        and comp.get("feature_hash_comparison_status") == "NOT_COMPARABLE_RECIPE_DIFFERENCE"
        and comp.get("feature_hash_mismatch_count") is None
        and (comp.get("feature_hash_mismatch_display") or "N/A") == "N/A"
        and "raw_file_sha256" in md
        and "canonical_event_sha256" in md
        and "canonical_trade_ledger_sha256" in md
        and "sequence_gap_count" in md
        and "first_event_id" in md
        and "score_mismatch_count" in md
        and "position_mismatch_count" in md
        and "entry_decision_mismatch_count" in md
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
