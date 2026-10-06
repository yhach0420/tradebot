"""Classify opening-input capability. No thresholds. No PnL. No strategy."""
from __future__ import annotations

from typing import Any

from research.opening_logic_input_capability_audit_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    DEVELOPMENT_DAYS,
    NEXT_AB,
    NEXT_C,
)

READY = "READY"
PARTIAL = "PARTIAL"
NOT_AVAILABLE = "NOT_AVAILABLE"

CORE_PREOPEN_INTERVALS = (
    "08:50:00-08:54:59",
    "08:55:00-08:57:59",
    "08:58:00-08:58:59",
    "08:59:00-08:59:29",
    "08:59:30-08:59:49",
    "08:59:50-08:59:59",
)


def _field(rows: list[dict[str, Any]], fid: str) -> dict[str, Any]:
    for r in rows:
        if r.get("FIELD_ID") == fid:
            return r
    return {}


def _bool_n(days: list[dict[str, Any]], pred) -> int:
    return sum(1 for r in days if pred(r))


def time_coverage(days: list[dict[str, Any]]) -> dict[str, Any]:
    pre_days = [r for r in days if int(r.get("preopen_n") or 0) > 0]
    by_interval_events = {k: 0 for k in (days[0].get("EVENT_N_BY_INTERVAL") or {})} if days else {}
    by_interval_sym_max = {k: 0 for k in by_interval_events}
    earliest = []
    for r in days:
        earliest.append({"date": r.get("date"), "earliest": r.get("earliest_ingress_iso")})
        ev = dict(r.get("EVENT_N_BY_INTERVAL") or {})
        sm = dict(r.get("SYMBOL_N_BY_INTERVAL") or {})
        for k, v in ev.items():
            by_interval_events[k] = int(by_interval_events.get(k) or 0) + int(v or 0)
            by_interval_sym_max[k] = max(int(by_interval_sym_max.get(k) or 0), int(sm.get(k) or 0))
    def _approaches_open(r: dict[str, Any]) -> bool:
        ev = dict(r.get("EVENT_N_BY_INTERVAL") or {})
        early = int(ev.get("08:50:00-08:54:59") or 0) + int(ev.get("08:55:00-08:57:59") or 0)
        late = (
            int(ev.get("08:58:00-08:58:59") or 0)
            + int(ev.get("08:59:00-08:59:29") or 0)
            + int(ev.get("08:59:30-08:59:49") or 0)
            + int(ev.get("08:59:50-08:59:59") or 0)
        )
        return int(r.get("preopen_n") or 0) > 0 and early > 0 and late > 0

    core_ok = len(pre_days) == len(days) and all(_approaches_open(r) for r in days)
    seq_ok = all(bool(r.get("sequence_monotonic")) and int(r.get("ingress_n") or 0) > 0 for r in days)
    return {
        "PREOPEN_CAPTURE_EXISTS": len(pre_days) > 0,
        "PREOPEN_CAPTURE_DAY_N": len(pre_days),
        "EARLIEST_CAPTURE_TIME_BY_DAY": earliest,
        "EVENT_N_BY_INTERVAL": by_interval_events,
        "SYMBOL_N_BY_INTERVAL_MAX": by_interval_sym_max,
        "EVENT_N_BY_INTERVAL_BY_DAY": [
            {"date": r.get("date"), **dict(r.get("EVENT_N_BY_INTERVAL") or {})} for r in days
        ],
        "SYMBOL_N_BY_INTERVAL_BY_DAY": [
            {"date": r.get("date"), **dict(r.get("SYMBOL_N_BY_INTERVAL") or {})} for r in days
        ],
        "PREOPEN_EVENT_SEQUENCE_RECONSTRUCTABLE": bool(core_ok and seq_ok),
        "WINDOW_08_45_FULLY_POPULATED": all(
            int((r.get("EVENT_N_BY_INTERVAL") or {}).get("08:45:00-08:49:59") or 0) > 0 for r in days
        ),
        "DAYS_WITH_08_45_TO_OPEN_STREAM": [
            r.get("date")
            for r in days
            if int((r.get("EVENT_N_BY_INTERVAL") or {}).get("08:50:00-08:54:59") or 0) > 0
            and (
                int((r.get("EVENT_N_BY_INTERVAL") or {}).get("08:58:00-08:58:59") or 0)
                + int((r.get("EVENT_N_BY_INTERVAL") or {}).get("08:59:00-08:59:29") or 0)
                + int((r.get("EVENT_N_BY_INTERVAL") or {}).get("08:59:30-08:59:49") or 0)
                + int((r.get("EVENT_N_BY_INTERVAL") or {}).get("08:59:50-08:59:59") or 0)
            )
            > 0
        ],
        "DAYS_WITH_PREOPEN_HOLE_BEFORE_OPEN": [
            r.get("date")
            for r in days
            if int(r.get("preopen_n") or 0) > 0
            and int((r.get("EVENT_N_BY_INTERVAL") or {}).get("08:50:00-08:54:59") or 0) == 0
            and (
                int((r.get("EVENT_N_BY_INTERVAL") or {}).get("08:58:00-08:58:59") or 0)
                + int((r.get("EVENT_N_BY_INTERVAL") or {}).get("08:59:50-08:59:59") or 0)
            )
            == 0
        ],
        "ORDERED_INGRESS_AND_SEQUENCE": bool(seq_ok),
    }


def timestamp_semantics(days: list[dict[str, Any]], fields: list[dict[str, Any]]) -> dict[str, Any]:
    bid_n = sum(int(r.get("bidtime_n") or 0) for r in days)
    ask_n = sum(int(r.get("asktime_n") or 0) for r in days)
    ing_fb = sum(int(r.get("board_fresh_ingress_fallback_n") or 0) for r in days)
    board_n = sum(int(r.get("board_fresh_bid_or_ask_n") or 0) for r in days)
    cpt = _field(fields, "CurrentPriceTime")
    return {
        "BidTime": {
            "CLASS": "EXCHANGE_SOURCE_TIME",
            "USABLE": bid_n > 0,
            "PREOPEN_POPULATED_DAY_N": _field(fields, "BidTime").get("PREOPEN_POPULATED_DAY_N"),
        },
        "AskTime": {
            "CLASS": "EXCHANGE_SOURCE_TIME",
            "USABLE": ask_n > 0,
            "PREOPEN_POPULATED_DAY_N": _field(fields, "AskTime").get("PREOPEN_POPULATED_DAY_N"),
        },
        "INGRESS": {
            "CLASS": "INGRESS_RECEIVE_TIME",
            "USABLE": all(int(r.get("ingress_n") or 0) > 0 for r in days),
            "KEYS": ["received_at", "received_at_jst", "persisted_at", "received_at_utc", "event_time"],
        },
        "Buy1.Time / Sell1.Time": {"CLASS": "FIELD_UPDATE_TIME", "USABLE": True},
        "CurrentPriceTime": {
            "CLASS": "FIELD_UPDATE_TIME",
            "USABLE_AS_BOARD_FRESHNESS": False,
            "FORBIDDEN_AS_BID_ASK_FRESHNESS": True,
            "PREOPEN_POPULATED_DAY_N": cpt.get("PREOPEN_POPULATED_DAY_N"),
        },
        "BOARD_FRESHNESS_CAUSALLY_RESOLVABLE": bool(bid_n > 0 and ask_n > 0) or bool(ing_fb > 0 or board_n > 0),
        "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N": 0,
        "fallback_events_preopen": ing_fb,
    }


def auction_indicative(days: list[dict[str, Any]], fields: list[dict[str, Any]]) -> dict[str, Any]:
    calc = _field(fields, "CalcPrice")
    ind = _field(fields, "IndicativePrice")
    exp = _field(fields, "ExpectedOpen")
    direct = bool(ind.get("FIELD_EXISTS") or exp.get("FIELD_EXISTS"))
    return {
        "DIRECT_INDICATIVE_OPEN_AVAILABLE": direct,
        "DIRECT_FIELD": None if not direct else "IndicativePrice",
        "CalcPrice_EXISTS": bool(calc.get("FIELD_EXISTS")),
        "CalcPrice_PREOPEN_POPULATED_DAY_N": calc.get("PREOPEN_POPULATED_DAY_N"),
        "CalcPrice_SEMANTICS": "kabu BoardSuccess 計算値; not proven JPX itayose indicative match price",
        "DERIVED_INDICATIVE_OPEN_POSSIBLE": False,
        "DERIVED_REASON": (
            "10-level book + MarketOrderBuyQty/SellQty + OverSellQty/UnderBuyQty is truncated. "
            "Uncaptured orders remain. No auction matching algorithm is assumed."
        ),
        "ITAYOSE_SIGN_FIELDS": ["BidSign", "AskSign", "Buy1.Sign", "Sell1.Sign"],
        "SIGN_0117_IS_NOT_A_PRICE_FIELD": True,
    }


def symbol_trajectory(days: list[dict[str, Any]], cov: dict[str, Any]) -> dict[str, Any]:
    traj_days = _bool_n(days, lambda r: int(r.get("trajectory_symbol_n") or 0) >= 8)
    components = {
        "Bid/Ask": _bool_n(days, lambda r: int(r.get("preopen_bidask_symbol_n") or 0) >= 8),
        "10-level depth": _bool_n(days, lambda r: int(r.get("preopen_depth10_symbol_n") or 0) >= 8),
        "OVER/UNDER": _bool_n(days, lambda r: int(r.get("preopen_overunder_symbol_n") or 0) >= 8),
        "auction MO qty": _bool_n(days, lambda r: int(r.get("preopen_mo_symbol_n") or 0) >= 8),
        "ingress event sequence": bool(cov.get("PREOPEN_EVENT_SEQUENCE_RECONSTRUCTABLE")),
        "direct indicative price": False,
    }
    ok = bool(cov.get("PREOPEN_EVENT_SEQUENCE_RECONSTRUCTABLE")) and traj_days >= 8
    return {
        "SYMBOL_PREOPEN_TRAJECTORY_RECONSTRUCTABLE": ok,
        "TRAJECTORY_DAY_N_WITH_GE8_SYMBOLS": traj_days,
        "COMPONENTS": components,
        "NOTE": (
            "08:45-08:59 itayose window is captured on 2/10 LEGACY_DEV days only "
            "(20260722, 20260728). Other days have an early-morning burst then a Capture hole until 09:00."
        ),
    }


def actual_open(days: list[dict[str, Any]]) -> dict[str, Any]:
    px_any = all(int(r.get("opening_price_populated_symbol_n") or 0) > 0 for r in days) and len(days) == 10
    tm_any = all(int(r.get("opening_price_time_populated_symbol_n") or 0) > 0 for r in days) and len(days) == 10
    tr_any = all(int(r.get("first_post_open_currentprice_symbol_n") or 0) > 0 for r in days) and len(days) == 10
    bar_any = all(int(r.get("first_1m_input_symbol_n") or 0) > 0 for r in days) and len(days) == 10
    px8 = _bool_n(days, lambda r: int(r.get("opening_price_populated_symbol_n") or 0) >= 8)
    bar8 = _bool_n(days, lambda r: int(r.get("first_1m_input_symbol_n") or 0) >= 8)
    causal = bool(px_any and tm_any)
    return {
        "ACTUAL_OPEN_PRICE_SOURCE": "OpeningPrice on board after 09:00",
        "ACTUAL_OPEN_TIME_SOURCE": "OpeningPriceTime; compared to ingress receive time",
        "FIRST_POST_OPEN_TRADE_SOURCE": "CurrentPrice first populated at/after 09:00 (print, not board freshness)",
        "FIRST_COMPLETED_1M_BAR_SOURCE": (
            "SymbolBarBuilder: 09:00 minute finalizes on first continuous event of 09:01. "
            "This audit only checks that post-open CurrentPrice exists in 09:00 and a later event exists at/after 09:01. "
            "OHLC is not computed."
        ),
        "ACTUAL_OPEN_PRICE_CAUSAL": bool(px_any),
        "ACTUAL_OPEN_TIME_CAUSAL": bool(tm_any),
        "FIRST_POST_OPEN_TRADE_CAUSAL": bool(tr_any),
        "FIRST_COMPLETED_1M_BAR_CAUSAL": bool(bar_any),
        "ACTUAL_OPEN_CAUSALLY_AVAILABLE": bool(causal),
        "EXPECTATION_VS_OPEN_WITHOUT_LOOKAHEAD": bool(causal),
        "DAY_N_OPENINGPRICE_GE8_BY_09_02": px8,
        "DAY_N_FIRST_1M_INPUTS_GE8_BY_09_02": bar8,
        "NOTE": "Availability is source/timestamp, not a 09:02 completeness quota. Scan truncated at 09:02.",
    }


def prior_session(days: list[dict[str, Any]], fields: list[dict[str, Any]]) -> dict[str, Any]:
    pc = _field(fields, "PreviousClose")
    pc_ok = int(pc.get("PREOPEN_POPULATED_DAY_N") or 0) >= 8
    return {
        "PRIOR_CLOSE": {
            "AVAILABLE": pc_ok,
            "SOURCE": "PreviousClose / PreviousCloseTime on current preopen board",
            "TIMESTAMP": "PreviousCloseTime; visible before 09:00",
        },
        "PRIOR_OPEN": {
            "AVAILABLE": False,
            "SOURCE": "no PreviousOpen field; current OpeningPrice is this session",
        },
        "PRIOR_HIGH": {
            "AVAILABLE": False,
            "SOURCE": "no PreviousHigh; HighPrice is current session running high",
        },
        "PRIOR_LOW": {
            "AVAILABLE": False,
            "SOURCE": "no PreviousLow; LowPrice is current session running low",
        },
        "PRIOR_RANGE": {"AVAILABLE": False, "SOURCE": "requires prior high and low"},
        "PRIOR_SESSION_VWAP": {
            "AVAILABLE": False,
            "SOURCE": (
                "Exchange VWAP is current session. Research session VWAP is from completed AM 1m bars "
                "of a prior captured day, not a preopen as-of field on the current board."
            ),
        },
    }


def futures_index(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fut_n = sum(int(r.get("registered_futures_like_n") or 0) for r in days)
    etf_n = sum(int(r.get("registered_etf_n") or 0) for r in days)
    sec = sorted({s for r in days for s in (r.get("security_types") or [])})
    rows = [
        {
            "SOURCE_ID": "NIKKEI_225_FUTURES",
            "EXISTS": False,
            "HISTORICAL_EXISTS_ON_ALLOWED_DAYS": False,
            "LIVE_RUNTIME_SOURCE_EXISTS": False,
            "PREOPEN_EXISTS": False,
            "TIMESTAMP_CAUSAL": False,
            "HISTORICAL_LIVE_SAME_SEMANTICS": False,
            "NOTE": "Not in Capture schema or registered_symbols on LEGACY_DEV days. kabu register uses TSE cash equities (exchange=1).",
        },
        {
            "SOURCE_ID": "TOPIX_FUTURES",
            "EXISTS": False,
            "HISTORICAL_EXISTS_ON_ALLOWED_DAYS": False,
            "LIVE_RUNTIME_SOURCE_EXISTS": False,
            "PREOPEN_EXISTS": False,
            "TIMESTAMP_CAUSAL": False,
            "HISTORICAL_LIVE_SAME_SEMANTICS": False,
            "NOTE": "Not captured.",
        },
        {
            "SOURCE_ID": "OSE_INDEX_FUTURES",
            "EXISTS": False,
            "HISTORICAL_EXISTS_ON_ALLOWED_DAYS": False,
            "LIVE_RUNTIME_SOURCE_EXISTS": False,
            "PREOPEN_EXISTS": False,
            "TIMESTAMP_CAUSAL": False,
            "HISTORICAL_LIVE_SAME_SEMANTICS": False,
            "NOTE": "Not captured. FLEX MBO/OSE is a closed external-source lineage, not used here.",
        },
        {
            "SOURCE_ID": "NIKKEI_225_INDEX",
            "EXISTS": False,
            "HISTORICAL_EXISTS_ON_ALLOWED_DAYS": False,
            "LIVE_RUNTIME_SOURCE_EXISTS": False,
            "PREOPEN_EXISTS": False,
            "TIMESTAMP_CAUSAL": False,
            "HISTORICAL_LIVE_SAME_SEMANTICS": False,
            "NOTE": "No cash index feed in Capture.",
        },
        {
            "SOURCE_ID": "TOPIX_INDEX",
            "EXISTS": False,
            "HISTORICAL_EXISTS_ON_ALLOWED_DAYS": False,
            "LIVE_RUNTIME_SOURCE_EXISTS": False,
            "PREOPEN_EXISTS": False,
            "TIMESTAMP_CAUSAL": False,
            "HISTORICAL_LIVE_SAME_SEMANTICS": False,
            "NOTE": "No cash index feed in Capture.",
        },
        {
            "SOURCE_ID": "ETF_PROXY",
            "EXISTS": etf_n > 0,
            "HISTORICAL_EXISTS_ON_ALLOWED_DAYS": etf_n > 0,
            "LIVE_RUNTIME_SOURCE_EXISTS": etf_n > 0,
            "PREOPEN_EXISTS": etf_n > 0,
            "TIMESTAMP_CAUSAL": etf_n > 0,
            "HISTORICAL_LIVE_SAME_SEMANTICS": etf_n > 0,
            "NOTE": (
                f"KNOWN_ETF_CODES may appear in the cash universe. registered_etf_event_days_sum={etf_n}. "
                "An ETF print is not a futures/index. SecurityTypes observed={sec}."
            ),
        },
        {
            "SOURCE_ID": "OTHER_CAPTURED_MARKET_INDEX",
            "EXISTS": False,
            "HISTORICAL_EXISTS_ON_ALLOWED_DAYS": bool(fut_n > 0),
            "LIVE_RUNTIME_SOURCE_EXISTS": False,
            "PREOPEN_EXISTS": False,
            "TIMESTAMP_CAUSAL": False,
            "HISTORICAL_LIVE_SAME_SEMANTICS": False,
            "NOTE": f"futures-like registered count sum={fut_n}; security_types={sec}",
        },
    ]
    return rows


def cross_sectional(days: list[dict[str, Any]], cov: dict[str, Any], fields: list[dict[str, Any]]) -> dict[str, Any]:
    xs_days = _bool_n(days, lambda r: int(r.get("xs_late_preopen_symbol_n") or 0) >= 8)
    pc = int(_field(fields, "PreviousClose").get("PREOPEN_POPULATED_DAY_N") or 0)
    bid = int(_field(fields, "BidPrice").get("PREOPEN_POPULATED_DAY_N") or 0)
    feasible = (
        bool(cov.get("PREOPEN_EVENT_SEQUENCE_RECONSTRUCTABLE"))
        and xs_days >= 8
        and pc >= 8
        and bid >= 8
    )
    return {
        "CROSS_SECTIONAL_PREOPEN_PRESSURE_FEASIBLE": bool(feasible),
        "DAY_N_WITH_GE8_LATE_PREOPEN_SYMBOLS": xs_days,
        "INPUT_CLASSES_ONLY": [
            "fraction of symbols with preopen expected-price proxy vs prior close",
            "cross-sectional board pressure (depth / OVER-UNDER / MO qty)",
            "cross-sectional CalcPrice displacement vs prior close",
        ],
        "NO_THRESHOLD": True,
        "NOT_EQUIVALENT_TO_FUTURES": True,
    }


def gap_capability(
    *,
    traj: dict[str, Any],
    actual: dict[str, Any],
    xs: dict[str, Any],
    fut: list[dict[str, Any]],
    auction: dict[str, Any],
) -> dict[str, Any]:
    direct_fut = any(r["SOURCE_ID"] == "NIKKEI_225_FUTURES" and r["EXISTS"] for r in fut) or any(
        r["SOURCE_ID"] == "TOPIX_FUTURES" and r["EXISTS"] for r in fut
    )
    direct_idx = any(r["SOURCE_ID"] in ("NIKKEI_225_INDEX", "TOPIX_INDEX") and r["EXISTS"] for r in fut)
    symbol_gap = PARTIAL if traj.get("SYMBOL_PREOPEN_TRAJECTORY_RECONSTRUCTABLE") and not auction.get("DIRECT_INDICATIVE_OPEN_AVAILABLE") else (
        READY if auction.get("DIRECT_INDICATIVE_OPEN_AVAILABLE") else NOT_AVAILABLE
    )
    if not traj.get("SYMBOL_PREOPEN_TRAJECTORY_RECONSTRUCTABLE"):
        symbol_gap = NOT_AVAILABLE
    market_gap = NOT_AVAILABLE
    if direct_fut:
        market_gap = READY
    elif direct_idx:
        market_gap = READY
    elif xs.get("CROSS_SECTIONAL_PREOPEN_PRESSURE_FEASIBLE"):
        market_gap = PARTIAL
    rel = NOT_AVAILABLE
    if symbol_gap == READY and market_gap == READY:
        rel = READY
    elif symbol_gap != NOT_AVAILABLE and market_gap != NOT_AVAILABLE:
        rel = PARTIAL
    return {
        "SYMBOL_EXPECTED_OPEN": symbol_gap,
        "SYMBOL_EXPECTED_GAP_STATUS": symbol_gap,
        "MARKET_EXPECTED_GAP_STATUS": market_gap,
        "RELATIVE_DISLOCATION_STATUS": rel,
        "DIRECT_FUTURES_PRESSURE": direct_fut,
        "DIRECT_INDEX_PRESSURE": direct_idx,
        "EQUITY_CROSS_SECTIONAL_PREOPEN_PRESSURE": bool(xs.get("CROSS_SECTIONAL_PREOPEN_PRESSURE_FEASIBLE")),
        "NO_MARKET_CONTEXT": not (direct_fut or direct_idx or xs.get("CROSS_SECTIONAL_PREOPEN_PRESSURE_FEASIBLE")),
    }


def _obj(oid: str, required: list[str], available: list[str], missing: list[str], status: str, note: str) -> dict[str, Any]:
    return {
        "OBJECT_ID": oid,
        "REQUIRED_INPUTS": required,
        "AVAILABLE_INPUTS": available,
        "MISSING_INPUTS": missing,
        "CAUSAL": status != NOT_AVAILABLE,
        "REPLAYABLE": status != NOT_AVAILABLE,
        "LIVE_OBSERVABLE": status != NOT_AVAILABLE,
        "STATUS": status,
        "NOTE": note,
    }


def opening_objects(gap: dict[str, Any], actual: dict[str, Any], prior: dict[str, Any], auction: dict[str, Any]) -> list[dict[str, Any]]:
    o1_status = READY if gap["SYMBOL_EXPECTED_GAP_STATUS"] != NOT_AVAILABLE else PARTIAL
    o1_miss = (
        []
        if gap["SYMBOL_EXPECTED_GAP_STATUS"] != NOT_AVAILABLE
        else ["08:45-08:59 itayose trajectory into open (present on 2/10 days only)"]
    )
    o2_miss = []
    if not gap["DIRECT_FUTURES_PRESSURE"] and not gap["DIRECT_INDEX_PRESSURE"]:
        o2_miss.append("direct futures/index")
    if not gap["EQUITY_CROSS_SECTIONAL_PREOPEN_PRESSURE"]:
        o2_miss.append("equity cross-section")
    o2_status = PARTIAL if gap["EQUITY_CROSS_SECTIONAL_PREOPEN_PRESSURE"] and not gap["DIRECT_FUTURES_PRESSURE"] else (
        READY if gap["DIRECT_FUTURES_PRESSURE"] or gap["DIRECT_INDEX_PRESSURE"] else NOT_AVAILABLE
    )
    o3_status = gap["RELATIVE_DISLOCATION_STATUS"]
    o4_status = READY if actual.get("ACTUAL_OPEN_CAUSALLY_AVAILABLE") and prior["PRIOR_CLOSE"]["AVAILABLE"] else (
        PARTIAL if actual.get("ACTUAL_OPEN_CAUSALLY_AVAILABLE") or prior["PRIOR_CLOSE"]["AVAILABLE"] else NOT_AVAILABLE
    )
    o5_status = PARTIAL if gap["SYMBOL_EXPECTED_GAP_STATUS"] != NOT_AVAILABLE and actual.get("ACTUAL_OPEN_CAUSALLY_AVAILABLE") else NOT_AVAILABLE
    if auction.get("DIRECT_INDICATIVE_OPEN_AVAILABLE") and actual.get("ACTUAL_OPEN_CAUSALLY_AVAILABLE"):
        o5_status = READY
    path_ok = bool(actual.get("FIRST_POST_OPEN_TRADE_CAUSAL") or actual.get("FIRST_COMPLETED_1M_BAR_CAUSAL"))
    o68 = PARTIAL if actual.get("ACTUAL_OPEN_CAUSALLY_AVAILABLE") and path_ok else NOT_AVAILABLE
    return [
        _obj(
            "O1_SYMBOL_OPEN_PRESSURE",
            ["preopen Bid/Ask or depth or OVER/UNDER or MO qty", "ingress order"],
            ["Bid/Ask", "Buy1-10/Sell1-10", "OverSellQty/UnderBuyQty", "MarketOrderBuyQty/SellQty"],
            o1_miss,
            o1_status,
            "Board/auction qty exist in captured preopen. Itayose-window trajectory into 09:00 is not supported on 8/10 days.",
        ),
        _obj(
            "O2_MARKET_OPEN_PRESSURE",
            ["futures or index or equity cross-section"],
            ["equity cross-section"] if gap["EQUITY_CROSS_SECTIONAL_PREOPEN_PRESSURE"] else [],
            o2_miss,
            o2_status,
            "Not a futures substitute. Class C only if equity XS is feasible.",
        ),
        _obj(
            "O3_RELATIVE_DISLOCATION",
            ["symbol expectation", "market expectation"],
            ["symbol board trajectory", "equity XS"] if o3_status != NOT_AVAILABLE else [],
            (["direct indicative"] if not auction.get("DIRECT_INDICATIVE_OPEN_AVAILABLE") else [])
            + (["direct futures/index"] if not gap["DIRECT_FUTURES_PRESSURE"] and not gap["DIRECT_INDEX_PRESSURE"] else []),
            o3_status,
            "No numerical formula in this run.",
        ),
        _obj(
            "O4_ACTUAL_GAP_STATE",
            ["OpeningPrice", "PreviousClose"],
            ["OpeningPrice after 09:00", "PreviousClose before 09:00"] if o4_status != NOT_AVAILABLE else [],
            [] if o4_status == READY else ["OpeningPrice or PreviousClose"],
            o4_status,
            "Gap value is not computed.",
        ),
        _obj(
            "O5_EXPECTATION_ERROR",
            ["preopen expectation", "actual open"],
            ["board/CalcPrice proxy", "OpeningPrice"] if o5_status != NOT_AVAILABLE else [],
            ["direct indicative open"] if not auction.get("DIRECT_INDICATIVE_OPEN_AVAILABLE") else [],
            o5_status,
            "Error vs official itayose is not available; proxy vs OpeningPrice is PARTIAL.",
        ),
        _obj(
            "O6_OPENING_CONTINUATION",
            ["actual open", "post-open path"],
            ["OpeningPrice", "CurrentPrice after 09:00", "first 1m bar inputs"] if o68 != NOT_AVAILABLE else [],
            [] if path_ok else ["post-open path"],
            o68,
            "Object not defined. Raw path inputs exist. No persistence length.",
        ),
        _obj(
            "O7_OPENING_EXHAUSTION",
            ["actual open", "post-open path"],
            ["OpeningPrice", "CurrentPrice after 09:00"] if o68 != NOT_AVAILABLE else [],
            [] if path_ok else ["post-open path"],
            o68,
            "No overbought threshold.",
        ),
        _obj(
            "O8_OPENING_REVERSAL",
            ["actual open", "post-open path"],
            ["OpeningPrice", "CurrentPrice after 09:00"] if o68 != NOT_AVAILABLE else [],
            [] if path_ok else ["post-open path"],
            o68,
            "No reversal rule.",
        ),
    ]


def confirmed_and_missing(gap: dict[str, Any], fields: list[dict[str, Any]], actual: dict[str, Any], xs: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def pack(iid: str, **kw: Any) -> dict[str, Any]:
        return {"INPUT_ID": iid, "CAUSAL": True, "REPLAYABLE": True, "LIVE_OBSERVABLE": True, **kw}

    confirmed = [
        pack("INGRESS_EVENT_SEQUENCE", NOTE="received_at / event_time / sequence on Capture JSONL"),
        pack("BID_ASK_PREOPEN", NOTE="BidPrice/AskPrice and Buy1/Sell1"),
        pack("DEPTH_10_PREOPEN", NOTE="Buy2-10 / Sell2-10 Price+Qty"),
        pack("OVER_SELL_QTY", NOTE="OverSellQty"),
        pack("UNDER_BUY_QTY", NOTE="UnderBuyQty"),
        pack("MARKET_ORDER_BUY_QTY", NOTE="MarketOrderBuyQty"),
        pack("MARKET_ORDER_SELL_QTY", NOTE="MarketOrderSellQty"),
        pack("BID_TIME", NOTE="exchange quote clock"),
        pack("ASK_TIME", NOTE="exchange quote clock"),
        pack("INGRESS_RECEIVE_TIME", NOTE="board freshness fallback; never CurrentPriceTime"),
        pack("BID_SIGN_ASK_SIGN", NOTE="preopen/itayose/special quote phase"),
        pack("PREVIOUS_CLOSE", NOTE="PreviousClose/PreviousCloseTime before 09:00"),
        pack("OPENING_PRICE", NOTE="after 09:00"),
        pack("OPENING_PRICE_TIME", NOTE="after 09:00"),
        pack("CURRENT_PRICE_POST_OPEN", NOTE="first post-open print"),
        pack("FIRST_COMPLETED_1M_BAR_INPUTS", NOTE="09:00 prints + 09:01+ event; OHLC not computed here"),
        pack("CALC_PRICE", NOTE="vendor 計算値; not confirmed as official indicative open"),
    ]
    if xs.get("CROSS_SECTIONAL_PREOPEN_PRESSURE_FEASIBLE"):
        confirmed.append(pack("EQUITY_CROSS_SECTIONAL_PREOPEN_BOARD", NOTE="multi-symbol preopen tape; no threshold"))
    missing = [
        {"INPUT_ID": "DIRECT_INDICATIVE_OPEN", "CAUSAL": False, "REPLAYABLE": False, "LIVE_OBSERVABLE": False, "NOTE": "no dedicated field"},
        {"INPUT_ID": "NIKKEI_225_FUTURES", "CAUSAL": False, "REPLAYABLE": False, "LIVE_OBSERVABLE": False, "NOTE": "not captured"},
        {"INPUT_ID": "TOPIX_FUTURES", "CAUSAL": False, "REPLAYABLE": False, "LIVE_OBSERVABLE": False, "NOTE": "not captured"},
        {"INPUT_ID": "NIKKEI_225_INDEX", "CAUSAL": False, "REPLAYABLE": False, "LIVE_OBSERVABLE": False, "NOTE": "not captured"},
        {"INPUT_ID": "TOPIX_INDEX", "CAUSAL": False, "REPLAYABLE": False, "LIVE_OBSERVABLE": False, "NOTE": "not captured"},
        {"INPUT_ID": "PREVIOUS_OPEN", "CAUSAL": False, "REPLAYABLE": False, "LIVE_OBSERVABLE": False, "NOTE": "no field at current preopen"},
        {"INPUT_ID": "PREVIOUS_HIGH", "CAUSAL": False, "REPLAYABLE": False, "LIVE_OBSERVABLE": False, "NOTE": "no field at current preopen"},
        {"INPUT_ID": "PREVIOUS_LOW", "CAUSAL": False, "REPLAYABLE": False, "LIVE_OBSERVABLE": False, "NOTE": "no field at current preopen"},
        {"INPUT_ID": "PRIOR_SESSION_VWAP_ASOF_PREOPEN", "CAUSAL": False, "REPLAYABLE": False, "LIVE_OBSERVABLE": False, "NOTE": "not on current board"},
        {"INPUT_ID": "EXPLICIT_TRADING_STATUS_PHASE", "CAUSAL": False, "REPLAYABLE": False, "LIVE_OBSERVABLE": False, "NOTE": "inferred from signs"},
        {"INPUT_ID": "FULL_AUCTION_ORDER_BOOK", "CAUSAL": False, "REPLAYABLE": False, "LIVE_OBSERVABLE": False, "NOTE": "uncaptured depth/orders"},
        {"INPUT_ID": "FLEX_MBO", "CAUSAL": False, "REPLAYABLE": False, "LIVE_OBSERVABLE": False, "NOTE": "MBO_WORK_THIS_RUN=false"},
    ]
    if not actual.get("ACTUAL_OPEN_CAUSALLY_AVAILABLE"):
        missing.append({"INPUT_ID": "ACTUAL_OPEN", "CAUSAL": False, "REPLAYABLE": False, "LIVE_OBSERVABLE": False, "NOTE": "OpeningPrice not broadly populated"})
    _ = fields
    return confirmed, missing


def decide(*, traj_ok: bool, actual_ok: bool, market_ok: bool) -> dict[str, Any]:
    if not traj_ok or not actual_ok:
        reason = (
            "Preopen symbol trajectory cannot be supported causally. "
            "Change architecture. Do not run another generic input audit."
            if not traj_ok
            else (
                "Preopen tape exists but actual open is not causally available, so "
                "PREOPEN EXPECTATION → ACTUAL OPEN cannot close. Change architecture. "
                "Do not run another generic input audit."
            )
        )
        return {
            "CASE": "C",
            "VERDICT": CASE_C,
            "NEXT": NEXT_C,
            "INTERPRETATION": reason,
        }
    if actual_ok and market_ok:
        return {
            "CASE": "A",
            "VERDICT": CASE_A,
            "NEXT": NEXT_AB,
            "INTERPRETATION": (
                "Preopen symbol trajectory is supported, at least one market-context class exists, "
                "and actual open is causal. Proceed to OPENING_LOGIC_REQUIREMENTS_FREEZE_V1. "
                "Do not acquire MBO/futures in that freeze."
            ),
        }
    return {
        "CASE": "B",
        "VERDICT": CASE_B,
        "NEXT": NEXT_AB,
        "INTERPRETATION": (
            "Preopen symbol trajectory and actual open are causal, but no usable market-context class exists. "
            "Still freeze requirements. Mark MARKET_OPEN_PRESSURE NOT_AVAILABLE. Do not acquire MBO/futures."
        ),
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    a = dict(report.get("time_coverage") or {})
    frows = list(report.get("preopen_fields") or [])
    ts = dict(report.get("timestamp_semantics") or {})
    auc = dict(report.get("auction_indicative") or {})
    tr = dict(report.get("symbol_trajectory") or {})
    ao = dict(report.get("actual_open") or {})
    pr = dict(report.get("prior_session") or {})
    gap = dict(report.get("gap_capability") or {})
    objs = {r["OBJECT_ID"]: r for r in list(report.get("opening_objects") or [])}
    d = dict(report.get("decision") or {})
    confirmed = [r["INPUT_ID"] for r in list(report.get("confirmed_input_set") or [])]
    missing = [r["INPUT_ID"] for r in list(report.get("missing_inputs") or [])]
    bid = _field(frows, "BidPrice")
    d10 = _field(frows, "Buy10")
    over = _field(frows, "OVER")
    imb = _field(frows, "MarketOrderBuyQty")

    def st(oid: str) -> Any:
        return (objs.get(oid) or {}).get("STATUS")

    return {
        "1_objective_aligned": True,
        "2_exact_allowed_raw_dates": list(DEVELOPMENT_DAYS),
        "3_raw_market_dates_opened": list((report.get("data_boundary") or {}).get("RAW_MARKET_DATES_OPENED") or []),
        "4_Holdout_read": False,
        "5_Stress_read": False,
        "6_future_read": False,
        "7_preopen_Capture_exists": a.get("PREOPEN_CAPTURE_EXISTS"),
        "8_preopen_Capture_day_N": a.get("PREOPEN_CAPTURE_DAY_N"),
        "9_earliest_time_by_day": a.get("EARLIEST_CAPTURE_TIME_BY_DAY"),
        "10_08_45_09_00_event_sequence_reconstructable": a.get("PREOPEN_EVENT_SEQUENCE_RECONSTRUCTABLE"),
        "11_Bid_Ask_preopen_available": int(bid.get("PREOPEN_POPULATED_DAY_N") or 0) >= 8,
        "12_10_level_board_preopen_available": int(d10.get("PREOPEN_POPULATED_DAY_N") or 0) >= 8,
        "13_OVER_UNDER_preopen_available": int(over.get("PREOPEN_POPULATED_DAY_N") or 0) >= 8,
        "14_auction_imbalance_field_available": int(imb.get("PREOPEN_POPULATED_DAY_N") or 0) >= 8,
        "15_direct_indicative_open_available": auc.get("DIRECT_INDICATIVE_OPEN_AVAILABLE"),
        "16_derived_indicative_open_possible": auc.get("DERIVED_INDICATIVE_OPEN_POSSIBLE"),
        "17_BidTime_usable": bool((ts.get("BidTime") or {}).get("USABLE")),
        "18_AskTime_usable": bool((ts.get("AskTime") or {}).get("USABLE")),
        "19_ingress_timestamp_fallback_usable": bool((ts.get("INGRESS") or {}).get("USABLE")),
        "20_board_freshness_causal": ts.get("BOARD_FRESHNESS_CAUSALLY_RESOLVABLE"),
        "21_symbol_preopen_trajectory_reconstructable": tr.get("SYMBOL_PREOPEN_TRAJECTORY_RECONSTRUCTABLE"),
        "22_actual_open_price_causal": ao.get("ACTUAL_OPEN_PRICE_CAUSAL"),
        "23_actual_open_time_causal": ao.get("ACTUAL_OPEN_TIME_CAUSAL"),
        "24_first_completed_1m_bar_causal": ao.get("FIRST_COMPLETED_1M_BAR_CAUSAL"),
        "25_prior_close_available": bool((pr.get("PRIOR_CLOSE") or {}).get("AVAILABLE")),
        "26_prior_high_available": bool((pr.get("PRIOR_HIGH") or {}).get("AVAILABLE")),
        "27_prior_low_available": bool((pr.get("PRIOR_LOW") or {}).get("AVAILABLE")),
        "28_Nikkei_futures_exists": bool(gap.get("DIRECT_FUTURES_PRESSURE")),
        "29_TOPIX_futures_exists": False,
        "30_direct_index_exists": bool(gap.get("DIRECT_INDEX_PRESSURE")),
        "31_cross_sectional_preopen_pressure_feasible": bool(gap.get("EQUITY_CROSS_SECTIONAL_PREOPEN_PRESSURE")),
        "32_DIRECT_FUTURES_PRESSURE_status": READY if gap.get("DIRECT_FUTURES_PRESSURE") else NOT_AVAILABLE,
        "33_DIRECT_INDEX_PRESSURE_status": READY if gap.get("DIRECT_INDEX_PRESSURE") else NOT_AVAILABLE,
        "34_EQUITY_CROSS_SECTIONAL_PREOPEN_PRESSURE_status": READY if gap.get("EQUITY_CROSS_SECTIONAL_PREOPEN_PRESSURE") else NOT_AVAILABLE,
        "35_SYMBOL_EXPECTED_GAP_status": gap.get("SYMBOL_EXPECTED_GAP_STATUS"),
        "36_MARKET_EXPECTED_GAP_status": gap.get("MARKET_EXPECTED_GAP_STATUS"),
        "37_RELATIVE_DISLOCATION_status": gap.get("RELATIVE_DISLOCATION_STATUS"),
        "38_O1_status": st("O1_SYMBOL_OPEN_PRESSURE"),
        "39_O2_status": st("O2_MARKET_OPEN_PRESSURE"),
        "40_O3_status": st("O3_RELATIVE_DISLOCATION"),
        "41_O4_status": st("O4_ACTUAL_GAP_STATE"),
        "42_O5_status": st("O5_EXPECTATION_ERROR"),
        "43_O6_status": st("O6_OPENING_CONTINUATION"),
        "44_O7_status": st("O7_OPENING_EXHAUSTION"),
        "45_O8_status": st("O8_OPENING_REVERSAL"),
        "46_confirmed_opening_input_IDs": confirmed,
        "47_missing_desired_input_IDs": missing,
        "48_strategy_created": False,
        "49_threshold_selected": False,
        "50_PnL_computed": False,
        "51_MBO_work": False,
        "52_external_data_acquired": False,
        "53_Sizing": False,
        "54_Runtime_changed": False,
        "55_Capture_changed": False,
        "56_submit_cancel_live": "0/0/0",
        "57_TRUE_OOS": False,
        "58_CERTIFIED": False,
        "59_VERDICT": d.get("VERDICT"),
        "60_NEXT": d.get("NEXT"),
    }


def assemble(scan: dict[str, Any]) -> dict[str, Any]:
    days = list(scan.get("days") or [])
    fields = list(scan.get("field_rows") or [])
    cov = time_coverage(days)
    ts = timestamp_semantics(days, fields)
    auc = auction_indicative(days, fields)
    tr = symbol_trajectory(days, cov)
    ao = actual_open(days)
    pr = prior_session(days, fields)
    fut = futures_index(days)
    xs = cross_sectional(days, cov, fields)
    gap = gap_capability(traj=tr, actual=ao, xs=xs, fut=fut, auction=auc)
    objs = opening_objects(gap, ao, pr, auc)
    confirmed, missing = confirmed_and_missing(gap, fields, ao, xs)
    market_ok = bool(gap.get("DIRECT_FUTURES_PRESSURE") or gap.get("DIRECT_INDEX_PRESSURE") or gap.get("EQUITY_CROSS_SECTIONAL_PREOPEN_PRESSURE"))
    decision = decide(
        traj_ok=bool(tr.get("SYMBOL_PREOPEN_TRAJECTORY_RECONSTRUCTABLE")),
        actual_ok=bool(ao.get("ACTUAL_OPEN_CAUSALLY_AVAILABLE")),
        market_ok=market_ok,
    )
    return {
        "time_coverage": cov,
        "preopen_fields": fields,
        "timestamp_semantics": ts,
        "auction_indicative": auc,
        "symbol_trajectory": tr,
        "actual_open": ao,
        "prior_session": pr,
        "futures_index": fut,
        "cross_sectional_context": xs,
        "gap_capability": gap,
        "opening_objects": objs,
        "confirmed_input_set": confirmed,
        "missing_inputs": missing,
        "decision": decision,
    }
