"""Canonical funnel from actual pipeline SoT — no invented stages."""
from __future__ import annotations

from typing import Any


def freeze_canonical_funnel() -> list[dict[str, Any]]:
    """
    Ordered stages discovered from runtime/research code + artifacts.
    future_info stages are documented but excluded from ENTRY-pop attribution.
    """
    return [
        {
            "stage_id": "STAGE_0_RUNTIME_UNIVERSE_CSV",
            "source_file": "src/universe/core10_dynamic40_price_risk.py",
            "source_functions": [
                "build_am_universe_price_risk",
                "build_pm_universe_price_risk",
            ],
            "artifact": "results/daily/{day}/runtime/universe_core10_dynamic40_price_risk_{am|am_refresh1000|pm|pm_refresh1430}_{day}.csv",
            "membership_semantics": "Core10+Dynamic40 with Dynamic40 price/tick risk filter; ~50 slots per session CSV",
            "timestamp_semantics": "session membership (not episode timestamps)",
            "selection_occurs_when": "pre-session / intraday refresh (runtime)",
            "uses_future_information": False,
            "entry_pop_comparable": True,
            "role": "UNIVERSE_SELECTION",
        },
        {
            "stage_id": "STAGE_1_PUSH_JSONL_CAPTURE",
            "source_file": "src/storage/push_recorder.py",
            "source_functions": ["PushRecorder.append"],
            "artifact": "data/push_jsonl/{YYYY-MM-DD}/{symbol}.T.jsonl",
            "membership_semantics": "Any symbol with >=1 PUSH line that day",
            "timestamp_semantics": "recorded_at on PUSH lines",
            "selection_occurs_when": "live session / ingress",
            "uses_future_information": False,
            "entry_pop_comparable": True,
            "role": "CAPTURE_SELECTION",
            "market_label": "CAPTURED_MARKET_PROXY",
            "note": "May be superset of frozen 50-slot universe CSV; exact extras provenance UNRESOLVED_DUE_TO_SOURCE_COVERAGE",
        },
        {
            "stage_id": "STAGE_2_RESEARCH_LIST_CAPTURED",
            "source_file": "src/research/e1_x14_board_independent_signal/ticks.py",
            "source_functions": ["list_day_symbols"],
            "membership_semantics": "All push_jsonl symbols for day (no CSV re-screen)",
            "timestamp_semantics": "N/A (symbol list)",
            "selection_occurs_when": "research rebuild",
            "uses_future_information": False,
            "entry_pop_comparable": True,
            "role": "CAPTURED_MARKET_PROXY",
            "alias_of": "STAGE_1_PUSH_JSONL_CAPTURE",
        },
        {
            "stage_id": "STAGE_3_10S_GRID",
            "source_file": "src/research/e1_x14_board_independent_signal/grid.py",
            "source_functions": ["build_symbol_day_grid"],
            "membership_semantics": "AM/PM 10s grid with as-of fill; quality_status OK/not",
            "timestamp_semantics": "grid_epoch / grid_time",
            "selection_occurs_when": "research",
            "uses_future_information": False,
            "entry_pop_comparable": False,
            "role": "GRID_CONSTRUCTION",
            "note": "Full grid rebuild excluded from X32 performance matrix for cost; coverage counted separately when available",
        },
        {
            "stage_id": "STAGE_4_FEATURE_ELIGIBILITY",
            "source_file": "src/research/e1_x14_board_independent_signal/features.py",
            "source_functions": ["attach_path_volume_features"],
            "membership_semantics": "quality_status==OK and price present → feature_status OK else FEATURE_NOT_EVALUABLE",
            "timestamp_semantics": "same grid_epoch",
            "selection_occurs_when": "research",
            "uses_future_information": False,
            "entry_pop_comparable": False,
            "role": "FEATURE_ELIGIBILITY",
            "note": "Real filter; episode-level performance requires grid rebuild → coverage/diagnostic only in X32",
        },
        {
            "stage_id": "STAGE_5_FORWARD_LABEL_GATE",
            "source_file": "src/research/e1_x14_board_independent_signal/features.py",
            "source_functions": ["attach_forward_labels", "cluster_anchors"],
            "membership_semantics": "forward_return_60s or forward_return_180s present required for cluster membership",
            "timestamp_semantics": "grid_epoch",
            "selection_occurs_when": "research cluster build",
            "uses_future_information": True,
            "entry_pop_comparable": False,
            "role": "EXCLUDED_FUTURE_MEMBERSHIP",
            "note": "Excluded from ENTRY population attribution per X32 rule",
        },
        {
            "stage_id": "STAGE_6_CLUSTER_FIRST_ANCHORS",
            "source_file": "src/research/e1_x14_board_independent_signal/features.py",
            "source_functions": ["cluster_anchors"],
            "also": "src/research/e1_x19_outcome_pre_path/population.py:_build_day",
            "membership_semantics": "feature_status==OK + forward return present; CLUSTER_WINDOW_SEC=300; keep CLUSTER_FIRST_ANCHOR",
            "timestamp_semantics": "first cluster member grid_epoch",
            "selection_occurs_when": "research cache build",
            "uses_future_information": True,
            "entry_pop_comparable": True,
            "role": "ANCHOR_CANDIDATE_EPISODES",
            "note": "Membership uses forward labels; outcome metrics still evaluated with X28 ask→bid contract (not mid/CP)",
        },
        {
            "stage_id": "STAGE_7_X30_X31_POPULATION",
            "source_file": "src/research/e1_x28e_absolute_rise_exit_arch/population.py",
            "source_functions": ["load_combined_population"],
            "also": "src/research/e1_x30_absolute_rise_entry_v2/population.py:load_population",
            "membership_semantics": "X19 base 17688 + X23 0804 + X28D 0805-07 = 22491 historical episodes",
            "timestamp_semantics": "date|symbol|session|grid_epoch",
            "selection_occurs_when": "research load",
            "uses_future_information": False,
            "entry_pop_comparable": True,
            "role": "CANDIDATE_SIGNAL_POPULATION",
            "alias_of_performance": "STAGE_6_CLUSTER_FIRST_ANCHORS",
        },
    ]


def attribution_funnel_ids() -> list[str]:
    """Stages used in parent→child marginal attribution (no future-only stages as parents of clocks)."""
    return [
        "CAPTURED_MARKET_PROXY",           # STAGE_1/2 symbols @ common clock
        "RUNTIME_UNIVERSE_SELECTED",       # STAGE_0 ∩ captured @ common clock
        "CANDIDATE_SYMBOL_POOL",           # symbols in X30 pop @ common clock
        "CANDIDATE_CLUSTER_ANCHORS",       # X30 episode timestamps (ask→bid labels)
    ]


def transitions() -> list[tuple[str, str]]:
    return [
        ("CAPTURED_MARKET_PROXY", "RUNTIME_UNIVERSE_SELECTED"),
        ("RUNTIME_UNIVERSE_SELECTED", "CANDIDATE_SYMBOL_POOL"),
        ("CANDIDATE_SYMBOL_POOL", "CANDIDATE_CLUSTER_ANCHORS"),
    ]
