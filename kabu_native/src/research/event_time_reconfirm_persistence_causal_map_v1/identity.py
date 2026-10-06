"""Precommitted map rules. This document does not freeze an entry."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_obj(body: Any) -> str:
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def bind(signal_sha: str) -> dict[str, Any]:
    rules = {
        "MAP_ID": "EVENT_TIME_RECONFIRM_PERSISTENCE_CAUSAL_MAP_V1",
        "signal_sha256": signal_sha,
        "ordinals": [2, 3, 4],
        "r1_tested": False,
        "entry_uses_future_ratchet_count": False,
        "entry_uses_future_exit_reason": False,
        "entry_uses_future_mfe": False,
        "entry_uses_future_mae": False,
        "entry_uses_future_hold": False,
        "entry_uses_future_pnl": False,
        "primary_latency_ms": 100,
        "robust_latency_ms": 250,
        "selection": "earliest ordinal that clears the precommitted gates",
        "entry_frozen": False,
    }
    return {"rules": rules, "map_sha256": sha256_obj(rules)}
