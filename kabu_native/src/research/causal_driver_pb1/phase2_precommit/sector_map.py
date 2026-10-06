"""Bind TSE33 labels for the 105 universe before any outcome is viewed."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from research.causal_driver_pb1.datasets.universe import load_research_observation_universe
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase2_precommit import EXPECTED_UNIVERSE_N, SECTOR_MIN_CONSTITUENTS
from research.causal_driver_pb1.phase2_precommit.isolation import UNIVERSE_MANIFEST


def _slug(sector_id: str) -> str:
    return f"TSE33_{sector_id}_EQW"


def bind_sector_mapping() -> dict[str, Any]:
    uni = load_research_observation_universe()
    doc = json.loads(UNIVERSE_MANIFEST.read_text(encoding="utf-8"))
    union = list(((doc.get("pool") or {}).get("union")) or [])
    by_sym = {str(r.get("symbol")): r for r in union if isinstance(r, dict) and r.get("symbol")}
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for sym in uni.ordered_symbols:
        rec = by_sym.get(sym) or {}
        sector_id = str(rec.get("tse33_code") or "").strip()
        sector_name = str(rec.get("tse33_name") or "").strip()
        if not sector_id or not sector_name:
            missing.append(sym)
        rows.append(
            {
                "symbol": sym,
                "sector_id": sector_id,
                "sector_name": sector_name,
                "mapping_source": "daytrade_historical_research_foundation_v2.research_pool_manifest.pool.union",
            }
        )
    mapping_sha = sha256_obj(
        [{"symbol": r["symbol"], "sector_id": r["sector_id"], "sector_name": r["sector_name"]} for r in rows]
    )
    counts = Counter((r["sector_id"], r["sector_name"]) for r in rows if r["sector_id"])
    eligible_sectors: list[dict[str, Any]] = []
    ineligible_sectors: list[dict[str, Any]] = []
    for (sid, name), n in sorted(counts.items(), key=lambda x: (-x[1], x[0][0])):
        item = {
            "sector_id": sid,
            "sector_name": name,
            "constituent_n": n,
            "target_scope": _slug(sid),
            "eligible": n >= SECTOR_MIN_CONSTITUENTS,
        }
        if item["eligible"]:
            eligible_sectors.append(item)
        else:
            ineligible_sectors.append(item)
    target_scopes = ["MKT105_EQW"] + [s["target_scope"] for s in eligible_sectors]
    return {
        "pass": uni.symbol_count == EXPECTED_UNIVERSE_N and not missing,
        "universe105_count": uni.symbol_count,
        "universe105_sha256": uni.source_sha256,
        "universe105_symbols_order_sha256": uni.symbols_order_sha256,
        "sector_mapping_sha256": mapping_sha,
        "mapping_source": "research_pool_manifest.pool.union tse33_code/tse33_name as-of pool freeze",
        "rows": rows,
        "missing_sector_label_symbols": missing,
        "sector_n": len(counts),
        "eligible_sector_n": len(eligible_sectors),
        "eligible_sectors": eligible_sectors,
        "ineligible_sectors": ineligible_sectors,
        "target_scopes": target_scopes,
        "price_weighted": False,
        "equal_weight": True,
        "old_usdjpy_winners_used": False,
        "dynamic40_used": False,
        "pb1_candidates_used": False,
    }
