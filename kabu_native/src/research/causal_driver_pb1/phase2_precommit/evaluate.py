"""Assemble Phase 2 precommit. Does not compute USDJPY/stock lead outcomes."""
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
from research.causal_driver_pb1.phase2_precommit import (
    BLOCK_STOCK_TS,
    CASE_BLOCKED,
    CASE_READY,
    EXPECTED_PHASE1_INVENTORY_SHA256,
    EXPECTED_PHASE1_SOURCE_FINGERPRINT,
    EXPECTED_UNIVERSE_MANIFEST_SHA256,
    NEXT_RESOLVE,
    NEXT_RUN,
    PARENT_PHASE1_VERDICT,
    PRECOMMIT_ID,
)
from research.causal_driver_pb1.phase2_precommit.contamination import contamination_ledger
from research.causal_driver_pb1.phase2_precommit.contract import frozen_contract
from research.causal_driver_pb1.phase2_precommit.eligibility import compute_fx_eligibility
from research.causal_driver_pb1.phase2_precommit.isolation import MINUTE_REF, PHASE1_OUT
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.causal_driver_pb1.phase2_precommit.stock_semantics import minute_parquet_path, prove_stock_timestamp_semantics


def _ok(name: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "pass": True, **extra}


def _fail(name: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "pass": False, "reason": reason, **extra}


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


def evaluate() -> dict[str, Any]:
    blockers: list[str] = []
    identity = bind_identities()
    pre_hashes = frozen_source_hashes()
    if identity.get("V4_MACHINE_SHA256") != EXPECTED_V4_MACHINE_SHA256:
        blockers.append("V4_CHANGED")
    if identity.get("COMPLETE_STRATEGY_SHA256") != EXPECTED_COMPLETE_STRATEGY_SHA256:
        blockers.append("COMPLETE_STRATEGY_CHANGED")

    parent = _phase1_parent()
    if parent.get("verdict") != PARENT_PHASE1_VERDICT:
        blockers.append("PHASE1_NOT_READY")
    if parent.get("inventory_sha256") != EXPECTED_PHASE1_INVENTORY_SHA256:
        blockers.append("PHASE1_INVENTORY_SHA_MISMATCH")
    if parent.get("source_fingerprint") != EXPECTED_PHASE1_SOURCE_FINGERPRINT:
        blockers.append("PHASE1_SOURCE_FINGERPRINT_MISMATCH")

    ledger = AccessLedger(run_id="PHASE2_PRECOMMIT", run_mode=RunMode.DRIVER_DISCOVERY)
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
        blockers.append(BLOCK_STOCK_TS)

    sectors = bind_sector_mapping()
    if not sectors.get("pass"):
        blockers.append("SECTOR_MAPPING")
    if sectors.get("universe105_sha256") != EXPECTED_UNIVERSE_MANIFEST_SHA256:
        blockers.append("UNIVERSE105_SHA_MISMATCH")

    missing_pq = [sym for sym in sectors.get("rows") or [] if not minute_parquet_path(sym["symbol"]).is_file()]
    if missing_pq:
        blockers.append("STOCK_PARQUET_MISSING")
    parquet_n = 105 - len(missing_pq)

    fx = compute_fx_eligibility()
    if int(fx.get("conflicting_duplicate_n") or 0) > 0:
        blockers.append("FX_CONFLICTING_DUPLICATE")
    if not fx.get("pass"):
        blockers.append("FX_ELIGIBILITY_INSUFFICIENT")

    folds = fx.get("folds") or {}
    contract = frozen_contract(stock=stock, sectors=sectors, folds=folds, phase1=parent)
    contam = contamination_ledger()

    post_hashes = frozen_source_hashes()
    runtime_pass = pre_hashes == post_hashes and identity.get("V4_MACHINE_SHA256") == EXPECTED_V4_MACHINE_SHA256
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
        "primary_model": contract["primary_model"],
        "control_variables": ["target_past_5m_return", "MKT105_past_5m_return", "minute_of_day"],
        "bootstrap_method": contract["bootstrap_method"],
        "bootstrap_n": contract["bootstrap_n"],
        "bootstrap_seed_sha": contract["bootstrap_seed_sha"],
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
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "V4_CHANGED": identity.get("V4_MACHINE_SHA256") != EXPECTED_V4_MACHINE_SHA256,
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "identity": identity,
        "stock": stock,
        "sectors": {k: v for k, v in sectors.items() if k != "rows"} | {"mapping_rows": sectors.get("rows")},
        "fx_eligibility": {
            "pass": fx.get("pass"),
            "dev_n": len(folds.get("development_dates") or []),
            "c1_n": len(folds.get("c1_dates") or []),
            "excluded_n": len((fx.get("coverage") or {}).get("excluded_rows") or []),
            "excluded_rows": (fx.get("coverage") or {}).get("excluded_rows") or [],
            "window": (fx.get("coverage") or {}).get("window"),
            "min_coverage": (fx.get("coverage") or {}).get("min_coverage"),
        },
        "folds": folds,
        "contract": contract,
        "contamination": contam,
        "firewall": {"fv_denied": fv_denied, "prospective_denied": pr_denied, "date_denies": date_denies, "pass": fv_denied and pr_denied},
        "runtime_nonimpact": {"pass": runtime_pass},
        "parquet_n": parquet_n,
        "missing_parquet_symbols": [r["symbol"] for r in missing_pq] if missing_pq else [],
        "MINUTE_REF": str(MINUTE_REF).replace("\\", "/"),
        "research_only": True,
    }
