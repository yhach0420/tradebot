"""Same-family HTF states. Native period counts on completed 3m/5m. VWAP is 1m session as-of."""
from __future__ import annotations

from typing import Any

from research.c1_multi_timeframe_precommit_v1 import HTF_IDS, HTF_WIDTH_SEC, STATE_IDS
from research.c1_multi_timeframe_precommit_v1.one_min_states import one_min_registry
from research.simple_tech_entry_family import (
    BB_PERIOD,
    EMA_LONG,
    EMA_SHORT,
    EMA_SLOPE_BARS,
    RCI_CROSS_LEVEL,
    RCI_PERIOD,
    VOLUME_MEDIAN_BARS,
    VOLUME_MULT,
)
from research.systematic_state_transition_full_strategy_v1.states import STATE_FNS


def htf_registry() -> list[dict[str, Any]]:
    one = {r["STATE_ID"]: r for r in one_min_registry()}
    rows: list[dict[str, Any]] = []
    for htf in HTF_IDS:
        width = float(HTF_WIDTH_SEC[htf])
        for sid in STATE_IDS:
            src = one[sid]
            if sid == "S_CLOSE_ABOVE_VWAP":
                definition = (
                    f"{htf} Close[h] > SESSION_VWAP_ASOF(h.finalize_t). "
                    "SESSION_VWAP_ASOF is attach_indicators.vwap on completed 1m bars, last 1m "
                    "with finalize_t <= h.finalize_t. Not SUM(HTF_Close*HTF_Volume)/SUM(HTF_Volume)."
                )
                fn = "htf_close_above_session_vwap"
            elif sid == "S_VOL_CONFIRM_1M":
                definition = (
                    f"{htf} volume[h] >= {float(VOLUME_MULT)} * median(previous {int(VOLUME_MEDIAN_BARS)} "
                    "completed same-TF HTF bars). Current HTF bar excluded. 1m volume median not reused."
                )
                fn = "volume_confirm on HTF indicators"
            elif sid == "S_MA_TREND_UP":
                definition = (
                    f"{htf} EMA{int(EMA_SHORT)}[h] > EMA{int(EMA_LONG)}[h] AND "
                    f"EMA{int(EMA_LONG)}[h] > EMA{int(EMA_LONG)}[h-{int(EMA_SLOPE_BARS)}]; strict >."
                )
                fn = "trend_up on HTF indicators"
            elif sid == "S_BB_ABOVE_MID":
                definition = (
                    f"{htf} Close[h] > BB_MID[h]; BB_PERIOD={int(BB_PERIOD)}; Close==mid FALSE; not NOT(Close<mid)."
                )
                fn = "bb_above_mid on HTF indicators"
            else:
                definition = (
                    f"{htf} RCI{int(RCI_PERIOD)}[h] > {float(RCI_CROSS_LEVEL)}; standing level; RCI==-80 FALSE."
                )
                fn = "rci_above_neg80 on HTF indicators"
            rows.append(
                {
                    "HTF_ID": htf,
                    "WIDTH_SEC": width,
                    "STATE_ID": sid,
                    "FAMILY": src["FAMILY"],
                    "STANDING_CONTEXT_ONLY": True,
                    "HTF_ONSET_REQUIRED": False,
                    "HTF_AGE_REQUIRED": False,
                    "HTF_PERSISTENCE_REQUIRED": False,
                    "EXACT_DEFINITION": definition,
                    "SOURCE_FUNCTION": fn,
                    "THRESHOLD_CHANGED": False,
                    "1M_THRESHOLD_SOURCE": src["THRESHOLD_SOURCE"],
                    "STATE_FN_KEYS": list(STATE_FNS.keys()),
                }
            )
    if len(rows) != 10:
        raise RuntimeError("HTF_STATE_N")
    return rows
