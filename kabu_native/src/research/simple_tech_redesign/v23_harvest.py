"""V23 sealed Capture restream: clock split + stale class for V22 UNEVALUABLE_stale only. No fill. No PnL."""
from __future__ import annotations

import gc
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    iter_push,
    record_event_stamp,
)
from research.simple_tech_entry_family.harvest import (
    _board_row,
    _f,
    _parse_iso,
    _snap_at,
    load_day_cache,
)
from research.simple_tech_entry_family.v3_harvest import ask_entry_ok
from research.simple_tech_redesign.isolation import RESEARCH_CACHE
from research.simple_tech_redesign.v23_spec import (
    CANONICAL_FRESHNESS_SEC,
    CLOCK_DISAGREE_SEC,
    INGRESS_FRESH_SEC,
    REAL_CLASS,
    STALE_N_EXPECTED,
    V22_SPEC_SHA256_EXPECTED,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

JST = ZoneInfo("Asia/Tokyo")
V22_CACHE = RESEARCH_CACHE / "v22_entry_coverage_rca"
V23_CACHE = RESEARCH_CACHE / "v23_stale_execution_coverage_rca"
INGRESS_KEYS = ("received_at", "received_at_jst", "persisted_at", "received_at_utc")
NAN = float("nan")


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _iso(t: Any) -> Optional[str]:
    if not _finite(t):
        return None
    return datetime.fromtimestamp(float(t), JST).isoformat()


def _age(later: Any, earlier: Any) -> Optional[float]:
    if not _finite(later) or not _finite(earlier):
        return None
    return float(later) - float(earlier)


def _fresh_ok(age: Any) -> bool:
    return _finite(age) and float(age) <= float(CANONICAL_FRESHNESS_SEC) + 1e-12


def _ingress_epoch(rec: dict[str, Any], pay: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    for obj in (rec, pay):
        if not isinstance(obj, dict):
            continue
        for k in INGRESS_KEYS:
            t = _parse_iso(obj.get(k))
            if t is not None:
                return float(t), str(k)
    return None, None


def _fresh_source(pay: dict[str, Any], event_t: float) -> tuple[str, float]:
    ba = _f(pay.get("board_age_sec"))
    if ba is not None:
        return "board_age_sec", float(ba)
    fs = _f(pay.get("fresh_sec"))
    if fs is not None:
        return "fresh_sec", float(fs)
    for k in ("CurrentPriceTime", "AskTime", "BidTime"):
        qt = _parse_iso(pay.get(k))
        if qt is not None:
            return str(k), float(event_t) - float(qt)
    return "zero", 0.0


def _board_quote_t(pay: dict[str, Any]) -> Optional[float]:
    cands: list[float] = []
    for k in ("BidTime", "AskTime"):
        t = _parse_iso(pay.get(k))
        if t is not None:
            cands.append(float(t))
    for nest_k in ("Buy1", "Sell1"):
        nest = pay.get(nest_k)
        if not isinstance(nest, dict):
            continue
        for k in ("Time", "QuoteTime", "BidTime", "AskTime"):
            t = _parse_iso(nest.get(k))
            if t is not None:
                cands.append(float(t))
    if not cands:
        return None
    return max(cands)


class ClockBuf:
    __slots__ = (
        "t_event",
        "t_ingress",
        "t_price",
        "t_board",
        "fresh_sec",
        "payload_board_age",
        "fresh_source",
        "ingress_key",
        "event_stamp_kind",
    )

    def __init__(self) -> None:
        self.t_event: list[float] = []
        self.t_ingress: list[float] = []
        self.t_price: list[float] = []
        self.t_board: list[float] = []
        self.fresh_sec: list[float] = []
        self.payload_board_age: list[float] = []
        self.fresh_source: list[str] = []
        self.ingress_key: list[str] = []
        self.event_stamp_kind: list[str] = []

    def append(
        self,
        *,
        t_event: float,
        t_ingress: Optional[float],
        t_price: Optional[float],
        t_board: Optional[float],
        fresh_sec: float,
        payload_board_age: Optional[float],
        fresh_source: str,
        ingress_key: Optional[str],
        event_stamp_kind: str,
    ) -> None:
        self.t_event.append(float(t_event))
        self.t_ingress.append(float(t_ingress) if t_ingress is not None else NAN)
        self.t_price.append(float(t_price) if t_price is not None else NAN)
        self.t_board.append(float(t_board) if t_board is not None else NAN)
        self.fresh_sec.append(float(fresh_sec))
        self.payload_board_age.append(float(payload_board_age) if payload_board_age is not None else NAN)
        self.fresh_source.append(str(fresh_source))
        self.ingress_key.append(str(ingress_key or ""))
        self.event_stamp_kind.append(str(event_stamp_kind))

    def view(self) -> dict[str, Any]:
        return {
            "t_event": np.asarray(self.t_event, dtype=float),
            "t_ingress": np.asarray(self.t_ingress, dtype=float),
            "t_price": np.asarray(self.t_price, dtype=float),
            "t_board": np.asarray(self.t_board, dtype=float),
            "fresh_sec": np.asarray(self.fresh_sec, dtype=float),
            "payload_board_age": np.asarray(self.payload_board_age, dtype=float),
            "fresh_source": np.asarray(self.fresh_source, dtype=object),
            "ingress_key": np.asarray(self.ingress_key, dtype=object),
            "event_stamp_kind": np.asarray(self.event_stamp_kind, dtype=object),
        }


def _last_finite(arr: np.ndarray, i0: int) -> Optional[int]:
    for i in range(int(i0), -1, -1):
        if _finite(arr[i]):
            return int(i)
    return None


def _next_finite(arr: np.ndarray, t_event: np.ndarray, i0: int, t0: float) -> Optional[int]:
    n = int(arr.size)
    for i in range(int(i0) + 1, n):
        if float(t_event[i]) <= float(t0) + 1e-12:
            continue
        if _finite(arr[i]):
            return int(i)
    return None


def _next_canonical_fresh(fresh: np.ndarray, t_event: np.ndarray, i0: int, t0: float) -> Optional[int]:
    n = int(fresh.size)
    for i in range(int(i0) + 1, n):
        if float(t_event[i]) <= float(t0) + 1e-12:
            continue
        if _fresh_ok(fresh[i]):
            return int(i)
    return None


def _v22_snap_i(t_event: np.ndarray, t0: float) -> int:
    if int(t_event.size) == 0:
        return -1
    return int(np.searchsorted(t_event, float(t0), side="right") - 1)


def _causal_last_i(t_event: np.ndarray, t0: float) -> int:
    last = -1
    cut = float(t0) + 1e-12
    for i, t in enumerate(t_event):
        if float(t) <= cut:
            last = int(i)
    return last


def classify_stale(clk: dict[str, Any], *, t0: float, v22_ask_reason: str) -> dict[str, Any]:
    t_event = clk["t_event"]
    t_ingress = clk["t_ingress"]
    t_price = clk["t_price"]
    t_board = clk["t_board"]
    fresh = clk["fresh_sec"]
    payload_age = clk["payload_board_age"]
    fresh_source = clk["fresh_source"]
    event_stamp_kind = clk["event_stamp_kind"]
    n = int(t_event.size)
    i_v22 = _v22_snap_i(t_event, t0)
    i_causal = _causal_last_i(t_event, t0)
    i_ing = -1
    for i in range(n):
        if _finite(t_ingress[i]) and float(t_ingress[i]) <= float(t0) + 1e-12:
            i_ing = i

    board_view = {
        "t": t_event,
        "bid": np.ones(n, dtype=float) if n else np.asarray([], dtype=float),
        "ask": np.ones(n, dtype=float) if n else np.asarray([], dtype=float),
        "bid_qty": np.full(n, 100.0) if n else np.asarray([], dtype=float),
        "ask_qty": np.full(n, 100.0) if n else np.asarray([], dtype=float),
        "special": np.zeros(n, dtype=bool) if n else np.asarray([], dtype=bool),
        "fresh_sec": fresh,
        "executable": np.ones(n, dtype=bool) if n else np.asarray([], dtype=bool),
        "px": np.ones(n, dtype=float) if n else np.asarray([], dtype=float),
        "board_execution_state": np.asarray(["CONTINUOUS_TRADING"] * n, dtype=object),
    }
    v22_snap = _snap_at(board_view, float(t0)) if n else {"ok": False}
    stale_by_fresh = False
    if v22_snap.get("ok"):
        ok, reason = ask_entry_ok(
            {
                **v22_snap,
                "executable": True,
                "special": False,
                "state": "CONTINUOUS_TRADING",
                "ask": 1.0,
                "ask_qty": 100.0,
            }
        )
        stale_by_fresh = (not ok) and reason == "STALE"
    else:
        reason = "NO_BOARD"

    ip = _last_finite(t_price, i_v22) if i_v22 >= 0 else None
    ib = _last_finite(t_board, i_v22) if i_v22 >= 0 else None
    last_price_event = float(t_price[ip]) if ip is not None else None
    last_board_event = float(t_board[ib]) if ib is not None else None
    last_price_recv = float(t_ingress[ip]) if ip is not None and _finite(t_ingress[ip]) else None
    last_board_recv = float(t_ingress[ib]) if ib is not None and _finite(t_ingress[ib]) else None
    last_event_t = float(t_event[i_v22]) if i_v22 >= 0 else None
    last_event_recv = float(t_ingress[i_v22]) if i_v22 >= 0 and _finite(t_ingress[i_v22]) else None
    src = str(fresh_source[i_v22]) if i_v22 >= 0 else ""
    kind = str(event_stamp_kind[i_v22]) if i_v22 >= 0 else ""
    canonical_fresh = float(fresh[i_v22]) if i_v22 >= 0 and _finite(fresh[i_v22]) else None
    payload_ba = float(payload_age[i_v22]) if i_v22 >= 0 and _finite(payload_age[i_v22]) else None

    np_i = _next_finite(t_price, t_event, i_v22, t0) if i_v22 >= 0 else None
    nb_i = _next_finite(t_board, t_event, i_v22, t0) if i_v22 >= 0 else None
    nf_i = _next_canonical_fresh(fresh, t_event, i_v22, t0) if i_v22 >= 0 else None

    price_age_event = _age(t0, last_price_event)
    board_age_event = _age(t0, last_board_event)
    price_age_received = _age(t0, last_price_recv)
    board_age_received = _age(t0, last_board_recv)
    last_push_age = _age(t0, last_event_t)
    last_ingress_age = _age(t0, last_event_recv)
    quote_vs_event = _age(last_event_t, last_price_event) if last_price_event is not None else canonical_fresh
    quote_vs_ingress = _age(last_event_recv, last_price_event)
    board_vs_ingress = _age(last_event_recv, last_board_event)

    excluded_fresh = False
    excluded_n = 0
    if i_v22 >= 0:
        for j in range(i_v22 + 1, n):
            if _finite(t_ingress[j]) and float(t_ingress[j]) <= float(t0) + 1e-12 and float(t_event[j]) > float(t0) + 1e-12:
                excluded_n += 1
                if _fresh_ok(fresh[j]):
                    excluded_fresh = True

    ingress_join_fresh = False
    if i_ing >= 0 and i_ing != i_v22 and _fresh_ok(fresh[i_ing]):
        ingress_join_fresh = True

    unsorted = False
    if n >= 2:
        unsorted = bool(np.any(t_event[1:] + 1e-9 < t_event[:-1]))

    evidence: list[str] = []
    cls = "S6_UNRESOLVED"
    if str(v22_ask_reason or "") != "STALE":
        cls = "S6_UNRESOLVED"
        evidence.append(f"v22_ask_reason={v22_ask_reason}")
    elif i_v22 < 0 or not v22_snap.get("ok"):
        cls = "S6_UNRESOLVED"
        evidence.append("no_v22_snap")
    elif last_price_event is None and last_board_event is None:
        cls = "S6_UNRESOLVED"
        evidence.append("no_price_or_board_quote_clock")
    elif unsorted and i_v22 != i_causal:
        cls = "S3_CAPTURE_JOIN_ALIGNMENT_FAILURE"
        evidence.append(f"unsorted_event_clock i_v22={i_v22} i_causal={i_causal}")
    elif ingress_join_fresh:
        cls = "S3_CAPTURE_JOIN_ALIGNMENT_FAILURE"
        evidence.append(f"ingress_join_would_be_canonical_fresh i_v22={i_v22} i_ing={i_ing}")
    elif excluded_fresh:
        cls = "S3_CAPTURE_JOIN_ALIGNMENT_FAILURE"
        evidence.append(f"recv_le_t0_event_gt_t0_canonical_fresh excluded_n={excluded_n}")
    elif i_v22 != i_causal and i_causal >= 0 and _fresh_ok(fresh[i_causal]) and not _fresh_ok(canonical_fresh):
        cls = "S3_CAPTURE_JOIN_ALIGNMENT_FAILURE"
        evidence.append(f"searchsorted_vs_causal_last i_v22={i_v22} i_causal={i_causal}")
    else:
        board_live_vs_t0 = _fresh_ok(board_age_event)
        board_live_vs_ingress = _fresh_ok(board_vs_ingress)
        price_stale_vs_t0 = not _fresh_ok(price_age_event)
        src_is_price = src in ("CurrentPriceTime", "zero") or (src == "board_age_sec" and board_live_vs_ingress and price_stale_vs_t0)
        payload_disagree = (
            payload_ba is not None
            and _finite(quote_vs_event)
            and abs(float(payload_ba) - float(quote_vs_event)) > float(CLOCK_DISAGREE_SEC)
        )
        neg = any(
            _finite(a) and float(a) < -1e-3
            for a in (price_age_event, board_age_event, price_age_received, board_age_received, canonical_fresh)
        )
        event_ingress_disagree = (
            _finite(last_event_t)
            and _finite(last_event_recv)
            and abs(float(last_event_t) - float(last_event_recv)) > float(CLOCK_DISAGREE_SEC)
        )
        same_quote_event_stale_ingress_fresh = (
            not _fresh_ok(quote_vs_event) and _fresh_ok(quote_vs_ingress) and last_price_event is not None
        )
        if neg:
            cls = "S4_TIMESTAMP_SEMANTIC_MISMATCH"
            evidence.append("negative_age")
        elif payload_disagree:
            cls = "S4_TIMESTAMP_SEMANTIC_MISMATCH"
            evidence.append(f"payload_board_age_sec={payload_ba} recomputed={quote_vs_event}")
        elif (board_live_vs_t0 or board_live_vs_ingress) and (price_stale_vs_t0 or not _fresh_ok(canonical_fresh)):
            cls = "S4_TIMESTAMP_SEMANTIC_MISMATCH"
            evidence.append(
                f"canonical_fresh={canonical_fresh} source={src} board_age_event={board_age_event} "
                f"board_vs_ingress={board_vs_ingress} price_age_event={price_age_event}"
            )
        elif last_board_event is None and src_is_price and _fresh_ok(last_ingress_age) and not _fresh_ok(canonical_fresh):
            cls = "S4_TIMESTAMP_SEMANTIC_MISMATCH"
            evidence.append(f"CurrentPriceTime_used_as_board_freshness source={src} ingress_age={last_ingress_age}")
        elif event_ingress_disagree and same_quote_event_stale_ingress_fresh:
            cls = "S2_EVENT_TIME_STALE_BUT_INGRESS_FRESH"
            evidence.append(
                f"event_t={last_event_t} ingress={last_event_recv} quote_vs_event={quote_vs_event} "
                f"quote_vs_ingress={quote_vs_ingress} stamp_kind={kind}"
            )
        elif event_ingress_disagree and _fresh_ok(last_ingress_age) and not _fresh_ok(canonical_fresh):
            cls = "S2_EVENT_TIME_STALE_BUT_INGRESS_FRESH"
            evidence.append(f"event_ingress_disagree_abs={abs(float(last_event_t)-float(last_event_recv))} stamp_kind={kind}")
        elif (not _fresh_ok(price_age_event) or last_price_event is None) and (
            not _fresh_ok(board_age_event) or last_board_event is None
        ):
            cls = REAL_CLASS
            if _finite(last_push_age) and float(last_push_age) > float(INGRESS_FRESH_SEC) + 1e-12:
                evidence.append(f"NO_PUSH_WITHIN_FRESHNESS_WINDOW last_push_age={last_push_age}")
            else:
                evidence.append(
                    f"PUSH_PRESENT_BUT_QUOTE_CLOCKS_STALE price_age_event={price_age_event} "
                    f"board_age_event={board_age_event} canonical_fresh={canonical_fresh}"
                )
        elif not stale_by_fresh and str(v22_ask_reason) == "STALE":
            cls = "S5_OTHER_PROVEN"
            evidence.append(f"v22_stale_but_canonical_fresh_replay={canonical_fresh} snap_reason={reason}")
        elif canonical_fresh is None or canonical_fresh != canonical_fresh:
            cls = "S5_OTHER_PROVEN"
            evidence.append("nan_canonical_fresh_sec")
        else:
            cls = "S6_UNRESOLVED"
            evidence.append(
                f"unclassified canonical_fresh={canonical_fresh} source={src} "
                f"price_age_event={price_age_event} board_age_event={board_age_event} "
                f"last_ingress_age={last_ingress_age}"
            )

    next_price_event = float(t_price[np_i]) if np_i is not None else None
    next_board_event = float(t_board[nb_i]) if nb_i is not None else None
    next_price_recv = float(t_ingress[np_i]) if np_i is not None and _finite(t_ingress[np_i]) else None
    next_board_recv = float(t_ingress[nb_i]) if nb_i is not None and _finite(t_ingress[nb_i]) else None
    next_fresh_t = float(t_event[nf_i]) if nf_i is not None else None
    time_to_next = _age(next_fresh_t, t0) if next_fresh_t is not None else None

    return {
        "signal_time": _iso(t0),
        "t0": float(t0),
        "last_price_event_time": _iso(last_price_event),
        "last_board_event_time": _iso(last_board_event),
        "last_price_received_at": _iso(last_price_recv),
        "last_board_received_at": _iso(last_board_recv),
        "last_price_event_epoch": last_price_event,
        "last_board_event_epoch": last_board_event,
        "last_price_received_epoch": last_price_recv,
        "last_board_received_epoch": last_board_recv,
        "price_age_event_sec": price_age_event,
        "board_age_event_sec": board_age_event,
        "price_age_received_sec": price_age_received,
        "board_age_received_sec": board_age_received,
        "canonical_fresh_sec": canonical_fresh,
        "canonical_fresh_source": src,
        "event_stamp_kind": kind,
        "last_push_age_sec": last_push_age,
        "last_ingress_age_sec": last_ingress_age,
        "next_price_event_time": _iso(next_price_event),
        "next_board_event_time": _iso(next_board_event),
        "next_price_received_at": _iso(next_price_recv),
        "next_board_received_at": _iso(next_board_recv),
        "time_to_next_fresh_quote_sec": time_to_next,
        "stale_class": cls,
        "reason": "; ".join(evidence) if evidence else cls,
        "v22_ask_reason": str(v22_ask_reason or ""),
        "replay_stale_by_fresh": bool(stale_by_fresh),
        "i_v22": i_v22,
        "i_causal": i_causal,
        "i_ingress_join": i_ing,
        "unsorted_event_clock": bool(unsorted),
        "excluded_recv_le_t0_event_gt_t0_n": int(excluded_n),
        "FILL_REPLAY": False,
        "VIRTUAL_FILL": False,
        "PNL_EVAL": False,
    }


def load_v22_stale_signals(days: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for day in days:
        body = load_v22_day_cache_strict(V22_CACHE / f"day_{day}.json")
        for r in list(body.get("rows") or []):
            if str(r.get("cohort") or "") != "C":
                continue
            if str(r.get("uneval_class") or "") != "stale" and str(r.get("ask_reason") or "") != "STALE":
                continue
            out.append(
                {
                    "date": str(r.get("date") or day),
                    "symbol": str(r.get("symbol") or "").replace(".T", ""),
                    "t0": float(r.get("t0") or 0.0),
                    "ask_reason": str(r.get("ask_reason") or ""),
                    "uneval_class": str(r.get("uneval_class") or ""),
                    "funnel_reason": str(r.get("funnel_reason") or ""),
                    "executable_signal": bool(r.get("executable_signal")),
                }
            )
    return out


def load_v22_day_cache_strict(path: Path) -> dict[str, Any]:
    body = load_day_cache(path, V22_SPEC_SHA256_EXPECTED)
    return body if body else {}


def replay_stale_clocks_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    signals = list(payload.get("signals") or [])
    spec_sha = str(payload.get("spec_sha") or "")
    t0w = time.perf_counter()
    leak: dict[str, Any] = {
        "VIRTUAL_FILL_N": 0,
        "FILL_REPLAY_N": 0,
        "PNL_EVAL_N": 0,
        "THRESHOLD_SEARCH_N": 0,
        "FRESHNESS_CHANGE_N": 0,
        "ENTRY_RULE_CHANGE_N": 0,
        "E4_CHANGE_N": 0,
        "FUTURE_QUOTE_CARRY_BACK_N": 0,
        "FUTURE_RECEIVED_CARRY_BACK_N": 0,
        "EXIT_SIM_N": 0,
        "UNSORTED_EVENT_CLOCK_SYMBOL_N": 0,
    }
    if not signals:
        return {
            "ok": True,
            "date": day,
            "spec_sha": spec_sha,
            "events_n": 0,
            "rows": [],
            "leak": leak,
            "elapsed_sec": 0.0,
            "blocker": None,
        }
    try:
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
        needed = {_bare(r.get("symbol")) for r in signals if _bare(r.get("symbol"))}
        bufs: dict[str, ClockBuf] = {s: ClockBuf() for s in needed}
        events_n = 0
        last_et: Optional[float] = None
        for rec in iter_push(capture):
            sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
            if not sym or sym not in needed:
                continue
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            et = capture_event_epoch(rec, pay)
            if et is None:
                continue
            if float(et) < am_start - 120.0:
                continue
            if float(et) > am_end + 2.0:
                continue
            recv_stamp = record_event_stamp(rec)
            if recv_stamp:
                pay["received_at"] = recv_stamp
            t_ing, ing_key = _ingress_epoch(rec, pay)
            if t_ing is None:
                t_ing, ing_key = _ingress_epoch({"received_at": recv_stamp} if recv_stamp else {}, pay)
            stamp_kind = "ingress" if t_ing is not None and ing_key in INGRESS_KEYS else "quote_fallback"
            src, _src_age = _fresh_source(pay, float(et))
            row = _board_row(pay, float(et))
            bufs[sym].append(
                t_event=float(et),
                t_ingress=t_ing,
                t_price=_parse_iso(pay.get("CurrentPriceTime")),
                t_board=_board_quote_t(pay),
                fresh_sec=float(row["fresh_sec"]),
                payload_board_age=_f(pay.get("board_age_sec")),
                fresh_source=src,
                ingress_key=ing_key,
                event_stamp_kind=stamp_kind,
            )
            last_et = float(et)
            events_n += 1
            if events_n % 200000 == 0:
                print(f"{day} v23 stale clock stream kept={events_n} last_et={last_et}", flush=True)
        views = {s: bufs[s].view() for s in needed}
        for s, clk in views.items():
            te = clk["t_event"]
            if int(te.size) >= 2 and bool(np.any(te[1:] + 1e-9 < te[:-1])):
                leak["UNSORTED_EVENT_CLOCK_SYMBOL_N"] = int(leak["UNSORTED_EVENT_CLOCK_SYMBOL_N"]) + 1
        rows = []
        for sig in signals:
            s = _bare(sig.get("symbol"))
            pack = classify_stale(views[s], t0=float(sig["t0"]), v22_ask_reason=str(sig.get("ask_reason") or ""))
            pack["date"] = str(sig.get("date") or day)
            pack["symbol"] = s
            pack["v22_uneval_class"] = str(sig.get("uneval_class") or "")
            pack["v22_funnel_reason"] = str(sig.get("funnel_reason") or "")
            rows.append(pack)
        print(f"{day} v23 stale clocks kept_events={events_n} stale_signals={len(rows)} last_et={last_et}", flush=True)
        del bufs, views
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "spec_sha": spec_sha,
            "events_n": events_n,
            "last_et": last_et,
            "rows": rows,
            "leak": leak,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "blocker": None,
            "stale_n": len(rows),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }


def save_v23_day_cache(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slim = json_sanitize(
        {
            "ok": body.get("ok"),
            "date": body.get("date"),
            "spec_sha": body.get("spec_sha"),
            "events_n": body.get("events_n"),
            "last_et": body.get("last_et"),
            "leak": body.get("leak"),
            "elapsed_sec": body.get("elapsed_sec"),
            "rows": body.get("rows"),
            "blocker": body.get("blocker"),
            "stale_n": body.get("stale_n"),
        }
    )
    path.write_text(__import__("json").dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")


def load_v23_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    body = load_day_cache(path, spec_sha)
    return body if body else {}


assert int(STALE_N_EXPECTED) == 149
assert abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) < 1e-12
