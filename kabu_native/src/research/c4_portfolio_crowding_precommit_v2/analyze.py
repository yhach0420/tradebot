"""C4 V2 semantic correction + intervention prune. Policy hash before counts. No economics."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from research.c1_multi_timeframe_precommit_v1.spec import (
    canary_spec,
    coverage_gates,
    dumps_sha256,
    economic_gates,
    execution_contract,
    fold_coverage_gates,
    portfolio_contract,
    stability_gates,
)
from research.c4_portfolio_crowding_precommit_v2 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_E,
    CASE_INSUFFICIENT,
    CONTROL_POLICY_ID,
    DEVELOPMENT_DAYS,
    EXECUTION_ID,
    EXPECTED_EVENT_N,
    EXPECTED_SEQ_DUPLICATE_N,
    EXPECTED_SEQ_MISSING_N,
    EXPECTED_SEQ_NON_MONOTONE_N,
    NEXT_IF_A,
    NEXT_IF_E,
    NEXT_IF_INSUFFICIENT,
    TREATMENT_POLICY_ID,
)
from research.c4_portfolio_crowding_precommit_v2.intervention import classify_intervention
from research.c4_portfolio_crowding_precommit_v2.library import build_eligible_library
from research.c4_portfolio_crowding_precommit_v2.policy import policy_contract, policy_sha256
from research.c4_portfolio_crowding_precommit_v2.prior import load_v1
from research.c4_portfolio_crowding_precommit_v2.spec import (
    attribution_stability_gates,
    canonical_spec,
    fold_c4_contract,
    incremental_gate,
    source_sha256,
    spec_sha256,
)
from research.c4_portfolio_crowding_precommit_v2.streams import AUDIT, compare_and_apply, load_v1_raw_streams
from research.systematic_state_transition_full_strategy_v1 import CANARY_EXPECTED, CANARY_ID
from research.systematic_state_transition_full_strategy_v1.spec import frozen_library
from research.c4_portfolio_crowding_precommit_v1 import KEPT_EXIT_IDS

JST = ZoneInfo("Asia/Tokyo")


def _inum(d: dict[str, Any], key: str) -> int | None:
    if key not in d or d[key] is None:
        return None
    return int(d[key])


def decide(*, harvest_result: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = canonical_spec()
    prior = load_v1()
    policy = policy_contract()
    policy_hash = policy_sha256()
    policy_frozen_at = datetime.now(JST).isoformat(timespec="seconds")
    if harvest_result is None:
        harvest = load_v1_raw_streams()
    else:
        harvest = dict(harvest_result)
    counts_started_at = datetime.now(JST).isoformat(timespec="seconds")
    order = dict(harvest.get("order") or {})
    rows_by = dict(harvest.get("rows_by") or {})
    harvest_ok = bool(harvest.get("ok"))
    streams = compare_and_apply(
        rows_by,
        prior_raw_hashes=dict(prior.get("raw_hashes") or {}),
        prior_raw_n=dict(prior.get("raw_n") or {}),
        prior_v1_pass=dict(prior.get("v1_pass_n") or {}),
    ) if harvest_ok else {
        "raw_streams": [],
        "c4_streams": [],
        "compare": [],
        "applied": {},
        "RAW_STREAM_REPRODUCTION_PASS": False,
        "C4_STREAM_INVARIANCE_PASS": False,
        "V1_POLICY_REPRODUCTION_PASS": False,
        "RAW_HASH_MATCH_V1": False,
        "ENTRY_N": 0,
    }
    intervention = classify_intervention(dict(streams.get("applied") or {}), rows_by)
    lib = build_eligible_library(list(intervention.get("eligible_ids") or []))
    entries = frozen_library()
    totals = dict(order.get("totals") or prior.get("totals") or {})
    forensic: list[dict[str, Any]] = []
    if not prior.get("ok"):
        forensic.append({"gap": "PRIOR_V1", "blocker": prior.get("blocker")})
    if spec["CANDIDATE_ECONOMICS_RUN"] or spec["PNL_COMPUTED"]:
        forensic.append({"gap": "ECONOMICS_FLAG"})
    if int(AUDIT.get("PNL_COMPUTED") or 0) or int(AUDIT.get("FILL_COUNT_COMPUTED") or 0):
        forensic.append({"gap": "AUDIT_ECONOMICS"})
    if int(AUDIT.get("V1_OUT_WRITE_N") or 0):
        forensic.append({"gap": "V1_OUT_WRITE"})
    if not harvest_ok:
        forensic.append({"gap": "STREAM_RECONSTRUCTION", "blocker": harvest.get("blocker")})
    if harvest_ok:
        if _inum(totals, "EVENT_N") != EXPECTED_EVENT_N:
            forensic.append({"gap": "EVENT_N"})
        if _inum(totals, "SEQ_MISSING_N") != EXPECTED_SEQ_MISSING_N:
            forensic.append({"gap": "SEQ_MISSING"})
        if _inum(totals, "SEQ_DUPLICATE_N") != EXPECTED_SEQ_DUPLICATE_N:
            forensic.append({"gap": "SEQ_DUPLICATE"})
        if _inum(totals, "SEQ_NON_MONOTONE_N") != EXPECTED_SEQ_NON_MONOTONE_N:
            forensic.append({"gap": "SEQ_NON_MONOTONE"})
        if order.get("SOURCE_EVENT_ORDER_CAUSAL") is not True:
            forensic.append({"gap": "ORDER_NOT_CAUSAL"})
        if order.get("FILESYSTEM_ORDER_USED") or order.get("LEXICOGRAPHIC_SYMBOL_ORDER_USED"):
            forensic.append({"gap": "FORBIDDEN_ORDER"})
        if not streams.get("RAW_STREAM_REPRODUCTION_PASS"):
            forensic.append({"gap": "RAW_STREAM_MISMATCH"})
        if not streams.get("V1_POLICY_REPRODUCTION_PASS"):
            forensic.append({"gap": "V1_C4_STREAM_NOT_REPRODUCED"})
        if not streams.get("C4_STREAM_INVARIANCE_PASS"):
            forensic.append({"gap": "C4_STREAM_INVARIANCE"})
        if int(streams.get("ENTRY_N") or 0) != 25 or len(entries) != 25:
            forensic.append({"gap": "ENTRY_N"})
    if int(intervention.get("ENTRY_N") or 0) != 25:
        forensic.append({"gap": "INTERVENTION_ENTRY_N"})
    if intervention.get("BACKFILL") or lib.get("BACKFILL"):
        forensic.append({"gap": "BACKFILL"})
    if not lib.get("EVERY_TREATMENT_HAS_MATCHED_CONTROL"):
        forensic.append({"gap": "UNMATCHED_CONTROL"})
    if int(lib.get("ELIGIBLE_ENTRY_N") or 0) != int(intervention.get("C4_ATTRIBUTION_ELIGIBLE_ENTRY_N") or 0):
        forensic.append({"gap": "ELIGIBLE_LIBRARY_N"})
    eligible_n = int(intervention.get("C4_ATTRIBUTION_ELIGIBLE_ENTRY_N") or 0)
    if eligible_n:
        if int(lib.get("ELIGIBLE_TOTAL_ARM_N") or 0) != eligible_n * 8:
            forensic.append({"gap": "ELIGIBLE_ARM_N"})
        if any(r.get("C4_POLICY") != TREATMENT_POLICY_ID for r in lib.get("treatments") or []):
            forensic.append({"gap": "TREATMENT_POLICY"})
        if any(r.get("C4_POLICY") != CONTROL_POLICY_ID for r in lib.get("controls") or []):
            forensic.append({"gap": "CONTROL_POLICY"})
    policy_before_counts = policy_frozen_at <= counts_started_at
    hashes = {
        "C4_V2_POLICY_SHA256": policy_hash,
        "C4_V1_POLICY_SHA256": prior.get("C4_POLICY_SHA256"),
        "SPEC_SHA256": spec_sha256(),
        "SOURCE_SHA256": source_sha256(),
        "ENTRY_LIBRARY_SHA256": dumps_sha256(entries),
        "EXECUTION_SHA256": dumps_sha256(execution_contract()),
        "PORTFOLIO_SHA256": dumps_sha256(portfolio_contract()),
        "COVERAGE_GATES_SHA256": dumps_sha256(coverage_gates()),
        "ECONOMIC_GATES_SHA256": dumps_sha256(economic_gates()),
        "STABILITY_GATES_SHA256": dumps_sha256(stability_gates()),
        "INCREMENTAL_GATE_SHA256": dumps_sha256(incremental_gate()),
        "ATTRIBUTION_STABILITY_SHA256": dumps_sha256(attribution_stability_gates()),
        "GATES_SHA256": dumps_sha256(
            {
                "coverage": coverage_gates(),
                "fold_coverage": fold_coverage_gates(),
                "economic": economic_gates(),
                "stability": stability_gates(),
            }
        ),
        "CANARY_SPEC_SHA256": dumps_sha256(canary_spec()),
        "FOLD_ASSIGNMENT_SHA256": dumps_sha256(fold_c4_contract()),
        "ELIGIBLE_IDS_SHA256": dumps_sha256(list(intervention.get("eligible_ids") or [])),
        "TREATMENT_IDS_SHA256": dumps_sha256([r["CANDIDATE_ID"] for r in lib.get("treatments") or []]),
        "CONTROL_IDS_SHA256": dumps_sha256([r["CANDIDATE_ID"] for r in lib.get("controls") or []]),
    }
    if forensic:
        case, case_name, nxt, verdict = "E", CASE_E, NEXT_IF_E, CASE_E
    elif eligible_n == 0:
        case, case_name, nxt, verdict = "I", CASE_INSUFFICIENT, NEXT_IF_INSUFFICIENT, CASE_INSUFFICIENT
    elif (
        harvest_ok
        and prior.get("ok")
        and streams.get("RAW_STREAM_REPRODUCTION_PASS")
        and streams.get("C4_STREAM_INVARIANCE_PASS")
        and policy_before_counts
        and spec["CANDIDATE_ECONOMICS_RUN"] is False
        and bool(lib.get("EVERY_TREATMENT_HAS_MATCHED_CONTROL"))
    ):
        case, case_name, nxt, verdict = "A", CASE_A, NEXT_IF_A, CASE_A
    else:
        case, case_name, nxt, verdict = "E", CASE_E, NEXT_IF_E, CASE_E
        forensic.append({"gap": "CASE_A_CONDITIONS"})

    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "CASE": case,
        "CASE_NAME": case_name,
        "VERDICT": verdict,
        "NEXT": nxt,
        "prior": prior,
        "policy": policy,
        "C4_V2_POLICY_SHA256": policy_hash,
        "C4_POLICY_FROZEN_AT": policy_frozen_at,
        "COUNTS_STARTED_AT": counts_started_at,
        "C4_POLICY_HASH_FROZEN_BEFORE_COUNTS": bool(policy_before_counts),
        "harvest": {
            "ok": harvest_ok,
            "blocker": harvest.get("blocker"),
            "day_ok": harvest.get("day_ok"),
            "SOURCE": harvest.get("SOURCE"),
            "audit": harvest.get("audit") or dict(AUDIT),
        },
        "order": order,
        "streams": {k: v for k, v in streams.items() if k != "applied"},
        "intervention": {k: v for k, v in intervention.items() if k != "rows"},
        "intervention_rows": list(intervention.get("rows") or []),
        "block_intervention": list(intervention.get("block_rows") or []),
        "library": {
            "ENTRY_N": 25,
            "EXIT_N": 4,
            "EXIT_IDS": list(KEPT_EXIT_IDS),
            "EXECUTION_ID": EXECUTION_ID,
            "CONTROL_POLICY_ID": CONTROL_POLICY_ID,
            "TREATMENT_POLICY_ID": TREATMENT_POLICY_ID,
            "ELIGIBLE_ENTRY_N": lib.get("ELIGIBLE_ENTRY_N"),
            "ELIGIBLE_MATCHED_STRATEGY_N": lib.get("ELIGIBLE_MATCHED_STRATEGY_N"),
            "ELIGIBLE_CONTROL_ARM_N": lib.get("ELIGIBLE_CONTROL_ARM_N"),
            "ELIGIBLE_TREATMENT_ARM_N": lib.get("ELIGIBLE_TREATMENT_ARM_N"),
            "ELIGIBLE_TOTAL_ARM_N": lib.get("ELIGIBLE_TOTAL_ARM_N"),
            "EVERY_TREATMENT_HAS_MATCHED_CONTROL": lib.get("EVERY_TREATMENT_HAS_MATCHED_CONTROL"),
            "CONTROL_ELIGIBLE_AS_WINNER": False,
        },
        "controls": lib.get("controls") or [],
        "treatments": lib.get("treatments") or [],
        "matched_matrix": lib.get("matched_matrix") or [],
        "execution": execution_contract(),
        "portfolio": portfolio_contract(),
        "coverage_gates": coverage_gates(),
        "fold_coverage_gates": fold_coverage_gates(),
        "economic_gates": economic_gates(),
        "stability_gates": stability_gates(),
        "incremental_gate": incremental_gate(),
        "attribution_stability": attribution_stability_gates(),
        "folds": fold_c4_contract(),
        "canary": canary_spec(),
        "CANARY_ID": CANARY_ID,
        "CANARY_EXPECTED": dict(CANARY_EXPECTED),
        "CANARY_RUN_THIS_PRECOMMIT": False,
        "hashes": hashes,
        "forensic": forensic,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "CANDIDATE_ECONOMICS_RUN": False,
        "CONTROL_ECONOMICS_RUN": False,
        "TREATMENT_ECONOMICS_RUN": False,
        "FILL_COUNT_COMPUTED": False,
        "TRADE_COUNT_COMPUTED": False,
        "PNL_COMPUTED": False,
        "PF_COMPUTED": False,
        "MAXDD_COMPUTED": False,
        "RANKING_COMPUTED": False,
        "FOLD_ECONOMICS_RUN": False,
        "CONTROL_ELIGIBLE_AS_WINNER": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "SIZING": False,
        "spec": spec,
    }


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    prior = dict(pack.get("prior") or {})
    order = dict(pack.get("order") or {})
    totals = dict(order.get("totals") or prior.get("totals") or {})
    streams = dict(pack.get("streams") or {})
    compare = list(streams.get("compare") or [])
    c4s = list(streams.get("c4_streams") or [])
    inter = dict(pack.get("intervention") or {})
    rows = list(pack.get("intervention_rows") or [])
    lib = dict(pack.get("library") or {})
    mismatch = {r["ENTRY_ID"]: r.get("V1_V2_ROW_MISMATCH_N") for r in compare}
    reject_n = {r["ENTRY_ID"]: r.get("C4_REJECT_N") for r in c4s}
    pass_n = {r["ENTRY_ID"]: r.get("C4_PASS_N") for r in c4s}
    informative = {r["ENTRY_ID"]: r.get("C4_INFORMATIVE_BLOCK_N") for r in rows if r.get("C4_INTERVENTION_ACTIVE")}
    return {
        "1": prior.get("VERDICT"),
        "2": False,
        "3": prior.get("C4_POLICY_ID"),
        "4": prior.get("C4_POLICY_SHA256"),
        "5": TREATMENT_POLICY_ID,
        "6": pack.get("C4_V2_POLICY_SHA256"),
        "7": totals.get("EVENT_N"),
        "8": {
            "SEQ_MISSING_N": totals.get("SEQ_MISSING_N"),
            "SEQ_DUPLICATE_N": totals.get("SEQ_DUPLICATE_N"),
            "SEQ_NON_MONOTONE_N": totals.get("SEQ_NON_MONOTONE_N"),
        },
        "9": False,
        "10": False,
        "11": bool(streams.get("RAW_STREAM_REPRODUCTION_PASS")),
        "12": mismatch,
        "13": reject_n,
        "14": pass_n,
        "15": bool(streams.get("C4_STREAM_INVARIANCE_PASS")),
        "16": 25,
        "17": inter.get("NO_INTERVENTION_ENTRY_N"),
        "18": inter.get("ACTIVE_ENTRY_N"),
        "19": informative,
        "20": inter.get("C4_ATTRIBUTION_ELIGIBLE_ENTRY_N"),
        "21": list(inter.get("eligible_ids") or []),
        "22": list(inter.get("ineligible_ids") or []),
        "23": lib.get("ELIGIBLE_MATCHED_STRATEGY_N"),
        "24": lib.get("ELIGIBLE_CONTROL_ARM_N"),
        "25": lib.get("ELIGIBLE_TREATMENT_ARM_N"),
        "26": lib.get("ELIGIBLE_TOTAL_ARM_N"),
        "27": bool(lib.get("EVERY_TREATMENT_HAS_MATCHED_CONTROL")),
        "28": False,
        "29": True,
        "30": True,
        "31": True,
        "32": True,
        "33": True,
        "34": True,
        "35": True,
        "36": True,
        "37": True,
        "38": False,
        "39": False,
        "40": False,
        "41": False,
        "42": False,
        "43": False,
        "44": "0/0/0",
        "45": False,
        "46": False,
        "47": pack.get("VERDICT"),
        "48": pack.get("NEXT"),
    }
