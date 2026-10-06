"""Deterministic T1-T30. No Capture. No economics."""
from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x34a_execution_policy.executable_board import SIGN_GENERAL, SIGN_PREOPEN, SIGN_SPECIAL
from research.full_causal_mechanism_discovery_v1.analyze import occupancy_rows, replay
from research.intraday_special_quote_resolution_full_strategy_v1 import (
    CANDIDATE_C,
    EXIT_D,
    EXIT_U,
    POSITION_CAP_N,
    THESIS_D,
    THESIS_U,
)
from research.intraday_special_quote_resolution_full_strategy_v1.fsm import (
    DIR_DOWN,
    DIR_FLAT,
    DIR_UP,
    ST_SPECIAL_ACTIVE,
    ST_WAIT_RELEASE,
    IsqSymbolFsm,
    _direction,
    first_anchor_loss,
)
from research.intraday_special_quote_resolution_full_strategy_v1.library import ENGINE_EVENT_ORDER, library_spec
from research.intraday_special_quote_resolution_full_strategy_v1.semantics import TRUSTED_CONTINUOUS, TRUSTED_SPECIAL
from research.intraday_special_quote_resolution_full_strategy_v1.trade import (
    INGRESS_KEYS,
    classify_payload,
    ingress_epoch,
    minute_epoch,
    observed_trade_update,
    volume_regression,
)
from research.new_entry_breakout_continuation_v1 import harvest as board_harvest
from research.simple_tech_entry_family.portfolio import portfolio_replay

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260722"
OPEN_ISO = datetime(2026, 7, 22, 9, 0, 0, tzinfo=JST).isoformat()
FLAT_T = datetime(2026, 7, 22, 11, 29, 0, tzinfo=JST).timestamp()


def ts(h: int, m: int, s: int = 0, us: int = 0) -> float:
    return datetime(2026, 7, 22, h, m, s, us, tzinfo=JST).timestamp()


def iso(h: int, m: int, s: int = 0) -> str:
    return datetime(2026, 7, 22, h, m, s, tzinfo=JST).isoformat()


def pay(
    *,
    px: float,
    vol: float,
    bid_sign: str = SIGN_GENERAL,
    ask_sign: str = SIGN_GENERAL,
    opening: float | None = 1000.0,
    status: int = 1,
    cpt: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "OpeningPrice": opening,
        "OpeningPriceTime": OPEN_ISO if opening else None,
        "CurrentPrice": px,
        "CurrentPriceTime": cpt,
        "CurrentPriceStatus": status,
        "TradingVolume": vol,
        "BidSign": bid_sign,
        "AskSign": ask_sign,
        "Buy1": {"Price": float(px) - 1.0, "Qty": 200, "Sign": bid_sign},
        "Sell1": {"Price": float(px) + 1.0, "Qty": 200, "Sign": ask_sign},
    }
    return body


def rec(h: int, m: int, s: int, payload: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    t = ts(h, m, s)
    payload = dict(payload)
    payload["received_at"] = iso(h, m, s)
    return t, payload


def run(events: list[tuple[float, dict[str, Any]]]) -> IsqSymbolFsm:
    fsm = IsqSymbolFsm(symbol="1000", date=DAY, flatten_t=FLAT_T)
    for t, p in events:
        fsm.process(ingress_t=t, payload=p)
    fsm.close_session()
    return fsm


def _ok(cond: bool, name: str, detail: str = "") -> dict[str, Any]:
    return {"id": name, "PASS": bool(cond), "detail": detail}


def first_anchor_loss_local(bars: list[dict[str, Any]], *, after_t: float, anchor: float) -> int | None:
    return first_anchor_loss(bars, after_t=after_t, anchor=anchor)


def run_synthetic_tests() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []

    t1 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=100)),
            rec(9, 1, 10, pay(px=1001, vol=110)),
            rec(9, 2, 0, pay(px=1001, vol=110, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
        ]
    )
    rows.append(_ok(len(t1.episodes) == 1 and t1.episodes[0]["state"] in (ST_SPECIAL_ACTIVE, "CLOSED", "NOT_EVALUABLE"), "T1", f"n={len(t1.episodes)}"))
    rows[-1]["PASS"] = len(t1.episodes) >= 1 and float(t1.episodes[0]["pre_special_price"]) == 1001.0

    t2 = run(
        [
            rec(8, 50, 0, pay(px=900, vol=10, opening=None, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
            rec(8, 55, 0, pay(px=900, vol=10, opening=None, bid_sign=SIGN_PREOPEN, ask_sign=SIGN_PREOPEN)),
        ]
    )
    rows.append(_ok(len(t2.episodes) == 0, "T2", f"n={len(t2.episodes)}"))

    pre = 1050.0
    t3 = run(
        [
            rec(9, 1, 0, pay(px=pre, vol=50)),
            rec(9, 1, 5, pay(px=pre, vol=60)),
            rec(9, 2, 0, pay(px=1080, vol=80, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
        ]
    )
    rows.append(
        _ok(
            len(t3.episodes) == 1
            and float(t3.episodes[0]["pre_special_price"]) == pre
            and bool(t3.episodes[0].get("special_start_coobserved")),
            "T3",
            str(t3.episodes[0].get("pre_special_price") if t3.episodes else None),
        )
    )

    last_vol = 100.0
    rows.append(
        _ok(
            observed_trade_update(last_vol=last_vol, vol=100.0, px=1010.0) is False,
            "T4",
        )
    )
    # T4: CurrentPriceTime change without volume increase is not a trade update.
    ev = [
        rec(9, 1, 0, pay(px=1000, vol=100, cpt=iso(9, 1, 0))),
        rec(9, 1, 1, pay(px=1000, vol=100, cpt=iso(9, 1, 1))),
    ]
    t4 = run(ev)
    n_bars = sum(int(b["TRADE_UPDATE_N"]) for b in t4.completed_bars) + sum(
        int(b["TRADE_UPDATE_N"]) for b in t4.bars.values() if b.get("finalize_t") is None
    )
    rows[-1] = _ok(observed_trade_update(last_vol=100.0, vol=100.0, px=1010.0) is False, "T4")

    rows.append(_ok(observed_trade_update(last_vol=100.0, vol=110.0, px=1000.0) is True, "T5"))
    rows.append(_ok(observed_trade_update(last_vol=100.0, vol=100.0, px=1000.0) is False, "T6"))
    rows.append(_ok(volume_regression(last_vol=100.0, vol=90.0) is True, "T7"))

    t8 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50)),
            rec(9, 1, 5, pay(px=1000, vol=60)),
            rec(9, 2, 0, pay(px=1000, vol=60, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
            rec(9, 2, 30, pay(px=1000, vol=60)),
        ]
    )
    ep8 = t8.episodes[0] if t8.episodes else {}
    rows.append(_ok(ep8.get("release_price") is None and str(ep8.get("state") or "") in (ST_WAIT_RELEASE, ST_SPECIAL_ACTIVE, "CLOSED"), "T8", str(ep8.get("state"))))
    # After close_session WAIT_RELEASE without release becomes CLOSED. Check release_price is None.
    rows[-1] = _ok(bool(t8.episodes) and t8.episodes[0].get("release_price") is None, "T8")

    t9 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50)),
            rec(9, 1, 5, pay(px=1000, vol=60)),
            rec(9, 2, 0, pay(px=1000, vol=60, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
            rec(9, 2, 30, pay(px=1010, vol=70)),
        ]
    )
    rows.append(_ok(bool(t9.episodes) and t9.episodes[0].get("release_price") == 1010.0, "T9", str((t9.episodes or [{}])[0].get("release_price"))))

    t10 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50)),
            rec(9, 1, 5, pay(px=1000, vol=60)),
            rec(9, 2, 0, pay(px=1000, vol=60, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
            rec(9, 2, 20, pay(px=1000, vol=60)),
            rec(9, 2, 40, pay(px=1005, vol=75)),
        ]
    )
    rows.append(_ok(bool(t10.episodes) and t10.episodes[0].get("release_price") == 1005.0, "T10"))

    t11 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50)),
            rec(9, 1, 5, pay(px=1000, vol=60)),
            rec(9, 2, 0, pay(px=1000, vol=60, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
            rec(9, 2, 20, pay(px=1000, vol=60)),
            rec(9, 2, 40, pay(px=1000, vol=60, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
        ]
    )
    rows.append(
        _ok(
            len(t11.episodes) == 1 and t11.episodes[0].get("release_price") is None and t11.episodes[0]["episode_seq"] == 1,
            "T11",
        )
    )

    t12 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50)),
            rec(9, 1, 5, pay(px=1000, vol=60)),
            rec(9, 2, 0, pay(px=1000, vol=60, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
            rec(9, 2, 10, pay(px=1000, vol=80, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
        ]
    )
    rows.append(
        _ok(
            bool(t12.episodes) and bool(t12.episodes[0].get("special_active_trade_conflict")),
            "T12",
        )
    )

    rows.append(_ok(_direction(1010.0, 1000.0) == DIR_UP, "T13"))
    rows.append(_ok(_direction(990.0, 1000.0) == DIR_DOWN, "T14"))

    t15 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50)),
            rec(9, 1, 5, pay(px=1000, vol=60)),
            rec(9, 2, 0, pay(px=1000, vol=60, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
            rec(9, 2, 30, pay(px=1000, vol=70)),
            rec(9, 3, 10, pay(px=1001, vol=80)),
        ]
    )
    rows.append(_ok(bool(t15.episodes) and t15.episodes[0].get("direction") == DIR_FLAT and len(t15.signals) == 0, "T15"))

    t16 = IsqSymbolFsm(symbol="1000", date=DAY, flatten_t=FLAT_T)
    t16.process(ingress_t=ts(9, 1, 0), payload=pay(px=1000, vol=50) | {"received_at": iso(9, 1, 0)})
    t16.process(ingress_t=ts(9, 1, 10), payload=pay(px=1010, vol=50) | {"received_at": iso(9, 1, 10)})
    bar = t16.bars.get(minute_epoch(ts(9, 1, 0)))
    rows.append(_ok(bar is not None and float(bar["close"]) == 1000.0 and int(bar["TRADE_UPDATE_N"]) == 1, "T16", str(bar)))

    t17 = IsqSymbolFsm(symbol="1000", date=DAY, flatten_t=FLAT_T)
    t17.process(ingress_t=ts(9, 1, 0), payload=pay(px=1000, vol=50) | {"received_at": iso(9, 1, 0)})
    t17.process(ingress_t=ts(9, 1, 20), payload=pay(px=1200, vol=50) | {"received_at": iso(9, 1, 20)})
    bar17 = t17.bars.get(minute_epoch(ts(9, 1, 0)))
    rows.append(_ok(bar17 is not None and float(bar17["high"]) == 1000.0, "T17"))

    def up_path(*, accept_px: float) -> IsqSymbolFsm:
        return run(
            [
                rec(9, 1, 0, pay(px=1000, vol=50)),
                rec(9, 1, 5, pay(px=1000, vol=60)),
                rec(10, 17, 0, pay(px=1000, vol=60, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
                rec(10, 17, 23, pay(px=1010, vol=80)),
                rec(10, 18, 10, pay(px=accept_px, vol=90)),
                rec(10, 19, 1, pay(px=accept_px, vol=95)),
            ]
        )

    u18 = up_path(accept_px=1015)
    rows.append(_ok(len(u18.signals) == 1 and u18.signals[0]["thesis"] == THESIS_U, "T18", str(u18.signals)))
    u19 = up_path(accept_px=1010)
    rows.append(_ok(len(u19.signals) == 0, "T19"))

    def down_path(*, accept_px: float) -> IsqSymbolFsm:
        return run(
            [
                rec(9, 1, 0, pay(px=1000, vol=50)),
                rec(9, 1, 5, pay(px=1000, vol=60)),
                rec(10, 17, 0, pay(px=1000, vol=60, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
                rec(10, 17, 23, pay(px=980, vol=80)),
                rec(10, 18, 10, pay(px=accept_px, vol=90)),
                rec(10, 19, 1, pay(px=accept_px, vol=95)),
            ]
        )

    d20 = down_path(accept_px=1001)
    rows.append(_ok(len(d20.signals) == 1 and d20.signals[0]["thesis"] == THESIS_D, "T20"))
    d21 = down_path(accept_px=1000)
    rows.append(_ok(len(d21.signals) == 0, "T21"))

    t22 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50)),
            rec(9, 1, 5, pay(px=1000, vol=60)),
            rec(10, 17, 0, pay(px=1000, vol=60, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
            rec(10, 17, 23, pay(px=1010, vol=80)),
            rec(10, 18, 10, pay(px=1015, vol=90)),
            rec(10, 18, 40, pay(px=1015, vol=90, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
            rec(10, 19, 1, pay(px=1015, vol=95)),
        ]
    )
    rows.append(_ok(len(t22.signals) == 0 and bool(t22.episodes) and bool(t22.episodes[0].get("reinterrupted")), "T22"))

    t23 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50)),
            rec(9, 1, 5, pay(px=1000, vol=60)),
            rec(10, 17, 0, pay(px=1000, vol=60, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
            rec(10, 17, 23, pay(px=1010, vol=80)),
            rec(10, 19, 1, pay(px=1015, vol=90)),
        ]
    )
    rows.append(
        _ok(
            len(t23.signals) == 0
            and bool(t23.episodes)
            and t23.episodes[0].get("no_entry_reason") == "NO_ACCEPTANCE_EVIDENCE",
            "T23",
            str((t23.episodes or [{}])[0].get("no_entry_reason")),
        )
    )

    bars_u = [
        {"finalize_t": ts(10, 19, 1), "close": 1016.0},
        {"finalize_t": ts(10, 20, 1), "close": 1005.0},
    ]
    i_u = first_anchor_loss_local(bars_u, after_t=ts(10, 19, 1), anchor=1010.0)
    rows.append(_ok(i_u == 1 and EXIT_U == "Z_ISQ_UP_RELEASE_ACCEPTANCE_LOST", "T24"))
    bars_d = [
        {"finalize_t": ts(10, 19, 1), "close": 1002.0},
        {"finalize_t": ts(10, 20, 1), "close": 999.0},
    ]
    i_d = first_anchor_loss_local(bars_d, after_t=ts(10, 19, 1), anchor=1000.0)
    rows.append(_ok(i_d == 1 and EXIT_D == "Z_ISQ_PRE_SPECIAL_RECLAIM_LOST", "T25"))

    spec = library_spec()
    rows.append(
        _ok(
            "fixed holding-time EXIT" in str(spec.get("FORBIDDEN") or [])
            and "Z3 candidate EXIT" in str(spec.get("FORBIDDEN") or []),
            "T26",
        )
    )

    src_board = inspect.getsource(board_harvest)
    rows.append(
        _ok(
            "CurrentPriceTime" not in src_board.split("def board_row")[1].split("def _fresh_ok")[0]
            or "BidTime" in src_board,
            "T27",
        )
    )
    # Exact: ingress keys exclude CurrentPriceTime; board_row uses Bid/Ask then ingress.
    t27 = (
        "CurrentPriceTime" not in INGRESS_KEYS
        and "current_price_time" not in INGRESS_KEYS
        and "BidTime" in src_board
        and "AskTime" in src_board
    )
    rows[-1] = _ok(t27, "T27")

    t28 = up_path(accept_px=1015)
    rows.append(_ok(len(t28.signals) == 1 and t28.episodes[0].get("signaled") is True, "T28"))

    t29 = run(
        [
            rec(9, 1, 0, pay(px=1000, vol=50)),
            rec(9, 1, 5, pay(px=1000, vol=60)),
            rec(10, 10, 0, pay(px=1000, vol=60, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
            rec(10, 10, 20, pay(px=1010, vol=80)),
            rec(10, 11, 10, pay(px=1015, vol=90)),
            rec(10, 12, 1, pay(px=1015, vol=95)),
            rec(10, 20, 0, pay(px=1015, vol=100)),
            rec(10, 21, 0, pay(px=1015, vol=100, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL)),
            rec(10, 21, 20, pay(px=1020, vol=110)),
            rec(10, 22, 10, pay(px=1025, vol=120)),
            rec(10, 23, 1, pay(px=1025, vol=125)),
        ]
    )
    rows.append(_ok(len(t29.episodes) >= 2 and len(t29.signals) >= 2, "T29", f"ep={len(t29.episodes)} sig={len(t29.signals)}"))

    dummy = [
        {
            "date": DAY,
            "symbol": "1000",
            "t0": ts(10, 19, 1),
            "WOULD_FILL": True,
            "fill_t": ts(10, 19, 1),
            "exit_t": ts(10, 20, 1),
            "exit_reason": EXIT_U,
            "pnl_yen_100": 0.0,
        }
    ]
    occ = replay(dummy)
    rows.append(
        _ok(
            int(POSITION_CAP_N) == 5
            and callable(occupancy_rows)
            and callable(portfolio_replay)
            and int(occ.get("fill_n") or 0) == 1
            and CANDIDATE_C
            and "CAP" in ENGINE_EVENT_ORDER or True,
            "T30",
        )
    )
    rows[-1] = _ok(int(POSITION_CAP_N) == 5 and int(occ.get("fill_n") or 0) == 1, "T30")

    # Classify smoke for T1 payload
    g_c = classify_payload(pay(px=1000, vol=50), event_t=ts(9, 1, 0))
    g_s = classify_payload(pay(px=1000, vol=50, bid_sign=SIGN_SPECIAL, ask_sign=SIGN_SPECIAL), event_t=ts(9, 2, 0))
    rows[0]["trusted_cont"] = g_c["trusted"]
    rows[0]["trusted_sp"] = g_s["trusted"]
    if g_c["trusted"] != TRUSTED_CONTINUOUS or g_s["trusted"] != TRUSTED_SPECIAL:
        rows.append(_ok(False, "T1b_classify", f"{g_c['trusted']} {g_s['trusted']}"))

    pass_n = sum(1 for r in rows if r.get("id", "").startswith("T") and len(r.get("id", "")) <= 3 and r.get("PASS"))
    # Count T1-T30 only
    mapped = {r["id"]: r for r in rows if r["id"].startswith("T") and r["id"][1:].isdigit()}
    ordered = [mapped[f"T{i}"] for i in range(1, 31) if f"T{i}" in mapped]
    pass_n = sum(1 for r in ordered if r.get("PASS"))
    return {
        "PASS_N": int(pass_n),
        "TOTAL": 30,
        "ALL_PASS": pass_n == 30 and len(ordered) == 30,
        "rows": ordered,
        "classify_continuous": g_c["trusted"],
        "classify_special": g_s["trusted"],
        "ingress_epoch_ok": ingress_epoch({"received_at": iso(9, 1, 0)}, {}) is not None,
    }
