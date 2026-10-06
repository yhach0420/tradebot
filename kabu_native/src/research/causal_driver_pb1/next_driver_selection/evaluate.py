"""Select next Causal Driver family. No future-return tests. No USDJPY reopen."""
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
from research.causal_driver_pb1.datasets.firewall import AccessLedger, request_dataset
from research.causal_driver_pb1.identity.pin import bind_identities, frozen_source_hashes
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.phase2_discovery.bind import bind_precommit
from research.causal_driver_pb1.phase2_discovery.isolation import OUT as PHASE2_OUT
from research.causal_driver_pb1.next_driver_selection import (
    EXPECTED_PRECOMMIT_SHA256,
    FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256,
    PARENT_PHASE2_NEXT,
    PARENT_PHASE2_REASON,
    PARENT_PHASE2_VERDICT,
    PHASE2_C1_N,
    PHASE2_CANDIDATE_LIST_SHA256,
    PHASE2_D1_FIRST_FAIL,
    PHASE2_D2_FIRST_FAIL,
    PHASE2_D3_FIRST_FAIL,
    PHASE2_DEV_CANDIDATE_N,
    PHASE2_DEV_N,
    PHASE2_FX_FEATURE_MISSING_RATE,
    PHASE2_STOCK_TARGET_MISSING_RATE,
    PHASE2_TEST_N,
    USDJPY_FAMILY_STATUS,
)
from research.causal_driver_pb1.next_driver_selection.coverage import audit_shared_response
from research.causal_driver_pb1.next_driver_selection.futures import audit_futures
from research.causal_driver_pb1.next_driver_selection.inventory import driver_inventory
from research.causal_driver_pb1.next_driver_selection.select import selection_matrix


def _firewall() -> dict[str, Any]:
    ledger = AccessLedger(run_id="NEXT_DRIVER_SELECTION_V1", run_mode=RunMode.DRIVER_DISCOVERY)
    request_dataset(
        ledger=ledger,
        run_mode=RunMode.DRIVER_DISCOVERY,
        dataset_id="RESEARCH_OBSERVATION_UNIVERSE_105",
        dataset_role=DatasetRole.DEVELOPMENT,
        requested_fields=("symbol", "tse33_code"),
        access_kind=AccessKind.METADATA,
    )
    request_dataset(
        ledger=ledger,
        run_mode=RunMode.DRIVER_DISCOVERY,
        dataset_id="EQUITY_MINUTE_DEV_PRESENCE",
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
    return {"fv_denied": fv, "prospective_denied": pr, "pass": fv and pr and date_ok, "events_n": len(ledger.events)}


def _phase2_parent() -> dict[str, Any]:
    path = PHASE2_OUT / "report.json"
    doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    a = doc.get("answers") or {}
    return {
        "VERDICT": a.get("VERDICT"),
        "NEXT": a.get("NEXT"),
        "reason": a.get("reason"),
        "precommit_sha256": a.get("precommit_sha256"),
        "DEV_candidate_n": a.get("DEV_candidate_n"),
        "candidate_list_sha256": a.get("candidate_list_sha256"),
        "C1_rows_read_before_candidate_freeze": a.get("C1_rows_read_before_candidate_freeze"),
        "C1_opened": a.get("C1_opened"),
        "failure_stage_counts": a.get("failure_stage_counts"),
        "missingness": a.get("missingness"),
        "eligible_dev_n": a.get("eligible_dev_n"),
        "eligible_c1_n": a.get("eligible_c1_n"),
    }


def evaluate() -> dict[str, Any]:
    blockers: list[str] = []
    identity = bind_identities()
    pre_hashes = frozen_source_hashes()
    if identity.get("V4_MACHINE_SHA256") != EXPECTED_V4_MACHINE_SHA256:
        blockers.append("V4_CHANGED")
    if identity.get("COMPLETE_STRATEGY_SHA256") != EXPECTED_COMPLETE_STRATEGY_SHA256:
        blockers.append("COMPLETE_STRATEGY_CHANGED")

    bound = bind_precommit()
    blockers.extend(bound.get("blockers") or [])
    if bound.get("precommit_sha256") != EXPECTED_PRECOMMIT_SHA256:
        blockers.append("PRECOMMIT_SHA_MISMATCH")
    if bound.get("precommit_sha256") == FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256:
        blockers.append("SUPERSEDED_V1_PRECOMMIT_USED")

    parent = _phase2_parent()
    if parent.get("VERDICT") != PARENT_PHASE2_VERDICT:
        blockers.append("PHASE2_VERDICT_MISMATCH")
    if parent.get("reason") != PARENT_PHASE2_REASON:
        blockers.append("PHASE2_REASON_MISMATCH")
    if parent.get("NEXT") != PARENT_PHASE2_NEXT:
        blockers.append("PHASE2_NEXT_MISMATCH")
    if parent.get("precommit_sha256") != EXPECTED_PRECOMMIT_SHA256:
        blockers.append("PHASE2_PRECOMMIT_SHA_MISMATCH")
    if parent.get("DEV_candidate_n") != PHASE2_DEV_CANDIDATE_N:
        blockers.append("PHASE2_CANDIDATE_N_MISMATCH")
    if parent.get("candidate_list_sha256") != PHASE2_CANDIDATE_LIST_SHA256:
        blockers.append("PHASE2_CANDIDATE_SHA_MISMATCH")
    if parent.get("C1_rows_read_before_candidate_freeze") != 0:
        blockers.append("PHASE2_C1_WAS_READ_BEFORE_FREEZE")
    if parent.get("C1_opened"):
        blockers.append("PHASE2_C1_WAS_OPENED")

    fw = _firewall()
    if not fw.get("pass"):
        blockers.append("FIREWALL")

    closeout = {
        "USDJPY_DRIVER_FAMILY_STATUS": USDJPY_FAMILY_STATUS,
        "VERDICT": PARENT_PHASE2_VERDICT,
        "reason": PARENT_PHASE2_REASON,
        "DEV_tests": PHASE2_TEST_N,
        "DEV_candidate_n": PHASE2_DEV_CANDIDATE_N,
        "D1_first_fail": PHASE2_D1_FIRST_FAIL,
        "D2_first_fail": PHASE2_D2_FIRST_FAIL,
        "D3_first_fail": PHASE2_D3_FIRST_FAIL,
        "D3_failures_are_not_candidates": True,
        "forbidden_not_done": [
            "FDR緩和",
            "q threshold変更",
            "sector subset再探索",
            "lookback追加",
            "horizon追加",
            "USDJPY threshold探索",
            "old 3-symbol list復活",
            "C1を覗く",
            "PB1条件付きUSDJPY再探索",
        ],
        "interpretation": (
            "Negated family is USDJPY causal return -> market/sector future return under the frozen Phase2 contract. "
            "This is not a claim that USDJPY and Japanese equities are unrelated."
        ),
        "USDJPY_REOPENED": False,
        "C1_USDJPY_OUTCOMES_OPENED": False,
    }

    if blockers:
        return {
            "ok": False,
            "VERDICT": "NEXT_DRIVER_SELECTION_BLOCKED_BY_SHARED_RESPONSE_DATA_V1",
            "NEXT": "FIX_SHARED_STOCK_RESPONSE_CONTRACT_IMPLEMENTATION_V1",
            "blockers": list(dict.fromkeys(blockers)),
            "closeout": closeout,
            "identity": {k: v for k, v in identity.items() if k != "source_inventory"},
            "firewall": fw,
            "USDJPY_REOPENED": False,
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
        }

    sectors = bound["sectors"]
    symbols = tuple(r["symbol"] for r in (sectors.get("rows") or []))
    rca = audit_shared_response(
        symbols=symbols,
        sectors=sectors,
        dev_dates=list(bound["development_dates"]),
        c1_dates=list(bound["c1_dates"]),
    )
    futures = audit_futures()
    inv = driver_inventory()
    decided = selection_matrix(rca=rca, futures=futures)

    post_hashes = frozen_source_hashes()
    if pre_hashes != post_hashes:
        blockers.append("RUNTIME_NONIMPACT")
    if blockers:
        decided = {
            **decided,
            "VERDICT": "NEXT_DRIVER_SELECTION_BLOCKED_BY_SHARED_RESPONSE_DATA_V1",
            "NEXT": "FIX_SHARED_STOCK_RESPONSE_CONTRACT_IMPLEMENTATION_V1",
        }

    miss_pub = (parent.get("missingness") or {})
    return {
        "ok": decided.get("VERDICT")
        in {
            "NEXT_CAUSAL_DRIVER_SELECTED_INDEX_FUTURES_V1",
            "NEXT_CAUSAL_DRIVER_SELECTED_CROSS_SECTIONAL_LEADER_LAGGARD_V1",
        },
        "VERDICT": decided.get("VERDICT"),
        "NEXT": decided.get("NEXT"),
        "blockers": blockers,
        "closeout": closeout,
        "phase2_parent": parent,
        "published_stock_target_missing_rate": miss_pub.get("stock_target_missing_rate") or PHASE2_STOCK_TARGET_MISSING_RATE,
        "published_fx_feature_missing_rate": miss_pub.get("fx_feature_missing_rate") or PHASE2_FX_FEATURE_MISSING_RATE,
        "rca": rca,
        "inventory": inv,
        "futures": futures,
        "leader_laggard": {
            "ready": bool(decided.get("leader_ready")),
            "leave_target_out_required": True,
            "not_pb1": True,
            "new_driver_family_id": decided.get("new_driver_family_id"),
            "phase0_enum_mutated": False,
            "runtime": decided.get("runtime"),
            "leader_set_must_be_frozen_in_precommit": True,
            "future_return_tests_this_task": False,
        },
        "sector_breadth": {
            "ready": bool(decided.get("stock_panel_ready")) and not decided.get("harness_block"),
            "leave_target_out_required": True,
            "priority": "BACKUP",
            "overlap_with_same_stock_technical": True,
        },
        "global_external": {
            "US_index_futures": "TRUE_CME_NQ_ES_SOURCE_BLOCKED_V1",
            "NASDAQ_futures": "blocked_with_true_cme",
            "sector_futures_etf": "PROXY_NOT_FUTURES",
            "ADR": "source_ambiguous",
            "commodity_futures": "source_ambiguous_oil_package_not_run",
            "rates": "no_local_intraday",
            "other_FX": "USDJPY_family_stopped_other_pairs_not_sourced",
            "ready": False,
        },
        "selection": decided,
        "PRIMARY_NEXT_DRIVER": decided.get("PRIMARY_NEXT_DRIVER"),
        "BACKUP_DRIVER": decided.get("BACKUP_DRIVER"),
        "why": decided.get("why"),
        "eligible_dev_n": PHASE2_DEV_N,
        "eligible_c1_n": PHASE2_C1_N,
        "precommit_sha256": EXPECTED_PRECOMMIT_SHA256,
        "identity": {k: v for k, v in identity.items() if k != "source_inventory"},
        "firewall": fw,
        "USDJPY_REOPENED": False,
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "V4_CHANGED": False,
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "research_only": True,
        "future_return_search": False,
        "PHASE2_OUTCOMES_REUSED_NOT_RERUN": True,
    }
