"""Exact five 1m states from SYSTEMATIC_STATE_TRANSITION_LIBRARY_PRECOMMIT_V1. No retune."""
from __future__ import annotations

from typing import Any

from research.c1_multi_timeframe_precommit_v1 import STATE_IDS
from research.simple_tech_entry_family import (
    BB_PERIOD,
    EMA_LONG,
    EMA_SHORT,
    EMA_SLOPE_BARS,
    RCI_CROSS_LEVEL,
    RCI_PERIOD,
    VOLUME_MEDIAN_BARS,
    VOLUME_MULT,
    WARMUP_BARS,
)
from research.systematic_state_transition_library_precommit_v1.state_registry import build_state_registry
from research.systematic_state_transition_full_strategy_v1.states import (
    STATE_FNS,
    bb_above_mid,
    close_above_vwap,
    rci_above_neg80,
)


def one_min_registry() -> list[dict[str, Any]]:
    rows = build_state_registry()
    ids = tuple(str(r["STATE_ID"]) for r in rows)
    if ids != STATE_IDS:
        raise RuntimeError(f"STATE_REGISTRY_DRIFT {ids}")
    if any(r.get("THRESHOLD_TUNED_THIS_RUN") for r in rows):
        raise RuntimeError("THRESHOLD_TUNED")
    extra = {
        "S_MA_TREND_UP": (
            f"EMA{int(EMA_SHORT)}[i] > EMA{int(EMA_LONG)}[i] AND "
            f"EMA{int(EMA_LONG)}[i] > EMA{int(EMA_LONG)}[i-{int(EMA_SLOPE_BARS)}]; strict >; finite required. "
            f"WARMUP_BARS={int(WARMUP_BARS)}."
        ),
        "S_BB_ABOVE_MID": (
            f"Close[i] > BB_MID[i]; BB_PERIOD={int(BB_PERIOD)}; Close==mid is FALSE. "
            "Implemented as bb_above_mid (strict >), not NOT(Close < mid)."
        ),
        "S_RCI_ABOVE_NEG80": (
            f"RCI{int(RCI_PERIOD)}[i] > {float(RCI_CROSS_LEVEL)}; RCI==-80 is FALSE; standing level, not one-bar pulse."
        ),
        "S_VOL_CONFIRM_1M": (
            f"Volume[i] >= {float(VOLUME_MULT)} * median(previous {int(VOLUME_MEDIAN_BARS)} completed same-TF bars); "
            "current bar excluded; equality TRUE."
        ),
        "S_CLOSE_ABOVE_VWAP": "Close[i] > SESSION_VWAP[i]; strict >; Close==VWAP is FALSE.",
    }
    out = []
    for r in rows:
        rec = dict(r)
        rec["PRECOMMIT_EXACT_TEXT"] = extra[str(r["STATE_ID"])]
        rec["ONSET"] = (
            "S[i-1] evaluable AND FALSE AND S[i] evaluable AND TRUE on completed 1m bars. "
            "Missing/warmup is not a trigger. No previous-session carry."
        )
        rec["THRESHOLD_CHANGED"] = False
        out.append(rec)
    assert bb_above_mid and rci_above_neg80 and close_above_vwap and STATE_FNS
    return out
