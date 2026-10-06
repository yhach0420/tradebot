"""Source hash. Reuse only a successful READY panel, never a blocked result."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.aligned_historical_panel_v1 import CASE_READY

SOURCE_FILES = (
    "__init__.py",
    "isolation.py",
    "universe_bind.py",
    "minute_schema.py",
    "time_semantics.py",
    "probe_minute.py",
    "external.py",
    "technical.py",
    "split_panel.py",
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
    from research.aligned_historical_panel_v1.isolation import OUT

    path = OUT / "report.json"
    if not path.is_file():
        return {"REUSED_EXISTING_RESULT": False}
    prev = json.loads(path.read_text(encoding="utf-8"))
    verdict = str(dict(prev.get("decision") or {}).get("VERDICT") or "")
    if verdict == CASE_READY:
        return {"REUSED_EXISTING_RESULT": True, "prior_report": prev}
    return {"REUSED_EXISTING_RESULT": False}
