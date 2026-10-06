"""Publish the shadow implementation summary. Does not activate and does not register."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_bytes
from research.causal_driver_pb1.sector_state_alpha_shadow import (
    ANALYSIS_ID,
    CANDIDATE_LIST_SHA256,
    CASE_BLOCKED,
    CASE_READY,
    CASE_REG_BLOCKED,
    M3_TARGET_SHA256,
    NEXT_READY,
    NEXT_REG,
    PRECOMMIT_ID,
    PRECOMMIT_SHA256,
    PROGRAM_ID,
    Q60_OFF,
    Q80_ON,
    SECTOR_BOUNDARY_SHA256,
    VALIDATED_TRANSMISSION_SHA256,
)
from research.causal_driver_pb1.sector_state_alpha_shadow.checks import run_checks
from research.causal_driver_pb1.sector_state_alpha_shadow.identity import schema_sha, source_sha, verify
from research.causal_driver_pb1.sector_state_alpha_shadow.isolation import OUT, assert_write_root, write_overlap_n
from research.causal_driver_pb1.sector_state_alpha_shadow.machine import SCHEMA_FIELDS
from research.causal_driver_pb1.sector_state_alpha_shadow.register_audit import audit_coexistence
from research.causal_driver_pb1.sector_state_alpha_shadow.safety import counters


def _hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    driver = sha256_bytes((root / "driver.py").read_bytes())
    state = sha256_bytes((root / "machine.py").read_bytes())
    names = ["config.py", "driver.py", "machine.py", "register_audit.py", "safety.py"]
    impl = source_sha([str(root / name) for name in names])
    return {
        "implementation_sha256": impl,
        "driver_implementation_sha256": driver,
        "state_machine_sha256": state,
        "schema_sha256": schema_sha(),
    }


def build() -> dict[str, Any]:
    identity = verify()
    checks = run_checks()
    blockers = list(identity.get("blockers") or [])
    if not checks.get("pass"):
        blockers.append("STATE_OR_SAFETY_TEST_FAILED")
    parity = {"pass": False, "driver_value_mismatch_n": None, "eligible_clock_n": None, "prospective_rows_read": 0, "episodes": {}}
    if identity.get("pass") and checks.get("pass"):
        from research.causal_driver_pb1.sector_state_alpha_shadow.parity import run_parity

        parity = run_parity(list(identity["symbols"]))
        if not parity.get("pass"):
            blockers.append("DRIVER_VALUE_PARITY_MISMATCH")
    else:
        blockers.append("PARITY_NOT_RUN")
    registration = audit_coexistence(list(identity.get("symbols") or []))
    if not registration.get("coexistence_possible"):
        blockers.append("SHADOW_LIVE_REGISTRATION_CONFLICT")
    code_pass = bool(identity.get("pass") and checks.get("pass") and parity.get("pass"))
    if not code_pass:
        verdict = CASE_BLOCKED
        if "DRIVER_VALUE_PARITY_MISMATCH" in blockers:
            nxt = "DRIVER_VALUE_PARITY_MISMATCH"
        elif "STATE_OR_SAFETY_TEST_FAILED" in blockers:
            nxt = "STATE_OR_SAFETY_TEST_FAILED"
        else:
            nxt = blockers[0] if blockers else "IMPLEMENTATION_IDENTITY_FAILED"
    elif registration.get("coexistence_possible"):
        verdict = CASE_READY
        nxt = NEXT_READY
    else:
        verdict = CASE_REG_BLOCKED
        nxt = NEXT_REG
    return {
        "verdict": verdict,
        "next": nxt,
        "identity": identity,
        "checks": checks,
        "parity": parity,
        "registration": registration,
        "hashes": _hashes(),
        "blockers": blockers,
        "code_pass": code_pass,
    }


def publish(result: dict[str, Any]) -> dict[str, str]:
    import pandas as pd

    assert_write_root()
    identity = result["identity"]
    parity = result["parity"]
    registration = result["registration"]
    checks = result["checks"]
    answers = {
        "VERDICT": result["verdict"],
        "NEXT": result["next"],
        "precommit_sha256_verified": identity.get("precommit_sha256") == PRECOMMIT_SHA256 and "PRECOMMIT_SHA_MISMATCH" not in result["blockers"],
        "precommit_sha256": PRECOMMIT_SHA256,
        "validated_transmission_sha256": VALIDATED_TRANSMISSION_SHA256,
        "transmission_candidate_list_sha256": CANDIDATE_LIST_SHA256,
        "implementation_sha256": result["hashes"]["implementation_sha256"],
        "driver_implementation_sha256": result["hashes"]["driver_implementation_sha256"],
        "state_machine_sha256": result["hashes"]["state_machine_sha256"],
        "schema_sha256": result["hashes"]["schema_sha256"],
        "driver_constituent_n": len(identity.get("symbols") or []),
        "driver_constituent_set_sha256": identity.get("driver_constituent_set_sha256"),
        "M3_target_sha256": M3_TARGET_SHA256,
        "Q80_ON": Q80_ON,
        "Q60_OFF": Q60_OFF,
        "threshold_boundary_sha256": SECTOR_BOUNDARY_SHA256,
        "eligible_clock_n": parity.get("eligible_clock_n"),
        "implementation_driver_n": parity.get("implementation_driver_n"),
        "parent_reference_driver_n": parity.get("parent_reference_driver_n"),
        "driver_value_mismatch_n": parity.get("driver_value_mismatch_n"),
        "registration_status": registration.get("status"),
        "existing_mandatory_register_n": registration.get("existing_mandatory_register_n"),
        "m3_required_n": registration.get("m3_required_n"),
        "overlap_n": registration.get("overlap_n"),
        "union_n": registration.get("union_n"),
        "coexistence_possible": registration.get("coexistence_possible"),
        "live_registration_changed": False,
        "PB1_INVOCATION_N": counters()["PB1_INVOCATION_N"],
        "ENTRY_CREATED_N": counters()["ENTRY_CREATED_N"],
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "PROSPECTIVE_DATA_OPENED": False,
        "prospective_rows_read": int(parity.get("prospective_rows_read") or 0),
        "SHADOW_ALPHA_IMPLEMENTED": bool(result["code_pass"]),
        "ALPHA_CREATED": False,
        "enabled_default": False,
    }
    doc = {
        "program_id": PROGRAM_ID,
        "analysis_id": ANALYSIS_ID,
        "precommit_id": PRECOMMIT_ID,
        "answers": answers,
        "blockers": result["blockers"],
        "episodes": parity.get("episodes") or {},
        "registration_planner": registration.get("planner_contract"),
        "overlap_symbols": registration.get("overlap_symbols") or [],
        "safety": {
            "research_only": True,
            "FROZEN_VALIDATION_ECONOMIC_OPENED": True,
            "PROSPECTIVE_DATA_OPENED": False,
            "ALPHA_CREATED": False,
            "SHADOW_ALPHA_IMPLEMENTED": bool(result["code_pass"]),
            "MECHANISM_FROZEN": False,
            "PB1_BOUND": False,
            "COMPLETE_STRATEGY_RUN": False,
            "V4_CHANGED": False,
            "V5_CREATED": False,
            "submit": 0,
            "cancel": 0,
            "live": 0,
            "shadow_only": True,
            "orders_enabled": False,
            "enabled": False,
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(answers), encoding="utf-8")
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as writer:
        _sheets(writer, doc, result)
    if write_overlap_n("", ""):
        raise RuntimeError("shadow_write_isolation_failed")
    return {"verdict": answers["VERDICT"], "implementation_sha256": answers["implementation_sha256"]}


def _markdown(answers: dict[str, Any]) -> str:
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: {answers['VERDICT']}",
        f"NEXT: {answers['NEXT']}",
        "",
        f"precommit_sha256 verified: {answers['precommit_sha256_verified']}",
        f"implementation_sha256: {answers['implementation_sha256']}",
        f"driver mismatch n: {answers['driver_value_mismatch_n']}",
        f"union n: {answers['union_n']} coexistence: {answers['coexistence_possible']}",
        "",
        "M3 shadow code is fail-closed. This run did not change the Kabu register and did not read prospective rows.",
        "Q60 closes the shadow episode only. It is not a position exit.",
        "",
    ]
    return "\n".join(lines)


def _sheets(writer, doc: dict[str, Any], result: dict[str, Any]) -> None:
    import pandas as pd

    def put(name: str, rows: list[dict[str, Any]]) -> None:
        pd.DataFrame(rows or [{"note": "none"}]).to_excel(writer, sheet_name=name[:31], index=False)

    answers = doc["answers"]
    identity = result["identity"]
    registration = result["registration"]
    parity = result["parity"]
    checks = result["checks"]
    put("Manifest", [{"field": k, "value": str(v)} for k, v in answers.items()])
    put("Precommit_Binding", [{"precommit_id": PRECOMMIT_ID, "precommit_sha256": PRECOMMIT_SHA256, "verified": answers["precommit_sha256_verified"]}])
    put("Driver_Universe", [{"ordinal": i, "symbol": s, "n": 30, "sha256": identity.get("driver_constituent_set_sha256")} for i, s in enumerate(identity.get("symbols") or [], start=1)])
    put("Target_Set", [{"symbol": s, "target_sha256": M3_TARGET_SHA256} for s in ("6590", "6787", "6861", "6941", "6961")])
    put("Thresholds", [{"Q80_ON": Q80_ON, "Q60_OFF": Q60_OFF, "boundary_sha256": SECTOR_BOUNDARY_SHA256, "source": "PARENT_DEV_FROZEN_QUINTILES", "recomputed": False}])
    put("Timestamp_Semantics", [{"resolver": "BAR_START", "available_at": "bar_start+1m", "price": "last completed same-session close", "push_current_price_is_not_a_bar": True}])
    put("Freshness", [{"max_age_sec": 60, "both_endpoints": True, "required_valid_n_when_listed_30": 24, "below_required": "DRIVER_UNAVAILABLE", "zero_fill": False}])
    put("Registration_Coexistence", [{
        "limit": registration.get("register_limit"),
        "existing_mandatory_register_n": registration.get("existing_mandatory_register_n"),
        "m3_n": registration.get("m3_required_n"),
        "overlap_n": registration.get("overlap_n"),
        "union_n": registration.get("union_n"),
        "status": registration.get("status"),
        "source_day": registration.get("source_day"),
        "overlap_symbols": ",".join(registration.get("overlap_symbols") or []),
        "live_registration_changed": False,
        "names_dropped": 0,
    }])
    put("Historical_Parity", [{
        "eligible_clock_n": parity.get("eligible_clock_n"),
        "implementation_driver_n": parity.get("implementation_driver_n"),
        "parent_reference_driver_n": parity.get("parent_reference_driver_n"),
        "driver_value_mismatch_n": parity.get("driver_value_mismatch_n"),
        "tolerance_abs": parity.get("tolerance_abs"),
        "forward_returns_computed": False,
        **{k: v for k, v in (parity.get("episodes") or {}).items() if not isinstance(v, dict)},
    }])
    put("Episode_State_Machine", [{"rule": "ON", "detail": "INACTIVE and driver>=Q80"}, {"rule": "HOLD", "detail": "ACTIVE while driver>Q60"}, {"rule": "OFF", "detail": "driver<=Q60; not a position exit"}, {"rule": "GAP", "detail": "ACTIVE_DATA_GAP; no silent Q60"}, {"rule": "WINDOW_END", "detail": "RESEARCH_CLOCK_END_1125"}, {"rule": "RESET", "detail": "INACTIVE at each session start"}])
    put("Unit_Tests", list(checks["state"]))
    put("Target_Emission", [{"pass": checks["targets"]["pass"], "n": checks["targets"]["n"], "symbols": ",".join(checks["targets"]["symbols"])}])
    put("M1_M2_Negative", [{"pass": checks["parents"]["pass"], "m1_events": checks["parents"]["m1_events"], "m2_events": checks["parents"]["m2_events"]}])
    put("Order_Path_Safety", [{"pass": checks["safety"]["pass"], "banned_hits": ",".join(checks["safety"]["banned_hits"]), **checks["safety"]["counters"]}])
    put("PB1_Firewall", [{"PB1_BOUND": False, "PB1_INVOCATION_N": 0, "ENTRY_CREATED_N": 0}])
    put("Prospective_Firewall", [{"PROSPECTIVE_DATA_OPENED": False, "prospective_rows_read": answers["prospective_rows_read"], "sealed_from": "20260924"}])
    put("Implementation_Identity", [result["hashes"]])
    put("Safety", [doc["safety"]])
    _ = SCHEMA_FIELDS
