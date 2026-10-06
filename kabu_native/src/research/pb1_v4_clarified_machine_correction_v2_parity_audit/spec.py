"""Source hash for the parity-audit package only."""
from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_FILES = (
    "__init__.py",
    "isolation.py",
    "bind.py",
    "reconstruct.py",
    "changed_audit.py",
    "one_bar_audit.py",
    "two_sided_audit.py",
    "active_audit.py",
    "failed_open_audit.py",
    "family_a_audit.py",
    "form_b_audit.py",
    "hidden1m.py",
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
