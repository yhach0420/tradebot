"""Frozen event-time contract. Written before any capture outcome is read."""
from __future__ import annotations

RESET_IMPLEMENTATION = """
SESSION
AM is [09:00:00, 11:30:00). PM is [12:30:00, 15:00:00). These are the existing research session bounds.
A window never crosses a session boundary. Lunch and the overnight gap are not bins.
Each session starts with episode_open false for A, B, and C independently.

EVENT CLOCK
Events stay in received order. A session array is stable-sorted by event time, keeping received order on ties.
Feature time T is that event's time. No later event enters a feature.
Volume delta is attached before the sort, from the received-order cumulative TradingVolume cursor.

WINDOWS
Intervals are half-open on the left and closed on the right.
current 10s = (T-10s, T]
prior 10s bin k, k=1..6 = (T-10s*(k+1), T-10s*k]
current 30s = (T-30s, T]
prior 30s bin k, k=1..4 = (T-30s*(k+1), T-30s*k]
An empty bin has volume 0 and tick count 0.
VOL_ACCEL_10 is eligible only when T-70s is at or after the session open, so all six prior bins sit inside the session.
VOL_ACCEL_30 is eligible only when T-150s is at or after the session open.
Comparison is strict greater than the median. Equality fails. No multiplier.

VOLUME CLASSIFICATION
ASK_CLASSIFIED_VOLUME: positive TradingVolume delta, finite price, finite ask > 0, price + 1e-12 >= ask.
BID_CLASSIFIED_VOLUME: the same delta was not ask-classified, finite bid > 0, price - 1e-12 <= bid.
There is no exchange aggressor flag. Inside-spread volume stays unclassified.
BUY_DOMINANT_10: ask-classified volume in the current 10s > bid-classified volume in the current 10s.
FULL rejects the event when ask-classified 10s + bid-classified 10s == 0.

TICK
One tick is one continuous event with a finite positive CurrentPrice.
TICK_ACCEL_10 uses the same six prior 10s bins and the same session-open rule as VOL_ACCEL_10.
Strict greater than the median. No multiplier.

PRICE BREAK
PRE_BREAK_HIGH is the maximum CurrentPrice among events in [T-60s, T).
The current event is excluded. Events with the same timestamp as T are excluded.
The previous causal price is the latest finite price at an earlier received position.
PRICE_BREAK requires a finite previous price, a finite PRE_BREAK_HIGH,
previous price <= PRE_BREAK_HIGH, and CurrentPrice[T] > PRE_BREAK_HIGH.

SPREAD
The trigger event itself must be a finite two-sided fresh executable quote:
executable, not special, fresh_sec <= 5, bid > 0, ask >= bid, both quantities >= 100.
SPREAD_NOT_WORSE: ask-bid at T <= median ask-bid of quotes passing that same test in [T-60s, T).
No prior fresh quote makes the condition false. No bps cutoff is searched.

POPULATIONS
A = VOL_ACCEL_10 AND VOL_ACCEL_30 AND BUY_DOMINANT_10 AND classified 10s volume > 0
    AND TICK_ACCEL_10 AND PRICE_BREAK AND SPREAD_NOT_WORSE.
B = PRICE_BREAK. Other flags may also be true. This is break alone.
C = PRICE_BREAK AND VOL_ACCEL_10 AND VOL_ACCEL_30 AND NOT BUY_DOMINANT_10.
A, B, and C are not mutually exclusive samples. Each has its own episode latch.

FIRST EVENT AND RESET
This rule is fixed before outcomes are inspected.
For one population predicate P, in one session:
episode_open starts false and break_level is unset.
If episode_open is false and P is true, admit this event, set episode_open true,
and freeze break_level to this event's PRE_BREAK_HIGH.
While episode_open is true, admit nothing.
Clear the episode only when both are true at a later event:
CurrentPrice is finite and CurrentPrice <= the frozen break_level, and P is false.
The clearing event is not a signal.
The frozen level is the admitted event's PRE_BREAK_HIGH, not a level recomputed later.
A session boundary clears every latch. Nothing carries into the next session or the next day.
""".strip()
