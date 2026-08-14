#!/usr/bin/env python3
"""Check live-order design schema against code (Phase687W3).

Exit code 0 = PASS, 1 = FAIL (design/code mismatch).
Writes results/reports/phase687w3_e2e_readonly_reconciliation/phase687w3_design_consistency.json
when --write is passed (default).
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from pathlib import Path
from typing import Any

NATIVE_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = NATIVE_ROOT / "docs" / "live_trading" / "schema" / "live_order_design_schema.json"
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w3_e2e_readonly_reconciliation"


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def check() -> dict[str, Any]:
    # Ensure src importable
    src = str(NATIVE_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    repo = str(NATIVE_ROOT.parent)
    if repo not in sys.path:
        sys.path.insert(0, repo)

    from small_paper.live_order_error_codes import ERROR_CODE_PREFIXES
    from small_paper.live_order_safety_sm import (
        AppendOnlyStore,
        BrokerAdapter,
        KabuBrokerAdapter,
        LiveOrderSafetyEngine,
        OrderLifecycleState,
    )
    from small_paper.config import load_pilot_config

    schema = _load_schema()
    mismatches: list[dict[str, Any]] = []

    code_states = [s.value for s in OrderLifecycleState]
    if code_states != schema["order_lifecycle_states"]:
        mismatches.append(
            {
                "check": "order_lifecycle_states",
                "schema": schema["order_lifecycle_states"],
                "code": code_states,
            }
        )

    broker_methods = sorted(
        m for m, _ in inspect.getmembers(BrokerAdapter, predicate=inspect.isfunction) if not m.startswith("_")
    )
    schema_broker = sorted(schema["broker_adapter_methods"])
    if broker_methods != schema_broker:
        mismatches.append(
            {
                "check": "broker_adapter_methods",
                "schema": schema_broker,
                "code": broker_methods,
            }
        )

    engine_methods = {
        m
        for m, _ in inspect.getmembers(LiveOrderSafetyEngine, predicate=inspect.isfunction)
        if not m.startswith("_")
    }
    for m in schema["safety_engine_methods"]["IMPLEMENTED_DRYRUN"]:
        if m not in engine_methods:
            mismatches.append({"check": "engine_method_missing", "method": m})
    for m in schema["safety_engine_methods"].get("NOT_IMPLEMENTED", []):
        if m in engine_methods:
            mismatches.append({"check": "engine_method_marked_not_implemented_but_exists", "method": m})

    # Journal filenames from AppendOnlyStore source
    store_src = Path(inspect.getsourcefile(AppendOnlyStore) or "")
    text = store_src.read_text(encoding="utf-8") if store_src.is_file() else ""
    for name in schema["journal_files"]["IMPLEMENTED_DRYRUN_APPEND_ONLY"]:
        if f'"{name}"' not in text and f"'{name}'" not in text:
            mismatches.append({"check": "journal_file_missing_in_code", "file": name})
    for name in schema["journal_files"]["NOT_IMPLEMENTED"]:
        if f'"{name}"' in text or f"'{name}'" in text:
            mismatches.append({"check": "journal_marked_not_implemented_but_in_code", "file": name})

    if list(ERROR_CODE_PREFIXES) != list(schema["error_code_prefixes"]):
        mismatches.append(
            {
                "check": "error_code_prefixes",
                "schema": schema["error_code_prefixes"],
                "code": list(ERROR_CODE_PREFIXES),
            }
        )

    flags = schema["config_flags"]
    cfg_path = (
        NATIVE_ROOT
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    cfg = load_pilot_config(cfg_path)
    if bool(cfg.live_trading_enabled) != bool(flags["live_trading_enabled"]):
        mismatches.append(
            {
                "check": "live_trading_enabled",
                "schema": flags["live_trading_enabled"],
                "config": cfg.live_trading_enabled,
            }
        )
    if bool(cfg.order_enabled) != bool(flags["order_enabled"]):
        mismatches.append(
            {
                "check": "order_enabled",
                "schema": flags["order_enabled"],
                "config": cfg.order_enabled,
            }
        )
    if "live_order_safety_sm_enabled" in flags:
        if bool(cfg.live_order_safety_sm_enabled) != bool(flags["live_order_safety_sm_enabled"]):
            mismatches.append(
                {
                    "check": "live_order_safety_sm_enabled",
                    "schema": flags["live_order_safety_sm_enabled"],
                    "config": cfg.live_order_safety_sm_enabled,
                }
            )

    # Kabu hard-fail (submit + cancel)
    hard_fail_ok = False
    cancel_hard_fail_ok = False
    try:
        KabuBrokerAdapter().submit_entry_order({"symbol": "X", "quantity": 100})
    except RuntimeError as exc:
        hard_fail_ok = "HARD_FAIL" in str(exc)
    try:
        KabuBrokerAdapter().cancel_order("x")
    except RuntimeError as exc:
        cancel_hard_fail_ok = "HARD_FAIL" in str(exc)
    if schema.get("kabu_submit_hard_fail") and not hard_fail_ok:
        mismatches.append({"check": "kabu_submit_hard_fail", "ok": False})
    if not cancel_hard_fail_ok:
        mismatches.append({"check": "kabu_cancel_hard_fail", "ok": False})

    # Required docs exist
    docs = [
        NATIVE_ROOT / "docs" / "live_trading" / "live_order_system_design.md",
        NATIVE_ROOT / "docs" / "live_trading" / "live_order_interface_spec.md",
        NATIVE_ROOT / "docs" / "live_trading" / "live_order_data_spec.md",
        NATIVE_ROOT / "docs" / "live_trading" / "live_order_operations.md",
        NATIVE_ROOT / "docs" / "live_trading" / "live_order_test_traceability.md",
        NATIVE_ROOT / "docs" / "live_trading" / "adr" / "ADR-687W2-order-safety-state-machine.md",
        NATIVE_ROOT / "docs" / "live_trading" / "adr" / "ADR-687W3-e2e-readonly-reconciliation.md",
        NATIVE_ROOT / "docs" / "live_trading" / "adr" / "ADR-687W4-runtime-dryrun-readonly.md",
        NATIVE_ROOT / "docs" / "live_trading" / "adr" / "ADR-687W4T-kabu-token-readonly-readiness.md",
        NATIVE_ROOT / "docs" / "live_trading" / "adr" / "ADR-687W5-kabu-order-request-contract.md",
        NATIVE_ROOT / "docs" / "live_trading" / "adr" / "ADR-687W5A-official-sendorder-contract-reconciliation.md",
        NATIVE_ROOT / "docs" / "live_trading" / "adr" / "ADR-687W5B-account-capability-execution-policy-shadow.md",
    ]
    for p in docs:
        if not p.is_file():
            mismatches.append({"check": "missing_doc", "path": str(p)})

    # Runtime hooks (Phase687W4)
    for hook in schema.get("runtime_hooks") or []:
        pilot = (NATIVE_ROOT / "src" / "small_paper" / "pilot_runner.py").read_text(encoding="utf-8")
        if hook not in pilot:
            mismatches.append({"check": "runtime_hook_missing", "hook": hook})

    # Account status enum
    if schema.get("account_status_enum"):
        from small_paper.live_order_account_status import AccountReadStatus

        code_acct = [s.value for s in AccountReadStatus]
        if code_acct != schema["account_status_enum"]:
            mismatches.append(
                {
                    "check": "account_status_enum",
                    "schema": schema["account_status_enum"],
                    "code": code_acct,
                }
            )

    # Latency fields
    if schema.get("latency_fields"):
        from small_paper.live_order_runtime_bridge import LATENCY_FIELDS

        if list(LATENCY_FIELDS) != list(schema["latency_fields"]):
            mismatches.append(
                {
                    "check": "latency_fields",
                    "schema": schema["latency_fields"],
                    "code": list(LATENCY_FIELDS),
                }
            )

    # Phase687W4T readiness
    if schema.get("readiness_cli"):
        cli = NATIVE_ROOT / "src" / "small_paper" / "check_kabu_readonly_readiness.py"
        if not cli.is_file():
            mismatches.append({"check": "readiness_cli_missing"})
    if schema.get("token_probe_statuses"):
        from small_paper.kabu_readonly_readiness import TOKEN_MAX_RETRIES, TokenProbeStatus

        code_tp = [s.value for s in TokenProbeStatus]
        if code_tp != schema["token_probe_statuses"]:
            mismatches.append({"check": "token_probe_statuses", "schema": schema["token_probe_statuses"], "code": code_tp})
        if schema.get("token_retry_max") != TOKEN_MAX_RETRIES:
            mismatches.append({"check": "token_retry_max"})

    # Phase687W5 order request contract
    if schema.get("order_request_builder_class"):
        from small_paper.kabu_order_request_builder import (
            BUILDER_VERSION,
            REQUEST_MUTATION_DETECTED,
            REQUEST_SCHEMA_VERSION,
            OrderRequestBuilder,
            actual_broker_submit_count,
        )
        from small_paper.kabu_order_response_parser import OrderResponseParser
        from small_paper.kabu_order_execution_policy import EXECUTION_POLICY_IDS, ExecutionPolicy

        if OrderRequestBuilder.__name__ != schema["order_request_builder_class"]:
            mismatches.append({"check": "order_request_builder_class"})
        if OrderResponseParser.__name__ != schema.get("order_response_parser_class"):
            mismatches.append({"check": "order_response_parser_class"})
        if REQUEST_SCHEMA_VERSION != schema.get("request_schema_version"):
            mismatches.append(
                {
                    "check": "request_schema_version",
                    "schema": schema.get("request_schema_version"),
                    "code": REQUEST_SCHEMA_VERSION,
                }
            )
        if BUILDER_VERSION != schema.get("builder_version"):
            mismatches.append({"check": "builder_version"})
        if list(EXECUTION_POLICY_IDS) != list(schema.get("execution_policy_ids") or []):
            mismatches.append(
                {
                    "check": "execution_policy_ids",
                    "schema": schema.get("execution_policy_ids"),
                    "code": list(EXECUTION_POLICY_IDS),
                }
            )
        if schema.get("mutation_error_code") != REQUEST_MUTATION_DETECTED:
            mismatches.append({"check": "mutation_error_code"})
        if schema.get("execution_policy_production_authorized") is not False:
            mismatches.append({"check": "execution_policy_production_authorized"})
        if ExecutionPolicy().production_authorized is not False:
            mismatches.append({"check": "default_policy_production_authorized"})
        if schema.get("network_isolation") and actual_broker_submit_count() != 0:
            mismatches.append({"check": "network_isolation_submit_count"})
        builder_path = NATIVE_ROOT / "src" / "small_paper" / "kabu_order_request_builder.py"
        btxt = builder_path.read_text(encoding="utf-8") if builder_path.is_file() else ""
        for banned in ("requests.post", "import requests", "submit_entry_order", "emergency_flatten"):
            if banned in btxt:
                mismatches.append({"check": "network_isolation_source", "banned": banned})
        for fld in ("request_fingerprint", "canonical_payload_hash", "schema_version", "builder_version"):
            if fld not in btxt:
                mismatches.append({"check": "fingerprint_field_missing", "field": fld})

        if schema.get("exchange_policy_ids"):
            from small_paper.kabu_sendorder_contract import ExchangePolicy

            code_ep = [e.value for e in ExchangePolicy]
            if code_ep != list(schema["exchange_policy_ids"]):
                mismatches.append(
                    {
                        "check": "exchange_policy_ids",
                        "schema": schema["exchange_policy_ids"],
                        "code": code_ep,
                    }
                )
        if schema.get("normal_new_exchange_tse") != "FORBIDDEN":
            mismatches.append({"check": "normal_new_exchange_tse"})
        snap = NATIVE_ROOT / "docs" / "live_trading" / "vendor" / "kabusapi_sendorder_contract.json"
        if schema.get("official_sendorder_snapshot") and not snap.is_file():
            mismatches.append({"check": "official_sendorder_snapshot_missing"})

        # Phase687W5B modules
        if schema.get("account_capability_module"):
            from small_paper.kabu_account_capability import CapabilityProvenance, CapabilityStatus
            from small_paper.kabu_close_policy import ClosePolicyId

            if list(schema.get("capability_statuses") or []) != [e.value for e in CapabilityStatus]:
                mismatches.append({"check": "capability_statuses"})
            if schema.get("capability_provenances") and list(
                schema.get("capability_provenances") or []
            ) != [e.value for e in CapabilityProvenance]:
                mismatches.append({"check": "capability_provenances"})
            if list(schema.get("close_policy_ids") or []) != [e.value for e in ClosePolicyId]:
                mismatches.append({"check": "close_policy_ids"})
            for mod in (
                "kabu_account_capability.py",
                "kabu_position_identity.py",
                "kabu_close_policy.py",
                "kabu_execution_policy_shadow.py",
            ):
                if not (NATIVE_ROOT / "src" / "small_paper" / mod).is_file():
                    mismatches.append({"check": "w5b_module_missing", "module": mod})
            bridge_txt = (NATIVE_ROOT / "src" / "small_paper" / "live_order_runtime_bridge.py").read_text(
                encoding="utf-8"
            )
            for fld in schema.get("soak_snapshot_fields_w5b") or []:
                if fld not in bridge_txt:
                    mismatches.append({"check": "soak_w5b_field_missing", "field": fld})
            for fld in schema.get("soak_snapshot_fields_w5b1") or []:
                if fld not in bridge_txt:
                    mismatches.append({"check": "soak_w5b1_field_missing", "field": fld})
            if schema.get("production_policy_selection") != "NOT_IMPLEMENTED":
                mismatches.append({"check": "production_policy_selection"})
            if schema.get("w4s_min_sessions_before_policy_selection") != 3:
                mismatches.append({"check": "w4s_min_sessions_before_policy_selection"})

        # Phase687W6 production enablement gate
        if schema.get("production_enablement_gate_module"):
            from small_paper.production_enablement_gate import (
                ApprovalStatus,
                PRODUCTION_ORDER_ENABLEMENT,
                SCHEMA_VERSION as GATE_SCHEMA_VERSION,
                approval_schema_fields,
                canary_plan_schema,
            )

            gate_mod = NATIVE_ROOT / "src" / "small_paper" / "production_enablement_gate.py"
            cli_mod = NATIVE_ROOT / "src" / "small_paper" / "check_production_enablement_readiness.py"
            if not gate_mod.is_file():
                mismatches.append({"check": "production_enablement_gate_module_missing"})
            if not cli_mod.is_file():
                mismatches.append({"check": "production_enablement_cli_missing"})
            if schema.get("production_order_enablement") != "NOT_AUTHORIZED / NOT_IMPLEMENTED":
                mismatches.append({"check": "production_order_enablement"})
            if PRODUCTION_ORDER_ENABLEMENT != "NOT_AUTHORIZED / NOT_IMPLEMENTED":
                mismatches.append({"check": "gate_production_order_enablement_const"})
            if schema.get("production_write_adapter") != "NOT_IMPLEMENTED":
                mismatches.append({"check": "production_write_adapter"})
            if schema.get("canary_execution") != "FORBIDDEN":
                mismatches.append({"check": "canary_execution"})
            if not canary_plan_schema().get("canary_execution_forbidden"):
                mismatches.append({"check": "canary_execution_forbidden_flag"})
            if schema.get("approval_statuses") and list(schema["approval_statuses"]) != [
                e.value for e in ApprovalStatus
            ]:
                mismatches.append({"check": "approval_statuses"})
            if schema.get("approval_artifact_required_fields") and list(
                schema["approval_artifact_required_fields"]
            ) != approval_schema_fields():
                mismatches.append({"check": "approval_artifact_required_fields"})
            if schema.get("w4s_min_sessions_before_production_enablement") != 3:
                mismatches.append({"check": "w4s_min_sessions_before_production_enablement"})
            exit_map = schema.get("production_enablement_exit_codes") or {}
            expected_exits = {
                "TECH_PASS_NOT_AUTHORIZED": 0,
                "SOAK_INSUFFICIENT": 2,
                "CAPABILITY_POLICY_INSUFFICIENT": 3,
                "RECON_SAFETY_FAILED": 4,
                "DESIGN_CONFIG_MISMATCH": 5,
            }
            if exit_map != expected_exits:
                mismatches.append({"check": "production_enablement_exit_codes", "schema": exit_map})
            # W6 gate module keeps its own SCHEMA_VERSION; design schema may advance to W7+
            if not str(schema.get("schema_version") or "").startswith(("687W6", "687W7")):
                mismatches.append({"check": "schema_version_not_w6_or_w7"})

        # Phase687W7 operational recovery
        if schema.get("operational_recovery_module"):
            from small_paper.operational_recovery import (
                SCHEMA_VERSION as REC_SCHEMA_VERSION,
                RecoveryMode,
                JournalIntegrityStatus,
                OperatorAckStatus,
                DISK_WARNING_PCT,
                DISK_CRITICAL_PCT,
                DISK_HARD_STOP_PCT,
            )

            rec_mod = NATIVE_ROOT / "src" / "small_paper" / "operational_recovery.py"
            rec_cli = NATIVE_ROOT / "src" / "small_paper" / "check_live_order_recovery_readiness.py"
            if not rec_mod.is_file():
                mismatches.append({"check": "operational_recovery_module_missing"})
            if not rec_cli.is_file():
                mismatches.append({"check": "operational_recovery_cli_missing"})
            if list(schema.get("recovery_modes") or []) != [e.value for e in RecoveryMode]:
                mismatches.append({"check": "recovery_modes"})
            if list(schema.get("journal_integrity_statuses") or []) != [
                e.value for e in JournalIntegrityStatus
            ]:
                mismatches.append({"check": "journal_integrity_statuses"})
            if list(schema.get("operator_ack_statuses") or []) != [e.value for e in OperatorAckStatus]:
                mismatches.append({"check": "operator_ack_statuses"})
            exit_r = schema.get("recovery_readiness_exit_codes") or {}
            expected_r = {
                "DRYRUN_RECOVERY_READY": 0,
                "JOURNAL_RECON_ISSUE": 2,
                "KILL_SWITCH_ACK_ISSUE": 3,
                "DISK_CLOCK_ISSUE": 4,
                "DESIGN_CONFIG_MISMATCH": 5,
            }
            if exit_r != expected_r:
                mismatches.append({"check": "recovery_readiness_exit_codes", "schema": exit_r})
            thr = schema.get("disk_thresholds_pct") or {}
            if thr.get("warning") != DISK_WARNING_PCT or thr.get("critical") != DISK_CRITICAL_PCT or thr.get("hard_stop") != DISK_HARD_STOP_PCT:
                mismatches.append({"check": "disk_thresholds_pct"})
            # operational_recovery may remain on 687W7.x while design advances to 687W7A.x
            if not str(REC_SCHEMA_VERSION).startswith("687W7"):
                mismatches.append({"check": "recovery_module_schema_not_w7"})
            if not str(schema.get("schema_version") or "").startswith(("687W7", "687W7A")):
                mismatches.append({"check": "schema_version_not_w7_family"})
            bridge_or_pilot = (
                NATIVE_ROOT / "src" / "small_paper" / "pilot_runner.py"
            ).read_text(encoding="utf-8")
            if "create_session_manifest" not in bridge_or_pilot:
                mismatches.append({"check": "pilot_session_manifest_hook_missing"})
            if (
                "write_session_seal" not in bridge_or_pilot
                and "write_full_session_seal" not in bridge_or_pilot
                and "finalize_session_seal_propagation" not in bridge_or_pilot
            ):
                mismatches.append({"check": "pilot_session_seal_hook_missing"})

        # Phase687W7A stateful recovery
        if schema.get("stateful_journal_recovery_module"):
            from small_paper.stateful_journal_recovery import (
                SCHEMA_VERSION as W7A_SCHEMA,
                REQUIRED_SEAL_ARTIFACTS,
                soak_w7a_fields,
            )

            mod = NATIVE_ROOT / "src" / "small_paper" / "stateful_journal_recovery.py"
            if not mod.is_file():
                mismatches.append({"check": "stateful_journal_recovery_module_missing"})
            if W7A_SCHEMA != schema.get("schema_version"):
                # allow W7A1 design schema while stateful module tracks same family
                if not str(schema.get("schema_version") or "").startswith("687W7A"):
                    mismatches.append(
                        {
                            "check": "w7a_schema_version_mismatch",
                            "schema": schema.get("schema_version"),
                            "module": W7A_SCHEMA,
                        }
                    )
            if schema.get("implementation_stage") not in (
                "STATEFUL_RECOVERY_PROOF_READY",
                "RECOVERY_ASSERTION_INTEGRITY_FIXED",
                "W4S_SEAL_PROPAGATION_FIXED",
            ):
                mismatches.append({"check": "implementation_stage_w7a"})
            if list(schema.get("session_seal_required_artifacts") or []) != list(REQUIRED_SEAL_ARTIFACTS):
                mismatches.append({"check": "session_seal_required_artifacts"})
            bridge_txt = (NATIVE_ROOT / "src" / "small_paper" / "live_order_runtime_bridge.py").read_text(
                encoding="utf-8"
            )
            if "restore_from_journal" not in bridge_txt:
                mismatches.append({"check": "bridge_restore_hook_missing"})
            for fld in schema.get("soak_snapshot_fields_w7a") or []:
                if fld not in bridge_txt and fld not in str(soak_w7a_fields()):
                    mismatches.append({"check": "soak_w7a_field_missing", "field": fld})
            for fld in schema.get("soak_snapshot_fields_w7a1") or []:
                if fld not in bridge_txt and fld not in str(soak_w7a_fields()):
                    mismatches.append({"check": "soak_w7a1_field_missing", "field": fld})
            for fld in schema.get("soak_snapshot_fields_w7a2") or []:
                if fld not in bridge_txt and fld not in str(soak_w7a_fields()):
                    mismatches.append({"check": "soak_w7a2_field_missing", "field": fld})
            pilot_txt = (NATIVE_ROOT / "src" / "small_paper" / "pilot_runner.py").read_text(encoding="utf-8")
            if "finalize_session_seal_propagation" not in pilot_txt and "write_full_session_seal" not in pilot_txt:
                mismatches.append({"check": "pilot_full_seal_hook_missing"})
            if "resolve_git_commit" not in pilot_txt:
                mismatches.append({"check": "pilot_git_commit_hook_missing"})
            if schema.get("recovery_assertion_oracle_module"):
                from small_paper.recovery_assertion_oracle import (
                    CAPITAL_RESERVED_SEMANTICS,
                    KILL_SWITCH_RESERVATION_POLICY,
                    TEST_ORACLE_VERSION,
                )

                oracle_mod = NATIVE_ROOT / "src" / "small_paper" / "recovery_assertion_oracle.py"
                if not oracle_mod.is_file():
                    mismatches.append({"check": "recovery_assertion_oracle_missing"})
                if schema.get("test_oracle_version") != TEST_ORACLE_VERSION:
                    mismatches.append({"check": "test_oracle_version"})
                if schema.get("capital_reserved_semantics") != {
                    "expected_intent_count": CAPITAL_RESERVED_SEMANTICS["expected_intent_count"],
                    "expected_order_aggregate_count": CAPITAL_RESERVED_SEMANTICS["expected_order_aggregate_count"],
                    "expected_active_reservation_count": CAPITAL_RESERVED_SEMANTICS[
                        "expected_active_reservation_count"
                    ],
                }:
                    mismatches.append({"check": "capital_reserved_semantics"})
                if (schema.get("kill_switch_reservation_policy") or {}).get("kill_switch_active") != "HOLD_UNTIL_OPERATOR":
                    mismatches.append({"check": "kill_switch_reservation_policy"})
                if KILL_SWITCH_RESERVATION_POLICY.get("policy_letter") != "A":
                    mismatches.append({"check": "kill_switch_policy_letter"})
            if schema.get("w4s_seal_propagation_module"):
                from small_paper.w4s_seal_propagation import SEAL_PROPAGATION_VERSION

                prop_mod = NATIVE_ROOT / "src" / "small_paper" / "w4s_seal_propagation.py"
                if not prop_mod.is_file():
                    mismatches.append({"check": "w4s_seal_propagation_missing"})
                if schema.get("seal_propagation_version") != SEAL_PROPAGATION_VERSION:
                    mismatches.append({"check": "seal_propagation_version"})
                if "finalize_session_seal_propagation" not in pilot_txt:
                    mismatches.append({"check": "pilot_seal_propagation_hook_missing"})

    if schema.get("readiness_station_fields"):
        from small_paper.kabu_readonly_readiness import TokenDiagnostics

        for fld in schema["readiness_station_fields"]:
            if not hasattr(TokenDiagnostics, fld):
                mismatches.append({"check": "readiness_station_field_missing", "field": fld})

    for adr_name in schema.get("adr_required") or []:
        adr_path = NATIVE_ROOT / "docs" / "live_trading" / "adr" / adr_name
        if not adr_path.is_file():
            mismatches.append({"check": "missing_adr", "path": str(adr_path)})

    # Invariant IDs present in system design
    design = (NATIVE_ROOT / "docs" / "live_trading" / "live_order_system_design.md").read_text(
        encoding="utf-8"
    )
    for inv in schema["invariants"]:
        if inv not in design:
            mismatches.append({"check": "invariant_missing_in_design", "id": inv})

    result = {
        "pass": len(mismatches) == 0,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "schema_version": schema.get("schema_version"),
        "implementation_stage": schema.get("implementation_stage"),
        "production_order_enablement": schema.get("production_order_enablement"),
        "code_states": code_states,
        "broker_methods": broker_methods,
        "live_trading_enabled": bool(cfg.live_trading_enabled),
        "order_enabled": bool(cfg.order_enabled),
        "live_order_safety_sm_enabled": bool(cfg.live_order_safety_sm_enabled),
        "kabu_hard_fail": hard_fail_ok,
        "kabu_cancel_hard_fail": cancel_hard_fail_ok,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", default=True)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    result = check()
    if args.write and not args.no_write:
        try:
            from small_paper.derived_artifact_contract import stamp_derived_artifact

            stamp_derived_artifact(
                result,
                artifact_type="design_consistency",
                native_root=NATIVE_ROOT,
                producer="check_live_order_design_consistency",
            )
        except Exception:
            pass
    if args.write and not args.no_write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORT_DIR / "phase687w3_design_consistency.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        w4 = NATIVE_ROOT / "results" / "reports" / "phase687w4_runtime_readonly_latency"
        w4.mkdir(parents=True, exist_ok=True)
        (w4 / "phase687w4_design_consistency.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        w5 = NATIVE_ROOT / "results" / "reports" / "phase687w5_kabu_order_contract"
        w5.mkdir(parents=True, exist_ok=True)
        (w5 / "phase687w5_design_consistency.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {out}")
        print(f"wrote {w4 / 'phase687w4_design_consistency.json'}")
        print(f"wrote {w5 / 'phase687w5_design_consistency.json'}")
    print(json.dumps({"pass": result["pass"], "mismatch_count": result["mismatch_count"]}, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())