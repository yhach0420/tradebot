"""D1-only freeze of live-causal 5m MA playbook semantics. No D2 inspection. No period search."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.mtf_5min_sma5_25_75_with_1min_trigger_v1 import (
    ATR1M_N,
    B_REFRACTORY,
    NEGLIGIBLE_ABS_BPS,
    PEER_RESCUE,
    SESSION_FLAT,
    SMA_PERIODS,
    ZONE_ATR_MULT,
)

DEFINITIONS = {
    "playbook": "5M TREND STRUCTURE → TARGET PULLBACK → DYNAMIC MA LOCATION → 1M PRICE-ACTION TURN → NEXT 1M OPEN",
    "sma_periods": list(SMA_PERIODS),
    "sma_timeframe": "5-minute chart, live-causal",
    "primary_ma": "LIVE_CAUSAL_5M_SMA",
    "confirmed_ma": "diagnostic only; never chosen by performance",
    "trend": "BULL LIVE SMA5>SMA25>SMA75 ; BEAR LIVE SMA5<SMA25<SMA75",
    "pullback": "after trend-side of live 5m SMA5, price retraces and reaches the frozen SMA25 zone",
    "sma25_zone": "± ZONE_ATR_MULT × causal 1m ATR20 around current live 5m SMA25",
    "zone_atr_mult": float(ZONE_ATR_MULT),
    "atr1m_n": int(ATR1M_N),
    "trigger": "completed 1m bar reclaims live 5m SMA5 OR breaks last causally confirmed minor 1m swing",
    "entry": "next causal 1m bar open after trigger bar completes",
    "same_bar_entry": False,
    "invalidation": "pullback extreme known at trigger (bull pullback low / bear pullback high)",
    "sma75_role": "structural fail if completed 1m close through live 5m SMA75 against DIR; not an entry trigger",
    "sma5_role": "short-term momentum; may be lost during pullback; reclaim is one trigger",
    "slope": "diagnostic only; no slope threshold for primary setup",
    "comparison_B": "same 1m trigger without 5m MA stack alignment",
    "comparison_C": "5m stack aligned + same 1m trigger without SMA25-zone pullback",
    "lunch": "no synthetic 5m lunch bars; no overnight synthetic bars; prior-session 5m closes remain in MA",
    "sma_reset_at_open": False,
    "session_flat": SESSION_FLAT,
    "b_refractory_bars": int(B_REFRACTORY),
    "negligible_abs_bps": float(NEGLIGIBLE_ABS_BPS),
    "peer_rescue": bool(PEER_RESCUE),
    "d1_only": True,
    "d2_not_read": True,
    "wait_for_5m_candle_completion": False,
}


def freeze_payload() -> dict[str, Any]:
    root = Path(__file__).resolve().parent
    machine = (root / "machine.py").read_bytes()
    ma = (root / "ma.py").read_bytes()
    bars = (root / "bars5.py").read_bytes()
    rec = dict(DEFINITIONS)
    rec["MACHINE_SHA256"] = hashlib.sha256(machine + ma + bars + json.dumps(DEFINITIONS, sort_keys=True).encode("utf-8")).hexdigest()
    rec["FREEZE_SHA256"] = hashlib.sha256(json.dumps(rec, sort_keys=True).encode("utf-8")).hexdigest()
    rec["locked"] = True
    rec["ok"] = True
    return rec
