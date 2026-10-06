"""Namespaced candidate freeze for the contract-corrected discovery."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_breadth_corrected_discovery import EXPECTED_PRECOMMIT_SHA256, INVALID_EMPTY_SHA256


def freeze_candidates(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    cands = []
    for r in sorted(
        rows,
        key=lambda x: (str(x["metric"]), str(x["scope_id"]), int(x["lookback"]), int(x["horizon"])),
    ):
        qb = r.get("quintile_boundaries") or {}
        cands.append(
            {
                "test_id": r.get("test_id"),
                "metric": str(r["metric"]),
                "scope_id": r["scope_id"],
                "scope": r["scope_id"],
                "mechanism": r["mechanism"],
                "sector_id": r.get("sector_id"),
                "sector": r.get("sector_id"),
                "lookback": int(r["lookback"]),
                "horizon": int(r["horizon"]),
                "direction": r.get("direction"),
                "DEV_beta_primary": float(r["b_fx"]),
                "DEV_beta": float(r["b_fx"]),
                "DEV_beta_60": None if r.get("beta_60") is None else float(r["beta_60"]),
                "strict60_beta": None if r.get("beta_60") is None else float(r["beta_60"]),
                "DEV_CI": [float(r["ci_lo"]), float(r["ci_hi"])],
                "CI": [float(r["ci_lo"]), float(r["ci_hi"])],
                "DEV_p": None if r.get("p_boot") is None else float(r["p_boot"]),
                "p": None if r.get("p_boot") is None else float(r["p_boot"]),
                "DEV_q_value": float(r["q"]),
                "q": float(r["q"]),
                "Q_boundaries": qb,
                "quintile_boundary_identity": {
                    "Q20": qb.get("Q20"),
                    "Q40": qb.get("Q40"),
                    "Q60": qb.get("Q60"),
                    "Q80": qb.get("Q80"),
                    "n": qb.get("n"),
                    "boundary_sha": qb.get("boundary_sha"),
                },
                "D1": bool(r["D1"]),
                "D2": bool(r["D2"]),
                "D3": bool(r["D3"]),
                "D4": bool(r["D4"]),
                "D5": bool(r["D5"]),
                "D6": bool(r["D6"]),
                "D7": bool(r["D7"]),
                "global": bool(r.get("global")),
            }
        )
    payload = {
        "family": "SECTOR_BREADTH_DISPERSION_CONTRACT_CORRECTED_V1",
        "precommit_sha256": EXPECTED_PRECOMMIT_SHA256,
        "candidates": cands,
    }
    sha = sha256_obj(payload)
    if sha == INVALID_EMPTY_SHA256:
        raise RuntimeError("candidate_freeze_reused_generic_empty_sha")
    return cands, sha


def freeze_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
