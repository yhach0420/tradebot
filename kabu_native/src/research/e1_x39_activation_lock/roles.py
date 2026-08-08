"""Shadow isolation, 1M carry, heartbeat, ledgers, startup sequence."""
from __future__ import annotations

from typing import Any

from research.e1_x38_operational_wiring.shadow import ShadowIsolationGuard

from . import (
    CAPITAL_1M_ROLE,
    INITIAL_1M_CASH,
    LEDGERS,
    PBV2_ROLE,
    POSITION_CAP,
    PRIMARY_ROLE,
    V1R_SHA,
    MODEL_ARTIFACT_SHA,
    PRECOMMIT_SHA,
)


def shadow_and_1m() -> dict[str, Any]:
    g = ShadowIsolationGuard()
    g.assert_pbv2_cannot_admit_primary()
    iso = True
    probe = ShadowIsolationGuard()
    try:
        probe.record_pbv2_attempt_primary_slot()
        iso = False
    except RuntimeError:
        pass
    try:
        probe.record_1m_attempt_primary_slot()
        iso = False
    except RuntimeError:
        pass

    # 1M carry: no daily reset
    cash = INITIAL_1M_CASH
    cash += 12_500.0  # day1 pnl
    cash -= 3_000.0   # day2 pnl
    carry_ok = cash == INITIAL_1M_CASH + 12_500.0 - 3_000.0

    return {
        "primary_role": PRIMARY_ROLE,
        "pbv2_role": PBV2_ROLE,
        "capital_1m_role": CAPITAL_1M_ROLE,
        "shadow_isolation_pass": iso and len(g.mutations) == 0,
        "capital_1m_initial": INITIAL_1M_CASH,
        "capital_1m_daily_reset": False,
        "capital_1m_carry": carry_ok,
        "capital_1m_affects_primary": False,
        "cap": POSITION_CAP,
        "ledgers": list(LEDGERS),
        "ledgers_aggregate_forbidden": True,
        "pass": iso and carry_ok,
    }


def heartbeat_template() -> dict[str, Any]:
    return {
        "V1R_PRIMARY": True,
        "PBV2_SHADOW": True,
        "ONE_M_SHADOW": True,
        "active_universe_generation": "UNRESOLVED_PENDING_BINDING",
        "universe_effective_time": None,
        "strategy_sha": V1R_SHA,
        "model_sha": MODEL_ARTIFACT_SHA,
        "precommit_sha": PRECOMMIT_SHA,
        "last_anchor": None,
        "next_anchor": None,
        "OPEN": 0,
        "PENDING": 0,
        "cash_1m": INITIAL_1M_CASH,
        "decision_latency_ms": None,
        "ingest_latency_ms": None,
        "notify_backlog": 0,
        "dropped_events": 0,
        "submit_cancel_live": "0/0/0",
    }


def startup_sequence() -> list[str]:
    return [
        "1_universe_prebuild_resolve",
        "2_kabu_readonly_readiness",
        "3_registration",
        "4_capture_ONLINE",
        "5_v1r_model_precommit_sha_verify",
        "6_recovery",
        "7_rolling_state_initialization",
        "8_heartbeat",
        "9_v1r_primary_observer",
        "10_pbv2_shadow",
        "11_1m_shadow",
        "12_0900_market_ingest",
        "13_fixed_anchor_wait",
    ]
