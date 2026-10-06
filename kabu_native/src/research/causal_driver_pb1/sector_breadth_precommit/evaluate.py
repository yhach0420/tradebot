"""Assemble sector-breadth precommit. Input-only days. No future-return discovery."""
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
from research.causal_driver_pb1.datasets.firewall import AccessLedger, request_dataset
from research.causal_driver_pb1.identity.pin import bind_identities, frozen_source_hashes
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.causal_driver_pb1.phase2_precommit.stock_semantics import prove_stock_timestamp_semantics
from research.causal_driver_pb1.phase2_precommit_v1_1.tse_calendar import build_tse_cash_calendar
from research.causal_driver_pb1.response.cases import run_resolver_tests
from research.causal_driver_pb1.sector_breadth_precommit import (
    CASE_BLOCKED,
    CASE_READY,
    EXPECTED_SECTOR_IDS,
    EXPECTED_SECTOR_MAPPING_SHA256,
    EXPECTED_SECTOR_N,
    EXPECTED_UNIVERSE_N,
    EXPECTED_UNIVERSE_SHA256,
    FAMILY_N,
    HORIZONS,
    LOOKBACKS,
    NEXT_RESOLVE,
    NEXT_RUN,
    PHASE0_DRIVER_FAMILY_ENUM_MUTATED,
    PHASE0_DRIVER_FAMILY_VALUES,
    PRECOMMIT_ID,
    PRIMARY_NEXT_DRIVER,
    RESEARCH_FAMILY_ID,
    SCOPE_N,
)
from research.causal_driver_pb1.cross_sectional_precommit.contamination import contamination_ledger
from research.causal_driver_pb1.sector_breadth_precommit.contract import frozen_contract
from research.causal_driver_pb1.sector_breadth_precommit.days import compute_family_days
from research.causal_driver_pb1.sector_breadth_precommit.family import family_identity
from research.causal_driver_pb1.sector_breadth_precommit.parent import bind_parent


def _firewall() -> dict[str, Any]:
    ledger = AccessLedger(run_id="SBD_PRECOMMIT", run_mode=RunMode.DRIVER_DISCOVERY)
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
    print("TSE_CALENDAR", flush=True)
    calendar = build_tse_cash_calendar()
    if not calendar.get("pass"):
        blockers.append("TSE_CALENDAR")
    symbols = [r["symbol"] for r in (sectors.get("rows") or [])]
    print("FAMILY_DAY_ELIGIBILITY", flush=True)
    days = compute_family_days(symbols=symbols, tse_days=list(calendar.get("trading_days") or []))
    if not days.get("pass"):
        blockers.append("ELIGIBLE_DAYS")
    if days.get("future_return_used") or days.get("c1_future_return_outcomes"):
        blockers.append("OUTCOMES_OPENED")
    contract = {}
    if not blockers:
        contract = frozen_contract(stock=stock, sectors=sectors, days=days)
        ident = ((contract.get("placebos") or {}).get("sector_identity_specificity_gate") or {})
        if ident.get("label") != "SECTOR_IDENTITY_SPECIFICITY_GATE":
            blockers.append("IDENTITY_GATE_NOT_FROZEN")
        d7 = (contract.get("dev_gates") or {}).get("D7") or {}
        if not d7.get("cannot_rescue_D1_D6_failure"):
            blockers.append("D7_NOT_FROZEN")
        if (contract.get("family") or {}).get("n") != FAMILY_N:
            blockers.append("CONTRACT_FAMILY_N")
    post_hashes = frozen_source_hashes()
    if pre_hashes != post_hashes:
        blockers.append("RUNTIME_NONIMPACT")
    ok = not blockers and bool(contract.get("precommit_sha256"))
    return {
        "ok": ok,
        "VERDICT": CASE_READY if ok else CASE_BLOCKED,
        "NEXT": NEXT_RUN if ok else NEXT_RESOLVE,
        "reason": None if ok else "SECTOR_BREADTH_DISPERSION_PRECOMMIT_BLOCKED",
        "blockers": list(dict.fromkeys(blockers)),
        "precommit_id": PRECOMMIT_ID,
        "precommit_sha256": contract.get("precommit_sha256"),
        "PRIMARY_NEXT_DRIVER": PRIMARY_NEXT_DRIVER,
        "RESEARCH_FAMILY_ID": RESEARCH_FAMILY_ID,
        "phase0_DriverFamily_enum_mutated": PHASE0_DRIVER_FAMILY_ENUM_MUTATED,
        "phase0_observation_family_if_later_bound": "JP_INTERNAL",
        "parent": parent,
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
        "eligible_day_sha256": days.get("eligible_day_sha256"),
        "eligible_dev_n": days.get("eligible_dev_n"),
        "eligible_c1_n": days.get("eligible_c1_n"),
        "fold_boundary_sha256": (days.get("folds") or {}).get("fold_boundary_sha256"),
        "permutation_sha256": (days.get("shuffle") or {}).get("permutation_sha256"),
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
        "calendar": {k: v for k, v in calendar.items() if k != "rows"} | {"row_n": len(calendar.get("rows") or [])},
        "days": {k: v for k, v in days.items() if k not in {"rows", "listing_rows", "listing_start"}} | {"row_n": len(days.get("rows") or [])},
        "day_rows": days.get("rows") or [],
        "listing_start": days.get("listing_start") or {},
        "listing_rows": days.get("listing_rows") or [],
        "listing_start_sha256": days.get("listing_start_sha256"),
        "family_384": [] if ids != EXPECTED_SECTOR_IDS else (contract.get("family_384") or family_identity(eligible_sectors=elig).get("tests")),
        "contract": contract,
        "contamination": {
            **contamination_ledger(),
            "leader_laggard_near_misses_used": False,
            "leader_laggard_reopened": False,
            "usdjpy_reopened": False,
            "sector_breadth_dispersion_outcomes_opened": False,
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
    }
