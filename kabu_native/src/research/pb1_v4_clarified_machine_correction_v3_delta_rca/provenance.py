"""Delta C: provenance of V3-promoted numeric predicates. Diagnosis only."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_correction_v3.encoding import (
    RCA_CLOSE_PROGRESS_UNDO_MAJORITY,
    RCA_COMPARABLE_OPPOSITE_BODY,
    RCA_COMPARABLE_OPPOSITE_EXCURSION,
    RCA_MAJORITY_ONE_BAR_SHARE,
    RCA_THIRD_CONTINUATION_NET,
)
from research.pb1_v4_clarified_machine_correction_v3 import LAST_BODY_MIN, TRUE_BODY_FRAC_MIN


def provenance_table() -> list[dict[str, Any]]:
    return [
        {
            "predicate": "RCA_MAJORITY_ONE_BAR_SHARE",
            "value": RCA_MAJORITY_ONE_BAR_SHARE,
            "first_known_artifact": "V2 RCA one_bar_rca.py majority_one_bar = share > 0.5; also ordinary language 'majority'",
            "first_known_sha": "V2 RCA package (parent machine SHA 916ec521 is Correction V2; this constant is RCA diagnostic)",
            "preexisting_before_RCA": True,
            "used_only_for_diagnosis": False,
            "promoted_in_V3": True,
            "semantic_justification": "Literal majority of opening range. Mathematically definitional, not a searched cutoff.",
            "chosen_after_viewing_88": False,
            "grid_searched": False,
            "allowed_as_frozen_inherited_constant": True,
            "clarified_v2_explicit_number": False,
            "source_class": "definitional_majority",
        },
        {
            "predicate": "RCA_CLOSE_PROGRESS_UNDO_MAJORITY",
            "value": RCA_CLOSE_PROGRESS_UNDO_MAJORITY,
            "first_known_artifact": "V2 RCA two_sided_rca.py: undo >= 0.5 as 'gives back a majority of first-bar close progress'",
            "first_known_sha": "V2 RCA diagnostic (not in Clarified V2 numeric spec)",
            "preexisting_before_RCA": False,
            "used_only_for_diagnosis": True,
            "promoted_in_V3": True,
            "semantic_justification": "Definitional majority undo of first-auction close progress. Not a 88-grid.",
            "chosen_after_viewing_88": False,
            "grid_searched": False,
            "allowed_as_frozen_inherited_constant": True,
            "clarified_v2_explicit_number": False,
            "source_class": "definitional_majority_promoted_from_V2_RCA",
        },
        {
            "predicate": "RCA_COMPARABLE_OPPOSITE_BODY",
            "value": RCA_COMPARABLE_OPPOSITE_BODY,
            "first_known_artifact": "V2 RCA two_sided_rca.py: middle body >= 0.50 to call a comparable opposite auction",
            "first_known_sha": "V2 RCA diagnostic; stricter than frozen TRUE_BODY_FRAC_MIN=0.35",
            "preexisting_before_RCA": False,
            "used_only_for_diagnosis": True,
            "promoted_in_V3": True,
            "semantic_justification": (
                "V2 RCA needed 'committed opposite auction' vs pullback. 0.35 (TRUE_BODY / old substantial_opp) "
                "was already judged too broad (6963 body~0.39). 0.50 sits above those pullbacks and below 7741~0.63. "
                "That split was RCA-diagnostic, then promoted unchanged into V3. Not retuned after the V3 walk."
            ),
            "chosen_after_viewing_88": False,
            "chosen_during_V2_RCA_case_review": True,
            "grid_searched": False,
            "allowed_as_frozen_inherited_constant": True,
            "post_hoc_numeric_encoding_risk": True,
            "clarified_v2_explicit_number": False,
            "source_class": "V2_RCA_diagnostic_promoted_authorized_by_V3_spec",
        },
        {
            "predicate": "RCA_COMPARABLE_OPPOSITE_EXCURSION",
            "value": RCA_COMPARABLE_OPPOSITE_EXCURSION,
            "first_known_artifact": "V2 RCA two_sided_rca.py retrace_exc >= 0.35; same numeric family as TRUE_BODY_FRAC_MIN",
            "first_known_sha": "TRUE_BODY_FRAC_MIN frozen since leaked/clarified machine (parent 33b1bf / corrected 21fc72eb scale)",
            "preexisting_before_RCA": True,
            "used_only_for_diagnosis": False,
            "promoted_in_V3": True,
            "semantic_justification": "Inherited TRUE body/committed-size scale reused as 'middle excursion vs first-leg excursion'.",
            "chosen_after_viewing_88": False,
            "grid_searched": False,
            "allowed_as_frozen_inherited_constant": True,
            "clarified_v2_explicit_number": False,
            "source_class": "inherited_TRUE_BODY_FRAC_MIN_family",
        },
        {
            "predicate": "RCA_THIRD_CONTINUATION_NET",
            "value": RCA_THIRD_CONTINUATION_NET,
            "first_known_artifact": "V2 RCA two_sided_rca.py: last_vs_first_net >= 0.70 and last body >= 0.50 for third_is_new_drive",
            "first_known_sha": "V2 RCA diagnostic only; affects third_bar_role, not two_sided_balance",
            "preexisting_before_RCA": False,
            "used_only_for_diagnosis": True,
            "promoted_in_V3": True,
            "semantic_justification": "Relative 'third bar net vs first-leg net' role label. Not used as TWO_SIDED kill by itself.",
            "chosen_after_viewing_88": False,
            "chosen_during_V2_RCA_case_review": True,
            "grid_searched": False,
            "allowed_as_frozen_inherited_constant": True,
            "post_hoc_numeric_encoding_risk": True,
            "clarified_v2_explicit_number": False,
            "source_class": "V2_RCA_diagnostic_promoted_role_label",
        },
        {
            "predicate": "TRUE_BODY_FRAC_MIN (crawl vs committed, leftover_nick crawl)",
            "value": TRUE_BODY_FRAC_MIN,
            "first_known_artifact": "Leaked V4 / clarified machine TRUE_OPENING_DRIVE scale",
            "first_known_sha": "fa0451bb / 33b1bf family; unchanged through V3",
            "preexisting_before_RCA": True,
            "used_only_for_diagnosis": False,
            "promoted_in_V3": False,
            "semantic_justification": "Frozen TRUE body fraction. V3 leftover_nick uses it as crawl vs committed, not a new cutoff.",
            "chosen_after_viewing_88": False,
            "grid_searched": False,
            "allowed_as_frozen_inherited_constant": True,
            "clarified_v2_explicit_number": False,
            "source_class": "frozen_inherited",
        },
        {
            "predicate": "LAST_BODY_MIN",
            "value": LAST_BODY_MIN,
            "first_known_artifact": "Clarified machine / Correction V2 last-bar crawl fail",
            "first_known_sha": "33b1bf / 916ec521",
            "preexisting_before_RCA": True,
            "used_only_for_diagnosis": False,
            "promoted_in_V3": False,
            "semantic_justification": "Inherited last-bar crawl detector. Not a new one-bar share cutoff.",
            "chosen_after_viewing_88": False,
            "grid_searched": False,
            "allowed_as_frozen_inherited_constant": True,
            "source_class": "frozen_inherited",
        },
        {
            "predicate": "STALE opposite body (uses RCA_COMPARABLE_OPPOSITE_BODY) + range >= prev",
            "value": {"body": RCA_COMPARABLE_OPPOSITE_BODY, "range_vs_prev": ">= 1.0"},
            "first_known_artifact": "V3 active.classify_stale_range_resolution — new use of the RCA body constant",
            "first_known_sha": "Correction V3 299fb0ca",
            "preexisting_before_RCA": False,
            "used_only_for_diagnosis": False,
            "promoted_in_V3": True,
            "semantic_justification": (
                "Event shape is committed opposite 5m that does not contract. Relative range>=prev is definitional "
                "'non-contracting'. Body scale is the promoted 0.50, not a new V3 grid. This event matches 4063/3382-10:04 "
                "opposite bounces and misses 7011 leftover overlap — encoding gap, not a missing spec number."
            ),
            "chosen_after_viewing_88": False,
            "grid_searched": False,
            "allowed_as_frozen_inherited_constant": True,
            "post_hoc_numeric_encoding_risk": True,
            "source_class": "V3_event_encoding_reusing_promoted_RCA_body",
        },
    ]


def provenance_judgment(table: list[dict[str, Any]]) -> dict[str, Any]:
    v3_invented = [
        r
        for r in table
        if r.get("promoted_in_V3") and not r.get("preexisting_before_RCA") and r.get("chosen_after_viewing_88")
    ]
    rca_promoted = [r["predicate"] for r in table if r.get("source_class", "").startswith("V2_RCA")]
    return {
        "any_v3_post_walk_invention": bool(v3_invented),
        "v2_rca_promoted_predicates": rca_promoted,
        "v3_task_authorized_promotion": True,
        "v3_task_language": "Promote RCA predicates; save definitions in manifest; do not change them after seeing V3 88 results.",
        "THRESHOLD_OPTIMIZED_false_is_not_enough_if_post_hoc": True,
        "post_hoc_risk_present_but_licensed": True,
        "requires_uncontaminated_reconstitution_of_V3": False,
        "Q9_any_created_after_RCA_then_new_in_V3": (
            "Yes as named V3 constants: RCA_COMPARABLE_OPPOSITE_BODY, RCA_THIRD_CONTINUATION_NET, "
            "RCA_CLOSE_PROGRESS_UNDO_MAJORITY originated as V2 RCA diagnostics and were promoted. "
            "They were not invented after the V3 walk. Majority 0.5 and 0.35 excursion are preexisting/definitional."
        ),
        "do_not_retune_050_070_in_V4": True,
    }
