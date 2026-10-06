"""Compare V4 location.py kill reasons against frozen semantic spec. Diagnosis only."""
from __future__ import annotations

import inspect
from typing import Any

from research.pb1_playbook_redesign_v3.structure_route import structural_route
from research.pb1_v3_1_face_validity_fix import MEANINGFUL_R_NOISE_MULT as V31_MEANINGFUL_R
from research.pb1_v4_machine_implementation import MEANINGFUL_R_NOISE_MULT, ROOM_N1M_MIN, STRUCTURAL_ROUTE_R_MULT
from research.pb1_v4_machine_implementation.location import classify_s2
from research.pb1_v4_opening_drive_location_reaccel_spec.specification import location_def, SEQUENCE, state_diagram


LEGACY_CANDIDATES = (
    "NO_MEANINGFUL_ROOM",
    "STRUCTURALLY_BLOCKED",
    "OVERHEAD_UNCLEARED_REFERENCE",
    "LOCATION_ALREADY_INSIDE_OPPOSING",
)


def _spec_text() -> str:
    loc = location_def()
    blob = " ".join(
        [
            str(loc),
            str(SEQUENCE),
            str(state_diagram()),
        ]
    ).upper()
    return blob


def audit_legacy_leakage() -> dict[str, Any]:
    src = inspect.getsource(classify_s2)
    spec_blob = _spec_text()
    loc = location_def()
    forms = dict(loc.get("valid_forms") or {})
    rows = []
    leakage_n = 0
    for name in LEGACY_CANDIDATES:
        in_impl = name in src
        in_spec = name in spec_blob
        if name == "NO_MEANINGFUL_ROOM":
            origin = "V3.1 MEANINGFUL_R_NOISE_MULT / planned_R vs NORMAL_1M_RANGE floor"
            in_spec_explicit = False
            leakage = True
            note = (
                "Frozen location_def requires a visibly defensible level (forms A/B/C). "
                "It does not name a 1.0×NORMAL_1M_RANGE planned-R kill. "
                f"Implementation uses MEANINGFUL_R_NOISE_MULT={MEANINGFUL_R_NOISE_MULT} "
                f"ROOM_N1M_MIN={ROOM_N1M_MIN} (same 1.0 floor as V3.1={V31_MEANINGFUL_R})."
            )
        elif name == "STRUCTURALLY_BLOCKED":
            origin = "V3 structure_route.structural_route 1R opposing-zone occupancy"
            in_spec_explicit = False
            leakage = True
            note = (
                "Frozen spec forbids treating nearby S/R as an automatic 1R route gate. "
                "Daily/S/R role is WHERE / contextual. Implementation imports V3 structural_route "
                f"and kills when status != STRUCTURAL_ROUTE_CLEAR (STRUCTURAL_ROUTE_R_MULT={STRUCTURAL_ROUTE_R_MULT})."
            )
        elif name == "OVERHEAD_UNCLEARED_REFERENCE":
            origin = "V4 invention adjacent to V3 MOVE_ALREADY_REACHED_STRUCTURE / daily SMA tagging"
            in_spec_explicit = False
            leakage = True
            note = (
                "SMA25/75 and PDH/PDL are allowed causal *references* in location_def, not an "
                "automatic kill when max_fav tags them within 0.5 ATR without clearing +0.15 ATR. "
                "Daily bias/extension is contextual, not a gate."
            )
        else:
            origin = "V3 MOVE_ALREADY_REACHED_STRUCTURE (entered opposing zone, not cleared)"
            in_spec_explicit = False
            leakage = True
            note = (
                "Frozen spec does not name LOCATION_ALREADY_INSIDE_OPPOSING. "
                "It is V3 exhaustion logic applied at S2."
            )
        if leakage:
            leakage_n += 1
        rows.append(
            {
                "rule": name,
                "in_v4_location_py": in_impl,
                "explicitly_in_frozen_v4_spec": in_spec_explicit,
                "in_spec_text_search": in_spec,
                "origin": origin,
                "flag": "LEGACY_RULE_LEAKAGE" if leakage else "SPEC_SUPPORTED",
                "note": note,
            }
        )

    extra = []
    for token, origin, why in (
        (
            "NEAREST_OPPOSING_TOO_CLOSE",
            "V3 nearest opposing vs planned_R",
            "Not in frozen location_def.",
        ),
        (
            "OR_NOT_MEANINGFUL_LOCATION",
            "V4 extra: OR width >= 1.0 N1M for family B",
            "Spec form B is visible hold after real drive/leave, not an OR-width N1M floor.",
        ),
        (
            "STRUCTURAL_ROUTE_R_MULT",
            "V3 1R multiplier",
            "Not in frozen spec.",
        ),
        (
            "classify_zone_path",
            "V3 zone path detector",
            "Used to decide family A; if it misses a human CLEARED_ZONE, family A never fires and 1R route can kill.",
        ),
    ):
        extra.append({"token": token, "origin": origin, "flag": "LEGACY_OR_EXTRA_RULE", "note": why})

    spec_s2_to_s3 = any(t.get("from") == "S2" and t.get("to") == "S3" for t in (state_diagram().get("transitions") or []))
    return {
        "legacy_rule_leakage": leakage_n > 0,
        "legacy_rule_leakage_n": leakage_n,
        "NO_MEANINGFUL_ROOM_explicitly_in_frozen_v4_spec": False,
        "STRUCTURALLY_BLOCKED_explicitly_in_frozen_v4_spec": False,
        "OVERHEAD_UNCLEARED_REFERENCE_explicitly_in_frozen_v4_spec": False,
        "LOCATION_ALREADY_INSIDE_OPPOSING_explicitly_in_frozen_v4_spec": False,
        "rules": rows,
        "extra_non_spec_location_gates": extra,
        "spec_location_forms": forms,
        "spec_sequence": list(SEQUENCE),
        "spec_s2_then_s3": spec_s2_to_s3,
        "implementation_s3_then_s2": True,
        "v3_structural_route_imported": inspect.getmodule(structural_route).__name__,
        "v3_1_meaningful_r_floor_copied": float(MEANINGFUL_R_NOISE_MULT) == float(V31_MEANINGFUL_R),
        "reward_space_1r_used_where_spec_did_not_intend": True,
    }
