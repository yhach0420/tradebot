"""Pin analysis identity. No economics. No MBO."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.existing_data_entry_aligned_full_causal_logic_completion_v1 import ANALYSIS_ID
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.isolation import RESEARCH_ROOT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "states.py",
    "theses.py",
    "duplicates.py",
    "harvest.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        p = root / name
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()


def freeze_sha(body: dict[str, Any]) -> str:
    return dumps_sha256(body)


def already_executed_check() -> dict[str, Any]:
    from research.existing_data_entry_aligned_full_causal_logic_completion_v1.isolation import OUT

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
assert RESEARCH_ROOT
