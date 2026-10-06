"""Assemble V1.2 precommit. Structural control-gate correction only. No discovery rerun."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1 import (
    EXPECTED_COMPLETE_STRATEGY_SHA256,
    EXPECTED_V4_MACHINE_SHA256,
    FV_FIRST,
    PROSPECTIVE_FROM,
)
from research.causal_driver_pb1.contracts.enums import AccessKind, DatasetRole, DriverFamily, RunMode
from research.causal_driver_pb1.contracts.errors import FirewallDenied
from research.causal_driver_pb1.cross_sectional_precommit.contamination import contamination_ledger
from research.causal_driver_pb1.datasets.firewall import AccessLedger, request_dataset
from research.causal_driver_pb1.identity.pin import bind_identities, frozen_source_hashes
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.causal_driver_pb1.phase2_precommit.stock_semantics import prove_stock_timestamp_semantics
from research.causal_driver_pb1.response.cases import run_resolver_tests
from research.causal_driver_pb1.sector_breadth_precommit import (
    EXPECTED_SECTOR_IDS,
    EXPECTED_SECTOR_MAPPING_SHA256,
    EXPECTED_SECTOR_N,
    EXPECTED_UNIVERSE_N,
    EXPECTED_UNIVERSE_SHA256,
    FAMILY_N,
    PHASE0_DRIVER_FAMILY_ENUM_MUTATED,
    PHASE0_DRIVER_FAMILY_VALUES,
    PRIMARY_NEXT_DRIVER,
    RESEARCH_FAMILY_ID,
    SCOPE_N,
)
from research.causal_driver_pb1.sector_breadth_precommit.family import family_identity
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.inference import inference_spec, run_validation_tests
from research.causal_driver_pb1.sector_breadth_precommit_v1_2 import (
    AFFECTED_CONSTITUENT_N,
    AFFECTED_SECTOR_ID,
    AFFECTED_TEST_N,
    CASE_BLOCKED,
    CASE_READY,
    NEXT_RESOLVE,
    NEXT_RUN,
    PRECOMMIT_ID,
    SUPERSEDES_PRECOMMIT_SHA256,
    V1_BOOTSTRAP_INDEX_SHA256,
    V1_ELIGIBLE_C1_N,
    V1_ELIGIBLE_DAY_SHA256,
    V1_ELIGIBLE_DEV_N,
    V1_FAMILY_384_SHA256,
    V1_FOLD_BOUNDARY_SHA256,
    V1_PERMUTATION_SHA256,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.contract import frozen_contract_v1_2
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.control_gate import (
    control_gate_spec,
    new_gate_structurally_possible,
    old_gate_structurally_possible,
    required_ex_sector_valid_n,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.feasibility import sector_structural_feasibility
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.identity import bind_v11_identities
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.invalidation import classify_old_discovery


def _firewall() -> dict[str, Any]:
    ledger = AccessLedger(run_id="SBD_PRECOMMIT_V1_2", run_mode=RunMode.DRIVER_DISCOVERY)
    request_dataset(
        ledger=ledger,
        run_mode=RunMode.DRIVER_DISCOVERY,
        dataset_id="RESEARCH_OBSERVATION_UNIVERSE_105",
        dataset_role=DatasetRole.DEVELOPMENT,
        requested_fields=("symbol", "tse33_code", "tse33_name"),
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
    return {
        "fv_denied": fv,
        "prospective_denied": pr,
        "pass": fv and pr and date_ok,
        "c1_future_return_outcomes": False,
        "c1_not_accessed": True,
        "corrected_discovery_not_run": True,
    }


def _unit_tests() -> dict[str, Any]:
    n75 = 75
    old75 = old_gate_structurally_possible(n75)
    new_req75 = required_ex_sector_valid_n(n75)
    t3650 = (not old75) and new_req75 == 60 and new_gate_structurally_possible(n75)
    n91 = 91
    req91 = required_ex_sector_valid_n(n91)
    tnorm = req91 == 80 and req91 != 73 and old_gate_structurally_possible(n91)
    rows = [
        {"id": "T_3650", "N_EX_SECTOR": n75, "old_required": 80, "old_possible": old75, "new_required": new_req75, "new_possible": new_gate_structurally_possible(n75), "pass": t3650},
        {"id": "T_NORMAL", "N_EX_SECTOR": n91, "required": req91, "not": 73, "pass": tnorm},
    ]
    return {"pass": bool(t3650 and tnorm), "rows": rows}


def evaluate() -> dict[str, Any]:
    blockers: list[str] = []
    resolver_tests = run_resolver_tests()
    if not all(r.get("pass") for r in resolver_tests):
        blockers.append("RESOLVER_UNIT_TEST_FAIL")
    identity = bind_identities()
    pre_hashes = frozen_source_hashes()
    if identity.get("V4_MACHINE_SHA256") != EXPECTED_V4_MACHINE_SHA256:
        blockers.append("V4_CHANGED")
    if identity.get("COMPLETE_STRATEGY_SHA256") != EXPECTED_COMPLETE_STRATEGY_SHA256:
        blockers.append("COMPLETE_STRATEGY_CHANGED")
    if tuple(m.value for m in DriverFamily) != PHASE0_DRIVER_FAMILY_VALUES:
        blockers.append("PHASE0_DRIVERFAMILY_ENUM_MUTATED")
    fw = _firewall()
    if not fw.get("pass"):
        blockers.append("FIREWALL")
    stock = prove_stock_timestamp_semantics()
    if not stock.get("pass"):
        blockers.append("STOCK_TIMESTAMP_SEMANTICS")
    sectors = bind_sector_mapping()
    if sectors.get("universe105_sha256") != EXPECTED_UNIVERSE_SHA256:
        blockers.append("UNIVERSE105_SHA_MISMATCH")
    if sectors.get("sector_mapping_sha256") != EXPECTED_SECTOR_MAPPING_SHA256:
        blockers.append("SECTOR_MAPPING_SHA_MISMATCH")
    if int(sectors.get("universe105_count") or 0) != EXPECTED_UNIVERSE_N:
        blockers.append("UNIVERSE_N_MISMATCH")
    elig = list(sectors.get("eligible_sectors") or [])
    ids = tuple(sorted(str(s["sector_id"]) for s in elig))
    if ids != EXPECTED_SECTOR_IDS or len(elig) != EXPECTED_SECTOR_N:
        blockers.append("ELIGIBLE_SECTOR_SET_MISMATCH")
    s3650 = next((s for s in elig if str(s["sector_id"]) == AFFECTED_SECTOR_ID), None)
    if s3650 is None or int(s3650.get("constituent_n") or 0) != AFFECTED_CONSTITUENT_N:
        blockers.append("SECTOR_3650_CONSTITUENT_N_MISMATCH")
    bound = bind_v11_identities()
    blockers.extend(bound.get("blockers") or [])
    if int(bound.get("C1_rows_read_before_candidate_freeze") or 0) != 0:
        blockers.append("C1_ROWS_BEFORE_FREEZE")
    if bound.get("C1_opened"):
        blockers.append("C1_OPENED")
    inv = classify_old_discovery()
    if not inv.get("all_32_model_n_zero"):
        blockers.append("AFFECTED_32_NOT_ALL_MODEL_N_ZERO")
    if not inv.get("control_gate_impossibility_not_missing_driver"):
        blockers.append("AFFECTED_32_DRIVER_DATA_MISSING")
    if int(inv.get("C1_rows_read_before_candidate_freeze") or 0) != 0:
        blockers.append("INV_C1_ROWS_BEFORE_FREEZE")
    units = _unit_tests()
    if not units.get("pass"):
        blockers.append("CONTROL_GATE_UNIT_TEST_FAIL")
    feas = {}
    if ids == EXPECTED_SECTOR_IDS:
        feas = sector_structural_feasibility(
            eligible_sectors=elig,
            mapping_rows=list(sectors.get("rows") or []),
            development_dates=list(bound.get("development_dates") or []),
        )
        if not feas.get("pass"):
            blockers.append("STRUCTURAL_FEASIBILITY_FAIL")
    fam = family_identity(eligible_sectors=elig) if ids == EXPECTED_SECTOR_IDS else {}
    if fam.get("family_384_sha256") != V1_FAMILY_384_SHA256:
        blockers.append("FAMILY_384_SHA_DRIFT")
    spec = {}
    tests = {}
    if not (bound.get("blockers") or []):
        tests = run_validation_tests(
            development_dates=list(bound.get("development_dates") or []),
            c1_dates=list(bound.get("c1_dates") or []),
            bootstrap_index_sha256=V1_BOOTSTRAP_INDEX_SHA256,
        )
        if not tests.get("pass"):
            blockers.append("INFERENCE_VALIDATION_FAIL")
        spec = inference_spec(
            bootstrap_index_sha256=V1_BOOTSTRAP_INDEX_SHA256,
            development_n=V1_ELIGIBLE_DEV_N,
            c1_n=V1_ELIGIBLE_C1_N,
        )
    days_for_contract = {
        "eligible_day_sha256": V1_ELIGIBLE_DAY_SHA256,
        "folds": bound.get("folds") or {},
        "shuffle": bound.get("shuffle") or {},
    }
    contract: dict[str, Any] = {}
    if not blockers:
        contract = frozen_contract_v1_2(stock=stock, sectors=sectors, days=days_for_contract, inference=spec)
        if (contract.get("family") or {}).get("n") != FAMILY_N:
            blockers.append("CONTRACT_FAMILY_N")
        if (contract.get("family") or {}).get("family_384_sha256") != V1_FAMILY_384_SHA256:
            blockers.append("CONTRACT_FAMILY_SHA_DRIFT")
        if (contract.get("folds") or {}).get("eligible_day_sha256") != V1_ELIGIBLE_DAY_SHA256:
            blockers.append("CONTRACT_DAY_SHA_DRIFT")
        if (contract.get("folds") or {}).get("fold_boundary_sha256") != V1_FOLD_BOUNDARY_SHA256:
            blockers.append("CONTRACT_FOLD_SHA_DRIFT")
        rule = ((contract.get("controls") or {}).get("mkt_ex_required_valid_n_rule") or {})
        if rule.get("if_N_EX_SECTOR_PIT_ge_80") != "REQUIRED_EX_SECTOR_VALID_N = 80":
            blockers.append("CONTROL_RULE_NOT_FROZEN")
        if not rule.get("not_unconditional_80pct_relaxation"):
            blockers.append("UNCONDITIONAL_RELAXATION_NOT_FORBIDDEN")
    post_hashes = frozen_source_hashes()
    if pre_hashes != post_hashes:
        blockers.append("RUNTIME_NONIMPACT")
    ok = not blockers and bool(contract.get("precommit_sha256"))
    gate = control_gate_spec()
    return {
        "ok": ok,
        "VERDICT": CASE_READY if ok else CASE_BLOCKED,
        "NEXT": NEXT_RUN if ok else NEXT_RESOLVE,
        "reason": None if ok else "SECTOR_BREADTH_DISPERSION_PRECOMMIT_V1_2_BLOCKED",
        "blockers": list(dict.fromkeys(blockers)),
        "precommit_id": PRECOMMIT_ID,
        "precommit_sha256": contract.get("precommit_sha256"),
        "supersedes_precommit_sha256": SUPERSEDES_PRECOMMIT_SHA256,
        "PRIMARY_NEXT_DRIVER": PRIMARY_NEXT_DRIVER,
        "RESEARCH_FAMILY_ID": RESEARCH_FAMILY_ID,
        "phase0_DriverFamily_enum_mutated": PHASE0_DRIVER_FAMILY_ENUM_MUTATED,
        "invalidation": inv,
        "control_gate": gate,
        "unit_tests": units,
        "feasibility": {k: v for k, v in feas.items() if k != "matrix"} | {"matrix": feas.get("matrix") or []},
        "LEADER_LAGGARD_REOPENED": False,
        "USDJPY_REOPENED": False,
        "DEV_OUTCOMES_PREVIOUSLY_OPENED": True,
        "fresh_blind_first_look": False,
        "SECTOR_BREADTH_DISPERSION_CORRECTED_OUTCOMES_OPENED": False,
        "C1_outcomes_opened": False,
        "C1_opened": False,
        "C1_rows_read_before_candidate_freeze": 0,
        "candidate_list_sha256": None,
        "universe105_sha256": sectors.get("universe105_sha256"),
        "sector_mapping_sha256": sectors.get("sector_mapping_sha256"),
        "eligible_sector_n": len(elig),
        "scope_n": SCOPE_N,
        "family_n": FAMILY_N,
        "family_384_sha256": fam.get("family_384_sha256") or V1_FAMILY_384_SHA256,
        "eligible_day_sha256": V1_ELIGIBLE_DAY_SHA256,
        "eligible_dev_n": V1_ELIGIBLE_DEV_N,
        "eligible_c1_n": V1_ELIGIBLE_C1_N,
        "fold_boundary_sha256": V1_FOLD_BOUNDARY_SHA256,
        "permutation_sha256": V1_PERMUTATION_SHA256,
        "bootstrap_index_sha256": V1_BOOTSTRAP_INDEX_SHA256,
        "bootstrap": bound.get("bootstrap") or {},
        "inference_tests": tests,
        "resolver_tests": resolver_tests,
        "identity": {k: v for k, v in identity.items() if k != "source_inventory"},
        "firewall": fw,
        "stock": stock,
        "sectors": {
            "universe105_sha256": sectors.get("universe105_sha256"),
            "sector_mapping_sha256": sectors.get("sector_mapping_sha256"),
            "eligible_sectors": elig,
            "ineligible_sectors": sectors.get("ineligible_sectors"),
            "rows": sectors.get("rows"),
        },
        "days": days_for_contract,
        "family_384": fam.get("tests") or [],
        "affected_test_n": AFFECTED_TEST_N,
        "contract": contract,
        "bound": {k: v for k, v in bound.items() if k not in {"v1", "folds", "shuffle", "eligible_dates", "development_dates", "c1_dates"}},
        "diff": {
            "changed": {
                "mkt_ex_required_valid_n": "80 if N_EX_SECTOR_PIT>=80 else ceil(0.80*N_EX_SECTOR_PIT)",
                "always_valid_fraction_ge_0_80": True,
                "old_discovery_classification": inv.get("old_discovery_classification"),
                "precommit_sha": {
                    "v1_1": SUPERSEDES_PRECOMMIT_SHA256,
                    "v1_2": contract.get("precommit_sha256"),
                    "changed": True,
                },
            },
            "unchanged": {
                "universe105_sha256": EXPECTED_UNIVERSE_SHA256,
                "sector_mapping_sha256": EXPECTED_SECTOR_MAPPING_SHA256,
                "eligible_day_sha256": V1_ELIGIBLE_DAY_SHA256,
                "fold_boundary_sha256": V1_FOLD_BOUNDARY_SHA256,
                "permutation_sha256": V1_PERMUTATION_SHA256,
                "bootstrap_index_sha256": V1_BOOTSTRAP_INDEX_SHA256,
                "family_n": 384,
                "family_384_sha256": V1_FAMILY_384_SHA256,
                "metrics_scopes_lookbacks_horizons": True,
                "ci_p_bh": True,
                "d1_d7_logical_structure": True,
                "c1_c7_logical_structure": True,
                "placebos": True,
                "fv_firewall": True,
                "prospective_firewall": True,
                "other_sectors_absolute_80_when_feasible": True,
            },
        },
        "contamination": {
            **contamination_ledger(),
            "DEV_OUTCOMES_PREVIOUSLY_OPENED": True,
            "fresh_blind_first_look": False,
            "correction_derived_from": ["universe size", "sector membership count", "control-gate arithmetic"],
            "correction_not_derived_from": ["beta", "p", "q", "near-miss ranking", "direction", "economic result"],
            "sector_3200_breadth_not_used": True,
            "sector_3600_breadth_not_used": True,
            "global_breadth_not_used": True,
            "q_0548_cases_not_used": True,
            "leader_laggard_near_misses_used": False,
            "leader_laggard_reopened": False,
            "usdjpy_reopened": False,
            "c1_outcomes_opened": False,
            "v1_days_regenerated": False,
            "v1_1_bootstrap_regenerated": False,
        },
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "V4_CHANGED": "V4_CHANGED" in blockers,
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "research_only": True,
        "discovery_not_run": True,
        "corrected_discovery_not_run": True,
        "v1_out_not_overwritten": True,
        "v1_1_out_not_overwritten": True,
        "discovery_v1_out_not_overwritten": True,
    }
