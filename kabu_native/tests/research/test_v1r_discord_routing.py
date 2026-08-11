"""V1R Discord routing tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from notify.discord_notification_model import (
    WEBHOOK_ENV_CAP,
    WEBHOOK_ENV_RESEARCH,
    WEBHOOK_ENV_TRADE,
)
from notify.v1r_discord_routing import (
    WEBHOOK_ENV_V1R_ENTRY,
    V1RNotifyKind,
    assert_negative_routing,
    format_entry,
    format_exit,
    format_fill,
    public_routing_table,
    ROUTING_TABLE,
)

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "v1r_discord_routing_finalization"


def test_negative_routing_static():
    r = assert_negative_routing()
    assert r["pass"] is True


def test_routing_table_expected_envs():
    assert ROUTING_TABLE[V1RNotifyKind.ENTRY]["env_keys"] == (WEBHOOK_ENV_V1R_ENTRY,)
    assert ROUTING_TABLE[V1RNotifyKind.EXPIRED]["env_keys"] == (WEBHOOK_ENV_V1R_ENTRY,)
    assert ROUTING_TABLE[V1RNotifyKind.FILL]["env_keys"] == (WEBHOOK_ENV_TRADE,)
    assert ROUTING_TABLE[V1RNotifyKind.EXIT]["env_keys"] == (WEBHOOK_ENV_TRADE,)
    assert ROUTING_TABLE[V1RNotifyKind.PRIMARY_SUMMARY]["env_keys"] == (WEBHOOK_ENV_RESEARCH,)
    assert ROUTING_TABLE[V1RNotifyKind.PBV2_SHADOW]["env_keys"] == (WEBHOOK_ENV_RESEARCH,)
    assert ROUTING_TABLE[V1RNotifyKind.ONE_M_SHADOW]["env_keys"] == (WEBHOOK_ENV_RESEARCH,)
    assert ROUTING_TABLE[V1RNotifyKind.CAP_BLOCKED]["env_keys"] == (WEBHOOK_ENV_CAP,)


def test_message_info_density_no_pbv2_reasons():
    title, desc = format_entry({
        "symbol": "6674.T", "symbol_name": "ジーエス・ユアサ コーポレーション",
        "anchor": "15:05:00", "rank": 3, "score": 0.913,
        "limit": 5234, "qty": 100, "pending": 1, "open": 2, "cap": 5,
        "entry_count_today": 1,
    })
    assert "🟢 ENTRY" in title
    assert "ENTRY判断" in desc
    assert "5分高値" not in desc
    assert "損切り価格" not in desc
    t2, d2 = format_fill({
        "symbol": "6674.T", "fill": 5234, "limit": 5234, "rank": 3, "score": 0.913,
        "fill_delay_sec": 0.42, "exit_target": "15:15:00.42", "qty": 100,
        "anchor": "15:05:00", "fill_time": "15:05:00.42", "open": 3, "pending": 0, "cap": 5,
    })
    assert "🔵 FILL" in t2
    assert "EXIT予定" in d2
    t3, d3 = format_exit({
        "symbol": "6674.T", "entry_price": 5234, "exit_price": 5229,
        "entry_time": "15:05:00.42", "exit_time": "15:15:05.21",
        "pnl_yen": -500, "pnl_pct": -0.10, "hold_sec": 604.8,
        "daily_symbol_pnl_yen": -5400, "daily_v1r_pnl_yen": 18700,
        "mfe_pct": 0.08, "mae_pct": -0.11,
        "reason": "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET",
    })
    assert "🔴 EXIT" in t3
    assert "本日V1R累計" in d3
    assert "FIRST_VALID" not in d3
    assert "600秒経過後の最初の有効Buy1" in d3


def test_public_table_no_secrets():
    for row in public_routing_table():
        assert "discord.com" not in json.dumps(row)


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if not p.exists():
        pytest.skip("no interim")
    return json.loads(p.read_text(encoding="utf-8"))


def test_audit_artifacts(interim):
    assert interim.get("negative_routing") is True
    assert interim.get("opened_20260810") is False
    assert interim.get("submit_cancel_live") == "0/0/0"
    assert interim.get("ledger_state_mutation") is False
