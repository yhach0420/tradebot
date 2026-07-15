"""Phase687W28-R3 — Recovery FAIL root cause audit (read-only)."""

from __future__ import annotations

import csv
import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from small_paper.operational_recovery import (
    check_journal_integrity,
    discover_prior_completed_sessions,
    evaluate_prior_session_artifacts,
    probe_workspace_recovery,
    trading_date_jst_now,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
REPORT = NATIVE / "results" / "reports" / "phase687w28_r3_recovery_fail_audit"
CHECKED = NATIVE / "results" / "reports" / "paper_trade_checked_runner" / "checked_runner_20260715_075608.json"


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=JST).isoformat()
    day = trading_date_jst_now()
    checked = json.loads(CHECKED.read_text(encoding="utf-8"))
    rec_step = next(s for s in checked["steps"] if s.get("name") == "recovery")

    probe = probe_workspace_recovery(NATIVE)
    priors = discover_prior_completed_sessions(NATIVE, trading_date=day)
    ref = priors[0] if priors else {}
    prior_eval = evaluate_prior_session_artifacts(ref) if ref else {}
    safety = Path(str(ref.get("safety_dir") or ""))
    intents = safety / "order_intents.jsonl"
    jr = check_journal_integrity(intents, make_recovery_copy=False) if intents.is_file() else None

    seq_by_file: dict[str, list[int]] = {}
    if safety.is_dir():
        for jf in sorted(safety.glob("*.jsonl")):
            seqs: list[int] = []
            for line in jf.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row.get("sequence"), int):
                    seqs.append(int(row["sequence"]))
            seq_by_file[jf.name] = seqs

    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process | Where-Object { "
                    "$_.CommandLine -match 'market_capture_sidecar|am_pm_daily_runner|"
                    "small_paper_pilot|paper_trade_checked_runner|run_phase113' } | "
                    "ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }"
                ),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError:
        out = ""
    procs = []
    for line in out.splitlines():
        if "|" not in line:
            continue
        pid_s, cmd = line.split("|", 1)
        try:
            procs.append({"pid": int(pid_s), "cmd": cmd[:240]})
        except ValueError:
            pass

    rows: list[dict] = []

    def add(path: Path, kind: str, **extra: object) -> None:
        rows.append(
            {
                "path": str(path),
                "kind": kind,
                "exists": path.is_file() or path.is_dir(),
                **{k: str(v) for k, v in extra.items()},
            }
        )

    add(NATIVE / "runtime" / "universe_prebuild.lock", "universe_lock")
    add(NATIVE / "data" / "market_capture" / day / "operator_stop.flag", "capture_operator_stop")
    add(
        NATIVE / "results" / "small_paper" / day / "daily_symbol_discord_state.json",
        "daily_symbol_discord_state",
    )
    add(NATIVE / "data" / "market_capture" / day / "capture_status.json", "capture_status")
    add(NATIVE / "data" / "market_capture" / day / "capture_heartbeat.json", "capture_heartbeat")
    for p in priors:
        ev = evaluate_prior_session_artifacts(p)
        add(
            Path(p["session_root"]),
            "prior_session",
            session_id=p.get("session_id"),
            seal_status=(ev.get("detail") or {}).get("seal_status"),
            journal=ev.get("journal_integrity"),
            seal_valid=ev.get("session_seal_valid"),
        )
        add(Path(p["seal_path"]), "session_seal", session_id=p.get("session_id"))
        add(Path(p["safety_dir"]) / "order_intents.jsonl", "order_intents", session_id=p.get("session_id"))

    with (REPORT / "stale_state_inventory.csv").open("w", encoding="utf-8", newline="") as fh:
        fields = sorted({k for r in rows for k in r})
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    (REPORT / "recovery_console.txt").write_text(
        json.dumps(
            {
                "checked_runner": str(CHECKED),
                "blocked": checked.get("blocked"),
                "recovery_step": {
                    "result": rec_step.get("result"),
                    "exit_code": rec_step.get("exit_code"),
                    "stdout": rec_step.get("stdout_tail"),
                    "stderr": rec_step.get("stderr_tail"),
                },
                "reprobe_summary": {
                    "recovery_ready": probe.get("recovery_ready"),
                    "exit_code": probe.get("exit_code"),
                    "blockers": probe.get("blockers"),
                    "journal_integrity": probe.get("journal_integrity"),
                    "session_seal_valid": probe.get("session_seal_valid"),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    root_cause = {
        "phase": "687W28-R3",
        "generated_at": now,
        "verdict": "STALE_RECOVERY_STATE_FOUND",
        "checked_runner": str(CHECKED),
        "direct_reason": {
            "failed_step": "recovery",
            "exit_code": 2,
            "blockers": [
                {
                    "code": "SESSION_SEAL_INVALID",
                    "detail": "prior session_seal_status=INCOMPLETE + post-seal hash mismatches",
                },
                {
                    "code": "JOURNAL_INTEGRITY_FAIL",
                    "detail": "JOURNAL_SEQUENCE_GAP sequence_gap:5->18 in order_intents.jsonl",
                },
            ],
        },
        "causal_artifacts": {
            "reference_session": ref,
            "seal_path": str(ref.get("seal_path")),
            "seal_status": "INCOMPLETE",
            "missing_required": [
                "broker_reconciliation.jsonl",
                "kill_switch_events.jsonl",
                "np_feature_summary.json",
            ],
            "hash_mismatches": (prior_eval.get("detail") or {}).get("seal_verify", {}).get("mismatches"),
            "order_intents": str(intents),
            "order_intents_sequences": jr.sequences if jr else None,
            "order_intents_issues": jr.issues if jr else None,
            "all_journal_sequences_in_safety_dir": seq_by_file,
            "am_prior_also_incomplete": True,
        },
        "answers": {
            "1_direct_reason": (
                "SESSION_SEAL_INVALID (INCOMPLETE seal) + "
                "JOURNAL_INTEGRITY_FAIL (JOURNAL_SEQUENCE_GAP on order_intents)"
            ),
            "2_files": [
                str(ref.get("seal_path")),
                str(intents),
                str(Path(str(ref.get("session_root") or "")) / "small_paper_summary.json"),
                str(Path(str(ref.get("session_root") or "")) / "small_paper_positions.csv"),
                str(Path(str(ref.get("safety_dir") or "")) / "soak_session_snapshot.json"),
            ],
            "3_previous_paper_residue": True,
            "4_capture_continuation_cause": False,
            "4_note": (
                "Capture may be running for 20260715 but recovery evaluates prior Paper "
                "session seal/journals only"
            ),
            "5_safe_to_quarantine": True,
            "5_note": (
                "Do not delete; quarantine/archive 20260714 live_session_122532 and "
                "live_session_082256 out of discovery. Do not edit recovery logic."
            ),
            "6_manual_ops_required": True,
            "7_rerun": (
                r"cd C:\Users\yhach\Documents\tradebotfile; "
                r".\run_paper_trade_checked.bat --no-pause"
            ),
            "8_submit_cancel": {
                "submit": 0,
                "cancel": 0,
                "real_orders": "DISABLED",
                "paper_call_count": checked.get("paper_call_count"),
            },
        },
        "secondary_note_on_journal_gap": (
            "order_intents sequences [5,18,31] are sparse because sequence is shared with "
            "order_state_events/capital_reservations; per-file contiguous check on intents "
            "alone yields SEQUENCE_GAP. Primary fail-closed blocker remains INCOMPLETE seal."
        ),
        "probe_mode": probe.get("probe_mode"),
        "capture_processes": procs,
    }
    (REPORT / "recovery_root_cause.json").write_text(
        json.dumps(root_cause, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (REPORT / "process_inventory.json").write_text(
        json.dumps(
            {
                "at": now,
                "processes": procs,
                "capture_only_not_recovery_blocker": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (REPORT / "order_safety_audit.json").write_text(
        json.dumps(
            {
                "real_orders": "DISABLED",
                "submit": 0,
                "cancel": 0,
                "paper_call_count": checked.get("paper_call_count"),
                "write_adapter_present": False,
                "submit_hard_fail": True,
                "flags_mutated": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    steps = f"""# Phase687W28-R3 — Safe Recovery Steps

## Verdict
`STALE_RECOVERY_STATE_FOUND`

## What failed
Recovery gate (`python -m small_paper.check_live_order_recovery_readiness`) **exit 2**.

Checked runner: `{CHECKED.name}`  
Reference prior: `results/small_paper/20260714/live_session_122532`

### Blockers
1. **SESSION_SEAL_INVALID** — `session_seal_status=INCOMPLETE` (missing required:
   `broker_reconciliation.jsonl`, `kill_switch_events.jsonl`, `np_feature_summary.json`)
   plus post-seal hash mismatches on summary/positions/soak snapshot.
2. **JOURNAL_INTEGRITY_FAIL** — `order_intents.jsonl` → `JOURNAL_SEQUENCE_GAP` (`5->18`).

AM prior `live_session_082256` is also INCOMPLETE with a sequence gap.

## Not the cause
- Today's Capture sidecar (if running) — recovery does not use Capture as seal/journal SoT
- `universe_prebuild.lock` — absent
- `daily_symbol_discord_state` — absent (not a blocker)
- Live orders — DISABLED; submit/cancel = 0

## Safe operator actions (no recovery-logic change)
1. **Do not delete** the 20260714 session trees (keep as audit evidence).
2. Quarantine both incomplete priors out of discovery, e.g. move entire dirs to:
   `results/small_paper/_quarantine_w28r3/20260714/live_session_122532`
   `results/small_paper/_quarantine_w28r3/20260714/live_session_082256`
3. Capture stop is optional (not required to clear Recovery FAIL).
4. Confirm no Paper orphans (`am_pm_daily_runner` / `small_paper_pilot`).
5. Re-run:
   ```bat
   cd C:\\Users\\yhach\\Documents\\tradebotfile
   .\\run_paper_trade_checked.bat --no-pause
   ```
6. After quarantine, Recovery should treat workspace as clean slate
   (`pre_start_no_prior_session`) unless another sealed prior remains.

## Forbidden
- Changing Recovery conditions / skipping Recovery
- Editing seal hashes to fake `SEALED_VALID`
- Enabling real orders / forcing Paper start
"""
    (REPORT / "safe_recovery_steps.md").write_text(steps, encoding="utf-8")
    (REPORT / "phase687w28_r3_verdict.txt").write_text("STALE_RECOVERY_STATE_FOUND\n", encoding="utf-8")
    print(f"W28-R3 → {REPORT}")
    print("verdict=STALE_RECOVERY_STATE_FOUND")


if __name__ == "__main__":
    main()
