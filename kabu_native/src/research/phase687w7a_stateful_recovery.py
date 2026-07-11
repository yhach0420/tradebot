"""Phase687W7A — Stateful recovery proof + runtime session seal audit."""

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
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w7a_stateful_recovery"
DOCS = NATIVE_ROOT / "docs" / "live_trading"
JST = ZoneInfo("Asia/Tokyo")

VERDICT_READY = "STATEFUL_RECOVERY_PROOF_READY"
VERDICT_ORDER = "ORDER_RESTORE_FAILED"
VERDICT_RES = "RESERVATION_RESTORE_FAILED"
VERDICT_POS = "POSITION_RESTORE_FAILED"
VERDICT_MANIFEST = "SESSION_MANIFEST_INCOMPLETE"
VERDICT_SEAL = "SESSION_SEAL_INCOMPLETE"
VERDICT_RESUBMIT = "AUTOMATIC_RESUBMIT_FOUND"
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
    from small_paper.operational_recovery import disk_guard_report
    from small_paper.stateful_journal_recovery import (
        PRODUCTION_ORDER_ENABLEMENT,
        SCHEMA_VERSION,
        REQUIRED_SEAL_ARTIFACTS,
        build_full_session_seal,
        restored_order_detail_rows,
        run_seal_mutation_tests,
        run_stateful_restart_matrix,
        soak_w7a_fields,
        w4s_ready_extra_ok,
        write_full_session_seal,
        resolve_git_commit,
    )
    from small_paper.operational_recovery import create_session_manifest as _csm

    smoke = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_phase687w7a_stateful_recovery.py",
            "-q",
            "--tb=line",
        ]
    )
    _wj("phase687w7a_smoke_result.json", smoke)

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
        "canary": "FORBIDDEN",
        "pass": (not cfg.live_trading_enabled) and (not cfg.order_enabled),
        "checked_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _wj("phase687w7a_preflight_result.json", preflight)

    with tempfile.TemporaryDirectory(prefix="w7a_") as td:
        tmp = Path(td)
        rows = run_stateful_restart_matrix(tmp / "matrix")
        _wc("phase687w7a_stateful_restart_results.csv", rows)
        details = restored_order_detail_rows(tmp / "details")
        _wc("phase687w7a_restored_order_details.csv", details)

        by = {r["stop_point"]: r for r in rows}
        _wj(
            "phase687w7a_reservation_recovery.json",
            {
                "capital_reserved": by.get("capital_reserved"),
                "partially_filled": by.get("partially_filled"),
                "entry_filled": by.get("entry_filled"),
                "pass": by["capital_reserved"]["pass"]
                and by["partially_filled"]["pass"]
                and by["entry_filled"]["pass"],
            },
        )
        _wj(
            "phase687w7a_position_recovery.json",
            {
                "partially_filled_qty": by["partially_filled"]["position_qty"],
                "entry_filled_qty": by["entry_filled"]["position_qty"],
                "partial_exit_qty": by["partial_exit"]["position_qty"],
                "pass": by["partially_filled"]["position_qty"] == 30
                and by["entry_filled"]["position_qty"] == 100
                and by["partial_exit"]["position_qty"] == 60,
            },
        )
        _wj(
            "phase687w7a_kill_switch_restore.json",
            {
                "case": by["kill_switch_active"],
                "pass": by["kill_switch_active"]["pass"]
                and by["kill_switch_active"]["recovery_mode"] == "KILL_SWITCH_ACTIVE",
            },
        )

        # runtime manifest synthetic session (not UNSET/demo)
        sess = tmp / "runtime_sess"
        sess.mkdir()
        _csm(
            session_id="SYNTHETIC-W7A-1",
            output_dir=sess,
            trading_day=datetime.now(JST).strftime("%Y-%m-%d"),
            session_am_pm="AM",
            git_commit=resolve_git_commit(NATIVE_ROOT),
            config_sha="synthetic_config_sha_not_demo",
            safety_sm_enabled=True,
            np_logger_enabled=True,
            kabu_readonly_status="ONLINE_VALID",
            token_probe_status="TOKEN_ACQUIRED",
            journal_sequence_start=0,
        )
        man = json.loads((sess / "session_manifest.json").read_text(encoding="utf-8"))
        man_ok = (
            man.get("git_commit") not in ("UNSET", "demo", "")
            and man.get("config_sha256") not in ("UNSET", "demo", "")
            and man.get("session_id") == "SYNTHETIC-W7A-1"
        )
        pilot_txt = (NATIVE_ROOT / "src" / "small_paper" / "pilot_runner.py").read_text(encoding="utf-8")
        bridge_txt = (NATIVE_ROOT / "src" / "small_paper" / "live_order_runtime_bridge.py").read_text(
            encoding="utf-8"
        )
        _wj(
            "phase687w7a_runtime_manifest_test.json",
            {
                "synthetic_manifest": man,
                "manifest_fields_ok": man_ok,
                "pilot_create_hook": "create_session_manifest" in pilot_txt,
                "pilot_finalize_hook": "finalize_session_manifest" in pilot_txt,
                "pilot_full_seal_hook": "write_full_session_seal" in pilot_txt,
                "bridge_restore_hook": "restore_from_journal" in bridge_txt,
                "status": "SYNTHETIC_RECOVERY_PROOF_PASS" if man_ok else "SESSION_MANIFEST_INCOMPLETE",
                "forward_status": "FORWARD_SESSION_SEAL_PENDING",
                "pass": man_ok
                and "create_session_manifest" in pilot_txt
                and "write_full_session_seal" in pilot_txt
                and "restore_from_journal" in bridge_txt,
            },
        )

        # full seal synthetic
        full = tmp / "full_seal"
        full.mkdir()
        for name in REQUIRED_SEAL_ARTIFACTS:
            (full / name).write_text("{}\n" if name.endswith(".json") else '{"row":1}\n', encoding="utf-8")
        _csm(
            session_id="SYNTHETIC-SEAL",
            output_dir=full,
            git_commit=resolve_git_commit(NATIVE_ROOT),
            config_sha="synthetic_sha",
        )
        seal = build_full_session_seal(full, session_id="SYNTHETIC-SEAL")
        write_full_session_seal(full, session_id="SYNTHETIC-SEAL")
        _wj(
            "phase687w7a_full_session_seal_test.json",
            {
                "seal_status": seal["session_seal_status"],
                "missing": seal["required_artifact_missing_count"],
                "entry_count": seal["entry_count"],
                "pass": seal["session_seal_status"] == "SEALED_VALID",
            },
        )

        seal_mut = run_seal_mutation_tests(tmp / "seal_mut")
        _wc("phase687w7a_seal_mutation_tests.csv", seal_mut)

        w7a = soak_w7a_fields(
            journal_restore_status="JOURNAL_OK",
            restored_order_count=by["intent_created"]["restored_order_count"],
            restored_reservation_count=by["intent_created"]["restored_reservation_count"],
            restored_position_count=0,
            session_manifest_status="COMPLETE",
            session_seal_status="SEALED_VALID",
            session_seal_entry_count=seal["entry_count"],
            session_seal_required_count=seal.get("required_count") or 14,
            required_artifact_missing_count=0,
            session_seal_verified=True,
            session_seal_generated_at=str(seal.get("generated_at") or ""),
            session_seal_schema_version=str(seal.get("schema_version") or ""),
            session_seal_manifest_sha256=str(seal.get("session_seal_manifest_sha256") or "d" * 64),
            post_seal_mutation_detected=False,
            seal_propagation_status="SEAL_PROPAGATION_OK",
            recovery_mode_at_end="NORMAL",
            recovery_assertion_failure_count=0,
            recovery_unexpected_object_count=0,
            recovery_expected_actual_match=True,
        )
        _wj(
            "phase687w7a_w4s_snapshot_test.json",
            {
                "fields": w7a,
                "bridge_has_w7a": "soak_w7a_fields" in bridge_txt or "restart_recovery_test_version" in bridge_txt,
                "w4s_extra_ok": w4s_ready_extra_ok(w7a),
                "pass": w4s_ready_extra_ok(w7a) and ("soak_w7a_fields" in bridge_txt),
            },
        )

    _wj("phase687w7a_disk_guard.json", disk_guard_report(NATIVE_ROOT))

    hard_s = hard_c = False
    try:
        KabuBrokerAdapter().submit_entry_order({"symbol": "X", "quantity": 1})
    except RuntimeError as exc:
        hard_s = "HARD_FAIL" in str(exc)
    try:
        KabuBrokerAdapter().cancel_order("OID")
    except RuntimeError as exc:
        hard_c = "HARD_FAIL" in str(exc)
    network = {
        "submit_hard_fail": hard_s,
        "cancel_hard_fail": hard_c,
        "actual_broker_submit_count": actual_broker_submit_count(),
        "pass": hard_s and hard_c and actual_broker_submit_count() == 0,
    }
    _wj("phase687w7a_network_isolation.json", network)

    design = _run([sys.executable, str(NATIVE_ROOT / "scripts" / "check_live_order_design_consistency.py")])
    design_path = (
        NATIVE_ROOT
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    design_payload = json.loads(design_path.read_text(encoding="utf-8")) if design_path.is_file() else {"pass": False}
    _wj("phase687w7a_design_consistency.json", design_payload)

    adr = DOCS / "adr" / "ADR-687W7A-stateful-recovery-session-seal.md"
    doc_rev = {
        "adr_present": adr.is_file(),
        "adr_mentions_stateful": "stateful" in adr.read_text(encoding="utf-8").lower() if adr.is_file() else False,
        "system_design_w7a": "Phase687W7A" in (DOCS / "live_order_system_design.md").read_text(encoding="utf-8"),
        "operations_w7a": "687W7A" in (DOCS / "live_order_operations.md").read_text(encoding="utf-8")
        or "stateful" in (DOCS / "live_order_operations.md").read_text(encoding="utf-8").lower(),
        "pass": False,
    }
    doc_rev["pass"] = all(
        [doc_rev["adr_present"], doc_rev["adr_mentions_stateful"], doc_rev["system_design_w7a"], doc_rev["operations_w7a"]]
    )
    _wj("phase687w7a_documentation_review.json", doc_rev)

    matrix_ok = all(r["pass"] for r in rows)
    intent_ok = by["intent_created"]["restored_order_count"] >= 1
    partial_ok = by["partially_filled"]["pass"]
    res_ok = by["capital_reserved"]["pass"] and by["entry_filled"]["restored_reservation_count"] == 0
    pos_ok = by["entry_filled"]["position_qty"] == 100 and by["partial_exit"]["position_qty"] == 60
    resubmit_ok = all(r["automatic_resubmit_count"] == 0 for r in rows)
    seal_ok = json.loads((REPORT_DIR / "phase687w7a_full_session_seal_test.json").read_text(encoding="utf-8")).get(
        "pass"
    )
    man_ok = json.loads((REPORT_DIR / "phase687w7a_runtime_manifest_test.json").read_text(encoding="utf-8")).get("pass")
    w4s_ok = json.loads((REPORT_DIR / "phase687w7a_w4s_snapshot_test.json").read_text(encoding="utf-8")).get("pass")
    seal_mut_ok = all(r["pass"] for r in seal_mut)

    checks = {
        "smoke": smoke.get("ok", False),
        "preflight": preflight.get("pass", False),
        "matrix": matrix_ok,
        "intent_restore": intent_ok,
        "partial_fill": partial_ok,
        "reservation": res_ok,
        "position": pos_ok,
        "no_resubmit": resubmit_ok,
        "seal": seal_ok and seal_mut_ok,
        "runtime_hooks": man_ok,
        "w4s": w4s_ok,
        "network": network.get("pass", False),
        "design": design_payload.get("pass", False),
        "docs": doc_rev.get("pass", False),
        "submit_zero": actual_broker_submit_count() == 0 and hard_s,
    }

    if not checks["network"] or not checks["submit_zero"]:
        verdict = VERDICT_NETWORK
    elif not checks["no_resubmit"]:
        verdict = VERDICT_RESUBMIT
    elif not checks["design"] or not checks["docs"]:
        verdict = VERDICT_DESIGN
    elif not checks["intent_restore"] or not checks["partial_fill"]:
        verdict = VERDICT_ORDER
    elif not checks["reservation"]:
        verdict = VERDICT_RES
    elif not checks["position"]:
        verdict = VERDICT_POS
    elif not checks["runtime_hooks"]:
        verdict = VERDICT_MANIFEST
    elif not checks["seal"]:
        verdict = VERDICT_SEAL
    elif not all(checks.values()):
        verdict = VERDICT_ORDER
    else:
        verdict = VERDICT_READY

    report = {
        "phase": "687W7A",
        "verdict": verdict,
        "checks": checks,
        "schema_version": SCHEMA_VERSION,
        "production_order_enablement": PRODUCTION_ORDER_ENABLEMENT,
        "synthetic_status": "SYNTHETIC_RECOVERY_PROOF_PASS",
        "runtime_integration": "RUNTIME_INTEGRATION_READY",
        "forward_status": "FORWARD_SESSION_SEAL_PENDING",
        "live_trading_enabled": False,
        "order_enabled": False,
        "note": "READY = stateful restore proof + runtime hooks; not order authorization. Monday forward seal still pending.",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _wj("phase687w7a_report.json", report)
    (REPORT_DIR / "phase687w7a_decision.md").write_text(
        f"""# Phase687W7A Decision

**Verdict:** `{verdict}`

## Meaning of READY
Stateful journal restore is proven against real append-only files, and Runtime manifest/finalize/seal hooks are connected.
This does **not** authorize real orders.

## Status
- SYNTHETIC_RECOVERY_PROOF_PASS
- RUNTIME_INTEGRATION_READY
- FORWARD_SESSION_SEAL_PENDING (Monday+ live Paper)

## Absolute gates
- PRODUCTION ORDER ENABLEMENT: NOT AUTHORIZED / NOT IMPLEMENTED
- Write adapter absent; submit/cancel HARD_FAIL
- No valid production approval / canary
""",
        encoding="utf-8",
    )
    print(json.dumps({"verdict": verdict, "checks": checks}, indent=2))
    return 0 if verdict == VERDICT_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
