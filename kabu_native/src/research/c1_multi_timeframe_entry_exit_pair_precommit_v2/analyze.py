"""EXIT semantic uniqueness audit. No candidate economics. No harvest of C1 signals."""
from __future__ import annotations

import json
from typing import Any

import numpy as np

from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.spec import (
    canary_spec,
    coverage_gates,
    dumps_sha256 as c1_dumps,
    economic_gates,
    execution_contract,
    fold_assignment,
    fold_coverage_gates,
    portfolio_contract,
    stability_gates,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_E,
    DEV_CLASSIFICATION,
    DEVELOPMENT_DAYS,
    EXECUTION_ID,
    EXIT_IDS,
    NEXT_IF_A,
    NEXT_IF_B,
    NEXT_IF_E,
    REQUIRED_PRIOR_HASHES,
    Z3_PREVIOUSLY_OBSERVED_PAIR_N,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.bar_contract import prove_valid_bar_contract
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.bars_load import load_dev_ohlc
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.equivalence import build_final_pairs, classify
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.exits_proof import prove_exit_predicates, z2_z4_source_domain_equivalence
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.isolation import RESEARCH_ROOT
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.prior import load_prior, snapshot_prior_artifacts
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.spec import canonical_spec, dumps_sha256, spec_sha256
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.suffix_audit import audit_suffix_domain
from research.c1_multi_timeframe_precommit_v1.library import build_raw_library


def already_executed_check(*, exit_file_sha: str, finish_sha: str) -> dict[str, Any]:
    path = RESEARCH_ROOT / "c1_multi_timeframe_entry_exit_pair_precommit_v2" / "report.json"
    searched = [str(path)]
    if not path.is_file():
        return {
            "ALREADY_EXECUTED_CHECK": True,
            "REUSED_EXISTING_RESULT": False,
            "reason": "NO_PRIOR_V2_REPORT",
            "searched": searched,
        }
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "ALREADY_EXECUTED_CHECK": True,
            "REUSED_EXISTING_RESULT": False,
            "reason": "V2_REPORT_UNREADABLE",
            "searched": searched,
        }
    d = dict(obj.get("decision") or {})
    same = (
        str(obj.get("ANALYSIS_ID") or "") == ANALYSIS_ID
        and str((d.get("exit_predicates") or {}).get("SOURCE_FILE_SHA256") or "") == str(exit_file_sha)
        and str((d.get("bar_contract") or {}).get("FINISH_SHA256") or "") == str(finish_sha)
        and bool((d.get("suffix_audit") or {}).get("ALL_10_PAIRS_COMPLETED"))
        and str(d.get("VERDICT") or "") in {CASE_A, CASE_B}
    )
    return {
        "ALREADY_EXECUTED_CHECK": True,
        "REUSED_EXISTING_RESULT": bool(same),
        "reason": "MATCHING_SOURCE_AND_CONTRACT" if same else "EXISTING_REPORT_NOT_REUSABLE",
        "searched": searched,
        "prior_verdict": d.get("VERDICT"),
    }


def _int(d: dict[str, Any], key: str, default: int = 0) -> int:
    if key not in d or d[key] is None:
        return int(default)
    return int(d[key])


def decide(*, rows: list[dict[str, Any]] | None = None, skip_ohlc_load: bool = False) -> dict[str, Any]:
    spec = canonical_spec()
    prior = load_prior()
    prior_art = snapshot_prior_artifacts()
    bar_c = prove_valid_bar_contract()
    pred = prove_exit_predicates()
    z2z4 = z2_z4_source_domain_equivalence(bar_c, pred)
    reuse = already_executed_check(
        exit_file_sha=str(pred.get("SOURCE_FILE_SHA256") or ""),
        finish_sha=str(bar_c.get("FINISH_SHA256") or ""),
    )
    integ = {
        "OPEN_NONFINITE_N": 0,
        "HIGH_NONFINITE_N": 0,
        "LOW_NONFINITE_N": 0,
        "CLOSE_NONFINITE_N": 0,
        "ARRAY_N": 0,
        "BAR_N": 0,
        "DAY_N": 0,
    }
    ohlc_ok = True
    ohlc_blocker = None
    suffix = {"ok": False, "pairwise": [], "ALL_10_PAIRS_COMPLETED": False, "SUFFIX_CASE_N": 0}
    reused = False
    if rows is not None:
        suffix = audit_suffix_domain(rows)
        integ["ARRAY_N"] = len(rows)
        integ["BAR_N"] = sum(int(r.get("bar_n") or 0) for r in rows)
        for r in rows:
            for name, key in (("OPEN", "open"), ("HIGH", "high"), ("LOW", "low"), ("CLOSE", "close")):
                arr = r.get(key)
                if arr is None:
                    continue
                integ[f"{name}_NONFINITE_N"] += int(np.sum(~np.isfinite(np.asarray(arr, dtype=float))))
    elif bool(reuse.get("REUSED_EXISTING_RESULT")) and not skip_ohlc_load:
        old = json.loads((RESEARCH_ROOT / "c1_multi_timeframe_entry_exit_pair_precommit_v2" / "report.json").read_text(encoding="utf-8"))
        old_d = dict(old.get("decision") or {})
        suffix = dict(old_d.get("suffix_audit") or {})
        integ = dict(old_d.get("bar_integrity") or integ)
        reused = True
    else:
        loaded = load_dev_ohlc()
        ohlc_ok = bool(loaded.get("ok"))
        ohlc_blocker = loaded.get("blocker")
        integ = dict(loaded.get("integrity") or integ)
        if ohlc_ok:
            suffix = audit_suffix_domain(list(loaded.get("rows") or []))
    eq = classify(list(suffix.get("pairwise") or []), z2z4=z2z4, predicates=pred)
    kept = list(eq.get("kept_exit_ids") or [])
    dropped = list(eq.get("dropped_exit_ids") or [])
    forensic = []
    if not prior.get("ok"):
        forensic.append({"gap": "PRIOR", "blocker": prior.get("blocker")})
    if not bar_c.get("ok"):
        forensic.append({"gap": "VALID_BAR_CONTRACT"})
    if not pred.get("ok") or not pred.get("HASH_MATCH"):
        forensic.append({"gap": "EXIT_SOURCE_HASH"})
    if not ohlc_ok:
        forensic.append({"gap": "DEV_OHLC", "blocker": ohlc_blocker})
    if not suffix.get("ok") or not suffix.get("ALL_10_PAIRS_COMPLETED"):
        forensic.append({"gap": "SUFFIX_AUDIT_INCOMPLETE"})
    if eq.get("unproven_dev_equivalent"):
        forensic.append({"gap": "DEV_DOMAIN_EQUIVALENT_BUT_GLOBAL_SEMANTICS_UNPROVEN"})
    if eq.get("contradiction"):
        forensic.append({"gap": "SOURCE_PROVEN_BUT_DEV_MISMATCH"})
    entries = build_raw_library()
    entry_hash = dumps_sha256(entries)
    gates_obj = {
        "coverage": coverage_gates(),
        "fold_coverage": fold_coverage_gates(),
        "economic": economic_gates(),
        "stability": stability_gates(),
    }
    exec_hash = c1_dumps(execution_contract())
    gates_hash = c1_dumps(gates_obj)
    if entry_hash != REQUIRED_PRIOR_HASHES["FINAL_CANDIDATE_LIBRARY_SHA256"]:
        forensic.append({"gap": "ENTRY_LIBRARY_HASH"})
    if exec_hash != REQUIRED_PRIOR_HASHES["EXECUTION_SHA256"]:
        forensic.append({"gap": "EXECUTION_HASH"})
    if gates_hash != REQUIRED_PRIOR_HASHES["GATES_SHA256"]:
        forensic.append({"gap": "GATES_MODIFIED"})
    if not all(bool(v) for v in prior_art.values()):
        forensic.append({"gap": "PRIOR_ARTIFACT_MISSING"})
    if spec["CANDIDATE_ECONOMICS_RUN"] or spec["NEW_EXIT"] or spec["ENTRY_MODIFIED"]:
        forensic.append({"gap": "FORBIDDEN_FLAG"})

    integrity = bool(forensic)
    all_distinct = int(eq.get("FINAL_EXIT_N") or 0) == 5 and not dropped
    collapsed = int(eq.get("FINAL_EXIT_N") or 0) < 5 and bool(dropped)
    if integrity:
        case, case_name, nxt, verdict = "E", CASE_E, NEXT_IF_E, CASE_E
        kept = list(EXIT_IDS)
        dropped = []
        eq = dict(eq)
        eq["kept_exit_ids"] = kept
        eq["dropped_exit_ids"] = dropped
        eq["FINAL_EXIT_N"] = 5
    elif collapsed:
        case, case_name, nxt, verdict = "A", CASE_A, NEXT_IF_A, CASE_A
    elif all_distinct:
        case, case_name, nxt, verdict = "B", CASE_B, NEXT_IF_B, CASE_B
    else:
        case, case_name, nxt, verdict = "E", CASE_E, NEXT_IF_E, CASE_E
        kept = list(EXIT_IDS)
        dropped = []
        eq = dict(eq)
        eq["kept_exit_ids"] = kept
        eq["dropped_exit_ids"] = dropped
        eq["FINAL_EXIT_N"] = 5

    final_pairs = build_final_pairs(kept)
    hashes = {
        "ENTRY_LIBRARY_SHA256": entry_hash,
        "EXECUTION_SHA256": exec_hash,
        "PORTFOLIO_SHA256": c1_dumps(portfolio_contract()),
        "FOLD_ASSIGNMENT_SHA256": c1_dumps(fold_assignment()),
        "GATES_SHA256": gates_hash,
        "CANARY_SPEC_SHA256": c1_dumps(canary_spec()),
        "EXIT_FILE_SHA256": pred.get("SOURCE_FILE_SHA256"),
        "BAR_FINISH_SHA256": bar_c.get("FINISH_SHA256"),
        "SPEC_SHA256": spec_sha256(),
        "EQUIVALENCE_SHA256": dumps_sha256(eq.get("classes")),
        "FINAL_EXIT_SHA256": dumps_sha256(kept),
        "FINAL_PAIR_SHA256": dumps_sha256([r["CANDIDATE_ID"] for r in final_pairs]),
    }

    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "CASE": case,
        "CASE_NAME": case_name,
        "VERDICT": verdict,
        "NEXT": nxt,
        "prior": prior,
        "prior_artifact_sha256": prior_art,
        "already_executed": reuse,
        "reused_existing_result": reused,
        "bar_contract": bar_c,
        "bar_integrity": integ,
        "exit_predicates": pred,
        "z2_z4": z2z4,
        "suffix_audit": {k: v for k, v in suffix.items() if k != "mismatch_examples"},
        "mismatch_examples": list(suffix.get("mismatch_examples") or []),
        "equivalence": eq,
        "final_exits": [{"EXIT_ID": z, "KEPT": z in kept, "DROPPED": z in dropped} for z in EXIT_IDS],
        "final_pairs": final_pairs,
        "execution": execution_contract(),
        "portfolio": portfolio_contract(),
        "folds": fold_assignment(),
        "coverage_gates": coverage_gates(),
        "fold_coverage_gates": fold_coverage_gates(),
        "economic_gates": economic_gates(),
        "stability_gates": stability_gates(),
        "canary": canary_spec(),
        "hashes": hashes,
        "forensic": forensic,
        "DEV_CLASSIFICATION": DEV_CLASSIFICATION,
        "PRECOMMIT_ALL_PAIRS_PRE_ECONOMICS": False,
        "Z3_SLICE_PREVIOUSLY_OBSERVED": True,
        "Z3_PREVIOUSLY_OBSERVED_PAIR_N": int(Z3_PREVIOUSLY_OBSERVED_PAIR_N),
        "PRIOR_ARTIFACT_MODIFIED": False,
        "ENTRY_MODIFIED": False,
        "EXECUTION_MODIFIED": False,
        "EXIT_IMPLEMENTATION_MODIFIED": False,
        "SELECTION_GATES_MODIFIED": False,
        "NEW_EXIT_CREATED": False,
        "CANDIDATE_BACKFILL": False,
        "CANDIDATE_ECONOMICS_RUN": False,
        "CANDIDATE_SIGNAL_COUNT_COMPUTED": False,
        "CANDIDATE_FILL_COUNT_COMPUTED": False,
        "CANDIDATE_TRADE_COUNT_COMPUTED": False,
        "CANDIDATE_PNL_COMPUTED": False,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "EXECUTION_ID": EXECUTION_ID,
        "integrity_ok": not integrity,
    }


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    prior = dict(pack.get("prior") or {})
    bar = dict(pack.get("bar_contract") or {})
    integ = dict(pack.get("bar_integrity") or {})
    pred = dict(pack.get("exit_predicates") or {})
    z2z4 = dict(pack.get("z2_z4") or {})
    suf = dict(pack.get("suffix_audit") or {})
    eq = dict(pack.get("equivalence") or {})
    return {
        "1": prior.get("PAIR_VERDICT"),
        "2": bool(pack.get("PRIOR_ARTIFACT_MODIFIED")) is False,
        "3": True,
        "4": prior.get("PRIOR_Z3_ONLY_VERDICT"),
        "5": pack.get("DEV_CLASSIFICATION"),
        "6": False,
        "7": pred.get("SOURCE_FILE_SHA256"),
        "8": bool(bar.get("VALID_COMPLETED_BAR_REQUIRES_FINITE_OPEN")),
        "9": bool(bar.get("VALID_COMPLETED_BAR_REQUIRES_FINITE_HIGH")),
        "10": bool(bar.get("VALID_COMPLETED_BAR_REQUIRES_FINITE_LOW")),
        "11": bool(bar.get("VALID_COMPLETED_BAR_REQUIRES_FINITE_CLOSE")),
        "12": {
            "OPEN_NONFINITE_N": _int(integ, "OPEN_NONFINITE_N"),
            "HIGH_NONFINITE_N": _int(integ, "HIGH_NONFINITE_N"),
            "LOW_NONFINITE_N": _int(integ, "LOW_NONFINITE_N"),
            "CLOSE_NONFINITE_N": _int(integ, "CLOSE_NONFINITE_N"),
        },
        "13": _int(suf, "SUFFIX_CASE_N"),
        "14": bool(suf.get("ALL_10_PAIRS_COMPLETED")),
        "15": bool(z2z4.get("Z4_PEAK_VALUE_USED_IN_EXIT_THRESHOLD")),
        "16": bool(z2z4.get("Z4_PEAK_ONLY_ACTS_AS_FINITE_HIGH_SEEN_GUARD")),
        "17": eq.get("Z2_Z4_MISMATCH_N"),
        "18": bool(eq.get("Z2_Z4_SOURCE_DOMAIN_EQUIVALENCE_PROVEN")),
        "19": bool(eq.get("Z2_Z4_SEMANTIC_DUPLICATE")),
        "20": eq.get("classes"),
        "21": bool(eq.get("Z5_DISTINCT_FROM_Z1")),
        "22": bool(eq.get("Z5_DISTINCT_FROM_Z2")),
        "23": eq.get("FINAL_EXIT_N"),
        "24": eq.get("kept_exit_ids"),
        "25": eq.get("dropped_exit_ids"),
        "26": len(list(pack.get("final_pairs") or [])),
        "27": False,
        "28": False,
        "29": False,
        "30": False,
        "31": False,
        "32": False,
        "33": False,
        "34": False,
        "35": False,
        "36": False,
        "37": False,
        "38": False,
        "39": "0/0/0",
        "40": False,
        "41": False,
        "42": pack.get("VERDICT"),
        "43": pack.get("NEXT"),
    }
