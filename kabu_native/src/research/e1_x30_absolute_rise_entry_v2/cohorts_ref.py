"""Old Specific49 / Family118 reference comparison (not used for selection)."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.e1_x28e_absolute_rise_exit_arch.cohorts import build_masks, load_cohorts

from .metrics import day_symbol_concentration, summarize_mask


def reference_cohort_metrics(
    *,
    rows: list[dict[str, Any]],
    labels: dict[str, np.ndarray],
    dates: np.ndarray,
    symbols: np.ndarray,
) -> dict[str, Any]:
    cohorts = load_cohorts()
    _, masks = build_masks(rows, cohorts)
    out = {}
    for name, ids in (
        ("ENTRY_V1_Specific49", cohorts["specific_ids"]),
        ("ENTRY_V1_Family118", cohorts["family_ids"]),
    ):
        m = np.zeros(len(rows), dtype=bool)
        for cid in ids:
            m |= masks[cid]
        sm = summarize_mask(
            mask=m, labels=labels, dates=dates, symbols=symbols,
            complement_base=np.ones(len(rows), dtype=bool),
        )
        conc = day_symbol_concentration(
            mask=m, labels=labels, dates=dates, symbols=symbols
        )
        out[name] = {
            **sm,
            **conc,
            "n_candidates": len(ids),
            "reference_only": True,
            "used_for_selection": False,
        }
    return {
        "cohorts": out,
        "specific_n": len(cohorts["specific_ids"]),
        "family_n": len(cohorts["family_ids"]),
        "overlap": 0,
    }
