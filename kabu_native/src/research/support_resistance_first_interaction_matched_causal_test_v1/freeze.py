"""Frozen detector hashes. Do not retune confirmation, width, activation, invalidation, selection, or the episode machine."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from research.support_resistance_face_valid_first_interaction_rebuild_v1 import CONFIRM_ATR, ZONE_HALF_ATR
from research.support_resistance_face_valid_first_interaction_rebuild_v1.spec import SOURCE_FILES as REBUILD_SOURCE_FILES

DETECTOR_FILES = ("__init__.py", "swings.py", "zones.py", "select.py", "machine.py")


def _root() -> Path:
    return Path(__file__).resolve().parents[1] / "support_resistance_face_valid_first_interaction_rebuild_v1"


def _file_sha(name: str) -> str:
    return hashlib.sha256((_root() / name).read_bytes()).hexdigest()


def detector_sha256() -> str:
    root = _root()
    h = hashlib.sha256()
    h.update(f"CONFIRM_ATR={CONFIRM_ATR}\n".encode("utf-8"))
    h.update(f"ZONE_HALF_ATR={ZONE_HALF_ATR}\n".encode("utf-8"))
    for name in DETECTOR_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()


def state_machine_sha256() -> str:
    return _file_sha("machine.py")


def rebuild_source_sha256() -> str:
    root = _root()
    h = hashlib.sha256()
    for name in REBUILD_SOURCE_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()


def freeze_record() -> dict[str, Any]:
    return {
        "confirm_atr": CONFIRM_ATR,
        "zone_half_atr": ZONE_HALF_ATR,
        "confirm_atr_frozen": CONFIRM_ATR == 0.75,
        "zone_half_atr_frozen": ZONE_HALF_ATR == 0.15,
        "detector_files": list(DETECTOR_FILES),
        "DETECTOR_SHA256": detector_sha256(),
        "STATE_MACHINE_SHA256": state_machine_sha256(),
        "REBUILD_SOURCE_SHA256": rebuild_source_sha256(),
        "file_sha256": {name: _file_sha(name) for name in DETECTOR_FILES},
        "retuned": False,
        "frozen_confirm_atr": 0.75,
        "frozen_zone_half_atr": 0.15,
    }
