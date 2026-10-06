"""Frozen 105 and SECTOR_3650 target sets. No ranking, no Dynamic40."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.datasets.universe import load_research_observation_universe
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.causal_driver_pb1.sector_breadth_precommit import EXPECTED_SECTOR_MAPPING_SHA256, EXPECTED_UNIVERSE_SHA256
from research.causal_driver_pb1.sector_state_transmission_precommit import GLOBAL_TARGET_N, SECTOR3650_TARGET_N


def bind_targets() -> dict[str, Any]:
    blockers: list[str] = []
    uni = load_research_observation_universe()
    sectors = bind_sector_mapping()
    symbols = [str(s) for s in uni.ordered_symbols]
    if len(symbols) != GLOBAL_TARGET_N or uni.symbol_count != GLOBAL_TARGET_N:
        blockers.append("GLOBAL_TARGET_N_MISMATCH")
    if uni.source_sha256 != EXPECTED_UNIVERSE_SHA256:
        blockers.append("UNIVERSE_SHA_MISMATCH")
    if sectors.get("sector_mapping_sha256") != EXPECTED_SECTOR_MAPPING_SHA256:
        blockers.append("SECTOR_MAPPING_SHA_MISMATCH")
    rows = list(sectors.get("rows") or [])
    sector_of = {str(r["symbol"]): str(r["sector_id"]) for r in rows}
    if [str(r["symbol"]) for r in rows] != symbols:
        blockers.append("SECTOR_ROW_ORDER_MISMATCH")
    s3650 = [s for s in symbols if sector_of.get(s) == "3650"]
    if len(s3650) != SECTOR3650_TARGET_N:
        blockers.append("SECTOR3650_TARGET_N_MISMATCH")
    if any(not sector_of.get(s) for s in symbols):
        blockers.append("MISSING_SECTOR_LABEL")
    global_sha = sha256_obj({"namespace": "TRANSMISSION_GLOBAL_TARGET_SET_V1", "symbols": symbols})
    sector_sha = sha256_obj({"namespace": "TRANSMISSION_SECTOR3650_TARGET_SET_V1", "symbols": s3650})
    return {
        "pass": not blockers,
        "blockers": blockers,
        "symbols": symbols,
        "sector_of": sector_of,
        "sector3650_symbols": s3650,
        "global_target_n": len(symbols),
        "sector3650_target_n": len(s3650),
        "global_target_set_sha256": global_sha,
        "sector3650_target_set_sha256": sector_sha,
        "universe105_sha256": uni.source_sha256,
        "sector_mapping_sha256": sectors.get("sector_mapping_sha256"),
        "dynamic40_not_used": True,
        "concentration_identities_not_used_for_target_selection": True,
    }
