"""Frozen V4 state-machine semantics. Setup ends at FIVE_M_CONTINUATION_STATE. Execution is separate."""
from __future__ import annotations

import hashlib
from pathlib import Path

from research.pb1_opening_range_continuation_face_valid_v2 import LAST_BREAK, LAST_TRIGGER, OR_KNOWN_FROM
from research.pb1_v4_machine_implementation import (
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
)

STATE_MACHINE_TEXT = f"""
PB1 V4 MACHINE. INTRADAY 5M CONTINUATION. NOT 1-MINUTE SCALPING.

PARENT V3.2 SHA (frozen, not mutated): {PARENT_V32_SHA}
PARENT V3.1 SHA (frozen, not mutated): {PARENT_V31_SHA}
PARENT V3 SHA (frozen, not mutated): {PARENT_V3_SHA}
PARENT V2 SHA (frozen, not mutated): {PARENT_V2_SHA}

LAYERS:
  A. SETUP MACHINE ends at FIVE_M_CONTINUATION_STATE / SETUP_ELIGIBLE.
  B. EXECUTION ADAPTERS run only after SETUP_ELIGIBLE_AT.
  INVARIANT: if FIVE_M_SETUP_VALID == false then ONE_M_ENTRY_ALLOWED == false.
  1m never REJECT → ELIGIBLE.

SETUP SEQUENCE (absorbing rejects):
  S0 WHY_THIS_STOCK_TODAY = DISTINCTIVE_OPENING_ACTIVITY_V4
  S1 FIVE_M_OPENING_DRIVE
  S2 MEANINGFUL_PRICE_LOCATION
  S3 BREAK_RETEST
  S4 FIVE_M_CONTINUATION_STATE → SETUP_ELIGIBLE_AT (completed causal 5m only)

S0 DISTINCTIVE_OPENING_ACTIVITY_V4:
  Primary: max first-three 5m range / NORMAL_OPENING_5M_RANGE >= {S0_RANGE_MIN}
  Assist: |gap|/ATR20 >= {S0_GAP_ATR_MIN} AND max first-three 5m range / normal >= {S0_GAP_RANGE_MIN}
  Cross-sectional activity alone NEVER passes.
  TradingValue alone on a visually flat stock NEVER passes.
  Not old GENUINELY_IN_PLAY.

S1 5m bars 09:00-09:04 / 09:05-09:09 / 09:10-09:14. Later 5m may extend a forming
  FAILED_OPEN only while not two-sided/range-bound. OR-half is NOT in the drive definition.
  TRUE_OPENING_DRIVE: displacement/normal >= {TRUE_DISP_MIN} AND max range/normal >= {TRUE_RANGE_MIN}
    AND n_same_dir >= {TRUE_N_SAME_MIN} AND counter <= {TRUE_COUNTER_FRAC} * displacement
    AND mean body/range >= {TRUE_BODY_FRAC_MIN}.
  FAILED_OPEN_THEN_REAL_DRIVE: visible counter 5m (range/normal >= {FAIL_COUNTER_MIN}, body/range
    >= 0.40) THEN opposite 5m auction from the failed-open extreme, displacement/normal >= {FAIL_DRIVE_DISP_MIN}.
  Reject absorbing: TWO_SIDED_OPEN, FLAT_OR_CRAWL, LATE_RANGE_RESOLUTION, MICRO_OR_LEAK.
  Tiny first-5m dip + 09:14 OR-half close fails here. 1m cannot rescue.

S2 location families (first match, no additive score):
  A CLEARED_ZONE_RETEST — zone known before break, causally cleared, far-side retest hold.
  B OR_HELD_AFTER_REAL_DRIVE — valid S1, real leave, retest interaction, visible hold. Mere OR touch invalid.
  C VISIBLE_CONFLUENT_LOCATION — identified causal level + reaction, not "2 of 7".
  OR touch alone is NOT sufficient.

S3 first meaningful pullback after valid drive/break. 5m thesis must remain alive.
  No 5-minute age gate. No 09:30. No 09:45.

THESIS LOST (state, not clock): displacement unwind, multiple failed breaks,
  repeated OR recross, two-sided range re-established, lack of directional expansion.

S4 FIVE_M_CONTINUATION_STATE (eligibility boundary):
  Defended location held AND counter-pressure weakened AND renewed progress on 5m
  (directional close loc >= {S4_CLOSE_LOC}, body/range >= {S4_BODY_FRAC},
   range >= {S4_RANGE_OVER_OPEN5} * NORMAL_OPENING_5M or >= {S4_RANGE_OVER_N1M} * NORMAL_1M,
   close beyond retest extreme). Not a huge completed 5m breakout.

EXECUTION (same setup identity; timing only):
  E0 {EXEC_5M_DIRECT}: next causally available 1m open after SETUP_ELIGIBLE_AT. No same-bar.
  E1 {EXEC_1M_CONFIRMED}: wait for 1m state-change while 5m thesis alive, then next 1m open.
     Descriptors: 3-bar dir net / N1M >= {E1_NET_N1M}, trigger range / N1M >= {E1_RANGE_N1M},
     trigger body / N1M >= {E1_BODY_N1M}, body >= opposite wick. Micro-cross last, not sufficient alone.
     No TradingValue trigger. If no confirm: WAIT. If 5m dies: CANCEL_EXECUTION_OPPORTUNITY.
     E1 cannot revive a rejected setup.

SESSION FRAME (inherited, not 09:30 thesis cutoff): last break {LAST_BREAK}, last trigger {LAST_TRIGGER},
  OR known {OR_KNOWN_FROM}. Flatten 15:20. Lunch HOLD_THROUGH_LUNCH_RESUME_PM.

NO: economic labels, PnL, PF, MFE/MAE, Confirmation, Frozen Validation, symbol-specific rules,
  trees/forests/boosting, 7203/8035/6857 special cases, 1m creating eligibility.
""".strip()

RULE_DIFF = {
    "changed_from_v32": [
        "S0 DISTINCTIVE_OPENING_ACTIVITY_V4 replaces GENUINELY_IN_PLAY as final truth",
        "S1 5m drive replaces OR-half CLEAN_OPENING_DRIVE_V3",
        "S2 location families A/B/C; OR touch not automatic S/R",
        "S4 5m continuation is the eligibility boundary",
        "1m is execution adapter only (E0/E1)",
        "thesis lost is a state, not STALE_30M / 09:30",
    ],
    "not_done": [
        "no PnL / Confirmation / Frozen Validation",
        "no economic E0 vs E1 comparison",
        "no independent face validation in this package",
        "numeric gates frozen for semantic correspondence only",
    ],
}


def machine_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    h.update(STATE_MACHINE_TEXT.encode("utf-8"))
    for name in (
        "definitions.py",
        "bars5.py",
        "baselines.py",
        "s0.py",
        "s1.py",
        "location.py",
        "thesis.py",
        "s4.py",
        "execution.py",
        "machine.py",
    ):
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()
