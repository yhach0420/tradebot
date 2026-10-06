"""Family scoring on data / causality / independence / runtime only. No PnL."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.next_driver_selection import (
    BACKUP_BREADTH,
    CASE_FUTURES,
    CASE_HARNESS,
    CASE_LEADER,
    CASE_NONE,
    FROZEN_LEADER_SET_MAX_N,
    KABU_RUNTIME_SLOT_N,
    NEW_DRIVER_FAMILY_ID,
    NEXT_ACQUIRE,
    NEXT_FIX_HARNESS,
    NEXT_PRECOMMIT_FUTURES,
    NEXT_PRECOMMIT_LEADER,
    PRIMARY_INDEX_FUTURES,
    PRIMARY_LEADER_LAGGARD,
    TRADE_CANDIDATE_HEADROOM_N,
)


CRITERIA = (
    "INFORMATION_INDEPENDENCE",
    "CAUSAL_TIMING",
    "HISTORICAL_COVERAGE",
    "TIMESTAMP_QUALITY",
    "RUNTIME_AVAILABILITY",
    "TARGET_MAPPING",
    "PRIOR_EXHAUSTION",
    "IMPLEMENTATION_FEASIBILITY",
)


def _score_row(family: str, scores: dict[str, str], ready: bool, notes: str) -> dict[str, Any]:
    row = {"family": family, "ready": ready, "notes": notes}
    row.update(scores)
    return row


def selection_matrix(*, rca: dict[str, Any], futures: dict[str, Any]) -> dict[str, Any]:
    harness_block = bool((rca.get("contract_gap") or {}).get("shared_harness_blocks_next_driver"))
    asof_ok = float(rca.get("dev_asof_usable_rate") or 0.0) >= 0.50
    c1_cov = float(rca.get("c1_mean_dates_with_am_bar_per_symbol") or 0.0) >= 0.90 * float(rca.get("c1_expected_dates") or 1)
    dev_cov = float(rca.get("dev_mean_dates_with_am_bar_per_symbol") or 0.0) >= 0.90 * float(rca.get("dev_expected_dates") or 1)
    stock_panel_ready = bool(rca.get("ok")) and asof_ok and c1_cov and dev_cov and not rca.get("missing_files")

    futures_ready = bool(futures.get("ready_as_next_driver"))
    leader_ready = stock_panel_ready and not harness_block
    breadth_ready = stock_panel_ready and not harness_block

    rows = [
        _score_row(
            PRIMARY_INDEX_FUTURES,
            {
                "INFORMATION_INDEPENDENCE": "HIGH",
                "CAUSAL_TIMING": "HIGH_IF_AVAILABLE_AT_PROVEN",
                "HISTORICAL_COVERAGE": "FAIL_DEV_C1_ABSENT",
                "TIMESTAMP_QUALITY": "LIVE_ONLY_UNPROVEN_HIST",
                "RUNTIME_AVAILABILITY": "YES_KABU_PUSH",
                "TARGET_MAPPING": "HIGH_MARKET_THEN_SECTOR",
                "PRIOR_EXHAUSTION": "UNTESTED_TRUE_FUTURES",
                "IMPLEMENTATION_FEASIBILITY": "BLOCKED_NO_HISTORY",
            },
            futures_ready,
            futures.get("reason") or "",
        ),
        _score_row(
            PRIMARY_LEADER_LAGGARD,
            {
                "INFORMATION_INDEPENDENCE": "HIGH_OTHER_SYMBOL_NOT_TARGET_SELF",
                "CAUSAL_TIMING": "YES_LEADER_LE_T",
                "HISTORICAL_COVERAGE": "PASS_DEV_C1_STOCK_1M" if stock_panel_ready else "FAIL",
                "TIMESTAMP_QUALITY": "PROVEN_BAR_START",
                "RUNTIME_AVAILABILITY": f"YES_IF_FROZEN_LEADER_SET_LE_{FROZEN_LEADER_SET_MAX_N}_PLUS_CANDIDATES_LE_{KABU_RUNTIME_SLOT_N}",
                "TARGET_MAPPING": "YES_SECTOR_PEER_OR_LARGE_CAP_LEADER",
                "PRIOR_EXHAUSTION": "UNTESTED_AS_CROSS_SECTIONAL_DRIVER_NOT_A_RENAME_OF_PB1",
                "IMPLEMENTATION_FEASIBILITY": "YES_LEAVE_TARGET_OUT",
            },
            leader_ready,
            "Requires LEAVE_TARGET_OUT. Small frozen leader set must be specified in precommit, not after seeing results.",
        ),
        _score_row(
            BACKUP_BREADTH,
            {
                "INFORMATION_INDEPENDENCE": "MEDIUM_MARKET_STATE_OVERLAP",
                "CAUSAL_TIMING": "YES_AGGREGATE_LE_T",
                "HISTORICAL_COVERAGE": "PASS_DEV_C1_STOCK_1M" if stock_panel_ready else "FAIL",
                "TIMESTAMP_QUALITY": "PROVEN_BAR_START",
                "RUNTIME_AVAILABILITY": "YES_FROM_REGISTERED_NAMES",
                "TARGET_MAPPING": "YES_SECTOR_THEN_SYMBOL",
                "PRIOR_EXHAUSTION": "UNTESTED_BUT_OVERLAPS_NATIVE_CONTEXT",
                "IMPLEMENTATION_FEASIBILITY": "YES_LEAVE_TARGET_OUT",
            },
            breadth_ready,
            "Backup only. Do not promote over leader-laggard when both are ready.",
        ),
        _score_row(
            "GLOBAL_EXTERNAL_US_ASIA_COMMODITY",
            {
                "INFORMATION_INDEPENDENCE": "HIGH",
                "CAUSAL_TIMING": "UNPROVEN",
                "HISTORICAL_COVERAGE": "FAIL",
                "TIMESTAMP_QUALITY": "FAIL",
                "RUNTIME_AVAILABILITY": "AMBIGUOUS",
                "TARGET_MAPPING": "PARTIAL_CATALOG",
                "PRIOR_EXHAUSTION": "CME_KOREA_HKG_CLOSED_OR_BLOCKED",
                "IMPLEMENTATION_FEASIBILITY": "NO_AMBIGUOUS_SOURCE",
            },
            False,
            "True CME blocked. Korea unavailable. HKG simultaneous-only. Other FX/rates/commodity source ambiguous.",
        ),
    ]

    if harness_block:
        return {
            "VERDICT": CASE_HARNESS,
            "NEXT": NEXT_FIX_HARNESS,
            "PRIMARY_NEXT_DRIVER": None,
            "BACKUP_DRIVER": None,
            "rows": rows,
            "harness_block": True,
            "stock_panel_ready": stock_panel_ready,
            "futures_ready": futures_ready,
            "leader_ready": False,
            "runtime": {
                "kabu_slot_n": KABU_RUNTIME_SLOT_N,
                "frozen_leader_set_max_n": FROZEN_LEADER_SET_MAX_N,
                "trade_candidate_headroom_n": TRADE_CANDIDATE_HEADROOM_N,
                "leader_plus_candidates_fits_50": FROZEN_LEADER_SET_MAX_N + TRADE_CANDIDATE_HEADROOM_N <= KABU_RUNTIME_SLOT_N,
            },
            "new_driver_family_id": NEW_DRIVER_FAMILY_ID,
            "phase0_enum_mutated": False,
        }

    if futures_ready:
        return {
            "VERDICT": CASE_FUTURES,
            "NEXT": NEXT_PRECOMMIT_FUTURES,
            "PRIMARY_NEXT_DRIVER": PRIMARY_INDEX_FUTURES,
            "BACKUP_DRIVER": PRIMARY_LEADER_LAGGARD if leader_ready else BACKUP_BREADTH,
            "rows": rows,
            "harness_block": False,
            "stock_panel_ready": stock_panel_ready,
            "futures_ready": True,
            "leader_ready": leader_ready,
            "runtime": {
                "kabu_slot_n": KABU_RUNTIME_SLOT_N,
                "frozen_leader_set_max_n": FROZEN_LEADER_SET_MAX_N,
                "trade_candidate_headroom_n": TRADE_CANDIDATE_HEADROOM_N,
                "leader_plus_candidates_fits_50": True,
            },
            "new_driver_family_id": NEW_DRIVER_FAMILY_ID,
            "phase0_enum_mutated": False,
            "why": "Index futures have Development+C1 historical 1m with same semantics.",
        }

    if leader_ready:
        return {
            "VERDICT": CASE_LEADER,
            "NEXT": NEXT_PRECOMMIT_LEADER,
            "PRIMARY_NEXT_DRIVER": PRIMARY_LEADER_LAGGARD,
            "BACKUP_DRIVER": BACKUP_BREADTH,
            "rows": rows,
            "harness_block": False,
            "stock_panel_ready": stock_panel_ready,
            "futures_ready": False,
            "leader_ready": True,
            "runtime": {
                "kabu_slot_n": KABU_RUNTIME_SLOT_N,
                "frozen_leader_set_max_n": FROZEN_LEADER_SET_MAX_N,
                "trade_candidate_headroom_n": TRADE_CANDIDATE_HEADROOM_N,
                "leader_plus_candidates_fits_50": FROZEN_LEADER_SET_MAX_N + TRADE_CANDIDATE_HEADROOM_N <= KABU_RUNTIME_SLOT_N,
            },
            "new_driver_family_id": NEW_DRIVER_FAMILY_ID,
            "phase0_enum_mutated": False,
            "why": (
                "Index futures lack Dev+C1 historical 1m. Leader-laggard uses existing 105 1m, "
                "other-symbol information with LEAVE_TARGET_OUT, is independent of PB1 target-self structure, "
                "and can drop to a small frozen leader set plus trade candidates within 50 Kabu slots."
            ),
        }

    return {
        "VERDICT": CASE_NONE,
        "NEXT": NEXT_ACQUIRE,
        "PRIMARY_NEXT_DRIVER": None,
        "BACKUP_DRIVER": None,
        "rows": rows,
        "harness_block": False,
        "stock_panel_ready": stock_panel_ready,
        "futures_ready": False,
        "leader_ready": False,
        "runtime": {
            "kabu_slot_n": KABU_RUNTIME_SLOT_N,
            "frozen_leader_set_max_n": FROZEN_LEADER_SET_MAX_N,
            "trade_candidate_headroom_n": TRADE_CANDIDATE_HEADROOM_N,
        },
        "new_driver_family_id": NEW_DRIVER_FAMILY_ID,
        "phase0_enum_mutated": False,
        "why": "No family meets historical coverage + timestamp + runtime together. Do not force a weak family.",
    }
