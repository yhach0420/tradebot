"""Assemble the transmission precommit. Input feasibility only. No symbol betas."""
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
from research.causal_driver_pb1.sector_state_transmission_precommit import (
    CASE_BLOCKED,
    CASE_READY,
    FAMILY_N,
    NEXT_RESOLVE,
    NEXT_RUN,
    PARENT_PRECOMMIT_SHA256,
    PARENT_VERDICT,
    PRECOMMIT_ID,
)
from research.causal_driver_pb1.sector_state_transmission_precommit.bootstrap import freeze_bootstraps
from research.causal_driver_pb1.sector_state_transmission_precommit.contract import frozen_contract
from research.causal_driver_pb1.sector_state_transmission_precommit.family import build_family
from research.causal_driver_pb1.sector_state_transmission_precommit.feasibility import prove_inputs
from research.causal_driver_pb1.sector_state_transmission_precommit.parent import bind_parent
from research.causal_driver_pb1.sector_state_transmission_precommit.targets import bind_targets


def _firewall() -> dict[str, Any]:
    ledger = AccessLedger(run_id="SYMBOL_TRANSMISSION_PRECOMMIT_V1", run_mode=RunMode.DRIVER_DISCOVERY)
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
    return {
        "fv_economic_payload_denied": fv,
        "prospective_denied": pr,
        "fv_input_presence_only": True,
        "pass": fv and pr and date_ok,
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
    targets = bind_targets()
    blockers.extend(targets.get("blockers") or [])
    family = {"rows": [], "family_n": 0, "family_sha256": None, "pass": False, "all_target_self_in_driver_false": False}
    if targets.get("pass"):
        family = build_family(
            symbols=list(targets["symbols"]),
            sector_of=dict(targets["sector_of"]),
            sector3650_symbols=list(targets["sector3650_symbols"]),
        )
        if not family.get("pass"):
            blockers.append("FAMILY_OR_LTO_FAIL")
        if int(family.get("family_n") or 0) != FAMILY_N:
            blockers.append("FAMILY_N")
    dev = list(parent.get("development_dates") or [])
    c1 = list(parent.get("c1_dates") or [])
    eligible = list(parent.get("eligible_dates") or [])
    discovery = list(eligible)
    if discovery and sha256_obj(discovery) != parent.get("eligible_day_sha256"):
        blockers.append("DISCOVERY_DATE_SHA_MISMATCH")
    if set(dev) | set(c1) != set(eligible):
        blockers.append("DISCOVERY_PARTITION_MISMATCH")
    feas: dict[str, Any] = {"rows": [], "pass": False, "fv_folds": {}, "fail_n": None}
    boots: dict[str, Any] = {}
    if not blockers:
        feas = prove_inputs(
            symbols=list(targets["symbols"]),
            sector_of=dict(targets["sector_of"]),
            sector3650=list(targets["sector3650_symbols"]),
            family_rows=list(family["rows"]),
            discovery_dates=discovery,
            mechanisms=list(parent["mechanisms"]),
        )
        if not feas.get("pass"):
            blockers.append("INPUT_FEASIBILITY_FAIL")
        if int(feas.get("fv_eligible_n") or 0) < 3:
            blockers.append("FV_FOLD_INFEASIBLE")
        if int(feas.get("structural_peer_infeasible_n") or 0) > 0:
            blockers.append("SECTOR_EX_TARGET_PEER_N_GE_2_IMPOSSIBLE")
    if discovery and int(feas.get("fv_eligible_n") or 0) >= 1:
        boots = freeze_bootstraps(discovery_dates=discovery, fv_dates=list(feas.get("fv_eligible_dates") or []))
    post_hashes = frozen_source_hashes()
    if pre_hashes != post_hashes:
        blockers.append("RUNTIME_NONIMPACT")
    contract: dict[str, Any] = {}
    if boots and feas.get("fv_eligible_day_sha256") and family.get("family_sha256"):
        contract = frozen_contract(
            parent_mechanism_set_sha256=parent["parent_mechanism_set_sha256"],
            global_target_set_sha256=targets["global_target_set_sha256"],
            sector3650_target_set_sha256=targets["sector3650_target_set_sha256"],
            family_sha256=family["family_sha256"],
            discovery_date_sha256=sha256_obj(discovery),
            fv_eligible_day_sha256=feas.get("fv_eligible_day_sha256"),
            fv_fold_sha256=(feas.get("fv_folds") or {}).get("fold_sha256"),
            discovery_bootstrap_sha256=(boots.get("discovery") or {}).get("bootstrap_index_sha256"),
            fv_bootstrap_sha256=(boots.get("fv") or {}).get("bootstrap_index_sha256"),
        )
    ok = not blockers and bool(contract.get("precommit_sha256"))
    return {
        "ok": ok,
        "VERDICT": CASE_READY if ok else CASE_BLOCKED,
        "NEXT": NEXT_RUN if ok else NEXT_RESOLVE,
        "reason": None if ok else ("SECTOR_EX_TARGET_PEER_N_GE_2_IMPOSSIBLE" if "SECTOR_EX_TARGET_PEER_N_GE_2_IMPOSSIBLE" in blockers else "SECTOR_STATE_SYMBOL_TRANSMISSION_PRECOMMIT_BLOCKED"),
        "blockers": list(dict.fromkeys(blockers)),
        "precommit_id": PRECOMMIT_ID,
        "precommit_sha256": contract.get("precommit_sha256") if ok else None,
        "parent_verdict": PARENT_VERDICT,
        "parent_precommit_sha256": PARENT_PRECOMMIT_SHA256,
        "parent_mechanism_n": len(parent.get("mechanisms") or []),
        "parent_mechanism_set_sha256": parent.get("parent_mechanism_set_sha256"),
        "mechanisms": parent.get("mechanisms"),
        "global_target_n": targets.get("global_target_n"),
        "sector3650_target_n": targets.get("sector3650_target_n"),
        "global_target_set_sha256": targets.get("global_target_set_sha256"),
        "sector3650_target_set_sha256": targets.get("sector3650_target_set_sha256"),
        "sector3650_symbols": targets.get("sector3650_symbols"),
        "universe105_sha256": targets.get("universe105_sha256"),
        "family_n": family.get("family_n"),
        "family_sha256": family.get("family_sha256"),
        "family_rows": family.get("rows"),
        "lto_all_270": bool(family.get("all_target_self_in_driver_false")) and int(family.get("family_n") or 0) == FAMILY_N,
        "input_feasible_all_270": bool(feas.get("pass")),
        "feasibility_fail_n": feas.get("fail_n"),
        "structural_peer_infeasible_n": feas.get("structural_peer_infeasible_n"),
        "structural_peer_symbols": feas.get("structural_peer_symbols"),
        "feasibility_rows": feas.get("rows"),
        "discovery_dates": discovery,
        "discovery_dev_dates": dev,
        "discovery_c1_dates": c1,
        "fv_eligible_dates": list(feas.get("fv_eligible_dates") or []),
        "discovery_date_n": len(discovery),
        "discovery_dev_n": len(dev),
        "discovery_c1_n": len(c1),
        "discovery_date_sha256": sha256_obj(discovery) if discovery else None,
        "discovery_fold_sha256": parent.get("fold_boundary_sha256"),
        "fv_input_eligible_n": feas.get("fv_eligible_n"),
        "fv_eligible_day_sha256": feas.get("fv_eligible_day_sha256"),
        "fv_fold_sha256": (feas.get("fv_folds") or {}).get("fold_sha256"),
        "fv_folds": feas.get("fv_folds"),
        "discovery_bootstrap_sha256": (boots.get("discovery") or {}).get("bootstrap_index_sha256"),
        "fv_bootstrap_sha256": (boots.get("fv") or {}).get("bootstrap_index_sha256"),
        "bootstraps": boots,
        "contract": contract,
        "firewall": fw,
        "identity": {k: v for k, v in identity.items() if k != "source_inventory"},
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
            "CONCENTRATION_IDENTITIES_NOT_USED_FOR_TARGET_SELECTION": True,
            "dynamic40_not_used": True,
            "failed_parent_mechanisms_not_reopened": True,
            "symbol_future_return_beta_not_computed": True,
            "fv_economic_outcomes_not_opened": True,
            "prospective_not_opened": True,
        },
    }
