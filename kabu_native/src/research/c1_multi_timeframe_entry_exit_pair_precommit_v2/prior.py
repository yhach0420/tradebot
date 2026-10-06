"""Load prior pair-precommit and Z3-only slice read-only. Never write those artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.c1_multi_timeframe_entry_exit_pair_precommit_v2 import (
    PRIOR_PAIR_ANALYSIS_ID,
    PRIOR_PAIR_VERDICT_REQUIRED,
    PRIOR_Z3_ONLY_ANALYSIS_ID,
    PRIOR_Z3_ONLY_VERDICT_REQUIRED,
    RAW_CANDIDATE_IDS,
    Z3_PREVIOUSLY_OBSERVED_PAIR_N,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.isolation import RESEARCH_ROOT

PRIOR_OUT_NAMES = (
    "c1_multi_timeframe_precommit_v1",
    "c1_multi_timeframe_entry_exit_pair_precommit_v1",
)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prior_artifact_paths() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for name in PRIOR_OUT_NAMES:
        root = RESEARCH_ROOT / name
        for fn in ("report.json", "report.md", "audit.xlsx"):
            out[f"{name}/{fn}"] = root / fn
    return out


def snapshot_prior_artifacts() -> dict[str, str]:
    out = {}
    for key, path in prior_artifact_paths().items():
        out[key] = _file_sha256(path) if path.is_file() else ""
    return out


def artifacts_modified(before: dict[str, str], after: dict[str, str]) -> bool:
    return dict(before) != dict(after)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def load_prior() -> dict[str, Any]:
    pair_path = RESEARCH_ROOT / "c1_multi_timeframe_entry_exit_pair_precommit_v1" / "report.json"
    z3_path = RESEARCH_ROOT / "c1_multi_timeframe_full_strategy_v1" / "report.json"
    pair_obj = _load_json(pair_path)
    z3_obj = _load_json(z3_path)
    pd = dict(pair_obj.get("decision") or {})
    pa = dict(pair_obj.get("answers") or {})
    pair_verdict = str(pd.get("VERDICT") or pa.get("35") or "")
    counts = dict(pd.get("counts") or {})
    try:
        raw_n = int(counts["RAW_STRATEGY_N"]) if "RAW_STRATEGY_N" in counts else None
    except (TypeError, ValueError):
        raw_n = None
    try:
        final_n = int(counts["FINAL_STRATEGY_N"]) if "FINAL_STRATEGY_N" in counts else None
    except (TypeError, ValueError):
        final_n = None
    try:
        exit_n = int(counts["EXIT_N"]) if "EXIT_N" in counts else None
    except (TypeError, ValueError):
        exit_n = None
    entry_ids = [str(r.get("CANDIDATE_ID") or "") for r in list(pd.get("entry_library") or [])]
    zd = dict(z3_obj.get("decision") or {})
    z3_verdict = str(zd.get("VERDICT") or "")
    checks = {
        "PAIR_ANALYSIS_ID": str(pair_obj.get("ANALYSIS_ID") or "") == PRIOR_PAIR_ANALYSIS_ID,
        "PAIR_VERDICT": pair_verdict == PRIOR_PAIR_VERDICT_REQUIRED,
        "RAW_N": raw_n == 50,
        "FINAL_N": final_n == 50,
        "EXIT_N": exit_n == 5,
        "ENTRY_N": entry_ids == list(RAW_CANDIDATE_IDS) or len(entry_ids) == 10,
        "PAIR_ECONOMICS": pd.get("CANDIDATE_ECONOMICS_RUN") is False,
        "Z3_ANALYSIS_ID": str(z3_obj.get("ANALYSIS_ID") or "") == PRIOR_Z3_ONLY_ANALYSIS_ID,
        "Z3_VERDICT": z3_verdict == PRIOR_Z3_ONLY_VERDICT_REQUIRED,
        "ARTIFACTS": all(bool(v) for v in snapshot_prior_artifacts().values()),
    }
    # ENTRY ids: prefer exact 10 frozen IDs when present
    if entry_ids and entry_ids != list(RAW_CANDIDATE_IDS):
        checks["ENTRY_N"] = False
    failed = [k for k, v in checks.items() if not v]
    return {
        "ok": not failed,
        "blocker": None if not failed else "PRIOR_MISMATCH:" + ",".join(failed),
        "failed_checks": failed,
        "PAIR_ANALYSIS_ID": pair_obj.get("ANALYSIS_ID"),
        "PAIR_VERDICT": pair_verdict,
        "RAW_STRATEGY_N": raw_n,
        "FINAL_STRATEGY_N": final_n,
        "EXIT_N": exit_n,
        "ENTRY_IDS": entry_ids,
        "CANDIDATE_ECONOMICS_RUN": pd.get("CANDIDATE_ECONOMICS_RUN"),
        "PRIOR_Z3_ONLY_ANALYSIS_ID": z3_obj.get("ANALYSIS_ID"),
        "PRIOR_Z3_ONLY_VERDICT": z3_verdict,
        "Z3_SLICE_PREVIOUSLY_OBSERVED": True,
        "Z3_PREVIOUSLY_OBSERVED_PAIR_N": int(Z3_PREVIOUSLY_OBSERVED_PAIR_N),
        "artifact_sha256": snapshot_prior_artifacts(),
        "path": str(pair_path),
        "z3_path": str(z3_path),
    }
