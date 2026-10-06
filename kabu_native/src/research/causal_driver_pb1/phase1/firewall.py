"""Phase 1 ingest firewall overlay. Does not mutate Phase 0 enums or PAYLOAD_ALLOW."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.contracts.enums import AccessKind, DatasetRole, RunMode
from research.causal_driver_pb1.contracts.errors import FirewallDenied
from research.causal_driver_pb1.datasets.firewall import AccessLedger, firewall_config_sha256, request_dataset
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase1 import PHASE1_FIREWALL_RUN_MODE, PHASE1_RUN_MODE_LABEL
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed, ingest_date_allowed


def phase1_firewall_overlay_sha256() -> str:
    return sha256_obj(
        {
            "phase1_run_mode_label": PHASE1_RUN_MODE_LABEL,
            "maps_to_phase0_run_mode": PHASE1_FIREWALL_RUN_MODE,
            "reason": (
                "Phase 0 RunMode enum is frozen. DRIVER_INGEST is a Phase 1 adapter-build alias "
                "that reuses DRIVER_DISCOVERY payload allowlist (DEVELOPMENT + ECONOMIC_DEVELOPMENT_EXPOSED only)."
            ),
            "allowed_roles": ["DEVELOPMENT", "ECONOMIC_DEVELOPMENT_EXPOSED"],
            "denied_roles": ["FROZEN_VALIDATION", "PROSPECTIVE", "RUNTIME_CAPTURE", "SYNTHETIC_TEST"],
            "economic_payload": "DENY",
            "silent_source_substitute": False,
            "phase0_firewall_config_sha256": firewall_config_sha256(),
        }
    )


def phase1_run_mode() -> RunMode:
    if PHASE1_FIREWALL_RUN_MODE != RunMode.DRIVER_DISCOVERY.value:
        raise FirewallDenied("phase1_firewall_run_mode_must_map_to_driver_discovery")
    return RunMode.DRIVER_DISCOVERY


def request_usdjpy_ingest(
    *,
    ledger: AccessLedger,
    session_yyyymmdd: str,
    dataset_id: str = "USDJPY_1M_JETTA",
) -> dict[str, Any]:
    role = assert_ingest_date_allowed(session_yyyymmdd)
    decision = request_dataset(
        ledger=ledger,
        run_mode=phase1_run_mode(),
        dataset_id=f"{dataset_id}:{session_yyyymmdd}",
        dataset_role=role,
        requested_fields=("bid_ohlc", "ask_ohlc", "bar_start", "available_at"),
        access_kind=AccessKind.PAYLOAD,
    )
    return {"allowed": decision.allowed, "role": role.value, "reason": decision.reason, "date": session_yyyymmdd}


def prove_restricted_dates(ledger: AccessLedger) -> dict[str, Any]:
    denied: list[dict[str, Any]] = []
    for day, expect in (("20260422", "FROZEN_VALIDATION_USDJPY_DENIED"), ("20260924", "PROSPECTIVE_USDJPY_DENIED")):
        try:
            request_usdjpy_ingest(ledger=ledger, session_yyyymmdd=day)
            denied.append({"date": day, "pass": False, "reason": "expected_deny_but_allowed"})
        except FirewallDenied as exc:
            denied.append({"date": day, "pass": True, "reason": str(exc), "expect": expect})
        if ingest_date_allowed(day):
            denied[-1]["pass"] = False
            denied[-1]["reason"] = "ingest_date_allowed_true"
    fv_payload_denied = False
    try:
        request_dataset(
            ledger=ledger,
            run_mode=phase1_run_mode(),
            dataset_id="USDJPY_FV_PROBE",
            dataset_role=DatasetRole.FROZEN_VALIDATION,
            requested_fields=("bid_ohlc",),
            access_kind=AccessKind.PAYLOAD,
        )
    except FirewallDenied:
        fv_payload_denied = True
    prospective_payload_denied = False
    try:
        request_dataset(
            ledger=ledger,
            run_mode=phase1_run_mode(),
            dataset_id="USDJPY_PROSPECTIVE_PROBE",
            dataset_role=DatasetRole.PROSPECTIVE,
            requested_fields=("bid_ohlc",),
            access_kind=AccessKind.PAYLOAD,
        )
    except FirewallDenied:
        prospective_payload_denied = True
    return {
        "restricted_date_rows": denied,
        "restricted_dates_pass": all(r.get("pass") for r in denied),
        "fv_payload_denied": fv_payload_denied,
        "prospective_payload_denied": prospective_payload_denied,
        "economic_payload_not_requested": True,
        "pass": all(r.get("pass") for r in denied) and fv_payload_denied and prospective_payload_denied,
    }
