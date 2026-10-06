"""Assemble the V1.1 control-regime precommit. No symbol betas."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1 import (
    EXPECTED_COMPLETE_STRATEGY_SHA256,
    EXPECTED_V4_MACHINE_SHA256,
    FV_FIRST,
    PROSPECTIVE_FROM,
)
from research.causal_driver_pb1.contracts.enums import AccessKind, DatasetRole, RunMode
from research.causal_driver_pb1.contracts.errors import FirewallDenied
from research.causal_driver_pb1.cross_sectional_precommit.contamination import contamination_ledger
from research.causal_driver_pb1.datasets.firewall import AccessLedger, request_dataset
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.identity.pin import bind_identities, frozen_source_hashes
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.sector_state_transmission_precommit.bootstrap import freeze_bootstraps
from research.causal_driver_pb1.sector_state_transmission_precommit.family import build_family
from research.causal_driver_pb1.sector_state_transmission_precommit.parent import bind_parent, parent_mechanism_set_sha256
from research.causal_driver_pb1.sector_state_transmission_precommit.targets import bind_targets
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1 import (
    BLOCKED_SYMBOLS,
    CASE_BLOCKED,
    CASE_READY,
    CONTROL_CONTRACT_ID,
    DISCOVERY_BOOTSTRAP_SHA256,
    DISCOVERY_C1_N,
    DISCOVERY_DATE_SHA256,
    DISCOVERY_DEV_N,
    DISCOVERY_FOLD_SHA256,
    DISCOVERY_N,
    FAMILY_N,
    FAMILY_SERIALIZATION_SHA256,
    FV_BOOTSTRAP_SHA256,
    FV_ELIGIBLE_DAY_SHA256,
    FV_FOLD_SHA256,
    FV_INPUT_N,
    GLOBAL_TARGET_SET_SHA256,
    NEXT_RESOLVE,
    NEXT_RUN,
    OLD_BLOCKER,
    PARENT_MECHANISM_SET_SHA256,
    PARENT_PRECOMMIT_SHA256,
    PARENT_VERDICT,
    PRECOMMIT_ID,
    SECTOR3650_TARGET_SET_SHA256,
)
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1.contract import frozen_contract
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1.feasibility import prove_inputs
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1.regimes import (
    REGIME_MULTI,
    REGIME_NONE,
    REGIME_SINGLE,
    assign_structural_regimes,
    contract_hashes,
    model_definitions,
)


def _firewall() -> dict[str, Any]:
    ledger = AccessLedger(run_id="SYMBOL_TRANSMISSION_PRECOMMIT_V1_1", run_mode=RunMode.DRIVER_DISCOVERY)
    request_dataset(
        ledger=ledger,
        run_mode=RunMode.DRIVER_DISCOVERY,
        dataset_id="RESEARCH_OBSERVATION_UNIVERSE_105",
        dataset_role=DatasetRole.DEVELOPMENT,
        requested_fields=("symbol",),
        access_kind=AccessKind.METADATA,
    )
    fv = pr = False
    try:
        request_dataset(
            ledger=ledger,
            run_mode=RunMode.DRIVER_DISCOVERY,
            dataset_id="EQUITY_MINUTE_FV",
            dataset_role=DatasetRole.FROZEN_VALIDATION,
            requested_fields=("close",),
            access_kind=AccessKind.PAYLOAD,
        )
    except FirewallDenied:
        fv = True
    try:
        request_dataset(
            ledger=ledger,
            run_mode=RunMode.DRIVER_DISCOVERY,
            dataset_id="EQUITY_MINUTE_PROSPECTIVE",
            dataset_role=DatasetRole.PROSPECTIVE,
            requested_fields=("close",),
            access_kind=AccessKind.PAYLOAD,
        )
    except FirewallDenied:
        pr = True
    date_ok = True
    for day in (FV_FIRST, PROSPECTIVE_FROM):
        try:
            assert_ingest_date_allowed(day)
            date_ok = False
        except IngestDateDenied:
            pass
    return {"fv_economic_payload_denied": fv, "prospective_denied": pr, "fv_input_presence_only": True, "pass": fv and pr and date_ok}


def _examples(assignment: dict[str, Any], sector3650: list[str]) -> dict[str, Any]:
    by = assignment["by_symbol"]
    s9501 = by["9501"]
    s1515 = by["1515"]
    s1605 = by["1605"]
    example_3650 = sector3650[0]
    s3650 = by[example_3650]
    ok = (
        s9501["pool_n_peer_pit"] == 0
        and s9501["control_regime"] == REGIME_NONE
        and s9501["sector_control_status"] == "NOT_APPLICABLE"
        and s1515["pool_n_peer_pit"] == 1
        and s1515["control_regime"] == REGIME_SINGLE
        and s1515["sole_peer"] == "1605"
        and s1605["sole_peer"] == "1515"
        and s3650["pool_n_peer_pit"] == 29
        and s3650["control_regime"] == REGIME_MULTI
        and s3650["target_sector"] == "3650"
    )
    return {
        "pass": ok,
        "symbol_9501": s9501,
        "symbol_1515": s1515,
        "symbol_1605": s1605,
        "sector3650_example_symbol": example_3650,
        "sector3650_example": s3650,
        "model_structurally_evaluable_9501": True,
        "special_outcome_treatment_9501": False,
    }


def evaluate() -> dict[str, Any]:
    blockers: list[str] = []
    identity = bind_identities()
    pre_hashes = frozen_source_hashes()
    if identity.get("V4_MACHINE_SHA256") != EXPECTED_V4_MACHINE_SHA256:
        blockers.append("V4_CHANGED")
    if identity.get("COMPLETE_STRATEGY_SHA256") != EXPECTED_COMPLETE_STRATEGY_SHA256:
        blockers.append("COMPLETE_STRATEGY_CHANGED")
    fw = _firewall()
    if not fw.get("pass"):
        blockers.append("FIREWALL")
    parent = bind_parent()
    blockers.extend(parent.get("blockers") or [])
    if parent.get("parent_mechanism_set_sha256") != PARENT_MECHANISM_SET_SHA256:
        blockers.append("PARENT_MECHANISM_SHA_MISMATCH")
    if parent_mechanism_set_sha256() != PARENT_MECHANISM_SET_SHA256:
        blockers.append("PARENT_MECHANISM_SHA_MISMATCH")
    targets = bind_targets()
    blockers.extend(targets.get("blockers") or [])
    if targets.get("global_target_set_sha256") != GLOBAL_TARGET_SET_SHA256:
        blockers.append("GLOBAL_TARGET_SHA_MISMATCH")
    if targets.get("sector3650_target_set_sha256") != SECTOR3650_TARGET_SET_SHA256:
        blockers.append("SECTOR3650_TARGET_SHA_MISMATCH")
    family = {"rows": [], "family_n": 0, "family_sha256": None, "pass": False, "all_target_self_in_driver_false": False}
    assignment: dict[str, Any] = {"rows": [], "by_symbol": {}, "peer_structure": [], "multi_peer_targets": [], "single_peer_targets": [], "no_peer_targets": []}
    hashes: dict[str, str] = {}
    examples: dict[str, Any] = {}
    if targets.get("pass"):
        family = build_family(
            symbols=list(targets["symbols"]),
            sector_of=dict(targets["sector_of"]),
            sector3650_symbols=list(targets["sector3650_symbols"]),
        )
        if family.get("family_sha256") != FAMILY_SERIALIZATION_SHA256:
            blockers.append("FAMILY_SERIALIZATION_SHA_MISMATCH")
        if int(family.get("family_n") or 0) != FAMILY_N or not family.get("all_target_self_in_driver_false"):
            blockers.append("FAMILY_OR_LTO_FAIL")
        assignment = assign_structural_regimes(symbols=list(targets["symbols"]), sector_of=dict(targets["sector_of"]))
        derived = set(assignment["single_peer_targets"]) | set(assignment["no_peer_targets"])
        if derived != set(BLOCKED_SYMBOLS) or not assignment.get("blocked_symbols_resolved_as_non_multi"):
            blockers.append("BLOCKED_SYMBOL_SET_MISMATCH")
        examples = _examples(assignment, list(targets["sector3650_symbols"]))
        if not examples.get("pass"):
            blockers.append("STRUCTURAL_EXAMPLE_FAIL")
        for row in assignment["rows"]:
            if row["sole_peer"] == row["target_symbol"]:
                blockers.append("TARGET_IN_SECTOR_CONTROL")
        hashes = contract_hashes(assignment=assignment, family_rows=list(family["rows"]))
    dev = list(parent.get("development_dates") or [])
    c1 = list(parent.get("c1_dates") or [])
    discovery = list(parent.get("eligible_dates") or [])
    if sha256_obj(discovery) != DISCOVERY_DATE_SHA256 or parent.get("eligible_day_sha256") != DISCOVERY_DATE_SHA256:
        blockers.append("DISCOVERY_DATE_SHA_MISMATCH")
    if parent.get("fold_boundary_sha256") != DISCOVERY_FOLD_SHA256:
        blockers.append("DISCOVERY_FOLD_SHA_MISMATCH")
    if len(discovery) != DISCOVERY_N or len(dev) != DISCOVERY_DEV_N or len(c1) != DISCOVERY_C1_N:
        blockers.append("DISCOVERY_DATE_N_MISMATCH")
    feas: dict[str, Any] = {"rows": [], "pass": False, "fv_folds": {}, "fail_n": None}
    if not blockers:
        feas = prove_inputs(
            symbols=list(targets["symbols"]),
            sector_of=dict(targets["sector_of"]),
            sector3650=list(targets["sector3650_symbols"]),
            family_rows=list(family["rows"]),
            discovery_dates=discovery,
            mechanisms=list(parent["mechanisms"]),
            assignment_by_symbol=dict(assignment["by_symbol"]),
        )
        if not feas.get("pass"):
            blockers.append("INPUT_FEASIBILITY_FAIL")
        if int(feas.get("old_blocked_resolved_n") or 0) != 34 or int(feas.get("old_blocked_hypothesis_n") or 0) != 34:
            blockers.append("OLD_34_NOT_RESOLVED")
        if feas.get("fv_eligible_day_sha256") != FV_ELIGIBLE_DAY_SHA256 or int(feas.get("fv_eligible_n") or 0) != FV_INPUT_N:
            blockers.append("FV_INPUT_POPULATION_MISMATCH")
        if (feas.get("fv_folds") or {}).get("fold_sha256") != FV_FOLD_SHA256:
            blockers.append("FV_FOLD_SHA_MISMATCH")
    boots: dict[str, Any] = {}
    if discovery and int(feas.get("fv_eligible_n") or 0) == FV_INPUT_N and not any(b.startswith("FV_") or b.startswith("DISCOVERY_DATE") for b in blockers):
        boots = freeze_bootstraps(discovery_dates=discovery, fv_dates=list(feas.get("fv_eligible_dates") or []))
        if (boots.get("discovery") or {}).get("bootstrap_index_sha256") != DISCOVERY_BOOTSTRAP_SHA256:
            blockers.append("DISCOVERY_BOOTSTRAP_SHA_MISMATCH")
        if (boots.get("fv") or {}).get("bootstrap_index_sha256") != FV_BOOTSTRAP_SHA256:
            blockers.append("FV_BOOTSTRAP_SHA_MISMATCH")
    post_hashes = frozen_source_hashes()
    if pre_hashes != post_hashes:
        blockers.append("RUNTIME_NONIMPACT")
    contract: dict[str, Any] = {}
    if not blockers:
        contract = frozen_contract(
            family_hypothesis_sha256=hashes["family_hypothesis_sha256"],
            model_contract_sha256=hashes["model_contract_sha256"],
            symbol_control_contract_sha256=hashes["symbol_control_contract_sha256"],
        )
    ok = not blockers and bool(contract.get("precommit_sha256"))
    return {
        "ok": ok,
        "VERDICT": CASE_READY if ok else CASE_BLOCKED,
        "NEXT": NEXT_RUN if ok else NEXT_RESOLVE,
        "reason": None if ok else (blockers[0] if blockers else "BLOCKED"),
        "old_blocker": OLD_BLOCKER,
        "blockers": list(dict.fromkeys(blockers)),
        "precommit_id": PRECOMMIT_ID,
        "precommit_sha256": contract.get("precommit_sha256") if ok else None,
        "parent_verdict": PARENT_VERDICT,
        "parent_precommit_sha256": PARENT_PRECOMMIT_SHA256,
        "parent_mechanism_set_sha256": PARENT_MECHANISM_SET_SHA256,
        "mechanisms": parent.get("mechanisms"),
        "global_target_n": targets.get("global_target_n"),
        "sector3650_target_n": targets.get("sector3650_target_n"),
        "global_target_set_sha256": targets.get("global_target_set_sha256"),
        "sector3650_target_set_sha256": targets.get("sector3650_target_set_sha256"),
        "sector3650_symbols": targets.get("sector3650_symbols"),
        "family_n": family.get("family_n"),
        "family_hypotheses_unchanged": family.get("family_sha256") == FAMILY_SERIALIZATION_SHA256,
        "family_serialization_sha256": family.get("family_sha256"),
        "family_hypothesis_sha256": hashes.get("family_hypothesis_sha256"),
        "model_contract_sha256": hashes.get("model_contract_sha256"),
        "symbol_control_contract_id": CONTROL_CONTRACT_ID,
        "symbol_control_contract_sha256": hashes.get("symbol_control_contract_sha256"),
        "family_rows": family.get("rows"),
        "lto_all_270": bool(family.get("all_target_self_in_driver_false")) and int(family.get("family_n") or 0) == FAMILY_N,
        "multi_peer_target_n": len(assignment.get("multi_peer_targets") or []),
        "single_peer_target_n": len(assignment.get("single_peer_targets") or []),
        "no_peer_target_n": len(assignment.get("no_peer_targets") or []),
        "peer_structure": assignment.get("peer_structure"),
        "single_peer_map": [r for r in assignment.get("rows") or [] if r.get("control_regime") == REGIME_SINGLE],
        "no_peer_targets": [r for r in assignment.get("rows") or [] if r.get("control_regime") == REGIME_NONE],
        "examples": examples,
        "model_definitions": model_definitions(),
        "input_feasible_all_270": bool(feas.get("pass")),
        "feasibility_fail_n": feas.get("fail_n"),
        "fail_ids": feas.get("fail_ids"),
        "old_blocked_resolved_n": feas.get("old_blocked_resolved_n"),
        "old_34_resolved": int(feas.get("old_blocked_resolved_n") or 0) == 34,
        "feasibility_rows": feas.get("rows"),
        "discovery_date_n": len(discovery),
        "discovery_dev_n": len(dev),
        "discovery_c1_n": len(c1),
        "discovery_date_sha256": DISCOVERY_DATE_SHA256,
        "discovery_fold_sha256": DISCOVERY_FOLD_SHA256,
        "fv_input_eligible_n": feas.get("fv_eligible_n"),
        "fv_eligible_day_sha256": FV_ELIGIBLE_DAY_SHA256 if feas.get("fv_eligible_day_sha256") == FV_ELIGIBLE_DAY_SHA256 else feas.get("fv_eligible_day_sha256"),
        "fv_fold_sha256": FV_FOLD_SHA256 if (feas.get("fv_folds") or {}).get("fold_sha256") == FV_FOLD_SHA256 else (feas.get("fv_folds") or {}).get("fold_sha256"),
        "fv_folds": feas.get("fv_folds"),
        "discovery_bootstrap_sha256": DISCOVERY_BOOTSTRAP_SHA256,
        "fv_bootstrap_sha256": FV_BOOTSTRAP_SHA256,
        "bootstrap_verified_unchanged": (boots.get("discovery") or {}).get("bootstrap_index_sha256") == DISCOVERY_BOOTSTRAP_SHA256 and (boots.get("fv") or {}).get("bootstrap_index_sha256") == FV_BOOTSTRAP_SHA256,
        "bootstraps": {
            "discovery": {**(boots.get("discovery") or {}), "regenerated": False, "verified_equal_to_blocked_v1": (boots.get("discovery") or {}).get("bootstrap_index_sha256") == DISCOVERY_BOOTSTRAP_SHA256},
            "fv": {**(boots.get("fv") or {}), "regenerated": False, "verified_equal_to_blocked_v1": (boots.get("fv") or {}).get("bootstrap_index_sha256") == FV_BOOTSTRAP_SHA256},
        },
        "contract": contract,
        "firewall": fw,
        "symbol_outcomes_opened": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "log_return_computed": False,
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "V4_CHANGED": "V4_CHANGED" in blockers,
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "research_only": True,
        "contamination": {
            **contamination_ledger(),
            "symbol_outcomes_opened": False,
            "FV_economic_outcomes_opened": False,
            "correction_source": "sector constituent count, ex-target peer count, model feasibility",
            "outcomes_not_used": True,
            "9501_NOT_PRIORITIZED": True,
            "9501_NOT_EXCLUDED": True,
            "9501_NOT_REWEIGHTED": True,
            "9501_regime_follows_sector_4050_constituent_n_eq_1": True,
            "no_external_sector_proxy": True,
            "dynamic40_not_used": True,
            "CONCENTRATION_IDENTITIES_NOT_USED_FOR_TARGET_SELECTION": True,
        },
    }
