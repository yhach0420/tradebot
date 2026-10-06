"""Source and spec hashes. Does not include parent spec or any V4 machine."""
from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_FILES = (
    "__init__.py",
    "isolation.py",
    "bind.py",
    "specification.py",
    "exemplars.py",
    "analyze.py",
    "publish.py",
    "spec.py",
    "__main__.py",
)
SPEC_FILES = ("specification.py",)


def _hash_named(root: Path, names: tuple[str, ...]) -> str:
    h = hashlib.sha256()
    for name in names:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()


def source_sha256() -> str:
    return _hash_named(Path(__file__).resolve().parent, SOURCE_FILES)


def spec_sha256() -> str:
    return _hash_named(Path(__file__).resolve().parent, SPEC_FILES)
