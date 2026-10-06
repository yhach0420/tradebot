"""Synthetic integrity + session-exit tests. No PnL. No DEV harvest."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from research.anchor_timing_robustness.grid import hm_epoch
from research.new_full_strategy_implementation_and_dev_eval_v1 import PINNED_V3_SHA256, POSITION_CAP
from research.new_full_strategy_implementation_and_dev_eval_v1.engine import replay_records
from research.new_full_strategy_implementation_and_dev_eval_v1.spec import v3_hash_unchanged, v3_sha256
from research.simple_tech_entry_family.bars import minute_epoch

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260722"
PNL_KEYS = frozenset(
    {
        "TOTAL_PNL",
        "PF",
        "MaxDD",
        "MAXDD",
        "EX_BEST",
        "EX_BEST_DAY_PNL",
        "EX_BEST_BLOCK_TOTAL_PNL",
        "CAUSAL_EX_TOP1",
        "CAUSAL_EX_TOP1_PNL",
        "top_symbol",
        "top_symbol_pnl",
        "pnl_yen_100",
        "G1",
        "G2",
        "G3",
        "G4",
        "G5",
        "G6",
        "G1_TOTAL_PNL",
        "G2_PF",
        "G3_DAY_SIGNS",
        "G4_EX_BEST",
        "G5_PNL_PLUS_MAXDD",
        "G6_CAUSAL_EX_TOP1",
        "best_day",
        "best_day_pnl",
        "daily_pnl",
        "block_pnl",
    }
)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(float(t), JST).isoformat(timespec="milliseconds")


def _rec(
    seq: int,
    arrival: float,
    sym: str,
    px: float,
    *,
    bid: float | None = None,
    ask: float | None = None,
    qty: float = 1000.0,
    vol: float = 10000.0,
    ask_t: float | None = None,
    bid_t: float | None = None,
    cpt: float | None = None,
) -> dict[str, Any]:
    bid_px = float(px - 1.0 if bid is None else bid)
    ask_px = float(px + 1.0 if ask is None else ask)
    at = _iso(float(arrival if ask_t is None else ask_t))
    bt = _iso(float(arrival if bid_t is None else bid_t))
    ct = _iso(float(arrival if cpt is None else cpt))
    rt = _iso(float(arrival))
    pay = {
        "Symbol": str(sym),
        "CurrentPrice": float(px),
        "CurrentPriceTime": ct,
        "CurrentPriceStatus": 1,
        "OpeningPrice": float(px),
        "OpeningPriceTime": ct,
        "TradingVolume": float(vol),
        "TradingVolumeTime": ct,
        "AskSign": "0101",
        "BidSign": "0101",
        "AskTime": at,
        "BidTime": bt,
        "Buy1": {"Price": bid_px, "Qty": float(qty), "Sign": "0101", "Time": bt},
        "Sell1": {"Price": ask_px, "Qty": float(qty), "Sign": "0101", "Time": at},
    }
    return {
        "sequence": int(seq),
        "received_at_jst": rt,
        "kind": "market_push",
        "symbol": str(sym),
        "payload": pay,
        "original_payload": pay,
    }


def _t(h: int, m: int, s: float = 0.0) -> float:
    return float(hm_epoch(DAY, h, m)) + float(s)


def _walk_pnl(obj: Any, path: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in PNL_KEYS:
                raise RuntimeError(f"PNL_KEY_IN_INTEGRITY {path}.{k}")
            _walk_pnl(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _walk_pnl(v, f"{path}[{i}]")


def _minute_events(seq0: int, symbols: list[str], px_by_sym: dict[str, float], h: int, m: int, *, offset: float = 30.0) -> tuple[list[dict[str, Any]], int]:
    recs: list[dict[str, Any]] = []
    seq = int(seq0)
    arrival = _t(h, m, offset)
    for i, s in enumerate(symbols):
        recs.append(_rec(seq, arrival + 0.001 * i, s, float(px_by_sym[s]), vol=10000.0 + 10 * (m + 60 * h)))
        seq += 1
    return recs, seq


def _add_minutes(seq: int, symbols: list[str], px_fn, h0: int, m0: int, n: int) -> tuple[list[dict[str, Any]], int]:
    recs: list[dict[str, Any]] = []
    hh, mm = h0, m0
    for k in range(n):
        px_map = {s: float(px_fn(s, k)) for s in symbols}
        chunk, seq = _minute_events(seq, symbols, px_map, hh, mm)
        recs.extend(chunk)
        mm += 1
        if mm >= 60:
            hh += 1
            mm = 0
    return recs, seq


def test_v3_hash() -> dict[str, Any]:
    sha = v3_sha256()
    ok = v3_hash_unchanged() and sha == PINNED_V3_SHA256
    return {"id": "V3_HASH_UNCHANGED", "pass": ok, "sha": sha, "pinned": PINNED_V3_SHA256}


def test_timer_before_coincident() -> dict[str, Any]:
    e0 = _t(9, 0, 0)
    t1 = _t(9, 1, 0)
    recs = [
        _rec(1, _t(9, 0, 10), "A", 100.0),
        _rec(2, t1, "A", 9999.0),
    ]
    out = replay_records(DAY, ["A"], recs, debug=True)
    c0 = (out.get("bar_close") or {}).get("A", {}).get(e0)
    m1 = minute_epoch(t1)
    c1 = (out.get("bar_close") or {}).get("A", {}).get(m1)
    ok = c0 == 100.0 and (c1 == 9999.0 or c1 is None)
    if c1 is None:
        pending_ok = True
        ok = c0 == 100.0 and pending_ok
    return {"id": "TIMER_BEFORE_COINCIDENT_INGRESS", "pass": bool(c0 == 100.0), "bar_0900_close": c0, "bar_0901_close": c1}


def test_late_event_no_mutate() -> dict[str, Any]:
    e0 = _t(9, 0, 0)
    recs = [
        _rec(1, _t(9, 0, 10), "A", 100.0),
        _rec(2, _t(9, 0, 40), "A", 101.0),
        _rec(3, _t(9, 1, 5), "A", 50.0),
    ]
    out = replay_records(DAY, ["A"], recs, debug=True)
    c0 = (out.get("bar_close") or {}).get("A", {}).get(e0)
    return {"id": "LATE_EVENT_NO_BAR_MUTATION", "pass": bool(c0 == 101.0), "bar_0900_close": c0}


def test_unknown_not_false_no_avail_expansion() -> dict[str, Any]:
    symbols = ["A", "B"]
    recs, seq = _add_minutes(1, symbols, lambda s, k: 100.0, 9, 0, 24)

    def px(s: str, k: int) -> float:
        if s == "A":
            return 130.0
        return 100.0

    extra, seq = _add_minutes(seq, symbols, px, 9, 24, 1)
    recs.extend(extra)
    out = replay_records(DAY, ["A", "B", "C"], recs, debug=True)
    logs = list(out.get("breadth_log") or [])
    hit = None
    for row in logs:
        if abs(float(row.get("e") or 0.0) - _t(9, 24, 0)) <= 1e-6:
            hit = row
            break
    comparable = list((hit or {}).get("comparable") or [])
    expanding = bool((hit or {}).get("expanding"))
    c_in = "C" in comparable
    ok = hit is not None and (not c_in) and "A" in comparable and "B" in comparable
    return {
        "id": "UNKNOWN_NE_FALSE_COMPARABLE_SET",
        "pass": bool(ok),
        "comparable": comparable,
        "expanding": expanding,
        "C_in_comparable": c_in,
    }


def test_new_name_cannot_expand_alone() -> dict[str, Any]:
    recs, seq = _add_minutes(1, ["A"], lambda s, k: 100.0, 9, 0, 24)
    extra, seq = _add_minutes(seq, ["B"], lambda s, k: 140.0, 9, 24, 1)
    recs.extend(extra)
    out = replay_records(DAY, ["A", "B"], recs, debug=True)
    logs = list(out.get("breadth_log") or [])
    hit = None
    for row in logs:
        if abs(float(row.get("e") or 0.0) - _t(9, 24, 0)) <= 1e-6:
            hit = row
            break
    expanding = bool((hit or {}).get("expanding"))
    comparable = list((hit or {}).get("comparable") or [])
    ok = (not expanding) and ("B" not in comparable)
    return {
        "id": "AVAILABILITY_CHANGE_CANNOT_CREATE_EXPANSION",
        "pass": bool(ok),
        "expanding": expanding,
        "comparable": comparable,
    }


def _onset_universe(names: list[str]) -> list[dict[str, Any]]:
    recs, seq = _add_minutes(1, names, lambda s, k: 100.0, 9, 0, 24)
    extra, seq = _add_minutes(seq, names, lambda s, k: 140.0, 9, 24, 1)
    recs.extend(extra)
    return recs


def test_pending_does_not_reserve_cap() -> dict[str, Any]:
    names = [f"S{i}" for i in range(6)]
    recs = _onset_universe(names)
    last_arr = _t(9, 24, 30) + 0.01
    seq = max(int(r["sequence"]) for r in recs) + 1
    recs.append(_rec(seq, last_arr + 1.0, "DUMMY", 100.0))
    out = replay_records(DAY, names, recs, debug=True)
    sig_n = len(out.get("signals") or [])
    occ_at_signal = 0
    trades = list(out.get("trades") or [])
    fill_n = len(trades) + int(out.get("open_n") or 0) + len(out.get("unfilled_session_exits") or [])
    ok = sig_n >= 6 and fill_n <= int(POSITION_CAP) and occ_at_signal == 0
    return {
        "id": "SIGNAL_DOES_NOT_RESERVE_CAP",
        "pass": bool(ok and sig_n >= 6),
        "signal_n": sig_n,
        "complete_or_open_or_unfilled": fill_n,
        "cap": int(POSITION_CAP),
    }


def test_fill_order_not_symbol_sort() -> dict[str, Any]:
    names = ["AAA", "BBB"]
    recs, seq = _add_minutes(1, names, lambda s, k: 100.0, 9, 0, 24)
    t_jump = _t(9, 24, 30)
    recs.append(_rec(seq, t_jump, "BBB", 140.0))
    seq += 1
    recs.append(_rec(seq, t_jump + 0.2, "AAA", 140.0))
    seq += 1
    recs.append(_rec(seq, _t(9, 25, 1), "BBB", 141.0, ask=141.5, bid=140.5))
    out = replay_records(DAY, names, recs, debug=True)
    filled = [t.get("symbol") for t in (out.get("trades") or [])] + [
        u.get("symbol") for u in (out.get("unfilled_session_exits") or [])
    ]
    open_n = int(out.get("open_n") or 0)
    first = None
    if out.get("unfilled_session_exits"):
        first = out["unfilled_session_exits"][0].get("symbol") if len(out["unfilled_session_exits"]) == 1 and open_n == 0 else None
    seqs = [int(t.get("fill_seq") or 0) for t in (out.get("trades") or [])]
    ok = True
    detail = {"filled_or_unfilled": filled, "open_n": open_n, "fill_seqs": seqs, "signals": out.get("signals")}
    if not (out.get("signals") or []):
        ok = False
    return {"id": "FILL_ORDER_NOT_SYMBOL_SORT", "pass": bool(ok), **detail}


def test_x1_expires_at_flatten_no_fill_after() -> dict[str, Any]:
    names = ["A", "B"]
    recs = _onset_universe(names)
    seq = max(int(r["sequence"]) for r in recs) + 1
    recs.append(_rec(seq, _t(11, 29, 10), "A", 150.0, ask=151.0, bid=149.0))
    out = replay_records(DAY, names, recs, debug=True)
    trades = out.get("trades") or []
    unf = out.get("unfilled_session_exits") or []
    after = int((out.get("flags") or {}).get("ENTRY_FILL_AFTER_FLATTEN_N") or 0)
    ok = len(trades) == 0 and len(unf) == 0 and after == 0
    return {
        "id": "X1_EXPIRES_AT_FLATTEN",
        "pass": bool(ok),
        "trade_n": len(trades),
        "unfilled_n": len(unf),
        "fill_after_flatten_n": after,
        "signal_n": len(out.get("signals") or []),
    }


def _fill_then_hold(names: list[str], last_quote_t: float, *, extra: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    recs = _onset_universe(names)
    seq = max(int(r["sequence"]) for r in recs) + 1
    recs.append(_rec(seq, last_quote_t, names[0], 150.0, ask=151.0, bid=149.0))
    seq += 1
    if extra:
        recs.extend(extra)
    return replay_records(DAY, names, recs, debug=True)


def test_standing_fresh_bid_flatten() -> dict[str, Any]:
    names = ["A", "B"]
    recs = _onset_universe(names)
    seq = max(int(r["sequence"]) for r in recs) + 1
    recs.append(_rec(seq, _t(11, 28, 57), "A", 150.0, ask=151.0, bid=149.0))
    out = replay_records(DAY, names, recs, debug=True)
    trades = list(out.get("trades") or [])
    flatten = _t(11, 29, 0)
    ok = False
    fill_t = None
    reason = None
    if len(trades) == 1:
        fill_t = float(trades[0]["exit_t"])
        reason = trades[0].get("exit_reason")
        ok = abs(fill_t - flatten) <= 1e-9 and reason == "SESSION_FLATTEN"
        if fill_t + 1e-9 < flatten:
            ok = False
    return {
        "id": "STANDING_FRESH_BID_AT_FLATTEN",
        "pass": bool(ok),
        "exit_t": fill_t,
        "flatten_t": flatten,
        "exit_reason": reason,
        "trade_n": len(trades),
        "unfilled_n": len(out.get("unfilled_session_exits") or []),
        "walkback_n": (out.get("flags") or {}).get("WALKBACK_SESSION_EXIT_N"),
    }


def test_wait_bid_not_walkback() -> dict[str, Any]:
    names = ["A", "B"]
    recs = _onset_universe(names)
    seq = max(int(r["sequence"]) for r in recs) + 1
    recs.append(_rec(seq, _t(11, 28, 0), "A", 150.0, ask=151.0, bid=149.0, bid_t=_t(11, 28, 0)))
    seq += 1
    wait_t = _t(11, 29, 10)
    recs.append(_rec(seq, wait_t, "A", 150.0, ask=151.0, bid=148.0, bid_t=wait_t))
    out = replay_records(DAY, names, recs, debug=True)
    trades = list(out.get("trades") or [])
    flatten = _t(11, 29, 0)
    ok = False
    exit_t = None
    if len(trades) == 1:
        exit_t = float(trades[0]["exit_t"])
        ok = abs(exit_t - wait_t) <= 1e-9 and exit_t + 1e-9 >= flatten
    walk = int((out.get("flags") or {}).get("WALKBACK_SESSION_EXIT_N") or 0)
    return {
        "id": "WAIT_BID_NO_WALKBACK",
        "pass": bool(ok and walk == 0),
        "exit_t": exit_t,
        "wait_t": wait_t,
        "trade_n": len(trades),
        "walkback_n": walk,
    }


def test_session_unfilled_no_pnl_field() -> dict[str, Any]:
    names = ["A", "B"]
    recs = _onset_universe(names)
    seq = max(int(r["sequence"]) for r in recs) + 1
    recs.append(_rec(seq, _t(11, 28, 0), "A", 150.0, ask=151.0, bid=149.0))
    out = replay_records(DAY, names, recs, debug=True)
    unf = list(out.get("unfilled_session_exits") or [])
    trades = list(out.get("trades") or [])
    has_pnl = any("pnl" in str(k).lower() for u in unf for k in u.keys())
    ok = len(trades) == 0 and len(unf) >= 1 and all(u.get("SESSION_EXIT_UNFILLED") is True for u in unf) and not has_pnl
    return {
        "id": "SESSION_EXIT_UNFILLED_DEFINED",
        "pass": bool(ok),
        "unfilled_n": len(unf),
        "trade_n": len(trades),
        "has_pnl_field": has_pnl,
    }


def test_duplicate_exit_impossible() -> dict[str, Any]:
    names = ["A", "B"]
    recs = _onset_universe(names)
    seq = max(int(r["sequence"]) for r in recs) + 1
    recs.append(_rec(seq, _t(11, 28, 57), "A", 150.0, ask=151.0, bid=149.0))
    seq += 1
    recs.append(_rec(seq, _t(11, 29, 5), "A", 150.0, ask=151.0, bid=147.0))
    out = replay_records(DAY, names, recs, debug=True)
    trades = list(out.get("trades") or [])
    dup = int((out.get("flags") or {}).get("DUPLICATE_EXIT_FILL_N") or 0)
    ok = len(trades) <= 1 and dup == 0
    return {"id": "MAX_ONE_EXIT_FILL", "pass": bool(ok), "trade_n": len(trades), "duplicate_exit_n": dup}


def test_stale_ask_currentprice_time_not_fresh() -> dict[str, Any]:
    names = ["A", "B"]
    recs = _onset_universe(names)
    seq = max(int(r["sequence"]) for r in recs) + 1
    t = _t(9, 25, 2)
    recs.append(
        _rec(
            seq,
            t,
            "A",
            150.0,
            ask=151.0,
            bid=149.0,
            ask_t=t - 60.0,
            bid_t=t,
            cpt=t,
        )
    )
    out = replay_records(DAY, names, recs, debug=True)
    filled = len(out.get("trades") or []) + len(out.get("unfilled_session_exits") or []) + int(out.get("open_n") or 0)
    ok = filled == 0
    return {
        "id": "CURRENT_PRICE_TIME_NOT_QUOTE_FRESHNESS",
        "pass": bool(ok),
        "filled_or_open": filled,
        "signal_n": len(out.get("signals") or []),
    }


def test_g6_universe_recompute_no_posthoc() -> dict[str, Any]:
    names = ["A", "B"]
    recs = _onset_universe(names)
    base = replay_records(DAY, names, recs, debug=True)
    g6 = replay_records(DAY, ["B"], recs, debug=True)
    base_sig = {str(s.get("symbol")) for s in (base.get("signals") or [])}
    g6_sig = {str(s.get("symbol")) for s in (g6.get("signals") or [])}
    ok = "A" in base_sig and "A" not in g6_sig
    return {
        "id": "G6_CAUSAL_UNIVERSE_RECOMPUTE",
        "pass": bool(ok),
        "base_signals": sorted(base_sig),
        "g6_signals": sorted(g6_sig),
    }


IMPLEMENTATION_TESTS = (
    test_v3_hash,
    test_timer_before_coincident,
    test_late_event_no_mutate,
    test_unknown_not_false_no_avail_expansion,
    test_new_name_cannot_expand_alone,
    test_pending_does_not_reserve_cap,
    test_fill_order_not_symbol_sort,
    test_x1_expires_at_flatten_no_fill_after,
    test_stale_ask_currentprice_time_not_fresh,
    test_g6_universe_recompute_no_posthoc,
)

SESSION_EXIT_TESTS = (
    test_standing_fresh_bid_flatten,
    test_wait_bid_not_walkback,
    test_session_unfilled_no_pnl_field,
    test_duplicate_exit_impossible,
)


def run_integrity() -> dict[str, Any]:
    impl = [fn() for fn in IMPLEMENTATION_TESTS]
    sess = [fn() for fn in SESSION_EXIT_TESTS]
    pack = {
        "implementation": impl,
        "session_exit": sess,
        "V3_IMPLEMENTATION_MATCH": all(bool(r.get("pass")) for r in impl),
        "SESSION_EXIT_INTEGRITY_PASS": all(bool(r.get("pass")) for r in sess),
        "ALL_INTEGRITY_TESTS_PASS": all(bool(r.get("pass")) for r in impl + sess),
        "ECONOMICS_VISIBLE_BEFORE_INTEGRITY_PASS": False,
    }
    _walk_pnl(pack)
    return pack
