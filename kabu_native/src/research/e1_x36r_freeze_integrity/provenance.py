"""Final architecture selection provenance from X36 source."""
from __future__ import annotations

from typing import Any

from . import FINAL_FEATURE_SET, FINAL_REG, OUTER_SPECS


def document_provenance() -> dict[str, Any]:
    """
    X36 run_audit.py (pre-result implementation) defined:

      1. maj = Counter(family across outer folds).most_common(1)
      2. iterate folds in dict insertion order A→B→C→D
      3. take first fold whose family == maj → use that fold's feature_set + reg
      4. refit that spec on all Historical14

    Outer results:
      A BOARD_PRICE/1.0, B BOARD_PRICE/1.0, C COMPACT/1.0, D BOARD_PRICE/0.1
      majority family = A1_FILL (4/4)
      first fold with A1_FILL = A → BOARD_PRICE / 1.0

    This is deterministic code-path selection, not human post-hoc cherry-pick.
    """
    fams = [OUTER_SPECS[b]["family"] for b in ("A", "B", "C", "D")]
    from collections import Counter
    maj = Counter(fams).most_common(1)[0][0]
    chosen = None
    for b in ("A", "B", "C", "D"):
        if OUTER_SPECS[b]["family"] == maj:
            chosen = {"block": b, **OUTER_SPECS[b]}
            break

    valid = (
        chosen is not None
        and chosen["feature_set"] == FINAL_FEATURE_SET
        and float(chosen["reg"]) == float(FINAL_REG)
        and maj == "A1_FILL"
    )
    return {
        "procedure_locus": "src/research/e1_x36_joint_allocator/run_audit.py final_allocator block",
        "procedure": [
            "majority family across Outer A/B/C/D",
            "first fold in order A→B→C→D with that family supplies feature_set and reg",
            "refit that spec on all Historical14",
        ],
        "outer_specs": OUTER_SPECS,
        "majority_family": maj,
        "selected_from_block": (chosen or {}).get("block"),
        "selected_feature_set": (chosen or {}).get("feature_set"),
        "selected_reg": (chosen or {}).get("reg"),
        "matches_frozen_final": valid,
        "post_hoc_human_choice": False,
        "provenance_ok": valid,
        "note": (
            "Rule was in X36 implementation before/at run time; "
            "BOARD_PRICE/1.0 follows from fold-A being first A1_FILL survivor, "
            "not from inspecting test PnL after the fact to pick the best fold."
        ),
    }
