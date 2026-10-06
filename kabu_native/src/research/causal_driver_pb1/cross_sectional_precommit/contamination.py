"""Peer/SR historical knowledge is allowed only as existence. No winner reuse."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj

ALLOWED = (
    "historical knowledge that a previous different peer/SR mechanism existed",
    "last-completed as-of resolver from USDJPY contract-correct rerun",
    "BAR_START timestamp semantics",
    "frozen 105 universe identity",
    "TSE33 sector labels as-of pool freeze",
)
FORBIDDEN_NOT_USED = (
    "PEER_PROPAGATION leader names",
    "PEER_PROPAGATION sector names",
    "PEER_PROPAGATION lookbacks",
    "PEER_PROPAGATION horizons",
    "PEER_PROPAGATION directions",
    "PEER_PROPAGATION thresholds",
    "PEER_PROPAGATION target names",
    "USDJPY D3 near-misses",
    "USDJPY observed beta/p/q",
    "PB1 performance",
    "C1 USDJPY outcomes",
    "individual symbol winner selection",
)


def contamination_ledger() -> dict[str, Any]:
    payload = {
        "old_peer_verdict_referenced": "PEER_PROPAGATION_REAL_BUT_EDGE_CONSUMED_V1",
        "used_as": "existence_of_a_different_prior_peer_sr_mechanism_only",
        "claimed_first_look": False,
        "allowed": list(ALLOWED),
        "forbidden_not_embedded": list(FORBIDDEN_NOT_USED),
        "leader_names_from_old_peer": False,
        "sector_names_from_old_peer": False,
        "lookbacks_from_old_peer": False,
        "horizons_from_old_peer": False,
        "directions_from_old_peer": False,
        "thresholds_from_old_peer": False,
        "target_names_from_old_peer": False,
        "usdjpy_reopened": False,
        "leader_laggard_outcomes_opened": False,
        "alpha_created": False,
        "pb1_bound": False,
        "complete_strategy_run": False,
    }
    payload["sha256"] = sha256_obj(payload)
    return payload
