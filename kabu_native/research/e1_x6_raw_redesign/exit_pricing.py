"""Executable-price EXIT contract (Phase A-R1 §9). Definitions only, frozen to P1_R1.

All long trades:
- ENTRY price   : ask at the OPEN-confirmed grid
- EXIT price    : bid at the EXIT-confirmed grid
- STOP firing   : bid <= stop_level
- invalidation  : state test on mid (per setup spec); FILL at same-grid bid
- unrealized gain / MFE / +1R / no-progress / trailing: liquidation-value basis
  using the CURRENT BID
- initial risk  : R = entry_ask - stop_level (stop_level from the frozen packet)
- cost          : 5bps once per round trip (identical to canonical implementation)
- same-timestamp priority: the P1 order (session_close -> invalidation -> STOP
  -> no_progress -> trailing -> max_hold)
"""
from __future__ import annotations

EXIT_PRICE_BASIS = {
    "entry_price": "ask at OPEN-confirmed grid",
    "exit_price": "bid at EXIT-confirmed grid",
    "stop_firing": "bid <= stop_level",
    "invalidation_state_test": "mid conditions from the setup spec (frozen levels)",
    "invalidation_fill": "bid of the same grid",
    "unrealized_basis": "current bid (liquidation value): unrealized = bid - entry_ask",
    "initial_risk": "R = entry_ask - stop_level (frozen at TRIGGERED); entry REJECTED if R/entry_ask*1e4 > 60bps",
    "cost": "5bps once per round trip (same as existing canonical implementation)",
    "mfe": "max over held grids of (bid - entry_ask)",
    "same_timestamp_priority": [
        "1. SESSION_CLOSE / window end",
        "2. INVALIDATION",
        "3. STOP",
        "4. NO_PROGRESS",
        "5. TRAILING (EXIT_B only, only when armed)",
        "6. MAX_HOLD",
    ],
}

# EXIT_B trailing as numeric formulas (no ambiguity left for Phase B):
EXIT_B_TRAILING_FORMULA = {
    "R": "entry_ask - stop_level  (yen, > 0 enforced at entry)",
    "gain(t)": "bid(t) - entry_ask  (yen, liquidation value)",
    "arm_condition": "trailing is ARMED at the first grid where gain(t) >= R (i.e. +1R reached); boundary: gain == R arms",
    "max_favorable_R": "M(t) = max_{s<=t, s>=open} gain(s) / R  (updated on each grid, bid basis)",
    "trailing_floor": "floor(t) = entry_ask + (M(t) - giveback_R(t)) * R  with giveback_R(t) = 0.5 * M(t)",
    "floor_simplified": "floor(t) = entry_ask + 0.5 * M(t) * R",
    "fire_condition": "armed AND bid(t) <= floor(t); boundary: bid == floor fires",
    "fill": "bid of the firing grid",
    "notes": "M(t) is monotone nondecreasing; floor is monotone nondecreasing after arming",
}

NO_PROGRESS_FORMULA = {
    "EXIT_A": "at t_open+180s: exit if gain(t) < cost_yen + 1*tick(entry_ask)",
    "EXIT_B": "at t_open+120s: exit if gain(t) < cost_yen + 1*tick(entry_ask)",
    "cost_yen": "entry_ask * 5bps (one round-trip cost, per-share yen)",
    "tick": "dynamic resolver tick at entry_ask for the symbol class",
}
