"""Source hash for the Frozen Validation blind-confirmation harness. Not a V4 mutation."""
from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_FILES = (
    "__init__.py",
    "isolation.py",
    "spec.py",
    "gate.py",
    "dates.py",
    "precommit.py",
    "asf_audit.py",
    "walk_fv.py",
    "adjudicate.py",
    "publish.py",
    "__main__.py",
)


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()
