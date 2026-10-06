"""Exact 270-hypothesis family and leave-target-out construction. No betas."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_state_transmission_precommit import FAMILY_N
from research.causal_driver_pb1.sector_state_transmission_precommit.parent import MECHANISMS


def build_family(*, symbols: list[str], sector_of: dict[str, str], sector3650_symbols: list[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    lto_fail: list[str] = []
    s3650 = set(sector3650_symbols)
    symbol_set = set(symbols)
    for mech in MECHANISMS:
        targets = symbols if mech["global"] else list(sector3650_symbols)
        scope = symbol_set if mech["global"] else s3650
        for sym in targets:
            driver_members = scope - {sym}
            self_in = sym in driver_members
            if self_in or sym not in scope:
                lto_fail.append(f"{mech['parent_mechanism_id']}|{sym}")
            driver_id = "GLOBAL_BREADTH_EX_TARGET" if mech["global"] else "SECTOR3650_BREADTH_EX_TARGET"
            rows.append(
                {
                    "hypothesis_id": f"{mech['parent_mechanism_id']}|{sym}",
                    "parent_mechanism_id": mech["parent_mechanism_id"],
                    "parent_test_id": mech["test_id"],
                    "target_symbol": sym,
                    "target_sector": sector_of.get(sym),
                    "direction": "UP",
                    "metric": "BREADTH",
                    "lookback": int(mech["lookback"]),
                    "horizon": int(mech["horizon"]),
                    "scope_id": mech["scope_id"],
                    "global": bool(mech["global"]),
                    "driver_id": driver_id,
                    "driver_n": len(driver_members),
                    "TARGET_SELF_IN_DRIVER": bool(self_in),
                }
            )
    digest = sha256_obj(
        {
            "namespace": "SECTOR_STATE_SYMBOL_TRANSMISSION_FAMILY_V1",
            "n": len(rows),
            "hypotheses": rows,
        }
    )
    return {
        "pass": len(rows) == FAMILY_N and not lto_fail and all(r["TARGET_SELF_IN_DRIVER"] is False for r in rows),
        "family_n": len(rows),
        "family_sha256": digest,
        "rows": rows,
        "lto_fail": lto_fail,
        "all_target_self_in_driver_false": all(r["TARGET_SELF_IN_DRIVER"] is False for r in rows),
    }
