"""Pin stop parent. Freeze pause protocol. No economics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.research_pause_and_data_requirements_redesign_v1 import (
    ANALYSIS_ID,
    REQUIRED_CLOSED_ARCHITECTURE_N,
    REQUIRED_ELIGIBLE_METHOD_N,
    REQUIRED_METHOD_SPEC_SHA256,
    REQUIRED_PARENT_NEXT,
    REQUIRED_PARENT_VERDICT,
)
from research.research_pause_and_data_requirements_redesign_v1.isolation import RESEARCH_ROOT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "requirements.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)

PARENT_REPORT = RESEARCH_ROOT / "existing_data_strategy_research_stop_reassessment_v1" / "report.json"


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


def pin_parent() -> dict[str, Any]:
    prev = _load(PARENT_REPORT)
    d = dict(prev.get("decision") or {})
    a = dict(prev.get("answers") or {})
    hashes = dict(prev.get("hashes") or {})
    verdict = str(d.get("VERDICT") or a.get("53_VERDICT") or "")
    nxt = str(d.get("NEXT") or a.get("54_NEXT") or "")
    closed_n = int(a.get("10_closed_architecture_family_N") if a.get("10_closed_architecture_family_N") is not None else -1)
    elig_n = int(a.get("29_remaining_eligible_method_N") if a.get("29_remaining_eligible_method_N") is not None else -1)
    sel = a.get("31_selected_method_ID")
    sha = str(hashes.get("REMAINING_RESEARCH_METHOD_SPEC_SHA256") or a.get("41_REMAINING_RESEARCH_METHOD_SPEC_SHA256") or "")
    info_ex = bool(d.get("NEW_INFORMATION_OBJECT_SPACE_EXHAUSTED"))
    arch_ex = bool(d.get("JUSTIFIED_ARCHITECTURE_SPACE_EXHAUSTED"))
    meth_ex = bool(d.get("JUSTIFIED_RESEARCH_METHOD_SPACE_EXHAUSTED"))
    ok = (
        verdict == REQUIRED_PARENT_VERDICT
        and nxt == REQUIRED_PARENT_NEXT
        and closed_n == REQUIRED_CLOSED_ARCHITECTURE_N
        and elig_n == REQUIRED_ELIGIBLE_METHOD_N
        and sel is None
        and sha == REQUIRED_METHOD_SPEC_SHA256
        and info_ex
        and arch_ex
        and meth_ex
        and a.get("36_new_strategy_created") is False
        and a.get("37_new_ENTRY_created") is False
        and a.get("38_new_EXIT_created") is False
        and a.get("40_new_economics_run") is False
    )
    return {
        "ok": bool(ok),
        "PARENT_ID": "EXISTING_DATA_STRATEGY_RESEARCH_STOP_REASSESSMENT_V1",
        "VERDICT": verdict,
        "NEXT": nxt,
        "CLOSED_ARCHITECTURE_FAMILY_N": closed_n,
        "ELIGIBLE_RESEARCH_METHOD_N": elig_n,
        "SELECTED_METHOD_ID": sel,
        "REMAINING_RESEARCH_METHOD_SPEC_SHA256": sha,
        "NEW_INFORMATION_OBJECT_SPACE_EXHAUSTED": info_ex,
        "JUSTIFIED_ARCHITECTURE_SPACE_EXHAUSTED": arch_ex,
        "JUSTIFIED_RESEARCH_METHOD_SPACE_EXHAUSTED": meth_ex,
        "NEW_STRATEGY_CREATED": False,
        "NEW_ENTRY_CREATED": False,
        "NEW_EXIT_CREATED": False,
        "NEW_ECONOMIC_RUN": False,
    }


def freeze_protocol(body: dict[str, Any]) -> dict[str, Any]:
    sha = dumps_sha256(body)
    return {**body, "PAUSE_PROTOCOL_SPEC_SHA256": sha}


def already_executed_check() -> dict[str, Any]:
    from research.research_pause_and_data_requirements_redesign_v1.isolation import OUT

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
