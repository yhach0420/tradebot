"""Day2 frozen confirmation tests. No sendorder. No 20260911 mining."""
from __future__ import annotations

from pathlib import Path

import pytest

from research.futures_x_stock_state_day2_confirmation_v1 import (
    ANALYSIS_ID,
    DAY1_TRADING_DATE,
    ENTRY,
    EXIT,
    PRIMARY_BUCKET,
    PRIMARY_FAMILY,
    PRIMARY_HORIZON,
    PRIMARY_SELECTOR,
    SECONDARY_MAY_RESCUE,
    SUBSTITUTIONS_ALLOWED,
    TRADING_DATE,
    VERDICT_FAIL,
    VERDICT_NOT_RUN,
)
from research.futures_x_stock_state_day2_confirmation_v1.analyze import (
    evaluate_primary_gates,
    resolve_primary_gates,
    run_day2_confirmation,
)

PKG = Path(__file__).resolve().parents[2] / "src" / "research" / "futures_x_stock_state_day2_confirmation_v1"


def test_freeze_is_exact():
    assert ANALYSIS_ID == "FUTURES_X_STOCK_STATE_DAY2_CONFIRMATION_V1"
    assert TRADING_DATE == "20260914"
    assert DAY1_TRADING_DATE == "20260911"
    assert PRIMARY_SELECTOR == "OBSERVED_TRADE_N_180S"
    assert PRIMARY_FAMILY == "AGREEMENT_180S"
    assert PRIMARY_BUCKET == "BOTH_DOWN"
    assert PRIMARY_HORIZON == "10m"
    assert SUBSTITUTIONS_ALLOWED is False
    assert SECONDARY_MAY_RESCUE is False
    assert ENTRY is False
    assert EXIT is False


def test_gates_pass_only_when_all_c1_c7():
    row = {
        "context_clock_n": 5,
        "ranking_clock_n": 5,
        "interaction_mid_spread": 46.3,
        "lift_mid": 47.9,
        "interaction_long_spread": 1.2,
        "top_long_mean": 18.6,
        "top_long_median": -11.1,
        "drop_top_clock_spread": 10.0,
        "drop_top_symbol_spread": 8.0,
    }
    got = evaluate_primary_gates(row)
    assert got["evaluated"] is True
    assert got["PASS"] is True
    assert got["FAIL"] is False
    assert got["strong_confirmation"]["TOP_LONG_median_gt_0"] is False
    row["lift_mid"] = 4.9
    got2 = evaluate_primary_gates(row)
    assert got2["PASS"] is False
    assert got2["FAIL"] is True
    assert got2["gates"]["C3_incremental_lift_vs_BASE_A_gt_5bps"] is False


def test_insufficient_clocks_fail_only_after_full_evaluation():
    got = evaluate_primary_gates(
        {
            "context_clock_n": 2,
            "ranking_clock_n": 2,
            "interaction_mid_spread": 50.0,
            "lift_mid": 50.0,
            "interaction_long_spread": 1.0,
            "top_long_mean": 1.0,
            "drop_top_clock_spread": 1.0,
            "drop_top_symbol_spread": 1.0,
        }
    )
    assert got["evaluated"] is True
    assert got["PASS"] is False
    assert got["FAIL"] is True
    assert got["gates"]["C1_BOTH_DOWN_clock_n_ge_3"] is False


def test_not_full_is_not_evaluated_not_strategy_fail():
    uneval = resolve_primary_gates(full=False)
    assert uneval["evaluated"] is False
    assert uneval["gate_status"] == "NOT_EVALUATED"
    assert uneval["PASS"] is None
    assert uneval["FAIL"] is False
    assert uneval["strong_confirmation"] is None
    assert uneval["all_c1_c7"] is None
    assert all(v is None for v in uneval["gates"].values())
    body = run_day2_confirmation(trading_date="20260914")
    d = dict(body.get("decision") or {})
    a = dict(body.get("answers") or {})
    g = dict((body.get("primary_gates") or {}).get("gates") or {})
    assert body.get("status") == "NOT_FULL"
    assert a.get("13_primary_PASS_FAIL") == "NOT_RUN"
    assert d.get("VERDICT") == VERDICT_NOT_RUN
    assert d.get("PASS") is None
    assert d.get("FAIL") is False
    assert (body.get("primary_gates") or {}).get("gate_status") == "NOT_EVALUATED"
    assert all(g.get(k) is None for k in g)
    assert (body.get("primary_gates") or {}).get("strong_confirmation") is None
    assert VERDICT_FAIL not in str(d.get("VERDICT") or "")
    assert a.get("14_secondary_rescue_used") is False


def test_full_boolean_pass_and_fail():
    pass_row = {
        "context_clock_n": 5,
        "ranking_clock_n": 5,
        "interaction_mid_spread": 46.3,
        "lift_mid": 47.9,
        "interaction_long_spread": 1.2,
        "top_long_mean": 18.6,
        "top_long_median": 2.0,
        "drop_top_clock_spread": 10.0,
        "drop_top_symbol_spread": 8.0,
    }
    passed = resolve_primary_gates(full=True, row=pass_row)
    assert passed["evaluated"] is True
    assert passed["PASS"] is True
    assert passed["FAIL"] is False
    assert passed["gates"]["C1_BOTH_DOWN_clock_n_ge_3"] is True
    assert passed["strong_confirmation"]["TOP_LONG_median_gt_0"] is True
    failed = resolve_primary_gates(full=True, row={**pass_row, "lift_mid": 1.0})
    assert failed["evaluated"] is True
    assert failed["PASS"] is False
    assert failed["FAIL"] is True
    assert failed["gates"]["C3_incremental_lift_vs_BASE_A_gt_5bps"] is False
    table_err = resolve_primary_gates(full=True, table_error="RuntimeError:x", row=pass_row)
    assert table_err["PASS"] is None
    assert table_err["FAIL"] is False
    assert table_err["gate_status"] == "NOT_EVALUATED"


def test_gate_keys_match_freeze_manifest_and_do_not_rewrite_it():
    import json

    from research.futures_x_stock_state_day2_confirmation_v1.analyze import GATE_KEYS
    from research.futures_x_stock_state_day2_confirmation_v1.isolation import FREEZE_PARENT

    path = FREEZE_PARENT / "day2_freeze_manifest.json"
    before = path.read_bytes()
    body = json.loads(path.read_text(encoding="utf-8"))
    assert tuple((body.get("gates") or {}).keys()) == GATE_KEYS
    assert body.get("substitutions_allowed") is False
    assert body.get("secondary_may_not_rescue_primary_fail") is True
    run_day2_confirmation(trading_date="20260914")
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="closed"):
        run_day2_confirmation(trading_date="20260911")


def test_no_orders_or_substitutions_in_package():
    for p in PKG.glob("*.py"):
        txt = p.read_text(encoding="utf-8")
        assert "send" + "order(" not in txt
        assert "/" + "sendorder" not in txt
        assert "optuna" not in txt
        assert "substitutions_allowed = True" not in txt
