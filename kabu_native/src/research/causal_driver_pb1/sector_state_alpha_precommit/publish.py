"""Publish the alpha-signal precommit. Does not emit AlphaSignal rows."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_bytes
from research.causal_driver_pb1.sector_state_alpha_precommit import (
    ANALYSIS_ID,
    CANDIDATE_LIST_SHA256,
    CASE_BLOCKED,
    CASE_READY,
    M1_TARGETS,
    M2_TARGETS,
    M3_TARGETS,
    NEXT_BLOCKED,
    NEXT_READY,
    PARENT_VERDICT,
    PRECOMMIT_ID,
    PROGRAM_ID,
    VALIDATED_TRANSMISSION_SHA256,
)
from research.causal_driver_pb1.sector_state_alpha_precommit.bind import (
    GLOBAL_BOUNDARY_SHA,
    GLOBAL_Q,
    SECTOR_BOUNDARY_SHA,
    SECTOR_Q,
    THRESHOLD_ARTIFACT,
    THRESHOLD_SOURCE,
)
from research.causal_driver_pb1.sector_state_alpha_precommit.contract import contract
from research.causal_driver_pb1.sector_state_alpha_precommit.isolation import OUT, assert_write_root, write_overlap_n


def build() -> dict[str, Any]:
    from research.causal_driver_pb1.sector_state_alpha_precommit.bind import bind

    bound = bind()
    frozen = contract(bound)
    ready = bool(frozen.get("ready"))
    return {
        "verdict": CASE_READY if ready else CASE_BLOCKED,
        "next": NEXT_READY if ready else NEXT_BLOCKED,
        "bound": bound,
        "contract": frozen,
        "blockers": bound.get("blockers") or [],
    }


def publish(result: dict[str, Any]) -> dict[str, str]:
    import pandas as pd

    assert_write_root()
    bound = result["bound"]
    frozen = result["contract"]
    answers = {
        "VERDICT": result["verdict"],
        "NEXT": result["next"],
        "validated_transmission_sha256": VALIDATED_TRANSMISSION_SHA256,
        "transmission_candidate_list_sha256": CANDIDATE_LIST_SHA256,
        "M1_runtime_binding_status": bound["m1_status"],
        "M2_runtime_binding_status": bound["m2_status"],
        "M3_runtime_binding_status": bound["m3_status"],
        "M1_M2_role": "VALIDATED_BUT_NOT_RUNTIME_BOUND",
        "exact_simultaneous_n": {"M1": 105, "M2": 105, "M3": 30},
        "runtime_maximum_simultaneous_observation_capacity": bound["register_limit"],
        "driver_substitution_used": False,
        "GLOBAL_exact_runtime_observable": False,
        "SECTOR_3650_exact_runtime_observable": bool(bound["sector_exact"]),
        "M1_target_sha256": bound["target_shas"]["M1"],
        "M2_target_sha256": bound["target_shas"]["M2"],
        "M3_target_sha256": bound["target_shas"]["M3"],
        "parent_frozen_quintiles": {
            "GLOBAL_105_BREADTH_w1": {**GLOBAL_Q, "boundary_sha256": GLOBAL_BOUNDARY_SHA},
            "SECTOR_3650_BREADTH_w1": {**SECTOR_Q, "boundary_sha256": SECTOR_BOUNDARY_SHA},
        },
        "threshold_source": THRESHOLD_SOURCE,
        "threshold_artifact": THRESHOLD_ARTIFACT,
        "ACTIVE_RUNTIME_PARENT_SET": bound["active_runtime_parent_set"],
        "alpha_on_rule": frozen["alpha_on"],
        "alpha_off_rule": frozen["alpha_off"],
        "clock_window_jst": frozen["clock_window_jst"],
        "PROSPECTIVE_DATA_OPENED": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": True,
        "ALPHA_CREATED": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "precommit_sha256": frozen.get("precommit_sha256"),
    }
    doc = {
        "program_id": PROGRAM_ID,
        "precommit_id": PRECOMMIT_ID,
        "parent_verdict": PARENT_VERDICT,
        "answers": answers,
        "blockers": result["blockers"],
        "contract": frozen,
        "driver_identity": bound["driver_identity"],
        "signal_schema": _schema(),
        "episode_semantics": _episodes(),
        "safety": _safety(),
        "prospective_firewall": {"PROSPECTIVE_DATA_OPENED": False, "period": "20260924+", "rows_read": 0},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(answers, result["blockers"]), encoding="utf-8")
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as w:
        _sheets(w, doc, bound)
    sha = sha256_bytes((OUT / "report.json").read_bytes())
    if write_overlap_n("", ""):
        raise RuntimeError("alpha_precommit_write_isolation_failed")
    return {"report_sha256": sha, "verdict": answers["VERDICT"], "precommit_sha256": answers["precommit_sha256"] or ""}


def _schema() -> list[str]:
    return [
        "alpha_signal_id", "alpha_family_id", "target_symbol", "direction", "signal_time", "available_at",
        "driver_scope", "driver_metric", "driver_lookback", "driver_value", "q80_on", "q60_off",
        "episode_id", "episode_state", "supporting_parent_ids", "validated_transmission_sha256",
        "target_set_sha256", "driver_identity_sha256", "runtime_binding_status", "research_clock_valid", "freshness_valid",
    ]


def _episodes() -> dict[str, Any]:
    return {
        "historical_episode_replay": "NOT_RUN",
        "on": "driver_value >= frozen Q80",
        "off": "driver_value <= frozen Q60",
        "first_clock_already_above_q80_may_activate": True,
        "no_fabricated_pre_session_crossing": True,
        "no_duplicate_alpha_while_active": True,
        "rearm_requires_new_q80_transition": True,
        "fields": ["episode_id", "episode_start", "episode_end"],
        "q60_off_is_not_position_exit": True,
    }


def _safety() -> dict[str, Any]:
    return {
        "research_only": True,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": True,
        "PROSPECTIVE_DATA_OPENED": False,
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "V4_CHANGED": False,
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
    }


def _markdown(answers: dict[str, Any], blockers: list[str]) -> str:
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: {answers['VERDICT']}",
        f"NEXT: {answers['NEXT']}",
        "",
        f"validated_transmission_sha256: {answers['validated_transmission_sha256']}",
        f"M1: {answers['M1_runtime_binding_status']}",
        f"M2: {answers['M2_runtime_binding_status']}",
        f"M3: {answers['M3_runtime_binding_status']}",
        f"ACTIVE_RUNTIME_PARENT_SET: {answers['ACTIVE_RUNTIME_PARENT_SET']}",
        f"precommit_sha256: {answers['precommit_sha256']}",
        "",
        "M1 and M2 remain scientifically valid. The live Kabu push register holds 50 names, so the 105-name global driver cannot be observed exactly.",
        "M3 requires the frozen 30 SECTOR_3650 constituents. That set fits the same register without dropping names.",
        "Thresholds are the parent DEV quintiles. This precommit does not emit AlphaSignal rows.",
        "",
        f"blockers: {blockers or 'none'}",
        "",
    ]
    return "\n".join(lines)


def _sheets(w, doc: dict[str, Any], bound: dict[str, Any]) -> None:
    import pandas as pd

    def put(name: str, rows: list[dict[str, Any]]) -> None:
        pd.DataFrame(rows).to_excel(w, sheet_name=name[:31], index=False)

    answers = doc["answers"]
    put("Manifest", [{"field": k, "value": str(v)} for k, v in answers.items()])
    put("Parent_Binding", [
        {"parent": "transmission", "verdict": PARENT_VERDICT, "sha256": VALIDATED_TRANSMISSION_SHA256},
        {"parent": "candidate_list", "verdict": "", "sha256": CANDIDATE_LIST_SHA256},
        {"parent": "M4", "verdict": "EXCLUDED", "sha256": ""},
    ])
    put("Validated_Parents", [
        {"parent": "M1", "driver": "BREADTH|GLOBAL_105|w1|h1", "runtime_binding_status": bound["m1_status"], "role": "VALIDATED_BUT_NOT_RUNTIME_BOUND"},
        {"parent": "M2", "driver": "BREADTH|GLOBAL_105|w1|h3", "runtime_binding_status": bound["m2_status"], "role": "VALIDATED_BUT_NOT_RUNTIME_BOUND"},
        {"parent": "M3", "driver": "BREADTH|SECTOR_3650|w1|h3", "runtime_binding_status": bound["m3_status"], "role": "ACTIVE_RUNTIME"},
    ])
    put("Validated_Targets", (
        [{"parent": "M1", "symbol": s, "target_set_sha256": bound["target_shas"]["M1"]} for s in M1_TARGETS]
        + [{"parent": "M2", "symbol": s, "target_set_sha256": bound["target_shas"]["M2"]} for s in M2_TARGETS]
        + [{"parent": "M3", "symbol": s, "target_set_sha256": bound["target_shas"]["M3"]} for s in M3_TARGETS]
    ))
    put("Runtime_Data_Sources", [{
        "live_source": "Kabu PUSH via api.kabu_register",
        "historical_price_field": bound["price_field_historical"],
        "timestamp_semantics": bound["timestamp_semantics"],
        "available_at": "bar_start+1m",
        "freshness": "age_sec<=60 at both one-minute lookback endpoints",
        "no_prior_day_carry": True,
    }])
    put("Runtime_Observability", [
        {"parent": "M1", "required_universe": "GLOBAL_105", "required_n": 105, "exact_parity": False, "status": bound["m1_status"]},
        {"parent": "M2", "required_universe": "GLOBAL_105", "required_n": 105, "exact_parity": False, "status": bound["m2_status"]},
        {"parent": "M3", "required_universe": "SECTOR_3650", "required_n": 30, "exact_parity": True, "status": bound["m3_status"]},
    ])
    put("Registration_Capacity", [{
        "source": "api.kabu_register.KABU_PUSH_REGISTER_LIMIT",
        "maximum_simultaneous_n": bound["register_limit"],
        "global_105_fits": False,
        "sector_3650_fits": True,
        "constituents_dropped": 0,
        "driver_substitution_used": False,
    }])
    put("Driver_Identity", [{
        "alpha_driver": "original parent breadth",
        "lto_role": "WHY_THIS_SYMBOL only",
        "driver_identity_sha256": bound["driver_identity_sha256"],
        "universe105_sha256": bound["universe105_sha256"],
        "sector3650_constituent_sha256": bound["sector3650_target_set_sha256"],
    }])
    put("Parent_Quintiles", [
        {"scope": "GLOBAL_105", "lookback": 1, **GLOBAL_Q, "boundary_sha256": GLOBAL_BOUNDARY_SHA, "parents": "M1,M2"},
        {"scope": "SECTOR_3650", "lookback": 1, **SECTOR_Q, "boundary_sha256": SECTOR_BOUNDARY_SHA, "parents": "M3"},
    ])
    put("Threshold_Binding", [{
        "threshold_source": THRESHOLD_SOURCE,
        "artifact": THRESHOLD_ARTIFACT,
        "recomputed_on_fv": False,
        "economic_search": False,
    }])
    put("Alpha_Episodes", [{k: str(v) for k, v in doc["episode_semantics"].items()}])
    put("Signal_Schema", [{"ordinal": i, "field": name} for i, name in enumerate(doc["signal_schema"], start=1)])
    put("Parent_Fusion", [
        {"family": "GLOBAL_BREADTH_W1_UP", "emits_runtime_alpha": False, "note": "M1 h1 support and M2 h3 support are metadata on one driver"},
        {"family": "SECTOR3650_BREADTH_W1_UP", "emits_runtime_alpha": True, "note": "independent of global breadth; support count does not rank"},
    ])
    put("Clock_Boundary", [{"window_jst": "09:10-11:25", "pm_extrapolation": False, "resolver": "BAR_START"}])
    put("Prospective_Firewall", [doc["prospective_firewall"]])
    put("Safety", [doc["safety"]])
