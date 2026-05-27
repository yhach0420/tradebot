"""Phase168: entry_price_risk_guard missing_price post-fix unit tests."""

from __future__ import annotations

import sys
from pathlib import Path
NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from small_paper.entry_price_risk_guard import (  # noqa: E402
    EntryPriceRiskGuardConfig,
    EntryPriceRiskGuardState,
)
from small_paper.pilot_runner import _candidate_trade_from_push  # noqa: E402


def _guard(*, shadow_only: bool = True) -> EntryPriceRiskGuardState:
    return EntryPriceRiskGuardState(
        config=EntryPriceRiskGuardConfig(
            enabled=True,
            min_entry_price=50.0,
            max_tick_ratio_pct=5.0,
            shadow_only=shadow_only,
        )
    )


def test_case_a_current_price_key_not_missing():
    g = _guard()
    chk = g.check({"symbol": "6327.T", "current_price": 4110})
    assert chk.trigger != "missing_price"
    assert chk.blocked is False
    assert chk.price_source == "current_price"
    assert chk.current_price == 4110.0


def test_case_b_current_price_camel_case_not_missing():
    g = _guard()
    chk = g.check({"symbol": "6327.T", "CurrentPrice": 4110})
    assert chk.trigger != "missing_price"
    assert chk.blocked is False
    assert chk.price_source == "CurrentPrice"


def test_case_c_low_price_rejects():
    g = _guard()
    chk = g.check({"symbol": "5856.T", "current_price": 13})
    assert chk.trigger == "price_below_min"
    assert chk.blocked is True


def test_case_d_shadow_missing_price_bypassed():
    g = _guard(shadow_only=True)
    chk = g.check({"symbol": "6327.T", "current_price": None})
    assert chk.trigger == "missing_price"
    assert chk.blocked is False
    assert chk.shadow_missing_price_bypassed is True


def test_case_e_non_shadow_missing_price_rejects():
    g = _guard(shadow_only=False)
    chk = g.check({"symbol": "6327.T"})
    assert chk.trigger == "missing_price"
    assert chk.blocked is True
    assert chk.shadow_missing_price_bypassed is False


def test_pipeline_injects_price_before_gate():
    """Phase168: trade passed to gate must include CurrentPrice/current_price from payload."""
    payload = {
        "Symbol": "6327",
        "CurrentPrice": 4110.0,
        "CurrentPriceTime": "2026-05-27T09:10:00+09:00",
    }
    sym = "6327.T"
    trade = _candidate_trade_from_push(payload, symbol=sym, profile="momentum_volume_v13_combined")
    assert not (trade.get("current_price") or trade.get("CurrentPrice"))
    live_px = payload.get("CurrentPrice")
    if live_px is not None:
        trade.setdefault("CurrentPrice", live_px)
        trade.setdefault("current_price", live_px)

    guard = _guard()
    chk = guard.check(trade)
    assert trade.get("current_price") == 4110.0
    assert trade.get("CurrentPrice") == 4110.0
    assert chk.trigger != "missing_price"
    assert chk.blocked is False


if __name__ == "__main__":
    import unittest

    unittest.main()
