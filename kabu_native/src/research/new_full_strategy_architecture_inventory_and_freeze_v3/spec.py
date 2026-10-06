"""Spec hash helpers. No V5 freeze body unless a proposal is selected."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "v4_pin.py",
    "withdrawn.py",
    "inventory.py",
    "pullback_family.py",
    "information.py",
    "novelty.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


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


def spec_sha256_v5(frozen: dict[str, Any] | None) -> str | None:
    if not frozen:
        return None
    return dumps_sha256(frozen)
