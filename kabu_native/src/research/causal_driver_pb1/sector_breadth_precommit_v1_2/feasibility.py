"""Input-only structural feasibility of MKT_EX gates. No future returns."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from research.causal_driver_pb1.sector_breadth_precommit import EXPECTED_SECTOR_IDS, EXPECTED_UNIVERSE_N
from research.causal_driver_pb1.sector_breadth_precommit_v1_2 import AFFECTED_SECTOR_ID, MKT_EX_MIN_FRAC, OLD_MKT_EX_MIN_N
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.control_gate import (
    new_gate_structurally_possible,
    old_gate_structurally_possible,
    old_required_ex_sector_valid_n,
    required_ex_sector_valid_n,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.isolation import V1_OUT


def load_v1_listing_rows() -> list[dict[str, Any]]:
    path = V1_OUT / "audit.xlsx"
    if not path.is_file():
        return []
    df = pd.read_excel(path, sheet_name="PointInTime_Listing")
    return df.to_dict(orient="records")


def _listed_on(listing_start: str | None, day: str) -> bool:
    if listing_start is None or listing_start == "" or str(listing_start).lower() == "nan":
        return False
    return str(listing_start) <= str(day)


def sector_structural_feasibility(
    *,
    eligible_sectors: list[dict[str, Any]],
    mapping_rows: list[dict[str, Any]],
    development_dates: list[str],
    listing_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    listing_rows = listing_rows if listing_rows is not None else load_v1_listing_rows()
    start = {str(r.get("symbol")): r.get("listing_start") for r in listing_rows}
    sector_of = {str(r["symbol"]): str(r["sector_id"]) for r in mapping_rows}
    dates = list(development_dates)
    matrix = []
    for s in sorted(eligible_sectors, key=lambda x: str(x["sector_id"])):
        sid = str(s["sector_id"])
        n_sec = int(s.get("constituent_n") or 0)
        pool_ex = int(EXPECTED_UNIVERSE_N) - n_sec
        series: list[int] = []
        if listing_rows:
            for day in dates:
                n_ex = 0
                for sym, sec in sector_of.items():
                    if sec == sid:
                        continue
                    if _listed_on(start.get(sym), day):
                        n_ex += 1
                series.append(n_ex)
        else:
            series = [pool_ex] * max(len(dates), 1)
        arr = np.array(series, dtype=np.int32) if series else np.array([pool_ex], dtype=np.int32)
        pit_min = int(arr.min())
        pit_med = int(np.median(arr))
        pit_max = int(arr.max())
        old_req = old_required_ex_sector_valid_n(pit_max)
        new_req_at_max = required_ex_sector_valid_n(pit_max)
        new_req_at_min = required_ex_sector_valid_n(pit_min)
        old_possible = bool(old_gate_structurally_possible(pit_max))
        new_possible = bool(new_gate_structurally_possible(pit_min) and new_gate_structurally_possible(pit_max))
        matrix.append(
            {
                "sector_id": sid,
                "sector_name": s.get("sector_name"),
                "constituent_n": n_sec,
                "pool_ex_sector_n": pool_ex,
                "PIT_ex_sector_n_min": pit_min,
                "PIT_ex_sector_n_median": pit_med,
                "PIT_ex_sector_n_max": pit_max,
                "old_required_n": old_req,
                "new_required_n_rule": (
                    f"80 if N_EX>=80 else ceil({MKT_EX_MIN_FRAC}*N_EX); "
                    f"at_max={new_req_at_max}; at_min={new_req_at_min}"
                ),
                "new_required_n_at_max": new_req_at_max,
                "new_required_n_at_min": new_req_at_min,
                "old_gate_structurally_possible": old_possible,
                "new_gate_structurally_possible": new_possible,
                "old_behavior_preserved_when_N_EX_ge_80": True,
                "correction_branch_used": bool(pit_max < OLD_MKT_EX_MIN_N),
            }
        )
    s3650 = next((r for r in matrix if r["sector_id"] == AFFECTED_SECTOR_ID), None)
    others = [r for r in matrix if r["sector_id"] != AFFECTED_SECTOR_ID]
    all_other_old_possible = all(bool(r["old_gate_structurally_possible"]) for r in others)
    all_new_possible = all(bool(r["new_gate_structurally_possible"]) for r in matrix)
    return {
        "pass": bool(
            s3650 is not None
            and s3650["old_gate_structurally_possible"] is False
            and s3650["new_gate_structurally_possible"] is True
            and all_other_old_possible
            and all_new_possible
            and len(matrix) == len(EXPECTED_SECTOR_IDS)
        ),
        "n_universe": EXPECTED_UNIVERSE_N,
        "old_mkt_ex_min_n": OLD_MKT_EX_MIN_N,
        "listing_source": "V1_precommit_audit_PointInTime_Listing",
        "dates_used": "development_dates_only",
        "date_n": len(dates),
        "future_return_used": False,
        "c1_outcomes_used": False,
        "matrix": matrix,
        "sector_3650": s3650,
        "all_other_sectors_old_gate_structurally_possible": all_other_old_possible,
        "all_sectors_new_gate_structurally_possible": all_new_possible,
        "other_sectors_retain_absolute_80_when_N_EX_ge_80": True,
    }
