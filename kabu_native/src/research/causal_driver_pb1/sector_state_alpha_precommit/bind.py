"""Bind transmission identities, runtime capacity, and DEV-only parent quintiles."""
from __future__ import annotations

import json
from typing import Any

from api.kabu_register import KABU_PUSH_REGISTER_LIMIT
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase2_precommit import CLOCK_FIRST, CLOCK_LAST, STOCK_PRICE_FIELD, STOCK_TIMESTAMP_SEMANTICS
from research.causal_driver_pb1.sector_state_alpha_precommit import (
    CANDIDATE_LIST_SHA256,
    M1_TARGETS,
    M2_TARGETS,
    M3_TARGETS,
    PARENT_VERDICT,
    VALIDATED_TRANSMISSION_SHA256,
)
from research.causal_driver_pb1.sector_state_transmission.isolation import OUT as TRANSMISSION_OUT
from research.causal_driver_pb1.sector_state_transmission_precommit.targets import bind_targets

GLOBAL_Q = {
    "Q20": -0.35051546391752575,
    "Q40": -0.1111111111111111,
    "Q60": 0.10309278350515463,
    "Q80": 0.3431372549019608,
}
SECTOR_Q = {
    "Q20": -0.4,
    "Q40": -0.13793103448275862,
    "Q60": 0.1111111111111111,
    "Q80": 0.39285714285714285,
}
GLOBAL_BOUNDARY_SHA = "145f86729a634b410fccafeae0d50ee60ed65031013cb00a494fd96bd09a9cc8"
SECTOR_BOUNDARY_SHA = "3bfdce2d9d2afc129bd35b4eca23025b226fbb5f4570164a4106faccf72d81cf"
THRESHOLD_ARTIFACT = "results/causal_driver/sector_breadth_dispersion_contract_corrected_discovery_v1/report.json"
THRESHOLD_SOURCE = "PARENT_DEV_FROZEN_QUINTILES"


def _target_sha(parent: str, symbols: tuple[str, ...]) -> str:
    return sha256_obj({"namespace": "SECTOR_STATE_ALPHA_VALIDATED_TARGET_SET_V1", "parent_mechanism_id": parent, "n": len(symbols), "symbols": list(symbols)})


def bind() -> dict[str, Any]:
    blockers: list[str] = []
    report = json.loads((TRANSMISSION_OUT / "report.json").read_text(encoding="utf-8"))
    answers = report.get("answers") or {}
    if answers.get("VERDICT") != PARENT_VERDICT:
        blockers.append("PARENT_VERDICT_MISMATCH")
    if answers.get("validated_transmission_sha256") != VALIDATED_TRANSMISSION_SHA256:
        blockers.append("VALIDATED_TRANSMISSION_SHA_MISMATCH")
    if answers.get("transmission_candidate_list_sha256") != CANDIDATE_LIST_SHA256:
        blockers.append("CANDIDATE_LIST_SHA_MISMATCH")
    if list(answers.get("validated_parent_mechanisms") or []) != ["M1", "M2", "M3"]:
        blockers.append("VALIDATED_PARENT_SET_MISMATCH")
    sets = answers.get("validated_target_symbol_sets") or {}
    expected = {"M1": list(M1_TARGETS), "M2": list(M2_TARGETS), "M3": list(M3_TARGETS)}
    for key, symbols in expected.items():
        if list(sets.get(key) or []) != symbols:
            blockers.append(f"TARGET_SET_MISMATCH_{key}")
    if "M4" in (answers.get("validated_parent_mechanisms") or []):
        blockers.append("M4_INCLUDED")
    targets = bind_targets()
    blockers.extend(targets.get("blockers") or [])
    s3650 = list(targets.get("sector3650_symbols") or [])
    if len(s3650) != 30:
        blockers.append("SECTOR3650_N")
    if any(s not in set(s3650) for s in M3_TARGETS):
        blockers.append("M3_TARGET_OUTSIDE_SECTOR")
    if sha256_obj(GLOBAL_Q) != GLOBAL_BOUNDARY_SHA or sha256_obj(SECTOR_Q) != SECTOR_BOUNDARY_SHA:
        blockers.append("QUINTILE_SHA_MISMATCH")
    register_limit = int(KABU_PUSH_REGISTER_LIMIT)
    if register_limit != 50:
        blockers.append("REGISTER_LIMIT_CHANGED")
    global_exact = 105 <= register_limit
    sector_exact = len(s3650) <= register_limit and len(s3650) == 30
    if global_exact:
        blockers.append("GLOBAL_OBSERVABILITY_UNEXPECTED")
    m1_status = "VALIDATED_RESEARCH_ONLY_DATA_SOURCE_UNRESOLVED"
    m2_status = "VALIDATED_RESEARCH_ONLY_DATA_SOURCE_UNRESOLVED"
    m3_status = "EXACT_RUNTIME_OBSERVABILITY_PROVEN" if sector_exact else "DATA_SOURCE_UNRESOLVED"
    active = ["M3"] if m3_status == "EXACT_RUNTIME_OBSERVABILITY_PROVEN" else []
    if not active:
        blockers.append("NO_EXACT_RUNTIME_PARENT")
    target_shas = {"M1": _target_sha("M1", M1_TARGETS), "M2": _target_sha("M2", M2_TARGETS), "M3": _target_sha("M3", M3_TARGETS)}
    driver_identity = {
        "namespace": "SECTOR_STATE_ALPHA_DRIVER_IDENTITY_V1",
        "driver_substitution_used": False,
        "GLOBAL_105_n": 105,
        "SECTOR_3650_n": len(s3650),
        "metric": "BREADTH",
        "lookback": 1,
        "direction": "UP",
        "alpha_driver": "original validated parent breadth state",
        "transmission_lto_is_not_the_alpha_driver": True,
    }
    return {
        "pass": not blockers,
        "blockers": list(dict.fromkeys(blockers)),
        "register_limit": register_limit,
        "global_exact": global_exact,
        "sector_exact": sector_exact,
        "m1_status": m1_status,
        "m2_status": m2_status,
        "m3_status": m3_status,
        "active_runtime_parent_set": active,
        "target_shas": target_shas,
        "sector3650_symbols": s3650,
        "sector3650_target_set_sha256": targets.get("sector3650_target_set_sha256"),
        "universe105_sha256": targets.get("universe105_sha256"),
        "driver_identity_sha256": sha256_obj(driver_identity),
        "driver_identity": driver_identity,
        "clock_first": CLOCK_FIRST,
        "clock_last": CLOCK_LAST,
        "price_field_historical": STOCK_PRICE_FIELD,
        "timestamp_semantics": STOCK_TIMESTAMP_SEMANTICS,
    }
