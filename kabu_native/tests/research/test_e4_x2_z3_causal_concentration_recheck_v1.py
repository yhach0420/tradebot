"""Causal concentration recheck tests. Method A audit. Refill. LODO fold-local top. Stress sealed."""
from __future__ import annotations

import pytest

from research.e4_x2_z3_causal_concentration_recheck_v1 import (
    CASE_A,
    CASE_D,
    CASE_E,
    CASE_TRUE_DEP,
    DROP_TOP_METHOD_A,
    FOCUS_CANDIDATE,
    MAX_RESEARCH_DATE,
)
from research.e4_x2_z3_causal_concentration_recheck_v1.analyze import (
    audit_drop_top_method,
    decide,
    economic_other_ok,
    exclude_symbol,
    posthoc_ex_top1,
    top_symbol_from_trades,
)
from research.e4_x2_z3_causal_concentration_recheck_v1.harvest import AUDIT, assert_dev_only_day
from research.simple_tech_entry_family.portfolio import portfolio_replay


def test_drop_top_is_posthoc_subtraction():
    audit = audit_drop_top_method()
    assert audit["METHOD_CLASS"] == "A"
    assert audit["DROP_TOP_SYMBOL_METHOD"] == DROP_TOP_METHOD_A
    assert audit["PRIOR_DROP_TOP_CAUSAL"] is False
    assert audit["SOURCE_FUNCTION"] == "pack_trades"
    assert "total - top_sym_pnl" in audit["SOURCE_SNIPPET"].replace(" ", "") or "top_sym_pnl" in audit["SOURCE_SNIPPET"]


def test_causal_refill_not_equal_posthoc():
    t0 = 1_000_000.0
    rows = [
        {
            "date": "20260803",
            "symbol": "TOP",
            "t0": t0,
            "ORDERED": True,
            "WOULD_FILL": True,
            "fill_t": t0 + 0.1,
            "fill_price": 100.0,
            "exit_t": t0 + 200.0,
            "exit_price": 200.0,
            "pnl_yen_100": 1_000_000.0,
            "exit_reason": "Z3",
        },
        {
            "date": "20260803",
            "symbol": "OTH",
            "t0": t0 + 1.0,
            "ORDERED": True,
            "WOULD_FILL": True,
            "fill_t": t0 + 1.1,
            "fill_price": 100.0,
            "exit_t": t0 + 200.0,
            "exit_price": 101.0,
            "pnl_yen_100": 100.0,
            "exit_reason": "Z3",
        },
    ]
    occ = portfolio_replay(rows, wait_sec=5.0, position_cap=1)
    trades = list(occ["trades"])
    assert int(occ["fill_n"]) == 1
    top, _ = top_symbol_from_trades(trades)
    assert top == "TOP"
    post = posthoc_ex_top1(trades, top, days=["20260803"])
    assert abs(float(post["TOTAL_PNL"])) < 1e-9
    causal_rows = exclude_symbol(rows, top)
    occ2 = portfolio_replay([r for r in causal_rows if r.get("ORDERED")], wait_sec=5.0, position_cap=1)
    assert int(occ2["fill_n"]) == 1
    assert str(occ2["trades"][0]["symbol"]) == "OTH"
    refill = float(occ2["trades"][0]["pnl_yen_100"]) - float(post["TOTAL_PNL"])
    assert refill > 0


def test_fold_local_top_not_frozen():
    a = [{"symbol": "AAA", "pnl_yen_100": 50.0, "date": "20260803"}]
    b = [{"symbol": "BBB", "pnl_yen_100": 80.0, "date": "20260804"}]
    t1, _ = top_symbol_from_trades(a)
    t2, _ = top_symbol_from_trades(b)
    assert t1 == "AAA"
    assert t2 == "BBB"
    assert t1 != t2


def test_dev_only_blocks():
    AUDIT["HOLDOUT_BURNED_READ_N"] = 0
    AUDIT["STRESS_READ_N"] = 0
    AUDIT["FUTURE_DATA_N"] = 0
    with pytest.raises(RuntimeError, match="HOLDOUT_BURNED_READ"):
        assert_dev_only_day("20260810")
    AUDIT["HOLDOUT_BURNED_READ_N"] = 0
    with pytest.raises(RuntimeError, match="STRESS_READ"):
        assert_dev_only_day("20260828")
    AUDIT["STRESS_READ_N"] = 0
    AUDIT["STRESS_FILE_OPEN_N"] = 0
    AUDIT["STRESS_METRIC_COMPUTE_N"] = 0
    with pytest.raises(RuntimeError, match="FUTURE"):
        assert_dev_only_day("20260903")
    AUDIT["FUTURE_DATA_N"] = 0
    assert MAX_RESEARCH_DATE == "20260807"


def test_other_gates_ignore_drop_top():
    p = {
        "TOTAL_PNL": 820060.0,
        "PF": 1.89,
        "positive_day_n": 6,
        "negative_day_n": 4,
        "EX_BEST_DAY_PNL": 426650.0,
        "MAXDD": -162200.0,
        "DROP_TOP_SYMBOL_PNL": -387940.0,
    }
    assert economic_other_ok(p) is True


def test_decide_paths():
    d = decide(integrity=False, parity_ok_flag=True, method_class="A", e4_causal_pnl=1.0, pass_n=1, winner_id="x", lodo_pack={"stable": True})
    assert d["CASE"] == "E"
    assert d["VERDICT"] == CASE_E
    d = decide(integrity=True, parity_ok_flag=False, method_class="A", e4_causal_pnl=1.0, pass_n=1, winner_id="x", lodo_pack={"stable": True})
    assert d["CASE"] == "E"
    d = decide(integrity=True, parity_ok_flag=True, method_class="B", e4_causal_pnl=-1.0, pass_n=0, winner_id=None, lodo_pack=None)
    assert d["VERDICT"] == CASE_TRUE_DEP
    assert d["FULL_STRATEGY_DEV_FROZEN"] is False
    d = decide(integrity=True, parity_ok_flag=True, method_class="A", e4_causal_pnl=-100.0, pass_n=0, winner_id=None, lodo_pack=None)
    assert d["VERDICT"] == CASE_TRUE_DEP
    assert d["ARCHITECTURE_REDESIGN_ALLOWED"] is True
    d = decide(integrity=True, parity_ok_flag=True, method_class="A", e4_causal_pnl=10.0, pass_n=1, winner_id=FOCUS_CANDIDATE, lodo_pack={"stable": False})
    assert d["VERDICT"] == CASE_D
    assert d["FULL_STRATEGY_DEV_FROZEN"] is False
    d = decide(integrity=True, parity_ok_flag=True, method_class="A", e4_causal_pnl=10.0, pass_n=1, winner_id=FOCUS_CANDIDATE, lodo_pack={"stable": True})
    assert d["VERDICT"] == CASE_A
    assert d["FULL_STRATEGY_DEV_FROZEN"] is True
    assert d["STRESS_OPENED"] is False
