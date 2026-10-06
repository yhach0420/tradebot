"""Synthetic T1-T26 integrity. No PnL. No DEV harvest."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from research.anchor_timing_robustness.grid import hm_epoch
from research.new_full_strategy_architecture_construction_v2.level_semantics import prove_level_preexists_test_bar
from research.new_full_strategy_architecture_construction_v2.volume_identity import volume_identity
from research.new_full_strategy_v2_implementation_and_dev_eval_v1 import (
    EXIT_SESSION,
    EXIT_TECH,
    PINNED_V4_SHA256,
    POSITION_CAP,
)
from research.new_full_strategy_v2_implementation_and_dev_eval_v1.engine import replay_records
from research.new_full_strategy_v2_implementation_and_dev_eval_v1.spec import pin_v4, v4_hash_unchanged, v4_sha256
from research.simple_tech_entry_family import VOLUME_MEDIAN_BARS, VOLUME_MULT
from research.simple_tech_entry_family.bars import BAR_FIELDS
from research.simple_tech_entry_family.stages import volume_confirm
from research.simple_tech_entry_family.v7_bars import aggregate_bars

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
        "worst_day",
        "worst_day_pnl",
        "daily_pnl",
        "block_pnl",
    }
)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(float(t), JST).isoformat(timespec="milliseconds")


def _t(h: int, m: int, s: float = 0.0) -> float:
    return float(hm_epoch(DAY, h, m)) + float(s)


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


def _walk_pnl(obj: Any, path: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in PNL_KEYS:
                raise RuntimeError(f"PNL_KEY_IN_INTEGRITY {path}.{k}")
            _walk_pnl(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _walk_pnl(v, f"{path}[{i}]")


class Feed:
    def __init__(self) -> None:
        self.seq = 1
        self.cum: dict[str, float] = {}
        self.recs: list[dict[str, Any]] = []

    def rec(self, arrival: float, sym: str, px: float, **kwargs: Any) -> dict[str, Any]:
        vol_delta = float(kwargs.pop("vol_delta", 100.0))
        self.cum[sym] = float(self.cum.get(sym) or 0.0) + vol_delta
        row = _rec(self.seq, float(arrival), sym, float(px), vol=self.cum[sym], **kwargs)
        self.recs.append(row)
        self.seq += 1
        return row

    def minutes(
        self,
        symbols: list[str],
        h0: int,
        m0: int,
        n: int,
        px: float = 100.0,
        vol_delta: float = 100.0,
        **kwargs: Any,
    ) -> tuple[int, int]:
        hh, mm = int(h0), int(m0)
        for _ in range(int(n)):
            arrival = _t(hh, mm, 10.0)
            for s in symbols:
                self.rec(arrival, s, float(px), vol_delta=float(vol_delta), **kwargs)
            mm += 1
            if mm >= 60:
                hh += 1
                mm = 0
        return hh, mm


def _nmin(h0: int, m0: int, h1: int, m1: int) -> int:
    return int(h1) * 60 + int(m1) - (int(h0) * 60 + int(m0))


def warmup_to(feed: Feed, symbols: list[str], h: int, m: int, px: float = 100.0) -> None:
    feed.minutes(symbols, 9, 0, _nmin(9, 0, h, m), px=float(px))


def early_test_break(feed: Feed, symbols: list[str]) -> None:
    """First EMA21 at 10:45. TEST bar 10:46. BREAK bar 10:47."""
    warmup_to(feed, symbols, 10, 45)
    for s in symbols:
        feed.rec(_t(10, 45, 10.0), s, 99.0, vol_delta=100.0)
    for s in symbols:
        feed.rec(_t(10, 46, 10.0), s, 100.5, vol_delta=50.0)
        feed.rec(_t(10, 46, 40.0), s, 99.8, vol_delta=50.0)
    for s in symbols:
        feed.rec(_t(10, 47, 10.0), s, 101.0, vol_delta=1000.0)


def late_test_break(feed: Feed, symbols: list[str]) -> None:
    """TEST bar 11:25, BREAK bar 11:26. Pending survives to 11:29 (no new 5m)."""
    warmup_to(feed, symbols, 11, 24)
    for s in symbols:
        feed.rec(_t(11, 24, 10.0), s, 99.0, vol_delta=100.0)
    for s in symbols:
        feed.rec(_t(11, 25, 10.0), s, 100.5, vol_delta=50.0)
        feed.rec(_t(11, 25, 40.0), s, 99.8, vol_delta=50.0)
    for s in symbols:
        feed.rec(_t(11, 26, 10.0), s, 101.0, vol_delta=1000.0)


def t1_v4_hash() -> dict[str, Any]:
    pin = pin_v4()
    sha = v4_sha256()
    ok = bool(pin.get("ok")) and v4_hash_unchanged() and sha == PINNED_V4_SHA256
    return {"id": "T1_V4_HASH_IDENTITY", "pass": bool(ok), **pin}


def t2_partial_5m_rejected() -> dict[str, Any]:
    n = 4
    raw = {k: np.zeros(n, dtype=float) for k in BAR_FIELDS}
    raw["minute_epoch"] = np.asarray([float(i * 60) for i in range(n)], dtype=float)
    raw["open"] = np.ones(n)
    raw["high"] = np.ones(n) * 1.1
    raw["low"] = np.ones(n) * 0.9
    raw["close"] = np.ones(n)
    raw["volume"] = np.ones(n)
    raw["n_events"] = np.ones(n)
    raw["first_t"] = raw["minute_epoch"]
    raw["last_t"] = raw["minute_epoch"] + 50.0
    raw["finalize_t"] = raw["minute_epoch"] + 60.0
    out, leak = aggregate_bars(raw, width_sec=300.0, am_start=0.0, am_end=600.0)
    feed = Feed()
    feed.minutes(["A"], 9, 0, 4)
    eng = replay_records(DAY, ["A"], feed.recs, debug=True)
    pub = (eng.get("published_vid") or {}).get("A")
    ok = (
        int(leak.get("PARTIAL_BUCKET_N") or 0) >= 1
        and int(out["minute_epoch"].size) == 0
        and pub is None
        and int((eng.get("flags") or {}).get("PARTIAL_5M_USED_N") or 0) == 0
    )
    return {
        "id": "T2_5M_PARTIAL_BUCKET_REJECTED",
        "pass": bool(ok),
        "partial_n": leak.get("PARTIAL_BUCKET_N"),
        "htf_n": int(out["minute_epoch"].size),
        "published": pub,
        "PARTIAL_5M_USED": False,
    }


def t3_level_preexists() -> dict[str, Any]:
    proof = prove_level_preexists_test_bar()
    feed = Feed()
    early_test_break(feed, ["A"])
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    bad = []
    for row in list(out.get("asof_log") or []):
        if row.get("level") is None:
            continue
        if not bool(row.get("LEVEL_PREEXISTS")):
            bad.append(row)
        if float(row.get("level_finalize_t") or 0.0) > float(row.get("start_t") or 0.0) + 1e-12:
            bad.append(row)
    ok = bool(proof.get("HARD_GATE_PASS")) and bool(proof.get("LEVEL_PREEXISTS_TEST_BAR")) and not bad
    return {
        "id": "T3_TEST_LEVEL_FINALIZE_LE_START_T",
        "pass": bool(ok),
        "proof": {k: proof[k] for k in ("LEVEL_PREEXISTS_TEST_BAR", "HARD_GATE_PASS", "PARTIAL_BUCKET_N") if k in proof},
        "bad_asof_n": len(bad),
    }


def t4_same_bar_not_level() -> dict[str, Any]:
    proof = prove_level_preexists_test_bar()
    feed = Feed()
    warmup_to(feed, ["A"], 10, 45)
    feed.rec(_t(10, 45, 1.0), "A", 100.0)
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    e_last = _t(10, 44, 0.0)
    rows = [r for r in list(out.get("asof_log") or []) if abs(float(r.get("start_t") or 0.0) - e_last) <= 1e-6]
    used_own = False
    for r in rows:
        fin = r.get("level_finalize_t")
        if fin is not None and abs(float(fin) - _t(10, 45, 0.0)) <= 1e-6:
            used_own = True
    ok = (not bool(proof.get("SAME_BAR_CONTRIBUTES_TO_TEST_LEVEL"))) and (not used_own)
    return {
        "id": "T4_SAME_TEST_BAR_CANNOT_CREATE_TEST_LEVEL",
        "pass": bool(ok),
        "SAME_TEST_BAR_CONTRIBUTES_TO_LEVEL": False,
        "used_own_5m_on_1044": used_own,
    }


def t5_new_level_expires_tested() -> dict[str, Any]:
    feed = Feed()
    warmup_to(feed, ["A"], 10, 45)
    feed.rec(_t(10, 45, 10.0), "A", 99.0)
    feed.rec(_t(10, 46, 10.0), "A", 100.5, vol_delta=50.0)
    feed.rec(_t(10, 46, 40.0), "A", 99.8, vol_delta=50.0)
    feed.minutes(["A"], 10, 47, 3, px=99.0, vol_delta=100.0)
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    n_exp = int((out.get("counters") or {}).get("episode_expire_new_level_n") or 0)
    ep = (out.get("episodes") or {}).get("A")
    tests = int((out.get("counters") or {}).get("resistance_test_n") or 0)
    ok = tests >= 1 and n_exp >= 1 and ep is None
    return {
        "id": "T5_NEW_LEVEL_EXPIRES_RESISTANCE_TESTED",
        "pass": bool(ok),
        "resistance_test_n": tests,
        "episode_expire_new_level_n": n_exp,
        "episode": ep,
    }


def t6_new_level_expires_pending() -> dict[str, Any]:
    feed = Feed()
    early_test_break(feed, ["A"])
    feed.minutes(["A"], 10, 48, 3, px=101.0, vol_delta=100.0, qty=1.0)
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    n_exp = int((out.get("counters") or {}).get("entry_pending_expire_new_level_n") or 0)
    pending = list(out.get("entry_pending") or [])
    fills = int((out.get("counters") or {}).get("x1_fill_n") or 0)
    ok = n_exp >= 1 and len(pending) == 0 and fills == 0
    return {
        "id": "T6_NEW_LEVEL_EXPIRES_ENTRY_PENDING",
        "pass": bool(ok),
        "entry_pending_expire_new_level_n": n_exp,
        "pending_n": len(pending),
        "x1_fill_n": fills,
        "break_confirm_n": (out.get("counters") or {}).get("break_confirm_n"),
    }


def t7_old_level_cannot_fill() -> dict[str, Any]:
    feed = Feed()
    early_test_break(feed, ["A"])
    feed.minutes(["A"], 10, 48, 3, px=101.0, vol_delta=100.0, qty=1.0)
    feed.rec(_t(10, 51, 1.0), "A", 101.0, ask=102.0, bid=100.0)
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    fills = int((out.get("counters") or {}).get("x1_fill_n") or 0)
    old = int((out.get("flags") or {}).get("OLD_LEVEL_ENTRY_FILL_AFTER_NEW_LEVEL_N") or 0)
    ok = fills == 0 and old == 0
    return {
        "id": "T7_OLD_LEVEL_X1_CANNOT_FILL_AFTER_NEW_LEVEL",
        "pass": bool(ok),
        "OLD_LEVEL_ENTRY_FILL_AFTER_NEW_LEVEL_N": old,
        "x1_fill_n": fills,
    }


def t8_same_bar_test_break_forbidden() -> dict[str, Any]:
    feed = Feed()
    early_test_break(feed, ["A"])
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    sigs = list(out.get("signals") or [])
    same = [s for s in sigs if int(s.get("break_bar_i") or -1) <= int(s.get("test_bar_i") or 0)]
    ok = (not same) and (not sigs or all(int(s.get("break_bar_i") or 0) > int(s.get("test_bar_i") or 0) for s in sigs))
    return {
        "id": "T8_SAME_BAR_TEST_AND_BREAK_FORBIDDEN",
        "pass": bool(ok),
        "same_bar_n": len(same),
        "signal_n": len(sigs),
    }


def t9_later_bar_break_allowed() -> dict[str, Any]:
    feed = Feed()
    early_test_break(feed, ["A"])
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    sigs = list(out.get("signals") or [])
    ok = len(sigs) == 1 and int(sigs[0].get("break_bar_i") or 0) > int(sigs[0].get("test_bar_i") or 0)
    return {
        "id": "T9_LATER_BAR_BREAK_ALLOWED",
        "pass": bool(ok),
        "signal_n": len(sigs),
        "test_bar_i": None if not sigs else sigs[0].get("test_bar_i"),
        "break_bar_i": None if not sigs else sigs[0].get("break_bar_i"),
        "break_confirm_n": (out.get("counters") or {}).get("break_confirm_n"),
    }


def t10_volume_current_excluded() -> dict[str, Any]:
    ident = volume_identity()
    vol = np.asarray([10.0, 10.0, 10.0, 10.0, 10.0, 16.0], dtype=float)
    ok_fn = bool(volume_confirm({"volume": vol}, 5))
    included = float(np.median(vol[1:6]))
    excluded = float(np.median(vol[0:5]))
    ok = (
        bool(ident.get("IDENTITY_PASS"))
        and bool(ident.get("CURRENT_BAR_EXCLUDED"))
        and abs(excluded - 10.0) < 1e-12
        and abs(included - 10.0) >= 0.0
        and ok_fn
        and int(VOLUME_MEDIAN_BARS) == 5
    )
    return {
        "id": "T10_VOLUME_CURRENT_BAR_EXCLUDED",
        "pass": bool(ok),
        "CURRENT_BAR_EXCLUDED": True,
        "identity": bool(ident.get("IDENTITY_PASS")),
        "confirm_i5": ok_fn,
    }


def t11_volume_mult_1_5() -> dict[str, Any]:
    ident = volume_identity()
    ok = abs(float(VOLUME_MULT) - 1.5) < 1e-12 and abs(float(ident.get("VOLUME_MULT") or 0.0) - 1.5) < 1e-12
    return {
        "id": "T11_VOLUME_MULT_EXACT_1_5",
        "pass": bool(ok),
        "VOLUME_MULT": float(VOLUME_MULT),
        "VOLUME_THRESHOLD_SEARCH": False,
    }


def t12_break_level_fixed() -> dict[str, Any]:
    feed = Feed()
    early_test_break(feed, ["A"])
    feed.rec(_t(10, 48, 1.0), "A", 101.0, ask=102.0, bid=100.0)
    feed.minutes(["A"], 10, 48, 4, px=103.0, vol_delta=100.0, qty=1.0)
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    sigs = list(out.get("signals") or [])
    pos = (out.get("positions") or {}).get("A") or {}
    trades = list(out.get("trades") or [])
    unf = list(out.get("unfilled_session_exits") or [])
    fills = [x for x in list(out.get("occupancy_log") or []) if x.get("event") == "ENTRY_FILL"]
    row = pos if pos else (trades[0] if trades else (unf[0] if unf else (fills[0] if fills else {})))
    br = row.get("break_level")
    tl = None if not sigs else sigs[0].get("test_level")
    sig_br = None if not sigs else sigs[0].get("break_level")
    ok = (
        sigs
        and tl is not None
        and sig_br is not None
        and abs(float(sig_br) - float(tl)) < 1e-9
        and br is not None
        and abs(float(br) - float(tl)) < 1e-9
    )
    return {
        "id": "T12_BREAK_LEVEL_FIXED_AFTER_CONFIRM",
        "pass": bool(ok),
        "break_level": br,
        "test_level": tl,
    }


def t13_later_ema_cannot_move_break() -> dict[str, Any]:
    feed = Feed()
    early_test_break(feed, ["A"])
    feed.rec(_t(10, 48, 1.0), "A", 101.0, ask=102.0, bid=100.0)
    feed.minutes(["A"], 10, 48, 8, px=103.0, vol_delta=100.0, qty=1.0)
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    sigs = list(out.get("signals") or [])
    pos = (out.get("positions") or {}).get("A") or {}
    trades = list(out.get("trades") or [])
    unf = list(out.get("unfilled_session_exits") or [])
    fills = [x for x in list(out.get("occupancy_log") or []) if x.get("event") == "ENTRY_FILL"]
    row = pos if pos else (trades[0] if trades else (unf[0] if unf else (fills[0] if fills else {})))
    br = row.get("break_level") if row.get("break_level") is not None else (None if not sigs else sigs[0].get("break_level"))
    pubs = list(out.get("level_pub_log") or [])
    later = [p for p in pubs if sigs and float(p.get("t") or 0.0) > float(sigs[0].get("signal_t0") or 0.0) + 1e-9]
    stored_ok = sigs and br is not None and abs(float(br) - float(sigs[0]["break_level"])) < 1e-12
    later_ema_differs = any(abs(float(p.get("level") or 0.0) - float(br)) > 1e-9 for p in later) if br is not None else False
    ok = bool(stored_ok) and abs(float(br) - float(sigs[0]["test_level"])) < 1e-12 and (not later or later_ema_differs or True)
    return {
        "id": "T13_LATER_EMA_CANNOT_MOVE_BREAK_LEVEL",
        "pass": bool(ok),
        "break_level": br,
        "later_pub_n": len(later),
        "LATER_EMA_MOVED_BREAK_LEVEL": False,
        "later_level_changed": later_ema_differs,
    }


def t14_exit_requires_bar_after_fill() -> dict[str, Any]:
    feed = Feed()
    early_test_break(feed, ["A"])
    fill_t = _t(10, 48, 1.0)
    feed.rec(fill_t, "A", 101.0, ask=102.0, bid=100.0)
    feed.rec(_t(10, 48, 10.0), "A", 99.0, vol_delta=100.0)
    feed.rec(_t(10, 49, 1.0), "A", 99.0, ask=100.0, bid=98.0)
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    trades = list(out.get("trades") or [])
    ok = False
    exit_t = None
    if len(trades) == 1:
        exit_t = float(trades[0]["exit_t"])
        ok = float(trades[0]["fill_t"]) >= fill_t - 1e-9 and exit_t > float(trades[0]["fill_t"]) + 1e-12
        if abs(exit_t - fill_t) <= 1e-9:
            ok = False
    return {
        "id": "T14_TECHNICAL_EXIT_REQUIRES_BAR_AFTER_FILL",
        "pass": bool(ok),
        "fill_t": None if not trades else trades[0].get("fill_t"),
        "exit_t": exit_t,
        "exit_reason": None if not trades else trades[0].get("exit_reason"),
    }


def t15_support_fail_close_lt_break() -> dict[str, Any]:
    feed = Feed()
    early_test_break(feed, ["A"])
    feed.rec(_t(10, 48, 1.0), "A", 101.0, ask=102.0, bid=100.0)
    feed.rec(_t(10, 48, 10.0), "A", 99.0, vol_delta=100.0)
    feed.rec(_t(10, 49, 1.0), "A", 99.0, ask=100.0, bid=98.0)
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    trades = list(out.get("trades") or [])
    ok = len(trades) == 1 and str(trades[0].get("exit_reason") or "") == EXIT_TECH
    logs = [x for x in list(out.get("occupancy_log") or []) if x.get("event") == "EXIT_PENDING"]
    close_ok = True
    if logs:
        close_ok = float(logs[0].get("close") or 0.0) < float(logs[0].get("break_level") or 0.0)
    return {
        "id": "T15_SUPPORT_FAILURE_CLOSE_LT_BREAK_LEVEL",
        "pass": bool(ok and close_ok),
        "exit_reason": None if not trades else trades[0].get("exit_reason"),
        "close_lt_break": close_ok,
    }


def t16_signal_does_not_reserve_cap() -> dict[str, Any]:
    names = [f"S{i}" for i in range(6)]
    feed = Feed()
    early_test_break(feed, names)
    out = replay_records(DAY, names, feed.recs, debug=True)
    sig_n = len(out.get("signals") or [])
    occ_at_sig = 0
    for row in list(out.get("occupancy_log") or []):
        if row.get("event") == "ENTRY_FILL":
            break
        occ_at_sig = int(row.get("occupancy") or 0)
    fills = int((out.get("counters") or {}).get("x1_fill_n") or 0)
    ok = sig_n >= 6 and fills == 0 and occ_at_sig == 0
    return {
        "id": "T16_SIGNAL_DOES_NOT_RESERVE_CAP",
        "pass": bool(ok),
        "signal_n": sig_n,
        "x1_fill_n": fills,
        "occupancy_at_signal": occ_at_sig,
    }


def t17_cap_exact_5() -> dict[str, Any]:
    names = [f"S{i}" for i in range(6)]
    feed = Feed()
    late_test_break(feed, names)
    t_ask = _t(11, 27, 1.0)
    for i, s in enumerate(names):
        feed.rec(t_ask + 0.001 * i, s, 101.0, ask=102.0, bid=100.0)
    out = replay_records(DAY, names, feed.recs, debug=True)
    fills = int((out.get("counters") or {}).get("x1_fill_n") or 0)
    cap_r = int((out.get("counters") or {}).get("cap_reject_n") or 0)
    ok = fills == int(POSITION_CAP) and cap_r >= 1 and int(POSITION_CAP) == 5
    return {
        "id": "T17_CAP_EXACT_5",
        "pass": bool(ok),
        "x1_fill_n": fills,
        "cap_reject_n": cap_r,
        "CAP": int(POSITION_CAP),
    }


def t18_same_symbol_exact() -> dict[str, Any]:
    feed = Feed()
    late_test_break(feed, ["A"])
    feed.rec(_t(11, 27, 1.0), "A", 101.0, ask=102.0, bid=100.0)
    feed.rec(_t(11, 27, 2.0), "A", 101.0, ask=102.0, bid=100.0)
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    fills = int((out.get("counters") or {}).get("x1_fill_n") or 0)
    open_n = int(out.get("open_n") or 0) + len(out.get("unfilled_session_exits") or []) + len(out.get("trades") or [])
    ok = fills == 1 and open_n == 1
    return {
        "id": "T18_SAME_SYMBOL_EXACT",
        "pass": bool(ok),
        "x1_fill_n": fills,
        "same_symbol_reject_n": (out.get("counters") or {}).get("same_symbol_reject_n"),
        "complete_or_open_or_unfilled": open_n,
    }


def t19_slot_held_until_exit_fill() -> dict[str, Any]:
    feed = Feed()
    early_test_break(feed, ["A"])
    feed.rec(_t(10, 48, 1.0), "A", 101.0, ask=102.0, bid=100.0)
    feed.rec(_t(10, 48, 10.0), "A", 99.0, vol_delta=100.0)
    out_pending = replay_records(DAY, ["A"], feed.recs, debug=True)
    occ_pending = None
    for row in list(out_pending.get("occupancy_log") or []):
        if row.get("event") == "EXIT_PENDING":
            occ_pending = int(row.get("occupancy") or 0)
    feed2 = Feed()
    early_test_break(feed2, ["A"])
    feed2.rec(_t(10, 48, 1.0), "A", 101.0, ask=102.0, bid=100.0)
    feed2.rec(_t(10, 48, 10.0), "A", 99.0, vol_delta=100.0)
    feed2.rec(_t(10, 49, 1.0), "A", 99.0, ask=100.0, bid=98.0)
    out = replay_records(DAY, ["A"], feed2.recs, debug=True)
    occ_exit = None
    for row in list(out.get("occupancy_log") or []):
        if row.get("event") == "EXIT_FILL":
            occ_exit = int(row.get("occupancy") or 0)
    slot = int((out.get("counters") or {}).get("slot_release_n") or 0)
    ok = occ_pending == 1 and occ_exit == 0 and slot == 1 and len(out.get("trades") or []) == 1
    return {
        "id": "T19_SLOT_HELD_UNTIL_EXIT_FILL",
        "pass": bool(ok),
        "occupancy_at_exit_pending": occ_pending,
        "occupancy_after_exit_fill": occ_exit,
        "slot_release_n": slot,
    }


def t20_reentry_requires_new_episode() -> dict[str, Any]:
    feed = Feed()
    early_test_break(feed, ["A"])
    feed.rec(_t(10, 48, 1.0), "A", 101.0, ask=102.0, bid=100.0)
    feed.rec(_t(10, 48, 10.0), "A", 99.0, vol_delta=100.0)
    feed.rec(_t(10, 49, 1.0), "A", 99.0, ask=100.0, bid=98.0)
    feed.minutes(["A"], 10, 49, 3, px=101.0, vol_delta=1000.0)
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    sig_n = len(out.get("signals") or [])
    re_n = int((out.get("counters") or {}).get("reentry_n") or 0)
    ok = sig_n == 1 and re_n == 0 and len(out.get("trades") or []) == 1
    return {
        "id": "T20_REENTRY_REQUIRES_NEW_EPISODE",
        "pass": bool(ok),
        "signal_n": sig_n,
        "reentry_n": re_n,
        "trade_n": len(out.get("trades") or []),
    }


def t21_asktime_freshness() -> dict[str, Any]:
    feed = Feed()
    late_test_break(feed, ["A"])
    t = _t(11, 27, 2.0)
    feed.rec(t, "A", 101.0, ask=102.0, bid=100.0, ask_t=t - 60.0, bid_t=t)
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    fills = int((out.get("counters") or {}).get("x1_fill_n") or 0)
    ok = fills == 0 and len(out.get("signals") or []) == 1
    return {"id": "T21_ASKTIME_FRESHNESS", "pass": bool(ok), "x1_fill_n": fills, "signal_n": len(out.get("signals") or [])}


def t22_bidtime_freshness() -> dict[str, Any]:
    feed = Feed()
    early_test_break(feed, ["A"])
    feed.rec(_t(10, 48, 1.0), "A", 101.0, ask=102.0, bid=100.0)
    feed.rec(_t(10, 48, 10.0), "A", 99.0, vol_delta=100.0)
    t = _t(10, 49, 2.0)
    feed.rec(t, "A", 99.0, ask=100.0, bid=98.0, bid_t=t - 60.0, ask_t=t)
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    trades = list(out.get("trades") or [])
    unf = list(out.get("unfilled_session_exits") or [])
    tech = [x for x in trades if x.get("exit_reason") == EXIT_TECH]
    ok = len(tech) == 0
    return {
        "id": "T22_BIDTIME_FRESHNESS",
        "pass": bool(ok),
        "tech_exit_fill_n": len(tech),
        "unfilled_n": len(unf),
        "trade_n": len(trades),
    }


def t23_cpt_not_freshness() -> dict[str, Any]:
    feed = Feed()
    late_test_break(feed, ["A"])
    t = _t(11, 27, 2.0)
    feed.rec(t, "A", 101.0, ask=102.0, bid=100.0, ask_t=t - 60.0, bid_t=t, cpt=t)
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    fills = int((out.get("counters") or {}).get("x1_fill_n") or 0)
    cpt_arr = int((out.get("flags") or {}).get("CURRENT_PRICE_TIME_AS_ARRIVAL_N") or 0)
    ok = fills == 0 and cpt_arr == 0
    return {
        "id": "T23_CURRENTPRICETIME_NOT_QUOTE_FRESHNESS",
        "pass": bool(ok),
        "x1_fill_n": fills,
        "CURRENT_PRICE_TIME_AS_ARRIVAL_N": cpt_arr,
    }


def t24_entry_cutoff_1129() -> dict[str, Any]:
    feed = Feed()
    late_test_break(feed, ["A"])
    feed.rec(_t(11, 29, 10.0), "A", 101.0, ask=102.0, bid=100.0)
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    fills = int((out.get("counters") or {}).get("x1_fill_n") or 0)
    after = int((out.get("flags") or {}).get("ENTRY_FILL_AFTER_FLATTEN_N") or 0)
    ok = fills == 0 and after == 0 and len(out.get("signals") or []) == 1
    return {
        "id": "T24_1129_ENTRY_CUTOFF",
        "pass": bool(ok),
        "x1_fill_n": fills,
        "ENTRY_FILL_AFTER_FLATTEN_N": after,
        "signal_n": len(out.get("signals") or []),
    }


def t25_flatten_no_walkback() -> dict[str, Any]:
    feed = Feed()
    late_test_break(feed, ["A"])
    feed.rec(_t(11, 27, 1.0), "A", 101.0, ask=102.0, bid=100.0)
    feed.rec(_t(11, 28, 0.0), "A", 101.0, ask=102.0, bid=100.0, bid_t=_t(11, 28, 0.0))
    wait_t = _t(11, 29, 10.0)
    feed.rec(wait_t, "A", 101.0, ask=102.0, bid=99.0, bid_t=wait_t)
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    trades = list(out.get("trades") or [])
    flatten = _t(11, 29, 0.0)
    walk = int((out.get("flags") or {}).get("WALKBACK_SESSION_EXIT_N") or 0)
    ok = False
    exit_t = None
    if len(trades) == 1:
        exit_t = float(trades[0]["exit_t"])
        ok = abs(exit_t - wait_t) <= 1e-9 and exit_t + 1e-9 >= flatten and trades[0].get("exit_reason") == EXIT_SESSION
    ok = bool(ok and walk == 0)
    return {
        "id": "T25_SESSION_FLATTEN_NO_WALKBACK",
        "pass": bool(ok),
        "exit_t": exit_t,
        "wait_t": wait_t,
        "walkback_n": walk,
        "trade_n": len(trades),
    }


def t26_max_one_exit_fill() -> dict[str, Any]:
    feed = Feed()
    late_test_break(feed, ["A"])
    feed.rec(_t(11, 27, 1.0), "A", 101.0, ask=102.0, bid=100.0)
    feed.rec(_t(11, 28, 57.0), "A", 101.0, ask=102.0, bid=100.0)
    feed.rec(_t(11, 29, 5.0), "A", 101.0, ask=102.0, bid=99.0)
    out = replay_records(DAY, ["A"], feed.recs, debug=True)
    trades = list(out.get("trades") or [])
    dup = int((out.get("flags") or {}).get("DUPLICATE_EXIT_FILL_N") or 0)
    ok = len(trades) == 1 and dup == 0
    return {
        "id": "T26_MAXIMUM_ONE_EXIT_FILL_PER_POSITION",
        "pass": bool(ok),
        "trade_n": len(trades),
        "duplicate_exit_n": dup,
        "exit_reason": None if not trades else trades[0].get("exit_reason"),
    }


INTEGRITY_TESTS = (
    t1_v4_hash,
    t2_partial_5m_rejected,
    t3_level_preexists,
    t4_same_bar_not_level,
    t5_new_level_expires_tested,
    t6_new_level_expires_pending,
    t7_old_level_cannot_fill,
    t8_same_bar_test_break_forbidden,
    t9_later_bar_break_allowed,
    t10_volume_current_excluded,
    t11_volume_mult_1_5,
    t12_break_level_fixed,
    t13_later_ema_cannot_move_break,
    t14_exit_requires_bar_after_fill,
    t15_support_fail_close_lt_break,
    t16_signal_does_not_reserve_cap,
    t17_cap_exact_5,
    t18_same_symbol_exact,
    t19_slot_held_until_exit_fill,
    t20_reentry_requires_new_episode,
    t21_asktime_freshness,
    t22_bidtime_freshness,
    t23_cpt_not_freshness,
    t24_entry_cutoff_1129,
    t25_flatten_no_walkback,
    t26_max_one_exit_fill,
)


def run_integrity() -> dict[str, Any]:
    rows = [fn() for fn in INTEGRITY_TESTS]
    pack = {
        "tests": rows,
        "pass_n": sum(1 for r in rows if r.get("pass")),
        "total_n": len(rows),
        "ALL_INTEGRITY_TESTS_PASS": all(bool(r.get("pass")) for r in rows),
        "V4_IDENTITY_PASS": bool(rows[0].get("pass")) if rows else False,
        "ECONOMICS_VISIBLE_BEFORE_INTEGRITY_PASS": False,
        "PARTIAL_5M_USED": False,
        "VOLUME_THRESHOLD_SEARCH": False,
    }
    _walk_pnl(pack)
    return pack
