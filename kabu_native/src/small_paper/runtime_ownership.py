"""Production SoT for ownership classification (V26-C).

One implementation: small_paper.ownership_classifier.classify_owner.
Callers must not invent pid-only / generation-only ownership decisions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from small_paper.ownership_classifier import (
    CONFLICT,
    CURRENT_VALID,
    DEAD_OWNER,
    PID_REUSED,
    STALE_PROVEN_OWNED,
    UNKNOWN,
    classify_owner,
    current_identity_from_env,
)

CLASSIFIER_IMPLEMENTATION_ID = "small_paper.ownership_classifier.classify_owner"
CLASSIFIER_IMPLEMENTATION_COUNT = 1

OWNERSHIP_CLASSES = (
    CURRENT_VALID,
    STALE_PROVEN_OWNED,
    DEAD_OWNER,
    PID_REUSED,
    UNKNOWN,
    CONFLICT,
)


def classify_production_owner(
    *,
    native_root: Optional[Path] = None,
    trading_date: Optional[str] = None,
    current_pid: int = 0,
    pid_alive_fn: Optional[Callable[[int], bool]] = None,
    live_process_start_fn: Optional[Callable[[int], str]] = None,
    owner: Optional[Mapping[str, Any]] = None,
    bundle: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Load station/bundle (unless provided) and classify via the single SoT."""
    from small_paper.kabu_token_authority import load_station_bundle, load_station_owner

    own = dict(owner) if owner is not None else load_station_owner()
    bun = dict(bundle) if bundle is not None else load_station_bundle()
    current = current_identity_from_env(pid=int(current_pid or 0))
    if native_root is not None:
        current["native_root"] = str(native_root)
    if trading_date:
        current["trading_date"] = str(trading_date)
    out = classify_owner(
        owner=own,
        bundle=bun,
        current=current,
        pid_alive_fn=pid_alive_fn,
        live_process_start_fn=live_process_start_fn,
    )
    out["classifier"] = CLASSIFIER_IMPLEMENTATION_ID
    return out
