"""C4 matched-library freeze. Policy hash before counts. No economics."""
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
from research.c4_portfolio_crowding_precommit_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_E,
    CONTROL_POLICY_ID,
    DEVELOPMENT_DAYS,
    EXECUTION_ID,
    KEPT_EXIT_IDS,
    NEXT_IF_A,
    NEXT_IF_B,
    NEXT_IF_E,
    TREATMENT_POLICY_ID,
)
from research.c4_portfolio_crowding_precommit_v1.duplicate_audit import audit_arms
from research.c4_portfolio_crowding_precommit_v1.library import build_matched_library
from research.c4_portfolio_crowding_precommit_v1.order_proof import empty_seq_audit, summarize_order
from research.c4_portfolio_crowding_precommit_v1.policy import policy_contract, policy_sha256
from research.c4_portfolio_crowding_precommit_v1.prior import load_prior
from research.c4_portfolio_crowding_precommit_v1.spec import (
    canonical_spec,
    fold_c4_contract,
    incremental_gate,
    spec_sha256,
    source_sha256,
)
from research.c4_portfolio_crowding_precommit_v1.streams import AUDIT, build_stream_report, harvest_raw_streams
from research.systematic_state_transition_full_strategy_v1 import CANARY_EXPECTED, CANARY_ID
from research.systematic_state_transition_full_strategy_v1.spec import frozen_library

JST = ZoneInfo("Asia/Tokyo")


def _blocker_kind(blocker: str | None) -> str | None:
    text = str(blocker or "")
    if "SEQ_MISSING" in text:
        return "SEQ_MISSING"
    if "SEQ_DUPLICATE" in text:
        return "SEQ_DUPLICATE"
    if "BAR_FINISH_SEQ_MISSING" in text:
        return "SEQ_MISSING"
    return None


def decide(*, harvest_result: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = canonical_spec()
    prior = load_prior()
    policy = policy_contract()
    policy_hash = policy_sha256()
    policy_frozen_at = datetime.now(JST).isoformat(timespec="seconds")
    if harvest_result is None:
        harvest = harvest_raw_streams()
    else:
        harvest = dict(harvest_result)
    counts_started_at = datetime.now(JST).isoformat(timespec="seconds")
    order = dict(harvest.get("order") or summarize_order([]))
    rows_by = dict(harvest.get("rows_by") or {})
    harvest_ok = bool(harvest.get("ok"))
    kind = _blocker_kind(harvest.get("blocker"))
    apply_c4 = bool(harvest_ok and order.get("SOURCE_EVENT_ORDER_UNIQUE"))
    if apply_c4:
        streams = build_stream_report(rows_by)
    else:
        streams = {
            "raw_streams": [],
            "c4_streams": [],
            "invariance": [],
            "same_t0": [],
            "RAW_STREAM_INVARIANCE_PASS": False,
            "C4_STREAM_INVARIANCE_PASS": False,
            "ENTRY_N": 0,
            "C4_NOT_APPLIED": True,
            "REASON": harvest.get("blocker") or "ORDER_NOT_UNIQUE",
        }
    lib = build_matched_library()
    dup = audit_arms(lib)
    entries = frozen_library()
    entry_ids = [str(r["CANDIDATE_ID"]) for r in entries]
    gates_obj = {
        "coverage": coverage_gates(),
        "fold_coverage": fold_coverage_gates(),
        "economic": economic_gates(),
        "stability": stability_gates(),
    }
    hashes = {
        "C4_POLICY_SHA256": policy_hash,
        "SPEC_SHA256": spec_sha256(),
        "SOURCE_SHA256": source_sha256(),
        "ENTRY_LIBRARY_SHA256": dumps_sha256(entries),
        "EXECUTION_SHA256": dumps_sha256(execution_contract()),
        "PORTFOLIO_SHA256": dumps_sha256(portfolio_contract()),
        "COVERAGE_GATES_SHA256": dumps_sha256(coverage_gates()),
        "ECONOMIC_GATES_SHA256": dumps_sha256(economic_gates()),
        "STABILITY_GATES_SHA256": dumps_sha256(stability_gates()),
        "INCREMENTAL_GATE_SHA256": dumps_sha256(incremental_gate()),
        "GATES_SHA256": dumps_sha256(gates_obj),
        "CANARY_SPEC_SHA256": dumps_sha256(canary_spec()),
        "FOLD_ASSIGNMENT_SHA256": dumps_sha256(fold_c4_contract()),
        "MATCHED_MATRIX_SHA256": dumps_sha256([r["TREATMENT_ID"] for r in lib["matched_matrix"]]),
        "CONTROL_IDS_SHA256": dumps_sha256([r["CANDIDATE_ID"] for r in lib["controls"]]),
        "TREATMENT_IDS_SHA256": dumps_sha256([r["CANDIDATE_ID"] for r in lib["treatments"]]),
    }
    forensic: list[dict[str, Any]] = []
    if not prior.get("ok"):
        forensic.append({"gap": "PRIOR", "blocker": prior.get("blocker")})
    if spec["CANDIDATE_ECONOMICS_RUN"] or spec["CONTROL_ECONOMICS_RUN"] or spec["TREATMENT_ECONOMICS_RUN"]:
        forensic.append({"gap": "ECONOMICS_FLAG"})
    if spec["FILL_COUNT_COMPUTED"] or spec["PNL_COMPUTED"] or spec["RANKING_COMPUTED"]:
        forensic.append({"gap": "METRIC_FLAG"})
    if int(AUDIT.get("PNL_COMPUTED") or 0) or int(AUDIT.get("FILL_COUNT_COMPUTED") or 0):
        forensic.append({"gap": "AUDIT_ECONOMICS"})
    if int(lib["TOTAL_ARM_N"]) != 200:
        forensic.append({"gap": "ARM_N"})
    if not lib["EVERY_TREATMENT_HAS_MATCHED_CONTROL"]:
        forensic.append({"gap": "UNMATCHED_CONTROL"})
    if int(dup["TREATMENT_DUPLICATE_REMOVED_N"]) != 0 or int(dup["CONTROL_REMOVED_N"]) != 0:
        forensic.append({"gap": "ARM_REMOVED"})
    if "C4_FIRST_UNIQUE_T0" in TREATMENT_POLICY_ID:
        forensic.append({"gap": "AMBIGUOUS_POLICY_ID"})
    if harvest_ok and apply_c4:
        if not streams.get("RAW_STREAM_INVARIANCE_PASS"):
            forensic.append({"gap": "RAW_STREAM_INVARIANCE"})
        if not streams.get("C4_STREAM_INVARIANCE_PASS"):
            forensic.append({"gap": "C4_STREAM_INVARIANCE"})
        if int(streams.get("ENTRY_N") or 0) != 25:
            forensic.append({"gap": "STREAM_ENTRY_N"})
    if harvest_ok and order.get("PROOF_INCOMPLETE"):
        forensic.append({"gap": "ORDER_PROOF_INCOMPLETE"})
    if harvest_ok and not order.get("ok"):
        if not order.get("UNRESOLVABLE"):
            forensic.append({"gap": "ORDER_NOT_CAUSAL"})
    if not harvest_ok and kind is None:
        forensic.append({"gap": "HARVEST", "blocker": harvest.get("blocker")})
    if int(order.get("totals", empty_seq_audit()).get("EVENT_N") or 0) == 0 and kind is None and not harvest_ok:
        if not any(g.get("gap") == "HARVEST" for g in forensic):
            forensic.append({"gap": "NO_EVENTS"})

    unresolvable = bool(order.get("UNRESOLVABLE")) or kind in {"SEQ_MISSING", "SEQ_DUPLICATE"}
    if harvest_ok and int((order.get("totals") or {}).get("EVENT_N") or 0) == 0:
        unresolvable = False
        forensic.append({"gap": "ZERO_EVENTS"})

    policy_before_counts = policy_frozen_at <= counts_started_at
    case_a_ready = (
        not forensic
        and prior.get("ok")
        and harvest_ok
        and bool(order.get("SOURCE_EVENT_ORDER_CAUSAL"))
        and bool(order.get("SOURCE_EVENT_ORDER_UNIQUE"))
        and not order.get("FILESYSTEM_ORDER_USED")
        and not order.get("LEXICOGRAPHIC_SYMBOL_ORDER_USED")
        and int(lib["ENTRY_N"]) == 25
        and int(lib["EXIT_N"]) == 4
        and int(lib["TREATMENT_ARM_N"]) == 100
        and int(lib["CONTROL_ARM_N"]) == 100
        and bool(streams.get("RAW_STREAM_INVARIANCE_PASS"))
        and bool(streams.get("C4_STREAM_INVARIANCE_PASS"))
        and policy_before_counts
        and spec["CANDIDATE_ECONOMICS_RUN"] is False
    )
    if forensic:
        case, case_name, nxt, verdict = "E", CASE_E, NEXT_IF_E, CASE_E
    elif unresolvable:
        case, case_name, nxt, verdict = "B", CASE_B, NEXT_IF_B, CASE_B
    elif case_a_ready:
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
        "C4_POLICY_SHA256": policy_hash,
        "C4_POLICY_FROZEN_AT": policy_frozen_at,
        "COUNTS_STARTED_AT": counts_started_at,
        "C4_POLICY_HASH_FROZEN_BEFORE_COUNTS": bool(policy_before_counts),
        "harvest": {
            "ok": harvest_ok,
            "blocker": harvest.get("blocker"),
            "day_ok": harvest.get("day_ok"),
            "audit": harvest.get("audit") or dict(AUDIT),
        },
        "order": order,
        "streams": streams,
        "library": {
            "ENTRY_N": lib["ENTRY_N"],
            "EXIT_N": lib["EXIT_N"],
            "MATCHED_STRATEGY_N": lib["MATCHED_STRATEGY_N"],
            "CONTROL_ARM_N": lib["CONTROL_ARM_N"],
            "TREATMENT_ARM_N": lib["TREATMENT_ARM_N"],
            "TOTAL_ARM_N": lib["TOTAL_ARM_N"],
            "EVERY_TREATMENT_HAS_MATCHED_CONTROL": lib["EVERY_TREATMENT_HAS_MATCHED_CONTROL"],
            "ENTRY_IDS": entry_ids,
            "EXIT_IDS": list(KEPT_EXIT_IDS),
            "EXECUTION_ID": EXECUTION_ID,
            "CONTROL_POLICY_ID": CONTROL_POLICY_ID,
            "TREATMENT_POLICY_ID": TREATMENT_POLICY_ID,
        },
        "controls": lib["controls"],
        "treatments": lib["treatments"],
        "matched_matrix": lib["matched_matrix"],
        "duplicate_map": dup,
        "execution": execution_contract(),
        "portfolio": portfolio_contract(),
        "coverage_gates": coverage_gates(),
        "fold_coverage_gates": fold_coverage_gates(),
        "economic_gates": economic_gates(),
        "stability_gates": stability_gates(),
        "incremental_gate": incremental_gate(),
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
    c1 = dict(prior.get("c1") or {})
    rethink = dict(prior.get("rethink") or {})
    st = dict(prior.get("st_library") or {})
    streams = dict(pack.get("streams") or {})
    order = dict(pack.get("order") or {})
    lib = dict(pack.get("library") or {})
    same = list(streams.get("same_t0") or [])
    raw_n = {r["ENTRY_ID"]: r.get("RAW_SIGNAL_N") for r in same}
    multi_n = {r["ENTRY_ID"]: r.get("T0_WITH_MULTIPLE_ROWS_N") for r in same}
    pass_n = {r["ENTRY_ID"]: r.get("C4_PASS_N") for r in same}
    return {
        "1": c1.get("VERDICT"),
        "2": c1.get("ECONOMIC_PASS_N"),
        "3": bool(c1.get("C1_CLOSED")),
        "4": rethink.get("VERDICT"),
        "5": bool(rethink.get("C4_ELIGIBLE")),
        "6": rethink.get("C4_PRIOR_FULL_CAUSAL_TESTED"),
        "7": lib.get("ENTRY_N"),
        "8": bool(st.get("ALL_25_ENTRY_USED")) and int(lib.get("ENTRY_N") or 0) == 25,
        "9": False,
        "10": lib.get("EXIT_N"),
        "11": lib.get("EXIT_IDS"),
        "12": lib.get("EXECUTION_ID"),
        "13": bool(order.get("PRE_ADMISSION_STREAM_AVAILABLE")),
        "14": bool(order.get("CANONICAL_SOURCE_EVENT_ORDER_AVAILABLE")),
        "15": bool(order.get("SOURCE_EVENT_ORDER_CAUSAL")),
        "16": False,
        "17": TREATMENT_POLICY_ID,
        "18": True,
        "19": False,
        "20": False,
        "21": False,
        "22": False,
        "23": False,
        "24": raw_n,
        "25": multi_n,
        "26": pass_n,
        "27": bool(streams.get("RAW_STREAM_INVARIANCE_PASS")),
        "28": bool(streams.get("C4_STREAM_INVARIANCE_PASS")),
        "29": bool(pack.get("C4_POLICY_HASH_FROZEN_BEFORE_COUNTS")),
        "30": lib.get("MATCHED_STRATEGY_N"),
        "31": lib.get("CONTROL_ARM_N"),
        "32": lib.get("TREATMENT_ARM_N"),
        "33": lib.get("TOTAL_ARM_N"),
        "34": bool(lib.get("EVERY_TREATMENT_HAS_MATCHED_CONTROL")),
        "35": False,
        "36": True,
        "37": True,
        "38": True,
        "39": True,
        "40": True,
        "41": True,
        "42": False,
        "43": False,
        "44": False,
        "45": False,
        "46": False,
        "47": False,
        "48": "0/0/0",
        "49": False,
        "50": False,
        "51": pack.get("VERDICT"),
        "52": pack.get("NEXT"),
    }
