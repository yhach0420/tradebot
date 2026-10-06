"""Canonical DEV candidate freeze. Namespaced so empty != generic empty SHA."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_breadth_discovery import EXPECTED_PRECOMMIT_SHA256, INVALID_EMPTY_SHA256


def freeze_candidates(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    cands = []
    for r in sorted(
        rows,
        key=lambda x: (str(x["metric"]), str(x["scope_id"]), int(x["lookback"]), int(x["horizon"])),
    ):
        cands.append(
            {
                "metric": str(r["metric"]),
                "scope_id": r["scope_id"],
                "mechanism": r["mechanism"],
                "sector_id": r.get("sector_id"),
                "lookback": int(r["lookback"]),
                "horizon": int(r["horizon"]),
                "direction": r.get("direction"),
                "DEV_beta_primary": float(r["b_fx"]),
                "DEV_beta_60": None if r.get("beta_60") is None else float(r["beta_60"]),
                "DEV_CI": [float(r["ci_lo"]), float(r["ci_hi"])],
                "DEV_p": None if r.get("p_boot") is None else float(r["p_boot"]),
                "DEV_q_value": float(r["q"]),
                "Q_boundaries": r.get("quintile_boundaries"),
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
        "family": "SECTOR_BREADTH_DISPERSION_V1",
        "precommit_sha256": EXPECTED_PRECOMMIT_SHA256,
        "candidates": cands,
    }
    sha = sha256_obj(payload)
    if sha == INVALID_EMPTY_SHA256:
        raise RuntimeError("candidate_freeze_reused_generic_empty_sha")
    return cands, sha
