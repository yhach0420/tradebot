"""IDEES-CC unit tests."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from research.idees_fixed_candidate_concentration_oos.analysis import (
    classify_concentration,
    exclude_symbols,
    metrics,
    remove_top1_trade,
    symbol_table,
)
from research.idees_fixed_candidate_concentration_oos.constants import (
    CANCEL,
    FIXED_STRATEGY,
    LIVE_ORDER,
    REPRO_EXPECT,
    SUBMIT,
)
from research.integrated_directional_entry_exit_strategy.exits import TradeResult

JST = ZoneInfo("Asia/Tokyo")


def _tr(i, sym, pnl, bps=None, ask=1000.0):
    t0 = datetime(2026, 7, 21, 10, 0, 0, tzinfo=JST)
    return TradeResult(
        day="20260721", symbol=sym, sample_id=f"s{i}", strategy_id="E1_X5",
        entry_arm="E1", exit_arm="X5",
        entry_time=t0 + timedelta(seconds=i), exit_time=t0 + timedelta(seconds=i + 60),
        entry_ask=ask, exit_bid=ask + pnl / 100.0,
        signal_time=t0 + timedelta(seconds=i), signal_score=0.5,
        entry_spread_bps=3.0, confirm_wait_sec=0.0, hold_sec=60.0,
        exit_reason="TARGET", net_pnl_yen_100=pnl,
        net_return_bps=bps if bps is not None else pnl / (ask * 100) * 10000.0,
        mfe_bps=10, mae_bps=-5, pnl_5s=0, pnl_30s=0, pnl_180s=pnl,
        episode_id=f"ep{i}",
    )


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

    check("fixed_strategy", FIXED_STRATEGY == "E1_X5")
    check("submit0", SUBMIT == 0 and CANCEL == 0 and LIVE_ORDER == 0)
    check("repro_expect_trades", REPRO_EXPECT["trades"] == 69)

    # yen concentration without bps concentration
    trades = (
        [_tr(i, "CHEAP.T", 20, bps=10.0, ask=200.0) for i in range(10)]
        + [_tr(100, "EXP.T", 500, bps=5.0, ask=10000.0)]
    )
    st = symbol_table(trades)
    cls = classify_concentration(st)
    check("symbol_table_n", len(st) == 2)
    check("yen_vs_bps_classifiable", cls["code"] in (
        "YEN_PRICE_WEIGHT_CONCENTRATION", "TRUE_SYMBOL_EDGE_CONCENTRATION", "MODERATE_OR_MIXED"
    ))

    m = metrics(trades)
    check("metrics_trades", m["trades"] == 11)
    ex = exclude_symbols(trades, {"EXP.T"})
    check("exclude_top", len(ex) == 10 and metrics(ex)["total_pnl_yen_100"] == 200.0)
    left, top = remove_top1_trade(trades)
    check("top1_removed", top is not None and len(left) == 10)

    return {"passed": failed == 0, "n_passed": passed, "n_failed": failed, "rows": rows}
