"""Freshness-constant inventory. Classification only. No Runtime write."""
from __future__ import annotations

from typing import Any

FRESHNESS_INVENTORY: list[dict[str, Any]] = [
    {
        "file": "src/small_paper/v1r_primary_runtime.py",
        "function": "BOARD_FRESHNESS_SEC_V1R",
        "value": "5.0",
        "purpose": "Frozen V1R execution freshness. Comment: NOT YAML 3.0.",
        "class": "EXECUTION_SAFETY",
    },
    {
        "file": "src/research/e1_x28_executable_joint/__init__.py",
        "function": "BOARD_FRESHNESS_SEC",
        "value": "5.0",
        "purpose": "Executable quote max age vs quote clock on the same event; fill/exit scans.",
        "class": "EXECUTION_SAFETY",
    },
    {
        "file": "src/research/executable_target_v2_b_threshold/contract.py",
        "function": "last_executable_mid",
        "value": "break if (t_at-event_t)>5 or fresh_sec>5",
        "purpose": "V2/V3-M0 historical label copied execution 5s as mark age. That copy is the question V4 tests.",
        "class": "HISTORICAL_PRICE_MARK",
        "note": "EXECUTION_SAFETY 5s reused as label TTL. V4 must not inherit this for M4.",
    },
    {
        "file": "src/research/e1_x34a_execution_policy/arms.py",
        "function": "_row_ask_ok / find_ask_cross_fill",
        "value": "fresh_sec > BOARD_FRESHNESS_SEC reject",
        "purpose": "Passive Fill will not cross an event whose payload quote clock is >5s old.",
        "class": "EXECUTION_SAFETY",
    },
    {
        "file": "src/small_paper/v1r_native_entry_live.py",
        "function": "extract_board_row.fresh_sec",
        "value": "board_age_sec or event_t - CurrentPriceTime/AskTime/BidTime",
        "purpose": "Payload health: quote clock vs ingest time of THIS event. Not inter-PUSH TTL.",
        "class": "LIVE_DATA_HEALTH",
    },
    {
        "file": "src/small_paper/v1r_native_entry_live.py",
        "function": "_BoardBuf.append / _run_anchor snapshot",
        "value": "last row kept until next append; snapshot_age_ms = t0 - last_t",
        "purpose": "Runtime current board state is last PUSH. No timer zeros the book.",
        "class": "HISTORICAL_PRICE_MARK",
        "note": "Supports last-observed-state-remains-current (A).",
    },
    {
        "file": "src/small_paper/v1r_native_entry_live.py",
        "function": "compact_tail(20000)",
        "value": "memory bound, not market TTL",
        "purpose": "Drops old ticks in live memory. Not an exchange expire.",
        "class": "LIVE_DATA_HEALTH",
    },
    {
        "file": "src/small_paper/config.py / entry_scan_controller.py",
        "function": "entry_max_price_age_sec / entry_max_board_age_sec / board_stale_threshold_sec",
        "value": "3.0 YAML default",
        "purpose": "PBv2/live scan stale-socket guards. Isolated from V1R Primary (PBV2_SHADOW_ONLY_KEYS).",
        "class": "LIVE_DATA_HEALTH",
    },
    {
        "file": "src/small_paper/v1r_primary_runtime.py",
        "function": "event_stale_threshold_sec / board_stale_threshold_sec / trade_stale_threshold_sec",
        "value": "YAML; V1R Primary uses BOARD_FRESHNESS_SEC_V1R=5 not YAML 3",
        "purpose": "Shadow/PBv2 stale guards vs frozen V1R 5s fill.",
        "class": "LIVE_DATA_HEALTH",
    },
    {
        "file": "src/research/e1_x34b_entry_execution/features.py",
        "function": "preentry_from_board fresh_sec / event_rate_60s",
        "value": "feature from last board; event_rate = n_events/60",
        "purpose": "MODEL_FEATURE. Quiet names have low event_rate; not a mark TTL.",
        "class": "MODEL_FEATURE",
    },
    {
        "file": "src/research/uniform10_entry_rebuild/features.py",
        "function": "fresh_sec spec",
        "value": "event_t - quote_time",
        "purpose": "Feature catalog. Not TARGET M4 gate.",
        "class": "MODEL_FEATURE",
    },
    {
        "file": "src/research/e1_x28_executable_joint/board.py",
        "function": "fresh_sec = recv - CurrentPriceTime",
        "value": "per-event payload age",
        "purpose": "Same as extract_board_row. Clock skew/health of one PUSH, not persist-until-next.",
        "class": "LIVE_DATA_HEALTH",
    },
    {
        "file": "src/research/execution_grade_confirmation/board.py",
        "function": "QUOTE_FRESHNESS_MS / price_age_ms",
        "value": "reject if price_age > QUOTE_FRESHNESS_MS",
        "purpose": "Execution-grade confirmation, not historical label.",
        "class": "EXECUTION_SAFETY",
    },
    {
        "file": "src/research/pbv2_zero_base_revalidation/large_rise.py",
        "function": "board_stale / price_stale > 5s",
        "value": "5s",
        "purpose": "PBv2 research sample quality. Not V1R TARGET M4.",
        "class": "LIVE_DATA_HEALTH",
    },
    {
        "file": "src/research/e1_x28_executable_joint/__init__.py",
        "function": "EXEC_WINDOW_SEC",
        "value": "5.0",
        "purpose": "Fill/exit evaluation window. Execution contract.",
        "class": "EXECUTION_SAFETY",
    },
]
