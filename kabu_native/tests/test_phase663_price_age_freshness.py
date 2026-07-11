"""Phase663 — price age freshness analysis tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from research.phase663_price_age_freshness_analysis import (
    PHASE663_VERDICT,
    decide_guard_action,
    price_age_bucket,
    run_audit,
)


def test_price_age_bucket_thresholds():
    assert price_age_bucket(1.5) == "lt_300"
    assert price_age_bucket(299.9) == "lt_300"
    assert price_age_bucket(300) == "300_599"
    assert price_age_bucket(899.9) == "600_899"
    assert price_age_bucket(900) == "gte_900"
    assert price_age_bucket(None) == "missing"


def test_decide_reject_when_no_stale_entries():
    decision, _ = decide_guard_action(
        [{"price_age_bucket": "lt_300", "pnl_yen_100": 100}],
        comparison={"stale_ge_300": {"entry_count": 0}, "fresh_lt_300": {"entry_count": 1}},
        concentration={"stale_entry_count": 0},
    )
    assert decision == "REJECT"


def test_phase663_audit_on_canonical_dataset():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper").is_dir():
        pytest.skip("small_paper results missing")
    report = run_audit()
    assert report["verdict"] == PHASE663_VERDICT
    assert report["entry_count"] > 3000
    assert report["trading_day_count"] == 22
    assert report["decision"] in ("ADOPT", "HOLD", "REJECT")
