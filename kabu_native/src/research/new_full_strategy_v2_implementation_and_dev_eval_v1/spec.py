"""Pin frozen V4 identity. Do not mutate construction_v2 spec."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.new_full_strategy_architecture_construction_v2 import (
    ARCHITECTURE_ID,
    CASE_FROZEN,
)
from research.new_full_strategy_architecture_construction_v2.spec import spec_sha256 as construction_spec_sha256
from research.new_full_strategy_v2_implementation_and_dev_eval_v1 import (
    PINNED_V4_SHA256,
    REQUIRED_CONSTRUCTION_VERDICT,
)
from research.new_full_strategy_v2_implementation_and_dev_eval_v1.isolation import RESEARCH_ROOT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "engine.py",
    "integrity.py",
    "harvest.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)

CONSTRUCTION_REPORT = RESEARCH_ROOT / "new_full_strategy_architecture_construction_v2" / "report.json"


def dumps_sha256(obj: Any) -> str:
    body = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def v4_sha256() -> str:
    return str(construction_spec_sha256())


def v4_hash_unchanged() -> bool:
    return v4_sha256() == PINNED_V4_SHA256


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()


def load_construction_report() -> dict[str, Any]:
    if not CONSTRUCTION_REPORT.is_file():
        return {}
    return json.loads(CONSTRUCTION_REPORT.read_text(encoding="utf-8"))


def pin_v4() -> dict[str, Any]:
    live = v4_sha256()
    match_hash = live == PINNED_V4_SHA256
    prev = load_construction_report()
    decision = dict(prev.get("decision") or {})
    verdict = str(prev.get("VERDICT") or decision.get("VERDICT") or "")
    arch = str(decision.get("ARCHITECTURE_ID") or prev.get("ARCHITECTURE_ID") or "")
    if not arch:
        frozen = dict(prev.get("frozen") or prev.get("spec") or {})
        arch = str(frozen.get("ARCHITECTURE_ID") or "")
    report_sha = str(
        decision.get("FULL_STRATEGY_SPEC_SHA256_V4")
        or prev.get("spec_sha256")
        or prev.get("FULL_STRATEGY_SPEC_SHA256_V4")
        or ""
    )
    ok = (
        bool(match_hash)
        and verdict == REQUIRED_CONSTRUCTION_VERDICT
        and arch == ARCHITECTURE_ID
        and (not report_sha or report_sha == PINNED_V4_SHA256)
        and verdict == CASE_FROZEN
    )
    return {
        "ok": bool(ok),
        "V4_HASH_MATCH": bool(match_hash and (not report_sha or report_sha == PINNED_V4_SHA256)),
        "live_sha": live,
        "pinned_sha": PINNED_V4_SHA256,
        "report_sha": report_sha,
        "construction_verdict": verdict,
        "required_verdict": REQUIRED_CONSTRUCTION_VERDICT,
        "architecture_id": arch,
        "required_architecture_id": ARCHITECTURE_ID,
        "ANOTHER_PRECOMMIT_RUN": False,
        "V4_IMMUTABLE": True,
    }
