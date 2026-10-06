"""Run the non-overlapping offset correction. Offset gate first. Downstream only for passers."""
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
from research.causal_driver_pb1.identity.pin import bind_identities, frozen_source_hashes
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset import (
    C1_CONFIRMED_SET_SHA256,
    CANDIDATE_LIST_SHA256,
    CASE_BLOCKED,
    CASE_FOUND,
    CASE_NOT_FOUND,
    EXPECTED_C1_CONFIRMED_N,
    EXPECTED_PRECOMMIT_SHA256,
    NEXT_REASSESS,
    NEXT_RESOLVE,
    NEXT_TRANSMISSION,
    PARENT_PRECOMMIT_SHA256,
    PRECOMMIT_ID,
    REASON_ALL_OFFSET_FAIL,
)
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset.bind import bind_run
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset.downstream import run_downstream
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset.fit import fit_offsets


def _firewall() -> dict[str, Any]:
    ledger = AccessLedger(run_id="SBD_NONOVERLAP_OFFSET_V1", run_mode=RunMode.DRIVER_DISCOVERY)
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
    return {"fv_denied": fv, "prospective_denied": pr, "pass": fv and pr and date_ok}


def _downstream_reason(rows: list[dict[str, Any]]) -> str:
    stages = []
    for r in rows:
        if r.get("offset_pass") and not r.get("final_pass"):
            stages.append(str(r.get("failed_at") or "DOWNSTREAM"))
    uniq = list(dict.fromkeys(stages))
    mapping = {
        "SHUFFLE": "ALL_OFFSET_PASSERS_FAILED_DAY_SHUFFLE",
        "IDENTITY": "ALL_OFFSET_PASSERS_FAILED_SECTOR_IDENTITY",
        "DRIVER_CONCENTRATION": "ALL_OFFSET_PASSERS_FAILED_DRIVER_CONCENTRATION",
        "TARGET_CONCENTRATION": "ALL_OFFSET_PASSERS_FAILED_TARGET_CONCENTRATION",
        "COMMON_FACTOR": "ALL_OFFSET_PASSERS_FAILED_COMMON_FACTOR",
    }
    if len(uniq) == 1 and uniq[0] in mapping:
        return mapping[uniq[0]]
    return "ALL_OFFSET_PASSERS_FAILED_DOWNSTREAM:" + "+".join(uniq)


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
    bound = bind_run()
    blockers.extend(bound.get("blockers") or [])
    fit: dict[str, Any] = {"offset_results": [], "blocked": True, "sample_verification": [], "overlap_rows": []}
    down: dict[str, Any] = {"rows": [], "ran": False, "blockers": []}
    if not blockers:
        print("OFFSET_FIT", flush=True)
        fit = fit_offsets(records=list(bound.get("records") or []), c1_dates=list(bound.get("c1_dates") or []))
        if fit.get("blocked"):
            blockers.extend(fit.get("blockers") or ["OFFSET_FIT_BLOCKED"])
    offset_results = list(fit.get("offset_results") or [])
    if not blockers:
        passers = [r for r in offset_results if r.get("offset_pass")]
        down = run_downstream(
            passers=passers,
            records=list(bound.get("records") or []),
            panel=fit.get("panel") or {},
            c1_dates=list(bound.get("c1_dates") or []),
            development_dates=list(bound.get("development_dates") or []),
            eligible_dates=list(bound.get("eligible_dates") or []),
            c1_primary_b_fx=dict(bound.get("c1_primary_b_fx") or {}),
        )
        blockers.extend(down.get("blockers") or [])
    post_hashes = frozen_source_hashes()
    if pre_hashes != post_hashes:
        blockers.append("RUNTIME_NONIMPACT")
    down_rows = list(down.get("rows") or [])
    by_down = {str(r.get("test_id")): r for r in down_rows}
    for row in offset_results:
        extra = by_down.get(str(row.get("test_id"))) or {}
        if row.get("offset_pass"):
            row["shuffle_pass"] = bool(extra.get("shuffle_pass"))
            row["sector_identity_pass"] = bool(extra.get("sector_identity_pass"))
            row["driver_concentration_pass"] = bool(extra.get("driver_concentration_pass"))
            row["target_concentration_pass"] = bool(extra.get("target_concentration_pass"))
            row["common_factor_pass"] = bool(extra.get("common_factor_pass"))
            row["final_pass"] = bool(extra.get("final_pass"))
            row["downstream_failed_at"] = extra.get("failed_at")
        else:
            row["shuffle_pass"] = False
            row["sector_identity_pass"] = False
            row["driver_concentration_pass"] = False
            row["target_concentration_pass"] = False
            row["common_factor_pass"] = False
            row["final_pass"] = False
            row["downstream_failed_at"] = "NOT_RUN"
    offset_pass_n = int(sum(1 for r in offset_results if r.get("offset_pass")))
    final_rows = [r for r in offset_results if r.get("final_pass")]
    if blockers:
        verdict, nxt, reason = CASE_BLOCKED, NEXT_RESOLVE, "NONOVERLAP_OFFSET_CORRECTION_BLOCKED"
    elif offset_pass_n == 0:
        verdict, nxt, reason = CASE_NOT_FOUND, NEXT_REASSESS, REASON_ALL_OFFSET_FAIL
    elif final_rows:
        verdict, nxt, reason = CASE_FOUND, NEXT_TRANSMISSION, None
    else:
        verdict, nxt, reason = CASE_NOT_FOUND, NEXT_REASSESS, _downstream_reason(down_rows)
    return {
        "ok": not blockers and verdict != CASE_BLOCKED,
        "VERDICT": verdict,
        "NEXT": nxt,
        "reason": reason,
        "blockers": list(dict.fromkeys(blockers)),
        "precommit_id": PRECOMMIT_ID,
        "precommit_sha256": EXPECTED_PRECOMMIT_SHA256,
        "parent_precommit_sha256": PARENT_PRECOMMIT_SHA256,
        "candidate_list_sha256": CANDIDATE_LIST_SHA256,
        "c1_confirmed_set_sha256": bound.get("c1_confirmed_set_sha256") or C1_CONFIRMED_SET_SHA256,
        "C1_confirmed_n": EXPECTED_C1_CONFIRMED_N,
        "permutation_sha256": bound.get("permutation_sha256"),
        "offset_evaluated_n": len(offset_results),
        "offset_pass_n": offset_pass_n,
        "offset_results": offset_results,
        "sample_verification": fit.get("sample_verification") or [],
        "overlap_rows": fit.get("overlap_rows") or [],
        "shuffle_pass_n": int(sum(1 for r in offset_results if r.get("shuffle_pass"))),
        "sector_identity_pass_n": int(sum(1 for r in offset_results if r.get("sector_identity_pass"))),
        "driver_concentration_pass_n": int(sum(1 for r in offset_results if r.get("driver_concentration_pass"))),
        "target_concentration_pass_n": int(sum(1 for r in offset_results if r.get("target_concentration_pass"))),
        "common_factor_pass_n": int(sum(1 for r in offset_results if r.get("common_factor_pass"))),
        "final_pass_n": len(final_rows),
        "final_candidates": [
            {"test_id": r.get("test_id"), "scope_id": r.get("scope_id"), "lookback": r.get("w"), "horizon": r.get("h")}
            for r in final_rows
        ],
        "downstream_rows": down_rows,
        "day_shuffle": down.get("day_shuffle") or [{"status": "NOT_RUN"}],
        "sector_identity": down.get("sector_identity") or [{"status": "NOT_RUN"}],
        "driver_concentration": down.get("driver_concentration") or [{"status": "NOT_RUN"}],
        "target_concentration": down.get("target_concentration") or [{"status": "NOT_RUN"}],
        "common_factor": down.get("common_factor") or [{"status": "NOT_RUN"}],
        "downstream_ran": bool(down.get("ran")),
        "DEV_C1_reused_unchanged": True,
        "DEV_OUTCOMES_PREVIOUSLY_OPENED": True,
        "C1_OUTCOMES_PREVIOUSLY_OPENED": True,
        "fresh_blind_first_look": False,
        "new_driver_acquisition_started": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
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
        "firewall": fw,
        "identity": {k: v for k, v in identity.items() if k != "source_inventory"},
        "contamination": {
            **contamination_ledger(),
            "DEV_OUTCOMES_PREVIOUSLY_OPENED": True,
            "C1_OUTCOMES_PREVIOUSLY_OPENED": True,
            "fresh_blind_first_look": False,
            "old_overlapping_offset_magnitudes_not_used_in_O3": True,
            "new_driver_acquisition_started": False,
            "c2_failures_not_reopened": True,
            "dev_c1_selection_unchanged": True,
        },
    }
