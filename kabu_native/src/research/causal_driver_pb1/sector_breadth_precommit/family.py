"""Freeze the 384-test cartesian product. No outcomes."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_breadth_precommit import (
    EXPECTED_SECTOR_IDS,
    FAMILY_N,
    HORIZONS,
    LOOKBACKS,
    METRICS,
    SCOPE_N,
)


def build_scopes(eligible_sectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scopes = []
    for s in eligible_sectors:
        scopes.append(
            {
                "scope_id": f"SECTOR_{s['sector_id']}",
                "mechanism": "SECTOR_BREADTH_DISPERSION",
                "sector_id": s["sector_id"],
                "sector_name": s.get("sector_name"),
                "constituent_n": s.get("constituent_n"),
            }
        )
    scopes.append({"scope_id": "GLOBAL_105", "mechanism": "GLOBAL_BREADTH_DISPERSION", "sector_id": None, "sector_name": None, "constituent_n": 105})
    if len(scopes) != SCOPE_N:
        raise RuntimeError("scope_n_mismatch")
    return scopes


def build_family_384(scopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric in METRICS:
        for scope in scopes:
            for w in LOOKBACKS:
                for h in HORIZONS:
                    rows.append(
                        {
                            "test_id": f"{metric}|{scope['scope_id']}|w{w}|h{h}",
                            "metric": metric,
                            "scope_id": scope["scope_id"],
                            "sector_id": scope.get("sector_id"),
                            "lookback": int(w),
                            "horizon": int(h),
                        }
                    )
    if len(rows) != FAMILY_N:
        raise RuntimeError("family_n_mismatch")
    return rows


def family_identity(*, eligible_sectors: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = sorted(eligible_sectors, key=lambda s: str(s["sector_id"]))
    ids = tuple(str(s["sector_id"]) for s in eligible)
    if ids != EXPECTED_SECTOR_IDS:
        raise RuntimeError("eligible_sector_ids_mismatch")
    scopes = build_scopes(eligible)
    tests = build_family_384(scopes)
    return {
        "scopes": scopes,
        "tests": tests,
        "family_n": len(tests),
        "family_384_sha256": sha256_obj(tests),
        "scope_n": len(scopes),
        "metric_n": len(METRICS),
        "lookback_n": len(LOOKBACKS),
        "horizon_n": len(HORIZONS),
    }
