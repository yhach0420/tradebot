"""Harness source hash. Not a Complete Strategy mutation."""
from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_FILES = (
    "__init__.py",
    "isolation.py",
    "spec.py",
    "load.py",
    "path.py",
    "classify.py",
    "walks.py",
    "slices.py",
    "waterfall.py",
    "verdict.py",
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
