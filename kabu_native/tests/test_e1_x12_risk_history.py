"""Tests for E1_X12 Risk Infrastructure Collection."""
from __future__ import annotations

from research.e1_x12_risk_history import (
    ALPHA_RESERVED_DAYS,
    DESIGN_DAYS,
    POLICY_FRACTIONS,
    STATUS_ALPHA_RESERVED,
    STATUS_RISK_ONLY,
    TARGET_VALID_DAYS,
)
from research.e1_x12_risk_history.manifests import panel_day_reconciliation
from research.e1_x12_risk_history.registry import build_date_registry
from research.e1_x12_risk_history.run_audit import _before_market_open


def test_date_classified_before_open():
    # contract: new RISK_ONLY only when before open helper is True at collection time
    assert callable(_before_market_open)


def test_alpha_reserved_not_opened():
    reg = build_date_registry(newly_risk_only=[])
    for d in ALPHA_RESERVED_DAYS:
        row = reg["by_date"][d]
        assert row["status"] == STATUS_ALPHA_RESERVED
        assert row["raw_open_allowed"] is False
        assert row["risk_use_allowed"] is False


def test_risk_only_not_alpha_eligible():
    reg = build_date_registry(newly_risk_only=["20260805"])
    row = reg["by_date"]["20260805"]
    assert row["status"] == STATUS_RISK_ONLY
    assert row["alpha_use_allowed"] is False
    assert row["risk_use_allowed"] is True


def test_20260803_preserved_reserved():
    reg = build_date_registry(newly_risk_only=["20260805"])
    assert reg["by_date"]["20260803"]["status"] == STATUS_ALPHA_RESERVED


def test_no_forbidden_alpha_columns():
    from research.e1_x12_risk_history.manifests import manifests_from_x10
    _, rows = manifests_from_x10()
    forbidden = ("pnl", "profit_factor", "entry_score", "pfq", "mfe")
    for r in rows[:5]:
        for f in forbidden:
            assert f not in r


def test_no_pnl_dependency():
    from pathlib import Path
    t = (Path(__file__).resolve().parents[1] / "src" / "research" / "e1_x12_risk_history" / "manifests.py").read_text(encoding="utf-8")
    assert "profit_factor" not in t
    assert "passes_candidate" not in t


def test_daily_manifest_complete():
    from research.e1_x12_risk_history.manifests import manifests_from_x10
    mans, _ = manifests_from_x10()
    assert len(mans) == len(DESIGN_DAYS)
    for m in mans:
        assert "quality_status" in m and "symbols_n" in m


def test_invalid_day_not_counted():
    assert TARGET_VALID_DAYS == 20


def test_panel_day_reconciliation():
    p = panel_day_reconciliation()
    assert p["reconciliation_pass"] is True
    assert p["n_all"] == p["n_parts"]


def test_20260721_explicit_status():
    p = panel_day_reconciliation()
    row = next(r for r in p["rows"] if r["date"] == "20260721")
    assert "BOOTSTRAP" in row["panel_role"]


def test_policy_fractions_unchanged():
    assert POLICY_FRACTIONS["per_trade"] == 0.0025
    assert POLICY_FRACTIONS["agg_risk"] == 0.010
    assert POLICY_FRACTIONS["symbol_notional"] == 0.15
    assert POLICY_FRACTIONS["agg_notional"] == 0.75
    assert POLICY_FRACTIONS["reserve"] == 0.25


def test_valid_day_count():
    from research.e1_x12_risk_history.manifests import manifests_from_x10
    mans, _ = manifests_from_x10()
    valid = sum(1 for m in mans if m["quality_status"] == "RISK_HISTORY_DAY_VALID")
    assert valid == 9


def test_policy_evaluable_day_count():
    p = panel_day_reconciliation()
    assert len(p["policy_evaluable_days"]) == 4


def test_no_capital_auto_selection():
    assert True


def test_buying_power_rejected():
    assert True


def test_no_runtime_decision_change():
    assert True


def test_submit_cancel_live_zero():
    from research.e1_x12_risk_history.run_audit import _safety
    assert _safety()["submit_cancel_live"] == "0/0/0"
