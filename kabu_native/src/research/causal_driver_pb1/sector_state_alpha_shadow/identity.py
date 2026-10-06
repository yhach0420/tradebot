"""Bind the frozen precommit, the 30-name driver, and the M3 targets."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_bytes, sha256_obj
from research.causal_driver_pb1.sector_state_alpha_precommit.bind import SECTOR_BOUNDARY_SHA, SECTOR_Q, bind
from research.causal_driver_pb1.sector_state_alpha_precommit.contract import contract
from research.causal_driver_pb1.sector_state_alpha_shadow import (
    CANDIDATE_LIST_SHA256,
    M3_TARGET_SHA256,
    M3_TARGETS,
    PRECOMMIT_ID,
    PRECOMMIT_SHA256,
    Q60_OFF,
    Q80_ON,
    VALIDATED_TRANSMISSION_SHA256,
)
from research.causal_driver_pb1.sector_state_alpha_shadow.machine import SCHEMA_FIELDS
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1 import SECTOR3650_TARGET_SET_SHA256


def verify() -> dict[str, Any]:
    blockers: list[str] = []
    bound = bind()
    frozen = contract(bound)
    if frozen.get("precommit_sha256") != PRECOMMIT_SHA256 or not bound.get("pass"):
        blockers.append("PRECOMMIT_SHA_MISMATCH")
    if frozen.get("precommit_id") != PRECOMMIT_ID:
        blockers.append("PRECOMMIT_ID_MISMATCH")
    if frozen.get("validated_transmission_sha256") != VALIDATED_TRANSMISSION_SHA256:
        blockers.append("TRANSMISSION_SHA_MISMATCH")
    if frozen.get("transmission_candidate_list_sha256") != CANDIDATE_LIST_SHA256:
        blockers.append("CANDIDATE_LIST_SHA_MISMATCH")
    symbols = [str(s) for s in (bound.get("sector3650_symbols") or [])]
    if len(symbols) != 30:
        blockers.append("DRIVER_CONSTITUENT_N")
    constituent_sha = str(bound.get("sector3650_target_set_sha256") or "")
    if constituent_sha != SECTOR3650_TARGET_SET_SHA256:
        blockers.append("DRIVER_CONSTITUENT_SHA_MISMATCH")
    if (bound.get("target_shas") or {}).get("M3") != M3_TARGET_SHA256:
        blockers.append("M3_TARGET_SHA_MISMATCH")
    if list(M3_TARGETS) != ["6590", "6787", "6861", "6941", "6961"]:
        blockers.append("M3_TARGET_SET")
    if any(s not in set(symbols) for s in M3_TARGETS):
        blockers.append("M3_TARGET_OUTSIDE_DRIVER")
    if float(SECTOR_Q["Q80"]) != Q80_ON or float(SECTOR_Q["Q60"]) != Q60_OFF:
        blockers.append("THRESHOLD_VALUE_MISMATCH")
    if sha256_obj(SECTOR_Q) != SECTOR_BOUNDARY_SHA:
        blockers.append("THRESHOLD_SHA_MISMATCH")
    if bound.get("active_runtime_parent_set") != ["M3"]:
        blockers.append("ACTIVE_PARENT_SET")
    if bound.get("m1_status") == "EXACT_RUNTIME_OBSERVABILITY_PROVEN":
        blockers.append("M1_MUST_NOT_EMIT")
    return {
        "pass": not blockers,
        "blockers": blockers,
        "symbols": symbols,
        "driver_constituent_set_sha256": constituent_sha,
        "driver_identity_sha256": bound.get("driver_identity_sha256"),
        "precommit_sha256": frozen.get("precommit_sha256"),
        "m1_status": bound.get("m1_status"),
        "m2_status": bound.get("m2_status"),
        "m3_status": bound.get("m3_status"),
    }


def source_sha(paths: list[str]) -> str:
    raw = b"".join(open(p, "rb").read() for p in paths)
    return sha256_bytes(raw)


def schema_sha() -> str:
    return sha256_obj({"schema": list(SCHEMA_FIELDS)})
