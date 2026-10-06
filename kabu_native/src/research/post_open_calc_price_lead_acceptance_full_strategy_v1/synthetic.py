"""Deterministic T01-T35. No Capture. No economics."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x34a_execution_policy.executable_board import SIGN_GENERAL, SIGN_PREOPEN, SIGN_SPECIAL
from research.full_causal_mechanism_discovery_v1.analyze import occupancy_rows, replay
from research.full_causal_mechanism_discovery_v1.exits import resolve_operator_exit
from research.new_entry_breakout_continuation_v1.harvest import BoardTape
from research.post_open_calc_price_lead_acceptance_full_strategy_v1 import EXIT_ID, POSITION_CAP_N, STRATEGY_ID
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.calc import INGRESS_KEYS, calc_valid
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.fsm import CalcLeadFsm
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.library import strategy_spec
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


def pay(
    *,
    px: float | None = 1000.0,
    vol: float | None = 100.0,
    calc: Any = 1000.0,
    bid_sign: str = SIGN_GENERAL,
    ask_sign: str = SIGN_GENERAL,
    opening: float | None = 1000.0,
    status: int = 1,
    cpt: str | None = None,
    ask: float | None = 1001.0,
    bid: float | None = 999.0,
) -> dict[str, Any]:
    return {
        "OpeningPrice": opening,
        "OpeningPriceTime": OPEN_ISO if opening else None,
        "CurrentPrice": px,
        "CurrentPriceTime": cpt,
        "CurrentPriceStatus": status,
        "TradingVolume": vol,
        "CalcPrice": calc,
        "BidSign": bid_sign,
        "AskSign": ask_sign,
        "Buy1": {"Price": bid, "Qty": 200, "Sign": bid_sign, "Time": iso(9, 1, 0)},
        "Sell1": {"Price": ask, "Qty": 200, "Sign": ask_sign, "Time": iso(9, 1, 0)},
    }


def rec(h: int, m: int, s: int, payload: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    t = ts(h, m, s)
    payload = dict(payload)
    payload["received_at"] = iso(h, m, s)
    return t, payload


def run(events: list[tuple[float, dict[str, Any]]]) -> CalcLeadFsm:
    fsm = CalcLeadFsm(symbol="1000", date=DAY, flatten_t=FLAT_T, am_start=AM_START)
    for t, p in events:
        fsm.process(ingress_t=t, payload=p)
    return fsm


def _ok(cond: bool, name: str, detail: str = "") -> dict[str, Any]:
    return {"id": name, "PASS": bool(cond), "detail": detail}


def board_at(*, t: float, bid: float = 999.0, ask: float = 1001.0, special: bool = False) -> dict[str, Any]:
    tape = BoardTape()
    tape.append(
        {
            "t": float(t),
            "bid": float(bid),
            "ask": float(ask) if ask == ask else float("nan"),
            "bid_qty": 200.0,
            "ask_qty": 200.0 if ask == ask else float("nan"),
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

    rows.append(_ok(calc_valid({"CalcPrice": None}) is None, "T01", "CalcPrice missing"))
    rows.append(_ok(calc_valid({"CalcPrice": 0}) is None, "T02", "CalcPrice zero"))
    rows.append(_ok(calc_valid({"CalcPrice": -1}) is None, "T03", "CalcPrice negative"))

    t04 = run([rec(9, 1, 0, pay(px=1000, vol=50, calc=1010))])
    rows.append(_ok(len(t04.episodes) == 0 and t04.no_prior_trade_n == 1, "T04", "no prior trade"))

    t05 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 5, pay(px=1000, vol=50, calc=1000)),
        ]
    )
    rows.append(_ok(len(t05.episodes) == 0, "T05", "CalcPrice == last trade"))

    t06 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 5, pay(px=1000, vol=50, calc=990)),
        ]
    )
    rows.append(_ok(len(t06.episodes) == 0, "T06", "CalcPrice < last trade"))

    t07 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 5, pay(px=1000, vol=50, calc=1010)),
        ]
    )
    rows.append(_ok(len(t07.episodes) == 1 and float(t07.episodes[0]["anchor"]) == 1010.0, "T07", "CalcPrice > last trade"))

    t08 = t07
    rows.append(_ok(len(t08.episodes) == 1, "T08", "false→true episode start"))

    t09 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 5, pay(px=1000, vol=50, calc=1010)),
            rec(9, 1, 10, pay(px=1000, vol=50, calc=1012)),
        ]
    )
    rows.append(_ok(len(t09.episodes) == 1, "T09", "true→true no new episode"))

    t10 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 5, pay(px=1000, vol=50, calc=1010)),
            rec(9, 1, 10, pay(px=1000, vol=50, calc=1000)),
        ]
    )
    rows.append(
        _ok(
            len(t10.episodes) == 1
            and t10.episodes[0].get("end_reason") == "CALC_LEAD_FAILED_BEFORE_ACCEPTANCE"
            and t10.preaccept_failure_n == 1,
            "T10",
            "true→false pre-entry invalidation",
        )
    )

    t11 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 5, pay(px=1000, vol=50, calc=1010)),
            rec(9, 1, 10, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 15, pay(px=1000, vol=50, calc=1015)),
        ]
    )
    rows.append(_ok(len(t11.episodes) == 2, "T11", "false→true new episode"))

    t12 = t09
    rows.append(_ok(float(t12.episodes[0]["anchor"]) == 1010.0, "T12", "anchor immutable"))

    t13 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 5, pay(px=1010, vol=50, calc=1010)),
        ]
    )
    rows.append(_ok(len(t13.otus) == 1, "T13", "trade volume unchanged = not trade"))

    rows.append(_ok(len(run([rec(9, 1, 0, pay(px=1000, vol=50)), rec(9, 1, 5, pay(px=1001, vol=60))]).otus) == 2, "T14", "volume increase + CurrentPrice = trade"))

    t15 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000, cpt=iso(8, 0, 0))),
            rec(9, 1, 5, pay(px=1000, vol=50, calc=1010, cpt=iso(9, 9, 9))),
            rec(9, 1, 10, pay(px=1010, vol=60, calc=1010, cpt=iso(1, 0, 0))),
        ]
    )
    rows.append(_ok("CurrentPriceTime" not in INGRESS_KEYS and len(t15.signals) == 1, "T15", "CurrentPriceTime irrelevant"))

    t16 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 5, pay(px=1000, vol=50, calc=1010)),
            rec(9, 1, 10, pay(px=1009, vol=60, calc=1010)),
        ]
    )
    rows.append(_ok(len(t16.signals) == 0, "T16", "trade below anchor = no ENTRY"))

    t17 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 5, pay(px=1000, vol=50, calc=1010)),
            rec(9, 1, 10, pay(px=1010, vol=60, calc=1010)),
        ]
    )
    rows.append(_ok(len(t17.signals) == 1, "T17", "trade == anchor = ENTRY"))

    t18 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 5, pay(px=1000, vol=50, calc=1010)),
            rec(9, 1, 10, pay(px=1011, vol=60, calc=1010)),
        ]
    )
    rows.append(_ok(len(t18.signals) == 1, "T18", "trade above anchor = ENTRY"))

    t19 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 5, pay(px=1000, vol=50, calc=1010)),
            rec(9, 1, 10, pay(px=1010, vol=60, calc=1010)),
            rec(9, 1, 15, pay(px=1012, vol=70, calc=1010)),
        ]
    )
    rows.append(_ok(len(t19.signals) == 1, "T19", "one signal per episode"))

    bd = board_at(t=ts(9, 1, 10), ask=float("nan"))
    ex_no = evaluate_execution(bd, exec_id="X1", t0=ts(9, 1, 10), sess_end=AM_END)
    rows.append(_ok(bool(ex_no.get("WOULD_FILL")) is False, "T20", "no Ask = no fill"))

    occ = replay(
        [
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
    )
    rows.append(_ok(int(occ.get("fill_n") or 0) == 1, "T21", "fill occupies slot"))

    hold1 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 5, pay(px=1000, vol=50, calc=1010)),
            rec(9, 1, 10, pay(px=1010, vol=60, calc=1010)),
            rec(9, 1, 20, pay(px=1000, vol=70, calc=1010)),
        ]
    )
    rows.append(_ok(hold1.first_exit_fire(fill_t=ts(9, 1, 10), anchor=1010.0) is None, "T22", "trade < anchor / calc >= anchor = HOLD"))

    hold2 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 5, pay(px=1000, vol=50, calc=1010)),
            rec(9, 1, 10, pay(px=1010, vol=60, calc=1010)),
            rec(9, 1, 20, pay(px=1010, vol=70, calc=1000)),
        ]
    )
    rows.append(_ok(hold2.first_exit_fire(fill_t=ts(9, 1, 10), anchor=1010.0) is None, "T23", "trade >= anchor / calc < anchor = HOLD"))

    t24 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 5, pay(px=1000, vol=50, calc=1010)),
            rec(9, 1, 10, pay(px=1010, vol=60, calc=1010)),
            rec(9, 1, 20, pay(px=1000, vol=70, calc=1000)),
        ]
    )
    fire = t24.first_exit_fire(fill_t=ts(9, 1, 10), anchor=1010.0)
    rows.append(_ok(fire is not None and abs(float(fire) - ts(9, 1, 20)) < 1e-6, "T24", "trade < anchor / calc < anchor = EXIT fire"))

    empty = BoardTape().view()
    pending = resolve_operator_exit(
        empty,
        {"finalize_t": np.asarray([ts(9, 1, 20)], dtype=float), "close": np.asarray([1000.0], dtype=float)},
        exit_id=EXIT_ID,
        fill_t=ts(9, 1, 10),
        fill_px=1011.0,
        sess_end=AM_END,
        flatten_t=FLAT_T,
        fire_i=0,
    )
    rows.append(_ok(bool(pending.get("miss")) and pending.get("exit_t") is None, "T25", "EXIT fire without Bid = pending"))

    filled_ex = resolve_operator_exit(
        board_at(t=ts(9, 1, 21), bid=999.0),
        {"finalize_t": np.asarray([ts(9, 1, 20)], dtype=float), "close": np.asarray([1000.0], dtype=float)},
        exit_id=EXIT_ID,
        fill_t=ts(9, 1, 10),
        fill_px=1011.0,
        sess_end=AM_END,
        flatten_t=FLAT_T,
        fire_i=0,
    )
    rows.append(_ok(filled_ex.get("miss") is False, "T26", "Bid fill = close"))

    rows.append(
        _ok(
            occupancy_rows([{"WOULD_FILL": True, "fill_t": ts(9, 1, 10), "exit_t": None}]) == [],
            "T27",
            "slot release only after fill",
        )
    )

    rows.append(_ok(len(t19.signals) == 1, "T28", "same episode no reentry"))

    t29 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 5, pay(px=1000, vol=50, calc=1010)),
            rec(9, 1, 10, pay(px=1010, vol=60, calc=1010)),
            rec(9, 1, 15, pay(px=1010, vol=70, calc=1000)),
            rec(9, 1, 20, pay(px=1010, vol=70, calc=1020)),
            rec(9, 1, 25, pay(px=1020, vol=80, calc=1020)),
        ]
    )
    rows.append(_ok(len(t29.episodes) == 2 and len(t29.signals) == 2, "T29", "new episode reentry"))

    cap_rows = [
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
        for i in range(6)
    ]
    cap_occ = replay(cap_rows)
    rows.append(
        _ok(
            int(POSITION_CAP_N) == 5 and int(cap_occ.get("fill_n") or 0) == 5 and int(cap_occ.get("cap_blocked") or 0) == 1,
            "T30",
            "CAP5",
        )
    )

    sess = resolve_operator_exit(
        empty,
        {"finalize_t": np.asarray([], dtype=float), "close": np.asarray([], dtype=float)},
        exit_id=EXIT_ID,
        fill_t=ts(9, 1, 10),
        fill_px=1011.0,
        sess_end=AM_END,
        flatten_t=FLAT_T,
        fire_i=None,
    )
    rows.append(_ok(sess.get("miss") is True, "T31", "session close no synthetic Bid"))

    t32 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, calc=1000)),
            rec(9, 1, 5, pay(px=1000, vol=50, calc=1010, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
            rec(9, 1, 10, pay(px=1010, vol=60, calc=1010, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
        ]
    )
    rows.append(_ok(len(t32.signals) == 0, "T32", "special quote no signal"))

    t33 = run(
        [
            rec(8, 50, 0, pay(px=1000, vol=50, calc=1010, opening=None, bid_sign=SIGN_PREOPEN, ask_sign=SIGN_PREOPEN)),
            rec(8, 55, 0, pay(px=1010, vol=60, calc=1010, opening=None, bid_sign=SIGN_PREOPEN, ask_sign=SIGN_PREOPEN)),
        ]
    )
    rows.append(_ok(len(t33.signals) == 0 and len(t33.episodes) == 0, "T33", "preopen no signal"))

    rows.append(_ok(STRATEGY_ID == "CALC_LEAD_UP_ACCEPT_X1_Z_CALC_ANCHOR_LOSS_V1" and callable(portfolio_replay), "T34", "strategy id frozen"))
    rows.append(_ok("bps/tick/ATR" in str(spec.get("FORBIDDEN") or []), "T35", "spec frozen"))

    mapped = {r["id"]: r for r in rows}
    ordered = [mapped[f"T{i:02d}"] for i in range(1, 36) if f"T{i:02d}" in mapped]
    pass_n = sum(1 for r in ordered if r.get("PASS"))
    return {
        "PASS_N": int(pass_n),
        "TOTAL": len(ordered),
        "ALL_PASS": pass_n == len(ordered) and len(ordered) >= 30,
        "rows": ordered,
    }
