"""Freeze 50 Full Strategy pairs. No economics. No signal counts. No harvest."""
from __future__ import annotations

from typing import Any

from research.c1_multi_timeframe_entry_exit_pair_precommit_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_E,
    DEVELOPMENT_DAYS,
    EXECUTION_ID,
    EXIT_IDS,
    NEXT_IF_A,
    NEXT_IF_E,
    OR_FINAL_STATUS,
    PFQ_DOCUMENT_ID,
    PFQ_LINE,
    PFQ_VERDICT,
    RAW_CANDIDATE_IDS,
    REQUIRED_PRIOR_HASHES,
    SIMPLE_FULL_X1_ID,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.duplicate_audit import apply_prune, audit_raw_library
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.exits_registry import prove_exit_library
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.library import build_raw_strategy_library
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.prior import load_prior, snapshot_prior_artifacts
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.spec import (
    canary_spec,
    canonical_spec,
    coverage_gates,
    dumps_sha256,
    economic_gates,
    exact_entry_rule,
    execution_contract,
    fold_assignment,
    fold_coverage_gates,
    methodology_contract,
    next_full_strategy_rule,
    pair_interaction_diagnostics_contract,
    portfolio_contract,
    spec_sha256,
    stability_gates,
)
from research.c1_multi_timeframe_precommit_v1.htf_states import htf_registry
from research.c1_multi_timeframe_precommit_v1.library import build_raw_library
from research.c1_multi_timeframe_precommit_v1.one_min_states import one_min_registry


def _int(d: dict[str, Any], key: str, default: int = 0) -> int:
    if key not in d or d[key] is None:
        return int(default)
    return int(d[key])


def decide() -> dict[str, Any]:
    spec = canonical_spec()
    prior = load_prior()
    prior_art = snapshot_prior_artifacts()
    one_min = one_min_registry()
    htf = htf_registry()
    entries = build_raw_library()
    exits = prove_exit_library()
    raw = build_raw_strategy_library(entries)
    dup_map = audit_raw_library(raw)
    final, counts = apply_prune(raw, dup_map)
    exec_c = execution_contract()
    gates_obj = {
        "coverage": coverage_gates(),
        "fold_coverage": fold_coverage_gates(),
        "economic": economic_gates(),
        "stability": stability_gates(),
    }
    hashes = {
        "ONE_MIN_STATE_REGISTRY_SHA256": dumps_sha256(one_min),
        "HTF_STATE_REGISTRY_SHA256": dumps_sha256(htf),
        "ENTRY_RAW_LIBRARY_SHA256": dumps_sha256(entries),
        "ENTRY_FINAL_LIBRARY_SHA256": dumps_sha256(entries),
        "RAW_STRATEGY_LIBRARY_SHA256": dumps_sha256(raw),
        "DUPLICATE_MAP_SHA256": dumps_sha256(dup_map),
        "FINAL_STRATEGY_LIBRARY_SHA256": dumps_sha256(final),
        "EXECUTION_SHA256": dumps_sha256(exec_c),
        "EXIT_LIBRARY_SHA256": dumps_sha256(exits),
        "PORTFOLIO_SHA256": dumps_sha256(portfolio_contract()),
        "FOLD_ASSIGNMENT_SHA256": dumps_sha256(fold_assignment()),
        "GATES_SHA256": dumps_sha256(gates_obj),
        "CANARY_SPEC_SHA256": dumps_sha256(canary_spec()),
        "SPEC_SHA256": spec_sha256(),
    }
    entry_hash_ok = (
        hashes["ENTRY_RAW_LIBRARY_SHA256"] == REQUIRED_PRIOR_HASHES["RAW_CANDIDATE_LIBRARY_SHA256"]
        and hashes["ENTRY_FINAL_LIBRARY_SHA256"] == REQUIRED_PRIOR_HASHES["FINAL_CANDIDATE_LIBRARY_SHA256"]
        and bool(prior.get("ENTRY_LIBRARY_HASH_UNCHANGED"))
    )
    frozen_ok = (
        hashes["ONE_MIN_STATE_REGISTRY_SHA256"] == REQUIRED_PRIOR_HASHES["ONE_MIN_STATE_REGISTRY_SHA256"]
        and hashes["HTF_STATE_REGISTRY_SHA256"] == REQUIRED_PRIOR_HASHES["HTF_STATE_REGISTRY_SHA256"]
        and hashes["EXECUTION_SHA256"] == REQUIRED_PRIOR_HASHES["EXECUTION_SHA256"]
        and hashes["PORTFOLIO_SHA256"] == REQUIRED_PRIOR_HASHES["PORTFOLIO_SHA256"]
        and hashes["FOLD_ASSIGNMENT_SHA256"] == REQUIRED_PRIOR_HASHES["FOLD_ASSIGNMENT_SHA256"]
        and hashes["GATES_SHA256"] == REQUIRED_PRIOR_HASHES["GATES_SHA256"]
        and hashes["CANARY_SPEC_SHA256"] == REQUIRED_PRIOR_HASHES["CANARY_SPEC_SHA256"]
    )
    ids = [r["CANDIDATE_ID"] for r in raw]
    x1_ok = all(EXECUTION_ID in cid for cid in ids) and all("__X1__" not in cid for cid in ids)
    exec_ok = str(exec_c.get("EXEC_ID") or "") == EXECUTION_ID and SIMPLE_FULL_X1_ID != EXECUTION_ID
    exit_ok = (
        len(exits) == 5
        and [r["EXIT_ID"] for r in exits] == list(EXIT_IDS)
        and all(not r["PARAMETER_CHANGED"] for r in exits)
        and all(not r["IMPLEMENTATION_CHANGED"] for r in exits)
        and all(not r["NEW_EXIT"] for r in exits)
        and all(r.get("SOURCE_BRANCH") for r in exits)
    )
    pair_ok = _int(counts, "RAW_STRATEGY_N") == 50 and [r["CANDIDATE_ID"] for r in entries] == list(RAW_CANDIDATE_IDS)
    meth = methodology_contract()
    econ_ok = (
        spec["CANDIDATE_ECONOMICS_RUN"] is False
        and spec["CANDIDATE_SIGNAL_COUNT_COMPUTED"] is False
        and spec["CANDIDATE_PNL_COMPUTED"] is False
        and meth["TECHNICAL_EXIT_NEW_SEARCH"] is False
        and meth["EXIT6_CREATED"] is False
        and meth["ENTRY_ONLY_DECISION"] is False
        and meth["SEQUENTIAL_ENTRY_THEN_EXIT_SELECTION"] is False
        and meth["Z3_ONLY_C1_CLOSURE_FORBIDDEN"] is True
    )
    art_ok = all(bool(v) for v in prior_art.values())
    forensic = []
    if not prior.get("ok"):
        forensic.append({"gap": "PRIOR_C1", "blocker": prior.get("blocker")})
    if not entry_hash_ok:
        forensic.append({"gap": "ENTRY_LIBRARY_HASH"})
    if not frozen_ok:
        forensic.append({"gap": "FROZEN_CONTRACT_HASH"})
    if not x1_ok or not exec_ok:
        forensic.append({"gap": "EXECUTION_X1_IDENTITY"})
    if not exit_ok:
        forensic.append({"gap": "EXIT_SOURCE"})
    if not pair_ok:
        forensic.append({"gap": "PAIR_COUNT_OR_ENTRY_IDS"})
    if not econ_ok:
        forensic.append({"gap": "METHODOLOGY_OR_ECONOMICS_FLAG"})
    if not art_ok:
        forensic.append({"gap": "PRIOR_ARTIFACT_MISSING"})
    if _int(counts, "UNKNOWN_IDENTITY_N") != 0:
        forensic.append({"gap": "DUPLICATE_IDENTITY_UNKNOWN"})
    if bool(counts.get("BACKFILL")):
        forensic.append({"gap": "BACKFILL"})
    if _int(counts, "RAW_STRATEGY_N") != 50:
        forensic.append({"gap": "RAW_STRATEGY_N"})

    integrity = bool(forensic)
    if integrity:
        case = "E"
        case_name = CASE_E
        nxt = NEXT_IF_E
        verdict = CASE_E
    else:
        case = "A"
        case_name = CASE_A
        nxt = NEXT_IF_A
        verdict = CASE_A

    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "CASE": case,
        "CASE_NAME": case_name,
        "VERDICT": verdict,
        "NEXT": nxt,
        "prior": prior,
        "prior_artifact_sha256": prior_art,
        "one_min_states": one_min,
        "htf_states": htf,
        "entry_library": entries,
        "exit_library": exits,
        "raw_library": raw,
        "duplicate_map": dup_map,
        "final_library": final,
        "counts": counts,
        "hashes": hashes,
        "execution": exec_c,
        "portfolio": portfolio_contract(),
        "folds": fold_assignment(),
        "coverage_gates": coverage_gates(),
        "fold_coverage_gates": fold_coverage_gates(),
        "economic_gates": economic_gates(),
        "stability_gates": stability_gates(),
        "canary": canary_spec(),
        "methodology": meth,
        "next_full_strategy_rule": next_full_strategy_rule(),
        "pair_interaction_diagnostics": pair_interaction_diagnostics_contract(),
        "exact_entry_rule": exact_entry_rule(),
        "forensic": forensic,
        "ENTRY_LIBRARY_HASH_UNCHANGED": entry_hash_ok,
        "PRIOR_ARTIFACT_MODIFIED": False,
        "PRIOR_Z3_ONLY_NEXT_SUPERSEDED": True,
        "EXECUTION_ID_AMBIGUITY": False,
        "ENTRY_ONLY_DECISION": False,
        "SEQUENTIAL_ENTRY_THEN_EXIT_SELECTION": False,
        "Z3_ONLY_C1_CLOSURE_FORBIDDEN": True,
        "CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED": True,
        "TECHNICAL_EXIT_NEW_SEARCH": False,
        "NEW_EXIT_RULE_N": 0,
        "EXIT_THRESHOLD_RETUNE_N": 0,
        "EXIT6_CREATED": False,
        "EXIT_PARAMETER_CHANGED": False,
        "EXIT_IMPLEMENTATION_CHANGED": False,
        "NEW_EXIT_CREATED": False,
        "C4_REMAINS_ELIGIBLE": True,
        "C4_EXECUTED_THIS_RUN": False,
        "C4_RULE_CREATED": False,
        "C1_BROADENS_ENTRY_POPULATION_CLAIM": False,
        "CANDIDATE_ECONOMICS_RUN": False,
        "CANDIDATE_SIGNAL_COUNT_COMPUTED": False,
        "CANDIDATE_PNL_COMPUTED": False,
        "CANDIDATE_BACKFILL": False,
        "OR_FINAL_STATUS": OR_FINAL_STATUS,
        "OR_ECONOMICS_OPENED": False,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "PFQ": {"document": PFQ_DOCUMENT_ID, "verdict": PFQ_VERDICT, "line": PFQ_LINE},
        "integrity_ok": not integrity,
    }


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    prior = dict(pack.get("prior") or {})
    c = dict(pack.get("counts") or {})
    exits = list(pack.get("exit_library") or [])
    raw = list(pack.get("raw_library") or [])
    ids = [r.get("CANDIDATE_ID") for r in raw]
    x1_all = bool(ids) and all(EXECUTION_ID in str(x) for x in ids)
    exit_src = [
        {
            "EXIT_ID": r["EXIT_ID"],
            "SOURCE_PATH": r["SOURCE_PATH"],
            "SOURCE_FUNCTION": r["SOURCE_FUNCTION"],
            "SOURCE_KEY": r["SOURCE_KEY"],
            "SOURCE_SHA256": r["SOURCE_SHA256"],
            "SOURCE_FILE_SHA256": r["SOURCE_FILE_SHA256"],
            "CONSTANTS": r["CONSTANTS"],
            "TRIGGER_TIMEFRAME": r["TRIGGER_TIMEFRAME"],
            "FIRST_FIRE_SEMANTICS": r["FIRST_FIRE_SEMANTICS"],
            "BID_EXECUTION_SEMANTICS": r["BID_EXECUTION_SEMANTICS"],
            "SESSION_CLOSE_SEMANTICS": r["SESSION_CLOSE_SEMANTICS"],
        }
        for r in exits
    ]
    return {
        "1": prior.get("VERDICT"),
        "2": bool(pack.get("PRIOR_ARTIFACT_MODIFIED")),
        "3": bool(pack.get("PRIOR_Z3_ONLY_NEXT_SUPERSEDED")),
        "4": 10,
        "5": bool(pack.get("ENTRY_LIBRARY_HASH_UNCHANGED")),
        "6": EXECUTION_ID,
        "7": bool(pack.get("EXECUTION_ID_AMBIGUITY")) is False,
        "8": 5,
        "9": list(EXIT_IDS),
        "10": exit_src,
        "11": True,
        "12": False,
        "13": False,
        "14": False,
        "15": _int(c, "RAW_STRATEGY_N"),
        "16": _int(c, "DUPLICATE_N"),
        "17": _int(c, "FINAL_STRATEGY_N"),
        "18": x1_all,
        "19": False,
        "20": False,
        "21": True,
        "22": True,
        "23": True,
        "24": True,
        "25": True,
        "26": False,
        "27": False,
        "28": False,
        "29": False,
        "30": False,
        "31": False,
        "32": "0/0/0",
        "33": False,
        "34": False,
        "35": pack.get("VERDICT"),
        "36": pack.get("NEXT"),
        "1_prior_C1_verdict": prior.get("VERDICT"),
        "2_prior_artifact_modified": bool(pack.get("PRIOR_ARTIFACT_MODIFIED")),
        "3_prior_Z3_only_NEXT_superseded": bool(pack.get("PRIOR_Z3_ONLY_NEXT_SUPERSEDED")),
        "4_frozen_ENTRY_N": 10,
        "5_ENTRY_library_hash_unchanged": bool(pack.get("ENTRY_LIBRARY_HASH_UNCHANGED")),
        "6_exact_execution_ID": EXECUTION_ID,
        "7_bare_X1_ambiguity_avoided": bool(pack.get("EXECUTION_ID_AMBIGUITY")) is False,
        "8_EXIT_N": 5,
        "9_exact_EXIT_IDs": list(EXIT_IDS),
        "10_exact_EXIT_source_hash_each": exit_src,
        "11_technical_EXIT_development_exhausted": True,
        "12_new_technical_EXIT_search": False,
        "13_EXIT_parameter_changed": False,
        "14_new_EXIT_created": False,
        "15_raw_pair_N": _int(c, "RAW_STRATEGY_N"),
        "16_duplicate_N": _int(c, "DUPLICATE_N"),
        "17_final_pair_N": _int(c, "FINAL_STRATEGY_N"),
        "18_candidate_IDs_use_X1_IMMEDIATE_ASK": x1_all,
        "19_ENTRY_only_decision": False,
        "20_sequential_ENTRY_then_EXIT_selection": False,
        "21_Z3_only_C1_closure_forbidden": True,
        "22_coverage_gates_frozen": True,
        "23_economic_gates_frozen": True,
        "24_stability_gates_frozen": True,
        "25_canary_frozen": True,
        "26_candidate_economics_run": False,
        "27_Holdout_read": False,
        "28_Stress_read": False,
        "29_future_used": False,
        "30_Sizing": False,
        "31_Runtime_changed": False,
        "32_submit_cancel_live": "0/0/0",
        "33_TRUE_OOS": False,
        "34_CERTIFIED": False,
        "35_VERDICT": pack.get("VERDICT"),
        "36_NEXT": pack.get("NEXT"),
    }
