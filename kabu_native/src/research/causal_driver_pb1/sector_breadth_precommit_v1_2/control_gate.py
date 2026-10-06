"""Deterministic MKT_EX control-gate feasibility repair. Outcome-independent."""
from __future__ import annotations

from math import ceil
from typing import Any

from research.causal_driver_pb1.sector_breadth_precommit_v1_2 import MKT_EX_MIN_FRAC, OLD_MKT_EX_MIN_N


def required_ex_sector_valid_n(n_ex_sector_pit: int) -> int:
    """Preserve >=80 whenever the PIT ex-sector population itself is >=80. Else ceil(0.80 * N)."""
    n = int(n_ex_sector_pit)
    if n >= int(OLD_MKT_EX_MIN_N):
        return int(OLD_MKT_EX_MIN_N)
    return int(ceil(float(MKT_EX_MIN_FRAC) * n))


def old_required_ex_sector_valid_n(_n_ex_sector_pit: int) -> int:
    return int(OLD_MKT_EX_MIN_N)


def old_gate_structurally_possible(n_ex_sector_pit: int) -> bool:
    return int(n_ex_sector_pit) >= int(OLD_MKT_EX_MIN_N)


def new_gate_structurally_possible(n_ex_sector_pit: int) -> bool:
    n = int(n_ex_sector_pit)
    if n < 1:
        return False
    return n >= required_ex_sector_valid_n(n)


def control_gate_spec() -> dict[str, Any]:
    return {
        "rule_id": "MKT_EX_SECTOR_REQUIRED_VALID_N_FEASIBILITY_BRANCH_V1_2",
        "N_EX_SECTOR_PIT": "count of PIT-listed frozen-105 symbols not belonging to target sector S",
        "if_N_EX_SECTOR_PIT_ge_80": "REQUIRED_EX_SECTOR_VALID_N = 80",
        "else": "REQUIRED_EX_SECTOR_VALID_N = ceil(0.80 * N_EX_SECTOR_PIT)",
        "always_require_valid_fraction_ge": float(MKT_EX_MIN_FRAC),
        "valid_fraction": "valid_n / N_EX_SECTOR_PIT",
        "not_unconditional_80pct_relaxation": True,
        "not_threshold_optimization": True,
        "not_sample_rescue": True,
        "not_sector_specific_tuning": True,
        "outcome_independent": True,
        "derived_from": ("universe size 105", "sector membership count", "control-gate arithmetic"),
        "not_derived_from": ("beta", "p", "q", "near-miss ranking", "direction", "economic result"),
        "old_absolute_min_n": int(OLD_MKT_EX_MIN_N),
        "old_min_frac": float(MKT_EX_MIN_FRAC),
        "unit_3650": {"N_EX_SECTOR": 75, "old_required": 80, "old_possible": False, "new_required": 60, "new_possible": True},
        "unit_normal": {"N_EX_SECTOR": 91, "required": 80, "not": 73},
    }
