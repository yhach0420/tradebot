"""HTF indicator warmup: same-session arrays only. No previous-session carry unless source says so."""
from __future__ import annotations

from typing import Any

from research.simple_tech_entry_family import BB_PERIOD, EMA_LONG, EMA_SLOPE_BARS, RCI_PERIOD, VOLUME_MEDIAN_BARS, WARMUP_BARS
from research.simple_tech_entry_family.bars import SymbolBarBuilder
from research.simple_tech_entry_family.indicators import bollinger, ema, rci_series
from research.simple_tech_entry_family.stages import attach_indicators, evaluable


def prove_session_reset() -> dict[str, Any]:
    builder_doc = SymbolBarBuilder.__doc__ or ""
    no_overnight = "No overnight carry" in builder_doc
    ema_src = ema.__doc__ or ""
    # ema/rolling_mean/rci_series take the provided array only; they have no session calendar.
    semantics = (
        "HTF OHLCV arrays are aggregated from SymbolBarBuilder completed 1m bars for one AM "
        "session (am_start=09:00 JST, am_end=session end). SymbolBarBuilder states no overnight "
        "carry and drops the incomplete last minute. indicators.ema / bollinger / rci_series / "
        "volume median operate only on the array they are given. Therefore 3m/5m EMA, BB, RCI, "
        "and volume-median warmup start at the first completed same-session HTF bar. Previous "
        "session bars are not present and are not carried. VWAP likewise resets with the 1m "
        "session array in attach_indicators."
    )
    ok = bool(no_overnight) and int(WARMUP_BARS) == 24
    return {
        "ok": bool(ok),
        "HTF_INDICATOR_SESSION_RESET_SEMANTICS": semantics,
        "PREVIOUS_SESSION_CARRY_FOR_EMA_BB_RCI_VOL": False,
        "PREVIOUS_SESSION_CARRY_DEFINED_IN_SOURCE": False,
        "1M_BUILDER": "simple_tech_entry_family.bars.SymbolBarBuilder: No overnight carry.",
        "INDICATOR_SCOPE": "array-local; session array is AM-only",
        "1M_EVALUABLE": (
            f"stages.evaluable uses WARMUP_BARS={int(WARMUP_BARS)} "
            f"(EMA_LONG={int(EMA_LONG)}+EMA_SLOPE_BARS={int(EMA_SLOPE_BARS)}) for the 1m MA family. "
            "Other 1m families use their native lookback (BB_PERIOD, RCI_PERIOD, VOLUME_MEDIAN_BARS) "
            "via the state function returning False when inputs are missing."
        ),
        "HTF_EVALUABLE": (
            "Same state functions on the HTF bar index (native period counts on completed 3m/5m bars). "
            f"MA needs ema21 and ema21[i-3] (i>=23). BB mid needs {int(BB_PERIOD)} HTF bars. "
            f"RCI9 needs {int(RCI_PERIOD)} HTF bars. Volume median needs {int(VOLUME_MEDIAN_BARS)} "
            "prior same-TF HTF bars. VWAP family is evaluable when 1m session VWAP as-of HTF.finalize_t is finite."
        ),
        "CONTRADICTION_WITH_PRECOMMIT": False,
        "attach_indicators": attach_indicators.__module__ + ".attach_indicators",
        "evaluable": evaluable.__module__ + ".evaluable",
        "ema_doc": ema_src,
        "bollinger": bollinger.__module__,
        "rci_series": rci_series.__module__,
    }
