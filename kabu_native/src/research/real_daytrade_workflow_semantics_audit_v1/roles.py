"""One primary role per context. Descriptive, not profitability-selected."""
from __future__ import annotations

from typing import Any


def role_map() -> list[dict[str, Any]]:
    return [
        {"item": "gap vs prior close", "primary_role": "SELECTION", "also": "BIAS", "example": "Large gap + relative volume is why the name is on the desk, not a 10m return predictor."},
        {"item": "opening activity / relative TradingValue", "primary_role": "SELECTION", "also": "CONFIRMATION", "example": "Is this name in play today versus its own clock-normalized tape."},
        {"item": "sector / peer leadership", "primary_role": "SELECTION", "also": "BIAS", "example": "Peers already moved; target may still be the trade, or already too late."},
        {"item": "news / catalyst", "primary_role": "SELECTION", "available": "UNAVAILABLE", "example": "Would explain why this stock today. Not replaceable by SMA state."},
        {"item": "daily SMA5/25/75", "primary_role": "BIAS", "also": "LOCATION", "example": "Daily stack and SMA25 location are higher-timeframe bias, not a 1m trigger."},
        {"item": "prior day high/low/close", "primary_role": "LOCATION", "also": "BIAS", "example": "PDH/PDL/PDC are places the tape is expected to react."},
        {"item": "opening range 5/15", "primary_role": "SETUP", "also": "LOCATION", "example": "OR hold vs fail is an opening playbook, not an MA period."},
        {"item": "VWAP", "primary_role": "LOCATION", "also": "INVALIDATION", "example": "Intraday fair price. Reclaim/reject is location, not a standalone complete strategy."},
        {"item": "frozen swing S/R", "primary_role": "LOCATION", "also": "INVALIDATION", "example": "Face-valid zones mark where inventory previously defended. Not a reason to buy the name."},
        {"item": "5-minute price action", "primary_role": "SETUP", "example": "Intraday structure: impulse, pullback, range, failed break."},
        {"item": "intraday MA (short, optional)", "primary_role": "LOCATION", "example": "A 5m SMA5/20 can be a local pullback rail. It is not daily SMA75."},
        {"item": "1-minute price action", "primary_role": "TRIGGER", "example": "Timing only after selection, bias, location, and setup exist."},
        {"item": "1-minute SMA5/25/75", "primary_role": "CONFIRMATION", "belongs_in_common_playbook": False, "example": "Some scalpers use it. It is not the ordinary JP daytrade structure layer."},
        {"item": "volume / TradingValue contraction-expansion", "primary_role": "CONFIRMATION", "example": "Confirms whether a location reaction has participation. Not a substitute for location."},
        {"item": "pullback extreme / broken location", "primary_role": "INVALIDATION", "example": "The structural stop is the reason the setup is false, not a fixed -20 bps."},
        {"item": "measured move / opposite location / session flatten", "primary_role": "TARGET / MANAGEMENT", "example": "PDH, VWAP, OR high, or flatten 15:20. Not a +40 bps grid."},
        {"item": "5m SMA5>SMA25>SMA75 stack as tested", "primary_role": "SETUP", "face_valid_as_standard_usage": False, "example": "This was a mechanical stand-in for daily 5/25/75 on the wrong timeframe."},
    ]
