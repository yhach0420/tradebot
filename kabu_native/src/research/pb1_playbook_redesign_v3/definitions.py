"""Frozen V3 state-machine semantics. Written from trigger RCA + structural RCA, before any economic test."""
from __future__ import annotations

import hashlib
from pathlib import Path

from research.pb1_opening_range_continuation_face_valid_v2 import (
    BREAK_BEYOND_ATR_FRAC,
    BREAK_BEYOND_OR_FRAC,
    BREAK_CLOSE_LOC,
    IN_PLAY_ABS_GAP_ATR,
    IN_PLAY_TV_PCTL_ELEVATED,
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
from research.pb1_playbook_redesign_v3 import (
    ACCEPTANCE_FAIL_CLOSES,
    ARCHIVED_TRIGGER,
    PARENT_V2_SHA,
    PRIMARY_TRIGGER,
    STRUCTURAL_ROUTE_R_MULT,
)

STATE_MACHINE_TEXT = f"""
PB1 V3 OPENING-RANGE CONTINUATION — minimum coherent redesign (FACE VALIDITY).

PARENT V2 SHA (frozen, not mutated): {PARENT_V2_SHA}

THESIS:
  IN-PLAY → CLEAN OPENING IMPULSE → OR15 → SAME-DIR REAL BREAK → LEAVE →
  FIRST FRESH RETEST → DEFENDED LOCATION → CLEAR STRUCTURAL ROUTE →
  MICRO RECLAIM → NEXT 1M OPEN.

NOT THIS MACHINE: false-break reversal. failed-open reversal. FAILED_PUSH continuation.
  FAILED_PUSH_THEN_CLOSE_BACK is archived as FAILED_PUSH_SEPARATE_MECHANISM.
  Removed because it is a different mechanism (not because of return).

IDENTICAL TO V2:
  GENUINELY_IN_PLAY (gap/TV/xs constants unchanged; not retuned)
  CLEAN_OPENING_IMPULSE path classifier
  OR15 freeze [{OR_START},{OR_END}] known {OR_KNOWN_FROM}
  REAL_BREAK (beyond {BREAK_BEYOND_OR_FRAC} OR or {BREAK_BEYOND_ATR_FRAC} ATR, loc>={BREAK_CLOSE_LOC})
  LEAVE (>={MIN_AWAY_BARS} fully-away OR one bar >={LEAVE_EXT_OR_FRAC} OR)
  FIRST causal retest within {RETEST_FRESHNESS_MIN} trading minutes (outer limit kept; not 5m)
  next-bar execution, SAME_BAR_ENTRY_N=0, bull/bear mirror, causal data
  last break {LAST_BREAK}, last trigger {LAST_TRIGGER}, flatten {SESSION_FLAT}

CHANGED FROM V2:
  1. Trigger family = {PRIMARY_TRIGGER} only. {ARCHIVED_TRIGGER} is not PB1.
  2. PRICE LOCATION is part of the thesis. At TRIGGER CLOSE (not next open):
     planned_entry_reference = completed trigger close
     structural_stop_reference = frozen first-retest extreme
     planned_R = distance between them. planned_R<=0 => candidate invalid.
     CLEAR STRUCTURAL ROUTE: no known opposing S/R zone intersects
     [trigger close, trigger close + {STRUCTURAL_ROUTE_R_MULT} * planned_R] in trade direction.
     1R is unobstructed structural room vs the amount being risked. NOT searched
     over 0.5/0.8/1.2/1.5/2.0.
  3. DEFENDED LOCATION: first retest must defend OR boundary OR a causally
     known zone that was just cleared and is tested from the far side.
     ZONE_NOT_INVOLVED / ZONE_CLEARED_AND_DEFENDED / ZONE_NOT_CLEARED /
     ZONE_CLEARED_BUT_NOT_DEFENDED. Not a CLEAN_FLIP winner rule.
  4. EXHAUSTION is EVENT-BASED: if before first retest the breakout already
     touched/entered the next pre-known opposing zone, MOVE_ALREADY_REACHED_STRUCTURE.
     No extension-bps cutoff. No 5-minute retest gate.
  5. INVALIDATION (persist, not exit optimization):
     PRIMARY = RETEST_EXTREME_BREACH
     SECONDARY = PERSISTENT_ACCEPTANCE_FAILURE = {ACCEPTANCE_FAIL_CLOSES} consecutive
     completed 1m closes back through the defended OR / structural zone.
     One-close OR reentry is NOT immediate thesis failure. 2 is frozen, not searched.

NO: market-regime gate, symbol/archetype gate, CLEAN_FLIP filter, indicator add,
  gap/TV retune, PnL, Confirmation, Frozen Validation, Complete Strategy.
""".strip()

RULE_DIFF = {
    "identical_to_v2": [
        "GENUINELY_IN_PLAY constants",
        "CLEAN_OPENING_IMPULSE",
        "OR15 freeze",
        "REAL_BREAK",
        "LEAVE",
        "first causal retest",
        "RETEST_FRESHNESS_MIN=30 outer limit",
        "next-bar execution SAME_BAR_ENTRY_N=0",
        "bull/bear mirror",
        "causal data / no future swing",
        "gap/TV thresholds not retuned",
    ],
    "changed_from_v2": [
        "FAILED_PUSH_THEN_CLOSE_BACK removed from PB1; archived FAILED_PUSH_SEPARATE_MECHANISM",
        "structural route at trigger close: no opposing zone inside +1R (1R not searched)",
        "defended location required (OR or cleared-and-defended zone)",
        "event-based exhaustion MOVE_ALREADY_REACHED_STRUCTURE",
        "invalidation: RETEST_EXTREME_BREACH primary; two consecutive closes secondary; not one-close OR fail",
        "trigger family RECLAIM_RETEST_MICRO_HIGH only",
        "eligibility frozen at trigger close; next open is execution only",
    ],
    "not_done": [
        "no market/archetype/sector gate",
        "no CLEAN_FLIP winner rule",
        "no 5-minute retest gate",
        "no extension-bps optimization",
        "no PnL / PF / Confirmation / Frozen Validation",
        "1R not searched",
        "FAILED_PUSH not removed for return",
    ],
}


def machine_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    h.update(STATE_MACHINE_TEXT.encode("utf-8"))
    for name in ("definitions.py", "machine.py", "structure_route.py"):
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()
