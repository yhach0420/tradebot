"""V19 independent Capture replay of frozen V18 FIXED180 policy. Do not read V18 day cache."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from research.simple_tech_entry_family.harvest import load_day_cache
from research.simple_tech_exit_family.isolation import RESEARCH_CACHE
from research.simple_tech_exit_family.v18_harvest import replay_v18_day, save_v18_day_cache
from research.simple_tech_exit_family.v18_spec import V12_SPEC_SHA256_EXPECTED

V18_CACHE = RESEARCH_CACHE / "v18_fixed180_exit"
V19_CACHE = RESEARCH_CACHE / "v19_exit_structure_verification"


def replay_v19_day(payload: dict[str, Any]) -> dict[str, Any]:
    """Same frozen quote/fill engine as V18. Fresh Capture stream. Not a V18 cache copy."""
    day = str(payload.get("date") or "")
    print(f"{day} v19 independent replay (V18 cache unused)", flush=True)
    body = replay_v18_day(payload)
    if isinstance(body, dict):
        body["v18_cache_copy"] = False
        body["independent_replay"] = True
    return body


def save_v19_day_cache(path: Path, body: dict[str, Any]) -> None:
    if V18_CACHE in path.parents or path.parent == V18_CACHE:
        raise RuntimeError("V19 must not write V18 cache.")
    save_v18_day_cache(path, body)


def load_v19_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    if V18_CACHE in path.parents or path.parent == V18_CACHE:
        return {}
    body = load_day_cache(path, spec_sha)
    if not body:
        return {}
    if str(body.get("v12_spec_sha") or "") != V12_SPEC_SHA256_EXPECTED:
        return {}
    return body
