"""Assemble V1.1 precommit correction. No USDJPY lead outcomes."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1 import EXPECTED_COMPLETE_STRATEGY_SHA256, EXPECTED_V4_MACHINE_SHA256, FV_FIRST, PROSPECTIVE_FROM
from research.causal_driver_pb1.contracts.enums import AccessKind, DatasetRole, RunMode
from research.causal_driver_pb1.contracts.errors import FirewallDenied
from research.causal_driver_pb1.datasets.firewall import AccessLedger, request_dataset
from research.causal_driver_pb1.identity.pin import bind_identities, frozen_source_hashes
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.phase2_precommit.contamination import contamination_ledger
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.causal_driver_pb1.phase2_precommit.stock_semantics import minute_parquet_path, prove_stock_timestamp_semantics
from research.causal_driver_pb1.phase2_precommit_v1_1 import (
    CASE_BLOCKED,
    CASE_READY,
    EXPECTED_PHASE1_INVENTORY_SHA256,
    EXPECTED_PHASE1_SOURCE_FINGERPRINT,
    EXPECTED_UNIVERSE_MANIFEST_SHA256,
    NEXT_RESOLVE,
    NEXT_RUN,
    PARENT_PHASE1_VERDICT,
    PRECOMMIT_ID,
    SUPERSEDED_FOLD_BOUNDARY_SHA256,
    SUPERSEDES_PRECOMMIT_SHA256,
)
from research.causal_driver_pb1.phase2_precommit_v1_1.contract import frozen_contract_v1_1
from research.causal_driver_pb1.phase2_precommit_v1_1.eligibility import compute_corrected_eligibility
from research.causal_driver_pb1.phase2_precommit_v1_1.isolation import PHASE1_OUT, V1_PRECOMMIT_OUT
from research.causal_driver_pb1.phase2_precommit_v1_1.shuffle import generate_shuffle_permutations, shuffle_reproducible
from research.causal_driver_pb1.phase2_precommit_v1_1.tse_calendar import build_tse_cash_calendar

UNCHANGED_KEYS = (
    "universe105_sha256",
    "sector_mapping_sha256",
    "USDJPY_source_fingerprint",
    "fx_lookbacks",
    "response_horizons",
    "research_clock",
    "primary_model",
    "control_variables",
    "bootstrap_method",
    "bootstrap_n",
    "bootstrap_seed_sha",
    "multiple_testing_method",
    "fdr_q",
    "dev_candidate_gate",
    "c1_confirmation_gate",
    "offset_placebo_gate",
    "concentration_gate",
)


def _phase1_parent() -> dict[str, Any]:
    path = PHASE1_OUT / "report.json"
    doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    answers = doc.get("answers") or {}
    return {
        "verdict": answers.get("VERDICT"),
        "inventory_sha256": answers.get("inventory_sha256"),
        "source_fingerprint": (doc.get("evaluation") or {}).get("source_fingerprint") or answers.get("source_fingerprint"),
        "source_id": answers.get("source_id"),
        "source_provider": answers.get("source_provider"),
        "source_start": answers.get("source_start"),
        "source_end": answers.get("source_end"),
    }


def _v1_parent() -> dict[str, Any]:
    path = V1_PRECOMMIT_OUT / "report.json"
    doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return doc.get("answers") or {}


def _diff(v1: dict[str, Any], v11: dict[str, Any]) -> dict[str, Any]:
    unchanged = {}
    changed_ok = {}
    broken = []
    for k in UNCHANGED_KEYS:
        a = v1.get(k)
        b = v11.get(k)
        same = a == b
        unchanged[k] = same
        if not same:
            broken.append(k)
    changed_ok["fold_boundary_sha256"] = v1.get("fold_boundary_sha256") != v11.get("fold_boundary_sha256")
    changed_ok["precommit_sha256"] = v1.get("precommit_sha256") != v11.get("precommit_sha256")
    changed_ok["day_shuffle_algorithm"] = True
    changed_ok["eligible_day_definition"] = True
    return {
        "unchanged_pass": not broken,
        "unchanged": unchanged,
        "broken_unchanged_keys": broken,
        "changed_as_required": changed_ok,
        "old_fold_sha": v1.get("fold_boundary_sha256"),
        "new_fold_sha": v11.get("fold_boundary_sha256"),
        "old_precommit_sha": v1.get("precommit_sha256"),
        "new_precommit_sha": v11.get("precommit_sha256"),
    }


def evaluate() -> dict[str, Any]:
    blockers: list[str] = []
    identity = bind_identities()
    pre_hashes = frozen_source_hashes()
    if identity.get("V4_MACHINE_SHA256") != EXPECTED_V4_MACHINE_SHA256:
        blockers.append("V4_CHANGED")
    if identity.get("COMPLETE_STRATEGY_SHA256") != EXPECTED_COMPLETE_STRATEGY_SHA256:
        blockers.append("COMPLETE_STRATEGY_CHANGED")

    parent = _phase1_parent()
    v1 = _v1_parent()
    if parent.get("verdict") != PARENT_PHASE1_VERDICT:
        blockers.append("PHASE1_NOT_READY")
    if parent.get("inventory_sha256") != EXPECTED_PHASE1_INVENTORY_SHA256:
        blockers.append("PHASE1_INVENTORY_SHA_MISMATCH")
    if parent.get("source_fingerprint") != EXPECTED_PHASE1_SOURCE_FINGERPRINT:
        blockers.append("PHASE1_SOURCE_FINGERPRINT_MISMATCH")
    if v1.get("precommit_sha256") != SUPERSEDES_PRECOMMIT_SHA256:
        blockers.append("V1_PRECOMMIT_SHA_MISMATCH")

    ledger = AccessLedger(run_id="PHASE2_PRECOMMIT_V1_1", run_mode=RunMode.DRIVER_DISCOVERY)
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
        dataset_id="EQUITY_MINUTE_DEV_SCHEMA",
        dataset_role=DatasetRole.DEVELOPMENT,
        requested_fields=("date", "time_label", "close", "available_at_jst"),
        access_kind=AccessKind.PAYLOAD,
    )
    request_dataset(
        ledger=ledger,
        run_mode=RunMode.DRIVER_DISCOVERY,
        dataset_id="EQUITY_MINUTE_C1_DATES",
        dataset_role=DatasetRole.ECONOMIC_DEVELOPMENT_EXPOSED,
        requested_fields=("date",),
        access_kind=AccessKind.METADATA,
    )
    request_dataset(
        ledger=ledger,
        run_mode=RunMode.DRIVER_DISCOVERY,
        dataset_id="EQUITY_MINUTE_PRESENCE_DEV",
        dataset_role=DatasetRole.DEVELOPMENT,
        requested_fields=("date", "time_label"),
        access_kind=AccessKind.PAYLOAD,
    )
    request_dataset(
        ledger=ledger,
        run_mode=RunMode.DRIVER_DISCOVERY,
        dataset_id="EQUITY_MINUTE_PRESENCE_C1",
        dataset_role=DatasetRole.ECONOMIC_DEVELOPMENT_EXPOSED,
        requested_fields=("date", "time_label"),
        access_kind=AccessKind.PAYLOAD,
    )
    fv_denied = False
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
        fv_denied = True
    pr_denied = False
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
        pr_denied = True
    date_denies = []
    for day in (FV_FIRST, PROSPECTIVE_FROM):
        try:
            assert_ingest_date_allowed(day)
            date_denies.append({"date": day, "pass": False})
        except IngestDateDenied:
            date_denies.append({"date": day, "pass": True})
    if not fv_denied or not pr_denied or not all(r["pass"] for r in date_denies):
        blockers.append("FIREWALL")

    stock = prove_stock_timestamp_semantics()
    if not stock.get("pass"):
        blockers.append("STOCK_TIMESTAMP")

    sectors = bind_sector_mapping()
    if not sectors.get("pass") or sectors.get("universe105_sha256") != EXPECTED_UNIVERSE_MANIFEST_SHA256:
        blockers.append("UNIVERSE_OR_SECTOR")
    if sectors.get("sector_mapping_sha256") != v1.get("sector_mapping_sha256"):
        blockers.append("SECTOR_MAPPING_CHANGED")

    missing_pq = [r for r in (sectors.get("rows") or []) if not minute_parquet_path(r["symbol"]).is_file()]
    if missing_pq:
        blockers.append("STOCK_PARQUET_MISSING")

    try:
        calendar = build_tse_cash_calendar()
    except Exception as exc:
        calendar = {"pass": False, "reason": str(exc), "trading_days": []}
        blockers.append("TSE_CALENDAR")
    if not calendar.get("pass"):
        blockers.append("TSE_CALENDAR")

    elig = compute_corrected_eligibility(tse_trading_days=list(calendar.get("trading_days") or []))
    if int(elig.get("conflicting_duplicate_n") or 0) > 0:
        blockers.append("FX_CONFLICTING_DUPLICATE")
    if not elig.get("pass"):
        blockers.append("ELIGIBILITY")
    if elig.get("non_tse_in_folds"):
        blockers.append("NON_TSE_IN_FOLDS")
    folds = elig.get("folds") or {}
    if folds.get("fold_boundary_sha256") == SUPERSEDED_FOLD_BOUNDARY_SHA256:
        blockers.append("FOLD_SHA_NOT_REGENERATED")

    shuffle = generate_shuffle_permutations(list(folds.get("development_dates") or []) + list(folds.get("c1_dates") or []))
    if not shuffle_reproducible(list(folds.get("development_dates") or []) + list(folds.get("c1_dates") or [])):
        blockers.append("SHUFFLE_NOT_REPRODUCIBLE")
    if int(shuffle.get("shuffle_primary_n") or 0) + int(shuffle.get("shuffle_fallback_month_n") or 0) + int(shuffle.get("shuffle_unshufflable_n") or 0) != len(
        (folds.get("development_dates") or []) + (folds.get("c1_dates") or [])
    ):
        blockers.append("SHUFFLE_COUNT_MISMATCH")

    contract = frozen_contract_v1_1(
        stock=stock,
        sectors=sectors,
        folds=folds,
        phase1=parent,
        calendar=calendar,
        shuffle=shuffle,
    )
    if contract.get("precommit_sha256") == SUPERSEDES_PRECOMMIT_SHA256:
        blockers.append("PRECOMMIT_SHA_REUSED")

    v11_answers_preview = {
        "universe105_sha256": sectors.get("universe105_sha256"),
        "sector_mapping_sha256": sectors.get("sector_mapping_sha256"),
        "USDJPY_source_fingerprint": parent.get("source_fingerprint"),
        "fx_lookbacks": contract["usdjpy_feature_family"]["lookbacks_min"],
        "response_horizons": contract["response_horizons_min"],
        "research_clock": contract["research_clock"],
        "primary_model": contract["primary_model"],
        "control_variables": ["target_past_5m_return", "MKT105_past_5m_return", "minute_of_day"],
        "bootstrap_method": contract["bootstrap_method"],
        "bootstrap_n": contract["bootstrap_n"],
        "bootstrap_seed_sha": contract.get("bootstrap_seed_sha"),
        "multiple_testing_method": contract["multiple_testing_method"],
        "fdr_q": contract["fdr_q"],
        "dev_candidate_gate": contract["dev_candidate_gate"],
        "c1_confirmation_gate": contract["c1_confirmation_gate"],
        "offset_placebo_gate": contract["offset_placebo_gate"],
        "concentration_gate": contract["concentration_gate"],
        "fold_boundary_sha256": folds.get("fold_boundary_sha256"),
        "precommit_sha256": contract.get("precommit_sha256"),
    }
    diff = _diff(v1, v11_answers_preview)
    if not diff.get("unchanged_pass"):
        blockers.append("HYPOTHESIS_DRIFT")
    if not diff.get("changed_as_required", {}).get("fold_boundary_sha256"):
        blockers.append("FOLD_SHA_NOT_REGENERATED")

    post_hashes = frozen_source_hashes()
    runtime_pass = pre_hashes == post_hashes
    if not runtime_pass:
        blockers.append("RUNTIME_NONIMPACT")

    unique = list(dict.fromkeys(blockers))
    ok = not unique
    return {
        "ok": ok,
        "VERDICT": CASE_READY if ok else CASE_BLOCKED,
        "NEXT": NEXT_RUN if ok else NEXT_RESOLVE,
        "blockers": unique,
        "precommit_id": PRECOMMIT_ID,
        "precommit_sha256": contract["precommit_sha256"],
        "supersedes_precommit_sha256": SUPERSEDES_PRECOMMIT_SHA256,
        "parent_phase1_inventory_sha256": parent.get("inventory_sha256"),
        "USDJPY_source_fingerprint": parent.get("source_fingerprint"),
        "universe105_sha256": sectors.get("universe105_sha256"),
        "sector_mapping_sha256": sectors.get("sector_mapping_sha256"),
        "stock_response_source_identity": contract["stock_response_source_identity"],
        "stock_timestamp_semantics_proven": bool(stock.get("stock_timestamp_semantics_proven")),
        "eligible_periods": contract["eligible_periods"],
        "research_clock": contract["research_clock"],
        "fx_lookbacks": contract["usdjpy_feature_family"]["lookbacks_min"],
        "response_horizons": contract["response_horizons_min"],
        "development_fold_boundaries": folds.get("development_fold_boundaries"),
        "c1_fold_boundaries": folds.get("c1_fold_boundaries"),
        "fold_boundary_sha256": folds.get("fold_boundary_sha256"),
        "eligible_dev_n": len(folds.get("development_dates") or []),
        "eligible_c1_n": len(folds.get("c1_dates") or []),
        "primary_model": contract["primary_model"],
        "control_variables": ["target_past_5m_return", "MKT105_past_5m_return", "minute_of_day"],
        "bootstrap_method": contract["bootstrap_method"],
        "bootstrap_n": contract["bootstrap_n"],
        "bootstrap_seed_sha": contract.get("bootstrap_seed_sha"),
        "bootstrap_eligible_day_population": "PHASE2_ELIGIBLE_DAY",
        "eligible_day_sha256": elig.get("eligible_day_sha256"),
        "lomo": elig.get("lomo"),
        "superseded_fold_boundary_sha256": SUPERSEDED_FOLD_BOUNDARY_SHA256,
        "multiple_testing_method": contract["multiple_testing_method"],
        "fdr_q": contract["fdr_q"],
        "dev_candidate_gate": contract["dev_candidate_gate"],
        "c1_confirmation_gate": contract["c1_confirmation_gate"],
        "offset_placebo_gate": contract["offset_placebo_gate"],
        "day_shuffle_gate": contract["day_shuffle_gate"],
        "concentration_gate": contract["concentration_gate"],
        "FROZEN_VALIDATION_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "PHASE2_OUTCOMES_OPENED": False,
        "candidate_list_sha256": None,
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "V4_CHANGED": False,
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "identity": identity,
        "stock": stock,
        "sectors": {k: v for k, v in sectors.items() if k != "rows"} | {"mapping_rows": sectors.get("rows")},
        "calendar": {k: v for k, v in calendar.items() if k != "rows"} | {"row_n": len(calendar.get("rows") or [])},
        "calendar_rows": list(calendar.get("rows") or []),
        "eligibility": {
            "fx_only_non_tse_n": len(elig.get("fx_only_non_tse_dates") or []),
            "fx_only_non_tse_dates": elig.get("fx_only_non_tse_dates"),
            "tse_but_fx_below_95_n": len(elig.get("tse_but_fx_below_95") or []),
            "tse_but_fx_below_95": elig.get("tse_but_fx_below_95"),
            "named_date_exclusions": [],
            "eligible_day_sha256": elig.get("eligible_day_sha256"),
        },
        "folds": folds,
        "lomo": elig.get("lomo"),
        "shuffle": {k: v for k, v in shuffle.items() if k != "unshufflable"} | {"unshufflable": shuffle.get("unshufflable")},
        "diff": diff,
        "contract": contract,
        "contamination": contamination_ledger(),
        "firewall": {
            "fv_denied": fv_denied,
            "prospective_denied": pr_denied,
            "date_denies": date_denies,
            "pass": fv_denied and pr_denied,
        },
        "runtime_nonimpact": {"pass": runtime_pass},
        "research_only": True,
    }
