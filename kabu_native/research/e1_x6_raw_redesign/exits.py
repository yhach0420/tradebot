"""ENTRY-basis EXIT packages (definitions frozen in Phase A; executed in Phase B).

Two packages, both JointStrategyPackage halves tied to the setup rationale.
The evaluation/priority order below is the frozen contract for Phase B.
"""
from __future__ import annotations

from typing import Any

MAX_STRUCTURAL_RISK_BPS = 60.0   # entry rejected when structural stop is further away
NO_PROGRESS_MIN_EDGE = "cost(5bps) + 1 tick equivalent in bps at entry price"

INVALIDATION_DEFINITIONS: dict[str, list[str]] = {
    "CONT": [
        "mid falls back below breakout level (trigger_level) by >=1 tick",
        "short-term direction reversed for 2 consecutive grids (ret_30s_bps<0 twice)",
        "high updates stopped AND support lost: mid<low_60s and high_60s not updated for >=6 grids",
    ],
    "PULL": [
        "mid falls back below reclaim level (trigger_level) by >=1 tick",
        "mid breaks the pullback low recorded at SETUP",
        "when VWAP support was used: mid below as-of VWAP for 3 consecutive grids",
    ],
    "BREAK": [
        "mid returns inside the pre-breakout compression range (below range high)",
        "cannot hold above range high for 2 consecutive grids",
        "breakout failure: vol_ratio_60_300<1.0 within 60s after trigger",
    ],
}

EXIT_PACKAGES: list[dict[str, Any]] = [
    {
        "exit_id": "EXIT_A_STRUCTURAL",
        "initial_stop": (
            "structural: setup reference level - 1 tick "
            "(CONT: breakout level; PULL: pullback low; BREAK: compression range low); "
            f"entry REJECTED if implied risk > {MAX_STRUCTURAL_RISK_BPS}bps"
        ),
        "invalidation": "per-setup list (INVALIDATION_DEFINITIONS)",
        "no_progress": f"exit if after 180s unrealized gain < {NO_PROGRESS_MIN_EDGE}",
        "trailing": None,
        "target": None,
        "max_hold_sec": 600,
        "session_close": "force exit at session end (censored if window ends first)",
    },
    {
        "exit_id": "EXIT_B_STRUCTURAL_TRAIL",
        "initial_stop": "same structural stop and entry-risk rejection as EXIT_A",
        "invalidation": "same per-setup list as EXIT_A",
        "no_progress": f"exit if after 120s unrealized gain < {NO_PROGRESS_MIN_EDGE}",
        "trailing": (
            "armed only after +1R (unrealized gain >= initial risk); then trail: "
            "exit when giveback from max favorable >= 50% of gained R"
        ),
        "target": None,
        "max_hold_sec": 420,
        "session_close": "force exit at session end (censored if window ends first)",
    },
]

# Frozen per-event evaluation order for Phase B (same timestamp => this priority).
EXIT_EVALUATION_ORDER = [
    "1. update rolling structures (max favorable, elapsed, levels)",
    "2. SESSION_CLOSE / window end (censor)",
    "3. INVALIDATION (setup basis)",
    "4. STOP (structural initial stop)",
    "5. NO_PROGRESS",
    "6. TRAILING (EXIT_B only, only when armed)",
    "7. MAX_HOLD",
]

REENTRY_RULE = "re-entry into the same episode is forbidden (episode ends when setup condition disappears)"
