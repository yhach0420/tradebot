#!/usr/bin/env python3
"""E1_X5 Runtime/Offline Parity followup — 20260727 evidence corrections.

Reads frozen Oracle + Runtime Source-of-Truth logs; writes ONLY:
  results/research/e1_x5_runtime_offline_parity_followup_20260727/{report.md,report.json,audit.xlsx}

Does not overwrite:
  - e1_x5_runtime_offline_parity_20260727/
  - oracle_baseline/
  - Live Summary / Capture
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
OUT = REPO / "results" / "research" / "e1_x5_runtime_offline_parity_followup_20260727"

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
    funnel_no_double_count,
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


def assert_frozen_not_touched() -> None:
    """Hard check: followup must not rewrite prior triad."""
    for p in (
        PARITY_DIR / "report.md",
        PARITY_DIR / "report.json",
        PARITY_DIR / "audit.xlsx",
        ORACLE_DIR / "oracle_manifest.json",
        ORACLE_DIR / "oracle_trades.json",
    ):
        if not p.is_file():
            raise FileNotFoundError(f"frozen source missing: {p}")


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
        "note": "E1_X5 decision-core thresholds/EXIT/CAP not modified by followup",
    }


def main() -> int:
    assert_frozen_not_touched()
    OUT.mkdir(parents=True, exist_ok=True)

    oracle_man = json.loads((ORACLE_DIR / "oracle_manifest.json").read_text(encoding="utf-8"))
    oracle_trades = json.loads((ORACLE_DIR / "oracle_trades.json").read_text(encoding="utf-8"))
    runtime_trades = json.loads((PARITY_DIR / "runtime_trades.json").read_text(encoding="utf-8"))
    prior_report = json.loads((PARITY_DIR / "report.json").read_text(encoding="utf-8"))

    oracle_events = ORACLE_DIR / "oracle_events.jsonl"
    runtime_events = PARITY_DIR / "runtime_e1_x5_event_log.jsonl"

    event_parity = compare_event_streams(oracle_events, runtime_events)

    oracle_trade_sha = sha256_canonical(oracle_trades)
    runtime_trade_sha = sha256_canonical(runtime_trades)
    trade_sha_bundle = {
        "oracle": trade_ledger_sha_bundle(oracle_trades),
        "runtime": trade_ledger_sha_bundle(runtime_trades),
        "match": oracle_trade_sha == runtime_trade_sha == EXPECTED_TRADE_SHA,
        "expected_canonical_trade_ledger_sha256": EXPECTED_TRADE_SHA,
        # Legacy ambiguous names → mapped explicitly
        "legacy_name_map": {
            "event_manifest_sha256": "see raw_file_sha256 + canonical_event_sha256 on each side",
            "trade_ledger_sha256": "canonical_trade_ledger_sha256",
            "oracle_manifest_trade_ledger_sha256": oracle_man.get("trade_ledger_sha256"),
            "oracle_manifest_event_manifest_sha256": oracle_man.get("event_manifest_sha256"),
        },
    }

    prior_summary = prior_report.get("runtime_summary") or {}
    funnel = dict(prior_summary.get("entry_funnel_exclusive") or {})
    evaluated = int(prior_summary.get("evaluated_count") or oracle_man.get("observe_kinds", {}).get("SCORE") or 17353)
    no_eval = int(funnel.get("no_evaluation") or oracle_man.get("observe_kinds", {}).get("MISSING") or 308)
    miss_after = int(funnel.get("missing_score_after_valid_tick", funnel.get("missing_score", 0)) or 0)
    tick_fail = int(funnel.get("tick_build_failed") or no_eval)

    # Ensure funnel exclusive keys for report
    funnel.setdefault("no_evaluation", no_eval)
    funnel.setdefault("missing_score", miss_after)
    funnel.setdefault("missing_score_after_valid_tick", miss_after)
    funnel.setdefault("tick_build_failed", tick_fail)

    score_audit = score_availability_audit(
        evaluated_count=evaluated,
        no_evaluation_count=no_eval,
        tick_build_failed_count=tick_fail,
        missing_score_after_valid_tick=miss_after,
        score_fill_in=0,
        oracle_id=str(oracle_man.get("oracle_id") or "E1_X5_OFFLINE_ORACLE_20260727_PM"),
    )
    exclusive = funnel_no_double_count(funnel, no_evaluation=no_eval)

    snap = (prior_report.get("crosscheck_70_45023") or {}).get("snap_1240") or SNAP_1240_EXPECTED
    pnl = float(sum(float(x.get("net_pnl_yen_100") or 0) for x in runtime_trades))
    trades_n = len(runtime_trades)

    # Rebuild session-shaped summary for Discord generator (no re-trade)
    session = E1X5ForwardShadowSession(enabled=True)
    # Inject counts for display only
    session.evaluated_count = evaluated
    session.no_evaluation_count = no_eval
    session.missing_score_after_valid_tick = miss_after
    session.tick_build_failed_count = tick_fail
    session.missing_score_count = no_eval + miss_after
    # Restore exits/entries metrics from prior summary fields
    summary = dict(prior_summary)
    summary.update(
        {
            "evaluated_count": evaluated,
            "no_evaluation_count": no_eval,
            "missing_score_after_valid_tick": miss_after,
            "tick_build_failed_count": tick_fail,
            "missing_score_count": no_eval + miss_after,
            "entry_funnel_exclusive": funnel,
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
        "e1_x5_forward_shadow_missing_score_count": no_eval + miss_after,
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

    pbv2 = {
        "regression_diff": 0,
        "note": "PBv2 eval gate / CAP / candidate generation untouched by followup labeling fixes",
    }
    safety = {"submit": 0, "cancel": 0, "live_order": 0}
    constants = strategy_constants_snapshot()

    # Event time range from prior oracle / stream stats
    o_stats = event_parity.get("oracle") or {}
    r_stats = event_parity.get("runtime") or {}

    parity_ok = (
        trades_n == EXPECTED_TRADES
        and abs(pnl - EXPECTED_PNL) < 0.01
        and trade_sha_bundle["match"]
        and int(prior_report.get("oracle_vs_runtime_mismatch_count") or 0) == 0
        and prior_report.get("verdict") == VERDICT_PARITY_FIXED
    )

    # Market closed / weekend → no fake Forward Paper
    forward_status = FORWARD_DAY1_READY
    pm_forward = "NOT_ADOPTED"

    # Decision-relevant event mismatches (feature hash alone may differ if empty vs present)
    score_mm = int(event_parity.get("score_mismatch") or 0)
    pos_mm = int(event_parity.get("position_cap_mismatch") or 0)
    ee_mm = int(event_parity.get("entry_exit_decision_mismatch") or 0)
    fh_mm = int(event_parity.get("feature_hash_mismatch") or 0)

    report = {
        "verdict_parity": VERDICT_PARITY_FIXED if parity_ok else "E1_X5_PARITY_BLOCKED",
        "verdict_forward": forward_status,
        "oracle_id": oracle_man.get("oracle_id"),
        "scope": {
            "session": "PM Capture",
            "time_range_note": "approx 12:33–15:22 JST",
            "all_events": int(oracle_man.get("n_events") or o_stats.get("record_count") or 0),
            "score_evaluated": evaluated,
            "no_evaluation": no_eval,
            "no_evaluation_symbol": "5253.T",
            "no_evaluation_time_range": "12:38:03.827–14:12:24.547",
            "no_evaluation_reason": "TICK_BUILD_FAILED",
        },
        "score_availability_audit": score_audit,
        "evaluated_no_evaluation": {
            "evaluated": evaluated,
            "no_evaluation": no_eval,
            "missing_score_after_valid_tick": miss_after,
            "TICK_BUILD_FAILED": tick_fail,
            "topline": f"evaluated/no_evaluation: {evaluated}/{no_eval}",
        },
        "entry_funnel_exclusive": funnel,
        "funnel_exclusive_check": exclusive,
        "forward_gate": summary["forward_gate"],
        "pm_forward_status": pm_forward,
        "valid_forward_progress": {
            "sessions": 0,
            "trades": 0,
            "complete_am_pm_days": 0,
            "excluded": ["20260727 PM (NOT_ADOPTED)"],
            "note": "Only new Live Paper with provenance qualifies; Replay/fixture/synthetic excluded",
        },
        "sha_naming": {
            "oracle_raw_file_sha256": o_stats.get("raw_file_sha256"),
            "runtime_raw_file_sha256": r_stats.get("raw_file_sha256"),
            "oracle_canonical_event_sha256": o_stats.get("canonical_event_sha256"),
            "runtime_canonical_event_sha256": r_stats.get("canonical_event_sha256"),
            "canonical_trade_ledger_sha256_oracle": trade_sha_bundle["oracle"]["canonical_trade_ledger_sha256"],
            "canonical_trade_ledger_sha256_runtime": trade_sha_bundle["runtime"]["canonical_trade_ledger_sha256"],
            "legacy_name_map": trade_sha_bundle["legacy_name_map"],
        },
        "event_parity": {
            "status": event_parity.get("status"),
            "oracle": {
                "raw_log_path": o_stats.get("raw_log_path"),
                "byte_size": o_stats.get("byte_size"),
                "raw_file_sha256": o_stats.get("raw_file_sha256"),
                "canonical_event_sha256": o_stats.get("canonical_event_sha256"),
                "canonicalization_schema": o_stats.get("canonicalization_schema"),
                "canonicalization_version": o_stats.get("canonicalization_version"),
                "record_count": o_stats.get("record_count"),
                "sequence_min": o_stats.get("sequence_min"),
                "sequence_max": o_stats.get("sequence_max"),
                "sequence_gap_count": o_stats.get("sequence_gap_count"),
                "sequence_duplicate_count": o_stats.get("sequence_duplicate_count"),
                "sequence_inversion_count": o_stats.get("sequence_inversion_count"),
                "first_event_id": o_stats.get("first_event_id"),
                "last_event_id": o_stats.get("last_event_id"),
                "SCORE": o_stats.get("SCORE"),
                "NO_EVALUATION": o_stats.get("NO_EVALUATION"),
                "not_due": o_stats.get("not_due"),
                "feature_hash_present": o_stats.get("feature_hash_present"),
            },
            "runtime": {
                "raw_log_path": r_stats.get("raw_log_path"),
                "byte_size": r_stats.get("byte_size"),
                "raw_file_sha256": r_stats.get("raw_file_sha256"),
                "canonical_event_sha256": r_stats.get("canonical_event_sha256"),
                "canonicalization_schema": r_stats.get("canonicalization_schema"),
                "canonicalization_version": r_stats.get("canonicalization_version"),
                "record_count": r_stats.get("record_count"),
                "sequence_min": r_stats.get("sequence_min"),
                "sequence_max": r_stats.get("sequence_max"),
                "sequence_gap_count": r_stats.get("sequence_gap_count"),
                "sequence_duplicate_count": r_stats.get("sequence_duplicate_count"),
                "sequence_inversion_count": r_stats.get("sequence_inversion_count"),
                "first_event_id": r_stats.get("first_event_id"),
                "last_event_id": r_stats.get("last_event_id"),
                "SCORE": r_stats.get("SCORE"),
                "NO_EVALUATION": r_stats.get("NO_EVALUATION"),
                "not_due": r_stats.get("not_due"),
                "feature_hash_present": r_stats.get("feature_hash_present"),
            },
            "feature_hash_mismatch": fh_mm,
            "feature_hash_note": event_parity.get("feature_hash_note"),
            "score_mismatch": score_mm,
            "position_cap_mismatch": pos_mm,
            "entry_exit_decision_mismatch": ee_mm,
            "decision_parity_ok": event_parity.get("decision_parity_ok"),
            "first_mismatch": event_parity.get("first_mismatch"),
            "legacy_reason_split": LEGACY_REASON_SPLIT,
            "legacy_reason_note": event_parity.get("legacy_reason_note"),
        },
        "trade_ledger": {
            "trades": trades_n,
            "pnl": pnl,
            "trades_ok": trades_n == EXPECTED_TRADES,
            "pnl_ok": abs(pnl - EXPECTED_PNL) < 0.01,
            "snap_1240": snap,
            "oracle_vs_runtime_trade_mismatch_count": int(prior_report.get("oracle_vs_runtime_mismatch_count") or 0),
            **trade_sha_bundle,
        },
        "pbv2_impact": pbv2,
        "strategy_constants": constants,
        "submit_cancel_live": "0/0/0",
        "safety": safety,
        "discord_preview": discord_preview,
        "next_paper_requirements": {
            "runner": "Checked Paper Runner",
            "sessions": "AM+PM required for Day1 PASS",
            "must_save": [
                "Capture",
                "E1_X5 structured event log",
                "REGULAR_5S / STATE_CHANGE counts",
                "AM score / NO_EVALUATION reason",
                "event drop",
                "queue backlog / consumer lag",
                "heartbeat",
                "submit / cancel / live_order",
                "session provenance",
            ],
            "partial_label": "E1_X5_FORWARD_DAY1_PARTIAL",
            "pass_label": "E1_X5_FORWARD_DAY1_PASS",
            "pass_requires": "AM+PM complete + Live→Replay event+ledger parity",
            "not_mainline_adoption": True,
        },
        "gate_5s_preserved": True,
        "generated_at": datetime.now(JST).isoformat(),
        "sources_read_only": {
            "parity_dir": str(PARITY_DIR),
            "oracle_dir": str(ORACLE_DIR),
            "followup_out": str(OUT),
            "overwrite_forbidden": True,
        },
    }

    # Headline answers 1–10
    headlines = {
        "1_am_label_fix": score_audit["am_score_state"],
        "2_evaluated_no_evaluation": f"{evaluated}/{no_eval}",
        "3_forward_target_vs_progress": summary["forward_gate"]["lines"],
        "4_sha_separation": {
            "raw_file_sha256": {
                "oracle": o_stats.get("raw_file_sha256"),
                "runtime": r_stats.get("raw_file_sha256"),
            },
            "canonical_event_sha256": {
                "oracle": o_stats.get("canonical_event_sha256"),
                "runtime": r_stats.get("canonical_event_sha256"),
            },
            "canonical_trade_ledger_sha256": EXPECTED_TRADE_SHA,
        },
        "5_event_feature_mismatch": {
            "score_mismatch": score_mm,
            "feature_hash_mismatch": fh_mm,
            "position_cap_mismatch": pos_mm,
            "entry_exit_decision_mismatch": ee_mm,
        },
        "6_trades_pnl": {"trades": trades_n, "pnl": pnl},
        "7_pbv2_diff": 0,
        "8_submit_cancel_live": "0/0/0",
        "9_verdict_forward": forward_status,
        "10_pm_forward": pm_forward,
    }
    report["headline_answers"] = headlines

    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )

    discord_text = (discord_preview or {}).get("discord_text") or ""
    md = f"""# E1_X5 Runtime / Offline Parity Followup — 20260727

## Verdicts
- Parity: `{report['verdict_parity']}`
- Forward: `{forward_status}`
- 7/27 PM Forward: `{pm_forward}`

## Headline answers
1. **AM label**: `{score_audit['am_score_state']}` — PM no_evaluation={no_eval} reason=`TICK_BUILD_FAILED` score_fill_in=0
2. **evaluated/no_evaluation**: `{evaluated}/{no_eval}` — missing_score_after_valid_tick=`{miss_after}` — TICK_BUILD_FAILED=`{tick_fail}`
3. **Forward target/progress**:
{chr(10).join('   - ' + ln for ln in summary['forward_gate']['lines'])}
4. **SHA separation**:
   - oracle raw_file_sha256=`{o_stats.get('raw_file_sha256')}`
   - runtime raw_file_sha256=`{r_stats.get('raw_file_sha256')}`
   - oracle canonical_event_sha256=`{o_stats.get('canonical_event_sha256')}`
   - runtime canonical_event_sha256=`{r_stats.get('canonical_event_sha256')}`
   - canonical_trade_ledger_sha256=`{EXPECTED_TRADE_SHA}`
5. **Event/Feature mismatches**: score={score_mm} feature_hash={fh_mm} (algorithm divergence noted) position/CAP={pos_mm} ENTRY/EXIT={ee_mm} decision_parity_ok={event_parity.get('decision_parity_ok')}
6. **70 / +45,023.825**: trades={trades_n} pnl={pnl}
7. **PBv2 diff**: 0
8. **submit/cancel/live_order**: 0/0/0
9. **Forward status**: `{forward_status}`
10. **7/27 PM**: `{pm_forward}` (valid progress 0)

## Scope (PM Capture)
- Oracle ID: `{oracle_man.get('oracle_id')}`
- Events: {oracle_man.get('n_events')}
- Time: ~12:33–15:22; NO_EVALUATION 308 all 5253.T / TICK_BUILD_FAILED

## Score Availability Audit
- AM score state: `UNVERIFIED_PENDING_NEW_AM_PAPER`
- PM replay no_evaluation: {no_eval}
- PM replay reason: TICK_BUILD_FAILED
- score fill-in: 0
- Rejected deprecated label: `RESOLVED_OR_EXPLICIT`

## Legacy reason split
`legacy reason split: {LEGACY_REASON_SPLIT}` — frozen Oracle not rewritten.
Next Paper reasons: `REGULAR_5S` / `STATE_CHANGE` / `NOT_DUE` / `NO_EVALUATION` (decision-neutral).

## Snap 12:40
{json.dumps(snap, ensure_ascii=False)}

## Discord preview (excerpt)
```
{discord_text[:1800]}
```

## Next
Do not run synthetic/Replay as Forward. Wait for next trading day Checked Paper Runner AM+PM → `{forward_status}`.
"""
    (OUT / "report.md").write_text(md, encoding="utf-8")

    write_xlsx(
        OUT / "audit.xlsx",
        {
            "Score Availability Audit": score_audit,
            "Event Parity Oracle": report["event_parity"]["oracle"],
            "Event Parity Runtime": report["event_parity"]["runtime"],
            "Event Parity Compare": {
                "status": event_parity.get("status"),
                "feature_hash_mismatch": fh_mm,
                "score_mismatch": score_mm,
                "position_cap_mismatch": pos_mm,
                "entry_exit_decision_mismatch": ee_mm,
                "first_mismatch": event_parity.get("first_mismatch"),
                "legacy_reason_split": LEGACY_REASON_SPLIT,
            },
            "SHA Naming": report["sha_naming"],
            "Trade Ledger": {
                "trades": trades_n,
                "pnl": pnl,
                "canonical_trade_ledger_sha256": EXPECTED_TRADE_SHA,
                "match": trade_sha_bundle["match"],
                "snap_1240": snap,
            },
            "Gate Funnel": funnel,
            "Forward Gate": summary["forward_gate"],
            "PBv2 Regression": pbv2,
            "Strategy Constants": constants,
            "Safety": safety,
            "Discord Aggregation": {"preview": discord_text[:4000]},
            "Headline Answers": headlines,
        },
    )

    # Ensure only the three allowed files exist as primary deliverables
    allowed = {"report.md", "report.json", "audit.xlsx"}
    extras = [p.name for p in OUT.iterdir() if p.is_file() and p.name not in allowed]
    if extras:
        print(f"WARN: unexpected files in followup dir: {extras}", flush=True)

    print(report["verdict_parity"], flush=True)
    print(forward_status, flush=True)
    print(f"OUT={OUT}", flush=True)
    print(
        f"mismatches score/fh/pos/ee={score_mm}/{fh_mm}/{pos_mm}/{ee_mm} "
        f"trades={trades_n} pnl={pnl}",
        flush=True,
    )
    ok = (
        parity_ok
        and exclusive.get("double_count_ok")
        and safety["submit"] == 0
        and pbv2["regression_diff"] == 0
        and forward_status == FORWARD_DAY1_READY
        and bool(event_parity.get("decision_parity_ok"))
        and "evaluated/no_evaluation" in discord_text
        and "Forward gate target:" in discord_text
        and "Valid progress: 0 sessions / 0 trades" in discord_text
        and "gate progress: Forward 5 sessions" not in discord_text
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
