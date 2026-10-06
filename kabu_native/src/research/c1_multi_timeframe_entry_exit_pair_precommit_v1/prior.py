"""Load C1_MULTI_TIMEFRAME_PRECOMMIT_V1 read-only. Never write prior artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.c1_multi_timeframe_entry_exit_pair_precommit_v1 import (
    PRIOR_ANALYSIS_ID,
    PRIOR_VERDICT_REQUIRED,
    PRIOR_Z3_ONLY_ANALYSIS_ID,
    PRIOR_Z3_ONLY_NEXT,
    RAW_CANDIDATE_IDS,
    REQUIRED_PRIOR_HASHES,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.isolation import RESEARCH_ROOT
from research.c1_multi_timeframe_precommit_v1.library import build_raw_library
from research.c1_multi_timeframe_precommit_v1.spec import (
    canary_spec,
    coverage_gates,
    dumps_sha256,
    economic_gates,
    execution_contract,
    fold_assignment,
    fold_coverage_gates,
    portfolio_contract,
    stability_gates,
)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prior_artifact_paths() -> dict[str, Path]:
    root = RESEARCH_ROOT / "c1_multi_timeframe_precommit_v1"
    return {
        "report.json": root / "report.json",
        "report.md": root / "report.md",
        "audit.xlsx": root / "audit.xlsx",
    }


def snapshot_prior_artifacts() -> dict[str, str]:
    out = {}
    for name, path in prior_artifact_paths().items():
        if not path.is_file():
            out[name] = ""
            continue
        out[name] = _file_sha256(path)
    return out


def artifacts_modified(before: dict[str, str], after: dict[str, str]) -> bool:
    return dict(before) != dict(after)


def load_prior() -> dict[str, Any]:
    path = RESEARCH_ROOT / "c1_multi_timeframe_precommit_v1" / "report.json"
    z3_path = RESEARCH_ROOT / "c1_multi_timeframe_full_strategy_v1" / "report.json"
    if not path.is_file():
        return {"ok": False, "blocker": "PRIOR_REPORT_MISSING", "path": str(path)}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "blocker": f"PRIOR_REPORT_UNREADABLE:{type(exc).__name__}", "path": str(path)}
    if not isinstance(obj, dict):
        return {"ok": False, "blocker": "PRIOR_REPORT_NOT_OBJECT", "path": str(path)}
    d = dict(obj.get("decision") or {})
    a = dict(obj.get("answers") or {})
    verdict = str(d.get("VERDICT") or a.get("63_VERDICT") or "")
    hashes = dict(d.get("hashes") or {})
    final = list(d.get("final_library") or [])
    final_ids = [str(r.get("CANDIDATE_ID") or "") for r in final]
    counts = dict(d.get("counts") or {})
    try:
        final_n = int(counts["FINAL_CANDIDATE_N"]) if "FINAL_CANDIDATE_N" in counts else None
    except (TypeError, ValueError):
        final_n = None
    try:
        raw_n = int(counts["RAW_CANDIDATE_N"]) if "RAW_CANDIDATE_N" in counts else None
    except (TypeError, ValueError):
        raw_n = None

    recomputed_entry = dumps_sha256(build_raw_library())
    live_hashes = {
        "EXECUTION_SHA256": dumps_sha256(execution_contract()),
        "PORTFOLIO_SHA256": dumps_sha256(portfolio_contract()),
        "FOLD_ASSIGNMENT_SHA256": dumps_sha256(fold_assignment()),
        "GATES_SHA256": dumps_sha256(
            {
                "coverage": coverage_gates(),
                "fold_coverage": fold_coverage_gates(),
                "economic": economic_gates(),
                "stability": stability_gates(),
            }
        ),
        "CANARY_SPEC_SHA256": dumps_sha256(canary_spec()),
        "RAW_CANDIDATE_LIBRARY_SHA256": recomputed_entry,
        "FINAL_CANDIDATE_LIBRARY_SHA256": recomputed_entry,
    }
    hash_fail = []
    for k, expected in REQUIRED_PRIOR_HASHES.items():
        got = str(hashes.get(k) or "")
        if got != expected:
            hash_fail.append(f"REPORT_{k}")
        if k in live_hashes and live_hashes[k] != expected:
            hash_fail.append(f"LIVE_{k}")

    artifact_sha = snapshot_prior_artifacts()
    missing_art = [k for k, v in artifact_sha.items() if not v]

    z3_next = ""
    z3_verdict = ""
    if z3_path.is_file():
        try:
            z3_obj = json.loads(z3_path.read_text(encoding="utf-8"))
            z3_d = dict(z3_obj.get("decision") or {})
            z3_verdict = str(z3_d.get("VERDICT") or "")
            z3_next = str(z3_d.get("NEXT") or "")
        except Exception:
            z3_verdict = "UNREADABLE"

    checks = {
        "ANALYSIS_ID": str(obj.get("ANALYSIS_ID") or d.get("ANALYSIS_ID") or "") == PRIOR_ANALYSIS_ID,
        "VERDICT": verdict == PRIOR_VERDICT_REQUIRED,
        "FINAL_N": final_n == 10,
        "RAW_N": raw_n == 10,
        "ENTRY_IDS": final_ids == list(RAW_CANDIDATE_IDS),
        "HASHES": not hash_fail,
        "ARTIFACTS": not missing_art,
        "ECONOMICS_IN_PRIOR_PRECOMMIT": d.get("CANDIDATE_ECONOMICS_RUN") is False,
    }
    failed = [k for k, v in checks.items() if not v]
    ok = not failed
    return {
        "ok": bool(ok),
        "blocker": None if ok else "PRIOR_C1_MISMATCH:" + ",".join(failed),
        "failed_checks": failed,
        "hash_fail": hash_fail,
        "path": str(path),
        "ANALYSIS_ID": obj.get("ANALYSIS_ID"),
        "VERDICT": verdict,
        "NEXT_RECORDED": d.get("NEXT"),
        "FINAL_CANDIDATE_N": final_n,
        "RAW_CANDIDATE_N": raw_n,
        "ENTRY_IDS": final_ids,
        "hashes": hashes,
        "live_hashes": live_hashes,
        "artifact_sha256": artifact_sha,
        "ENTRY_LIBRARY_HASH_UNCHANGED": recomputed_entry == REQUIRED_PRIOR_HASHES["FINAL_CANDIDATE_LIBRARY_SHA256"],
        "PRIOR_Z3_ONLY_ANALYSIS_ID": PRIOR_Z3_ONLY_ANALYSIS_ID,
        "PRIOR_Z3_ONLY_VERDICT": z3_verdict,
        "PRIOR_Z3_ONLY_NEXT_RECORDED": z3_next,
        "PRIOR_Z3_ONLY_NEXT_SUPERSEDED": True,
        "PRIOR_Z3_ONLY_NEXT_WAS": PRIOR_Z3_ONLY_NEXT,
        "CANDIDATE_ECONOMICS_RUN": d.get("CANDIDATE_ECONOMICS_RUN"),
    }
