"""Causal-path complete-strategy tests. Event freeze before outcomes. Frozen Validation closed. M1-M11 stay rejected."""
from __future__ import annotations

import inspect

import pandas as pd

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add
from research.causal_path_to_complete_strategy_v1 import (
    EXPECTED_SPLIT_SHA256,
    FROZEN_VALIDATION_OPENED,
    FUTURE_AS_DECISION_FEATURE,
    KABU_50_APPLIED,
    M1_M11_RESCUED,
    MAX_CANDIDATES,
    OLD_CONFIRMATION_USED_TO_DESIGN,
    PARENT_VERDICT,
    REJECTED_M1_M11,
    SAME_BAR_CLOSE_ENTRY,
)
from research.causal_path_to_complete_strategy_v1.analyze import decide
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.causal_path_to_complete_strategy_v1.events import freeze_event_ids, grid_clocks, scan_day
from research.causal_path_to_complete_strategy_v1.isolation import FOUNDATION_OUT, FREEZE_OUT, OUT, PREV_DISCOVERY_OUT, write_overlap_n
from research.causal_path_to_complete_strategy_v1.paths import attach_forward_paths
from research.causal_path_to_complete_strategy_v1.publish import SHEET_ORDER


def test_constants_and_isolation():
    assert PARENT_VERDICT == "CAUSE_FIRST_NO_STABLE_MECHANISM_V1"
    assert EXPECTED_SPLIT_SHA256 == "2c9bd8f4ce7c86116833b54e1d41e4cbc4b59c3e504b769140fea435d375b3b4"
    assert len(REJECTED_M1_M11) == 11
    assert M1_M11_RESCUED is False
    assert FROZEN_VALIDATION_OPENED is False
    assert OLD_CONFIRMATION_USED_TO_DESIGN is False
    assert KABU_50_APPLIED is False
    assert SAME_BAR_CLOSE_ENTRY is False
    assert FUTURE_AS_DECISION_FEATURE is False
    assert MAX_CANDIDATES == 3
    assert OUT.name == "causal_path_to_complete_strategy_v1"
    assert FOUNDATION_OUT.name == "daytrade_historical_research_foundation_v2"
    assert FREEZE_OUT.name == "fixed_daytrade_universe_v1"
    assert PREV_DISCOVERY_OUT.name == "cause_first_mechanism_discovery_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Data_Binding"
    assert "Frozen_Validation_Status" in SHEET_ORDER
    assert "Candidate_3" in SHEET_ORDER


def test_blocks_chronological_frozen_before_eval():
    days = [f"202409{d:02d}" for d in range(17, 31)] + [f"202410{d:02d}" for d in range(1, 32)] + [f"202411{d:02d}" for d in range(1, 21)]
    b = freeze_discovery_blocks(days, n_blocks=4)
    assert b["ok"] is True
    assert b["random_split"] is False
    assert b["frozen_before_candidate_evaluation"] is True
    assert b["blocks"][0]["last"] < b["blocks"][1]["first"]
    assert b["blocks"][-2]["last"] < b["blocks"][-1]["first"]


def test_event_identities_freeze_before_outcomes():
    times = [f"09:{m:02d}" for m in range(0, 50)]
    symbols = [f"{7200 + i}" for i in range(25)]
    rows = []
    for t in times:
        i = int(t[3:5])
        for j, sym in enumerate(symbols):
            close = 1000.0 + i + 0.1 * j + (8 if i >= 32 and j == 0 else 0)
            rows.append(
                {
                    "symbol": sym,
                    "date": "20240917",
                    "time_label": t,
                    "open": close - 0.5,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 1000.0 + 10 * i + j,
                    "trading_value": 1_000_000.0,
                }
            )
    g = pd.DataFrame(rows)
    sector_of = {s: ("Transportation Equipment" if k < 12 else "Information & Communication") for k, s in enumerate(symbols)}
    events = scan_day(date="20240917", g=g, sector_of=sector_of, clocks=grid_clocks())
    assert events, "expected at least one causal onset on synthetic tape"
    sha1 = freeze_event_ids(events)
    attach_forward_paths(events)
    sha2 = freeze_event_ids(events)
    assert sha1 == sha2
    assert all(e.get("same_bar_close_entry") is False for e in events)
    for e in events:
        assert e["event_time"] == hhmm_add(e["feature_bar"], 1)
        assert e.get("future_used_as_decision_feature") is False
        assert str(e.get("date")) != "20260422"


def test_decide_and_forbidden_programs():
    bind_fail = decide(bind_ok=False, structure=True, promoted_n=1, secondary_any_pass=True, secondary_ran=True)
    assert bind_fail["VERDICT"].endswith("BIND_FAILED_V1")
    found = decide(bind_ok=True, structure=True, promoted_n=1, secondary_any_pass=True, secondary_ran=True)
    assert found["VERDICT"] == "COMPLETE_CAUSAL_STRATEGY_CANDIDATE_FOUND_V1"
    struct = decide(bind_ok=True, structure=True, promoted_n=0, secondary_any_pass=False, secondary_ran=True)
    assert struct["VERDICT"] == "CAUSAL_STRUCTURE_FOUND_EDGE_INSUFFICIENT_V1"
    none = decide(bind_ok=True, structure=False, promoted_n=0, secondary_any_pass=False, secondary_ran=False)
    assert none["VERDICT"] == "STOCK_PANEL_CAUSAL_INFORMATION_INSUFFICIENT_V1"
    import research.causal_path_to_complete_strategy_v1.analyze as a

    src = inspect.getsource(a)
    assert "DESIGN_KABU_50_SLOT_RUNTIME_UNIVERSE_V1" not in src
    assert "PAPER_DEPLOYABILITY_COMPRESSION_V1" not in src
    import research.causal_path_to_complete_strategy_v1.candidates as c

    csrc = inspect.getsource(c)
    assert "day_stability" not in csrc
    assert "BREADTH_STRONG" not in csrc
