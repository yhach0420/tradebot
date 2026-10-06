"""Source hash for the preflight package. Not the V4 machine SHA."""
from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_FILES = (
    "__init__.py",
    "isolation.py",
    "erratum.py",
    "eligibility.py",
    "completeness.py",
    "logging.py",
    "guards.py",
    "scans.py",
    "startgate.py",
    "checks.py",
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
