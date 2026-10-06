"""Pin parent expansion. Hash helpers. Already-executed check on frozen SHAs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.causal_mechanism_representation_expansion_v1.library import candidate_library
from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.full_causal_mechanism_discovery_v1 import (
    ANALYSIS_ID,
    CANDIDATE_MECHANISM_N,
    O1_N,
    O2_N,
    O3_N,
    PARENT_ID,
    PIN_P2_DEV_TOTAL_PNL,
    PIN_P2_EXECUTED_H5_MEAN,
    PIN_P2_RAW_H5_MEAN,
    PIN_P2_RAW_SIGNAL_N,
    PIN_P2_TRADE_N,
    PIN_R2_DEV_TOTAL_PNL,
    PIN_R2_EXECUTED_H5_MEAN,
    PIN_R2_RAW_H5_MEAN,
    PIN_R2_RAW_SIGNAL_N,
    PIN_R2_TRADE_N,
    REQUIRED_LIBRARY_SHA256,
    REQUIRED_PARENT_VERDICT,
)
from research.full_causal_mechanism_discovery_v1.isolation import RESEARCH_ROOT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "mapping.py",
    "candidates.py",
    "exits.py",
    "harvest.py",
    "integrity.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)

PARENT_REPORT = RESEARCH_ROOT / "causal_mechanism_representation_expansion_v1" / "report.json"


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def library_sha256() -> str:
    lib = candidate_library()
    return dumps_sha256(
        [{"MECHANISM_ID": r["MECHANISM_ID"], "TEMPLATE": r["TEMPLATE"], "DEFINITION": r["DEFINITION"]} for r in lib]
    )


def pin_parent() -> dict[str, Any]:
    prev = _load(PARENT_REPORT)
    d = dict(prev.get("decision") or {})
    gate = dict(prev.get("gate") or {})
    p1 = dict(prev.get("P1") or {})
    p2 = dict(prev.get("P2") or {})
    lib = dict(prev.get("library_freeze") or {})
    verdict = str(d.get("VERDICT") or prev.get("VERDICT") or "")
    lib_sha = str(lib.get("LIBRARY_SHA256") or d.get("LIBRARY_SHA256") or "")
    r2_raw_n = int(p1.get("RAW_SIGNAL_N") or -1)
    r2_h5 = p1.get("RAW_SIGNAL_H5_MEAN")
    p2_raw_n = int(p2.get("RAW_SIGNAL_N") or -1)
    p2_h5 = p2.get("RAW_SIGNAL_H5_MEAN")
    unit_valid = gate.get("DISCOVERY_UNIT_VALID")
    if unit_valid is None:
        unit_valid = d.get("DISCOVERY_UNIT_VALID")
    admission = gate.get("PORTFOLIO_ADMISSION_MATTERS")
    if admission is None:
        admission = d.get("PORTFOLIO_ADMISSION_MATTERS")
    new_read = d.get("NEW_MECHANISM_OUTCOMES_READ")
    if new_read is None:
        new_read = False
    computed = library_sha256()
    ok = (
        verdict == REQUIRED_PARENT_VERDICT
        and lib_sha == REQUIRED_LIBRARY_SHA256
        and computed == REQUIRED_LIBRARY_SHA256
        and int(lib.get("CANDIDATE_MECHANISM_N") or -1) == int(CANDIDATE_MECHANISM_N)
        and int(lib.get("O1_N") or -1) == int(O1_N)
        and int(lib.get("O2_N") or -1) == int(O2_N)
        and int(lib.get("O3_N") or -1) == int(O3_N)
        and r2_raw_n == int(PIN_R2_RAW_SIGNAL_N)
        and r2_h5 is not None
        and abs(float(r2_h5) - float(PIN_R2_RAW_H5_MEAN)) < 1e-12
        and p2_raw_n == int(PIN_P2_RAW_SIGNAL_N)
        and p2_h5 is not None
        and abs(float(p2_h5) - float(PIN_P2_RAW_H5_MEAN)) < 1e-12
        and unit_valid is False
        and admission is True
        and new_read is False
    )
    return {
        "ok": bool(ok),
        "PARENT_ID": PARENT_ID,
        "REQUIRED_PARENT_VERDICT": REQUIRED_PARENT_VERDICT,
        "observed_verdict": verdict,
        "DISCOVERY_UNIT_VALID": bool(unit_valid) if unit_valid is not None else None,
        "PORTFOLIO_ADMISSION_MATTERS": bool(admission) if admission is not None else None,
        "NEW_MECHANISM_OUTCOMES_PREVIOUSLY_READ": bool(new_read),
        "LIBRARY_SHA256": computed,
        "PARENT_LIBRARY_SHA256": lib_sha,
        "R2_RAW_SIGNAL_N": r2_raw_n,
        "R2_RAW_H5_MEAN": None if r2_h5 is None else float(r2_h5),
        "R2_TRADE_N": PIN_R2_TRADE_N,
        "R2_EXECUTED_H5_MEAN": PIN_R2_EXECUTED_H5_MEAN,
        "R2_DEV_TOTAL_PNL": PIN_R2_DEV_TOTAL_PNL,
        "P2_ID": p2.get("CONTROL_ID"),
        "P2_RAW_SIGNAL_N": p2_raw_n,
        "P2_RAW_H5_MEAN": None if p2_h5 is None else float(p2_h5),
        "P2_TRADE_N": PIN_P2_TRADE_N,
        "P2_EXECUTED_H5_MEAN": PIN_P2_EXECUTED_H5_MEAN,
        "P2_DEV_TOTAL_PNL": PIN_P2_DEV_TOTAL_PNL,
        "ADMISSION_COMPONENT_CAUSAL_ATTRIBUTION_CLAIMED": False,
        "CAP_ALONE_CAUSES_EDGE": False,
        "ANALYSIS_ID": ANALYSIS_ID,
    }


def already_executed_check(
    *,
    library_sha: str,
    mapping_sha: str,
    candidate_set_sha: str,
) -> dict[str, Any]:
    from research.full_causal_mechanism_discovery_v1.isolation import OUT

    path = OUT / "report.json"
    if not path.is_file():
        return {"REUSED_EXISTING_RESULT": False}
    prev = json.loads(path.read_text(encoding="utf-8"))
    hashes = dict(prev.get("hashes") or prev.get("pin") or {})
    same = (
        str(hashes.get("MECHANISM_LIBRARY_SHA256") or prev.get("MECHANISM_LIBRARY_SHA256") or "") == str(library_sha)
        and str(hashes.get("FULL_STRATEGY_MAPPING_SHA256") or "") == str(mapping_sha)
        and str(hashes.get("FULL_CAUSAL_CANDIDATE_SET_SHA256") or "") == str(candidate_set_sha)
        and bool(prev.get("decision"))
    )
    if same:
        return {"REUSED_EXISTING_RESULT": True, "prior_report": prev}
    return {"REUSED_EXISTING_RESULT": False}
