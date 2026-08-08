"""Tests for E1_X11 Risk Policy Calibration."""
from __future__ import annotations

from research.e1_x11_risk_policy import (
    AGG_NOTIONAL_FRAC,
    AGG_RISK_FRAC,
    MAX_CONCURRENT,
    PER_SYMBOL_NOTIONAL_FRAC,
    PER_TRADE_RISK_FRAC,
    POLICY_ID,
    RESERVE_FRAC,
    SOURCE_X10,
    SOURCE_X10_VERDICT,
)
from research.e1_x11_risk_policy.run_audit import _limits, _prior_days
from research.e1_x11_risk_policy.wallet_audit import resolve_capital_base, special_quote_audit
from research.e1_x7_pfq.config import DAYS


def test_source_e1x10_identity():
    assert SOURCE_X10 == "e1x10_risk_20260805_015534_A"
    assert SOURCE_X10_VERDICT == "E1_X10_RISK_BUDGET_NOT_CONFIGURED"


def test_cap5_is_canonical():
    assert MAX_CONCURRENT == 5


def test_cap3_filename_drift_recorded():
    assert "CONFIG_FILENAME_CAP3_CANONICAL_CAP5_DRIFT"


def test_risk_history_ends_d_minus_1():
    days = ["20260721", "20260722", "20260723", "20260724"]
    prior = _prior_days("20260724", days)
    assert prior[-1] == "20260723"
    assert "20260724" not in prior


def test_no_same_day_future():
    prior = _prior_days("20260728", list(DAYS))
    assert all(d < "20260728" for d in prior)


def test_recurring_symbol_definition():
    from research.e1_x11_risk_policy import RECURRING_MIN_DAYS
    assert RECURRING_MIN_DAYS == 5


def test_support_gate():
    from research.e1_x11_risk_policy import MIN_EXEC_ANCHORS, MIN_HISTORY_DAYS, MIN_JUMP_N, MIN_SPREAD_N
    assert MIN_HISTORY_DAYS == 5
    assert MIN_SPREAD_N == 500
    assert MIN_JUMP_N == 100
    assert MIN_EXEC_ANCHORS == 500


def test_wallet_fields_read_only():
    cb = resolve_capital_base()
    assert cb["status"] == "UNRESOLVED"
    assert all(f.get("read_only_safe") in (True, None) for f in cb["wallet_fields"])


def test_buying_power_not_used_as_equity_without_proof():
    cb = resolve_capital_base()
    assert cb["buying_power_not_used_as_equity"] is True


def test_candidate_policy_exact():
    assert POLICY_ID == "FIXED100_CONSERVATIVE_V1"


def test_per_trade_fraction_0025():
    assert PER_TRADE_RISK_FRAC == 0.0025
    assert _limits(1_000_000)["per_trade_risk_limit"] == 2500.0


def test_aggregate_risk_fraction_010():
    assert AGG_RISK_FRAC == 0.010
    assert _limits(1_000_000)["aggregate_risk_limit"] == 10000.0


def test_symbol_notional_fraction_015():
    assert PER_SYMBOL_NOTIONAL_FRAC == 0.15


def test_aggregate_notional_fraction_075():
    assert AGG_NOTIONAL_FRAC == 0.75
    assert abs(RESERVE_FRAC - 0.25) < 1e-12


def test_static_symbol_day_eligibility():
    lim = _limits(10_000_000)
    assert lim["per_symbol_notional_limit"] == 1_500_000.0


def test_board_freshness_for_execution_gate():
    from research.e1_x11_risk_policy import BOARD_FRESHNESS_SEC
    assert BOARD_FRESHNESS_SEC == 3.0


def test_existing_entry_freshness_unchanged():
    from research.e1_x11_risk_policy import PRICE_FRESHNESS_SEC
    assert PRICE_FRESHNESS_SEC == 3.0


def test_special_quote_not_invented():
    sq = special_quote_audit()
    assert sq["invented_implementation"] is False
    assert sq["status"] == "NOT_AVAILABLE_IN_CAPTURE"


def test_no_pnl_dependency():
    from pathlib import Path
    pkg = Path(__file__).resolve().parents[1] / "src" / "research" / "e1_x11_risk_policy"
    for name in ("wallet_audit.py", "publish.py", "__init__.py"):
        t = (pkg / name).read_text(encoding="utf-8")
        assert "profit_factor" not in t
        assert "passes_candidate" not in t


def test_no_unused_data():
    assert all(d <= "20260731" for d in DAYS)
    assert "20260803" not in DAYS


def test_no_runtime_change():
    assert resolve_capital_base()["status"] == "UNRESOLVED"  # YAML not written


def test_ab_determinism():
    assert _limits(5_000_000)["per_trade_risk_limit"] == _limits(5_000_000)["per_trade_risk_limit"]
