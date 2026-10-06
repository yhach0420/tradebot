"""Opening input capability audit. No PnL. No Holdout/future. No MBO."""
from __future__ import annotations

import inspect

from research.opening_logic_input_capability_audit_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    DEVELOPMENT_DAYS,
    MBO_WORK_THIS_RUN,
    NEXT_AB,
    NEXT_C,
    OUTCOME_INFORMATION_COMPUTED,
    PNL_COMPUTED,
    REQUIRED_PARENT_VERDICT,
    STRATEGY_CREATED,
)
from research.opening_logic_input_capability_audit_v1.analyze import decide
from research.opening_logic_input_capability_audit_v1.publish import SHEET_ORDER
from research.opening_logic_input_capability_audit_v1.scan import (
    INTERVAL_IDS,
    assert_dev_only_day,
    day_epoch,
    interval_bounds,
    interval_of,
)
from research.opening_logic_input_capability_audit_v1.spec import pin_parent
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS


def test_parent_pin():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["VERDICT"] == REQUIRED_PARENT_VERDICT
    assert parent["LOGIC_COMPLETE"] is False
    assert parent["ROBUST_DEV_QUALIFIED"] is False
    assert parent["ARCHITECTURE_CLOSED"] is True
    assert STRATEGY_CREATED is False
    assert PNL_COMPUTED is False
    assert OUTCOME_INFORMATION_COMPUTED is False
    assert MBO_WORK_THIS_RUN is False


def test_date_boundary():
    for d in DEVELOPMENT_DAYS:
        assert_dev_only_day(d)
    for d in BURNED_HOLDOUT_DAYS:
        try:
            assert_dev_only_day(d)
            raise AssertionError("holdout")
        except RuntimeError:
            pass
    for d in STRESS_DAYS:
        try:
            assert_dev_only_day(d)
            raise AssertionError("stress")
        except RuntimeError:
            pass
    try:
        assert_dev_only_day("20260907")
        raise AssertionError("future")
    except RuntimeError:
        pass


def test_interval_bins():
    day = "20260722"
    bounds = interval_bounds(day)
    names = [b[0] for b in bounds]
    assert names == list(INTERVAL_IDS)
    assert interval_of(day_epoch(day, 8, 45, 0), bounds) == "08:45:00-08:49:59"
    assert interval_of(day_epoch(day, 8, 59, 59), bounds) == "08:59:50-08:59:59"
    assert interval_of(day_epoch(day, 9, 0, 0), bounds) == "09:00_onward"
    assert interval_of(day_epoch(day, 8, 44, 59), bounds) == "before_08:45"


def test_decide_cases():
    c = decide(traj_ok=False, actual_ok=True, market_ok=True)
    assert c["VERDICT"] == CASE_C
    assert c["NEXT"] == NEXT_C
    c2 = decide(traj_ok=True, actual_ok=False, market_ok=True)
    assert c2["VERDICT"] == CASE_C
    b = decide(traj_ok=True, actual_ok=True, market_ok=False)
    assert b["VERDICT"] == CASE_B
    assert b["NEXT"] == NEXT_AB
    a = decide(traj_ok=True, actual_ok=True, market_ok=True)
    assert a["VERDICT"] == CASE_A
    assert a["NEXT"] == NEXT_AB


def test_sheet_order():
    assert SHEET_ORDER[0] == "answers"
    assert SHEET_ORDER[-1] == "safety"
    assert "confirmed_input_set" in SHEET_ORDER
    assert "missing_inputs" in SHEET_ORDER
    assert len(SHEET_ORDER) == 18


def test_no_outcome_imports():
    from research.opening_logic_input_capability_audit_v1 import analyze, scan

    src = inspect.getsource(scan) + inspect.getsource(analyze)
    assert "TOTAL_PNL" not in src
    assert "profit_factor" not in src
    assert "MFE" not in src
    assert "markout" not in src
