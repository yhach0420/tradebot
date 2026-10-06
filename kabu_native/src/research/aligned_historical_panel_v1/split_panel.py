"""Chronological split. Dates assigned only after a real common overlap exists. No random split."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from research.aligned_historical_panel_v1.time_semantics import JST

DISCOVERY_FRAC = 0.60
CONFIRMATION_FRAC = 0.20
FROZEN_VALIDATION_FRAC = 0.20


def _ymd(d: datetime) -> str:
    return d.strftime("%Y%m%d")


def assign_split(session_days: list[str]) -> dict[str, Any]:
    days = [str(d) for d in session_days if d]
    n = len(days)
    if n < 20:
        return {
            "dates_assigned": False,
            "reason": "common_overlap_too_short_or_unknown",
            "n_sessions": n,
            "random_split": False,
            "discovery_frac": DISCOVERY_FRAC,
            "confirmation_frac": CONFIRMATION_FRAC,
            "frozen_validation_frac": FROZEN_VALIDATION_FRAC,
            "frozen_validation_hidden_from_discovery": True,
        }
    n_disc = int(round(n * DISCOVERY_FRAC))
    n_conf = int(round(n * CONFIRMATION_FRAC))
    n_val = n - n_disc - n_conf
    if n_val <= 0:
        n_val = 1
        n_conf = max(1, n_conf - 1)
        n_disc = n - n_conf - n_val
    disc = days[:n_disc]
    conf = days[n_disc : n_disc + n_conf]
    val = days[n_disc + n_conf :]
    return {
        "dates_assigned": True,
        "random_split": False,
        "n_sessions": n,
        "discovery": {"first": disc[0], "last": disc[-1], "n": len(disc)},
        "confirmation": {"first": conf[0], "last": conf[-1], "n": len(conf)},
        "frozen_validation": {"first": val[0], "last": val[-1], "n": len(val)},
        "frozen_validation_hidden_from_discovery": True,
        "no_aggregate_validation_in_this_report": True,
        "assigned_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
    }


def unassigned() -> dict[str, Any]:
    return assign_split([])


assert abs(DISCOVERY_FRAC + CONFIRMATION_FRAC + FROZEN_VALIDATION_FRAC - 1.0) < 1e-12
assert unassigned()["dates_assigned"] is False
_ = timedelta
