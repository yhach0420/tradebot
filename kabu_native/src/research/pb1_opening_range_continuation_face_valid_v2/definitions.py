"""Frozen V2 state-machine semantics. Written from blinded V1 failures, before any economic test."""
from __future__ import annotations

import hashlib
from pathlib import Path

from research.pb1_opening_range_continuation_face_valid_v2 import (
    BREAK_BEYOND_ATR_FRAC,
    BREAK_BEYOND_OR_FRAC,
    BREAK_CLOSE_LOC,
    CLEAN_NET15_FRAC,
    FAILED_SPIKE_FRAC,
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

STATE_MACHINE_TEXT = f"""
PB1 V2 OPENING-RANGE CONTINUATION — semantic rebuild (FACE VALIDITY).

THESIS unchanged:
  IN-PLAY → CLEAN OPENING IMPULSE → OR15 → SAME-DIR BREAK → LEAVE →
  FIRST FRESH RETEST → HOLD → 1M TRIGGER → NEXT 1M OPEN.

NOT THIS MACHINE: false-break reversal. failed-open reversal. stale retest.

IN-PLAY (semantic calibration from V1 blinded charts; not returns):
  V1 OR of gap>=0.30 ATR / TV pctl>=0.50 / xs rank<=50% tagged ~81% of OR days.
  xs rank <= 50% is half the pool by construction and is NOT "in play" alone.
  TV pctl >= 0.50 is the median, i.e. ordinary.
  V2 GENUINELY_IN_PLAY if ANY:
    |gap|/ATR20 >= {IN_PLAY_ABS_GAP_ATR}
    OR own-clock 09:15 TV pctl >= {IN_PLAY_TV_PCTL_ELEVATED}
    OR (gap>=0.25 ATR AND TV pctl>=0.70)
    OR (TV pctl>=0.70 AND xs rank<=0.25)
    OR (gap>=0.25 ATR AND xs rank<=0.20)
  xs rank never sufficient by itself.
  News remains UNAVAILABLE.

OPENING IMPULSE is a 09:00–09:14 PATH:
  persist 09:00 open, 09:04 close, 09:14 close, first OR high time, first OR low time,
  MFE from open, MAE from open, net 5m, net OR15.
  Do NOT use 09:14 vs midpoint alone.

  FAILED_OPEN_REVERSAL: first spike >= {FAILED_SPIKE_FRAC} of OR range to one
  extreme, then 09:14 net move the other way. Exclude from PB1. Do not convert.

  CLEAN_OPENING_IMPULSE: net OR15 > {CLEAN_NET15_FRAC} of OR (bull), early 5m
  not opposite, MFE>=MAE, 09:14 close on that side of OR mid. Bear mirror.

  RANGE_OPEN / AMBIGUOUS_OPEN: exclude.
  PB1 emits only CLEAN_OPENING_IMPULSE, break DIR must match.

OR15 FREEZE: [{OR_START},{OR_END}] completed 1m; known from {OR_KNOWN_FROM}. Frozen.

REAL BREAK (not tick-creep):
  completed close beyond OR by {BREAK_BEYOND_OR_FRAC} of OR range or
  {BREAK_BEYOND_ATR_FRAC} of ATR20, with close location >= {BREAK_CLOSE_LOC}
  on the trend side of the break bar. Persist prev close, break OHLC, body,
  range, beyond, close location. Wick-only is not a break. Micro creep is not a break.
  Last break clock {LAST_BREAK}.

LEAVE: at least {MIN_AWAY_BARS} fully-away bars, OR one fully-away bar that
  extends >= {LEAVE_EXT_OR_FRAC} of OR range. Persist away_n, max_away, minutes outside.
  One-bar probe that barely clears is not a leave.

RETEST FRESHNESS: first return must begin within {RETEST_FRESHNESS_MIN} trading
  minutes of the valid break. Later = STALE_RETEST, episode ends. No later retest search.
  Persist break_to_retest_minutes. 30 is a definition of opening continuation, not a return search.

HOLD: first fresh retest close still trend-side. Close through = failed retest, not false-break.

TRIGGER: RECLAIM_RETEST_MICRO_HIGH or FAILED_PUSH_THEN_CLOSE_BACK after hold.
  Trigger cannot repair a bad open/break/leave. Last trigger {LAST_TRIGGER}.

EXECUTION: NEXT 1M OPEN. SAME_BAR_ENTRY_N=0. Flatten {SESSION_FLAT}. AM only.
  Minute OHLCV cannot prove queue/spread.

INVALIDATION: acceptance back inside through defended OR bound. Stop ref = retest extreme, not opposite OR.

REWARD SPACE at trigger, pre-known levels only:
  ROOM_AVAILABLE / ROOM_QUESTIONABLE / NO_OBVIOUS_ROOM.
  PATTERN_FACE_VALID and TRADE_FACE_VALID are separate questions.

NO FUTURE OUTCOME. NO MATCHING. NO PNL. NO NEW INDICATORS.
""".strip()


def machine_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    h.update(STATE_MACHINE_TEXT.encode("utf-8"))
    for name in ("definitions.py", "impulse.py", "inplay.py", "machine.py"):
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()
