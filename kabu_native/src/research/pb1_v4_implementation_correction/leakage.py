"""Eligibility leakage scan. Diagnostic persistence is allowed if marked."""
from __future__ import annotations

import inspect
from typing import Any

from research.pb1_v4_implementation_correction import location as loc_mod
from research.pb1_v4_implementation_correction import machine as mach_mod
from research.pb1_v4_implementation_correction import s1 as s1_mod
from research.pb1_v4_implementation_correction import walk as walk_mod
from research.pb1_v4_implementation_correction.location import classify_s2
from research.pb1_v4_implementation_correction.machine import commit_s2_s3, step_break_retest
from research.pb1_v4_implementation_correction.s1 import classify_s1, extend_failed_open

FORBIDDEN = (
    "structural_route(",
    "STRUCTURAL_ROUTE_R_MULT",
    "NO_MEANINGFUL_ROOM",
    "MEANINGFUL_R_NOISE_MULT",
    "STRUCTURALLY_BLOCKED",
    "NEAREST_OPPOSING_TOO_CLOSE",
    "OVERHEAD_UNCLEARED_REFERENCE",
    "LOCATION_ALREADY_INSIDE_OPPOSING",
    "classify_zone_path(",
)


def scan_eligibility_leakage() -> dict[str, Any]:
    blobs = {
        "location.py": inspect.getsource(loc_mod),
        "machine.py": inspect.getsource(mach_mod),
        "s1.py": inspect.getsource(s1_mod),
        "walk.py": inspect.getsource(walk_mod),
        "classify_s2": inspect.getsource(classify_s2),
        "commit_s2_s3": inspect.getsource(commit_s2_s3),
        "step_break_retest": inspect.getsource(step_break_retest),
        "classify_s1": inspect.getsource(classify_s1),
        "extend_failed_open": inspect.getsource(extend_failed_open),
    }
    hits: list[dict[str, Any]] = []
    for where, src in blobs.items():
        for tok in FORBIDDEN:
            if tok in src:
                hits.append({"where": where, "token": tok, "eligibility": True})
    return {
        "legacy_eligibility_leakage_n": len(hits),
        "hits": hits,
        "forbidden_tokens": list(FORBIDDEN),
        "diagnostic_module_allowed": "diagnostics.py marked NON_ELIGIBILITY_DIAGNOSTIC",
    }
