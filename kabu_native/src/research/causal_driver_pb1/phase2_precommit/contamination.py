"""Old USDJPY research may inform source semantics only. No winners, thresholds, or PnL."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj

ALLOWED = (
    "Jetta source identity",
    "timestamp semantics BAR_START",
    "Bid/Ask handling from Phase 1",
    "1m native resolution",
)
FORBIDDEN_NOT_USED = (
    "old winning sectors",
    "old 3-symbol list",
    "old response ranking",
    "old direction rule",
    "old threshold",
    "old effect size",
    "old PnL",
    "individual symbol winner selection",
    "symbol blacklist",
    "trade rule",
)


def contamination_ledger() -> dict[str, Any]:
    payload = {
        "old_verdict_referenced": "USDJPY_SECTOR_SPECIFIC_DRIVER_FOUND_V1",
        "claimed_first_look": False,
        "used_as": "source_semantics_only_via_phase1_adapter",
        "allowed": list(ALLOWED),
        "forbidden_not_embedded": list(FORBIDDEN_NOT_USED),
        "all_tse33_sectors_evaluated_same_condition": True,
        "phase2_outcomes_opened": False,
        "alpha_created": False,
        "pb1_bound": False,
        "complete_strategy_run": False,
    }
    payload["sha256"] = sha256_obj(payload)
    return payload
