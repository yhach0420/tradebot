"""Old USDJPY research may inform source semantics only. Economic ranking must not enter the adapter."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj


ALLOWED_FROM_OLD_USDJPY_RESEARCH = (
    "Dukascopy Jetta provider identity",
    "Jetta candle URL family /v1/candles/minute/USD-JPY/{BID|ASK}/Y/M/D",
    "delta-encoded OHLC decode",
    "timestamp semantics BAR_START UTC epoch ms",
    "Bid and Ask as separate 1m streams",
    "available_at = bar_start + 1 minute",
    "canonical conversion UTC → Asia/Tokyo",
)

FORBIDDEN_FROM_OLD_USDJPY_RESEARCH = (
    "winning sectors",
    "winning symbols",
    "response ranking",
    "USDJPY direction / threshold rules",
    "sector-specific profitability",
    "old LONG/SHORT mapping from FX move",
    "PB1 bind",
    "Complete Strategy economics",
)


def contamination_ledger() -> dict[str, Any]:
    payload = {
        "old_verdict_referenced": "USDJPY_SECTOR_SPECIFIC_DRIVER_FOUND_V1",
        "used_as": "source_semantics_evidence_only",
        "allowed": list(ALLOWED_FROM_OLD_USDJPY_RESEARCH),
        "forbidden_not_embedded": list(FORBIDDEN_FROM_OLD_USDJPY_RESEARCH),
        "adapter_embeds_old_economic_ranking": False,
        "adapter_embeds_old_symbol_list": False,
        "adapter_embeds_old_sector_list": False,
        "adapter_embeds_old_threshold": False,
        "direction_from_usdjpy_move": False,
        "phase1_direction": "NEUTRAL",
        "alpha_created": False,
        "pb1_bound": False,
        "complete_strategy_run": False,
        "usdjpy_alpha_tested": False,
        "sector_response_tested": False,
        "symbol_response_tested": False,
    }
    payload["sha256"] = sha256_obj(payload)
    return payload
