"""Pin parent feasibility CASE C. No economics. No protocol inference."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.flex_mbo_connection_spec_resolution_v1 import (
    ANALYSIS_ID,
    REQUIRED_FEASIBILITY_SPEC_SHA256,
    REQUIRED_PARENT_NEXT,
    REQUIRED_PARENT_VERDICT,
    REQUIRED_POLICY_SHA256,
    REQUIRED_SELECTED_SOURCE,
)
from research.flex_mbo_connection_spec_resolution_v1.isolation import RESEARCH_ROOT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "search.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)

PARENT_REPORT = RESEARCH_ROOT / "new_information_acquisition_feasibility_design_v1" / "report.json"


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
    gates = dict(prev.get("feasibility_gates") or {})
    verdict = str(d.get("VERDICT") or a.get("87_VERDICT") or "")
    nxt = str(d.get("NEXT") or a.get("88_NEXT") or "")
    sel = str(
        (prev.get("source_boundary") or {}).get("SELECTED_SOURCE_FAMILY_ID")
        or a.get("2_selected_source_family")
        or ""
    )
    policy = str(hashes.get("EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256") or "")
    feas = str(hashes.get("FEASIBILITY_SPEC_SHA256") or d.get("FEASIBILITY_SPEC_SHA256") or "")
    f8 = gates.get("F8_event_ordering_replayable")
    if f8 is None:
        f8 = a.get("55_F8")
    f9 = gates.get("F9_deterministic_book_reconstruction_possible")
    if f9 is None:
        f9 = a.get("56_F9")
    f12 = gates.get("F12_internal_analytical_use_legally_feasible")
    if f12 is None:
        f12 = a.get("59_F12")
    f13 = gates.get("F13_local_or_replay_storage_legal_or_confirmable")
    if f13 is None:
        f13 = a.get("60_F13")
    ok = (
        verdict == REQUIRED_PARENT_VERDICT
        and nxt == REQUIRED_PARENT_NEXT
        and sel == REQUIRED_SELECTED_SOURCE
        and policy == REQUIRED_POLICY_SHA256
        and feas == REQUIRED_FEASIBILITY_SPEC_SHA256
        and f8 is False
        and f9 is False
        and f12 is False
        and f13 is False
        and d.get("STRATEGY_RESEARCH_STATUS") == "PAUSED"
        and d.get("RESTART_ALLOWED") is False
        and a.get("66_market_data_purchased") is False
        and a.get("67_historical_data_downloaded") is False
        and a.get("70_account_created") is False
        and a.get("71_collector_implemented") is False
    )
    return {
        "ok": bool(ok),
        "PARENT_ID": "NEW_INFORMATION_ACQUISITION_FEASIBILITY_DESIGN_V1",
        "VERDICT": verdict,
        "NEXT": nxt,
        "SELECTED_SOURCE_FAMILY_ID": sel,
        "EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256": policy,
        "FEASIBILITY_SPEC_SHA256": feas,
        "STRATEGY_RESEARCH_STATUS": "PAUSED",
        "RESTART_ALLOWED": False,
        "PARENT_F8": f8,
        "PARENT_F9": f9,
        "PARENT_F12": f12,
        "PARENT_F13": f13,
        "MARKET_DATA_PURCHASED": False,
        "HISTORICAL_DATA_DOWNLOADED": False,
        "ACCOUNT_CREATED": False,
        "COLLECTOR_IMPLEMENTED": False,
    }


def freeze_sha(body: dict[str, Any]) -> str:
    return dumps_sha256(body)


def already_executed_check() -> dict[str, Any]:
    from research.flex_mbo_connection_spec_resolution_v1.isolation import OUT

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
