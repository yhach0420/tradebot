"""Assemble V1.1 precommit. Bind V1 days. Freeze inference only. No discovery outcomes."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed
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
    HORIZONS,
    LOOKBACKS,
    PHASE0_DRIVER_FAMILY_ENUM_MUTATED,
    PHASE0_DRIVER_FAMILY_VALUES,
    PRIMARY_NEXT_DRIVER,
    RESEARCH_FAMILY_ID,
    SCOPE_N,
)
from research.causal_driver_pb1.sector_breadth_precommit.family import family_identity
from research.causal_driver_pb1.sector_breadth_precommit.parent import bind_parent
from research.causal_driver_pb1.sector_breadth_precommit_v1_1 import (
    CASE_BLOCKED,
    CASE_READY,
    NEXT_RESOLVE,
    NEXT_RUN,
    PRECOMMIT_ID,
    SUPERSEDES_PRECOMMIT_SHA256,
    V1_ELIGIBLE_C1_N,
    V1_ELIGIBLE_DAY_SHA256,
    V1_ELIGIBLE_DEV_N,
    V1_FAMILY_384_SHA256,
    V1_FOLD_BOUNDARY_SHA256,
    V1_PERMUTATION_SHA256,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.contract import frozen_contract_v1_1
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.identity import load_v1_frozen_identities
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.inference import (
    freeze_bootstrap_indices,
    inference_spec,
    run_validation_tests,
)


def _firewall() -> dict[str, Any]:
    ledger = AccessLedger(run_id="SBD_PRECOMMIT_V1_1", run_mode=RunMode.DRIVER_DISCOVERY)
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
        "c1_presence_input_only": True,
        "v1_days_inherited_not_regenerated": True,
    }


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
    parent = bind_parent()
    blockers.extend(parent.get("blockers") or [])
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
    if int(2 * SCOPE_N * len(LOOKBACKS) * len(HORIZONS)) != FAMILY_N:
        blockers.append("FAMILY_N_MISMATCH")
    v1 = load_v1_frozen_identities()
    blockers.extend(v1.get("blockers") or [])
    fam = family_identity(eligible_sectors=elig) if ids == EXPECTED_SECTOR_IDS else {}
    if fam.get("family_384_sha256") != V1_FAMILY_384_SHA256:
        blockers.append("FAMILY_384_SHA_DRIFT")
    boot = {}
    tests = {}
    spec = {}
    if not (v1.get("blockers") or []):
        boot = freeze_bootstrap_indices(
            development_dates=list(v1.get("development_dates") or []),
            c1_dates=list(v1.get("c1_dates") or []),
        )
        tests = run_validation_tests(
            development_dates=list(v1.get("development_dates") or []),
            c1_dates=list(v1.get("c1_dates") or []),
            bootstrap_index_sha256=str(boot.get("bootstrap_index_sha256") or ""),
        )
        if not tests.get("pass"):
            blockers.append("INFERENCE_VALIDATION_FAIL")
        spec = inference_spec(
            bootstrap_index_sha256=str(boot.get("bootstrap_index_sha256") or ""),
            development_n=V1_ELIGIBLE_DEV_N,
            c1_n=V1_ELIGIBLE_C1_N,
        )
    days_for_contract = {
        "eligible_day_sha256": V1_ELIGIBLE_DAY_SHA256,
        "folds": v1.get("folds") or {},
        "shuffle": v1.get("shuffle") or {},
    }
    contract: dict[str, Any] = {}
    if not blockers:
        contract = frozen_contract_v1_1(stock=stock, sectors=sectors, days=days_for_contract, inference=spec)
        inf = contract.get("inference") or {}
        if inf.get("ci_method") != "BOOTSTRAP_PERCENTILE_CI":
            blockers.append("CI_METHOD_NOT_FROZEN")
        if inf.get("p_value_method") != "BOOTSTRAP_TWO_SIDED_SIGN_TAIL_PLUS_ONE":
            blockers.append("P_METHOD_NOT_FROZEN")
        if inf.get("bh_m") != 384:
            blockers.append("BH_M_NOT_384")
        if not inf.get("bootstrap_index_sha256"):
            blockers.append("BOOTSTRAP_INDEX_SHA_MISSING")
        if (contract.get("family") or {}).get("n") != FAMILY_N:
            blockers.append("CONTRACT_FAMILY_N")
        if (contract.get("folds") or {}).get("eligible_day_sha256") != V1_ELIGIBLE_DAY_SHA256:
            blockers.append("CONTRACT_DAY_SHA_DRIFT")
        if (contract.get("folds") or {}).get("fold_boundary_sha256") != V1_FOLD_BOUNDARY_SHA256:
            blockers.append("CONTRACT_FOLD_SHA_DRIFT")
        ident = ((contract.get("placebos") or {}).get("sector_identity_specificity_gate") or {})
        if ident.get("label") != "SECTOR_IDENTITY_SPECIFICITY_GATE":
            blockers.append("IDENTITY_GATE_NOT_FROZEN")
    post_hashes = frozen_source_hashes()
    if pre_hashes != post_hashes:
        blockers.append("RUNTIME_NONIMPACT")
    ok = not blockers and bool(contract.get("precommit_sha256"))
    return {
        "ok": ok,
        "VERDICT": CASE_READY if ok else CASE_BLOCKED,
        "NEXT": NEXT_RUN if ok else NEXT_RESOLVE,
        "reason": None if ok else "SECTOR_BREADTH_DISPERSION_PRECOMMIT_V1_1_BLOCKED",
        "blockers": list(dict.fromkeys(blockers)),
        "precommit_id": PRECOMMIT_ID,
        "precommit_sha256": contract.get("precommit_sha256"),
        "supersedes_precommit_sha256": SUPERSEDES_PRECOMMIT_SHA256,
        "PRIMARY_NEXT_DRIVER": PRIMARY_NEXT_DRIVER,
        "RESEARCH_FAMILY_ID": RESEARCH_FAMILY_ID,
        "phase0_DriverFamily_enum_mutated": PHASE0_DRIVER_FAMILY_ENUM_MUTATED,
        "phase0_observation_family_if_later_bound": "JP_INTERNAL",
        "parent": parent,
        "v1": {k: v for k, v in v1.items() if k not in {"days", "folds", "shuffle", "eligible_dates", "development_dates", "c1_dates"}},
        "LEADER_LAGGARD_REOPENED": False,
        "USDJPY_REOPENED": False,
        "SECTOR_BREADTH_DISPERSION_OUTCOMES_OPENED": False,
        "C1_outcomes_opened": False,
        "candidate_list_sha256": None,
        "universe105_sha256": sectors.get("universe105_sha256"),
        "sector_mapping_sha256": sectors.get("sector_mapping_sha256"),
        "eligible_sector_n": len(elig),
        "scope_n": SCOPE_N,
        "family_n": FAMILY_N,
        "family_384_sha256": fam.get("family_384_sha256"),
        "eligible_day_sha256": V1_ELIGIBLE_DAY_SHA256,
        "eligible_dev_n": V1_ELIGIBLE_DEV_N,
        "eligible_c1_n": V1_ELIGIBLE_C1_N,
        "fold_boundary_sha256": V1_FOLD_BOUNDARY_SHA256,
        "permutation_sha256": V1_PERMUTATION_SHA256,
        "bootstrap_index_sha256": boot.get("bootstrap_index_sha256"),
        "bootstrap": boot,
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
        "contract": contract,
        "diff": {
            "changed": {
                "bootstrap_block_index_algorithm": "explicit DATE_BLOCK_BOOTSTRAP with-replacement RandomState.randint",
                "bootstrap_index_sha256": boot.get("bootstrap_index_sha256"),
                "ci_method": "BOOTSTRAP_PERCENTILE_CI 2.5/97.5 linear; bound==0 FAIL",
                "p_value_method": "BOOTSTRAP_TWO_SIDED_SIGN_TAIL_PLUS_ONE",
                "bh_q_algorithm": "monotone BH q_(i)=min_j>=i (m/j p_(j)) cap 1; canonical order; m=384",
                "precommit_sha": {
                    "v1": SUPERSEDES_PRECOMMIT_SHA256,
                    "v1_1": contract.get("precommit_sha256"),
                    "changed": True,
                },
            },
            "unchanged": {
                "universe105_sha256": EXPECTED_UNIVERSE_SHA256,
                "sector_mapping_sha256": EXPECTED_SECTOR_MAPPING_SHA256,
                "eligible_day_sha256": V1_ELIGIBLE_DAY_SHA256,
                "fold_boundary_sha256": V1_FOLD_BOUNDARY_SHA256,
                "permutation_sha256": V1_PERMUTATION_SHA256,
                "family_n": 384,
                "metrics_scopes_lookbacks_horizons": True,
                "models_controls_freshness": True,
                "d1_d7_logical_structure": True,
                "c1_c7_logical_structure": True,
                "placebos": True,
                "fv_firewall": True,
                "prospective_firewall": True,
            },
        },
        "contamination": {
            **contamination_ledger(),
            "leader_laggard_near_misses_used": False,
            "leader_laggard_reopened": False,
            "usdjpy_reopened": False,
            "sector_breadth_dispersion_outcomes_opened": False,
            "v1_days_regenerated": False,
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
        "v1_out_not_overwritten": True,
    }
