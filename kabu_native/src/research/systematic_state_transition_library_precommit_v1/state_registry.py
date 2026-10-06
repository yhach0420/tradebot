"""Exactly one canonical causal binary state per family. PnL unused. No new thresholds."""
from __future__ import annotations

from typing import Any

from research.simple_tech_entry_family import (
    BB_PERIOD,
    BB_SIGMA,
    EMA_LONG,
    EMA_SHORT,
    EMA_SLOPE_BARS,
    RCI_CROSS_LEVEL,
    RCI_PERIOD,
    VOLUME_MEDIAN_BARS,
    VOLUME_MULT,
    WARMUP_BARS,
)
from research.systematic_state_transition_library_precommit_v1.isolation import NATIVE
from research.systematic_state_transition_library_precommit_v1.spec import file_sha256

SELECTION_PRIORITY = (
    "Capture/live causal availability",
    "lookahead none",
    "integrity issue none",
    "already-existing canonical definition",
    "parameter search not required",
    "simplest binary definition",
    "oldest source definition",
)

EVENT_TS = (
    "Completed 1m bar index i is the evaluation point. "
    "minute M finalizes at the first valid continuous-market event of M+1 "
    "(simple_tech_entry_family.bars.SymbolBarBuilder). "
    "State uses only bars[<=i]. Next evaluation point is i+1. No intra-bar lookahead."
)


def _sha(*rel: str) -> str:
    import hashlib

    acc = hashlib.sha256()
    for r in rel:
        p = NATIVE.joinpath(*r.replace("\\", "/").split("/"))
        acc.update(r.encode("utf-8"))
        acc.update(file_sha256(p).encode("utf-8"))
        acc.update(p.read_bytes())
    return acc.hexdigest()


def build_state_registry() -> list[dict[str, Any]]:
    """Pin 5 families. Unavailable families would be omitted; all 5 are uniquely selectable."""
    rows = [
        {
            "STATE_ID": "S_MA_TREND_UP",
            "FAMILY": "F1_MA_STRUCTURE",
            "AVAILABLE": True,
            "SOURCE_PATH": "src/research/simple_tech_entry_family/stages.py",
            "SOURCE_FUNCTION": "trend_up",
            "EXACT_DEFINITION": (
                "ema9[i] > ema21[i] AND ema21[i] > ema21[i-EMA_SLOPE_BARS] "
                "with finite ema9/ema21/ema21[i-3]; ema from indicators.ema; "
                "EMA_SHORT=9, EMA_LONG=21, EMA_SLOPE_BARS=3."
            ),
            "TIMEFRAME": "1m completed bars",
            "EVENT_TIMESTAMP_SEMANTICS": EVENT_TS,
            "LOOKBACK": int(WARMUP_BARS),
            "THRESHOLD_SOURCE": (
                "src/research/simple_tech_entry_family/__init__.py "
                f"EMA_SHORT={int(EMA_SHORT)} EMA_LONG={int(EMA_LONG)} "
                f"EMA_SLOPE_BARS={int(EMA_SLOPE_BARS)} WARMUP_BARS={int(WARMUP_BARS)}"
            ),
            "THRESHOLD_TUNED_THIS_RUN": False,
            "SELECTION_REASON": (
                "Named canonical MA_STRUCTURE ENTRY predicate in Simple-Tech V1. "
                "Capture-causal 1m. No lookahead. Quote-semantic unused. "
                "Not stripped to ema9>ema21 alone (that would invent a fragment)."
            ),
            "PNL_USED": False,
            "SOURCE_SHA256": _sha(
                "src/research/simple_tech_entry_family/stages.py",
                "src/research/simple_tech_entry_family/indicators.py",
                "src/research/simple_tech_entry_family/__init__.py",
            ),
        },
        {
            "STATE_ID": "S_BB_ABOVE_MID",
            "FAMILY": "F2_BB_LOCATION",
            "AVAILABLE": True,
            "SOURCE_PATH": "src/research/simple_tech_redesign/v26_harvest.py",
            "SOURCE_FUNCTION": "primitive_true('D_BB_STRUCTURE_LOSS') inverted",
            "EXACT_DEFINITION": (
                "Close[i] > bb_mid[i] with finite close and bb_mid; "
                "bb_mid = rolling_mean(close, BB_PERIOD=20) via indicators.bollinger. "
                "This is the complement of V26 D_BB_STRUCTURE_LOSS (close < bb_mid). "
                "BB_SIGMA unused for the mid comparison."
            ),
            "TIMEFRAME": "1m completed bars",
            "EVENT_TIMESTAMP_SEMANTICS": EVENT_TS,
            "LOOKBACK": int(BB_PERIOD),
            "THRESHOLD_SOURCE": (
                "src/research/simple_tech_entry_family/__init__.py "
                f"BB_PERIOD={int(BB_PERIOD)} BB_SIGMA={float(BB_SIGMA)} (band width unused for mid)"
            ),
            "THRESHOLD_TUNED_THIS_RUN": False,
            "SELECTION_REASON": (
                "Family is BB_LOCATION. Unique simplest existing location vs mid is the "
                "named V26 D_BB_STRUCTURE_LOSS complement. pullback_setup's Close>=bb_lower "
                "is a band-edge guard inside a compound pullback, not a standalone BB location."
            ),
            "PNL_USED": False,
            "SOURCE_SHA256": _sha(
                "src/research/simple_tech_redesign/v26_harvest.py",
                "src/research/simple_tech_entry_family/indicators.py",
                "src/research/simple_tech_entry_family/stages.py",
            ),
        },
        {
            "STATE_ID": "S_RCI_ABOVE_NEG80",
            "FAMILY": "F3_RCI_MOMENTUM",
            "AVAILABLE": True,
            "SOURCE_PATH": "src/research/simple_tech_entry_family/stages.py",
            "SOURCE_FUNCTION": "reversal_rci standing region (rci9[i] > RCI_CROSS_LEVEL)",
            "EXACT_DEFINITION": (
                "rci9[i] > RCI_CROSS_LEVEL with finite rci9; "
                "rci9 = indicators.rci_series(close, RCI_PERIOD=9); "
                f"RCI_CROSS_LEVEL={float(RCI_CROSS_LEVEL)}. "
                "Level state, not the one-bar cross pulse reversal_rci."
            ),
            "TIMEFRAME": "1m completed bars",
            "EVENT_TIMESTAMP_SEMANTICS": EVENT_TS,
            "LOOKBACK": int(RCI_PERIOD),
            "THRESHOLD_SOURCE": (
                "src/research/simple_tech_entry_family/__init__.py "
                f"RCI_PERIOD={int(RCI_PERIOD)} RCI_CROSS_LEVEL={float(RCI_CROSS_LEVEL)}"
            ),
            "THRESHOLD_TUNED_THIS_RUN": False,
            "SELECTION_REASON": (
                "Only preexisting RCI level. rci9>0 would be a new threshold. "
                "reversal_rci is a pulse (cannot persist). Standing region rci9>-80 is the "
                "canonical post-cross momentum state used with that level."
            ),
            "PNL_USED": False,
            "SOURCE_SHA256": _sha(
                "src/research/simple_tech_entry_family/stages.py",
                "src/research/simple_tech_entry_family/indicators.py",
                "src/research/simple_tech_entry_family/__init__.py",
            ),
        },
        {
            "STATE_ID": "S_VOL_CONFIRM_1M",
            "FAMILY": "F4_VOLUME_ACTIVITY",
            "AVAILABLE": True,
            "SOURCE_PATH": "src/research/simple_tech_entry_family/stages.py",
            "SOURCE_FUNCTION": "volume_confirm",
            "EXACT_DEFINITION": (
                "volume[i] >= VOLUME_MULT * median(volume[i-VOLUME_MEDIAN_BARS:i]) "
                f"with finite prior window; VOLUME_MEDIAN_BARS={int(VOLUME_MEDIAN_BARS)}, "
                f"VOLUME_MULT={float(VOLUME_MULT)}. Current bar excluded from median."
            ),
            "TIMEFRAME": "1m completed bars",
            "EVENT_TIMESTAMP_SEMANTICS": EVENT_TS,
            "LOOKBACK": int(VOLUME_MEDIAN_BARS),
            "THRESHOLD_SOURCE": (
                "src/research/simple_tech_entry_family/__init__.py "
                f"VOLUME_MEDIAN_BARS={int(VOLUME_MEDIAN_BARS)} VOLUME_MULT={float(VOLUME_MULT)}"
            ),
            "THRESHOLD_TUNED_THIS_RUN": False,
            "SELECTION_REASON": (
                "Named canonical VOLUME_ACTIVITY ENTRY predicate in Simple-Tech V1. "
                "Not T1 volume_percentile_60s 0.648... (10s Participation/Dynamic Anchor, closed). "
                "Not E2's Volume>median10 fragment (unnamed, AND with Close>prevClose)."
            ),
            "PNL_USED": False,
            "SOURCE_SHA256": _sha(
                "src/research/simple_tech_entry_family/stages.py",
                "src/research/simple_tech_entry_family/__init__.py",
            ),
        },
        {
            "STATE_ID": "S_CLOSE_ABOVE_VWAP",
            "FAMILY": "F5_VWAP_LOCATION",
            "AVAILABLE": True,
            "SOURCE_PATH": "src/research/simple_tech_entry_family/stages.py",
            "SOURCE_FUNCTION": "attach_indicators.vwap; Close[i] > VWAP[i] as used in simple_full _state_e1",
            "EXACT_DEFINITION": (
                "Close[i] > VWAP[i] with finite close and vwap. "
                "VWAP[i] = cumulative (close*volume)/sum(volume) through completed bar i "
                "as computed in attach_indicators (same recurrence as "
                "new_entry_breakout_continuation_v1.rule.session_vwap when vwap_num is unused)."
            ),
            "TIMEFRAME": "1m completed bars",
            "EVENT_TIMESTAMP_SEMANTICS": EVENT_TS,
            "LOOKBACK": 0,
            "THRESHOLD_SOURCE": "none (inequality vs session VWAP; no extra threshold)",
            "THRESHOLD_TUNED_THIS_RUN": False,
            "SELECTION_REASON": (
                "Simplest preexisting VWAP location binary. E4 first-cross "
                "(Close[i-1]<VWAP[i-1] AND Close[i]>VWAP[i]) is a transition pulse, not the "
                "standing location. Quote-inferred VWAP from VCIE unused (integrity-closed)."
            ),
            "PNL_USED": False,
            "SOURCE_SHA256": _sha(
                "src/research/simple_tech_entry_family/stages.py",
                "src/research/simple_full_strategy_discovery_v1/entries.py",
                "src/research/new_entry_breakout_continuation_v1/rule.py",
            ),
        },
    ]
    for r in rows:
        r["STATE_FAMILY_UNAVAILABLE"] = False
        r["INTEGRITY_ISSUE"] = False
        r["LOOKAHEAD"] = False
        r["CAPTURE_CAUSAL"] = True
    return rows


def available_states(registry: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = registry if registry is not None else build_state_registry()
    return [r for r in rows if r.get("AVAILABLE")]
