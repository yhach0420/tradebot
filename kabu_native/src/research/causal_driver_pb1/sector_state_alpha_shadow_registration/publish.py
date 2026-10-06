"""Publish the registration resolution. Does not activate and does not register."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1.sector_state_alpha_shadow_registration import (
    ACTIVATION_STEPS,
    ANALYSIS_ID,
    CASE_BLOCKED,
    CASE_READY,
    EXPECTED_DRIVER_SHA256,
    EXPECTED_IMPLEMENTATION_SHA256,
    EXPECTED_SCHEMA_SHA256,
    EXPECTED_STATE_SHA256,
    M3_SHA256,
    M3_TARGET_SHA256,
    NEXT_READY,
    OWNER,
    WINDOW_END_STEPS,
)
from research.causal_driver_pb1.sector_state_alpha_shadow_registration.checks import run_checks
from research.causal_driver_pb1.sector_state_alpha_shadow_registration.isolation import OUT, assert_write_root, write_overlap_n


def _probe() -> dict[str, Any]:
    try:
        from research.new_causal_information_acquisition_v1.exclusive import probe_exclusive

        raw = probe_exclusive()
        return {
            "read_only": True,
            "ok": bool(raw.get("ok")),
            "paper_simultaneous": bool(raw.get("paper_simultaneous")),
            "certification_mode": bool(raw.get("certification_mode")),
            "standard_capture_simultaneous": bool(raw.get("standard_capture_simultaneous")),
            "competitor_n": len(raw.get("competitors") or []),
            "activation_would_fail_closed": not bool(raw.get("ok")),
        }
    except Exception as exc:
        return {"read_only": True, "ok": False, "error": type(exc).__name__, "activation_would_fail_closed": True}


def build() -> dict[str, Any]:
    checks = run_checks()
    plan = checks["plan"]
    hashes = checks["hashes"]
    hash_ok = (
        hashes.get("implementation_sha256") == EXPECTED_IMPLEMENTATION_SHA256
        and hashes.get("driver_implementation_sha256") == EXPECTED_DRIVER_SHA256
        and hashes.get("state_machine_sha256") == EXPECTED_STATE_SHA256
        and hashes.get("schema_sha256") == EXPECTED_SCHEMA_SHA256
    )
    profile_a_ok = bool((plan.get("candidates") or {}).get("PROFILE_A", {}).get("valid"))
    ready = bool(checks.get("pass") and hash_ok and profile_a_ok and plan.get("would_mutate_live_register") is False)
    if ready:
        verdict, nxt = CASE_READY, NEXT_READY
    elif not hash_ok:
        verdict, nxt = CASE_BLOCKED, "ALPHA_IMPLEMENTATION_HASH_CHANGED"
    else:
        failed = [r["test"] for r in checks["rows"] if not r["pass"]]
        verdict, nxt = CASE_BLOCKED, str(failed[0] if failed else "REGISTRATION_PROFILE_INVALID")
    return {"verdict": verdict, "next": nxt, "checks": checks, "probe": _probe(), "ready": ready, "hash_ok": hash_ok}


def publish(result: dict[str, Any]) -> dict[str, str]:
    import pandas as pd

    assert_write_root()
    plan = result["checks"]["plan"]
    ident = plan["identities"]
    candidates = plan["candidates"]
    hashes = result["checks"]["hashes"]
    answers = {
        "VERDICT": result["verdict"],
        "NEXT": result["next"],
        "alpha_hashes_unchanged": bool(result["hash_ok"]),
        "implementation_sha256": hashes.get("implementation_sha256"),
        "M3_n": len(ident["m3"]),
        "M3_sha256": ident["m3_sha256"],
        "target_n": len(ident["targets"]),
        "target_sha256": M3_TARGET_SHA256,
        "core_n": len(ident["core"]),
        "dynamic_n": len(ident["dynamic"]),
        "futures_n": len(ident["futures"]),
        "standard_total_n": len(ident["occupied"]),
        "selected_profile": plan["selected_profile"],
        "selected_plan_sha256": plan["selected_plan_sha256"],
        "selected_register_n": plan["union_n"],
        "core_preserved_n": plan["core_preserved_n"],
        "dynamic_preserved_n": plan["dynamic_preserved_n"],
        "futures_preserved_n": plan["futures_preserved_n"],
        "standard_paper_simultaneous_allowed": False,
        "registration_owner": OWNER,
        "would_mutate_live_register": False,
        "live_registration_changed": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "prospective_rows_read": 0,
        "submit": 0,
        "cancel": 0,
        "live": 0,
    }
    doc = {
        "analysis_id": ANALYSIS_ID,
        "answers": answers,
        "profiles": {k: {kk: vv for kk, vv in row.items() if kk != "symbols"} for k, row in candidates.items()},
        "probe": result["probe"],
        "activation_steps": list(ACTIVATION_STEPS),
        "window_end_steps": list(WINDOW_END_STEPS),
        "safety": {
            "research_only": True,
            "FROZEN_VALIDATION_ECONOMIC_OPENED": True,
            "PROSPECTIVE_DATA_OPENED": False,
            "ALPHA_CREATED": False,
            "SHADOW_ALPHA_IMPLEMENTED": True,
            "MECHANISM_FROZEN": False,
            "PB1_BOUND": False,
            "COMPLETE_STRATEGY_RUN": False,
            "V4_CHANGED": False,
            "V5_CREATED": False,
            "live_registration_changed": False,
            "submit": 0,
            "cancel": 0,
            "live": 0,
            "SECTOR_STATE_ALPHA_SHADOW_ENABLED": False,
            "SECTOR_STATE_ALPHA_SHADOW_REGISTER_PROFILE_ENABLED": False,
            "SECTOR_STATE_ALPHA_SHADOW_ORDERS_ENABLED": False,
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "report.md").write_text(
        "\n".join([
            f"# {ANALYSIS_ID}",
            "",
            f"VERDICT: {answers['VERDICT']}",
            f"NEXT: {answers['NEXT']}",
            "",
            f"selected_profile: {answers['selected_profile']}",
            f"selected_plan_sha256: {answers['selected_plan_sha256']}",
            f"register_n: {answers['selected_register_n']}",
            "",
            "This profile is mutually exclusive with the standard Paper register. Dynamic38 is not required for the M3 driver.",
            "The live Kabu register was not changed. Prospective rows were not read.",
            "",
        ]),
        encoding="utf-8",
    )
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as writer:
        _sheets(writer, doc, result)
    if write_overlap_n("", ""):
        raise RuntimeError("registration_resolution_write_isolation_failed")
    return {"verdict": answers["VERDICT"], "selected_profile": answers["selected_profile"]}


def _sheets(writer, doc: dict[str, Any], result: dict[str, Any]) -> None:
    import pandas as pd

    plan = result["checks"]["plan"]
    ident = plan["identities"]
    candidates = plan["candidates"]

    def put(name: str, rows: list[dict[str, Any]]) -> None:
        pd.DataFrame(rows or [{"note": "none"}]).to_excel(writer, sheet_name=name[:31], index=False)

    put("Manifest", [{"field": k, "value": str(v)} for k, v in doc["answers"].items()])
    put("Conflict_Source", [{"existing_union_with_m3": 77, "limit": 50, "reason": "DIRECT_COEXISTENCE_IMPOSSIBLE", "source_day": ident["source_day"]}])
    put("Current_Profile", [{"bucket": "core", "n": len(ident["core"])}, {"bucket": "dynamic", "n": len(ident["dynamic"])}, {"bucket": "futures", "n": len(ident["futures"])}, {"bucket": "total", "n": len(ident["occupied"])}])
    put("M3_Profile", [{"symbol": s, "sha256": M3_SHA256} for s in ident["m3"]])
    put("Core_Set", [{"symbol": s} for s in ident["core"]])
    put("Dynamic_Set", [{"symbol": s, "in_shadow_profile": s in set(plan["selected_symbols"]), "reason": "M3_CONSTITUENT" if s in set(ident["m3"]) else "NOT_REQUIRED_FOR_M3_ALPHA"} for s in ident["dynamic"]])
    put("Futures_Set", [{"symbol": s, "code": c} for s, c in zip(ident["futures"], ident["future_codes"])])
    m3s, cores, futs, dyns = set(ident["m3"]), set(ident["core"]), set(ident["futures"]), set(ident["dynamic"])
    put("Overlap", [
        {"pair": "M3_core", "n": len(m3s & cores), "symbols": ",".join(sorted(m3s & cores))},
        {"pair": "M3_dynamic", "n": len(m3s & dyns), "symbols": ",".join(sorted(m3s & dyns))},
        {"pair": "M3_futures", "n": len(m3s & futs), "symbols": ",".join(sorted(m3s & futs))},
        {"pair": "core_futures", "n": len(cores & futs), "symbols": ",".join(sorted(cores & futs))},
    ])
    put("Candidate_Profiles", [{k: v for k, v in row.items() if k != "symbols"} | {"profile_id": key} for key, row in candidates.items()])
    put("Profile_Selection", [{"rule": "C then B then A", "selected": plan["selected_profile"], "plan_sha256": plan["selected_plan_sha256"], "register_n": plan["union_n"]}])
    put("Registration_Ownership", [{"owner": OWNER, "previous_owner": plan["previous_registration_owner"], "previous_plan_sha256": plan["previous_registration_plan_sha256"], "selected_plan_sha256": plan["selected_plan_sha256"]}])
    put("Mutual_Exclusion", [{"standard_paper_simultaneous_allowed": False, **result["probe"]}])
    put("Warmup", [{"capture_from": "09:00", "alpha_from": "09:10", "register_deadline": "08:55", "stable_through": "11:26", "prior_day_carry": False}])
    put("Activation_Sequence", [{"ordinal": i, "step": s} for i, s in enumerate(ACTIVATION_STEPS, start=1)])
    put("Rollback", [{"snapshot": "pre_shadow_registration_sha256", "restore_requires_exact_sha": True, "improvised_dynamic_replacement": False, "window_end": ",".join(WINDOW_END_STEPS)}])
    put("Dry_Run", [{
        "selected_profile": plan["selected_profile"],
        "selected_plan_sha256": plan["selected_plan_sha256"],
        "union_n": plan["union_n"],
        "remaining_slots": plan["remaining_slots"],
        "M3_required_n": 30,
        "M3_missing_n": 0,
        "core_preserved_n": plan["core_preserved_n"],
        "futures_preserved_n": plan["futures_preserved_n"],
        "dynamic_preserved_n": plan["dynamic_preserved_n"],
        "would_mutate_live_register": False,
    }])
    put("Unit_Tests", list(result["checks"]["rows"]))
    put("Alpha_Hash_Parity", [{"name": k, "actual": hashes, "expected": expected, "match": hashes == expected} for k, hashes, expected in (
        ("implementation", result["checks"]["hashes"]["implementation_sha256"], EXPECTED_IMPLEMENTATION_SHA256),
        ("driver", result["checks"]["hashes"]["driver_implementation_sha256"], EXPECTED_DRIVER_SHA256),
        ("state_machine", result["checks"]["hashes"]["state_machine_sha256"], EXPECTED_STATE_SHA256),
        ("schema", result["checks"]["hashes"]["schema_sha256"], EXPECTED_SCHEMA_SHA256),
    )])
    put("Prospective_Firewall", [{"PROSPECTIVE_DATA_OPENED": False, "prospective_rows_read": 0, "sealed_from": "20260924"}])
    put("Safety", [doc["safety"]])
