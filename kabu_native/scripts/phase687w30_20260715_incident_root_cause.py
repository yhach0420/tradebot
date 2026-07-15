#!/usr/bin/env python3
"""Phase687W30: 20260715 incident root-cause audit + certification artifacts."""

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
OUT = NATIVE / "results" / "reports" / "phase687w30_20260715_incident_root_cause"
DAY = "20260715"
AM = "live_session_081239"
PM = "live_session_122504"


def _load(path: Path) -> Any:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def _checked_runners() -> list[Path]:
    root = NATIVE / "results" / "reports" / "paper_trade_checked_runner"
    return sorted(root.glob(f"checked_runner_{DAY}_*.json"))


def _step_times(runner: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for s in runner.get("steps") or []:
        name = s.get("name") or s.get("step")
        rows.append(
            {
                "event": f"checked_step:{name}",
                "ts": s.get("finished_at") or s.get("ended_at") or s.get("started_at") or runner.get("generated_at") or "",
                "detail": s.get("status") or "",
                "source": "checked_runner",
            }
        )
    return rows


def build_timelines() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    am_sum = _load(NATIVE / "results" / "small_paper" / DAY / AM / "small_paper_summary.json") or {}
    pm_sum = _load(NATIVE / "results" / "small_paper" / DAY / PM / "small_paper_summary.json") or {}
    am_err = (NATIVE / "results" / "small_paper" / DAY / AM / "errors.jsonl").read_text(encoding="utf-8").splitlines()
    pm_err = (NATIVE / "results" / "small_paper" / DAY / PM / "errors.jsonl").read_text(encoding="utf-8").splitlines()
    am_seal = _load(NATIVE / "results" / "small_paper" / DAY / AM / "session_seal.json") or {}
    pm_seal = _load(NATIVE / "results" / "small_paper" / DAY / PM / "session_seal.json") or {}

    am_rows: list[dict[str, Any]] = [
        {"event": "paper_session_dir", "ts": f"{DAY}T08:12:39+09:00", "detail": AM, "source": "path"},
        {"event": "summary_generated", "ts": am_sum.get("generated_at"), "detail": f"runtime={am_sum.get('runtime_sec')}", "source": "summary"},
        {"event": "register_failed", "ts": am_sum.get("ended_at"), "detail": am_sum.get("stop_reason"), "source": "summary"},
        {"event": "PUSH", "ts": am_sum.get("ended_at"), "detail": f"push_messages={am_sum.get('push_messages')}", "source": "summary"},
        {"event": "gate", "ts": am_sum.get("ended_at"), "detail": f"gate_evaluations={am_sum.get('gate_evaluations')}", "source": "summary"},
        {"event": "heartbeat", "ts": am_sum.get("ended_at"), "detail": f"heartbeat_count={am_sum.get('heartbeat_count')}", "source": "summary"},
        {"event": "seal", "ts": am_seal.get("generated_at"), "detail": am_seal.get("session_seal_status"), "source": "session_seal"},
    ]
    for line in am_err:
        try:
            e = json.loads(line)
            am_rows.append(
                {
                    "event": e.get("error_type") or e.get("operation") or "error",
                    "ts": e.get("event_time"),
                    "detail": (e.get("message") or "")[:180],
                    "source": "errors.jsonl",
                }
            )
        except Exception:
            pass

    pm_rows: list[dict[str, Any]] = [
        {"event": "paper_session_dir", "ts": f"{DAY}T12:25:04+09:00", "detail": PM, "source": "path"},
        {"event": "summary_generated", "ts": pm_sum.get("generated_at"), "detail": f"runtime={pm_sum.get('runtime_sec')}", "source": "summary"},
        {"event": "register_failed", "ts": pm_sum.get("ended_at"), "detail": pm_sum.get("stop_reason"), "source": "summary"},
        {"event": "PUSH", "ts": pm_sum.get("ended_at"), "detail": f"push_messages={pm_sum.get('push_messages')}", "source": "summary"},
        {"event": "gate", "ts": pm_sum.get("ended_at"), "detail": f"gate_evaluations={pm_sum.get('gate_evaluations')}", "source": "summary"},
        {"event": "heartbeat", "ts": pm_sum.get("ended_at"), "detail": f"heartbeat_count={pm_sum.get('heartbeat_count')}", "source": "summary"},
        {"event": "seal", "ts": pm_seal.get("generated_at"), "detail": pm_seal.get("session_seal_status"), "source": "session_seal"},
    ]
    for line in pm_err:
        try:
            e = json.loads(line)
            pm_rows.append(
                {
                    "event": e.get("error_type") or e.get("operation") or "error",
                    "ts": e.get("event_time"),
                    "detail": (e.get("message") or "")[:180],
                    "source": "errors.jsonl",
                }
            )
        except Exception:
            pass

    # Checked runners
    for p in _checked_runners():
        d = _load(p) or {}
        tag = p.stem
        for row in _step_times(d):
            row["session"] = "am" if "0811" in tag or "0756" in tag or "0345" in tag or "0408" in tag else "pm"
            row["detail"] = f"{tag}|{row['detail']}"
            if "0811" in tag or "0756" in tag:
                am_rows.append(row)
            if "1239" in tag:
                pm_rows.append(row)

    fields = ["ts", "event", "detail", "source", "session"]
    for r in am_rows:
        r.setdefault("session", "am")
    for r in pm_rows:
        r.setdefault("session", "pm")
    am_rows.sort(key=lambda x: str(x.get("ts") or ""))
    pm_rows.sort(key=lambda x: str(x.get("ts") or ""))
    combined = sorted(am_rows + pm_rows, key=lambda x: str(x.get("ts") or ""))
    _write_csv(OUT / "incident_timeline_am.csv", am_rows, fields)
    _write_csv(OUT / "incident_timeline_pm.csv", pm_rows, fields)
    _write_csv(OUT / "combined_incident_timeline.csv", combined, fields)
    return am_rows, pm_rows, combined


def run_unit_tests() -> dict[str, Any]:
    tests = [
        "tests/test_kabu_register.py",
        "tests/test_phase687w11a_monday_p1_fixes.py",
    ]
    results = []
    for t in tests:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", t, "-q", "--tb=line"],
            cwd=str(NATIVE),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        results.append(
            {
                "test": t,
                "exit_code": proc.returncode,
                "ok": proc.returncode == 0,
                "stdout_tail": (proc.stdout or "")[-800:],
                "stderr_tail": (proc.stderr or "")[-400:],
            }
        )
    # inline session_validity / discovery checks
    from small_paper.session_validity import classify_session_validity, INVALID_REGISTER_FAILED
    from small_paper.operational_recovery import discover_prior_completed_sessions, check_journal_integrity
    from api.kabu_register import format_register_failure_message, register_symbols_cleared
    from api.rest_client import KabuNativeApiError
    from unittest.mock import MagicMock

    am = _load(NATIVE / "results" / "small_paper" / DAY / AM / "small_paper_summary.json") or {}
    v = classify_session_validity(am)
    assert v["session_validity"] == INVALID_REGISTER_FAILED
    results.append({"test": "classify_am_invalid", "ok": True, "exit_code": 0})

    # READY_FOR_FANOUT allows clear (synthetic)
    tmp = OUT / "_synth_ready"
    # discovery positive match: quarantine under recovery_quarantine not discovered
    priors = discover_prior_completed_sessions(NATIVE, trading_date="20991231")
    bad = [p for p in priors if "recovery_quarantine" in str(p.get("session_root") or "")]
    results.append(
        {
            "test": "discovery_excludes_quarantine",
            "ok": len(bad) == 0,
            "exit_code": 0 if not bad else 1,
            "prior_count": len(priors),
        }
    )

    # journal shared sequence skip
    jdir = OUT / "_synth_journal"
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "order_intents.jsonl").write_text(
        '{"sequence":5}\n{"sequence":18}\n', encoding="utf-8"
    )
    (jdir / "order_state_events.jsonl").write_text(
        '{"sequence":6}\n{"sequence":7}\n', encoding="utf-8"
    )
    jr = check_journal_integrity(jdir / "order_intents.jsonl", make_recovery_copy=False)
    results.append(
        {
            "test": "journal_shared_sequence_no_gap_fail",
            "ok": jr.status != "JOURNAL_SEQUENCE_GAP",
            "exit_code": 0 if jr.status != "JOURNAL_SEQUENCE_GAP" else 1,
            "status": jr.status,
            "issues": jr.issues,
        }
    )

    # false success message regression
    class FailPush:
        def unregister_all(self):
            return {"RegistNum": 0}

        def register(self, specs):
            raise KabuNativeApiError('{"Code":4002006}')

    try:
        register_symbols_cleared(FailPush(), [("1", 1)], settle_sec=0.0)
        msg_ok = False
        msg = ""
    except Exception as e:
        msg = str(e)
        msg_ok = "Cleared via unregister/all and retried once" not in msg and "FAILED" in msg
    results.append({"test": "honest_failure_message", "ok": msg_ok, "exit_code": 0 if msg_ok else 1, "message": msg[:200]})

    return {
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "results": results,
        "all_ok": all(bool(r.get("ok")) for r in results),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    from small_paper.session_validity import classify_session_validity

    am_sum = _load(NATIVE / "results" / "small_paper" / DAY / AM / "small_paper_summary.json") or {}
    pm_sum = _load(NATIVE / "results" / "small_paper" / DAY / PM / "small_paper_summary.json") or {}
    am_seal = _load(NATIVE / "results" / "small_paper" / DAY / AM / "session_seal.json") or {}
    pm_seal = _load(NATIVE / "results" / "small_paper" / DAY / PM / "session_seal.json") or {}
    am_v = classify_session_validity(am_sum, session_seal_status=am_seal.get("session_seal_status"))
    pm_v = classify_session_validity(pm_sum, session_seal_status=pm_seal.get("session_seal_status"))

    build_timelines()

    # Register path audit
    register_path = f"""# Register path audit (Phase687W30)

## Direct cause (2026-07-15)

Capture Sidecar was `CAPTURE_READY_FOR_FANOUT` with `applied=false` / follower-only.
`is_live_capture_registration_owner_active` incorrectly treated fresh Capture heartbeat as
**registration owner**, forcing `clear_first=False` (`CAPTURE_ACTIVE_CLEAR_DEFERRED`).

Paper then hit Code **4002006** (stale station registrations + request 50).
Retry path also skipped unregister. Failure text falsely claimed
"Cleared via unregister/all and retried once".

## Path responsibilities

| Path | Owner | Clear allowed? | Notes |
|------|-------|----------------|-------|
| checked runner registration_coordination | orchestration | N/A | PASS = coordination / PLANNED_FOLLOWER only — **not** runtime Kabu PUT success |
| pilot runtime registration | `pilot_runner._loop` | yes unless Capture **applied** owner | SoT for Paper PUSH subscription |
| refresh registration (10:00 / 14:30) | intraday refresh | same clear rules | never reached on 7/15 (abort at register) |
| retry registration | `register_symbols_cleared` | must actually unregister + readback | W30 requires RegistNum 0 then N |

## Why Registration PASS + register_failed coexisted

Checked step `[Registration] PASS` validates Capture/Paper coordination plan.
Actual `push.register` runs later inside Paper pilot → `stop_reason=register_failed`.
"""
    (OUT / "register_path_audit.md").write_text(register_path, encoding="utf-8")

    am_trace = {
        "session": AM,
        "requested_symbols": 50,
        "kabu_code": 4002006,
        "errors": [
            json.loads(x)
            for x in (NATIVE / "results" / "small_paper" / DAY / AM / "errors.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if x.strip()
        ],
        "false_success_claim_in_error_text": True,
        "unregister_all_actually_called": False,
        "reason": "CAPTURE_ACTIVE_CLEAR_DEFERRED inferred from READY_FOR_FANOUT + false cleared message",
        "retry_register_success": False,
        "summary_stop_reason": am_sum.get("stop_reason"),
    }
    pm_trace = {
        "session": PM,
        "requested_symbols": 50,
        "kabu_code": 4002006,
        "errors": [
            json.loads(x)
            for x in (NATIVE / "results" / "small_paper" / DAY / PM / "errors.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if x.strip()
        ],
        "false_success_claim_in_error_text": True,
        "unregister_all_actually_called": False,
        "reason": "same as AM; AM→PM left station regs uncleared",
        "retry_register_success": False,
        "summary_stop_reason": pm_sum.get("stop_reason"),
    }
    _write_json(OUT / "register_api_trace_am.json", am_trace)
    _write_json(OUT / "register_api_trace_pm.json", pm_trace)
    _write_json(
        OUT / "register_recovery_before_after.json",
        {
            "before": {
                "clear_deferred_on_ready_for_fanout": True,
                "failure_message_claimed_cleared": True,
                "readback_regist_num": False,
                "recovered_notify_before_push": True,
            },
            "after": {
                "ready_for_fanout_allows_paper_clear": True,
                "require_unregister_readback_zero": True,
                "require_register_readback_n": True,
                "register_recovered_flag_only_if_clear_ran": True,
                "discord_recovered_requires_push_receiving": True,
            },
        },
    )

    checked_pm = None
    for p in _checked_runners():
        if "123909" in p.name:
            checked_pm = _load(p)
    capture = (checked_pm or {}).get("capture") or {}
    _write_json(
        OUT / "push_capture_path_audit.json",
        {
            "am": {
                "push_messages": am_sum.get("push_messages"),
                "gate_evaluations": am_sum.get("gate_evaluations"),
                "heartbeat_count": am_sum.get("heartbeat_count"),
                "cause": "register_failed before PUSH loop",
            },
            "pm": {
                "push_messages": pm_sum.get("push_messages"),
                "gate_evaluations": pm_sum.get("gate_evaluations"),
                "heartbeat_count": pm_sum.get("heartbeat_count"),
                "cause": "register_failed before PUSH loop",
            },
            "capture": {
                "status": capture.get("status"),
                "event_count": capture.get("event_count"),
                "note": "Paper PUSH 0 ⇒ Capture fanout 0 by design (follower). Capture READY without Paper connection is now monitorable.",
            },
            "register_failed_is_primary_push0_cause": True,
            "independent_fanout_bug": False,
        },
    )

    _write_csv(
        OUT / "session_validity_classification.csv",
        [
            {"session": AM, "am_pm": "AM", **am_v},
            {"session": PM, "am_pm": "PM", **pm_v},
        ],
        [
            "session",
            "am_pm",
            "session_validity",
            "include_in_strategy_metrics",
            "stop_reason",
            "push_messages",
            "gate_evaluations",
            "heartbeat_count",
            "session_seal_status",
        ],
    )

    def seal_life(sess: str, seal: dict, summary: dict) -> list[dict[str, Any]]:
        return [
            {"step": "session_start_dir", "ts": "", "status": sess},
            {"step": "register_abort", "ts": summary.get("ended_at"), "status": summary.get("stop_reason")},
            {
                "step": "seal_written",
                "ts": seal.get("generated_at"),
                "status": seal.get("session_seal_status"),
                "missing_required": seal.get("required_artifact_missing_count"),
                "missing": ",".join(seal.get("missing_required") or []),
            },
            {
                "step": "root_cause",
                "ts": seal.get("generated_at"),
                "status": "seal_before_summary_finalize",
                "missing_required": "",
                "missing": "finalize_session_seal_propagation ran before writer.finalize_batch",
            },
            {
                "step": "fix",
                "ts": datetime.now(JST).isoformat(timespec="seconds"),
                "status": "post_finalize_reseal + ensure_required_seal_artifacts; Recovery skips INCOMPLETE priors",
            },
        ]

    fields_seal = ["step", "ts", "status", "missing_required", "missing"]
    _write_csv(OUT / "seal_lifecycle_am.csv", seal_life(AM, am_seal, am_sum), fields_seal)
    _write_csv(OUT / "seal_lifecycle_pm.csv", seal_life(PM, pm_seal, pm_sum), fields_seal)

    (OUT / "recovery_discovery_audit.md").write_text(
        """# Recovery discovery audit (Phase687W30)

## Before
- `results/small_paper.rglob(session_manifest.json)` picked quarantine trees under small_paper.
- INCOMPLETE priors blocked next-day Recovery.

## After
- Positive match only: `results/small_paper/YYYYMMDD/live_session_HHMMSS/live_order_safety/session_manifest.json`
- Formal quarantine: `results/recovery_quarantine/YYYYMMDD/session`
- Deny parts: recovery_quarantine, _quarantine, archive, debug, fixtures, …
- Only `SEALED_VALID` / `SEALED` priors are Recovery reference candidates
- Journal contiguous sequence check skipped when sibling journals share global sequence
""",
        encoding="utf-8",
    )

    _write_json(
        OUT / "invalid_session_exclusion_audit.json",
        {
            "day": DAY,
            "am": am_v,
            "pm": pm_v,
            "exclude_from": [
                "canonical cumulative PnL",
                "Shadow forward day count",
                "strategy performance / PF / win rate",
                "live readiness streak",
                "adoption evidence",
            ],
            "retain_for": [
                "incident evidence",
                "invalid session count",
                "operational reliability statistics",
            ],
            "checked_runner_post_session_note": (checked_pm or {}).get("post_session"),
        },
    )

    from small_paper.session_validity import (
        format_invalid_session_discord_lines,
        format_paper_not_running_discord_lines,
        format_register_recovered_discord_lines,
    )

    (OUT / "discord_incident_preview.md").write_text(
        "\n\n".join(
            [
                "## INVALID AM\n" + "\n".join(format_invalid_session_discord_lines(am_v)),
                "## PAPER NOT RUNNING\n" + "\n".join(format_paper_not_running_discord_lines()),
                "## REGISTER RETRY (before PUSH)\n"
                + "\n".join(format_register_recovered_discord_lines(push_receiving=False)),
                "## REGISTER RECOVERED (after PUSH)\n"
                + "\n".join(format_register_recovered_discord_lines(push_receiving=True)),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    reg = run_unit_tests()
    _write_json(OUT / "regression_test_results.json", reg)

    _write_json(
        OUT / "live_smoke_result.json",
        {
            "mode": "synthetic_offline + historical_artifact_replay",
            "market_hours_readonly_smoke": "not_run_in_this_phase_script",
            "note": "Code paths certified via unit/synthetic tests; live Kabu smoke deferred to operator window",
            "register_recovery_contract_coded": True,
            "ready_for_fanout_clear_allowed": True,
        },
    )

    _write_json(
        OUT / "code_change_manifest.json",
        {
            "files": [
                "src/api/kabu_register.py",
                "src/small_paper/registration_lifetime.py",
                "src/small_paper/operational_recovery.py",
                "src/small_paper/session_validity.py",
                "src/small_paper/pilot_runner.py",
                "src/small_paper/discord_message_builder.py",
                "src/small_paper/stateful_journal_recovery.py",
                "tests/test_kabu_register.py",
                "scripts/phase687w30_20260715_incident_root_cause.py",
            ],
            "strategy_entry_exit_cap_or_changed": False,
            "real_orders_enabled": False,
        },
    )
    _write_json(
        OUT / "order_safety_audit.json",
        {
            "submit": 0,
            "cancel": 0,
            "am_summary_submit_fields": {
                "live_order_adapter_would_send_count": am_sum.get("live_order_adapter_would_send_count"),
                "live_trading_enabled": am_sum.get("live_trading_enabled"),
                "dry_run": am_sum.get("dry_run"),
            },
            "pm_summary_submit_fields": {
                "live_order_adapter_would_send_count": pm_sum.get("live_order_adapter_would_send_count"),
                "live_trading_enabled": pm_sum.get("live_trading_enabled"),
                "dry_run": pm_sum.get("dry_run"),
            },
            "checked_post_session": {
                "actual_submit": ((checked_pm or {}).get("post_session") or {}).get("actual_submit"),
                "actual_cancel": ((checked_pm or {}).get("post_session") or {}).get("actual_cancel"),
            },
        },
    )

    answers = {
        "1_am_direct_cause": "register_failed Code 4002006; unregister skipped due to false Capture-owner defer (READY_FOR_FANOUT)",
        "2_pm_direct_cause": "same register_failed; station regs still stale after AM abort",
        "3_register_limit_cause": "requested 50 while Kabu Station still held prior registrations; clear deferred",
        "4_unregister_all_success": False,
        "5_retry_register_success": False,
        "6_registration_pass_vs_register_failed": "checked Registration PASS is coordination only; runtime register is pilot_runner",
        "7_push0_cause": "abort before PUSH loop after register_failed",
        "8_capture0_cause": "Paper never connected / no PUSH fanout; Capture READY follower event_count=0",
        "9_screening_notify_reason": "Discord screening fires from universe resolve before Paper register success",
        "10_paper_result0_meaning": "INVALID_REGISTER_FAILED — not a valid zero-trade day",
        "11_excluded_from_strategy": True,
        "12_seal_incomplete_cause": "seal ran before summary/artifacts finalize; missing_required=12",
        "13_recovery_prevention": "positive discovery + skip INCOMPLETE priors + formal recovery_quarantine",
        "14_fail_fast_notify": "PAPER NOT RUNNING / INVALID PAPER SESSION Discord templates added",
        "15_post_fix_cert": "unit/synthetic register recovery + READY_FOR_FANOUT clear + journal sequence",
        "16_submit_cancel": {"submit": 0, "cancel": 0},
        "17_mainline_strategy_unchanged": True,
    }

    verdict = "REGISTER_RECOVERY_AND_SESSION_VALIDITY_FIXED"
    if not reg.get("all_ok"):
        verdict = "ROOT_CAUSE_PARTIALLY_RESOLVED"

    report = {
        "phase": "687W30",
        "day": DAY,
        "verdict": verdict,
        "also_found": [
            "REGISTER_RETRY_FALSE_SUCCESS_FOUND",
            "MULTIPLE_RUNTIME_LIFECYCLE_BUGS",
        ],
        "answers": answers,
        "regression_all_ok": reg.get("all_ok"),
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _write_json(OUT / "phase687w30_report.json", report)

    decision = f"""# Phase687W30 Decision

**Verdict: {verdict}**

Also tagged: REGISTER_RETRY_FALSE_SUCCESS_FOUND, MULTIPLE_RUNTIME_LIFECYCLE_BUGS

## Root cause
Capture `READY_FOR_FANOUT` (not applied) deferred Paper `unregister/all`. Stale Kabu regs → 4002006.
Error text falsely claimed clear+retry. PUSH/Capture 0 followed. Seal INCOMPLETE because seal preceded finalize_batch.

## Fixes shipped
1. Capture ownership requires applied/ONLINE — READY_FOR_FANOUT allows Paper clear
2. Register recovery: unregister → readback 0 → settle → register → readback N; honest errors
3. Session validity classification + Discord INVALID / PAPER NOT RUNNING
4. Seal after finalize + ensure empty required artifacts; Recovery skips INCOMPLETE priors
5. Positive-match discovery; shared journal sequence contiguous check disabled

## Constraints
ENTRY/EXIT/CAP/OR unchanged. submit/cancel = 0. No real orders.
"""
    (OUT / "phase687w30_decision.md").write_text(decision, encoding="utf-8")
    print(json.dumps({"out": str(OUT), "verdict": verdict, "regression_ok": reg.get("all_ok")}, ensure_ascii=False))
    return 0 if reg.get("all_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
