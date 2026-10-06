"""Source hash for the audit package. Does not include frozen V4."""
from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_FILES = (
    "__init__.py",
    "isolation.py",
    "bind.py",
    "counts.py",
    "invariants.py",
    "leakage.py",
    "parity.py",
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
