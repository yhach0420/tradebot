"""Canonical day-trade decision stack and at most three untested candidates."""
from __future__ import annotations

from typing import Any


def canonical_workflow() -> dict[str, Any]:
    return {
        "stack": [
            {"step": "WHY THIS STOCK TODAY", "uses": ["gap", "opening activity", "relative TradingValue", "sector/peer leadership", "daily volatility"], "unavailable": ["news/catalyst", "historical futures tape"]},
            {"step": "HIGHER-TIMEFRAME BIAS", "uses": ["daily SMA5/25/75", "daily swing structure", "prior close / gap direction"]},
            {"step": "IMPORTANT PRICE LOCATION", "uses": ["PDH/PDL/PDC", "VWAP", "opening range", "face-valid S/R", "optional daily SMA25"]},
            {"step": "INTRADAY SETUP", "uses": ["5m or 15m price action at that location: pullback, break-accept, false-break reclaim"]},
            {"step": "ENTRY TRIGGER", "uses": ["1m turn: reclaim, failed break, hold of location"]},
            {"step": "EXECUTION", "uses": ["next 1m open approximation; cannot claim queue/bid-ask"]},
            {"step": "STRUCTURAL INVALIDATION", "uses": ["location break / pullback extreme, not a fixed bps stop"]},
            {"step": "TARGET / EXIT", "uses": ["opposite location, VWAP, session flatten 15:20"]},
        ],
        "not_a_strategy": True,
        "do_not_skip_to_trigger": True,
    }


def playbook_candidates() -> list[dict[str, Any]]:
    return [
        {
            "id": "PB1_GAP_RVOL_OPENING_RANGE_V1",
            "why_ordinary": "Standard cash-equity daytrade: the name is in play at the open because of gap and tape, then OR15 hold/fail.",
            "stock_selection": "Gap vs prior close AND clock-normalized opening TradingValue expansion. News UNAVAILABLE — do not proxy with MA.",
            "higher_timeframe": "Daily SMA stack and gap direction as bias only.",
            "location": "OR15 high/low, prior close, VWAP.",
            "setup_sequence": "Opening impulse → OR complete → hold or false-break of OR.",
            "trigger": "1m acceptance back inside OR or 1m hold of OR extreme.",
            "invalidation": "Opposite OR extreme through on a completed 1m close.",
            "target_logic": "VWAP then PDH/PDL or session flatten.",
            "data_availability": "gap/TV/OR/VWAP/PDH AVAILABLE; news and futures UNAVAILABLE",
            "not_tested_here": True,
        },
        {
            "id": "PB2_DAILY_SMA_BIAS_AT_DAILY_SMA25_LOCATION_V1",
            "why_ordinary": "This is the actual 5/25/75 convention: daily trend and daily SMA25 as dynamic location, not 5-minute SMA75.",
            "stock_selection": "Name already in play (gap/RVOL) OR daily range expansion. Daily SMA used for bias, not as the selection itself.",
            "higher_timeframe": "Daily SMA5>SMA25>SMA75 (bull) with live-causal daily SMA during the session.",
            "location": "Price at/into daily SMA25 band, and/or PDH/PDL confluence.",
            "setup_sequence": "Daily trend intact → pullback toward daily SMA25 → 5m/15m stop of countertrend progress.",
            "trigger": "1m reclaim of 5m structure or 1m break of minor swing in trend direction. Not a 5m candle wait.",
            "invalidation": "Completed daily close through SMA75 against DIR, or 5m close through the pullback extreme.",
            "target_logic": "Prior day extreme, VWAP, or flatten.",
            "data_availability": "daily OHLCV from native 1m AVAILABLE",
            "not_tested_here": True,
            "not_a_period_search": True,
        },
        {
            "id": "PB3_VWAP_OR_PRIOR_DAY_LOCATION_REACTION_V1",
            "why_ordinary": "kabu STATION exposes VWAP and prior-day fields. Face-valid S/R already separated path as LOCATION. Ordinary reaction at a watched level.",
            "stock_selection": "In-play name (same as PB1). Do not fire on quiet tape.",
            "higher_timeframe": "Daily bias must agree with the reaction direction.",
            "location": "VWAP and/or PDH/PDL / face-valid zone. One location, not an indicator zoo.",
            "setup_sequence": "Arrive at location → fail to continue through → reclaim or hold.",
            "trigger": "1m reclaim/hold after the location is known.",
            "invalidation": "Completed 1m close through the location against DIR.",
            "target_logic": "Return to VWAP or the other prior-day extreme; flatten 15:20.",
            "data_availability": "VWAP/PDH/PDL/S/R AVAILABLE; do not revive R11",
            "not_tested_here": True,
        },
    ]


def validation_framework() -> dict[str, Any]:
    return {
        "A_FACE_VALIDITY": "Does the event look like the intended chart setup on daily + 15m/5m + 1m with only causal information?",
        "B_CAUSAL_KNOWABILITY": "Was every input known before decision time? No future 5m close, no same-bar entry, no lookahead cluster.",
        "C_INCREMENTAL_INFORMATION": "Does the setup add information vs a control that is not the setup itself, without matching away mediators?",
        "D_TRADE_UTILITY": "From next-open execution, is the absolute path (not only vs a worse control) acceptable after structural risk and 8bps as stress, not as a selection tax unless labeled?",
        "do_not_substitute": True,
        "relative_edge_with_negative_absolute_is_not_enough": True,
    }


def execution_limits() -> dict[str, Any]:
    return {
        "minute_ohlcv_can_test": ["causal chart setup", "next-bar open approximation", "path MFE/MAE/structural invalidation"],
        "minute_ohlcv_cannot_prove": ["historical queue position", "true bid/ask fill", "order-book interaction", "latency microstructure"],
        "unless_those_data_exist": True,
        "do_not_overclaim": True,
    }


def absolute_gate() -> dict[str, Any]:
    return {
        "must_report": ["absolute next-open 5/10/20m", "MFE/MAE", "structural risk", "MFE/risk", "signal-to-entry", "execution cost feasibility"],
        "must_not_advance_only_because": "treatment > bad control",
        "mtf_lesson": "A vs B 10m signs could be positive while A absolute 10m was negative and D3 path incoherent.",
    }
