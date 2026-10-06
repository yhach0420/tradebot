"""Source hash. Do not reuse a blocked or incomplete composition audit."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.fixed_daytrade_universe_composition_audit_v1 import CASE_APPROVED, CASE_EXPAND, CASE_GAPS

SOURCE_FILES = (
    "__init__.py",
    "isolation.py",
    "stats.py",
    "proxies.py",
    "factors.py",
    "load_daily.py",
    "redundancy.py",
    "expansion.py",
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
    from research.fixed_daytrade_universe_composition_audit_v1.isolation import OUT

    path = OUT / "report.json"
    if not path.is_file():
        return {"REUSED_EXISTING_RESULT": False}
    prev = json.loads(path.read_text(encoding="utf-8"))
    verdict = str(dict(prev.get("decision") or {}).get("VERDICT") or "")
    if verdict in {CASE_APPROVED, CASE_GAPS, CASE_EXPAND}:
        return {"REUSED_EXISTING_RESULT": False, "prior_verdict": verdict}
    return {"REUSED_EXISTING_RESULT": False}
