"""Pre-known JPX sector peers. No performance selection. Target excluded."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.cross_sectional_peer_propagation_discovery_v1 import PEER_MIN_N


def sector_of_map(bind: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        out[str(sym)] = str(row.get("tse33_name") or row.get("sector33_name") or row.get("sector17_name") or "")
    return out


def peer_universe(symbols: list[str], sector_of: dict[str, str]) -> dict[str, Any]:
    by_sec: dict[str, list[str]] = defaultdict(list)
    for s in symbols:
        by_sec[sector_of.get(str(s), "") or ""].append(str(s))
    rows = []
    valid_n = 0
    for s in symbols:
        sec = sector_of.get(str(s), "") or ""
        peers = [x for x in by_sec.get(sec, []) if x != str(s)]
        ok = len(peers) >= int(PEER_MIN_N)
        valid_n += int(ok)
        rows.append(
            {
                "target": str(s),
                "sector": sec,
                "peers": peers,
                "peer_n": len(peers),
                "valid": ok,
            }
        )
    return {
        "rows": rows,
        "by_target": {r["target"]: r for r in rows},
        "by_sector": {k: list(v) for k, v in by_sec.items()},
        "sector_n": len(by_sec),
        "valid_target_n": valid_n,
        "peer_min_n": int(PEER_MIN_N),
        "performance_selected": False,
        "future_returns_used": False,
    }
