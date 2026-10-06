"""Harness source hash. Does not enter the frozen Complete Strategy SHA."""
from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_FILES = (
    "__init__.py",
    "isolation.py",
    "spec.py",
    "gate.py",
    "precommit.py",
    "replay_conf.py",
    "metrics.py",
    "invariants.py",
    "decide.py",
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
