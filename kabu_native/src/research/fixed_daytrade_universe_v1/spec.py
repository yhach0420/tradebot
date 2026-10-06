"""Source hash. Reuse only a successful freeze, never a waiting result."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.fixed_daytrade_universe_v1 import CASE_FROZEN

SOURCE_FILES = (
    "__init__.py",
    "isolation.py",
    "phase0_fix.py",
    "jquants_auth.py",
    "jquants_client.py",
    "schema.py",
    "calendar_window.py",
    "acquire.py",
    "secrets.py",
    "daily_source.py",
    "liquidity.py",
    "operability.py",
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
    from research.fixed_daytrade_universe_v1.isolation import OUT

    path = OUT / "report.json"
    if not path.is_file():
        return {"REUSED_EXISTING_RESULT": False}
    prev = json.loads(path.read_text(encoding="utf-8"))
    verdict = str(dict(prev.get("decision") or {}).get("VERDICT") or "")
    if verdict == CASE_FROZEN:
        return {"REUSED_EXISTING_RESULT": True, "prior_report": prev}
    return {"REUSED_EXISTING_RESULT": False}
