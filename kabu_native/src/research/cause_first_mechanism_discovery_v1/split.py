"""Chronological 60/20/20 split. Exact lists + SHA before any outcome look. No random split."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.cause_first_mechanism_discovery_v1 import (
    CONFIRMATION_FRAC,
    DISCOVERY_FRAC,
    FROZEN_VALIDATION_FRAC,
)


def split_session_days(session_days: list[str]) -> dict[str, Any]:
    days = [str(d) for d in session_days if d]
    days = sorted(set(days))
    n = len(days)
    if n < 30:
        return {
            "ok": False,
            "dates_assigned": False,
            "reason": "session_n_below_30",
            "n_sessions": n,
            "random_split": False,
            "frozen_validation_hidden_from_discovery": True,
        }
    n_disc = int(round(n * DISCOVERY_FRAC))
    n_conf = int(round(n * CONFIRMATION_FRAC))
    n_val = n - n_disc - n_conf
    if n_val < 1 or n_conf < 1 or n_disc < 1:
        return {"ok": False, "dates_assigned": False, "reason": "split_empty_partition", "n_sessions": n, "random_split": False}
    disc = days[:n_disc]
    conf = days[n_disc : n_disc + n_conf]
    val = days[n_disc + n_conf :]
    payload = {"discovery": disc, "confirmation": conf, "frozen_validation": val}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    overlap = set(disc) & set(conf) or set(disc) & set(val) or set(conf) & set(val)
    chronological = disc + conf + val == days
    return {
        "ok": bool(not overlap and chronological and len(disc) + len(conf) + len(val) == n),
        "dates_assigned": True,
        "random_split": False,
        "n_sessions": n,
        "discovery": {"first": disc[0], "last": disc[-1], "n": len(disc), "dates": disc},
        "confirmation": {"first": conf[0], "last": conf[-1], "n": len(conf), "dates": conf},
        "frozen_validation": {"first": val[0], "last": val[-1], "n": len(val), "dates": val},
        "split_sha256": sha,
        "frozen_validation_hidden_from_discovery": True,
        "frozen_validation_hidden_from_confirmation": True,
        "feature_selection_on_frozen_validation": False,
        "threshold_selection_on_frozen_validation": False,
        "entry_design_on_frozen_validation": False,
        "exit_design_on_frozen_validation": False,
        "frac": {"discovery": DISCOVERY_FRAC, "confirmation": CONFIRMATION_FRAC, "frozen_validation": FROZEN_VALIDATION_FRAC},
    }


def partition_set(split: dict[str, Any], name: str) -> set[str]:
    block = dict(split.get(name) or {})
    return {str(d) for d in list(block.get("dates") or [])}


assert split_session_days([])["ok"] is False
_tiny = [f"202601{i:02d}" for i in range(1, 31)]
_s = split_session_days(_tiny)
assert _s["ok"] is True
assert _s["random_split"] is False
assert not (set(_s["discovery"]["dates"]) & set(_s["frozen_validation"]["dates"]))
assert _s["discovery"]["dates"][-1] < _s["confirmation"]["dates"][0]
assert _s["confirmation"]["dates"][-1] < _s["frozen_validation"]["dates"][0]
