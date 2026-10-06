"""Corrected V4 state-machine text. Setup ends at S4. Execution separate."""
from __future__ import annotations

import hashlib
from pathlib import Path

from research.pb1_opening_range_continuation_face_valid_v2 import LAST_BREAK, LAST_TRIGGER, OR_KNOWN_FROM
from research.pb1_v4_implementation_correction import (
    E1_BODY_N1M,
    E1_NET_N1M,
    E1_RANGE_N1M,
    EXEC_1M_CONFIRMED,
    EXEC_5M_DIRECT,
    FAIL_COUNTER_MIN,
    FAIL_DRIVE_DISP_MIN,
    PARENT_V2_SHA,
    PARENT_V3_SHA,
    PARENT_V31_SHA,
    PARENT_V32_SHA,
    S0_GAP_ATR_MIN,
    S0_GAP_RANGE_MIN,
    S0_RANGE_MIN,
    S4_BODY_FRAC,
    S4_CLOSE_LOC,
    S4_RANGE_OVER_N1M,
    S4_RANGE_OVER_OPEN5,
    TRUE_BODY_FRAC_MIN,
    TRUE_COUNTER_FRAC,
    TRUE_DISP_MIN,
    TRUE_N_SAME_MIN,
    TRUE_RANGE_MIN,
    V4_LEGACY_LEAKED_IMPLEMENTATION,
)

STATE_MACHINE_TEXT = f"""
PB1 V4 CORRECTED MACHINE. INTRADAY 5M CONTINUATION. NOT 1-MINUTE SCALPING.

LEAKED IMPLEMENTATION SHA (immutable evidence): {V4_LEGACY_LEAKED_IMPLEMENTATION}
PARENT V3.2 SHA (frozen, not mutated): {PARENT_V32_SHA}
PARENT V3.1 SHA (frozen, not mutated): {PARENT_V31_SHA}
PARENT V3 SHA (frozen, not mutated): {PARENT_V3_SHA}
PARENT V2 SHA (frozen, not mutated): {PARENT_V2_SHA}

LAYERS:
  A. SETUP MACHINE ends at FIVE_M_CONTINUATION_STATE / SETUP_ELIGIBLE.
  B. EXECUTION ADAPTERS run only after SETUP_ELIGIBLE_AT.
  INVARIANT: if FIVE_M_SETUP_VALID == false then ONE_M_ENTRY_ALLOWED == false.
  S3 can NEVER exist without S2.

SETUP SEQUENCE (absorbing rejects):
  S0 WHY_THIS_STOCK_TODAY = DISTINCTIVE_OPENING_ACTIVITY_V4
  S1 FIVE_M_OPENING_DRIVE (exclusive taxonomy)
  S2 MEANINGFUL_PRICE_LOCATION (V4_LOCATION_CLASSIFIER families A/B/C)
  S3 BREAK_RETEST_VALIDATED (same retest event, only after S2)
  S4 FIVE_M_CONTINUATION_STATE → SETUP_ELIGIBLE_AT

S0 unchanged from leaked V4:
  Primary: max first-three 5m range / NORMAL_OPENING_5M_RANGE >= {S0_RANGE_MIN}
  Assist: |gap|/ATR20 >= {S0_GAP_ATR_MIN} AND max first-three 5m range / normal >= {S0_GAP_RANGE_MIN}

S1 TRUE_OPENING_DRIVE from 09:00-09:04 / 09:05-09:09 / 09:10-09:14 ONLY.
  Later 5m cannot create TRUE. Later 5m may only complete FAILED_OPEN_THEN_REAL_DRIVE
  if a real initial counter-auction was already visible, without two-sided/range rescue.
  TRUE: disp/normal >= {TRUE_DISP_MIN} AND max range/normal >= {TRUE_RANGE_MIN}
    AND n_same_dir >= {TRUE_N_SAME_MIN} AND counter <= {TRUE_COUNTER_FRAC} * displacement
    AND mean body/range >= {TRUE_BODY_FRAC_MIN} AND first+last opening bars same direction.
  FAILED_OPEN: visible counter 5m (range/normal >= {FAIL_COUNTER_MIN}, body/range >= 0.40)
    THEN opposite 5m auction from the failed-open extreme, displacement/normal >= {FAIL_DRIVE_DISP_MIN}.
  Exclusive absorbing rejects: TWO_SIDED_OPEN, FLAT_OR_CRAWL, LATE_RANGE_RESOLUTION, MICRO_OR_LEAK.
  Locked opening_state is never overwritten by a later invalid label.

S2 V4_LOCATION_CLASSIFIER (no V3 1R / room / overhead eligibility kills):
  A CLEARED_ZONE_RETEST — prior-known zone, 5m far-side clear, first pullback tests from far side, hold.
  B OR_HELD_AFTER_REAL_DRIVE — valid S1, completed 5m leave, first 5m return tests OR from continuation
    side with visible reject/hold. Forbidden if the OR retest area overlaps a pre-known uncleared reference
    (evaluate A or C instead). Mere 1m geometric OR hold is not enough. No OR-width N1M floor.
  C VISIBLE_CONFLUENT_LOCATION — one identifiable causal relationship (OR + one known level) held
    on the defended side. Not 2-of-7. Not uncleared overhead.
  Frozen S/R detector is INPUT only.

S3 first meaningful pullback after S2 on the same causal identity. S3_without_S2_n = 0.

S4 unchanged semantic scale: directional close loc >= {S4_CLOSE_LOC}, body/range >= {S4_BODY_FRAC},
  range >= {S4_RANGE_OVER_OPEN5} * NORMAL_OPENING_5M or >= {S4_RANGE_OVER_N1M} * NORMAL_1M.

E0 {EXEC_5M_DIRECT} / E1 {EXEC_1M_CONFIRMED} unchanged:
  E1 3-bar net/N1M >= {E1_NET_N1M}, range/N1M >= {E1_RANGE_N1M}, body/N1M >= {E1_BODY_N1M}.
  E1 never creates or revives. SAME_BAR_ENTRY = 0.

SESSION FRAME: last break {LAST_BREAK}, last trigger {LAST_TRIGGER}, OR known {OR_KNOWN_FROM}.
NO: PnL, Confirmation, Frozen Validation, V4.1, 1R eligibility, N1M room eligibility.
""".strip()

RULE_DIFF = {
    "removed_from_eligibility": [
        "NO_MEANINGFUL_ROOM",
        "STRUCTURALLY_BLOCKED",
        "OVERHEAD_UNCLEARED_REFERENCE",
        "LOCATION_ALREADY_INSIDE_OPPOSING",
        "NEAREST_OPPOSING_TOO_CLOSE",
        "STRUCTURAL_ROUTE_R_MULT / V3 structural_route kill",
        "V3.1 MEANINGFUL_R_NOISE_MULT kill",
        "OR-width N1M automatic location kill",
        "classify_zone_path as silent eligibility oracle",
    ],
    "kept_as_input_or_diagnostic": [
        "frozen causal S/R zones / PDH/PDL/PDC/VWAP/SMA as location identity",
        "NON_ELIGIBILITY_DIAGNOSTIC planned_R / route persist",
    ],
    "s1_fix": [
        "TRUE from first-three 5m only",
        "exclusive opening taxonomy",
        "later bars cannot create TRUE or rescue TWO_SIDED/FLAT/LATE/MICRO",
        "locked opening_state not overwritten",
    ],
    "s2_s3_fix": [
        "S2 then S3 on the same retest event",
        "S3 never without S2",
        "identity minted from S1",
    ],
}


def machine_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    h.update(STATE_MACHINE_TEXT.encode("utf-8"))
    for name in (
        "definitions.py",
        "s1.py",
        "location.py",
        "machine.py",
        "diagnostics.py",
        "walk.py",
    ):
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()
