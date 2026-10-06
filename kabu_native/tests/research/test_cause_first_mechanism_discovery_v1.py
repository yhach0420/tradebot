"""Cause-first discovery tests. Split frozen before outcomes. Frozen Validation not used. Kabu 50 not applied."""
from __future__ import annotations

import inspect

from research.cause_first_mechanism_discovery_v1 import (
    COMPLETE_STRATEGY_FROZEN,
    FROZEN_VALIDATION_ACCESSED,
    KABU_50_APPLIED,
    SAME_BAR_CLOSE_ENTRY,
    UNIVERSE_105_MANDATORY_FINAL,
)
from research.cause_first_mechanism_discovery_v1.analyze import decide
from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, interval_crosses_lunch
from research.cause_first_mechanism_discovery_v1.isolation import FOUNDATION_OUT, FREEZE_OUT, OUT, write_overlap_n
from research.cause_first_mechanism_discovery_v1.publish import SHEET_ORDER
from research.cause_first_mechanism_discovery_v1.split import split_session_days


def test_split_chronological_no_overlap():
    from datetime import date, timedelta

    days = []
    cur = date(2025, 1, 6)
    while len(days) < 100:
        days.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    s = split_session_days(days)
    assert s["ok"] is True
    assert s["random_split"] is False
    disc = s["discovery"]["dates"]
    conf = s["confirmation"]["dates"]
    val = s["frozen_validation"]["dates"]
    assert disc + conf + val == days
    assert not (set(disc) & set(val))
    assert s["frozen_validation_hidden_from_discovery"] is True
    s2 = split_session_days(days)
    assert s["split_sha256"] == s2["split_sha256"]


def test_causal_clock_and_no_same_bar():
    assert hhmm_add("09:30", 1) == "09:31"
    assert interval_crosses_lunch("09:30", "09:45") is False
    assert SAME_BAR_CLOSE_ENTRY is False
    assert FROZEN_VALIDATION_ACCESSED is False
    assert KABU_50_APPLIED is False
    assert UNIVERSE_105_MANDATORY_FINAL is False
    assert COMPLETE_STRATEGY_FROZEN is False


def test_decide_and_isolation():
    bind_fail = decide(bind_ok=False, split_ok=True, surviving_n=3)
    assert bind_fail["VERDICT"].endswith("BIND_FAILED_V1")
    none = decide(bind_ok=True, split_ok=True, surviving_n=0)
    assert "NO_STABLE" in none["VERDICT"]
    ready = decide(bind_ok=True, split_ok=True, surviving_n=2)
    assert ready["NEXT"] == "CAUSE_FIRST_CONTEXT_PLUS_TECHNICAL_ENTRY_V1"
    import research.cause_first_mechanism_discovery_v1.analyze as a

    src = inspect.getsource(a)
    assert "DESIGN_KABU_50_SLOT_RUNTIME_UNIVERSE_V1" not in src
    assert OUT.name == "cause_first_mechanism_discovery_v1"
    assert FOUNDATION_OUT.name == "daytrade_historical_research_foundation_v2"
    assert FREEZE_OUT.name == "fixed_daytrade_universe_v1"
    assert write_overlap_n("", "") == 0
    assert "Data_Split" in SHEET_ORDER
    assert "Surviving_Hypotheses" in SHEET_ORDER
