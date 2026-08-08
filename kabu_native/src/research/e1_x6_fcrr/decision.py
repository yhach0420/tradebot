"""Decision wrapper: observe → optional ENTRY (research-only)."""
from __future__ import annotations

from typing import Any, Optional

from .features import FeatureBuffer
from .state_machine import Machine


def push_and_decide(
    buf: FeatureBuffer,
    machine: Machine,
    *,
    t: float,
    bid: float,
    ask: float,
    vwap: float,
    cum_vol: float,
    evaluate: bool,
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """Update buffer; if evaluate, run one state-machine step."""
    err = buf.push(t, bid, ask, vwap, cum_vol)
    if not evaluate:
        return None, {"complete": False, "reason": "NOT_EVAL", "asof_time": t}
    feats = buf.snapshot(t)
    if err and not feats.get("complete"):
        feats["reason"] = feats.get("reason") or err
    sig = machine.observe(t, feats)
    return sig, feats
