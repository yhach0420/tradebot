"""V1R Discord UI information-preservation tests (52B)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from notify.v1r_discord_routing import (
    COLOR_ENTRY,
    COLOR_EXIT,
    COLOR_EXPIRED,
    COLOR_FILL,
    V1RNotifyKind,
    assert_color_lock,
    build_event_embed,
    event_color,
    field_completeness,
    format_entry,
    format_exit,
)

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "v1r_discord_ui_finalization"


def test_color_lock_unit():
    assert assert_color_lock()["pass"] is True


def test_exit_color_ignores_pnl():
    assert event_color(V1RNotifyKind.EXIT, pnl_yen=5500) == COLOR_EXIT
    assert event_color(V1RNotifyKind.EXIT, pnl_yen=-3000) == COLOR_EXIT


def test_event_colors_fixed():
    assert event_color(V1RNotifyKind.ENTRY) == COLOR_ENTRY
    assert event_color(V1RNotifyKind.FILL) == COLOR_FILL
    assert event_color(V1RNotifyKind.EXPIRED) == COLOR_EXPIRED


def test_entry_rich_fields():
    payload = {
        "symbol": "6674.T",
        "symbol_name": "ジーエス・ユアサ コーポレーション",
        "anchor": "15:05:00",
        "rank": 3,
        "candidates": 50,
        "score": 0.913,
        "limit": 5234,
        "qty": 100,
        "open": 2,
        "pending": 1,
        "cap": 5,
        "entry_count_today": 3,
        "previous_trade": {
            "exit_time": "10:42:25",
            "exit_price": 5175,
            "exit_pnl_yen": -2500,
            "elapsed": "4時間22分35秒",
            "exit_reason": "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET",
        },
    }
    title, desc = format_entry(payload)
    assert title.startswith("🟢 ENTRY")
    assert "ジーエス・ユアサ" in title
    assert "ENTRY判断" in desc
    assert "前回EXIT" in desc
    # PBv2-only reasons must not appear
    assert "5分高値" not in desc
    assert "Momentum" not in desc
    _, embeds, color = build_event_embed(V1RNotifyKind.ENTRY, payload, test_only=True)
    assert color == COLOR_ENTRY
    assert all(field_completeness(V1RNotifyKind.ENTRY, embeds[0]).values())


def test_exit_human_reason_and_mfe():
    payload = {
        "symbol": "6674.T",
        "symbol_name": "ジーエス・ユアサ コーポレーション",
        "entry_time": "15:05:00.42",
        "exit_time": "15:15:05.21",
        "entry_price": 5234,
        "exit_price": 5229,
        "qty": 100,
        "pnl_yen": -500,
        "pnl_pct": -0.10,
        "daily_symbol_pnl_yen": -5400,
        "daily_v1r_pnl_yen": 18700,
        "hold_sec": 604.8,
        "reason": "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET",
        "mfe_pct": 0.08,
        "mae_pct": -0.11,
        "buy1": 5229,
        "freshness_sec": 0.3,
    }
    title, desc = format_exit(payload)
    assert title.startswith("🔴 EXIT")
    assert "FIRST_VALID" not in desc
    assert "600秒経過後の最初の有効Buy1" in desc
    assert "MFE" in desc and "MAE" in desc
    assert "本日同銘柄累計" in desc
    assert "本日V1R累計" in desc
    _, embeds, color = build_event_embed(V1RNotifyKind.EXIT, payload, test_only=True)
    assert color == COLOR_EXIT
    assert all(field_completeness(V1RNotifyKind.EXIT, embeds[0]).values())
    assert "PAPER ONLY" in embeds[0]["footer"]["text"]


def test_fill_and_expired_fields():
    fill_p = {
        "symbol": "6674.T", "symbol_name": "ジーエス・ユアサ コーポレーション",
        "anchor": "15:05:00", "fill_time": "15:05:00.42", "limit": 5234, "fill": 5234,
        "qty": 100, "rank": 3, "score": 0.913, "fill_delay_sec": 0.42,
        "open": 3, "pending": 0, "cap": 5, "exit_target": "15:15:00.42",
        "buy1": 5234, "sell1": 5234, "freshness_sec": 0.4,
    }
    exp_p = {
        "symbol": "6674.T", "symbol_name": "ジーエス・ユアサ コーポレーション",
        "anchor": "15:05:00", "expire_time": "15:05:01", "limit": 5234, "qty": 100,
        "rank": 3, "score": 0.913, "buy1": 5230, "sell1": 5235, "freshness_sec": 0.5,
    }
    _, fe, _ = build_event_embed(V1RNotifyKind.FILL, fill_p, test_only=True)
    _, ee, _ = build_event_embed(V1RNotifyKind.EXPIRED, exp_p, test_only=True)
    assert all(field_completeness(V1RNotifyKind.FILL, fe[0]).values())
    assert all(field_completeness(V1RNotifyKind.EXPIRED, ee[0]).values())


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if not p.exists():
        pytest.skip("no interim")
    return json.loads(p.read_text(encoding="utf-8"))


def test_interim_safety(interim):
    assert interim.get("opened_20260810") is False
    assert interim.get("submit_cancel_live") == "0/0/0"
    assert interim.get("ledger_state_mutation") is False
    assert interim.get("one_event_one_message") == "PASS"
    assert interim.get("ENTRY_screenshot_fields") == "PASS"
    assert interim.get("EXIT_screenshot_fields") == "PASS"
