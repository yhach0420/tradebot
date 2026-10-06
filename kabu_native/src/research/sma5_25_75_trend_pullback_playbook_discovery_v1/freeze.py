"""D1-only freeze of the predeclared state machine. No D2 inspection. No period search."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.sma5_25_75_trend_pullback_playbook_discovery_v1 import (
    B_REFRACTORY,
    PEER_RESCUE,
    SMA_PERIODS,
    SESSION_FLAT,
)

DEFINITIONS = {
    "playbook": "TREND → PULLBACK → HOLD/RECLAIM → REACCELERATION",
    "sma_periods": list(SMA_PERIODS),
    "trend": "BULL SMA5>SMA25>SMA75 ; BEAR SMA5<SMA25<SMA75",
    "pullback": "after trend-side of SMA5 on an earlier bar, price leaves SMA5 toward SMA25; bar touches or reaches SMA25; completed close has not failed through SMA75",
    "trigger": "price action: SMA5 reclaim OR break of last causally confirmed minor 1m swing; selling/buying stop of progress = no new pullback extreme or SMA5 reclaim",
    "entry": "next causal bar open after trigger bar completes",
    "same_bar_entry": False,
    "invalidation": "pullback extreme known at trigger (bull pullback low / bear pullback high)",
    "participation": "clock-normalized TV: impulse mean > pullback mean AND trigger > pullback mean; no percentile search",
    "location": "touch-based MA25 / VWAP / frozen S/R; no distance grid",
    "comparison_B": "same 1m trigger without TREND_STRUCTURE_ALIGNED",
    "comparison_C": "aligned stack + same trigger without SMA25 pullback",
    "lunch": "trading-minute SMA; lunch is not elapsed trading minutes",
    "session_flat": SESSION_FLAT,
    "b_refractory_bars": int(B_REFRACTORY),
    "peer_rescue": bool(PEER_RESCUE),
    "d1_only": True,
    "d2_not_read": True,
}


def freeze_payload() -> dict[str, Any]:
    root = Path(__file__).resolve().parent
    machine = (root / "machine.py").read_bytes()
    ma = (root / "ma.py").read_bytes()
    rec = dict(DEFINITIONS)
    rec["MACHINE_SHA256"] = hashlib.sha256(machine + ma + json.dumps(DEFINITIONS, sort_keys=True).encode("utf-8")).hexdigest()
    rec["FREEZE_SHA256"] = hashlib.sha256(json.dumps(rec, sort_keys=True).encode("utf-8")).hexdigest()
    rec["locked"] = True
    rec["ok"] = True
    return rec
