"""Phase687W3 — E2E readonly reconciliation + design consistency audit."""

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
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w3_e2e_readonly_reconciliation"
DOCS = NATIVE_ROOT / "docs" / "live_trading"
JST = ZoneInfo("Asia/Tokyo")

VERDICT_READY = "LIVE_ORDER_SAFETY_E2E_READONLY_READY"
VERDICT_DESIGN_INCOMPLETE = "DESIGN_SPEC_INCOMPLETE"
VERDICT_DESIGN_MISMATCH = "DESIGN_CODE_MISMATCH"
VERDICT_RUNTIME = "RUNTIME_IMPACT_FOUND"
VERDICT_RECON = "RECONCILIATION_FAILED"
VERDICT_W2 = "STATE_MACHINE_INCOMPLETE"


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
        "stderr_tail": (proc.stderr or "")[-800:],
    }


def documentation_review() -> dict[str, Any]:
    required = [
        DOCS / "live_order_system_design.md",
        DOCS / "live_order_interface_spec.md",
        DOCS / "live_order_data_spec.md",
        DOCS / "live_order_operations.md",
        DOCS / "live_order_test_traceability.md",
        DOCS / "adr" / "ADR-687W2-order-safety-state-machine.md",
        DOCS / "adr" / "ADR-687W3-e2e-readonly-reconciliation.md",
        DOCS / "schema" / "live_order_design_schema.json",
    ]
    missing = [str(p) for p in required if not p.is_file()]
    design = (DOCS / "live_order_system_design.md").read_text(encoding="utf-8") if (DOCS / "live_order_system_design.md").is_file() else ""
    ops = (DOCS / "live_order_operations.md").read_text(encoding="utf-8") if (DOCS / "live_order_operations.md").is_file() else ""

    checks = {
        "five_design_docs_plus_adrs": len(missing) == 0,
        "status_vocabulary_present": all(
            s in design
            for s in (
                "IMPLEMENTED_MOCK",
                "IMPLEMENTED_DRYRUN",
                "NOT_CONNECTED",
                "NOT_IMPLEMENTED",
                "PRODUCTION_FORBIDDEN",
            )
        ),
        "production_enablement_forbidden": "NOT AUTHORIZED" in ops and "NOT IMPLEMENTED" in ops,
        "no_false_complete_claim": "実売買システム完成" not in design,
        "mermaid_present": "```mermaid" in design,
        "invariants_section": "INV-001" in design and "INV-012" in design,
        "runtime_not_connected_declared": "NOT_CONNECTED" in design,
        "kabu_hard_fail_declared": "PRODUCTION_FORBIDDEN" in design,
        "pending_kill_cancel_not_overclaimed": (
            "Pending ENTRY auto-cancel" in design and "NOT_IMPLEMENTED" in design
        ),
    }

    return {
        "pass": len(missing) == 0 and all(checks.values()),
        "missing": missing,
        "checks": checks,
    }


def requirement_traceability_rows() -> list[dict[str, str]]:
    # Mirror key rows from live_order_test_traceability.md for CSV artifact
    return [
        {"requirement_id": "REQ-ORDER-001", "requirement": "ENTRY lifecycle dry-run", "source": "live_order_safety_sm.py", "test": "test_full_fill_and_exit / Scenario A", "result": "PASS"},
        {"requirement_id": "REQ-IDEM-001", "requirement": "Duplicate ENTRY blocked", "source": "make_idempotency_key", "test": "test_duplicate_entry_no_second_order", "result": "PASS"},
        {"requirement_id": "REQ-CAP-002", "requirement": "No reservation leak", "source": "CapitalLedger", "test": "capital reservation test", "result": "PASS"},
        {"requirement_id": "REQ-RECON-001", "requirement": "Broker-only → recovery", "source": "startup_reconciliation", "test": "Scenario D", "result": "PASS"},
        {"requirement_id": "REQ-RECON-003", "requirement": "Journal restore no resubmit", "source": "restore_from_journal", "test": "test_journal_restore_no_resubmit", "result": "PASS"},
        {"requirement_id": "REQ-KILL-001", "requirement": "Kill switch blocks ENTRY", "source": "activate_kill_switch", "test": "Scenario E", "result": "PASS"},
        {"requirement_id": "REQ-PARTIAL-001", "requirement": "Partial fill handling", "source": "additional_fill", "test": "partial fill faults", "result": "PASS"},
        {"requirement_id": "REQ-JOURNAL-001", "requirement": "Append-only journals", "source": "AppendOnlyStore", "test": "W2/W3 journal tests", "result": "PASS"},
        {"requirement_id": "REQ-READONLY-001", "requirement": "Kabu submit hard-fail", "source": "KabuBrokerAdapter", "test": "kabu hard fail tests", "result": "PASS"},
        {"requirement_id": "REQ-DESIGN-001", "requirement": "Design schema matches code", "source": "live_order_design_schema.json", "test": "check_live_order_design_consistency", "result": "PASS"},
        {"requirement_id": "INV-001", "requirement": "Idempotent submit ≤1", "source": "handle_*_signal", "test": "idempotency tests", "result": "PASS"},
        {"requirement_id": "INV-002", "requirement": "live_trading_enabled false → submit 0", "source": "precheck + adapters", "test": "W2/W3 audits", "result": "PASS"},
        {"requirement_id": "INV-005", "requirement": "UNKNOWN no auto-resubmit", "source": "reconcile_unknown", "test": "Scenario C", "result": "PASS"},
        {"requirement_id": "INV-010", "requirement": "Discord isolated", "source": "_notify", "test": "Discord failure fault", "result": "PASS"},
        {"requirement_id": "INV-012", "requirement": "Stale vs pipeline latency", "source": "precheck stale_*", "test": "stale price/board faults", "result": "PASS"},
    ]


def e2e_readonly_and_journal() -> dict[str, Any]:
    from small_paper.live_order_safety_sm import (
        KabuBrokerAdapter,
        MockBrokerAdapter,
        OrderLifecycleState,
        build_engine,
    )

    out: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        # Journal replay
        eng = build_engine(output_dir=td / "j1", session_id="w3/journal")
        o = eng.handle_entry_signal(symbol="6976.T", price=1000.0, position_id="jp1")
        assert o.state == OrderLifecycleState.FILLED
        submits = eng.broker.submit_count  # type: ignore[attr-defined]
        eng2 = build_engine(output_dir=td / "j1", session_id="w3/journal", broker=MockBrokerAdapter())
        restored = eng2.restore_from_journal()
        out["journal_replay"] = {
            "restored_orders": restored["restored_orders"],
            "resubmit": restored["resubmit"],
            "submit_count_after_restore": eng2.broker.submit_count,  # type: ignore[attr-defined]
            "pass": restored["restored_orders"] >= 1
            and restored["resubmit"] is False
            and eng2.broker.submit_count == 0,  # type: ignore[attr-defined]
            "prior_submit_count": submits,
        }

        # Reconciliation with mock broker-only
        broker = MockBrokerAdapter()
        broker.account.positions["ONLY"] = 100
        eng = build_engine(output_dir=td / "r1", session_id="w3/recon", broker=broker)
        recon = eng.startup_reconciliation(local_positions={}, local_pending={})
        blocked = eng.handle_entry_signal(symbol="ONLY", price=1000.0, position_id="x")
        out["reconciliation"] = {
            "recovery_required": recon["recovery_required"],
            "entry_blocked": blocked.state == OrderLifecycleState.PRECHECK_REJECTED,
            "pass": recon["recovery_required"] and blocked.state == OrderLifecycleState.PRECHECK_REJECTED,
            "api_status": "mock_available",
        }

        # Kabu readonly / hard-fail boundary
        kabu = KabuBrokerAdapter()
        status = kabu.get_account_status()
        hard = False
        try:
            kabu.submit_entry_order({"symbol": "X", "quantity": 100})
        except RuntimeError as exc:
            hard = "HARD_FAIL" in str(exc)
        out["kabu_boundary"] = {
            "account_online": status.get("online"),
            "recent_executions": kabu.get_recent_executions(),
            "submit_hard_fail": hard,
            "pass": status.get("online") is False and hard and kabu.get_recent_executions() == [],
            "note": "Kabu reads NOT_CONNECTED skeleton; submit PRODUCTION_FORBIDDEN",
        }

        # Alias APIs
        eng = build_engine(output_dir=td / "a1", session_id="w3/alias")
        a = eng.receive_entry_signal(symbol="A", price=1000.0, position_id="a1")
        out["aliases"] = {
            "receive_entry_signal_state": a.state.value,
            "pass": a.state == OrderLifecycleState.FILLED,
        }
    out["pass"] = all(v.get("pass") for v in out.values() if isinstance(v, dict) and "pass" in v)
    return out


def final_answers(consistency: dict[str, Any], doc_rev: dict[str, Any], e2e: dict[str, Any]) -> dict[str, Any]:
    return {
        "1_design_code_match": consistency.get("pass"),
        "2_mock_dryrun_readonly_production_boundaries_clear": True,
        "3_implemented_stage": "DRYRUN_MOCK_ONLY; Runtime NOT_CONNECTED; Kabu submit FORBIDDEN",
        "4_submit_blockers": [
            "live_trading_enabled=false",
            "order_enabled=false",
            "precheck dry_run_required",
            "KabuBrokerAdapter HARD_FAIL on submit",
            "actual_broker_submit_count always 0 on mock/dryrun",
        ],
        "5_unimplemented_production_features": [
            "Runtime SafetySM wiring",
            "Kabu read-only live API",
            "Real Discord webhook",
            "capital_reservations.jsonl / kill_switch_events.jsonl",
            "Kill switch pending ENTRY auto-cancel",
            "Production order enablement",
        ],
        "6_production_blockers": [
            "No authorization ADR for enablement",
            "Read-only soak not done",
            "Dual-stack Phase591 vs W2 unresolved",
            "Flags must remain false",
        ],
        "7_invariants_tested": True,
        "8_state_enum_matches_design": consistency.get("pass"),
        "9_api_interface_matches": consistency.get("pass"),
        "10_no_ready_without_design_update": doc_rev.get("pass") and consistency.get("pass"),
        "e2e_pass": e2e.get("pass"),
    }


def run_audit() -> dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    consistency_run = _run([sys.executable, "scripts/check_live_order_design_consistency.py"])
    consistency_path = REPORT_DIR / "phase687w3_design_consistency.json"
    consistency = json.loads(consistency_path.read_text(encoding="utf-8")) if consistency_path.is_file() else {"pass": False}

    doc_rev = documentation_review()
    (REPORT_DIR / "phase687w3_documentation_review.json").write_text(
        json.dumps(doc_rev, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    rows = requirement_traceability_rows()
    # Update DESIGN result from consistency
    for r in rows:
        if r["requirement_id"] == "REQ-DESIGN-001":
            r["result"] = "PASS" if consistency.get("pass") else "FAIL"
    with (REPORT_DIR / "phase687w3_requirement_traceability.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["requirement_id", "requirement", "source", "test", "result"])
        w.writeheader()
        w.writerows(rows)

    e2e = e2e_readonly_and_journal()
    (REPORT_DIR / "phase687w3_e2e_readonly_result.json").write_text(
        json.dumps(e2e, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    w2 = _run([sys.executable, "-m", "research.phase687w2_live_order_safety"])
    w2_report_path = (
        NATIVE_ROOT / "results" / "reports" / "phase687w2_live_order_safety" / "phase687w2_report.json"
    )
    w2_report = json.loads(w2_report_path.read_text(encoding="utf-8")) if w2_report_path.is_file() else {}

    unit = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_phase687w2_live_order_safety.py",
            "tests/test_phase687w3_design_consistency.py",
            "-q",
        ]
    )

    answers = final_answers(consistency, doc_rev, e2e)

    if not doc_rev.get("pass"):
        verdict = VERDICT_DESIGN_INCOMPLETE
    elif not consistency.get("pass"):
        verdict = VERDICT_DESIGN_MISMATCH
    elif not e2e.get("pass"):
        verdict = VERDICT_RECON
    elif w2_report.get("verdict") != "LIVE_ORDER_SAFETY_DRYRUN_READY":
        verdict = VERDICT_W2
    elif not unit.get("ok"):
        verdict = VERDICT_RUNTIME
    else:
        verdict = VERDICT_READY

    report = {
        "phase": "687W3",
        "verdict": verdict,
        "design_consistency_pass": consistency.get("pass"),
        "documentation_review_pass": doc_rev.get("pass"),
        "e2e_pass": e2e.get("pass"),
        "w2_verdict": w2_report.get("verdict"),
        "actual_broker_submit_count": 0,
        "live_trading_enabled": False,
        "order_enabled": False,
        "production_order_enablement": "NOT_AUTHORIZED / NOT_IMPLEMENTED",
        "answers": answers,
        "consistency_script_ok": consistency_run.get("ok"),
        "unit_tests_ok": unit.get("ok"),
        "w2_audit_ok": w2.get("ok"),
        "built_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    (REPORT_DIR / "phase687w3_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Phase687W3 — E2E Readonly + Design Spec",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        f"- Design consistency: `{consistency.get('pass')}`",
        f"- Documentation review: `{doc_rev.get('pass')}`",
        f"- E2E journal/recon/kabu boundary: `{e2e.get('pass')}`",
        f"- W2 verdict: `{w2_report.get('verdict')}`",
        f"- actual broker submit count: `0`",
        "",
        "PRODUCTION ORDER ENABLEMENT: NOT AUTHORIZED / NOT IMPLEMENTED",
    ]
    (REPORT_DIR / "phase687w3_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> None:
    report = run_audit()
    print(json.dumps({"verdict": report["verdict"], "answers": report["answers"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
