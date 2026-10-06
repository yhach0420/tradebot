"""Frozen V3.1 state-machine semantics. Face-validity fix. No economic search."""
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
from research.pb1_v3_1_face_validity_fix import (
    ACCEPTANCE_FAIL_CLOSES,
    ARCHIVED_TRIGGER,
    DRIVE_NET_OR_FRAC,
    MEANINGFUL_LEAVE_NOISE_MULT,
    MEANINGFUL_R_NOISE_MULT,
    NOISE_LOOKBACK_SESSIONS,
    PARENT_V2_SHA,
    PARENT_V3_SHA,
    PRIMARY_TRIGGER,
    STRUCTURAL_ROUTE_R_MULT,
)

STATE_MACHINE_TEXT = f"""
PB1 V3.1 OPENING-RANGE CONTINUATION — FACE VALIDITY FIX.

PARENT V3 SHA (frozen, not mutated): {PARENT_V3_SHA}
PARENT V2 SHA (frozen, not mutated): {PARENT_V2_SHA}

THESIS:
  IN-PLAY → CLEAN_OPENING_DRIVE_V2 → OR15 → SAME-DIR REAL BREAK with impulse continuity →
  MEANINGFUL LEAVE (≥1 NORMAL_1M_RANGE) → FIRST FRESH RETEST → MEANINGFUL DEFENSE →
  MEANINGFUL STRUCTURAL R (≥1 NORMAL_1M_RANGE) → CLEAR +1R ROUTE → MICRO RECLAIM → NEXT 1M OPEN.

IDENTICAL TO V3:
  GENUINELY_IN_PLAY constants
  OR15 freeze [{OR_START},{OR_END}] known {OR_KNOWN_FROM}
  REAL_BREAK (beyond {BREAK_BEYOND_OR_FRAC} OR or {BREAK_BEYOND_ATR_FRAC} ATR, loc>={BREAK_CLOSE_LOC})
  geometrical LEAVE (>={MIN_AWAY_BARS} fully-away OR one bar >={LEAVE_EXT_OR_FRAC} OR)
  FIRST causal retest within {RETEST_FRESHNESS_MIN} trading minutes (not 5m; no 09:30/09:45 cutoff)
  RECLAIM_RETEST_MICRO_HIGH only; FAILED_PUSH archived not emitted
  next-bar execution SAME_BAR_ENTRY_N=0, bull/bear mirror, causal data
  structural map families unchanged; 1R route not searched
  invalidation RETEST_EXTREME_BREACH + {ACCEPTANCE_FAIL_CLOSES} consecutive closes
  last break {LAST_BREAK}, last trigger {LAST_TRIGGER}, flatten {SESSION_FLAT}

CHANGED FROM V3:
  1. CLEAN_OPENING_IMPULSE replaced by CLEAN_OPENING_DRIVE_V2.
     LONG: 09:14 close > 09:00 open; 09:14 close in upper half of OR;
     |net 09:00→09:14| >= {DRIVE_NET_OR_FRAC} × OR15; MFE > MAE; first 5m not net opposite.
     0.50 is the half-OR translation requirement. NOT searched.
  2. OPENING_IMPULSE_LOST: between 09:15 and REAL_BREAK, no completed close may enter
     the opposite half of frozen OR15.
  3. MEANINGFUL LEAVE: keep geometrical leave AND max distance outside OR before first
     retest >= {MEANINGFUL_LEAVE_NOISE_MULT} × NORMAL_1M_RANGE. Else MICRO_OR_LEAK.
     1.0 is one ordinary 1-minute range. NOT searched.
  4. MEANINGFUL STRUCTURAL R: planned_R / NORMAL_1M_RANGE >= {MEANINGFUL_R_NOISE_MULT}.
     Else MICRO_STRUCTURE_NOT_TRADABLE. Do not evaluate +1R route on sub-noise R.
     1.0 NOT searched.
  5. NORMAL_1M_RANGE = median completed 1m high-low at the same clock,
     prior {NOISE_LOOKBACK_SESSIONS} valid trading sessions only. No future day.
  6. MEANINGFUL_DEFENSE vs TOUCH_ONLY: retest must reach the defended area AND
     the completed retest close must be on the continuation side.
  7. Persist reclaim_move / NORMAL_1M_RANGE. Do not gate on it yet.

NO: market/archetype/CLEAN_FLIP gate, 5m retest, 09:30/09:45 cutoff, new indicator,
  gap/TV retune, PnL, Confirmation, Frozen Validation, Complete Strategy, threshold search.
""".strip()

RULE_DIFF = {
    "identical_to_v3": [
        "GENUINELY_IN_PLAY constants",
        "OR15 freeze / REAL_BREAK",
        "geometrical LEAVE constants",
        "RETEST_FRESHNESS_MIN=30 outer limit",
        "RECLAIM_RETEST_MICRO_HIGH only; FAILED_PUSH archived",
        "next-bar SAME_BAR_ENTRY_N=0",
        "1R structural route (not searched) after noise floor",
        "causal S/R map families",
        "invalidation RETEST_EXTREME_BREACH + two closes",
    ],
    "changed_from_v3": [
        "CLEAN_OPENING_IMPULSE rebuilt as CLEAN_OPENING_DRIVE_V2 (net >= 0.50 OR, not searched)",
        "OPENING_IMPULSE_LOST continuity 09:15→break",
        "planned_R must exceed one NORMAL_1M_RANGE else MICRO_STRUCTURE_NOT_TRADABLE",
        "leave must exceed one NORMAL_1M_RANGE else MICRO_OR_LEAK",
        "MEANINGFUL_DEFENSE vs TOUCH_ONLY",
        "reclaim_move/NORMAL_1M_RANGE persisted, not gated",
    ],
    "not_done": [
        "no 5-minute retest gate",
        "no 09:30/09:45 cutoff",
        "no new indicator",
        "no PnL / Confirmation / Frozen Validation",
        "1.0 noise multiple not searched",
        "0.50 drive fraction not searched",
        "reclaim not rebuilt",
    ],
}


def machine_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    h.update(STATE_MACHINE_TEXT.encode("utf-8"))
    for name in ("definitions.py", "machine.py", "drive.py", "noise.py"):
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()
