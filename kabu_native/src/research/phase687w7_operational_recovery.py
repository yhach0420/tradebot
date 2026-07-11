"""Phase687W7 — Operational recovery and audit drill (dry-run, no real orders)."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w7_operational_recovery"
DOCS = NATIVE_ROOT / "docs" / "live_trading"
JST = ZoneInfo("Asia/Tokyo")

VERDICT_READY = "OPERATIONAL_RECOVERY_DRYRUN_READY"
VERDICT_JOURNAL = "JOURNAL_RECOVERY_FAILED"
VERDICT_KILL = "KILL_SWITCH_DRILL_FAILED"
VERDICT_FILE = "FILE_FAILURE_SAFETY_FAILED"
VERDICT_SEAL = "SESSION_SEAL_FAILED"
VERDICT_OPERATOR = "OPERATOR_BOUNDARY_FAILED"
VERDICT_NETWORK = "NETWORK_ISOLATION_FAILED"
VERDICT_DESIGN = "DESIGN_CODE_MISMATCH"


def _run(cmd: list[str]) -> dict[str, Any]:
    import os

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{NATIVE_ROOT / 'src'};{REPO_ROOT}"
    proc = subprocess.run(cmd, cwd=str(NATIVE_ROOT), env=env, capture_output=True, text=True)
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-600:],
    }


def _wj(name: str, obj: Any) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _wc(name: str, rows: list[dict[str, Any]]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if not rows:
        (REPORT_DIR / name).write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with (REPORT_DIR / name).open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in keys})


def main() -> int:
    sys.path.insert(0, str(NATIVE_ROOT / "src"))
    sys.path.insert(0, str(REPO_ROOT))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    from small_paper.config import load_pilot_config
    from small_paper.kabu_order_request_builder import actual_broker_submit_count
    from small_paper.live_order_safety_sm import KabuBrokerAdapter
    from small_paper.operational_recovery import (
        PRODUCTION_ORDER_ENABLEMENT,
        SCHEMA_VERSION,
        build_audit_bundle_manifest,
        create_session_manifest,
        diagnose_clock,
        disk_guard_report,
        dryrun_ready_evidence,
        evaluate_recovery_readiness,
        finalize_session_manifest,
        recovery_mode_matrix_rows,
        run_fault_injection_matrix,
        run_file_failure_tests,
        run_kill_switch_drills,
        run_restart_drills,
        sample_operator_recovery_ack,
        validate_session_manifest,
        verify_session_seal,
        write_session_seal,
        check_journal_integrity,
    )

    smoke = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_phase687w7_operational_recovery.py",
            "-q",
            "--tb=line",
        ]
    )
    _wj("phase687w7_smoke_result.json", smoke)

    cfg = load_pilot_config(
        NATIVE_ROOT
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    preflight = {
        "live_trading_enabled": bool(cfg.live_trading_enabled),
        "order_enabled": bool(cfg.order_enabled),
        "production_order_enablement": PRODUCTION_ORDER_ENABLEMENT,
        "write_adapter": "NOT_IMPLEMENTED",
        "canary_execution": "FORBIDDEN",
        "valid_approval_generated": False,
        "pass": (not cfg.live_trading_enabled) and (not cfg.order_enabled),
        "checked_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _wj("phase687w7_preflight_result.json", preflight)

    _wc("phase687w7_recovery_mode_matrix.csv", recovery_mode_matrix_rows())

    with tempfile.TemporaryDirectory(prefix="w7_") as td:
        tmp = Path(td)
        # session manifest example
        create_session_manifest(session_id="W7-EXAMPLE", output_dir=tmp / "sess", config_sha="demo")
        finalize_session_manifest(tmp / "sess", canonical_entry_count=0, submit_count=0, cancel_count=0)
        man = json.loads((tmp / "sess" / "session_manifest.json").read_text(encoding="utf-8"))
        _wj("phase687w7_session_manifest_example.json", man)

        (tmp / "sess" / "events.jsonl").write_text('{"event":"demo"}\n', encoding="utf-8")
        write_session_seal(tmp / "sess")
        seal_v = verify_session_seal(tmp / "sess" / "session_seal.json", tmp / "sess")
        _wj(
            "phase687w7_session_seal_test.json",
            {
                "seal": json.loads((tmp / "sess" / "session_seal.json").read_text(encoding="utf-8")),
                "verify": seal_v,
                "manifest_valid": validate_session_manifest(tmp / "sess" / "session_manifest.json"),
                "pass": seal_v.get("valid") and validate_session_manifest(tmp / "sess" / "session_manifest.json").get("valid"),
            },
        )

        # journal integrity tests
        jp = tmp / "journal"
        jp.mkdir()
        (jp / "partial.jsonl").write_text('{"sequence":1}\n{"sequence":2', encoding="utf-8")
        (jp / "gap.jsonl").write_text('{"sequence":1}\n{"sequence":3}\n', encoding="utf-8")
        (jp / "ok.jsonl").write_text('{"sequence":1}\n{"sequence":2}\n', encoding="utf-8")
        j_tests = {
            "partial": check_journal_integrity(jp / "partial.jsonl").to_dict(),
            "gap": check_journal_integrity(jp / "gap.jsonl").to_dict(),
            "ok": check_journal_integrity(jp / "ok.jsonl").to_dict(),
            "pass": True,
        }
        j_tests["pass"] = (
            j_tests["partial"]["entry_blocked"]
            and j_tests["gap"]["entry_blocked"]
            and not j_tests["ok"]["entry_blocked"]
            and j_tests["partial"]["original_preserved"]
        )
        _wj("phase687w7_journal_integrity_tests.json", j_tests)

        ks = run_kill_switch_drills(tmp / "ks")
        _wj("phase687w7_kill_switch_drill.json", ks)

        rs = run_restart_drills(tmp / "rs")
        _wj("phase687w7_restart_drill.json", rs)

        ff = run_file_failure_tests(tmp / "ff")
        _wc("phase687w7_file_failure_tests.csv", ff)

        faults = run_fault_injection_matrix(tmp / "fi")
        _wc("phase687w7_fault_injection.csv", faults)

    _wj("phase687w7_disk_guard.json", disk_guard_report(NATIVE_ROOT))
    _wj("phase687w7_clock_integrity.json", diagnose_clock())
    _wj(
        "phase687w7_operator_ack_schema.json",
        {
            "sample": sample_operator_recovery_ack(),
            "valid_production_ack_generated": False,
            "statuses": ["SAMPLE_ONLY", "NOT_ACKNOWLEDGED", "ACKNOWLEDGED_DRYRUN", "PRODUCTION_FORBIDDEN"],
        },
    )
    _wj(
        "phase687w7_audit_bundle_manifest.json",
        build_audit_bundle_manifest(session_id="W7-EXAMPLE", incident_id="INC-W7-DEMO"),
    )

    hard_submit = False
    hard_cancel = False
    try:
        KabuBrokerAdapter().submit_entry_order({"symbol": "X", "quantity": 1})
    except RuntimeError as exc:
        hard_submit = "HARD_FAIL" in str(exc)
    try:
        KabuBrokerAdapter().cancel_order("OID")
    except RuntimeError as exc:
        hard_cancel = "HARD_FAIL" in str(exc)
    network = {
        "submit_hard_fail": hard_submit,
        "cancel_hard_fail": hard_cancel,
        "actual_broker_submit_count": actual_broker_submit_count(),
        "write_adapter_implemented": False,
        "pass": hard_submit and hard_cancel and actual_broker_submit_count() == 0,
    }
    _wj("phase687w7_network_isolation.json", network)

    design = _run([sys.executable, str(NATIVE_ROOT / "scripts" / "check_live_order_design_consistency.py")])
    design_path = (
        NATIVE_ROOT
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    design_payload = json.loads(design_path.read_text(encoding="utf-8")) if design_path.is_file() else {"pass": False}
    _wj("phase687w7_design_consistency.json", design_payload)

    adr = DOCS / "adr" / "ADR-687W7-operational-recovery-audit.md"
    doc_rev = {
        "adr_present": adr.is_file(),
        "adr_mentions_recovery_modes": "recovery mode" in adr.read_text(encoding="utf-8").lower()
        if adr.is_file()
        else False,
        "system_design_mentions_w7": "Phase687W7"
        in (DOCS / "live_order_system_design.md").read_text(encoding="utf-8"),
        "operations_mentions_cli": "check_live_order_recovery_readiness"
        in (DOCS / "live_order_operations.md").read_text(encoding="utf-8"),
        "pass": False,
    }
    doc_rev["pass"] = all(
        [
            doc_rev["adr_present"],
            doc_rev["adr_mentions_recovery_modes"],
            doc_rev["system_design_mentions_w7"],
            doc_rev["operations_mentions_cli"],
        ]
    )
    _wj("phase687w7_documentation_review.json", doc_rev)

    demo = evaluate_recovery_readiness(dryrun_ready_evidence())
    cli = _run(
        [sys.executable, "-m", "small_paper.check_live_order_recovery_readiness", "--demo-ready"]
    )

    seal_test = json.loads((REPORT_DIR / "phase687w7_session_seal_test.json").read_text(encoding="utf-8"))
    j_tests = json.loads((REPORT_DIR / "phase687w7_journal_integrity_tests.json").read_text(encoding="utf-8"))
    ks = json.loads((REPORT_DIR / "phase687w7_kill_switch_drill.json").read_text(encoding="utf-8"))
    rs = json.loads((REPORT_DIR / "phase687w7_restart_drill.json").read_text(encoding="utf-8"))
    ack = json.loads((REPORT_DIR / "phase687w7_operator_ack_schema.json").read_text(encoding="utf-8"))
    ff_pass = all(r.get("pass") for r in ff)
    fault_pass = all(r.get("pass") for r in faults)

    checks = {
        "smoke": smoke.get("ok", False),
        "preflight": preflight.get("pass", False),
        "seal": seal_test.get("pass", False),
        "journal": j_tests.get("pass", False),
        "kill_switch": ks.get("pass", False),
        "restart": rs.get("pass", False),
        "file_failure": ff_pass,
        "fault_injection": fault_pass,
        "network": network.get("pass", False),
        "design": design_payload.get("pass", False),
        "docs": doc_rev.get("pass", False),
        "operator_boundary": ack["sample"]["acknowledgment_status"] == "SAMPLE_ONLY"
        and not ack["valid_production_ack_generated"],
        "cli_demo": cli.get("ok") and demo.get("exit_code") == 0 and demo.get("production_authorized") is False,
        "submit_zero": actual_broker_submit_count() == 0 and hard_submit,
    }

    if not checks["network"] or not checks["submit_zero"]:
        verdict = VERDICT_NETWORK
    elif not checks["operator_boundary"]:
        verdict = VERDICT_OPERATOR
    elif not checks["design"] or not checks["docs"]:
        verdict = VERDICT_DESIGN
    elif not checks["seal"]:
        verdict = VERDICT_SEAL
    elif not checks["journal"]:
        verdict = VERDICT_JOURNAL
    elif not checks["kill_switch"] or not checks["restart"]:
        verdict = VERDICT_KILL
    elif not checks["file_failure"]:
        verdict = VERDICT_FILE
    elif not all(checks.values()):
        verdict = VERDICT_JOURNAL
    else:
        verdict = VERDICT_READY

    report = {
        "phase": "687W7",
        "verdict": verdict,
        "checks": checks,
        "schema_version": SCHEMA_VERSION,
        "production_order_enablement": PRODUCTION_ORDER_ENABLEMENT,
        "production_ready": False,
        "write_adapter": "NOT_IMPLEMENTED",
        "live_trading_enabled": False,
        "order_enabled": False,
        "note": "READY means dry-run operational recovery foundation complete — not order authorization",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _wj("phase687w7_report.json", report)
    (REPORT_DIR / "phase687w7_decision.md").write_text(
        f"""# Phase687W7 Decision

**Verdict:** `{verdict}`

## Meaning of READY
Dry-run operational recovery foundation is complete. This does **not** authorize, implement, or enable real orders.

## PRODUCTION ORDER ENABLEMENT
**NOT AUTHORIZED / NOT IMPLEMENTED**

## Absolute gates
- live_trading_enabled=false / order_enabled=false
- Write adapter absent; submit/cancel HARD_FAIL
- No valid production approval / canary
- Recovery modes never auto-return to NORMAL without operator ack

## CLI
`python -m small_paper.check_live_order_recovery_readiness`
""",
        encoding="utf-8",
    )
    print(json.dumps({"verdict": verdict, "checks": checks}, indent=2))
    return 0 if verdict == VERDICT_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
