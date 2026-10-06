"""Pin source hash. Refuse reuse of a mismatched prior report. No economics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.fixed_universe_historical_foundation_v1 import ANALYSIS_ID

SOURCE_FILES = (
    "__init__.py",
    "isolation.py",
    "universe.py",
    "sources.py",
    "timestamps.py",
    "calendar.py",
    "corporate_actions.py",
    "technical.py",
    "split.py",
    "analyze.py",
    "publish.py",
    "spec.py",
    "__main__.py",
)


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()


def already_executed_check() -> dict[str, Any]:
    from research.fixed_universe_historical_foundation_v1.isolation import OUT

    path = OUT / "report.json"
    if not path.is_file():
        return {"REUSED_EXISTING_RESULT": False}
    prev = json.loads(path.read_text(encoding="utf-8"))
    if str(prev.get("ANALYSIS_ID") or "") != ANALYSIS_ID:
        return {"REUSED_EXISTING_RESULT": False}
    if dict(prev.get("decision") or {}).get("VERDICT"):
        return {"REUSED_EXISTING_RESULT": True, "prior_report": prev}
    return {"REUSED_EXISTING_RESULT": False}
