"""IDEES unit tests."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from research.integrated_directional_entry_exit_strategy.constants import (
    CANCEL,
    ENTRIES,
    EXITS,
    FIXED_THRESHOLD,
    LIVE_ORDER,
    STRATEGIES,
    SUBMIT,
)
from research.integrated_directional_entry_exit_strategy.exits import _econ
from research.integrated_directional_entry_exit_strategy.portfolio import replay_cap5_ranked, train_passes
from research.integrated_directional_entry_exit_strategy.exits import TradeResult

JST = ZoneInfo("Asia/Tokyo")


def run_tests() -> dict[str, Any]:
    rows = []
    passed = failed = 0

    def check(name: str, cond: bool, detail: str = ""):
        nonlocal passed, failed
        rows.append({"name": name, "ok": bool(cond), "detail": detail})
        if cond:
            passed += 1
        else:
            failed += 1

    check("4_entries", len(ENTRIES) == 4)
    check("5_exits", len(EXITS) == 5)
    check("20_strategies", len(STRATEGIES) == 20)
    check("threshold", abs(FIXED_THRESHOLD - 0.48256067040851486) < 1e-15)
    net, bps = _econ(1000.0, 1010.0)
    check("pnl_100_5bps", abs(net - (1000.0 - 50.0)) < 1e-9)
    check("submit0", SUBMIT == 0)
    check("cancel0", CANCEL == 0)
    check("live0", LIVE_ORDER == 0)

    t0 = datetime(2026, 7, 21, 10, 0, 0, tzinfo=JST)
    def tr(i, sym, pnl, hold=60):
        return TradeResult(
            day="20260721", symbol=sym, sample_id=f"s{i}", strategy_id="E1_X1",
            entry_arm="E1", exit_arm="X1",
            entry_time=t0 + timedelta(seconds=i), exit_time=t0 + timedelta(seconds=i + hold),
            entry_ask=1000, exit_bid=1000 + pnl / 100, signal_time=t0 + timedelta(seconds=i),
            signal_score=0.9 - i * 0.01, entry_spread_bps=3.0, confirm_wait_sec=0.0,
            hold_sec=float(hold), exit_reason="FIXED_180", net_pnl_yen_100=pnl,
            net_return_bps=pnl / 10, mfe_bps=10, mae_bps=-5,
            pnl_5s=0, pnl_30s=0, pnl_180s=pnl, episode_id=f"ep{i}",
        )
    # CAP5: 6 different symbols at same time — only 5 accepted
    same_t = [tr(i, f"S{i}.T", 100) for i in range(6)]
    for t in same_t:
        t.entry_time = t0
        t.signal_time = t0
    m = replay_cap5_ranked(same_t)
    check("cap5_blocks_6th", m["trades"] == 5 and m["cap_blocked"] >= 1)
    # same symbol block
    a = tr(0, "AAA.T", 100)
    b = tr(1, "AAA.T", 50)
    b.entry_time = a.entry_time + timedelta(seconds=10)
    b.exit_time = b.entry_time + timedelta(seconds=60)
    m2 = replay_cap5_ranked([a, b])
    check("same_symbol_block", m2["trades"] == 1)
    # ranking by score
    low = tr(0, "L.T", 10)
    high = tr(0, "H.T", 10)
    low.entry_time = high.entry_time = t0
    low.signal_score = 0.5
    high.signal_score = 0.9
    # fill 5 slots with high score first then low should be blocked if we only have room...
    fillers = []
    for i in range(4):
        x = tr(i + 2, f"F{i}.T", 1)
        x.entry_time = t0
        x.signal_score = 0.8
        fillers.append(x)
    m3 = replay_cap5_ranked([low, high] + fillers)
    syms = {t.symbol for t in m3["accepted"]}
    check("score_rank_prefers_high", "H.T" in syms and "L.T" not in syms)

    ok, reasons = train_passes({
        "total_pnl_yen_100": 1000, "profit_factor_yen_100": 1.2, "avg_pnl_yen_100": 10,
        "trades": 60, "daily": {"20260721": 500, "20260722": 500},
        "max_drawdown_yen": -100, "top1_trade_share": 0.1, "top1_symbol_share": 0.1, "top3_symbol_share": 0.2,
    }, ["20260721", "20260722"])
    check("train_pass_gate", ok)

    return {"passed": failed == 0, "n_passed": passed, "n_failed": failed, "rows": rows}


if __name__ == "__main__":
    print(run_tests())
