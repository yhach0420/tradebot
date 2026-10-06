"""Pin pause parent. Freeze policy SHA. No economics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.new_information_source_policy_decision_v1 import (
    ANALYSIS_ID,
    REQUIRED_CLOSED_ARCHITECTURE_N,
    REQUIRED_ELIGIBLE_METHOD_N,
    REQUIRED_PARENT_NEXT,
    REQUIRED_PARENT_VERDICT,
    REQUIRED_PAUSE_PROTOCOL_SHA256,
)
from research.new_information_source_policy_decision_v1.isolation import RESEARCH_ROOT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "documentation.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)

PARENT_REPORT = RESEARCH_ROOT / "research_pause_and_data_requirements_redesign_v1" / "report.json"


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
    verdict = str(d.get("VERDICT") or a.get("58_VERDICT") or "")
    nxt = str(d.get("NEXT") or a.get("59_NEXT") or "")
    sha = str(hashes.get("PAUSE_PROTOCOL_SPEC_SHA256") or "")
    closed_n = int(a.get("3_closed_architecture_family_N") if a.get("3_closed_architecture_family_N") is not None else -1)
    elig_n = int(a.get("2_remaining_eligible_prior_method_N") if a.get("2_remaining_eligible_prior_method_N") is not None else -1)
    ok = (
        verdict == REQUIRED_PARENT_VERDICT
        and nxt == REQUIRED_PARENT_NEXT
        and sha == REQUIRED_PAUSE_PROTOCOL_SHA256
        and closed_n == REQUIRED_CLOSED_ARCHITECTURE_N
        and elig_n == REQUIRED_ELIGIBLE_METHOD_N
        and d.get("STRATEGY_RESEARCH_STATUS") == "PAUSED"
        and d.get("RESTART_ALLOWED") is False
        and a.get("4_LEGACY_DEV_RESEARCH_BURNED") is True
        and a.get("11_current_Capture_information_domain_closed") is True
        and a.get("12_current_justified_method_space_closed") is True
        and a.get("13_current_DEV_meta_research_burned") is True
        and a.get("14_fresh_independent_development_data_required") is True
        and a.get("15_fresh_data_alone_sufficient") is False
        and a.get("16_new_causal_information_required") is True
        and a.get("42_new_strategy_created") is False
    )
    return {
        "ok": bool(ok),
        "PARENT_ID": "RESEARCH_PAUSE_AND_DATA_REQUIREMENTS_REDESIGN_V1",
        "VERDICT": verdict,
        "NEXT": nxt,
        "STRATEGY_RESEARCH_STATUS": "PAUSED",
        "RESTART_ALLOWED": False,
        "PAUSE_PROTOCOL_SPEC_SHA256": sha,
        "CLOSED_ARCHITECTURE_FAMILY_N": closed_n,
        "ELIGIBLE_RESEARCH_METHOD_N": elig_n,
        "LEGACY_DEV_RESEARCH_BURNED": True,
        "CURRENT_DEV_META_RESEARCH_BURNED": True,
        "CURRENT_CAPTURE_INFORMATION_DOMAIN_CLOSED": True,
        "JUSTIFIED_RESEARCH_METHOD_SPACE_EXHAUSTED": True,
        "NEW_CAUSAL_INFORMATION_REQUIRED": True,
        "FRESH_INDEPENDENT_DEVELOPMENT_SAMPLE_REQUIRED": True,
        "FRESH_DATA_ONLY_RESTART_ALLOWED": False,
        "MISSING_INFORMATION_CATEGORY_IDS": list(a.get("18_missing_information_category_IDs") or []),
    }


def freeze_policy_sha(body: dict[str, Any]) -> str:
    return dumps_sha256(body)


def already_executed_check() -> dict[str, Any]:
    from research.new_information_source_policy_decision_v1.isolation import OUT

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
