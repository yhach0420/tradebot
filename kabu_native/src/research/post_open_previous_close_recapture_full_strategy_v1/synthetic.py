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
from research.post_open_previous_close_recapture_full_strategy_v1 import EXIT_ID, POSITION_CAP_N, STRATEGY_ID
from research.post_open_previous_close_recapture_full_strategy_v1.fields import INGRESS_KEYS, prev_close_valid
from research.post_open_previous_close_recapture_full_strategy_v1.fsm import PrevCloseFsm
from research.post_open_previous_close_recapture_full_strategy_v1.library import strategy_spec
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
    pc: Any = 1010.0,
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
        "PreviousClose": pc,
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


def run(events: list[tuple[float, dict[str, Any]]]) -> PrevCloseFsm:
    fsm = PrevCloseFsm(symbol="1000", date=DAY, flatten_t=FLAT_T, am_start=AM_START)
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

    rows.append(_ok(prev_close_valid({"PreviousClose": None}) is None, "T01", "PreviousClose missing"))
    rows.append(_ok(prev_close_valid({"PreviousClose": 0}) is None, "T02", "PreviousClose zero"))
    rows.append(_ok(prev_close_valid({"PreviousClose": -1}) is None, "T03", "PreviousClose negative"))

    t04 = run([rec(9, 1, 0, pay(px=1000, vol=50, pc=1010))])
    rows.append(_ok(len(t04.episodes) == 1 and len(t04.signals) == 0, "T04", "OTU below PreviousClose starts below regime"))

    t05 = run([rec(9, 1, 0, pay(px=1010, vol=50, pc=1010))])
    rows.append(_ok(len(t05.episodes) == 0 and len(t05.signals) == 0, "T05", "OTU == PreviousClose is not below or recapture"))

    t06 = run([rec(9, 1, 0, pay(px=1020, vol=50, pc=1010))])
    rows.append(_ok(len(t06.signals) == 0, "T06", "OTU above without prior below = no ENTRY"))

    t07 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, pc=1010)),
            rec(9, 1, 5, pay(px=1020, vol=60, pc=1010)),
        ]
    )
    rows.append(_ok(len(t07.signals) == 1 and float(t07.signals[0]["anchor"]) == 1010.0, "T07", "below then recapture = ENTRY"))

    t08 = t07
    rows.append(_ok(len(t08.episodes) == 1, "T08", "one episode for below→recapture"))

    t09 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, pc=1010)),
            rec(9, 1, 5, pay(px=1020, vol=60, pc=1010)),
            rec(9, 1, 10, pay(px=1030, vol=70, pc=1010)),
        ]
    )
    rows.append(_ok(len(t09.signals) == 1, "T09", "still above after recapture = no new episode"))

    t10 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, pc=1010)),
            rec(9, 1, 5, pay(px=1000, vol=50, pc=1010)),
        ]
    )
    rows.append(_ok(len(t10.episodes) == 1 and len(t10.otus) == 1, "T10", "volume unchanged = not a new OTU"))

    rows.append(
        _ok(
            len(run([rec(9, 1, 0, pay(px=1000, vol=50, pc=1010)), rec(9, 1, 5, pay(px=999, vol=60, pc=1010))]).otus) == 2,
            "T11",
            "volume increase + CurrentPrice = OTU",
        )
    )

    t12 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, pc=1010, cpt=iso(8, 0, 0))),
            rec(9, 1, 5, pay(px=1020, vol=60, pc=1010, cpt=iso(1, 0, 0))),
        ]
    )
    rows.append(_ok("CurrentPriceTime" not in INGRESS_KEYS and len(t12.signals) == 1, "T12", "CurrentPriceTime irrelevant"))

    t13 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, pc=1010)),
            rec(9, 1, 5, pay(px=1020, vol=60, pc=1010)),
            rec(9, 1, 10, pay(px=1000, vol=70, pc=1010)),
            rec(9, 1, 15, pay(px=1025, vol=80, pc=1010)),
        ]
    )
    rows.append(_ok(len(t13.episodes) == 2 and len(t13.signals) == 2, "T13", "new below after recapture = new episode"))

    rows.append(_ok(float(t07.episodes[0]["anchor"]) == 1010.0, "T14", "anchor = PreviousClose immutable"))

    t15 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, pc=1010)),
            rec(9, 1, 5, pay(px=1010, vol=60, pc=1010)),
        ]
    )
    rows.append(_ok(len(t15.signals) == 0, "T15", "return to exactly PreviousClose is not recapture"))

    t16 = t07
    rows.append(_ok(len(t16.signals) == 1, "T16", "one signal per episode"))

    bd = board_at(t=ts(9, 1, 5), ask=float("nan"))
    ex_no = evaluate_execution(bd, exec_id="X1", t0=ts(9, 1, 5), sess_end=AM_END)
    rows.append(_ok(bool(ex_no.get("WOULD_FILL")) is False, "T17", "no Ask = no fill"))

    occ = replay(
        [
            {
                "date": DAY,
                "symbol": "1000",
                "t0": ts(9, 1, 5),
                "WOULD_FILL": True,
                "fill_t": ts(9, 1, 5),
                "exit_t": ts(9, 2, 0),
                "exit_reason": EXIT_ID,
                "pnl_yen_100": 0.0,
            }
        ]
    )
    rows.append(_ok(int(occ.get("fill_n") or 0) == 1, "T18", "fill occupies slot"))

    hold_eq = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, pc=1010)),
            rec(9, 1, 5, pay(px=1020, vol=60, pc=1010)),
            rec(9, 1, 20, pay(px=1010, vol=70, pc=1010)),
        ]
    )
    rows.append(_ok(hold_eq.first_exit_fire(fill_t=ts(9, 1, 5), anchor=1010.0) is None, "T19", "post-fill OTU == PreviousClose = HOLD"))

    hold_up = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, pc=1010)),
            rec(9, 1, 5, pay(px=1020, vol=60, pc=1010)),
            rec(9, 1, 20, pay(px=1030, vol=70, pc=1010)),
        ]
    )
    rows.append(_ok(hold_up.first_exit_fire(fill_t=ts(9, 1, 5), anchor=1010.0) is None, "T20", "post-fill OTU > PreviousClose = HOLD"))

    t21 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, pc=1010)),
            rec(9, 1, 5, pay(px=1020, vol=60, pc=1010)),
            rec(9, 1, 20, pay(px=1000, vol=70, pc=1010)),
        ]
    )
    fire = t21.first_exit_fire(fill_t=ts(9, 1, 5), anchor=1010.0)
    rows.append(_ok(fire is not None and abs(float(fire) - ts(9, 1, 20)) < 1e-6, "T21", "post-fill OTU < PreviousClose = EXIT"))

    empty = BoardTape().view()
    pending = resolve_operator_exit(
        empty,
        {"finalize_t": np.asarray([ts(9, 1, 20)], dtype=float), "close": np.asarray([1010.0], dtype=float)},
        exit_id=EXIT_ID,
        fill_t=ts(9, 1, 5),
        fill_px=1021.0,
        sess_end=AM_END,
        flatten_t=FLAT_T,
        fire_i=0,
    )
    rows.append(_ok(bool(pending.get("miss")) and pending.get("exit_t") is None, "T22", "EXIT fire without Bid = pending"))

    filled_ex = resolve_operator_exit(
        board_at(t=ts(9, 1, 21), bid=999.0),
        {"finalize_t": np.asarray([ts(9, 1, 20)], dtype=float), "close": np.asarray([1010.0], dtype=float)},
        exit_id=EXIT_ID,
        fill_t=ts(9, 1, 5),
        fill_px=1021.0,
        sess_end=AM_END,
        flatten_t=FLAT_T,
        fire_i=0,
    )
    rows.append(_ok(filled_ex.get("miss") is False, "T23", "Bid fill = close"))

    rows.append(
        _ok(
            occupancy_rows([{"WOULD_FILL": True, "fill_t": ts(9, 1, 5), "exit_t": None}]) == [],
            "T24",
            "slot release only after fill",
        )
    )

    rows.append(_ok(len(t09.signals) == 1, "T25", "same episode no reentry"))
    rows.append(_ok(len(t13.signals) == 2, "T26", "new episode reentry"))

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
            "T27",
            "CAP5",
        )
    )

    sess = resolve_operator_exit(
        empty,
        {"finalize_t": np.asarray([], dtype=float), "close": np.asarray([], dtype=float)},
        exit_id=EXIT_ID,
        fill_t=ts(9, 1, 5),
        fill_px=1021.0,
        sess_end=AM_END,
        flatten_t=FLAT_T,
        fire_i=None,
    )
    rows.append(_ok(sess.get("miss") is True, "T28", "session close no synthetic Bid"))

    t29 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50, pc=1010, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
            rec(9, 1, 5, pay(px=1020, vol=60, pc=1010, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
        ]
    )
    rows.append(_ok(len(t29.signals) == 0, "T29", "special quote no signal"))

    t30 = run(
        [
            rec(8, 50, 0, pay(px=1000, vol=50, pc=1010, opening=None, bid_sign=SIGN_PREOPEN, ask_sign=SIGN_PREOPEN)),
            rec(8, 55, 0, pay(px=1020, vol=60, pc=1010, opening=None, bid_sign=SIGN_PREOPEN, ask_sign=SIGN_PREOPEN)),
        ]
    )
    rows.append(_ok(len(t30.signals) == 0 and len(t30.episodes) == 0, "T30", "preopen no signal"))

    rows.append(_ok(STRATEGY_ID == "PREV_CLOSE_RECAPTURE_X1_Z_PREV_CLOSE_LOSS_V1" and callable(portfolio_replay), "T31", "strategy id frozen"))
    rows.append(_ok("bps/tick/ATR" in str(spec.get("FORBIDDEN") or []), "T32", "spec frozen"))
    rows.append(_ok(len(t07.otus) == 2, "T33", "recapture OTU is Observed Trade Update"))
    rows.append(_ok(t07.signals[0]["t0"] == ts(9, 1, 5), "T34", "ENTRY is recapture event itself"))
    rows.append(_ok(t04.below_event_n == 1 and t04.recapture_n == 0, "T35", "below without recapture is not ENTRY"))

    mapped = {r["id"]: r for r in rows}
    ordered = [mapped[f"T{i:02d}"] for i in range(1, 36) if f"T{i:02d}" in mapped]
    pass_n = sum(1 for r in ordered if r.get("PASS"))
    return {
        "PASS_N": int(pass_n),
        "TOTAL": len(ordered),
        "ALL_PASS": pass_n == len(ordered) and len(ordered) >= 30,
        "rows": ordered,
    }
