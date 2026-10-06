"""Frozen state-machine semantics. Written before any economic test. Not a PnL object."""
from __future__ import annotations

import hashlib
from pathlib import Path

from research.pb1_opening_range_continuation_face_valid_v1 import (
    IN_PLAY_ABS_GAP_ATR,
    IN_PLAY_TV_WINDOW_PCTL,
    IN_PLAY_XS_RANK_MAX,
    LAST_BREAK,
    LAST_TRIGGER,
    NEAR_DAILY_MA_ATR,
    OR_END,
    OR_KNOWN_FROM,
    OR_START,
    SESSION_FLAT,
)

STATE_MACHINE_TEXT = f"""
PB1 OPENING-RANGE CONTINUATION — causal state machine (FACE VALIDITY).

THESIS (one side at a time; bear is the mirror):
  in-play stock → opening impulse defines OR15 → close-beyond OR_HIGH →
  first return to OR_HIGH → hold (no acceptance back inside) →
  1-minute price-action trigger → NEXT 1m OPEN.

NOT THIS MACHINE:
  opening-range false-break reversal.
  wick-only excursion.
  retrospective best-retest.
  opposite OR boundary as default stop.

IN-PLAY (descriptive tags, frozen before outcomes, not a grid):
  abs_gap_atr = |session_open − prior_close| / prior ATR20
  tv_0915 = sum TradingValue of 09:00–09:15 completed bars
  tv_0915_pctl = tv_0915 vs same-window prior {20} sessions (min 10)
  xs_rank_pct = rank of tv_0915 among the active 105-pool that clock (0 = most active)
  in_play = (abs_gap_atr >= {IN_PLAY_ABS_GAP_ATR})
            OR (tv_0915_pctl >= {IN_PLAY_TV_WINDOW_PCTL})
            OR (xs_rank_pct <= {IN_PLAY_XS_RANK_MAX})
  These half-spaces are round descriptive tags. They are not chosen from future returns.
  The generator records every causal continuation sequence and tags in_play.
  News/catalyst = UNAVAILABLE. Future daily volume is not used. MA is not an activity proxy.

OR15 FREEZE:
  OR_HIGH = max high, OR_LOW = min low, of completed 1m bars with
  time_label in [{OR_START}, {OR_END}] inclusive.
  Known only after the {OR_END} bar completes.
  At {OR_KNOWN_FROM} the pair is frozen for the rest of the session.
  The {OR_KNOWN_FROM} bar and all later bars never modify OR15.
  Incomplete OR (<15 bars) → no freeze → no playbook that session.

BREAK:
  Valid continuation break = a completed 1m CLOSE strictly beyond the defended
  OR boundary (bull: close > OR_HIGH; bear: close < OR_LOW), at or after {OR_KNOWN_FROM},
  and no later than {LAST_BREAK} (opening continuation, not a late-morning range break).
  Wick-only (extreme beyond, close still inside) is recorded, never a valid break.
  Follow-through (next completed close still beyond) is recorded, never required.
  First valid close-beyond of the OPENING-IMPULSE side starts the episode.
  Opening impulse direction = sign(09:14 close − OR midpoint), else sign of the gap.
  The opposite OR boundary is not a continuation of the opening impulse
  (that failed-open breakdown is a different thesis).
  Later nicer breaks are ignored.

LEAVE:
  After the break, at least one completed bar must sit entirely on the trend side
  of the boundary (bull: low > OR_HIGH). A next-bar tag of the same probe is not a retest.

RETEST:
  Only after that leave: the first later bar whose range returns to the already-known
  OR boundary (bull: low <= OR_HIGH; bear: high >= OR_LOW).
  First causal episode only. No look-ahead to a later prettier retest.

HOLD:
  On that first retest bar, the completed close remains on the trend side
  of the defended boundary (bull: close >= OR_HIGH; bear: close <= OR_LOW).
  A retest bar that closes back through the boundary is a failed retest:
  episode dies. That is not a continuation event and is not converted into
  a false-break reversal.
  After a successful hold, one later completed close through the defended
  boundary also kills the episode (acceptance back inside OR15).

1M TRIGGER (timing only, after break + successful hold, pos > retest_pos):
  Classify which completed-bar pattern actually occurred; do not pick a winner.
  BULL:
    RECLAIM_RETEST_MICRO_HIGH = close > high of the retest bar
    FAILED_PUSH_THEN_CLOSE_BACK = low < OR_HIGH and close >= OR_HIGH
    HIGHER_LOW_MINOR_SWING_BREAK = close > last confirmed 1m swing high
      (pivot at pos-1 known only after bar pos completes)
  BEAR: mirrors.
  First bar after retest that prints at least one of these patterns is the trigger.
  All matching labels on that bar are stored. No later trigger search.

EXECUTION:
  Earliest historical approximation = NEXT 1m OPEN after the trigger bar completes.
  SAME_BAR_ENTRY_N = 0.
  If the next bar is lunch, missing, or at/after {SESSION_FLAT}, no event.
  Last allowed trigger clock = {LAST_TRIGGER} (AM-only opening-range thesis).
  Minute OHLCV cannot prove queue position or spread.

INVALIDATION (thesis-matched, recorded at entry, not a bps stop):
  Bull continuation is wrong when price establishes acceptance back inside
  OR15 below the defended OR_HIGH. Structural stop reference = first-retest low.
  Do not default to OR_LOW (too wide for a breakout-retest).
  Persist: retest extreme, OR boundary, VWAP. No fixed-bps stop.

TARGET SPACE (pre-known at entry, not optimized):
  PDH/PDL, prior close, daily SMA25/SMA75 if in the trade direction,
  D5 high/low, VWAP if it still sits ahead.
  Question: is there room to the next obvious opposing location vs structural risk?

VWAP: location diagnostic at break / retest / trigger
  (trend-side / testing / opposed). Not required for the primary candidate.

PARTICIPATION: characterize opening-impulse vs retest vs reacceleration
  with clock-percentile TradingValue. Do not gate the event on TV pctl > X.

DAILY SMA5/25/75: BIAS / LOCATION only. Not required for PB1 face-validity.
  Record bull_aligned / bear_aligned / mixed and near SMA25 / SMA75
  (|px − MA| / ATR20 <= {NEAR_DAILY_MA_ATR}).

NO FUTURE OUTCOME. NO MATCHING. NO PNL.
""".strip()


def machine_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    h.update(STATE_MACHINE_TEXT.encode("utf-8"))
    for name in ("definitions.py", "or15.py", "machine.py"):
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()
