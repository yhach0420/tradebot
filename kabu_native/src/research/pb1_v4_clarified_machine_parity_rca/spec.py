"""Source hash for the parity RCA package only."""
from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_FILES = (
    "__init__.py",
    "isolation.py",
    "bind.py",
    "materialize.py",
    "progress.py",
    "seed_two_sided.py",
    "failed_open.py",
    "flat_crawl.py",
    "family_a.py",
    "one_bar.py",
    "reclassify.py",
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
