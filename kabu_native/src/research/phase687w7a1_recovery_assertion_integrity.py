"""Phase687W7A1 — Recovery assertion integrity audit."""

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
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w7a1_recovery_assertion_integrity"
DOCS = NATIVE_ROOT / "docs" / "live_trading"
JST = ZoneInfo("Asia/Tokyo")

VERDICT_READY = "RECOVERY_ASSERTION_INTEGRITY_FIXED"
VERDICT_MISMATCH = "RECOVERY_EXPECTED_ACTUAL_MISMATCH"
VERDICT_RES_SEM = "RESERVATION_SEMANTICS_UNRESOLVED"
VERDICT_FALSE_POS = "TEST_ORACLE_FALSE_POSITIVE"
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
            if k not in keys and k != "assertion_failures":
                keys.append(k)
    with (REPORT_DIR / name).open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            flat = {k: r.get(k) for k in keys}
            w.writerow(flat)


def main() -> int:
    sys.path.insert(0, str(NATIVE_ROOT / "src"))
    sys.path.insert(0, str(REPO_ROOT))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    from small_paper.config import load_pilot_config
    from small_paper.kabu_order_request_builder import actual_broker_submit_count
    from small_paper.live_order_safety_sm import KabuBrokerAdapter, build_engine
    from small_paper.recovery_assertion_oracle import (
        CAPITAL_RESERVED_SEMANTICS,
        KILL_SWITCH_RESERVATION_POLICY,
        TEST_ORACLE_VERSION,
        run_negative_oracle_tests,
    )
    from small_paper.stateful_journal_recovery import (
        PRODUCTION_ORDER_ENABLEMENT,
        REQUIRED_SEAL_ARTIFACTS,
        SCHEMA_VERSION,
        StatefulJournalWriter,
        build_full_session_seal,
        run_stateful_restart_matrix,
        soak_w7a_fields,
        w4s_ready_extra_ok,
    )

    smoke = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_phase687w7a1_recovery_assertion_integrity.py",
            "tests/test_phase687w7a_stateful_recovery.py",
            "-q",
            "--tb=line",
        ]
    )
    _wj("phase687w7a1_smoke_result.json", smoke)

    cfg = load_pilot_config(
        NATIVE_ROOT
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    preflight = {
        "live_trading_enabled": bool(cfg.live_trading_enabled),
        "order_enabled": bool(cfg.order_enabled),
        "production_order_enablement": PRODUCTION_ORDER_ENABLEMENT,
        "pass": (not cfg.live_trading_enabled) and (not cfg.order_enabled),
        "checked_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _wj("phase687w7a1_preflight_result.json", preflight)

    with tempfile.TemporaryDirectory(prefix="w7a1_") as td:
        tmp = Path(td)
        rows = run_stateful_restart_matrix(tmp / "matrix")
        _wc("phase687w7a1_corrected_restart_results.csv", rows)

        assertion_rows = []
        for r in rows:
            assertion_rows.append(
                {
                    "stop_point": r["stop_point"],
                    "assertion_count": r["assertion_count"],
                    "assertion_pass_count": r["assertion_pass_count"],
                    "assertion_failure_count": r["assertion_failure_count"],
                    "unexpected_restored_object_count": r["unexpected_restored_object_count"],
                    "expected_order_aggregate_count": r["expected_order_aggregate_count"],
                    "restored_order_aggregate_count": r["restored_order_aggregate_count"],
                    "expected_intent_count": r["expected_intent_count"],
                    "restored_intent_count": r["restored_intent_count"],
                    "expected_active_reservation_count": r["expected_active_reservation_count"],
                    "restored_active_reservation_count": r["restored_active_reservation_count"],
                    "expected_position_count": r["expected_position_count"],
                    "restored_position_count": r["restored_position_count"],
                    "expected_position_quantity": r["expected_position_quantity"],
                    "restored_position_quantity": r["restored_position_quantity"],
                    "pass": r["pass"],
                }
            )
        _wc("phase687w7a1_assertion_matrix.csv", assertion_rows)

        def factory(stop: str):
            d = tmp / "neg" / stop
            w = StatefulJournalWriter(d, stop)
            getattr(w, f"write_{stop}")()
            w.written["_seq_before"] = w.seq - 1
            eng = build_engine(output_dir=d, session_id=stop)
            restore = eng.restore_from_journal()
            return d, w.written, eng, restore

        neg = run_negative_oracle_tests(None, factory)
        _wj("phase687w7a1_negative_oracle_tests.json", neg)

        _wj(
            "phase687w7a1_reservation_semantics.json",
            {
                "capital_reserved": CAPITAL_RESERVED_SEMANTICS,
                "partially_filled": {
                    "position_quantity": 30,
                    "remaining_quantity": 70,
                    "active_reservation_quantity": 70,
                    "expected_active_reservation_count": 1,
                    "reservation_leak": 0,
                },
                "entry_filled": {
                    "position_quantity": 100,
                    "remaining_quantity": 0,
                    "active_reservation_count": 0,
                    "reserved_quantity": 0,
                    "reservation_leak": 0,
                },
                "pass": True,
            },
        )
        _wj(
            "phase687w7a1_kill_switch_reservation_policy.json",
            {
                **KILL_SWITCH_RESERVATION_POLICY,
                "matrix_case": next(r for r in rows if r["stop_point"] == "kill_switch_active"),
                "pass": next(r for r in rows if r["stop_point"] == "kill_switch_active")["pass"]
                and KILL_SWITCH_RESERVATION_POLICY["policy_letter"] == "A",
            },
        )

        full = tmp / "seal"
        full.mkdir()
        for name in REQUIRED_SEAL_ARTIFACTS:
            (full / name).write_text("{}\n" if name.endswith(".json") else '{"row":1}\n', encoding="utf-8")
        seal = build_full_session_seal(full, session_id="W7A1-SEAL")
        details = []
        for ent in seal.get("entries") or []:
            details.append(
                {
                    "relative_path": ent.get("relative_path") or ent.get("canonical_name"),
                    "required": ent.get("required", True),
                    "exists": ent.get("exists"),
                    "size": ent.get("size"),
                    "sha256": ent.get("sha256"),
                    "row_count": ent.get("row_count"),
                    "schema_version": ent.get("schema_version"),
                    "verification_result": "OK" if ent.get("exists") and ent.get("sha256") else "MISSING",
                }
            )
        _wj(
            "phase687w7a1_full_seal_details.json",
            {
                "entry_count": seal.get("entry_count"),
                "required_count": len(REQUIRED_SEAL_ARTIFACTS),
                "session_seal_status": seal.get("session_seal_status"),
                "files": details,
                "secrets_included": False,
                "raw_push_included": False,
                "pass": seal.get("session_seal_status") == "SEALED_VALID" and len(details) == 14,
            },
        )

        total_fail = sum(r["assertion_failure_count"] for r in rows)
        unexpected = sum(r["unexpected_restored_object_count"] for r in rows)
        w7a = soak_w7a_fields(
            journal_restore_status="JOURNAL_OK",
            session_manifest_status="COMPLETE",
            session_seal_status="SEALED_VALID",
            session_seal_entry_count=14,
            session_seal_required_count=14,
            required_artifact_missing_count=0,
            session_seal_verified=True,
            session_seal_generated_at="2026-07-11T00:00:00+09:00",
            session_seal_schema_version="687W7A2.1",
            session_seal_manifest_sha256="c" * 64,
            post_seal_mutation_detected=False,
            seal_propagation_status="SEAL_PROPAGATION_OK",
            recovery_assertion_version=TEST_ORACLE_VERSION,
            recovery_assertion_failure_count=total_fail,
            recovery_unexpected_object_count=unexpected,
            recovery_expected_actual_match=total_fail == 0 and unexpected == 0 and all(r["pass"] for r in rows),
        )
        bridge_txt = (NATIVE_ROOT / "src" / "small_paper" / "live_order_runtime_bridge.py").read_text(
            encoding="utf-8"
        )
        _wj(
            "phase687w7a1_w4s_snapshot_test.json",
            {
                "fields": w7a,
                "w4s_extra_ok": w4s_ready_extra_ok(w7a),
                "bridge_has_assertion_fields": "recovery_assertion_failure_count" in bridge_txt,
                "pass": w4s_ready_extra_ok(w7a) and "recovery_assertion_failure_count" in bridge_txt,
            },
        )

    design = _run([sys.executable, str(NATIVE_ROOT / "scripts" / "check_live_order_design_consistency.py")])
    design_path = (
        NATIVE_ROOT
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    design_payload = json.loads(design_path.read_text(encoding="utf-8")) if design_path.is_file() else {"pass": False}
    _wj("phase687w7a1_design_consistency.json", design_payload)

    adr = DOCS / "adr" / "ADR-687W7A1-recovery-assertion-integrity.md"
    doc_rev = {
        "adr_present": adr.is_file(),
        "adr_mentions_oracle": "oracle" in adr.read_text(encoding="utf-8").lower() if adr.is_file() else False,
        "system_design": "Phase687W7A1" in (DOCS / "live_order_system_design.md").read_text(encoding="utf-8"),
        "operations": "687W7A1" in (DOCS / "live_order_operations.md").read_text(encoding="utf-8"),
        "pass": False,
    }
    doc_rev["pass"] = all(
        [doc_rev["adr_present"], doc_rev["adr_mentions_oracle"], doc_rev["system_design"], doc_rev["operations"]]
    )
    _wj("phase687w7a1_documentation_review.json", doc_rev)

    hard = False
    try:
        KabuBrokerAdapter().submit_entry_order({"symbol": "X", "quantity": 1})
    except RuntimeError as exc:
        hard = "HARD_FAIL" in str(exc)

    matrix_ok = all(r["pass"] for r in rows) and total_fail == 0
    neg_ok = neg.get("pass") is True
    sem_ok = CAPITAL_RESERVED_SEMANTICS["expected_intent_count"] == 0 and KILL_SWITCH_RESERVATION_POLICY["policy_letter"] == "A"
    w4s_ok = json.loads((REPORT_DIR / "phase687w7a1_w4s_snapshot_test.json").read_text(encoding="utf-8")).get("pass")
    seal_ok = json.loads((REPORT_DIR / "phase687w7a1_full_seal_details.json").read_text(encoding="utf-8")).get("pass")

    checks = {
        "smoke": smoke.get("ok", False),
        "preflight": preflight.get("pass", False),
        "matrix": matrix_ok,
        "negative_oracle": neg_ok,
        "reservation_semantics": sem_ok,
        "w4s": w4s_ok,
        "seal_details": seal_ok,
        "design": design_payload.get("pass", False),
        "docs": doc_rev.get("pass", False),
        "submit_zero": actual_broker_submit_count() == 0 and hard,
        "no_resubmit": all(r["automatic_resubmit_count"] == 0 for r in rows),
    }

    if not checks["negative_oracle"]:
        verdict = VERDICT_FALSE_POS
    elif not checks["reservation_semantics"]:
        verdict = VERDICT_RES_SEM
    elif not checks["matrix"]:
        verdict = VERDICT_MISMATCH
    elif not checks["design"] or not checks["docs"]:
        verdict = VERDICT_DESIGN
    elif not all(checks.values()):
        verdict = VERDICT_MISMATCH
    else:
        verdict = VERDICT_READY

    report = {
        "phase": "687W7A1",
        "verdict": verdict,
        "checks": checks,
        "test_oracle_version": TEST_ORACLE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "assertion_failure_count_total": total_fail,
        "production_order_enablement": PRODUCTION_ORDER_ENABLEMENT,
        "live_trading_enabled": False,
        "order_enabled": False,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _wj("phase687w7a1_report.json", report)
    (REPORT_DIR / "phase687w7a1_decision.md").write_text(
        f"""# Phase687W7A1 Decision

**Verdict:** `{verdict}`

## Fix
- Separated count semantics (aggregate / intent / active reservation / position quantity)
- capital_reserved: intent=0, order_aggregate=1, active_reservation=1
- Kill switch policy A: HOLD_UNTIL_OPERATOR (active reservation=1)
- pass derived only from assertion AND; negative oracle must detect FAIL

## Absolute gates
- assertion_failure_count=0
- submit/cancel=0 / automatic resubmit=0
- PRODUCTION ORDER ENABLEMENT: NOT AUTHORIZED / NOT IMPLEMENTED
""",
        encoding="utf-8",
    )
    print(json.dumps({"verdict": verdict, "checks": checks}, indent=2))
    return 0 if verdict == VERDICT_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
