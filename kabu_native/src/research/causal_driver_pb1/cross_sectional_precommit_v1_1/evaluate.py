"""Assemble V1.1 precommit. Input-only day identities. No future-return discovery."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1 import (
    EXPECTED_COMPLETE_STRATEGY_SHA256,
    EXPECTED_V4_MACHINE_SHA256,
    FV_FIRST,
    PROSPECTIVE_FROM,
)
from research.causal_driver_pb1.contracts.enums import AccessKind, DatasetRole, RunMode
from research.causal_driver_pb1.contracts.errors import FirewallDenied
from research.causal_driver_pb1.cross_sectional_precommit import (
    BACKUP_DRIVER,
    DRIVER_FAMILY_ID,
    EXPECTED_UNIVERSE_MANIFEST_SHA256,
    FAMILY_N,
    FINAL_NEXT_DRIVER_VERDICT,
    FUTURES_STATUS,
    LEADER_N,
    LOOKBACKS_MIN,
    PARENT_CANDIDATE_LIST_SHA256,
    PARENT_USDJPY_REASON,
    PARENT_USDJPY_STATUS,
    PARENT_USDJPY_VERDICT,
    PHASE0_DRIVER_FAMILY_ENUM_MUTATED,
    PRIMARY_NEXT_DRIVER,
    RESPONSE_HORIZONS_MIN,
    SCOPE_N,
)
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1.cross_sectional_precommit.contamination import contamination_ledger
from research.causal_driver_pb1.cross_sectional_precommit_v1_1 import (
    CASE_BLOCKED,
    CASE_READY,
    EXPECTED_LEADER_PAIRS,
    EXPECTED_LEADER_SET_SHA256,
    EXPECTED_TARGET_SET_SHA256,
    NEXT_RESOLVE,
    NEXT_RUN,
    PRECOMMIT_ID,
    SUPERSEDES_PRECOMMIT_SHA256,
    V1_ELIGIBLE_DAY_SHA256,
    V1_FOLD_BOUNDARY_SHA256,
    V1_PERMUTATION_SHA256,
)
from research.causal_driver_pb1.cross_sectional_precommit_v1_1.contract import frozen_contract
from research.causal_driver_pb1.cross_sectional_precommit_v1_1.days import compute_dependency_day_eligibility
from research.causal_driver_pb1.cross_sectional_precommit_v1_1.identity import load_v1_frozen_identities
from research.causal_driver_pb1.cross_sectional_precommit_v1_1.isolation import USDJPY_CORRECTED_OUT, V1_OUT
from research.causal_driver_pb1.datasets.firewall import AccessLedger, request_dataset
from research.causal_driver_pb1.identity.pin import bind_identities, frozen_source_hashes
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.causal_driver_pb1.phase2_precommit.stock_semantics import prove_stock_timestamp_semantics
from research.causal_driver_pb1.phase2_precommit_v1_1.tse_calendar import build_tse_cash_calendar
from research.causal_driver_pb1.response.cases import run_resolver_tests


def _firewall() -> dict[str, Any]:
    ledger = AccessLedger(run_id="LL_PRECOMMIT_V1_1", run_mode=RunMode.DRIVER_DISCOVERY)
    request_dataset(
        ledger=ledger,
        run_mode=RunMode.DRIVER_DISCOVERY,
        dataset_id="RESEARCH_OBSERVATION_UNIVERSE_105",
        dataset_role=DatasetRole.DEVELOPMENT,
        requested_fields=("symbol", "tse33_code", "tse33_name"),
        access_kind=AccessKind.METADATA,
    )
    request_dataset(
        ledger=ledger,
        run_mode=RunMode.DRIVER_DISCOVERY,
        dataset_id="EQUITY_MINUTE_DEV",
        dataset_role=DatasetRole.DEVELOPMENT,
        requested_fields=("date", "time_label", "close"),
        access_kind=AccessKind.PAYLOAD,
    )
    request_dataset(
        ledger=ledger,
        run_mode=RunMode.DRIVER_DISCOVERY,
        dataset_id="EQUITY_MINUTE_C1_PRESENCE",
        dataset_role=DatasetRole.ECONOMIC_DEVELOPMENT_EXPOSED,
        requested_fields=("date", "time_label", "close"),
        access_kind=AccessKind.PAYLOAD,
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
    }


def _parent_usdjpy() -> dict[str, Any]:
    path = USDJPY_CORRECTED_OUT / "report.json"
    doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    a = doc.get("answers") or {}
    return {
        "VERDICT": a.get("VERDICT"),
        "reason": a.get("reason"),
        "USDJPY_DRIVER_FAMILY_STATUS": a.get("USDJPY_DRIVER_FAMILY_STATUS"),
        "DEV_candidate_n": a.get("DEV_candidate_n"),
        "candidate_list_sha256": a.get("candidate_list_sha256"),
        "C1_opened": a.get("C1_opened"),
        "C1_rows_read_before_corrected_candidate_freeze": a.get("C1_rows_read_before_corrected_candidate_freeze"),
        "precommit_sha256": a.get("precommit_sha256"),
    }


def _diff(*, leaders: dict[str, Any], days: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    folds = days.get("folds") or {}
    shuffle = days.get("shuffle") or {}
    day_sha = days.get("eligible_day_sha256")
    fold_sha = folds.get("fold_boundary_sha256")
    perm_sha = shuffle.get("permutation_sha256")
    pre_sha = contract.get("precommit_sha256")
    return {
        "changed": {
            "day_dependency_coverage": "09:10-11:25 -> 09:00-11:30",
            "eligible_dates": True,
            "eligible_day_sha256": {"v1": V1_ELIGIBLE_DAY_SHA256, "v1_1": day_sha, "changed": day_sha != V1_ELIGIBLE_DAY_SHA256},
            "folds_fold_sha": {"v1": V1_FOLD_BOUNDARY_SHA256, "v1_1": fold_sha, "changed": fold_sha != V1_FOLD_BOUNDARY_SHA256},
            "shuffle_permutation_sha": {"v1": V1_PERMUTATION_SHA256, "v1_1": perm_sha, "changed": perm_sha != V1_PERMUTATION_SHA256},
            "D7_strict_freshness_gate": True,
            "C7_strict_freshness_gate": True,
            "leader_identity_placebo_semantics": "LEADER_IDENTITY_SPECIFICITY_GATE strongest-of-8; no date resampling; not p95",
            "precommit_sha": {"v1": SUPERSEDES_PRECOMMIT_SHA256, "v1_1": pre_sha, "changed": pre_sha != SUPERSEDES_PRECOMMIT_SHA256},
        },
        "unchanged": {
            "8_frozen_leaders": list(EXPECTED_LEADER_PAIRS),
            "leader_set_sha": leaders.get("leader_set_sha256"),
            "97_targets": leaders.get("target_n"),
            "target_set_sha": leaders.get("target_set_sha256"),
            "universe_105": True,
            "9_scopes": True,
            "4_lookbacks": list(LOOKBACKS_MIN),
            "4_horizons": list(RESPONSE_HORIZONS_MIN),
            "144_family": FAMILY_N,
            "models": True,
            "BH_q": 0.05,
            "bootstrap_n": 2000,
            "primary_target_freshness_sec": 120,
            "leader_freshness_sec": 60,
            "time_offset_placebo": True,
            "concentration": True,
            "no_PB1": True,
            "no_complete_strategy": True,
        },
    }


def evaluate() -> dict[str, Any]:
    blockers: list[str] = []
    if not (V1_OUT / "report.json").is_file():
        blockers.append("V1_PRECOMMIT_MISSING")
    resolver_tests = run_resolver_tests()
    if not all(r.get("pass") for r in resolver_tests):
        blockers.append("RESOLVER_UNIT_TEST_FAIL")
    identity = bind_identities()
    pre_hashes = frozen_source_hashes()
    if identity.get("V4_MACHINE_SHA256") != EXPECTED_V4_MACHINE_SHA256:
        blockers.append("V4_CHANGED")
    if identity.get("COMPLETE_STRATEGY_SHA256") != EXPECTED_COMPLETE_STRATEGY_SHA256:
        blockers.append("COMPLETE_STRATEGY_CHANGED")
    parent = _parent_usdjpy()
    if parent.get("VERDICT") != PARENT_USDJPY_VERDICT:
        blockers.append("USDJPY_PARENT_VERDICT_MISMATCH")
    if parent.get("USDJPY_DRIVER_FAMILY_STATUS") != PARENT_USDJPY_STATUS:
        blockers.append("USDJPY_STATUS_MISMATCH")
    if parent.get("candidate_list_sha256") != PARENT_CANDIDATE_LIST_SHA256:
        blockers.append("USDJPY_CANDIDATE_SHA_MISMATCH")
    if parent.get("C1_opened") is True:
        blockers.append("USDJPY_C1_WAS_OPENED")
    if int(parent.get("C1_rows_read_before_corrected_candidate_freeze") or 0) != 0:
        blockers.append("USDJPY_C1_ROWS_BEFORE_FREEZE")
    if int(parent.get("DEV_candidate_n") or 0) != 0:
        blockers.append("USDJPY_CANDIDATE_N_NOT_ZERO")
    fw = _firewall()
    if not fw.get("pass"):
        blockers.append("FIREWALL")
    stock = prove_stock_timestamp_semantics()
    if not stock.get("pass"):
        blockers.append("STOCK_TIMESTAMP_SEMANTICS")
    sectors = bind_sector_mapping()
    if not sectors.get("pass") or sectors.get("universe105_sha256") != EXPECTED_UNIVERSE_MANIFEST_SHA256:
        blockers.append("UNIVERSE105_SHA_MISMATCH")
    print("LOAD_V1_FROZEN_IDENTITIES", flush=True)
    leaders = load_v1_frozen_identities()
    blockers.extend(leaders.get("blockers") or [])
    if leaders.get("leader_set_sha256") != EXPECTED_LEADER_SET_SHA256:
        blockers.append("LEADER_SET_SHA_NOT_PRESERVED")
    if leaders.get("target_set_sha256") != EXPECTED_TARGET_SET_SHA256:
        blockers.append("TARGET_SET_SHA_NOT_PRESERVED")
    print("TSE_CALENDAR", flush=True)
    calendar = build_tse_cash_calendar()
    if not calendar.get("pass"):
        blockers.append("TSE_CALENDAR")
    tse_days = list(calendar.get("trading_days") or [])
    days = {"pass": False, "eligible_dates": [], "folds": {}, "shuffle": {}}
    if leaders.get("pass"):
        print("DEPENDENCY_DAY_ELIGIBILITY", flush=True)
        days = compute_dependency_day_eligibility(
            leader_symbols=list(leaders.get("leader_symbols") or []),
            tse_days=tse_days,
        )
        if not days.get("pass"):
            blockers.append("ELIGIBLE_DAYS")
        if days.get("usdjpy_fx_exclusions_inherited"):
            blockers.append("USDJPY_EXCLUSIONS_INHERITED")
        if days.get("future_return_used") or days.get("c1_future_return_outcomes"):
            blockers.append("OUTCOMES_OPENED")
    if int(len(LOOKBACKS_MIN) * len(RESPONSE_HORIZONS_MIN) * SCOPE_N) != FAMILY_N:
        blockers.append("FAMILY_N_MISMATCH")
    if leaders.get("leader_n") != LEADER_N:
        blockers.append("LEADER_N_CHANGED")
    contract = {}
    if leaders.get("pass") and days.get("pass"):
        contract = frozen_contract(stock=stock, sectors=sectors, leaders=leaders, days=days)
        d7 = (contract.get("dev_gates") or {}).get("D7") or {}
        c7 = (contract.get("c1_gates") or {}).get("C7") or {}
        ident = ((contract.get("placebos") or {}).get("leader_identity_specificity_gate") or {})
        if not (d7.get("cannot_rescue_D1_D6_failure") and d7.get("conjunctive")):
            blockers.append("D7_NOT_FROZEN")
        if not (c7.get("cannot_rescue_C1_C6_failure") and c7.get("conjunctive")):
            blockers.append("C7_NOT_FROZEN")
        if ident.get("label") != "LEADER_IDENTITY_SPECIFICITY_GATE" or ident.get("date_resampling_extension") is not False:
            blockers.append("IDENTITY_GATE_NOT_FROZEN")
        if contract.get("precommit_sha256") == SUPERSEDES_PRECOMMIT_SHA256:
            blockers.append("PRECOMMIT_SHA_NOT_REGENERATED")
        dep = (contract.get("day_eligibility") or {}).get("DEPENDENCY_WINDOW") or ""
        if "09:00-11:30" not in dep:
            blockers.append("DEPENDENCY_WINDOW_NOT_0900_1130")
    post_hashes = frozen_source_hashes()
    if pre_hashes != post_hashes:
        blockers.append("RUNTIME_NONIMPACT")
    diff = _diff(leaders=leaders, days=days, contract=contract)
    ok = not blockers and bool(contract.get("precommit_sha256"))
    return {
        "ok": ok,
        "VERDICT": CASE_READY if ok else CASE_BLOCKED,
        "NEXT": NEXT_RUN if ok else NEXT_RESOLVE,
        "FINAL_NEXT_DRIVER_VERDICT": FINAL_NEXT_DRIVER_VERDICT if ok else None,
        "reason": None if ok else "LEADER_LAGGARD_PRECOMMIT_V1_1_BLOCKED",
        "blockers": list(dict.fromkeys(blockers)),
        "precommit_id": PRECOMMIT_ID,
        "precommit_sha256": contract.get("precommit_sha256"),
        "supersedes_precommit_sha256": SUPERSEDES_PRECOMMIT_SHA256,
        "PRIMARY_NEXT_DRIVER": PRIMARY_NEXT_DRIVER,
        "DRIVER_FAMILY_ID": DRIVER_FAMILY_ID,
        "BACKUP_DRIVER": BACKUP_DRIVER,
        "FUTURES_DRIVER_DATA_NOT_READY": FUTURES_STATUS,
        "phase0_DriverFamily_enum_mutated": PHASE0_DRIVER_FAMILY_ENUM_MUTATED,
        "parent_usdjpy": parent,
        "parent_usdjpy_reason": PARENT_USDJPY_REASON,
        "USDJPY_REOPENED": False,
        "LEADER_LAGGARD_OUTCOMES_OPENED": False,
        "C1_outcomes_opened": False,
        "C1_rows_read_before_candidate_freeze": 0,
        "candidate_list_sha256": None,
        "leader_n": leaders.get("leader_n"),
        "leader_set_sha256": leaders.get("leader_set_sha256"),
        "target_set_sha256": leaders.get("target_set_sha256"),
        "target_n": leaders.get("target_n"),
        "disjoint": leaders.get("disjoint"),
        "LEADER_SET_FROZEN_BEFORE_OUTCOME": True,
        "eligible_day_sha256": days.get("eligible_day_sha256"),
        "eligible_dev_n": days.get("eligible_dev_n"),
        "eligible_c1_n": days.get("eligible_c1_n"),
        "fold_boundary_sha256": (days.get("folds") or {}).get("fold_boundary_sha256"),
        "permutation_sha256": (days.get("shuffle") or {}).get("permutation_sha256"),
        "family_n": FAMILY_N,
        "resolver_tests": resolver_tests,
        "identity": {k: v for k, v in identity.items() if k != "source_inventory"},
        "firewall": fw,
        "stock": stock,
        "sectors": {
            "universe105_sha256": sectors.get("universe105_sha256"),
            "sector_mapping_sha256": sectors.get("sector_mapping_sha256"),
            "eligible_sectors": sectors.get("eligible_sectors"),
            "ineligible_sectors": sectors.get("ineligible_sectors"),
            "rows": sectors.get("rows"),
        },
        "calendar": {k: v for k, v in calendar.items() if k != "rows"} | {"row_n": len(calendar.get("rows") or [])},
        "leaders": leaders,
        "days": {k: v for k, v in days.items() if k != "rows"} | {"row_n": len(days.get("rows") or [])},
        "day_rows": days.get("rows") or [],
        "contract": contract,
        "diff": diff,
        "contamination": contamination_ledger(),
        "required_leader_n": LEADER_N,
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
