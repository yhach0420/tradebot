"""Market-language acquisition status. No alpha. No ENTRY."""
from __future__ import annotations

from typing import Any

from research.new_causal_information_acquisition_v1 import (
    CASE_FIRST_FULL,
    CASE_IMPL_FAIL,
    CASE_INVALID,
    CASE_MODE_READY,
    CASE_PARTIAL,
    CASE_SET_READY,
    CASE_STARTED,
)


def interpret(body: dict[str, Any]) -> dict[str, Any]:
    d = dict(body.get("decision") or {})
    a = dict(body.get("answers") or {})
    verdict = str(d.get("VERDICT") or "")
    if verdict == CASE_IMPL_FAIL:
        story = "NEW_INFO acquisition implementation did not pass parent pin, standard-config freeze, or the 30-test preflight."
    elif verdict == CASE_FIRST_FULL:
        story = "First FULL NEW_INFO day is on disk: 48 stocks + NK225mini + TOPIX with 08:45-09:00 and 09:00-11:30 dynamic continuity. Not alpha."
    elif verdict == CASE_SET_READY:
        story = "Ten FULL NEW_INFO days are frozen as the first construction set. Next is futures-context causal mechanism discovery, still without a frozen threshold."
    elif verdict == CASE_PARTIAL:
        story = "NEW_INFO raw exists but the day is PARTIAL. Keep the tape. Do not count it in the first 10 FULL-day set."
    elif verdict == CASE_INVALID:
        story = "NEW_INFO live artifacts exist but the day is INVALID (lineage, symbol, sanity, early death, or corruption). Do not use it as Day1."
    elif verdict == CASE_STARTED:
        story = "NEW_INFO capture is in progress. Do not downgrade to MODE_READY."
    else:
        story = (
            "Dedicated Core10+Dynamic38+NK225mini+TOPIX acquisition mode is implemented and isolated. "
            "Standard Paper remains Core10+Dynamic40=50. First FULL live day has not been captured. "
            "Do not claim futures explain stock markout yet."
        )
    return {
        "CASE": verdict or CASE_MODE_READY,
        "MARKET_MECHANISM": story,
        "ENTRY_THESIS": None,
        "OPENING_REVIVED": False,
        "FUTURES_ALPHA_CLAIM": False,
        "VERDICT": verdict,
        "NEXT": d.get("NEXT"),
        "FULL_acquisition_day": a.get("33_FULL_acquisition_day"),
        "submit_cancel_live": a.get("31_submit_cancel_live"),
    }
