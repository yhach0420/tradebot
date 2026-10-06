"""Deterministic T01-T34. No Capture. No economics."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x34a_execution_policy.executable_board import SIGN_GENERAL, SIGN_PREOPEN, SIGN_SPECIAL
from research.full_causal_mechanism_discovery_v1.analyze import occupancy_rows, replay
from research.full_causal_mechanism_discovery_v1.exits import resolve_operator_exit
from research.new_entry_breakout_continuation_v1.harvest import BoardTape
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1 import (
    EXIT_ID,
    POSITION_CAP_N,
    STRATEGY_ID,
)
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1.fsm import AopSymbolFsm
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1.library import strategy_spec
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1.pressure import (
    INGRESS_KEYS,
    buy_pressure,
    observed_trade_update,
)
from research.recovery_sequence_full_strategy_architecture_v1.execution import evaluate_execution
from research.simple_tech_entry_family.portfolio import portfolio_replay

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260722"
OPEN_ISO = datetime(2026, 7, 22, 9, 0, 0, tzinfo=JST).isoformat()
FLAT_T = datetime(2026, 7, 22, 11, 29, 0, tzinfo=JST).timestamp()
AM_START = datetime(2026, 7, 22, 9, 0, 0, tzinfo=JST).timestamp()
AM_END = datetime(2026, 7, 22, 11, 30, 0, tzinfo=JST).timestamp()


def ts(h: int, m: int, s: int = 0) -> float:
    return datetime(2026, 7, 22, h, m, s, tzinfo=JST).timestamp()


def iso(h: int, m: int, s: int = 0) -> str:
    return datetime(2026, 7, 22, h, m, s, tzinfo=JST).isoformat()


def qty(*, mo_b: Any = 10, mo_s: Any = 1, und: Any = 10, ov: Any = 1) -> dict[str, Any]:
    return {
        "MarketOrderBuyQty": mo_b,
        "MarketOrderSellQty": mo_s,
        "UnderBuyQty": und,
        "OverSellQty": ov,
    }


def pay(
    *,
    px: float | None = 1000.0,
    vol: float | None = 100.0,
    bid_sign: str = SIGN_GENERAL,
    ask_sign: str = SIGN_GENERAL,
    opening: float | None = 1000.0,
    status: int = 1,
    cpt: str | None = None,
    ask: float | None = 1001.0,
    bid: float | None = 999.0,
    aq: float = 200.0,
    bq: float = 200.0,
    **qtys: Any,
) -> dict[str, Any]:
    q = qty(**{k: qtys[k] for k in ("mo_b", "mo_s", "und", "ov") if k in qtys}) if qtys else qty()
    body: dict[str, Any] = {
        "OpeningPrice": opening,
        "OpeningPriceTime": OPEN_ISO if opening else None,
        "CurrentPrice": px,
        "CurrentPriceTime": cpt,
        "CurrentPriceStatus": status,
        "TradingVolume": vol,
        "BidSign": bid_sign,
        "AskSign": ask_sign,
        "Buy1": {"Price": bid, "Qty": bq, "Sign": bid_sign, "Time": iso(9, 1, 0)},
        "Sell1": {"Price": ask, "Qty": aq, "Sign": ask_sign, "Time": iso(9, 1, 0)},
        **q,
    }
    return body


def rec(h: int, m: int, s: int, payload: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    t = ts(h, m, s)
    payload = dict(payload)
    payload["received_at"] = iso(h, m, s)
    return t, payload


def run(events: list[tuple[float, dict[str, Any]]]) -> AopSymbolFsm:
    fsm = AopSymbolFsm(symbol="1000", date=DAY, flatten_t=FLAT_T, am_start=AM_START)
    for t, p in events:
        fsm.process(ingress_t=t, payload=p)
    return fsm


def _ok(cond: bool, name: str, detail: str = "") -> dict[str, Any]:
    return {"id": name, "PASS": bool(cond), "detail": detail}


def board_at(*, t: float, bid: float = 999.0, ask: float = 1001.0, bq: float = 200.0, aq: float = 200.0, special: bool = False) -> dict[str, Any]:
    tape = BoardTape()
    tape.append(
        {
            "t": float(t),
            "bid": float(bid),
            "ask": float(ask) if ask is not None else float("nan"),
            "bid_qty": float(bq),
            "ask_qty": float(aq) if aq is not None else float("nan"),
            "special": bool(special),
            "ask_fresh_sec": 0.0,
            "bid_fresh_sec": 0.0,
            "fresh_sec": 0.0,
            "fresh_source": "BOARD_EVENT_TIME",
            "executable": not special,
            "px": 1000.0,
            "state": "CONTINUOUS_TRADING",
            "cum_vol": 100.0,
            "continuous": not special,
        }
    )
    return tape.view()


def run_synthetic_tests() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    spec = strategy_spec()

    p_all = buy_pressure(qty(mo_b=10, mo_s=1, und=8, ov=2))
    rows.append(_ok(p_all["BUY_PRESSURE"] is True and p_all["established"] is True, "T01", "all four valid"))

    p_mo = buy_pressure(qty(mo_b=10, mo_s=1, und=1, ov=5))
    rows.append(_ok(p_mo["BUY_PRESSURE"] is False and p_mo["established"] is True, "T02", "MO pair only positive"))

    p_out = buy_pressure(qty(mo_b=1, mo_s=5, und=8, ov=2))
    rows.append(_ok(p_out["BUY_PRESSURE"] is False and p_out["established"] is True, "T03", "outside pair only positive"))

    p_both = buy_pressure(qty(mo_b=10, mo_s=1, und=8, ov=2))
    rows.append(_ok(p_both["BUY_PRESSURE"] is True, "T04", "both positive"))

    p_eq_mo = buy_pressure(qty(mo_b=5, mo_s=5, und=8, ov=2))
    p_eq_ou = buy_pressure(qty(mo_b=10, mo_s=1, und=4, ov=4))
    rows.append(_ok(p_eq_mo["BUY_PRESSURE"] is False and p_eq_ou["BUY_PRESSURE"] is False, "T05", "equality is not positive"))

    p_nan = buy_pressure(qty(mo_b=None, mo_s=1, und=8, ov=2))
    rows.append(_ok(p_nan["established"] is False and p_nan["reason"] == "NAN", "T06", "NaN"))

    p_neg = buy_pressure(qty(mo_b=10, mo_s=1, und=-1, ov=2))
    rows.append(_ok(p_neg["established"] is False and p_neg["reason"] == "NEGATIVE", "T07", "negative invalid"))

    off = pay(px=1000, vol=50, mo_b=1, mo_s=5, und=1, ov=5)
    on = pay(px=1000, vol=60, mo_b=10, mo_s=1, und=8, ov=2)
    t08 = run([rec(9, 1, 0, off), rec(9, 1, 5, on)])
    rows.append(_ok(len(t08.runs) == 1, "T08", "false→true"))

    t09 = run([rec(9, 1, 0, off), rec(9, 1, 5, on), rec(9, 1, 10, pay(px=1001, vol=70, mo_b=11, mo_s=1, und=9, ov=2))])
    rows.append(_ok(len(t09.runs) == 1, "T09", "true→true does not create new run"))

    t10 = run([rec(9, 1, 0, off), rec(9, 1, 5, on), rec(9, 1, 15, off)])
    rows.append(_ok(len(t10.runs) == 1 and t10.pressure_on is False, "T10", "true→false closes run"))

    t11 = run([rec(9, 1, 0, off), rec(9, 1, 5, on), rec(9, 1, 15, off), rec(9, 1, 20, on)])
    rows.append(_ok(len(t11.runs) == 2, "T11", "false→true creates new run"))

    t12 = run([rec(9, 1, 0, on)])
    rows.append(_ok(len(t12.runs) == 1 and t12.runs[0]["no_anchor"] is True and len(t12.signals) == 0, "T12", "no prior trade => no anchor"))

    t13 = run([rec(9, 1, 0, pay(px=1000, vol=50, mo_b=1, mo_s=5, und=1, ov=5)), rec(9, 1, 5, pay(px=1010, vol=50, mo_b=10, mo_s=1, und=8, ov=2))])
    rows.append(_ok(len(t13.signals) == 0, "T13", "carry-forward CurrentPrice not trade"))

    t14 = run([rec(9, 1, 0, pay(px=1000, vol=50)), rec(9, 1, 5, pay(px=1010, vol=50))])
    rows.append(_ok(observed_trade_update(last_vol=50.0, vol=50.0, px=1010.0) is False and len(t14.otus) == 1, "T14", "TradingVolume unchanged => not trade"))

    rows.append(_ok(observed_trade_update(last_vol=50.0, vol=60.0, px=1010.0) is True, "T15", "TradingVolume increase + valid price => trade"))

    t16 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, cpt=iso(9, 0, 1), mo_b=1, mo_s=5, und=1, ov=5)),
            rec(9, 1, 5, pay(px=1000, vol=60, cpt=iso(9, 9, 9), mo_b=10, mo_s=1, und=8, ov=2)),
            rec(9, 1, 10, pay(px=1001, vol=70, cpt=iso(8, 0, 0), mo_b=10, mo_s=1, und=8, ov=2)),
        ]
    )
    rows.append(_ok("CurrentPriceTime" not in INGRESS_KEYS and len(t16.signals) == 1, "T16", "CurrentPriceTime irrelevant"))

    t17 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, mo_b=1, mo_s=5, und=1, ov=5)),
            rec(9, 1, 5, pay(px=1000, vol=60, mo_b=10, mo_s=1, und=8, ov=2)),
            rec(9, 1, 10, pay(px=1000, vol=70, mo_b=10, mo_s=1, und=8, ov=2)),
        ]
    )
    rows.append(_ok(len(t17.signals) == 0 and t17.runs and float(t17.runs[0]["anchor"]) == 1000.0, "T17", "price == anchor => no entry"))

    t18 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, mo_b=1, mo_s=5, und=1, ov=5)),
            rec(9, 1, 5, pay(px=1000, vol=60, mo_b=10, mo_s=1, und=8, ov=2)),
            rec(9, 1, 10, pay(px=999, vol=70, mo_b=10, mo_s=1, und=8, ov=2)),
        ]
    )
    rows.append(_ok(len(t18.signals) == 0, "T18", "price < anchor => no entry"))

    t19 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, mo_b=1, mo_s=5, und=1, ov=5)),
            rec(9, 1, 5, pay(px=1000, vol=60, mo_b=10, mo_s=1, und=8, ov=2)),
            rec(9, 1, 10, pay(px=1001, vol=70, mo_b=10, mo_s=1, und=8, ov=2)),
        ]
    )
    rows.append(_ok(len(t19.signals) == 1 and t19.signals[0]["STRATEGY_ID"] == STRATEGY_ID, "T19", "price > anchor => entry"))

    t20 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, mo_b=1, mo_s=5, und=1, ov=5)),
            rec(9, 1, 5, pay(px=1000, vol=60, mo_b=10, mo_s=1, und=8, ov=2)),
            rec(9, 1, 10, pay(px=1001, vol=70, mo_b=10, mo_s=1, und=8, ov=2)),
            rec(9, 1, 15, pay(px=1005, vol=80, mo_b=10, mo_s=1, und=8, ov=2)),
        ]
    )
    rows.append(_ok(len(t20.signals) == 1, "T20", "one signal/run"))

    bd = board_at(t=ts(9, 1, 10), ask=float("nan"), aq=float("nan"))
    ex_no = evaluate_execution(bd, exec_id="X1", t0=ts(9, 1, 10), sess_end=AM_END)
    rows.append(_ok(bool(ex_no.get("WOULD_FILL")) is False, "T21", "X1 no Ask => no Fill"))

    dummy_fill = [
        {
            "date": DAY,
            "symbol": "1000",
            "t0": ts(9, 1, 10),
            "WOULD_FILL": True,
            "fill_t": ts(9, 1, 10),
            "exit_t": ts(9, 2, 0),
            "exit_reason": EXIT_ID,
            "pnl_yen_100": 0.0,
        }
    ]
    occ = replay(dummy_fill)
    rows.append(_ok(int(occ.get("fill_n") or 0) == 1, "T22", "Fill occupies slot"))

    hold_p = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, mo_b=1, mo_s=5, und=1, ov=5)),
            rec(9, 1, 5, pay(px=1000, vol=60, mo_b=10, mo_s=1, und=8, ov=2)),
            rec(9, 1, 10, pay(px=1001, vol=70, mo_b=10, mo_s=1, und=8, ov=2)),
            rec(9, 1, 20, pay(px=1002, vol=80, mo_b=1, mo_s=5, und=1, ov=5)),
        ]
    )
    fire_hold = hold_p.first_exit_fire(fill_t=ts(9, 1, 10), anchor=1000.0)
    rows.append(_ok(fire_hold is None, "T23", "pressure lost but price above anchor => HOLD"))

    hold_a = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, mo_b=1, mo_s=5, und=1, ov=5)),
            rec(9, 1, 5, pay(px=1000, vol=60, mo_b=10, mo_s=1, und=8, ov=2)),
            rec(9, 1, 10, pay(px=1001, vol=70, mo_b=10, mo_s=1, und=8, ov=2)),
            rec(9, 1, 20, pay(px=990, vol=80, mo_b=10, mo_s=1, und=8, ov=2)),
        ]
    )
    fire_hold_a = hold_a.first_exit_fire(fill_t=ts(9, 1, 10), anchor=1000.0)
    rows.append(_ok(fire_hold_a is None, "T24", "price below anchor but pressure still true => HOLD"))

    t25 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, mo_b=1, mo_s=5, und=1, ov=5)),
            rec(9, 1, 5, pay(px=1000, vol=60, mo_b=10, mo_s=1, und=8, ov=2)),
            rec(9, 1, 10, pay(px=1001, vol=70, mo_b=10, mo_s=1, und=8, ov=2)),
            rec(9, 1, 20, pay(px=990, vol=80, mo_b=1, mo_s=5, und=1, ov=5)),
        ]
    )
    fire = t25.first_exit_fire(fill_t=ts(9, 1, 10), anchor=1000.0)
    rows.append(_ok(fire is not None and abs(float(fire) - ts(9, 1, 20)) < 1e-6, "T25", "pressure lost + price below anchor => EXIT fire"))

    empty = BoardTape().view()
    pending = resolve_operator_exit(
        empty,
        {"finalize_t": np.asarray([ts(9, 1, 20)], dtype=float), "close": np.asarray([990.0], dtype=float)},
        exit_id=EXIT_ID,
        fill_t=ts(9, 1, 10),
        fill_px=1001.0,
        sess_end=AM_END,
        flatten_t=FLAT_T,
        fire_i=0,
    )
    rows.append(_ok(bool(pending.get("miss")) and pending.get("exit_t") is None, "T26", "EXIT fire without Bid => EXIT_PENDING"))

    with_bid = board_at(t=ts(9, 1, 21), bid=989.0)
    filled_ex = resolve_operator_exit(
        with_bid,
        {"finalize_t": np.asarray([ts(9, 1, 20)], dtype=float), "close": np.asarray([990.0], dtype=float)},
        exit_id=EXIT_ID,
        fill_t=ts(9, 1, 10),
        fill_px=1001.0,
        sess_end=AM_END,
        flatten_t=FLAT_T,
        fire_i=0,
    )
    rows.append(_ok(filled_ex.get("miss") is False and float(filled_ex.get("exit_price")) == 989.0, "T27", "Bid arrives => EXIT fill"))

    occ2 = replay(
        [
            {
                "date": DAY,
                "symbol": "1000",
                "t0": ts(9, 1, 10),
                "WOULD_FILL": True,
                "fill_t": ts(9, 1, 10),
                "exit_t": None,
                "exit_reason": "EXIT_MISS",
                "pnl_yen_100": None,
            }
        ]
    )
    rows.append(_ok(int(occ2.get("fill_n") or 0) == 0 and occupancy_rows(
        [{"WOULD_FILL": True, "fill_t": ts(9, 1, 10), "exit_t": None}]
    ) == [], "T28", "slot releases only on fill"))

    t29 = t20
    rows.append(_ok(len(t29.signals) == 1 and t29.runs[0]["signaled"] is True, "T29", "same-run reentry false"))

    t30 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, mo_b=1, mo_s=5, und=1, ov=5)),
            rec(9, 1, 5, pay(px=1000, vol=60, mo_b=10, mo_s=1, und=8, ov=2)),
            rec(9, 1, 10, pay(px=1001, vol=70, mo_b=10, mo_s=1, und=8, ov=2)),
            rec(9, 1, 15, pay(px=1001, vol=80, mo_b=1, mo_s=5, und=1, ov=5)),
            rec(9, 1, 20, pay(px=1001, vol=90, mo_b=10, mo_s=1, und=8, ov=2)),
            rec(9, 1, 25, pay(px=1002, vol=100, mo_b=10, mo_s=1, und=8, ov=2)),
        ]
    )
    rows.append(_ok(len(t30.runs) == 2 and len(t30.signals) == 2, "T30", "new-run reentry true"))

    cap_rows = []
    for i in range(6):
        cap_rows.append(
            {
                "date": DAY,
                "symbol": str(1000 + i),
                "t0": ts(9, 2, 0),
                "WOULD_FILL": True,
                "fill_t": ts(9, 2, 0),
                "exit_t": ts(9, 3, 0),
                "exit_reason": EXIT_ID,
                "pnl_yen_100": 0.0,
            }
        )
    cap_occ = replay(cap_rows)
    rows.append(
        _ok(
            int(POSITION_CAP_N) == 5 and int(cap_occ.get("fill_n") or 0) == 5 and int(cap_occ.get("cap_blocked") or 0) == 1,
            "T31",
            "CAP5",
        )
    )

    sess = resolve_operator_exit(
        empty,
        {"finalize_t": np.asarray([], dtype=float), "close": np.asarray([], dtype=float)},
        exit_id=EXIT_ID,
        fill_t=ts(9, 1, 10),
        fill_px=1001.0,
        sess_end=AM_END,
        flatten_t=FLAT_T,
        fire_i=None,
    )
    rows.append(_ok(sess.get("miss") is True and sess.get("exit_price") is None, "T32", "session close no synthetic Bid"))

    t33 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, mo_b=1, mo_s=5, und=1, ov=5)),
            rec(9, 1, 5, pay(px=1000, vol=60, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL, mo_b=10, mo_s=1, und=8, ov=2)),
            rec(9, 1, 10, pay(px=1001, vol=70, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL, mo_b=10, mo_s=1, und=8, ov=2)),
        ]
    )
    rows.append(_ok(len(t33.signals) == 0, "T33", "special quote cannot signal"))

    t34 = run(
        [
            rec(8, 50, 0, pay(px=1000, vol=50, opening=None, bid_sign=SIGN_PREOPEN, ask_sign=SIGN_PREOPEN, mo_b=10, mo_s=1, und=8, ov=2)),
            rec(8, 55, 0, pay(px=1001, vol=60, opening=None, bid_sign=SIGN_PREOPEN, ask_sign=SIGN_PREOPEN, mo_b=10, mo_s=1, und=8, ov=2)),
        ]
    )
    rows.append(_ok(len(t34.signals) == 0 and len(t34.runs) == 0, "T34", "preopen cannot signal"))

    rows.append(_ok(callable(portfolio_replay) and "ratio" in str(spec.get("FORBIDDEN") or []), "T35", "spec frozen"))

    mapped = {r["id"]: r for r in rows}
    ordered = [mapped[f"T{i:02d}"] for i in range(1, 35) if f"T{i:02d}" in mapped]
    extra = [mapped["T35"]] if "T35" in mapped else []
    all_rows = ordered + extra
    pass_n = sum(1 for r in all_rows if r.get("PASS"))
    return {
        "PASS_N": int(pass_n),
        "TOTAL": len(all_rows),
        "ALL_PASS": pass_n == len(all_rows) and len(ordered) == 34,
        "rows": all_rows,
    }
