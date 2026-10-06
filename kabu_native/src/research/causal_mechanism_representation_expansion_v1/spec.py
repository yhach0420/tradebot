"""Pin discovery + reassessment parents. Source identity hash. No Full Strategy freeze."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.causal_mechanism_representation_expansion_v1 import (
    REQUIRED_DISCOVERY_VERDICT,
    REQUIRED_PARENT_CANDIDATE_MECHANISM_N,
    REQUIRED_PARENT_COVERAGE_QUALIFIED_N,
    REQUIRED_PARENT_PASS_D1_D11_N,
    REQUIRED_RAW_PREDICATE_N,
    REQUIRED_REASSESS_VERDICT,
)
from research.causal_mechanism_representation_expansion_v1.isolation import RESEARCH_ROOT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "library.py",
    "operators.py",
    "reconstruct.py",
    "labels.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)

DISCOVERY_REPORT = RESEARCH_ROOT / "profitable_move_mechanism_discovery_v1" / "report.json"
REASSESS_REPORT = RESEARCH_ROOT / "discovery_search_space_reassessment_v1" / "report.json"


def dumps_sha256(obj: Any) -> str:
    body = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


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


def pin_parents() -> dict[str, Any]:
    disc = _load(DISCOVERY_REPORT)
    reas = _load(REASSESS_REPORT)
    dd = dict(disc.get("decision") or {})
    rd = dict(reas.get("decision") or {})
    d_verdict = str(dd.get("VERDICT") or disc.get("VERDICT") or "")
    r_verdict = str(rd.get("VERDICT") or reas.get("VERDICT") or "")
    raw_n = int(disc.get("RAW_PREDICATE_N") or -1)
    cand_n = int(disc.get("CANDIDATE_MECHANISM_N") or -1)
    cov_n = int(disc.get("COVERAGE_QUALIFIED_N") or -1)
    pass_n = int(dd.get("PASS_D1_D11_N") if dd.get("PASS_D1_D11_N") is not None else -1)
    feat_raw = (disc.get("data") or {}).get("FEATURE_LOOKAHEAD_N")
    feat = int(feat_raw) if feat_raw is not None else -1
    frozen = dd.get("FULL_STRATEGY_FROZEN")
    h5_valid = rd.get("FIXED_H5_HARD_GATE_VALID")
    p1 = dict(reas.get("P1") or {})
    p2 = dict(reas.get("P2") or {})
    p1_h5 = (p1.get("executable") or {}).get("H5_mean")
    p2_h5 = (p2.get("executable") or {}).get("H5_mean")
    ok = (
        d_verdict == REQUIRED_DISCOVERY_VERDICT
        and r_verdict == REQUIRED_REASSESS_VERDICT
        and raw_n == int(REQUIRED_RAW_PREDICATE_N)
        and cand_n == int(REQUIRED_PARENT_CANDIDATE_MECHANISM_N)
        and cov_n == int(REQUIRED_PARENT_COVERAGE_QUALIFIED_N)
        and pass_n == int(REQUIRED_PARENT_PASS_D1_D11_N)
        and feat == 0
        and frozen is False
        and h5_valid is True
        and p1.get("CONTROL_EXECUTED_COHORT_AVAILABLE") is True
        and p2.get("CONTROL_EXECUTED_COHORT_AVAILABLE") is True
        and p1_h5 is not None
        and p2_h5 is not None
        and float(p1_h5) > 0.0
        and float(p2_h5) > 0.0
    )
    return {
        "ok": bool(ok),
        "REQUIRED_DISCOVERY_VERDICT": REQUIRED_DISCOVERY_VERDICT,
        "observed_discovery_verdict": d_verdict,
        "REQUIRED_REASSESS_VERDICT": REQUIRED_REASSESS_VERDICT,
        "observed_reassess_verdict": r_verdict,
        "RAW_PREDICATE_N": raw_n,
        "CANDIDATE_MECHANISM_N": cand_n,
        "COVERAGE_QUALIFIED_N": cov_n,
        "PASS_D1_D11_N": pass_n,
        "FEATURE_LOOKAHEAD_N": feat,
        "FULL_STRATEGY_FROZEN": frozen,
        "FIXED_H5_HARD_GATE_VALID": h5_valid,
        "P1_EXECUTED_H5_MEAN": None if p1_h5 is None else float(p1_h5),
        "P2_EXECUTED_H5_MEAN": None if p2_h5 is None else float(p2_h5),
        "P1_TRADE_N": p1.get("TRADE_N"),
        "P2_TRADE_N": p2.get("TRADE_N"),
        "P1_ACTUAL_FULL_STRATEGY_TOTAL_PNL": p1.get("ACTUAL_FULL_STRATEGY_TOTAL_PNL"),
        "P2_ACTUAL_FULL_STRATEGY_TOTAL_PNL": p2.get("ACTUAL_FULL_STRATEGY_TOTAL_PNL"),
    }
