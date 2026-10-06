"""Assemble V1.3 precommit. Offset-placebo contract only. No corrected betas."""
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
from research.causal_driver_pb1.phase2_precommit.stock_semantics import prove_stock_timestamp_semantics
from research.causal_driver_pb1.response.cases import run_resolver_tests
from research.causal_driver_pb1.sector_breadth_precommit import PHASE0_DRIVER_FAMILY_VALUES
from research.causal_driver_pb1.sector_breadth_discovery import EXPECTED_C1_N
from research.causal_driver_pb1.sector_breadth_precommit_v1_3 import (
    CANDIDATE_LIST_SHA256,
    CASE_BLOCKED,
    CASE_READY,
    DECISION_STATUS,
    EXPECTED_C1_CONFIRMED_N,
    NEXT_RESOLVE,
    NEXT_RUN,
    PARENT_PRECOMMIT_SHA256,
    PRECOMMIT_ID,
    REASON_RAW,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.candidates import bind_c1_confirmed_set, load_v2_discovery
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.contract import frozen_contract_v1_3
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.invalidation import classify_v2
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.offset_def import future_placebo_offsets
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.sample import build_common_samples


def _firewall() -> dict[str, Any]:
    ledger = AccessLedger(run_id="SBD_PRECOMMIT_V1_3", run_mode=RunMode.DRIVER_DISCOVERY)
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
    return {"fv_denied": fv, "prospective_denied": pr, "pass": fv and pr and date_ok, "corrected_offset_beta_not_opened": True}


def _example_tests() -> dict[str, Any]:
    rows = [
        {"id": "w1h1", "w": 1, "h": 1, "expect": (3, 5, 7)},
        {"id": "w1h3", "w": 1, "h": 3, "expect": (5, 7, 9)},
        {"id": "w3h1", "w": 3, "h": 1, "expect": (5, 7, 9)},
    ]
    out = []
    ok = True
    for r in rows:
        got = future_placebo_offsets(lookback=r["w"], horizon=r["h"])
        hit = got == r["expect"]
        ok = ok and hit
        out.append({**r, "got": got, "pass": hit})
    return {"pass": ok, "rows": out}


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
    examples = _example_tests()
    if not examples.get("pass"):
        blockers.append("OFFSET_EXAMPLE_FAIL")
    bound = bind_c1_confirmed_set()
    blockers.extend(bound.get("blockers") or [])
    if int(bound.get("n") or 0) != EXPECTED_C1_CONFIRMED_N:
        blockers.append("C1_CONFIRMED_N")
    inv = classify_v2()
    records = list(bound.get("records") or [])
    overlap_ok = bool(records) and all(bool((r.get("offset_def") or {}).get("all_future_overlap_zero")) for r in records)
    session_ok = bool(records) and all(bool((r.get("offset_def") or {}).get("session_feasible")) for r in records)
    if not overlap_ok:
        blockers.append("FUTURE_WINDOW_OVERLAP")
    if not session_ok:
        blockers.append("SESSION_BOUNDARY_INFEASIBLE")
    v2 = load_v2_discovery()
    ev = v2.get("evaluation") or {}
    c1_dates = list((ev.get("bound") or {}).get("c1_dates") or [])
    if len(c1_dates) != EXPECTED_C1_N:
        blockers.append("C1_DATE_N_MISMATCH")
    samples: dict[str, Any] = {"rows": [], "pass": False, "blockers": [], "beta_computed": False, "ols_not_run": True}
    if not blockers:
        print("C1_COMMON_SAMPLE_MEMBERSHIP", flush=True)
        samples = build_common_samples(records=records, c1_dates=c1_dates)
        blockers.extend(samples.get("blockers") or [])
        if not samples.get("pass"):
            blockers.append("COMMON_SAMPLE_INVALID")
        if samples.get("beta_computed"):
            blockers.append("CORRECTED_BETA_OPENED")
    post_hashes = frozen_source_hashes()
    if pre_hashes != post_hashes:
        blockers.append("RUNTIME_NONIMPACT")
    contract: dict[str, Any] = {}
    if not blockers:
        contract = frozen_contract_v1_3(
            c1_confirmed_set_sha256=str(bound.get("c1_confirmed_set_sha256")),
            candidates=records,
            samples=list(samples.get("rows") or []),
            overlap_ok=overlap_ok,
        )
    ok = not blockers and bool(contract.get("precommit_sha256"))
    return {
        "ok": ok,
        "VERDICT": CASE_READY if ok else CASE_BLOCKED,
        "NEXT": NEXT_RUN if ok else NEXT_RESOLVE,
        "reason": None if ok else "SECTOR_BREADTH_DISPERSION_PRECOMMIT_V1_3_BLOCKED",
        "blockers": list(dict.fromkeys(blockers)),
        "precommit_id": PRECOMMIT_ID,
        "precommit_sha256": contract.get("precommit_sha256"),
        "parent_precommit_sha256": PARENT_PRECOMMIT_SHA256,
        "candidate_list_sha256": CANDIDATE_LIST_SHA256,
        "c1_confirmed_set_sha256": bound.get("c1_confirmed_set_sha256"),
        "C1_confirmed_n": bound.get("C1_confirmed_n"),
        "DEV_candidate_n": bound.get("DEV_candidate_n"),
        "reason_raw": bound.get("reason_raw") or REASON_RAW,
        "decision_status": DECISION_STATUS,
        "invalidation": inv,
        "examples": examples,
        "overlap_ok": overlap_ok,
        "session_ok": session_ok,
        "common_sample": {k: v for k, v in samples.items() if k != "access"} | {"access": samples.get("access")},
        "candidates": [
            {
                **{kk: r.get(kk) for kk in ("test_id", "metric", "scope_id", "sector_id", "lookback", "horizon", "direction", "global", "mechanism")},
                "offset_def": r.get("offset_def"),
                "C1": r.get("C1"),
                "C2": r.get("C2"),
                "C3": r.get("C3"),
                "C4": r.get("C4"),
                "C5": r.get("C5"),
                "C6": r.get("C6"),
                "C7": r.get("C7"),
                "c1_confirmed": True,
            }
            for r in records
        ],
        "contract": contract,
        "resolver_tests": resolver_tests,
        "identity": {k: v for k, v in identity.items() if k != "source_inventory"},
        "firewall": fw,
        "stock": stock,
        "LEADER_LAGGARD_REOPENED": False,
        "USDJPY_REOPENED": False,
        "DEV_OUTCOMES_PREVIOUSLY_OPENED": True,
        "C1_OUTCOMES_PREVIOUSLY_OPENED": True,
        "fresh_blind_first_look": False,
        "corrected_offset_beta_opened": False,
        "DEV_C1_reused_unchanged": True,
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
        "v2_out_not_overwritten": True,
        "contamination": {
            **contamination_ledger(),
            "DEV_OUTCOMES_PREVIOUSLY_OPENED": True,
            "C1_OUTCOMES_PREVIOUSLY_OPENED": True,
            "fresh_blind_first_look": False,
            "correction_derived_from": [
                "driver window definition",
                "target outcome window definition",
                "interval-overlap arithmetic",
            ],
            "correction_not_derived_from": [
                "observed beta magnitude",
                "observed offset ranking",
                "candidate profitability",
                "future validation",
            ],
            "old_positive_offset_magnitudes_not_reused": True,
            "leader_laggard_reopened": False,
            "usdjpy_reopened": False,
            "new_driver_acquisition_not_started": True,
        },
    }
