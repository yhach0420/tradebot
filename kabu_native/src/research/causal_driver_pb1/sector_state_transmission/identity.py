"""Startup identity check. No outcome rows."""
from __future__ import annotations

import json
from typing import Any

import numpy as np

from research.causal_driver_pb1.identity.ids import sha256_bytes, sha256_obj
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.inference import draw_date_block_indices
from research.causal_driver_pb1.sector_state_transmission import PRECOMMIT_ID, PRECOMMIT_SHA256
from research.causal_driver_pb1.sector_state_transmission_precommit import BOOTSTRAP_N, DISCOVERY_BOOTSTRAP_SEED
from research.causal_driver_pb1.sector_state_transmission_precommit.bootstrap import freeze_bootstraps
from research.causal_driver_pb1.sector_state_transmission_precommit.family import build_family
from research.causal_driver_pb1.sector_state_transmission_precommit.parent import bind_parent
from research.causal_driver_pb1.sector_state_transmission_precommit.targets import bind_targets
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1 import (
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
    GLOBAL_TARGET_SET_SHA256,
    PARENT_MECHANISM_SET_SHA256,
    PARENT_PRECOMMIT_SHA256,
    SECTOR3650_TARGET_SET_SHA256,
)
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1.contract import frozen_contract
from research.causal_driver_pb1.sector_state_transmission_precommit.isolation import DISC_V2_OUT
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1.isolation import OUT as PRECOMMIT_OUT
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1.regimes import (
    REGIME_MULTI,
    assign_structural_regimes,
    contract_hashes,
)


def _boot_index(*, dates: list[str], seed: int) -> np.ndarray:
    return draw_date_block_indices(n_day=len(dates), n_boot=BOOTSTRAP_N, seed=int(seed))


def verify_identity() -> dict[str, Any]:
    blockers: list[str] = []
    report = json.loads((PRECOMMIT_OUT / "report.json").read_text(encoding="utf-8"))
    answers = report.get("answers") or {}
    if answers.get("precommit_id") != PRECOMMIT_ID or answers.get("precommit_sha256") != PRECOMMIT_SHA256:
        blockers.append("PRECOMMIT_SHA_MISMATCH")
    parent = bind_parent()
    targets = bind_targets()
    blockers.extend(parent.get("blockers") or [])
    blockers.extend(targets.get("blockers") or [])
    if parent.get("parent_mechanism_set_sha256") != PARENT_MECHANISM_SET_SHA256:
        blockers.append("PARENT_MECHANISM_SHA_MISMATCH")
    if answers.get("parent_precommit_sha256") != PARENT_PRECOMMIT_SHA256:
        blockers.append("PARENT_PRECOMMIT_SHA_MISMATCH")
    if targets.get("global_target_set_sha256") != GLOBAL_TARGET_SET_SHA256:
        blockers.append("GLOBAL_TARGET_SHA_MISMATCH")
    if targets.get("sector3650_target_set_sha256") != SECTOR3650_TARGET_SET_SHA256:
        blockers.append("SECTOR3650_TARGET_SHA_MISMATCH")
    family = {"rows": [], "family_n": 0, "family_sha256": None, "pass": False, "all_target_self_in_driver_false": False}
    assignment: dict[str, Any] = {"by_symbol": {}, "rows": []}
    hashes: dict[str, str] = {}
    if targets.get("pass"):
        family = build_family(
            symbols=list(targets["symbols"]),
            sector_of=dict(targets["sector_of"]),
            sector3650_symbols=list(targets["sector3650_symbols"]),
        )
        assignment = assign_structural_regimes(symbols=list(targets["symbols"]), sector_of=dict(targets["sector_of"]))
        hashes = contract_hashes(assignment=assignment, family_rows=list(family["rows"]))
    if family.get("family_sha256") != FAMILY_SERIALIZATION_SHA256 or int(family.get("family_n") or 0) != FAMILY_N:
        blockers.append("FAMILY_SHA_MISMATCH")
    if not family.get("all_target_self_in_driver_false"):
        blockers.append("LTO_VIOLATION")
    for key, expect in (
        ("family_hypothesis_sha256", "7ca303a5bafa9d05f86f50c48808faae4e77f39c7dfde02b42a19c6d60e121e8"),
        ("model_contract_sha256", "a247c63489640ae8491b931b9669d9ed18cef075a187b62a6bb72d2a991c7db4"),
        ("symbol_control_contract_sha256", "f7ac0d5a32ab7e7874694619ff19d667dd88eae8552a13421a9bbfd21a45744a"),
    ):
        if hashes.get(key) != expect or answers.get(key) != expect:
            blockers.append("MODEL_CONTRACT_SHA_MISMATCH")
    if hashes.get("symbol_control_contract_id") != CONTROL_CONTRACT_ID:
        blockers.append("CONTROL_CONTRACT_ID_MISMATCH")
    if len(assignment.get("multi_peer_targets") or []) != 88:
        blockers.append("REGIME_COUNT_MISMATCH")
    if len(assignment.get("single_peer_targets") or []) != 10 or len(assignment.get("no_peer_targets") or []) != 7:
        blockers.append("REGIME_COUNT_MISMATCH")
    for sym in targets.get("sector3650_symbols") or []:
        if (assignment.get("by_symbol") or {}).get(sym, {}).get("control_regime") != REGIME_MULTI:
            blockers.append("SECTOR3650_REGIME_MISMATCH")
    ev = report.get("evaluation") or {}
    v2 = json.loads((DISC_V2_OUT / "report.json").read_text(encoding="utf-8"))
    v2_bound = ((v2.get("evaluation") or {}).get("bound") or {})
    discovery = [str(d) for d in (v2_bound.get("eligible_dates") or [])]
    dev = [str(d) for d in (v2_bound.get("development_dates") or [])]
    c1 = [str(d) for d in (v2_bound.get("c1_dates") or [])]
    folds = ev.get("fv_folds") or {}
    fv_dates = [str(d) for d in list(folds.get("FV_EARLY") or []) + list(folds.get("FV_MIDDLE") or []) + list(folds.get("FV_LATE") or [])]
    if sha256_obj(discovery) != DISCOVERY_DATE_SHA256 or len(discovery) != DISCOVERY_N:
        blockers.append("DISCOVERY_DATE_SHA_MISMATCH")
    if len(dev) != DISCOVERY_DEV_N or len(c1) != DISCOVERY_C1_N or set(dev) | set(c1) != set(discovery):
        blockers.append("DISCOVERY_FOLD_MISMATCH")
    if answers.get("discovery_fold_sha256") != DISCOVERY_FOLD_SHA256:
        blockers.append("DISCOVERY_FOLD_SHA_MISMATCH")
    folds = ev.get("fv_folds") or {}
    fold_payload = {
        "split_rule": "timestamp_sorted_date_count_equal_split_remainder_to_last_fold",
        "FV_EARLY": list(folds.get("FV_EARLY") or []),
        "FV_MIDDLE": list(folds.get("FV_MIDDLE") or []),
        "FV_LATE": list(folds.get("FV_LATE") or []),
        "outcome_used": False,
    }
    if sha256_obj(fv_dates) != FV_ELIGIBLE_DAY_SHA256 or sha256_obj(fold_payload) != FV_FOLD_SHA256:
        blockers.append("FV_DATE_SHA_MISMATCH")
    boots = freeze_bootstraps(discovery_dates=discovery, fv_dates=fv_dates) if discovery and fv_dates else {}
    if (boots.get("discovery") or {}).get("bootstrap_index_sha256") != DISCOVERY_BOOTSTRAP_SHA256:
        blockers.append("DISCOVERY_BOOTSTRAP_SHA_MISMATCH")
    if (boots.get("fv") or {}).get("bootstrap_index_sha256") != FV_BOOTSTRAP_SHA256:
        blockers.append("FV_BOOTSTRAP_SHA_MISMATCH")
    contract = {}
    if not blockers:
        contract = frozen_contract(
            family_hypothesis_sha256=hashes["family_hypothesis_sha256"],
            model_contract_sha256=hashes["model_contract_sha256"],
            symbol_control_contract_sha256=hashes["symbol_control_contract_sha256"],
        )
        if contract.get("precommit_sha256") != PRECOMMIT_SHA256:
            blockers.append("PRECOMMIT_SHA_MISMATCH")
    boot_index = _boot_index(dates=discovery, seed=DISCOVERY_BOOTSTRAP_SEED) if discovery and not blockers else None
    if boot_index is not None:
        got = sha256_bytes(np.ascontiguousarray(boot_index, dtype="<i8").tobytes())
        if got != (boots.get("discovery") or {}).get("index_sha256"):
            blockers.append("DISCOVERY_BOOTSTRAP_SHA_MISMATCH")
    return {
        "pass": not blockers,
        "blockers": list(dict.fromkeys(blockers)),
        "parent": parent,
        "targets": targets,
        "family": family,
        "assignment": assignment,
        "hashes": hashes,
        "discovery_dates": discovery,
        "dev_dates": dev,
        "c1_dates": c1,
        "fv_dates": fv_dates,
        "fv_folds": fold_payload,
        "discovery_boot_index": boot_index,
        "precommit_sha256": PRECOMMIT_SHA256 if not blockers else None,
    }
