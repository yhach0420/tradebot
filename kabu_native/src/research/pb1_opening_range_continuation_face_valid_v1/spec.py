"""Source hash."""
from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_FILES = (
    "__init__.py",
    "isolation.py",
    "bind.py",
    "definitions.py",
    "or15.py",
    "machine.py",
    "inplay.py",
    "walk.py",
    "charts.py",
    "classify.py",
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
