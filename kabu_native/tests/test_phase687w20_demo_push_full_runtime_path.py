"""Phase687W20 — demo PUSH full runtime path tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from small_paper.demo_push_runtime_path import (
    ENV_FLAG,
    DEMO_SYMBOLS,
    build_push_payload,
    demo_push_e2e_enabled,
    generate_scenario_records,
    require_demo_mode,
    write_push_fixtures,
)
from datetime import datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def test_demo_disabled_refuses_fixtures(monkeypatch):
    monkeypatch.delenv(ENV_FLAG, raising=False)
    assert demo_push_e2e_enabled() is False
    with pytest.raises(RuntimeError, match="DEMO_FIXTURE_REFUSED"):
        require_demo_mode()


def test_demo_enabled_via_env(monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    assert demo_push_e2e_enabled() is True
    require_demo_mode()


def test_push_payload_schema_fields():
    p = build_push_payload(
        symbol="7203",
        price=2800.0,
        ts=datetime(2026, 7, 14, 9, 10, tzinfo=JST),
        sequence=1,
    )
    for key in (
        "Symbol",
        "CurrentPrice",
        "CurrentPriceTime",
        "BidPrice",
        "AskPrice",
        "Buy1",
        "Sell1",
        "Volume",
        "TradingVolume",
        "VWAP",
        "HighPrice",
        "LowPrice",
        "MarketOrderBuyQty",
        "MarketOrderSellQty",
        "timestamp",
        "sequence",
    ):
        assert key in p
    assert p["demo"] is True


def test_multi_tick_scenarios_ordered(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(ENV_FLAG, "1")
    recs = generate_scenario_records()
    assert len(recs) > 10
    seqs = [int(r["sequence"]) for r in recs]
    assert seqs == sorted(seqs)
    sids = {r["scenario_id"] for r in recs}
    assert "A_reject_weak" in sids
    assert "B_pbv2_accept_equiv" in sids
    assert "C_stale" in sids
    assert "D_flat_band" in sids
    assert "E_rising_dispatch" in sids
    n = write_push_fixtures(tmp_path, recs)
    assert n == len(recs)
    assert any(tmp_path.glob("*.jsonl"))


def test_production_runner_flag_wiring():
    from small_paper import paper_trade_checked_runner as m
    import inspect

    src = inspect.getsource(m.main)
    assert "--demo-push-e2e" in src
    src2 = inspect.getsource(m.PaperTradeCheckedRunner.step_start_demo_push_e2e)
    assert "demo_push_runtime_path" in src2
