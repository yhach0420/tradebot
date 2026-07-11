"""Phase687W6 — Production enablement governance gate audit (no write adapter)."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w6_production_enablement_gate"
DOCS = NATIVE_ROOT / "docs" / "live_trading"
JST = ZoneInfo("Asia/Tokyo")

VERDICT_READY = "PRODUCTION_ENABLEMENT_GATE_READY"
VERDICT_FAIL_CLOSED = "FAIL_CLOSED_GATE_FAILED"
VERDICT_APPROVAL = "APPROVAL_BOUNDARY_FAILED"
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
    from small_paper.production_enablement_gate import (
        ApprovalStatus,
        PRODUCTION_ORDER_ENABLEMENT,
        SCHEMA_VERSION,
        approval_schema_fields,
        blocker_matrix_rows,
        canary_plan_schema,
        empty_evidence,
        evaluate_production_enablement,
        probe_current_workspace,
        sample_approval_artifact_not_authorized,
        technical_pass_evidence_not_authorized,
    )

    smoke = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_phase687w6_production_enablement_gate.py",
            "-q",
            "--tb=line",
        ]
    )
    _wj("phase687w6_smoke_result.json", smoke)

    cfg_path = (
        NATIVE_ROOT
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    cfg = load_pilot_config(cfg_path)
    preflight = {
        "live_trading_enabled": bool(cfg.live_trading_enabled),
        "order_enabled": bool(cfg.order_enabled),
        "production_order_enablement": PRODUCTION_ORDER_ENABLEMENT,
        "write_adapter": "NOT_IMPLEMENTED",
        "canary_execution": "FORBIDDEN",
        "pass": (not cfg.live_trading_enabled) and (not cfg.order_enabled),
        "checked_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _wj("phase687w6_preflight_result.json", preflight)

    approval = sample_approval_artifact_not_authorized(design_schema_version=SCHEMA_VERSION)
    _wj("phase687w6_approval_schema.json", {
        "required_fields": approval_schema_fields(),
        "sample": approval,
        "valid_approval_generated": False,
        "approval_status": ApprovalStatus.NOT_AUTHORIZED.value,
    })

    canary = canary_plan_schema()
    _wj("phase687w6_canary_plan_schema.json", canary)

    _wc("phase687w6_blocker_matrix.csv", blocker_matrix_rows())

    # Fail-closed matrix
    cases = {
        "empty_evidence_blocked": evaluate_production_enablement(empty_evidence())["blocker_count"] > 0,
        "soak_0_of_3_blocked": "W4S_SESSIONS_INSUFFICIENT"
        in {
            b["code"]
            for b in evaluate_production_enablement(
                technical_pass_evidence_not_authorized().__class__(
                    **{
                        **technical_pass_evidence_not_authorized().__dict__,
                        "w4s_session_count": 0,
                    }
                )
            )["blockers"]
        },
        "fixture_blocked": True,
        "mtt_unverified_blocked": True,
        "policy_unselected_blocked": True,
        "approval_missing_blocked": True,
        "sha_mismatch_blocked": True,
        "tech_pass_still_not_authorized": (
            evaluate_production_enablement(technical_pass_evidence_not_authorized())["exit_code"] == 0
            and evaluate_production_enablement(technical_pass_evidence_not_authorized())[
                "production_ready"
            ]
            is False
        ),
    }
    # Explicit fail-closed probes
    from small_paper.production_enablement_gate import ProductionEnablementEvidence

    base = technical_pass_evidence_not_authorized()

    def _with(**kw):
        d = dict(base.__dict__)
        d.update(kw)
        return ProductionEnablementEvidence(**d)

    cases["fixture_blocked"] = (
        "CAPABILITY_FIXTURE_OR_SYNTHETIC_OR_UNKNOWN"
        in {
            b["code"]
            for b in evaluate_production_enablement(
                _with(
                    capability_status="FIXTURE_ONLY",
                    capability_provenance="FIXTURE",
                    live_api_provenance_confirmed=False,
                    margin_trade_type_live_verified=False,
                )
            )["blockers"]
        }
    )
    cases["mtt_unverified_blocked"] = "MARGIN_TRADE_TYPE_NOT_LIVE_VERIFIED" in {
        b["code"]
        for b in evaluate_production_enablement(_with(margin_trade_type_live_verified=False))["blockers"]
    }
    cases["policy_unselected_blocked"] = "EXECUTION_POLICY_NOT_SELECTED" in {
        b["code"]
        for b in evaluate_production_enablement(
            _with(execution_policy_selected=False, approved_execution_policy_id="NOT_SELECTED")
        )["blockers"]
    }
    cases["approval_missing_blocked"] = "APPROVAL_MISSING" in {
        b["code"]
        for b in evaluate_production_enablement(_with(approval_present=False, approval_status=None))[
            "blockers"
        ]
    }
    cases["sha_mismatch_blocked"] = "CONFIG_SHA_MISMATCH" in {
        b["code"]
        for b in evaluate_production_enablement(
            _with(config_sha256="a", expected_config_sha256="b")
        )["blockers"]
    }
    cases["soak_0_of_3_blocked"] = "W4S_SESSIONS_INSUFFICIENT" in {
        b["code"] for b in evaluate_production_enablement(_with(w4s_session_count=0))["blockers"]
    }
    cases["pass"] = all(v for k, v in cases.items() if k != "pass")
    _wj("phase687w6_fail_closed_tests.json", cases)

    # Readiness probe (workspace fail-closed + demo technical pass)
    probe = probe_current_workspace(native_root=NATIVE_ROOT)
    demo = evaluate_production_enablement(technical_pass_evidence_not_authorized())
    cli = _run(
        [
            sys.executable,
            "-m",
            "small_paper.check_production_enablement_readiness",
            "--demo-technical-pass",
        ]
    )
    _wj(
        "phase687w6_readiness_probe.json",
        {
            "workspace_probe": {
                "blocker_count": probe.get("blocker_count"),
                "exit_code": probe.get("exit_code"),
                "production_ready": probe.get("production_ready"),
                "approval_status": probe.get("approval_status"),
            },
            "demo_technical_pass": {
                "exit_code": demo.get("exit_code"),
                "production_ready": demo.get("production_ready"),
                "technical_conditions_pass": demo.get("technical_conditions_pass"),
                "approval_status": demo.get("approval_status"),
            },
            "cli": cli,
            "cli_exit_0_means_not_authorized": demo.get("exit_code") == 0
            and demo.get("production_ready") is False,
            "flags_mutated": False,
            "pass": demo.get("exit_code") == 0 and demo.get("production_ready") is False and cli.get("ok"),
        },
    )

    hard_submit = False
    hard_cancel = False
    try:
        KabuBrokerAdapter().submit_entry_order({"symbol": "X", "quantity": 100})
    except RuntimeError as exc:
        hard_submit = "HARD_FAIL" in str(exc)
    try:
        KabuBrokerAdapter().cancel_order("OID")
    except RuntimeError as exc:
        hard_cancel = "HARD_FAIL" in str(exc)

    gate_src = (NATIVE_ROOT / "src" / "small_paper" / "production_enablement_gate.py").read_text(
        encoding="utf-8"
    )
    cli_src = (NATIVE_ROOT / "src" / "small_paper" / "check_production_enablement_readiness.py").read_text(
        encoding="utf-8"
    )
    network = {
        "submit_hard_fail": hard_submit,
        "cancel_hard_fail": hard_cancel,
        "actual_broker_submit_count": actual_broker_submit_count(),
        "write_adapter_implemented": False,
        "gate_calls_submit": "submit_entry_order" in gate_src and "evaluate_production" in gate_src,
        # probe may confirm HARD_FAIL via submit_entry_order — that is intentional isolation check
        "cli_mutates_flags": "live_trading_enabled = True" in cli_src or "order_enabled = True" in cli_src,
        "pass": hard_submit
        and hard_cancel
        and actual_broker_submit_count() == 0
        and ("live_trading_enabled = True" not in cli_src)
        and ("order_enabled = True" not in cli_src),
    }
    _wj("phase687w6_network_isolation.json", network)

    design = _run([sys.executable, str(NATIVE_ROOT / "scripts" / "check_live_order_design_consistency.py")])
    design_path = (
        NATIVE_ROOT
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    design_payload = json.loads(design_path.read_text(encoding="utf-8")) if design_path.is_file() else {"pass": False}
    _wj("phase687w6_design_consistency.json", design_payload)

    adr = DOCS / "adr" / "ADR-687W6-production-enablement-governance.md"
    doc_rev = {
        "adr_present": adr.is_file(),
        "adr_states_not_authorized": (
            "NOT AUTHORIZED" in adr.read_text(encoding="utf-8").upper()
            or "NOT_AUTHORIZED" in adr.read_text(encoding="utf-8")
        )
        if adr.is_file()
        else False,
        "system_design_mentions_w6": "Phase687W6"
        in (DOCS / "live_order_system_design.md").read_text(encoding="utf-8"),
        "operations_mentions_cli": "check_production_enablement_readiness"
        in (DOCS / "live_order_operations.md").read_text(encoding="utf-8"),
        "pass": False,
    }
    doc_rev["pass"] = (
        doc_rev["adr_present"]
        and doc_rev["adr_states_not_authorized"]
        and doc_rev["system_design_mentions_w6"]
        and doc_rev["operations_mentions_cli"]
    )
    _wj("phase687w6_documentation_review.json", doc_rev)

    readiness_probe = json.loads((REPORT_DIR / "phase687w6_readiness_probe.json").read_text(encoding="utf-8"))

    checks = {
        "smoke": smoke.get("ok", False),
        "preflight": preflight.get("pass", False),
        "fail_closed": cases.get("pass", False),
        "readiness": readiness_probe.get("pass", False),
        "network": network.get("pass", False),
        "design": design_payload.get("pass", False),
        "docs": doc_rev.get("pass", False),
        "approval_boundary": approval["approval_status"] == "NOT_AUTHORIZED"
        and not approval.get("secrets_present"),
        "canary_forbidden": canary.get("canary_execution_forbidden") is True,
        "submit_zero": actual_broker_submit_count() == 0 and hard_submit,
    }

    if not checks["network"] or not checks["submit_zero"]:
        verdict = VERDICT_NETWORK
    elif not checks["approval_boundary"]:
        verdict = VERDICT_APPROVAL
    elif not checks["design"] or not checks["docs"]:
        verdict = VERDICT_DESIGN
    elif not checks["fail_closed"]:
        verdict = VERDICT_FAIL_CLOSED
    elif not all(checks.values()):
        verdict = VERDICT_FAIL_CLOSED
    else:
        verdict = VERDICT_READY

    report = {
        "phase": "687W6",
        "verdict": verdict,
        "checks": checks,
        "production_order_enablement": PRODUCTION_ORDER_ENABLEMENT,
        "production_ready": False,
        "write_adapter": "NOT_IMPLEMENTED",
        "live_trading_enabled": False,
        "order_enabled": False,
        "note": "READY means governance gate complete — not order authorization or enablement",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _wj("phase687w6_report.json", report)
    (REPORT_DIR / "phase687w6_decision.md").write_text(
        f"""# Phase687W6 Decision

**Verdict:** `{verdict}`

## Meaning of READY
Governance gate is complete and fail-closed. This does **not** authorize, implement, or enable real orders.

## PRODUCTION ORDER ENABLEMENT
**NOT AUTHORIZED / NOT IMPLEMENTED**

## Absolute gates
- live_trading_enabled=false
- order_enabled=false
- Kabu write methods remain HARD_FAIL
- No production write adapter
- Canary execution forbidden
- Valid APPROVED approval artifact not generated in this phase

## CLI
`python -m small_paper.check_production_enablement_readiness`

Exit 0 = technical conditions pass but still NOT_AUTHORIZED (orders remain disabled).
""",
        encoding="utf-8",
    )
    print(json.dumps({"verdict": verdict, "checks": checks}, indent=2))
    return 0 if verdict == VERDICT_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
