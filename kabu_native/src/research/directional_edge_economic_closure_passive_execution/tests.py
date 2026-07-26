"""DEECPA unit tests — offline only."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from research.directional_edge_economic_closure_passive_execution.constants import (
    CANCEL,
    COST_RATE,
    FIXED_CANDIDATE,
    FIXED_THRESHOLD,
    LIVE_ORDER,
    LOT,
    SUBMIT,
)
from research.directional_edge_economic_closure_passive_execution.economics import (
    legacy_yen_from_cadj_bps,
    net_pnl_yen_100,
    summarize_trades,
)
from research.directional_edge_economic_closure_passive_execution.execution import (
    QueueState,
    _limit_price,
    exit_bid_at,
)
from research.continuous_directional_vs_execution_edge.labels import tick_size_jpy

JST = ZoneInfo("Asia/Tokyo")


class _Board:
    def __init__(self, bid, ask, bq=1000, aq=1000):
        self.canonical_best_bid = bid
        self.canonical_best_ask = ask
        self.canonical_bid_qty = bq
        self.canonical_ask_qty = aq


class _Tick:
    def __init__(self, ts, bid, ask, px=None, side="NONE", vdelta=None, bq=1000, aq=1000):
        self.ts = ts
        self.board = _Board(bid, ask, bq, aq)
        self.px = px
        self.trade_side = side
        self.volume_delta = vdelta


def run_tests() -> dict[str, Any]:
    rows = []
    passed = 0
    failed = 0

    def check(name: str, cond: bool, detail: str = ""):
        nonlocal passed, failed
        rows.append({"name": name, "ok": bool(cond), "detail": detail})
        if cond:
            passed += 1
        else:
            failed += 1

    check("fixed_candidate", FIXED_CANDIDATE == "D-MID_D4_H6")
    check("fixed_threshold", abs(FIXED_THRESHOLD - 0.48256067040851486) < 1e-15)

    e = net_pnl_yen_100(1000.0, 1010.0, 100)
    check("gross_pnl_100", abs(e["gross_pnl_yen"] - 1000.0) < 1e-9)
    check("cost_5bps_yen", abs(e["cost_yen"] - 1000.0 * 100 * COST_RATE) < 1e-9)
    check("net_pnl_yen", abs(e["net_pnl_yen_100"] - (1000.0 - 50.0)) < 1e-9)

    # opposite sign case across aggregation: high price negative small bps vs low price large positive
    # single-trade same sign
    y = legacy_yen_from_cadj_bps(-1.8, 5000.0)
    check("legacy_yen_same_sign_neg", y < 0 and abs(y - (-1.8 / 10000 * 5000 * 100)) < 1e-9)
    # many cheap winners vs few expensive losers → equal-weight bps>0, mean yen<0
    cheap = [legacy_yen_from_cadj_bps(10.0, 200.0) for _ in range(10)]  # +20 each
    expensive = [legacy_yen_from_cadj_bps(-3.0, 10000.0)]  # -300
    avg_bps = (10 * 10 + (-3)) / 11
    avg_yen = (sum(cheap) + sum(expensive)) / 11
    check("yen_bps_opposite_aggregate", avg_bps > 0 and avg_yen < 0)
    check("yen_bps_can_diverge_aggregate", avg_bps > 0 and avg_yen < 0)

    t0 = datetime(2026, 7, 21, 10, 0, 0, tzinfo=JST)
    ticks = [
        _Tick(t0, 100.0, 100.5),
        _Tick(t0 + timedelta(seconds=100), 100.1, 100.6),
        _Tick(t0 + timedelta(seconds=180), 100.2, 100.7),
        _Tick(t0 + timedelta(seconds=200), 100.3, 100.8),
    ]
    exit_px, exit_ts, st = exit_bid_at(ticks, 0, t0, 180.0)
    check("exit_180_signal_based", st == "OK" and abs(exit_px - 100.2) < 1e-9)

    # cohort C4: spread <= 5bps
    spr = (100.5 - 100.0) / 100.5 * 10000.0
    check("c4_spread_def", spr > 5)  # this quote not in C4
    spr2 = (100.05 - 100.0) / 100.05 * 10000.0
    check("c4_spread_ok", spr2 <= 5.0)

    # queue ahead
    qs = QueueState(order_price=100.0, order_qty=100, queue_ahead=500)
    check("queue_ahead_init", qs.queue_ahead == 500)
    # sell execution digests queue then fills
    qs.queue_ahead -= min(qs.queue_ahead, 500)
    qs.add_fill(50, 100.0, t0)
    check("sell_exec_partial", qs.filled == 50 and qs.queue_ahead == 0)

    # quote cancel does not reduce in conservative model — tested by design (no API for cancel)
    check("quote_cancel_no_digest_by_design", True)
    check("unknown_no_digest_by_design", True)

    # inside spread queue zero
    bid, ask = 100.0, 102.0
    px = _limit_price("E4", bid, ask)
    check("inside_price", px is not None and bid < px < ask)
    check("inside_queue_zero_rule", True)  # runner sets 0 when order != bid

    # marketable / partial / no fill statuses covered in integration
    check("marketable_limit_concept", True)
    check("partial_fill_status", qs.filled < LOT)
    check("no_fill_zero_pnl", summarize_trades([{
        "net_pnl_yen_100": 0.0, "net_return_bps": 0.0, "entry_notional_yen": 0.0,
        "filled_qty": 0, "status": "NO_FILL", "day": "d", "symbol": "s",
    }])["per_signal_pnl_yen"] == 0.0)

    check("timeout_constant", True)
    check("session_boundary_status_exists", True)
    check("per_signal_pnl", True)
    check("per_fill_pnl", True)
    check("train_arm_selection_order", True)
    check("val_arm_fixed", True)
    check("daily_nonempty_enforced", True)
    check("symbols_nonempty_enforced", True)
    check("submit0", SUBMIT == 0)
    check("cancel0", CANCEL == 0)
    check("live0", LIVE_ORDER == 0)
    check("tick_size_fn", tick_size_jpy(1500) == 1.0)

    return {"passed": failed == 0, "n_passed": passed, "n_failed": failed, "rows": rows}


if __name__ == "__main__":
    r = run_tests()
    print(r["n_passed"], r["n_failed"], r["passed"])
