"""Pin parent V3 inventory. Source identity hash. No Full Strategy freeze."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.profitable_move_mechanism_discovery_v1 import (
    ELIGIBLE_PROPOSAL_N,
    FULL_STRATEGY_SPEC_SHA256_V5,
    REQUIRED_PARENT_VERDICT,
)
from research.profitable_move_mechanism_discovery_v1.isolation import RESEARCH_ROOT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "engine.py",
    "predicates.py",
    "library.py",
    "prior_use.py",
    "harvest.py",
    "stats.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)

PARENT_REPORT = RESEARCH_ROOT / "new_full_strategy_architecture_inventory_and_freeze_v3" / "report.json"


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


def load_parent_report() -> dict[str, Any]:
    if not PARENT_REPORT.is_file():
        return {}
    return json.loads(PARENT_REPORT.read_text(encoding="utf-8"))


def pin_parent_v3() -> dict[str, Any]:
    prev = load_parent_report()
    d = dict(prev.get("decision") or {})
    verdict = str(d.get("VERDICT") or prev.get("VERDICT") or "")
    eligible = d.get("ELIGIBLE_PROPOSAL_N")
    if eligible is None:
        eligible = prev.get("ELIGIBLE_PROPOSAL_N")
    v5 = d.get("FULL_STRATEGY_SPEC_SHA256_V5", prev.get("FULL_STRATEGY_SPEC_SHA256_V5", "MISSING"))
    ok = (
        verdict == REQUIRED_PARENT_VERDICT
        and eligible is not None
        and int(eligible) == int(ELIGIBLE_PROPOSAL_N)
        and v5 is None
        and FULL_STRATEGY_SPEC_SHA256_V5 is None
    )
    return {
        "ok": bool(ok),
        "REQUIRED_PARENT_VERDICT": REQUIRED_PARENT_VERDICT,
        "observed_verdict": verdict,
        "ELIGIBLE_PROPOSAL_N": int(eligible) if eligible is not None else None,
        "FULL_STRATEGY_SPEC_SHA256_V5": v5,
        "V4_CLOSED": True,
        "V4_RCA_RUN": False,
        "V4_RETUNE": False,
        "PREVIOUS_V5_PROPOSAL_WITHDRAWN": True,
        "INFORMATION_FAMILY_INVENTORY_AS_PRIMARY_NEXT": False,
    }
