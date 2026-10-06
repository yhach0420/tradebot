"""Assemble leader-laggard precommit. No future-return discovery."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1 import (
    DEV_LAST,
    EXPECTED_COMPLETE_STRATEGY_SHA256,
    EXPECTED_V4_MACHINE_SHA256,
    FV_FIRST,
    PROSPECTIVE_FROM,
)
from research.causal_driver_pb1.contracts.enums import AccessKind, DatasetRole, RunMode
from research.causal_driver_pb1.contracts.errors import FirewallDenied
from research.causal_driver_pb1.cross_sectional_precommit import (
    BACKUP_DRIVER,
    CASE_BLOCKED,
    CASE_READY,
    DRIVER_FAMILY_ID,
    EXPECTED_UNIVERSE_MANIFEST_SHA256,
    FAMILY_N,
    FINAL_NEXT_DRIVER_VERDICT,
    FUTURES_STATUS,
    LEADER_N,
    LOOKBACKS_MIN,
    NEXT_RESOLVE,
    NEXT_RUN,
    PARENT_CANDIDATE_LIST_SHA256,
    PARENT_USDJPY_REASON,
    PARENT_USDJPY_STATUS,
    PARENT_USDJPY_VERDICT,
    PHASE0_DRIVER_FAMILY_ENUM_MUTATED,
    PRECOMMIT_ID,
    PRIMARY_NEXT_DRIVER,
    RESPONSE_HORIZONS_MIN,
    SCOPE_N,
)
from research.causal_driver_pb1.cross_sectional_precommit.contamination import contamination_ledger
from research.causal_driver_pb1.cross_sectional_precommit.contract import frozen_contract
from research.causal_driver_pb1.cross_sectional_precommit.days import compute_leader_day_eligibility
from research.causal_driver_pb1.cross_sectional_precommit.isolation import USDJPY_CORRECTED_OUT
from research.causal_driver_pb1.cross_sectional_precommit.leaders import freeze_leaders
from research.causal_driver_pb1.datasets.firewall import AccessLedger, request_dataset
from research.causal_driver_pb1.identity.pin import bind_identities, frozen_source_hashes
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.causal_driver_pb1.phase2_precommit.stock_semantics import prove_stock_timestamp_semantics
from research.causal_driver_pb1.phase2_precommit_v1_1.tse_calendar import build_tse_cash_calendar
from research.causal_driver_pb1.response.cases import run_resolver_tests


def _firewall() -> dict[str, Any]:
    ledger = AccessLedger(run_id="LL_PRECOMMIT", run_mode=RunMode.DRIVER_DISCOVERY)
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
        requested_fields=("date", "time_label", "close", "trading_value"),
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
    return {"fv_denied": fv, "prospective_denied": pr, "pass": fv and pr and date_ok}


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
    print("TSE_CALENDAR", flush=True)
    calendar = build_tse_cash_calendar()
    if not calendar.get("pass"):
        blockers.append("TSE_CALENDAR")
    tse_days = list(calendar.get("trading_days") or [])
    tse_dev = [d for d in tse_days if d <= DEV_LAST]
    print("FREEZE_LEADERS", flush=True)
    leaders = freeze_leaders(sectors=sectors, tse_dev_days=tse_dev)
    if not leaders.get("pass"):
        blockers.append("LEADER_SET_NOT_FROZEN")
    days = {"pass": False, "eligible_dates": [], "folds": {}, "shuffle": {}}
    if leaders.get("pass"):
        print("LEADER_DAY_ELIGIBILITY", flush=True)
        days = compute_leader_day_eligibility(leader_symbols=list(leaders.get("leader_symbols") or []), tse_days=tse_days)
        if not days.get("pass"):
            blockers.append("ELIGIBLE_DAYS")
    if int(len(LOOKBACKS_MIN) * len(RESPONSE_HORIZONS_MIN) * SCOPE_N) != FAMILY_N:
        blockers.append("FAMILY_N_MISMATCH")
    contract = {}
    if leaders.get("pass") and days.get("pass"):
        contract = frozen_contract(stock=stock, sectors=sectors, leaders=leaders, days=days)
    post_hashes = frozen_source_hashes()
    if pre_hashes != post_hashes:
        blockers.append("RUNTIME_NONIMPACT")
    ok = not blockers and bool(contract.get("precommit_sha256"))
    return {
        "ok": ok,
        "VERDICT": CASE_READY if ok else CASE_BLOCKED,
        "NEXT": NEXT_RUN if ok else NEXT_RESOLVE,
        "FINAL_NEXT_DRIVER_VERDICT": FINAL_NEXT_DRIVER_VERDICT if ok else None,
        "reason": None if ok else "LEADER_SET_OR_TIMESTAMP_FRESHNESS_NOT_FROZEN",
        "blockers": list(dict.fromkeys(blockers)),
        "precommit_id": PRECOMMIT_ID,
        "precommit_sha256": contract.get("precommit_sha256"),
        "PRIMARY_NEXT_DRIVER": PRIMARY_NEXT_DRIVER,
        "DRIVER_FAMILY_ID": DRIVER_FAMILY_ID,
        "BACKUP_DRIVER": BACKUP_DRIVER,
        "FUTURES_DRIVER_DATA_NOT_READY": FUTURES_STATUS,
        "phase0_DriverFamily_enum_mutated": PHASE0_DRIVER_FAMILY_ENUM_MUTATED,
        "parent_usdjpy": parent,
        "parent_usdjpy_reason": PARENT_USDJPY_REASON,
        "USDJPY_REOPENED": False,
        "LEADER_LAGGARD_OUTCOMES_OPENED": False,
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
    }
