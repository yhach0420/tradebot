"""Pin parent policy. No economics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.new_information_acquisition_feasibility_design_v1 import (
    ANALYSIS_ID,
    REQUIRED_PARENT_NEXT,
    REQUIRED_PARENT_VERDICT,
    REQUIRED_POLICY_SHA256,
    REQUIRED_SELECTED_SOURCE,
)
from research.new_information_acquisition_feasibility_design_v1.isolation import RESEARCH_ROOT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "protocol.py",
    "access.py",
    "date_exposure.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)

PARENT_REPORT = RESEARCH_ROOT / "new_information_source_policy_decision_v1" / "report.json"


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
    verdict = str(d.get("VERDICT") or a.get("66_VERDICT") or "")
    nxt = str(d.get("NEXT") or a.get("67_NEXT") or "")
    sel = str(d.get("SELECTED_SOURCE_FAMILY_ID") or a.get("36_selected_source_family_ID") or "")
    sha = str(
        hashes.get("EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256")
        or a.get("41_EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256")
        or ""
    )
    ok = (
        verdict == REQUIRED_PARENT_VERDICT
        and nxt == REQUIRED_PARENT_NEXT
        and sel == REQUIRED_SELECTED_SOURCE
        and sha == REQUIRED_POLICY_SHA256
        and d.get("STRATEGY_RESEARCH_STATUS") == "PAUSED"
        and d.get("RESTART_ALLOWED") is False
        and a.get("42_external_market_data_downloaded") is False
        and a.get("44_subscription_changed") is False
        and a.get("45_account_created") is False
        and a.get("46_API_key_created") is False
    )
    return {
        "ok": bool(ok),
        "PARENT_ID": "NEW_INFORMATION_SOURCE_POLICY_DECISION_V1",
        "VERDICT": verdict,
        "NEXT": nxt,
        "SELECTED_SOURCE_FAMILY_ID": sel,
        "EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256": sha,
        "STRATEGY_RESEARCH_STATUS": "PAUSED",
        "RESTART_ALLOWED": False,
        "ACTUAL_EXTERNAL_DATA_ACQUIRED": False,
        "EXTERNAL_MARKET_DATA_DOWNLOADED": False,
        "SUBSCRIPTION_CHANGED": False,
        "ACCOUNT_CREATED": False,
        "API_KEY_CREATED": False,
    }


def freeze_sha(body: dict[str, Any]) -> str:
    return dumps_sha256(body)


def already_executed_check() -> dict[str, Any]:
    from research.new_information_acquisition_feasibility_design_v1.isolation import OUT

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
