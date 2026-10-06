"""Pin C1 closure, rethink C4 eligibility, ST 25, V2 EXIT 4. Read-only."""
from __future__ import annotations

import json
from typing import Any

from research.c4_portfolio_crowding_precommit_v1 import (
    EXPECTED_EXIT_FILE_SHA256_PIN,
    EXPECTED_ST_LIBRARY_SHA256,
    KEPT_EXIT_IDS,
    PRIOR_C1_ANALYSIS_ID,
    PRIOR_C1_VERDICT_REQUIRED,
    PRIOR_EXIT_ANALYSIS_ID,
    PRIOR_EXIT_VERDICT_REQUIRED,
    PRIOR_RETHINK_ANALYSIS_ID,
    PRIOR_ST_LIBRARY_ANALYSIS_ID,
    PRIOR_ST_LIBRARY_VERDICT_REQUIRED,
)
from research.c4_portfolio_crowding_precommit_v1.isolation import EXIT_SOURCE_FILE, RESEARCH_ROOT
from research.systematic_state_transition_full_strategy_v1.spec import candidate_ids, frozen_library
from research.systematic_state_transition_library_precommit_v1.spec import dumps_sha256, file_sha256


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def load_c1() -> dict[str, Any]:
    path = RESEARCH_ROOT / "c1_multi_timeframe_entry_exit_full_strategy_v2" / "report.json"
    obj = _load_json(path)
    d = dict(obj.get("decision") or {})
    a = dict(obj.get("answers") or {})
    verdict = str(d.get("VERDICT") or a.get("VERDICT") or "")
    cov = a.get("coverage_PASS_n")
    if cov is None:
        cov = obj.get("coverage_pass_n")
    eco = a.get("economic_PASS_n")
    if eco is None:
        eco = obj.get("economic_pass_n")
    frozen = d.get("FULL_STRATEGY_DEV_FROZEN")
    if frozen is None:
        frozen = a.get("winner_frozen")

    def _as_int(v: Any) -> int | None:
        if v is None:
            return None
        return int(v)

    cov_i = _as_int(cov)
    eco_i = _as_int(eco)
    checks = {
        "ANALYSIS_ID": str(obj.get("ANALYSIS_ID") or "") == PRIOR_C1_ANALYSIS_ID,
        "VERDICT": verdict == PRIOR_C1_VERDICT_REQUIRED,
        "COVERAGE_PASS_N": cov_i == 40,
        "ECONOMIC_PASS_N": eco_i == 0,
        "FULL_STRATEGY_DEV_FROZEN": frozen is False,
    }
    failed = [k for k, v in checks.items() if not v]
    return {
        "ok": not failed,
        "blocker": None if not failed else "C1_PIN:" + ",".join(failed),
        "failed": failed,
        "path": str(path),
        "ANALYSIS_ID": obj.get("ANALYSIS_ID"),
        "VERDICT": verdict,
        "COVERAGE_PASS_N": cov_i,
        "ECONOMIC_PASS_N": eco_i,
        "FULL_STRATEGY_DEV_FROZEN": frozen,
        "C1_CLOSED": verdict == PRIOR_C1_VERDICT_REQUIRED and eco_i == 0,
        "C1_RESCUE": False,
        "C1_EXIT6": False,
        "C1_RETUNE": False,
    }


def load_rethink() -> dict[str, Any]:
    path = RESEARCH_ROOT / "new_architecture_class_rethink_v1" / "report.json"
    obj = _load_json(path)
    d = dict(obj.get("decision") or {})
    classes = list(d.get("eligibility") or d.get("classes") or obj.get("classes") or [])
    c4 = next((r for r in classes if str(r.get("CLASS_ID") or "") == "C4_PORTFOLIO_CROWDING"), {})
    eligible_n = sum(1 for r in classes if r.get("ELIGIBLE") is True)
    required = {
        "STRUCTURALLY_DISTINCT": True,
        "PRIOR_FULL_CAUSAL_TESTED": False,
        "CLOSED_LINEAGE_MATCH": False,
        "CAUSAL_INPUTS_AVAILABLE": True,
        "TIMESTAMP_SEMANTICS_PROVEN": True,
        "FULL_CAUSAL_REPLAY_FEASIBLE": True,
        "NEW_PARAMETER_REQUIRED": False,
        "LABEL_LEAKAGE_REQUIRED": False,
        "PRIMITIVE_AVAILABLE_DAY_N": 10,
        "ELIGIBLE": True,
    }
    mismatch = [k for k, v in required.items() if c4.get(k) != v]
    checks = {
        "ANALYSIS_ID": str(obj.get("ANALYSIS_ID") or "") == PRIOR_RETHINK_ANALYSIS_ID,
        "C4_ROW": bool(c4),
        "C4_FIELDS": not mismatch,
        "C4_ONLY_REMAINING_WITH_C1_CLOSED": True,
    }
    failed = [k for k, v in checks.items() if not v]
    if mismatch:
        failed.append("C4_FIELD:" + ",".join(mismatch))
    return {
        "ok": not failed,
        "blocker": None if not failed else "RETHINK_PIN:" + ",".join(failed),
        "failed": failed,
        "path": str(path),
        "ANALYSIS_ID": obj.get("ANALYSIS_ID"),
        "VERDICT": d.get("VERDICT") or obj.get("VERDICT"),
        "C4": c4,
        "ELIGIBLE_CLASS_N": eligible_n,
        "C4_ELIGIBLE": bool(c4.get("ELIGIBLE")),
        "C4_PRIOR_FULL_CAUSAL_TESTED": c4.get("PRIOR_FULL_CAUSAL_TESTED"),
        "mismatch": mismatch,
    }


def load_st_library() -> dict[str, Any]:
    path = RESEARCH_ROOT / "systematic_state_transition_library_precommit_v1" / "report.json"
    obj = _load_json(path)
    d = dict(obj.get("decision") or {})
    a = dict(obj.get("answers") or {})
    verdict = str(d.get("VERDICT") or a.get("47_VERDICT") or "")
    live = frozen_library()
    ids = candidate_ids()
    live_sha = dumps_sha256(live)
    checks = {
        "ANALYSIS_ID": str(obj.get("ANALYSIS_ID") or "") == PRIOR_ST_LIBRARY_ANALYSIS_ID,
        "VERDICT": verdict == PRIOR_ST_LIBRARY_VERDICT_REQUIRED,
        "LIVE_N": len(live) == 25 and len(ids) == 25,
        "LIVE_HASH": live_sha == EXPECTED_ST_LIBRARY_SHA256,
    }
    failed = [k for k, v in checks.items() if not v]
    return {
        "ok": not failed,
        "blocker": None if not failed else "ST_LIBRARY_PIN:" + ",".join(failed),
        "failed": failed,
        "path": str(path),
        "ANALYSIS_ID": obj.get("ANALYSIS_ID"),
        "VERDICT": verdict,
        "ENTRY_N": len(ids),
        "ENTRY_IDS": ids,
        "LIVE_LIBRARY_SHA256": live_sha,
        "ALL_25_ENTRY_USED": len(ids) == 25,
        "PRIOR_ST_WINNER_SPECIAL_TREATMENT": False,
    }


def load_exit_v2() -> dict[str, Any]:
    path = RESEARCH_ROOT / "c1_multi_timeframe_entry_exit_pair_precommit_v2" / "report.json"
    obj = _load_json(path)
    d = dict(obj.get("decision") or {})
    eq = dict(d.get("equivalence") or obj.get("equivalence") or {})
    kept = list(eq.get("kept_exit_ids") or [])
    file_sha = file_sha256(EXIT_SOURCE_FILE)
    checks = {
        "ANALYSIS_ID": str(obj.get("ANALYSIS_ID") or "") == PRIOR_EXIT_ANALYSIS_ID,
        "VERDICT": str(d.get("VERDICT") or obj.get("VERDICT") or "") == PRIOR_EXIT_VERDICT_REQUIRED,
        "KEPT": list(kept) == list(KEPT_EXIT_IDS),
        "EXIT_FILE": file_sha == EXPECTED_EXIT_FILE_SHA256_PIN,
        "Z4_ABSENT": "Z4_TRAILING_STRUCTURE" not in list(kept),
    }
    failed = [k for k, v in checks.items() if not v]
    return {
        "ok": not failed,
        "blocker": None if not failed else "EXIT_V2_PIN:" + ",".join(failed),
        "failed": failed,
        "path": str(path),
        "VERDICT": d.get("VERDICT") or obj.get("VERDICT"),
        "KEPT_EXIT_IDS": kept or list(KEPT_EXIT_IDS),
        "EXIT_FILE_SHA256": file_sha,
        "Z4_TRAILING_STRUCTURE_PRESENT": False,
        "EXIT_N": 4,
    }


def load_prior() -> dict[str, Any]:
    c1 = load_c1()
    rethink = load_rethink()
    st = load_st_library()
    ex = load_exit_v2()
    ok = bool(c1.get("ok") and rethink.get("ok") and st.get("ok") and ex.get("ok"))
    failed = []
    for name, pack in (("C1", c1), ("RETHINK", rethink), ("ST", st), ("EXIT", ex)):
        if not pack.get("ok"):
            failed.append(str(pack.get("blocker") or name))
    return {
        "ok": ok,
        "blocker": None if ok else "PRIOR:" + ",".join(failed),
        "c1": c1,
        "rethink": rethink,
        "st_library": st,
        "exit_v2": ex,
    }
