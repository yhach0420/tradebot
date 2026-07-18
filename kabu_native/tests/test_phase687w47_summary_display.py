"""Phase687W47 — Summary display fixes (no ENTRY/EXIT logic changes)."""

from __future__ import annotations

from small_paper.canonical_summary import enrich_summary_with_canonical
from small_paper.discord_message_builder import (
    _fmt_price_yen,
    build_entry_embed_payload,
    build_summary_embed_payload,
    format_runtime_health_lines,
)


def test_fmt_price_yen_rejects_zero_and_negative():
    assert _fmt_price_yen(None) == "N/A"
    assert _fmt_price_yen(0) == "N/A"
    assert _fmt_price_yen(0.0) == "N/A"
    assert _fmt_price_yen(-1) == "N/A"
    assert _fmt_price_yen(1234) == "1234円"


def test_entry_embed_uses_official_price_not_zero():
    embed = build_entry_embed_payload(
        symbol="1000.T",
        entry_price=1500.0,
        slot_usage="1/5",
        entry_score_v2=None,
        data={},
        name_map={},
        entry_time="2026-07-17T10:00:00+09:00",
        stop_price=1482.0,
    )
    desc = embed.get("description") or ""
    assert "1500円" in desc
    assert "ENTRY価格: 0円" not in desc


def test_entry_embed_missing_price_is_na_not_zero():
    embed = build_entry_embed_payload(
        symbol="1000.T",
        entry_price=None,
        slot_usage="0/5",
        entry_score_v2=None,
        data={},
        name_map={},
        entry_time="2026-07-17T10:00:00+09:00",
        stop_price=None,
    )
    desc = embed.get("description") or ""
    assert "ENTRY価格: N/A" in desc
    assert "ENTRY価格: 0円" not in desc


def test_summary_peak_label_and_observer_peak():
    metrics = {
        "trade_count": 1,
        "win_count": 1,
        "loss_count": 0,
        "draw_count": 0,
        "win_rate_yen_100": 1.0,
        "total_pnl_yen_100": 100.0,
        "avg_pnl_yen_100": 100.0,
        "profit_factor_yen_100": 2.0,
        "gross_profit_yen_100": 100.0,
        "gross_loss_yen_100": 0.0,
        "stop_count": 0,
        "stop_rate": 0.0,
        "max_concurrent": 4,
        "max_concurrent_cap": 5,
        "watch_symbols_count": 50,
        "traded_symbols_count": 1,
    }
    embed = build_summary_embed_payload(metrics, am_pm="AM")
    text = "\n".join(f.get("value", "") for f in embed.get("fields") or [])
    assert "ピーク保有 / CAP: 4 / 5" in text
    assert "最大保有: 0 / 5" not in text


def test_runtime_health_prefers_observer_peak_over_gate_zero():
    lines = format_runtime_health_lines(
        {
            "peak_open_slots": 0,
            "observer_open_max_positions": 3,
            "max_concurrent_positions": 5,
            "api_error_count": 0,
            "stale_tick_count": 0,
            "data_gap_count": 0,
        }
    )
    assert any("peak_slots: 3/5" in x for x in lines)


def test_canonical_enrich_uses_observer_peak_in_cap_mode():
    summary = {
        "position_cap_mode": True,
        "observer_open_max_positions": 4,
        "peak_open_slots": 0,
    }
    # empty events → timeline peak 0; observer peak should win
    out = enrich_summary_with_canonical(
        summary,
        [],
        peak_open_slots=0,
        max_concurrent_positions=5,
    )
    canon = out["canonical_summary"]
    assert int(canon["max_concurrent"]) == 4
    assert int(canon["max_concurrent_cap"]) == 5
