"""Pin V2 pair-precommit freeze. Mismatch => CASE E. Do not reopen V1 Z3 pair metrics."""
from __future__ import annotations

import json
from typing import Any

from research.c1_multi_timeframe_entry_exit_full_strategy_v2 import (
    DEV_CLASSIFICATION,
    DROPPED_EXIT_IDS,
    FROZEN_PAIRS,
    KEPT_EXIT_IDS,
    PRECOMMIT_ANALYSIS_ID,
    PRECOMMIT_NEXT_REQUIRED,
    PRECOMMIT_VERDICT_REQUIRED,
    PRIOR_Z3_ONLY_ANALYSIS_ID,
    PRIOR_Z3_ONLY_VERDICT,
    REQUIRED_V2_HASHES,
    Z3_PREVIOUSLY_OBSERVED_PAIR_N,
)
from research.c1_multi_timeframe_entry_exit_full_strategy_v2.isolation import EXIT_SOURCE_FILE, RESEARCH_ROOT
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.spec import (
    canary_spec,
    coverage_gates,
    dumps_sha256 as pair_dumps,
    economic_gates,
    execution_contract,
    fold_assignment,
    fold_coverage_gates,
    portfolio_contract,
    stability_gates,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.equivalence import build_final_pairs
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.spec import dumps_sha256, file_sha256
from research.c1_multi_timeframe_precommit_v1.library import build_raw_library


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def live_required_hashes() -> dict[str, str]:
    entries = build_raw_library()
    gates_obj = {
        "coverage": coverage_gates(),
        "fold_coverage": fold_coverage_gates(),
        "economic": economic_gates(),
        "stability": stability_gates(),
    }
    kept = list(KEPT_EXIT_IDS)
    pairs = build_final_pairs(kept)
    return {
        "ENTRY_LIBRARY_SHA256": dumps_sha256(entries),
        "EXECUTION_SHA256": pair_dumps(execution_contract()),
        "PORTFOLIO_SHA256": pair_dumps(portfolio_contract()),
        "FOLD_ASSIGNMENT_SHA256": pair_dumps(fold_assignment()),
        "GATES_SHA256": pair_dumps(gates_obj),
        "CANARY_SPEC_SHA256": pair_dumps(canary_spec()),
        "EXIT_FILE_SHA256": file_sha256(EXIT_SOURCE_FILE),
        "FINAL_EXIT_SHA256": dumps_sha256(kept),
        "FINAL_PAIR_SHA256": dumps_sha256([r["CANDIDATE_ID"] for r in pairs]),
    }


def load_precommit_report() -> dict[str, Any]:
    v2_path = RESEARCH_ROOT / "c1_multi_timeframe_entry_exit_pair_precommit_v2" / "report.json"
    z3_path = RESEARCH_ROOT / "c1_multi_timeframe_full_strategy_v1" / "report.json"
    obj = _load_json(v2_path)
    z3 = _load_json(z3_path)
    d = dict(obj.get("decision") or {})
    a = dict(obj.get("answers") or {})
    hashes = dict(d.get("hashes") or obj.get("hashes") or {})
    verdict = str(d.get("VERDICT") or obj.get("VERDICT") or "")
    nxt = str(d.get("NEXT") or obj.get("NEXT") or "")
    dev = str(d.get("DEV_CLASSIFICATION") or obj.get("DEV_CLASSIFICATION") or "")
    eq = dict(d.get("equivalence") or obj.get("equivalence") or {})
    kept = list(eq.get("kept_exit_ids") or [])
    dropped = list(eq.get("dropped_exit_ids") or [])
    final_pairs = list(d.get("final_pairs") or obj.get("final_pairs") or [])
    final_exit_n = int(eq.get("FINAL_EXIT_N") or len(kept) or -1)
    final_pair_n = len(final_pairs) if final_pairs else int(a.get("25") or -1)
    z3d = dict(z3.get("decision") or {})
    z3_verdict = str(z3d.get("VERDICT") or z3.get("VERDICT") or "")
    report_mismatch = [k for k, v in REQUIRED_V2_HASHES.items() if str(hashes.get(k) or "") != str(v)]
    live = live_required_hashes()
    live_mismatch = [k for k, v in live.items() if str(live.get(k) or "") != str(REQUIRED_V2_HASHES.get(k) or "")]
    eq_sha = str(hashes.get("EQUIVALENCE_SHA256") or "")
    eq_live = dumps_sha256(eq.get("classes")) if eq.get("classes") is not None else ""
    if eq_sha != REQUIRED_V2_HASHES["EQUIVALENCE_SHA256"]:
        report_mismatch.append("EQUIVALENCE_SHA256") if "EQUIVALENCE_SHA256" not in report_mismatch else None
    if eq_live and eq_live != REQUIRED_V2_HASHES["EQUIVALENCE_SHA256"]:
        live_mismatch.append("EQUIVALENCE_SHA256")
    pair_ids = [str(r.get("CANDIDATE_ID") or "") for r in final_pairs]
    z4_n = sum(1 for x in pair_ids if "Z4_TRAILING_STRUCTURE" in x) + sum(1 for x in kept if x == "Z4_TRAILING_STRUCTURE")
    failed = []
    if str(obj.get("ANALYSIS_ID") or "") != PRECOMMIT_ANALYSIS_ID:
        failed.append("ANALYSIS_ID")
    if verdict != PRECOMMIT_VERDICT_REQUIRED:
        failed.append("VERDICT")
    if nxt != PRECOMMIT_NEXT_REQUIRED:
        failed.append("NEXT")
    if dev != DEV_CLASSIFICATION:
        failed.append("DEV_CLASSIFICATION")
    if final_exit_n != 4:
        failed.append("FINAL_EXIT_N")
    if len(FROZEN_PAIRS) != 40:
        failed.append("FINAL_PAIR_N")
    if final_pairs and len(final_pairs) != 40:
        failed.append("REPORT_PAIR_N")
    if kept and list(kept) != list(KEPT_EXIT_IDS):
        failed.append("KEPT_EXITS")
    if dropped and list(dropped) != list(DROPPED_EXIT_IDS):
        failed.append("DROPPED_EXITS")
    if z4_n:
        failed.append("Z4_PRESENT")
    if report_mismatch:
        failed.append("REPORT_HASH:" + ",".join(report_mismatch))
    if live_mismatch:
        failed.append("LIVE_HASH:" + ",".join(live_mismatch))
    if z3_verdict != PRIOR_Z3_ONLY_VERDICT:
        failed.append("PRIOR_Z3_VERDICT")
    if str(z3.get("ANALYSIS_ID") or "") != PRIOR_Z3_ONLY_ANALYSIS_ID:
        failed.append("PRIOR_Z3_ANALYSIS_ID")
    ok = not failed
    return {
        "ok": bool(ok),
        "blocker": None if ok else "V2_PRECOMMIT_IDENTITY_MISMATCH:" + ",".join(failed),
        "failed": failed,
        "path": str(v2_path),
        "z3_path": str(z3_path),
        "ANALYSIS_ID": obj.get("ANALYSIS_ID"),
        "VERDICT": verdict,
        "NEXT": nxt,
        "DEV_CLASSIFICATION": dev,
        "FINAL_EXIT_N": final_exit_n,
        "FINAL_PAIR_N": 40 if len(FROZEN_PAIRS) == 40 else final_pair_n,
        "KEPT_EXIT_IDS": kept or list(KEPT_EXIT_IDS),
        "DROPPED_EXIT_IDS": dropped or list(DROPPED_EXIT_IDS),
        "Z4_TRAILING_STRUCTURE_PRESENT": bool(z4_n),
        "hashes_match": not report_mismatch and not live_mismatch,
        "report_hash_mismatch": report_mismatch,
        "live_hash_mismatch": live_mismatch,
        "report_hashes": hashes,
        "live_hashes": {**live, "EQUIVALENCE_SHA256": eq_live or eq_sha},
        "Z3_SLICE_PREVIOUSLY_OBSERVED": True,
        "Z3_PREVIOUSLY_OBSERVED_PAIR_N": int(Z3_PREVIOUSLY_OBSERVED_PAIR_N),
        "PRIOR_Z3_ONLY_VERDICT": z3_verdict,
        "PRIOR_Z3_METRIC_REUSE_N": 0,
        "PAIR_RERUN_N": 40,
    }
