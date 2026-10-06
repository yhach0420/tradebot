"""Sector-control regime from N_PEER_PIT only. No betas and no outcome selection."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1 import (
    BLOCKED_SYMBOLS,
    CONTROL_CONTRACT_ID,
    FAMILY_SERIALIZATION_SHA256,
)

REGIME_MULTI = "MULTI_PEER"
REGIME_SINGLE = "SINGLE_PEER"
REGIME_NONE = "NO_PEER"


def regime_from_n_peer(n_peer: int) -> str:
    n = int(n_peer)
    if n >= 2:
        return REGIME_MULTI
    if n == 1:
        return REGIME_SINGLE
    if n == 0:
        return REGIME_NONE
    raise ValueError("n_peer_negative")


def peer_control_ok(listed_m: np.ndarray, past_m: np.ndarray) -> np.ndarray:
    """Date-level sector-control availability. past_m means listed and fresh at both endpoints.

    N_PEER_PIT >= 2: valid_n >= 2 and valid_n / N_PEER_PIT >= 0.80.
    N_PEER_PIT == 1: the sole listed peer must itself be fresh. No zero-fill.
    N_PEER_PIT == 0: sector regressor omitted, so the control does not fail the row.
    """
    listed_i = np.asarray(listed_m, dtype=np.int16)
    past_i = np.asarray(past_m, dtype=np.int16)
    n_peer = listed_i.sum(axis=0)[None, :] - listed_i
    n_valid = past_i.sum(axis=0)[None, :] - past_i
    multi = (n_peer >= 2) & (n_valid >= 2) & (n_valid >= 0.80 * n_peer)
    single = (n_peer == 1) & (n_valid == 1)
    none = n_peer == 0
    return multi | single | none


def assign_structural_regimes(*, symbols: list[str], sector_of: dict[str, str]) -> dict[str, Any]:
    members: dict[str, list[str]] = {}
    for sym in symbols:
        members.setdefault(str(sector_of[sym]), []).append(sym)
    rows = []
    for sym in symbols:
        sid = str(sector_of[sym])
        peers = [p for p in members[sid] if p != sym]
        regime = regime_from_n_peer(len(peers))
        if sym in peers:
            raise RuntimeError("target_in_own_sector_control")
        rows.append(
            {
                "target_symbol": sym,
                "target_sector": sid,
                "sector_constituent_n": len(members[sid]),
                "pool_n_peer_pit": len(peers),
                "control_regime": regime,
                "sole_peer": peers[0] if regime == REGIME_SINGLE else None,
                "sector_control_status": "NOT_APPLICABLE" if regime == REGIME_NONE else "APPLICABLE",
                "counts_toward_global_standard_coverage": regime == REGIME_MULTI,
            }
        )
    by_symbol = {r["target_symbol"]: r for r in rows}
    blocked = [s for s in BLOCKED_SYMBOLS if by_symbol[s]["control_regime"] == REGIME_MULTI]
    missing = [s for s in BLOCKED_SYMBOLS if s not in by_symbol]
    peer_structure = []
    for sid in sorted(members):
        n = len(members[sid])
        peer_structure.append(
            {
                "sector_id": sid,
                "constituent_n": n,
                "pool_n_peer_pit": n - 1,
                "control_regime": regime_from_n_peer(n - 1),
                "symbols": list(members[sid]),
            }
        )
    return {
        "rows": rows,
        "by_symbol": by_symbol,
        "peer_structure": peer_structure,
        "multi_peer_targets": [r["target_symbol"] for r in rows if r["control_regime"] == REGIME_MULTI],
        "single_peer_targets": [r["target_symbol"] for r in rows if r["control_regime"] == REGIME_SINGLE],
        "no_peer_targets": [r["target_symbol"] for r in rows if r["control_regime"] == REGIME_NONE],
        "blocked_symbols_resolved_as_non_multi": not blocked and not missing and len(BLOCKED_SYMBOLS) == 17,
        "blocked_still_multi": blocked,
        "blocked_missing": missing,
    }


def hypothesis_identity_rows(family_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "parent_mechanism_id": r["parent_mechanism_id"],
            "target_symbol": r["target_symbol"],
            "lookback": int(r["lookback"]),
            "horizon": int(r["horizon"]),
            "direction": r["direction"],
        }
        for r in family_rows
    ]


def family_hypothesis_sha256(family_rows: list[dict[str, Any]]) -> str:
    return sha256_obj(
        {
            "namespace": "SECTOR_STATE_SYMBOL_TRANSMISSION_HYPOTHESIS_IDENTITY_V1",
            "hash_fields": ["parent_mechanism_id", "target_symbol", "lookback", "horizon", "direction"],
            "excludes_control_regime": True,
            "n": len(family_rows),
            "hypotheses": hypothesis_identity_rows(family_rows),
        }
    )


def model_definitions() -> dict[str, Any]:
    return {
        "symbol_control_contract_id": CONTROL_CONTRACT_ID,
        "selection": "N_PEER_PIT_ONLY",
        "no_model_selection_search": True,
        "no_zero_fill": True,
        "no_fabricated_sector_return": True,
        "target_self_control_mandatory": True,
        "market_control_mandatory": True,
        "primary_parameter": "beta_driver",
        "direction": "UP",
        "regimes": {
            REGIME_MULTI: {
                "when": "N_PEER_PIT>=2",
                "sector_control": "SECTOR_EX_TARGET_PAST_5M equal-weight valid peers",
                "require": "valid_peer_n>=2 AND valid_peer_n/N_PEER_PIT>=0.80",
            },
            REGIME_SINGLE: {
                "when": "N_PEER_PIT=1",
                "sector_control": "SINGLE_PEER_SECTOR_CONTROL sole peer causal 5m return",
                "require": "sole peer fresh at both endpoints; valid_peer_n=1; valid_fraction=1.0",
                "stale_sole_peer": "row invalid",
            },
            REGIME_NONE: {
                "when": "N_PEER_PIT=0",
                "sector_control_status": "NOT_APPLICABLE",
                "sector_regressor": "omitted",
            },
        },
        "global_models": {
            REGIME_MULTI: "Y=b_driver*GLOBAL_BREADTH_EX_TARGET+b_self*TARGET_PAST_5M+b_sector*SECTOR_EX_TARGET_PAST_5M+b_market*MKT_EX_SECTOR_PAST_5M+minute_FE",
            REGIME_SINGLE: "Y=b_driver*GLOBAL_BREADTH_EX_TARGET+b_self*TARGET_PAST_5M+b_peer*SINGLE_PEER_SECTOR_CONTROL+b_market*MKT_EX_SECTOR_PAST_5M+minute_FE",
            REGIME_NONE: "Y=b_driver*GLOBAL_BREADTH_EX_TARGET+b_self*TARGET_PAST_5M+b_market*MKT_EX_SECTOR_PAST_5M+minute_FE",
        },
        "sector3650_model_unchanged": "Y=b_driver*SECTOR3650_BREADTH_EX_TARGET+b_self*TARGET_PAST_5M+b_sector*SECTOR3650_EX_TARGET_PAST_5M+b_market*MKT_EX_3650_PAST_5M+minute_FE",
        "small_sector_firewall": {
            "SINGLE_PEER_and_NO_PEER_may_be_evaluated": True,
            "cannot_rescue_global_parent_coverage": True,
            "STANDARD_CONTROL": "pool N_PEER_PIT>=2",
            "GLOBAL_standard_confirmed_ge": 11,
            "GLOBAL_distinct_sectors_ge": 4,
            "GLOBAL_max_sector_share": 0.40,
            "SECTOR_3650_confirmed_ge": 5,
        },
        "bh_discovery_m": 270,
        "hashing_semantics": {
            "family_serialization_sha256": "blocked V1 family row serialization; driver construction included; control regime excluded",
            "family_serialization_sha256_value": FAMILY_SERIALIZATION_SHA256,
            "family_hypothesis_sha256": "parent_mechanism_id, target_symbol, lookback, horizon, direction only",
            "model_contract_sha256": "regime formulas and firewall; no symbol assignment",
            "symbol_control_contract_sha256": "model contract plus structural regime assignment for every frozen target",
            "changed_model_is_not_the_hypothesis_identity": True,
        },
    }


def contract_hashes(*, assignment: dict[str, Any], family_rows: list[dict[str, Any]]) -> dict[str, str]:
    model = model_definitions()
    model_sha = sha256_obj(model)
    symbol_payload = {
        "symbol_control_contract_id": CONTROL_CONTRACT_ID,
        "model_contract_sha256": model_sha,
        "assignment": [
            {
                "target_symbol": r["target_symbol"],
                "target_sector": r["target_sector"],
                "pool_n_peer_pit": r["pool_n_peer_pit"],
                "control_regime": r["control_regime"],
                "sole_peer": r["sole_peer"],
                "sector_control_status": r["sector_control_status"],
            }
            for r in assignment["rows"]
        ],
    }
    return {
        "family_hypothesis_sha256": family_hypothesis_sha256(family_rows),
        "model_contract_sha256": model_sha,
        "symbol_control_contract_sha256": sha256_obj(symbol_payload),
        "symbol_control_contract_id": CONTROL_CONTRACT_ID,
    }
