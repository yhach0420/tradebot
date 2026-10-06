"""Source hash for the clarified-machine package."""
from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_FILES = (
    "__init__.py",
    "isolation.py",
    "bind.py",
    "binding.py",
    "baselines.py",
    "s0.py",
    "seed.py",
    "active.py",
    "location.py",
    "thesis.py",
    "continuation.py",
    "execution.py",
    "machine.py",
    "walk.py",
    "calibrate.py",
    "audit.py",
    "invariants.py",
    "leakage.py",
    "definitions.py",
    "analyze.py",
    "compare.py",
    "hidden1m.py",
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
