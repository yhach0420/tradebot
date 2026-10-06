"""Pin parent CASE C. No economics. No MBO."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.delayed_open_buy_special_quote_release_full_strategy_v1 import (
    ANALYSIS_ID,
    PARENT_ID,
    REQUIRED_PARENT_VERDICT,
)
from research.delayed_open_buy_special_quote_release_full_strategy_v1.isolation import RESEARCH_ROOT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "duplicates.py",
    "semantics.py",
    "strategy.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)

PARENT_REPORT = RESEARCH_ROOT / "opening_logic_input_capability_audit_v1" / "report.json"


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
    verdict = str(d.get("VERDICT") or a.get("59_VERDICT") or "")
    ok = str(prev.get("ANALYSIS_ID") or "") == PARENT_ID and verdict == REQUIRED_PARENT_VERDICT
    return {
        "ok": bool(ok),
        "PARENT_ID": PARENT_ID,
        "VERDICT": verdict,
        "NEXT": d.get("NEXT") or a.get("60_NEXT"),
        "PREOPEN_TRAJECTORY_SUPPORTED": False,
        "DO_NOT_USE_PREOPEN_EXPECTATION": True,
        "DO_NOT_USE_PREDICTED_GU_GD": True,
    }


def already_executed_check() -> dict[str, Any]:
    from research.delayed_open_buy_special_quote_release_full_strategy_v1.isolation import OUT

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
