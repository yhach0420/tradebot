"""Pin parent CASE C. No economics. No MBO. No future dates."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.opening_logic_input_capability_audit_v1 import (
    ANALYSIS_ID,
    PARENT_ID,
    REQUIRED_PARENT_VERDICT,
)
from research.opening_logic_input_capability_audit_v1.isolation import RESEARCH_ROOT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "scan.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)

PARENT_REPORT = RESEARCH_ROOT / "existing_data_entry_aligned_full_causal_logic_completion_v1" / "report.json"


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        p = root / name
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def pin_parent() -> dict[str, Any]:
    prev = _load(PARENT_REPORT)
    d = dict(prev.get("decision") or {})
    a = dict(prev.get("answers") or {})
    verdict = str(d.get("VERDICT") or a.get("66_VERDICT") or "")
    logic = d.get("LOGIC_COMPLETE")
    if logic is None:
        logic = a.get("51_LOGIC_COMPLETE")
    robust = d.get("ROBUST_DEV_QUALIFIED")
    if robust is None:
        robust = a.get("52_ROBUST_DEV_QUALIFIED")
    ok = (
        str(prev.get("ANALYSIS_ID") or "") == PARENT_ID
        and verdict == REQUIRED_PARENT_VERDICT
        and logic is False
        and robust is False
    )
    return {
        "ok": bool(ok),
        "PARENT_ID": PARENT_ID,
        "PARENT_ANALYSIS_ID": prev.get("ANALYSIS_ID"),
        "VERDICT": verdict,
        "NEXT": d.get("NEXT") or a.get("67_NEXT"),
        "LOGIC_COMPLETE": bool(logic),
        "ROBUST_DEV_QUALIFIED": bool(robust),
        "ARCHITECTURE_CLOSED": True,
        "DO_NOT_RETUNE": True,
        "DO_NOT_REUSE_EXIT_FAMILY": True,
        "CURRENT_RESEARCH_LINE": "OPENING_DISLOCATION_EXPECTATION_RESOLUTION",
    }


def already_executed_check() -> dict[str, Any]:
    from research.opening_logic_input_capability_audit_v1.isolation import OUT

    path = OUT / "report.json"
    if not path.is_file():
        return {"REUSED_EXISTING_RESULT": False}
    prev = json.loads(path.read_text(encoding="utf-8"))
    if str(prev.get("ANALYSIS_ID") or "") != ANALYSIS_ID:
        return {"REUSED_EXISTING_RESULT": False}
    if dict(prev.get("decision") or {}).get("VERDICT"):
        return {"REUSED_EXISTING_RESULT": True, "prior_report": prev}
    return {"REUSED_EXISTING_RESULT": False}


assert dumps_sha256
assert PARENT_REPORT
