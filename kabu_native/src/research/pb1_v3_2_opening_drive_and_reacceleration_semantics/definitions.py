"""Frozen V3.2 state-machine semantics. Drive V3 + reacceleration V2. No economic search."""
from __future__ import annotations

import hashlib
from pathlib import Path

from research.pb1_opening_range_continuation_face_valid_v2 import (
    BREAK_BEYOND_ATR_FRAC,
    BREAK_BEYOND_OR_FRAC,
    BREAK_CLOSE_LOC,
    LAST_BREAK,
    LAST_TRIGGER,
    LEAVE_EXT_OR_FRAC,
    MIN_AWAY_BARS,
    OR_END,
    OR_KNOWN_FROM,
    OR_START,
    RETEST_FRESHNESS_MIN,
    SESSION_FLAT,
)
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics import (
    ACCEPTANCE_FAIL_CLOSES,
    ARCHIVED_TRIGGER,
    MEANINGFUL_LEAVE_NOISE_MULT,
    MEANINGFUL_R_NOISE_MULT,
    OR_CLOSE_HALF,
    PARENT_V2_SHA,
    PARENT_V3_SHA,
    PARENT_V31_SHA,
    PRIMARY_TRIGGER,
    REACCEL_CLOSE_LOC,
    STRUCTURAL_ROUTE_R_MULT,
    TWO_SIDED_EXCURSION,
    TWO_SIDED_LOC_HI,
    TWO_SIDED_LOC_LO,
)

STATE_MACHINE_TEXT = f"""
PB1 V3.2 OPENING-DRIVE AND REACCELERATION SEMANTICS.

PARENT V3.1 SHA (frozen, not mutated): {PARENT_V31_SHA}
PARENT V3 SHA (frozen, not mutated): {PARENT_V3_SHA}
PARENT V2 SHA (frozen, not mutated): {PARENT_V2_SHA}

THESIS:
  IN-PLAY → CLEAN_OPENING_DRIVE_V3 → OR15 → SAME-DIR REAL BREAK with impulse continuity →
  MEANINGFUL LEAVE (≥1 NORMAL_1M_RANGE) → FIRST FRESH RETEST → MEANINGFUL DEFENSE →
  MEANINGFUL STRUCTURAL R (≥1 NORMAL_1M_RANGE) → CLEAR +1R ROUTE →
  REACCELERATION_TRIGGER_V2 → NEXT 1M OPEN.

IDENTICAL TO V3.1:
  GENUINELY_IN_PLAY constants
  OR15 freeze [{OR_START},{OR_END}] known {OR_KNOWN_FROM}
  REAL_BREAK (beyond {BREAK_BEYOND_OR_FRAC} OR or {BREAK_BEYOND_ATR_FRAC} ATR, loc>={BREAK_CLOSE_LOC})
  geometrical LEAVE (>={MIN_AWAY_BARS} fully-away OR one bar >={LEAVE_EXT_OR_FRAC} OR)
  MEANINGFUL LEAVE / MEANINGFUL R noise floors ({MEANINGFUL_LEAVE_NOISE_MULT} / {MEANINGFUL_R_NOISE_MULT}, not searched)
  FIRST causal retest within {RETEST_FRESHNESS_MIN} trading minutes (not 5m; no 09:30/09:45 cutoff)
  OPENING_IMPULSE_LOST continuity 09:15→break
  FAILED_PUSH archived not emitted
  next-bar execution SAME_BAR_ENTRY_N=0, bull/bear mirror
  structural map families unchanged; 1R route not searched ({STRUCTURAL_ROUTE_R_MULT})
  MEANINGFUL_DEFENSE vs TOUCH_ONLY
  invalidation RETEST_EXTREME_BREACH + {ACCEPTANCE_FAIL_CLOSES} consecutive closes
  last break {LAST_BREAK}, last trigger {LAST_TRIGGER}, flatten {SESSION_FLAT}

CHANGED FROM V3.1:
  1. CLEAN_OPENING_DRIVE_V2 replaced by CLEAN_OPENING_DRIVE_V3.
     Dominant OR15 auction by 09:14 close location in directional half (half={OR_CLOSE_HALF}, not searched).
     TWO_SIDED_OPEN if close in ({TWO_SIDED_LOC_LO},{TWO_SIDED_LOC_HI}) AND both excursions >={TWO_SIDED_EXCURSION}.
     RANGE_OPEN if close in that middle band without two-sided extremes.
     Allows INITIAL_DIRECTION_CONTINUATION and EARLY_REVERSAL_THEN_DOMINANT_DRIVE.
     Does NOT require session-open → 09:14 net 0.50 OR.
     Does NOT require first 5m same sign.
     Directional efficiency persisted, not gated.
  2. RECLAIM_RETEST_MICRO_HIGH replaced by {PRIMARY_TRIGGER}.
     After defended retest: close through micro structure AND directional close half
     of trigger bar (>={REACCEL_CLOSE_LOC}) AND body >= opposite wick AND trigger range
     >= retest bar range. Reclaim_move/NORMAL_1M_RANGE persisted, NOT gated at 1.0.
     Not chosen from profit.
  3. Persist TV during opening impulse / retest / reacceleration. Not a gate.

NO: market/archetype/CLEAN_FLIP gate, 5m retest, 09:30/09:45 cutoff, new indicator,
  PnL, Confirmation, Frozen Validation, Complete Strategy, threshold search,
  reclaim_move>=1.0 profit gate.
""".strip()

RULE_DIFF = {
    "identical_to_v31": [
        "GENUINELY_IN_PLAY / OR15 / REAL_BREAK / geometrical LEAVE",
        "NORMAL_1M_RANGE noise floors for leave and planned_R",
        "RETEST_FRESHNESS_MIN=30 outer limit",
        "OPENING_IMPULSE_LOST",
        "FAILED_PUSH archived",
        "MEANINGFUL_DEFENSE vs TOUCH_ONLY",
        "1R structural route after noise floor",
        "next-bar SAME_BAR_ENTRY_N=0",
        "invalidation RETEST_EXTREME_BREACH + two closes",
    ],
    "changed_from_v31": [
        "CLEAN_OPENING_DRIVE_V3 dominant-OR auction including early reversal",
        "REACCELERATION_TRIGGER_V2 replaces micro-cross reclaim",
        "TV persisted at impulse/retest/reacceleration, not gated",
    ],
    "not_done": [
        "no 5-minute retest gate",
        "no 09:30/09:45 cutoff",
        "no new indicator",
        "no PnL / Confirmation / Frozen Validation",
        "reclaim_move>=1.0 not used as a gate",
        "efficiency not gated",
        "independent face review not run on unseen data",
    ],
}


def machine_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    h.update(STATE_MACHINE_TEXT.encode("utf-8"))
    for name in ("definitions.py", "machine.py", "drive.py", "reaccel.py"):
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()
