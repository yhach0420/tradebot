"""Correction V2 state-machine text. New SHA. Parents frozen."""
from __future__ import annotations

import hashlib
from pathlib import Path

from research.pb1_v4_clarified_machine_correction_v2 import (
    ATR_SANITY_FRAC,
    E1_BODY_N1M,
    E1_NET_N1M,
    E1_RANGE_N1M,
    EXEC_1M_CONFIRMED,
    EXEC_5M_DIRECT,
    EXPECTED_SPEC_SHA256,
    LAST_BODY_MIN,
    OPERATIONAL_AM_LAST,
    OR_KNOWN_FROM,
    PARENT_CLARIFIED_MACHINE_SHA256,
    PRIMARY_SETUP_TIMEFRAME,
    TRUE_DISP_MIN,
    TRUE_RANGE_MIN,
)
from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction_rca import V4_CORRECTED_MACHINE_SHA256

STATE_MACHINE_TEXT = f"""
PB1 V4 CLARIFIED MACHINE CORRECTION V2. Bound to SPEC_SHA256 {EXPECTED_SPEC_SHA256}.
INTRADAY 5M CONTINUATION. NOT 1-MINUTE SCALPING. PRIMARY TF {PRIMARY_SETUP_TIMEFRAME}.

FROZEN PARENTS (do not mutate):
  READY_V1 spec
  CLARIFIED_V2 spec
  parent clarified machine {PARENT_CLARIFIED_MACHINE_SHA256}
  corrected machine {V4_CORRECTED_MACHINE_SHA256}
  leaked machine {V4_LEGACY_LEAKED_IMPLEMENTATION}

STATES (distinct; no mixed eligibility blob):
  WHY_THIS_STOCK
  OPENING_DRIVE_SEED
  OPENING_DRIVE_ACTIVE
  LOCATION_IDENTIFIED
  THESIS_READY
  E0_5M_CONFIRMATION
  E1_1M_LEVEL_INTERACTION
  EXECUTION_READY
  THESIS_LOST

IDENTITIES (immutable causal chain, no cross-episode reuse):
  candidate_day_id @ WHY
  opening_seed_id @ SEED
  opening_drive_id @ ACTIVE
  location_id @ LOCATION_IDENTIFIED
  interaction_id @ interaction
  thesis_id @ THESIS_READY
  execution_id @ E0/E1

IMPLEMENTATION BINDING:
  A. first unsuccessful location observation does NOT automatically kill thesis.
  B. OR family B: real drive + real leave -> OR boundary is LOCATION_CANDIDATE / LOCATION_IDENTIFIED.
     Hold may validate execution. Hold does not invent the level identity.

S0 WHY_THIS_STOCK: same-clock 09:00-09:04 / 09:05-09:09 / 09:10-09:14 baselines. Not first-bar-only.
ATR20 is a sanity scale (frac {ATR_SANITY_FRAC}), not profit-tuned.

SEED taxonomy by auction path, not TRUE-first:
  TRUE_OPENING_DRIVE_SEED: one-sided or middle-only pullback + continued-intent STATE.
    Substantial counter-auction remains TWO_SIDED even if the last bar commits.
    One-bar-heavy opening is invalid only when followthrough does not continue the auction.
    disp/scale >= {TRUE_DISP_MIN}, own-clock range >= {TRUE_RANGE_MIN}. Last crawl < {LAST_BODY_MIN} fails.
  FAILED_OPEN_SEED: causal path only.
    FORM A DIRECTIONAL_FAILED_ATTEMPT: directional body + visible range + later invalidation.
    FORM B WIDE_REJECTION_FAILED_ATTEMPT: small-body first bar with unresolved interior close,
      visible range vs own clock and ATR sanity, then opposite auction forming/established.
    Wide doji/range geometry alone is not FAILED_OPEN.
  NO_VALID_DRIVE_SEED: TWO_SIDED / FLAT_OR_CRAWL / MICRO_OR_LEAK.

ACTIVE stall resets only on RENEWED_DIRECTIONAL_AUCTION:
  REAL_BREAKOUT_EXTENSION or MEANINGFUL_DIRECTIONAL_EXTENSION.
  RANGE_DRIFT_EXTREME / MARGINAL_EXTREME_ONLY / NO_DIRECTIONAL_PROGRESS do not reset.
  Any new wick extreme is not progress. No 09:30/09:45/N-bar clock expiry.

LOCATION family A: preexisting structural level far-side cleared (A1/A2).
  VWAP/MA are confluence/context, not A_CLEARED_ZONE (A3 must not mint family A).
  09:14 identification is allowed when a genuine causal clear exists.
  LOCATION_IDENTIFIED remains separate from LOCATION_INTERACTION. No future hold required.

LOCATION_IDENTIFIED is a 5m thesis state. 1m cannot create it.
E0 {EXEC_5M_DIRECT}: from THESIS_READY, completed 5m hold + continuation, then next 1m open.
E1 {EXEC_1M_CONFIRMED}: from same THESIS_READY, 1m at the pre-identified level.
  families: 3-bar net/N1M {E1_NET_N1M}, range/N1M {E1_RANGE_N1M}, body/N1M {E1_BODY_N1M}.
E1 cannot alter opening_drive_id, location_id, direction, thesis_id, and cannot revive thesis.

THESIS_READY = WHY + ACTIVE + LOCATION_IDENTIFIED + thesis alive. No extra gate.

OR known {OR_KNOWN_FROM}. Operational AM window {OPERATIONAL_AM_LAST} is operational, not semantic expiry.

HIDDEN_1M_THESIS_PARITY required.
NO: PnL, Confirmation, Frozen Validation, prospective unseen, V4.1, V3 spec, symbol overrides.
""".strip()

MACHINE_FILES = (
    "definitions.py",
    "binding.py",
    "baselines.py",
    "s0.py",
    "seed.py",
    "active.py",
    "location.py",
    "thesis.py",
    "continuation.py",
    "execution.py",
    "machine.py",
    "walk.py",
)


def machine_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    h.update(STATE_MACHINE_TEXT.encode("utf-8"))
    h.update(EXPECTED_SPEC_SHA256.encode("utf-8"))
    for name in MACHINE_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()
