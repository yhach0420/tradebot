"""Phase613 entry latency trace classification tests."""

from __future__ import annotations

from small_paper.entry_latency_trace import classify_stale


def test_classify_feed_already_stale() -> None:
    assert (
        classify_stale(
            current_price_time="2026-06-29T09:00:00+09:00",
            d_feed_price_age_sec=5.0,
            d_price_age_at_freshness_sec=5.1,
            max_price_age_sec=3.0,
            gate_reason="data_stale_price",
        )
        == "A_feed_already_stale"
    )


def test_classify_system_latency_stale() -> None:
    assert (
        classify_stale(
            current_price_time="2026-06-29T09:00:00+09:00",
            d_feed_price_age_sec=2.0,
            d_price_age_at_freshness_sec=4.0,
            max_price_age_sec=3.0,
            gate_reason="data_stale_price",
        )
        == "B_system_latency_stale"
    )


def test_classify_missing_cpt() -> None:
    assert (
        classify_stale(
            current_price_time=None,
            d_feed_price_age_sec=None,
            d_price_age_at_freshness_sec=5.0,
            max_price_age_sec=3.0,
            gate_reason="data_stale_price",
        )
        == "C_missing_current_price_time"
    )
