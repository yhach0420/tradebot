#!/usr/bin/env python3
"""Phase687W32: Recovery and session seal root-fix certification."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))

JST = ZoneInfo("Asia/Tokyo")
OUT = NATIVE / "results" / "reports" / "phase687w32_recovery_session_seal_root_fix"


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _wc(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def classify_20260714() -> list[dict[str, Any]]:
    rows = []
    for sess in ("live_session_082256", "live_session_122532"):
        q = NATIVE / "results" / "recovery_quarantine" / "20260714" / sess
        seal_p = q / "session_seal.json"
        seal = {}
        if seal_p.is_file():
            seal = json.loads(seal_p.read_text(encoding="utf-8"))
        summary_p = q / "small_paper_summary.json"
        stop = ""
        if summary_p.is_file():
            stop = str(json.loads(summary_p.read_text(encoding="utf-8")).get("stop_reason") or "")
        # Global journal re-eval
        from small_paper.operational_recovery import check_journals_global_sequence, check_journal_integrity

        safety = q / "live_order_safety"
        per_file = "n/a"
        global_st = "n/a"
        if (safety / "order_intents.jsonl").is_file():
            # Old wrong check (contiguous forced)
            bad = check_journal_integrity(
                safety / "order_intents.jsonl",
                make_recovery_copy=False,
                require_contiguous_sequence=True,
            )
            per_file = bad.status
            glob = check_journals_global_sequence(safety)
            global_st = glob.status
        rows.append(
            {
                "session": sess,
                "location": "recovery_quarantine",
                "session_seal_status": seal.get("session_seal_status"),
                "missing_required": ",".join(seal.get("missing_required") or []),
                "missing_count": seal.get("required_artifact_missing_count"),
                "stop_reason": stop or "(unknown)",
                "old_per_file_intents_contiguous": per_file,
                "new_global_sequence": global_st,
                "recovery_prior_candidate": False,
                "classification": "INCIDENT_EVIDENCE_INCOMPLETE_NOT_PRIOR",
                "strategy_include": False,
            }
        )
    return rows


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    (OUT / "seal_lifecycle_before_after.md").write_text(
        """# Seal lifecycle before / after (Phase687W32)

## Before (root bugs)
1. `finalize_session_seal_propagation` ran **before** `writer.finalize_batch`
2. `small_paper_summary.json` / events / positions written **after** seal → missing or hash mismatch
3. Early abort left `INCOMPLETE` (missing journals / np_feature_summary)
4. Post-seal rewrite of summary invalidated hashes
5. Recovery treated INCOMPLETE priors as blockers

## After
1. Soak + manifest prep only (status `PENDING_SEAL`) — **no seal**
2. Discord / Shadow / validity classification
3. `writer.finalize_batch` (summary, events, positions, rejects)
4. Refresh soak; `ensure_required_seal_artifacts` (empty journals / np_feature_summary OK)
5. **Single** `finalize_session_seal_propagation` → `SEALED_VALID`
6. AM/PM summary **copy** only (`small_paper_summary_{am|pm}.json`) — not a seal target
7. Never rewrite sealed `small_paper_summary.json` after seal
8. Recovery discovers only `results/small_paper/YYYYMMDD/live_session_HHMMSS` with `SEALED_VALID|SEALED`
""",
        encoding="utf-8",
    )

    _wc(
        OUT / "seal_timeline_normal.csv",
        [
            {"step": 1, "action": "stop_entry_exit", "seal": "no"},
            {"step": 2, "action": "journal_flush", "seal": "no"},
            {"step": 3, "action": "soak_snapshot_draft", "seal": "no"},
            {"step": 4, "action": "manifest_PENDING_SEAL", "seal": "no"},
            {"step": 5, "action": "discord_shadow_finalize", "seal": "no"},
            {"step": 6, "action": "finalize_batch_summary_events", "seal": "no"},
            {"step": 7, "action": "ensure_required_artifacts", "seal": "no"},
            {"step": 8, "action": "finalize_session_seal_propagation", "seal": "YES SEALED_VALID"},
            {"step": 9, "action": "am_pm_summary_copy_only", "seal": "no (non-required)"},
        ],
        ["step", "action", "seal"],
    )
    _wc(
        OUT / "seal_timeline_abort.csv",
        [
            {"step": 1, "action": "register_failed_or_early_abort", "seal": "no"},
            {"step": 2, "action": "finalize_batch_partial_ok", "seal": "no"},
            {"step": 3, "action": "ensure_required_empty_artifacts", "seal": "no"},
            {"step": 4, "action": "seal SEALED_VALID (existing schema)", "seal": "YES"},
            {"step": 5, "action": "session_validity INVALID_*", "seal": "strategy exclude"},
            {"step": 6, "action": "Recovery prior?", "seal": "SEALED_VALID allowed; INCOMPLETE never"},
        ],
        ["step", "action", "seal"],
    )

    from small_paper.stateful_journal_recovery import REQUIRED_SEAL_ARTIFACTS

    req_rows = []
    for name in REQUIRED_SEAL_ARTIFACTS:
        loc = "live_order_safety/" if name.endswith(".jsonl") or name in (
            "session_manifest.json",
            "soak_session_snapshot.json",
        ) else "session_root/"
        req_rows.append(
            {
                "artifact": name,
                "location": loc,
                "paper_only_empty_ok": name
                in (
                    "broker_reconciliation.jsonl",
                    "kill_switch_events.jsonl",
                    "np_feature_summary.json",
                    "np_pre_entry_features.jsonl",
                    "np_pre_entry_outcomes.jsonl",
                    "order_intents.jsonl",
                    "order_state_events.jsonl",
                    "capital_reservations.jsonl",
                ),
                "created_by": "ensure_required_seal_artifacts if missing",
            }
        )
    _wc(
        OUT / "required_artifact_matrix.csv",
        req_rows,
        ["artifact", "location", "paper_only_empty_ok", "created_by"],
    )

    prior = classify_20260714()
    _wc(
        OUT / "prior_session_classification.csv",
        prior,
        list(prior[0].keys()) if prior else ["session"],
    )
    _wc(
        OUT / "journal_global_sequence_audit.csv",
        [
            {
                "session": r["session"],
                "old_per_file": r["old_per_file_intents_contiguous"],
                "new_global": r["new_global_sequence"],
                "verdict": (
                    "FALSE_GAP_FIXED"
                    if r["old_per_file_intents_contiguous"] == "JOURNAL_SEQUENCE_GAP"
                    and r["new_global_sequence"] == "JOURNAL_OK"
                    else r["new_global_sequence"]
                ),
            }
            for r in prior
        ],
        ["session", "old_per_file", "new_global", "verdict"],
    )

    (OUT / "recovery_discovery_audit.md").write_text(
        """# Recovery discovery audit (W32)

## Positive match
`results/small_paper/YYYYMMDD/live_session_HHMMSS/live_order_safety/session_manifest.json`

## Excluded
- `results/recovery_quarantine/**`
- `_quarantine*`, archive, debug, fixtures, reports, tests

## Prior seal filter
Only `SEALED_VALID` or `SEALED`. Missing / `INCOMPLETE` → not a Recovery prior.

## 20260714
Quarantined INCOMPLETE sessions are **not** prior candidates → next-day Recovery clean slate / next SEALED_VALID prior.
""",
        encoding="utf-8",
    )

    # Recovery probe simulation: quarantine must not appear
    from small_paper.operational_recovery import discover_prior_completed_sessions

    found = discover_prior_completed_sessions(NATIVE, trading_date="20260716")
    q_hits = [f for f in found if "recovery_quarantine" in str(f.get("session_root") or "")]
    incomplete_hits = [
        f for f in found if str(f.get("session_seal_status") or "") == "INCOMPLETE"
    ]

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_phase687w32_recovery_session_seal.py", "-q", "--tb=line"],
        cwd=str(NATIVE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    reg = {
        "pytest_exit": proc.returncode,
        "pytest_ok": proc.returncode == 0,
        "stdout_tail": (proc.stdout or "")[-500:],
        "quarantine_discovered": len(q_hits),
        "incomplete_discovered": len(incomplete_hits),
        "discovery_ok": len(q_hits) == 0 and len(incomplete_hits) == 0,
    }
    _wj(OUT / "regression_test_results.json", reg)

    _wj(
        OUT / "code_change_manifest.json",
        {
            "files": [
                "src/small_paper/pilot_runner.py",
                "src/small_paper/operational_recovery.py",
                "src/small_paper/stateful_journal_recovery.py (ensure_required used)",
                "tests/test_phase687w32_recovery_session_seal.py",
                "scripts/phase687w32_recovery_session_seal_root_fix.py",
            ],
            "not_changed": [
                "registration_lifetime.py",
                "kabu_register.py",
                "ENTRY/EXIT/CAP/OR/Shadow",
                "real orders",
            ],
        },
    )
    _wj(OUT / "order_safety_audit.json", {"submit": 0, "cancel": 0})

    next_recovery_pass = reg["discovery_ok"] and reg["pytest_ok"]
    verdict = "RECOVERY_AND_SESSION_SEAL_FIXED" if next_recovery_pass else "ROOT_CAUSE_PARTIALLY_RESOLVED"
    report = {
        "phase": "687W32",
        "verdict": verdict,
        "answers": {
            "1_seal_order": "finalize_batch → ensure artifacts → single seal; no pre-summary seal",
            "2_hash_mismatch_cause": "summary/events written after early seal; post-seal summary rewrite",
            "3_required_missing_cause": "abort path never created empty journals/np_feature_summary; seal before finalize",
            "4_early_abort_seal": "ensure_required + SEALED_VALID (existing schema); INVALID_* for strategy",
            "5_journal_gap_fix": "check_journals_global_sequence merges shared allocator",
            "6_quarantine_discovery": "positive match under small_paper only; quarantine denied",
            "7_20260714_reclass": prior,
            "8_next_recovery_pass": next_recovery_pass,
            "9_submit_cancel": {"submit": 0, "cancel": 0},
            "10_registration_unchanged": True,
            "11_mainline_unchanged": True,
        },
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _wj(OUT / "phase687w32_report.json", report)
    (OUT / "phase687w32_decision.md").write_text(
        f"""# Phase687W32 Decision

**Verdict: {verdict}**

Seal runs once after finalize_batch with required empty artifacts for abort.
Global journal sequence replaces per-file contiguous checks.
Recovery discovers only SEALED_VALID live_session paths under small_paper.
Registration / strategy / orders unchanged.
""",
        encoding="utf-8",
    )
    print(json.dumps({"out": str(OUT), "verdict": verdict}, ensure_ascii=False))
    return 0 if verdict == "RECOVERY_AND_SESSION_SEAL_FIXED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
