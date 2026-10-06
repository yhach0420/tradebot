"""Ingest-only tradability / t0-price classification. No dual, no orders, no C14 write."""
from __future__ import annotations

import gc
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_timing_robustness.grid import hm_epoch, hm_label
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    iter_push,
)
from research.e1_x34a_execution_policy.executable_board import (
    PREOPEN_ITAYOSE_SIGNS,
    SPECIAL_QUOTE_SIGNS,
    STATE_NOT_OPENED,
    STATE_PREOPEN_ITAYOSE,
    STATE_SPECIAL_QUOTE,
    STATE_SPECIAL_QUOTE_FIELD,
)
from research.uniform10_entry_rebuild import UNIFORM10, WAIT_SEC as FILL_WAIT
from research.uniform10_entry_validity_audit import GROUP_A, GROUP_B, GROUP_C, WAIT_SEC
from small_paper.v1r_native_entry_live import extract_board_row

ITAYOSE_SOURCES = frozenset(
    {
        "ITAYOSE_LOCKED_OR_CROSSED_MID",
        "PREOPEN_INDICATIVE",
        "SPECIAL_QUOTE_MID",
        "NON_EXECUTABLE_BOARD_MID",
        "SYNTHETIC_OR_MISSING_MID",
    }
)


def _fin(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _opened(row: Optional[dict[str, Any]]) -> bool:
    if not row:
        return False
    op = _fin(row.get("OpeningPrice"))
    return bool(op is not None and op > 0)


def _mid_ok(row: Optional[dict[str, Any]]) -> bool:
    if not row:
        return False
    if row.get("special"):
        return False
    a = _fin(row.get("ask"))
    b = _fin(row.get("bid"))
    return bool(a is not None and b is not None and a > 0 and b > 0)


def t0_price_source(mid_row: Optional[dict[str, Any]]) -> str:
    if not _mid_ok(mid_row):
        return "SYNTHETIC_OR_MISSING_MID"
    assert mid_row is not None
    a = float(mid_row["ask"])
    b = float(mid_row["bid"])
    locked = b >= a
    exe = bool(mid_row.get("executable"))
    state = str(mid_row.get("board_execution_state") or "")
    ask_sign = str(mid_row.get("AskSign") or "")
    if exe and not locked:
        return "CONTINUOUS_BOARD_MID"
    if locked:
        return "ITAYOSE_LOCKED_OR_CROSSED_MID"
    if state in {STATE_NOT_OPENED, STATE_PREOPEN_ITAYOSE} or ask_sign in PREOPEN_ITAYOSE_SIGNS:
        return "PREOPEN_INDICATIVE"
    if state in {STATE_SPECIAL_QUOTE, STATE_SPECIAL_QUOTE_FIELD} or ask_sign in SPECIAL_QUOTE_SIGNS:
        return "SPECIAL_QUOTE_MID"
    if not exe:
        return "NON_EXECUTABLE_BOARD_MID"
    return "CONTINUOUS_BOARD_MID"


def _slim(row: dict[str, Any]) -> dict[str, Any]:
    a = _fin(row.get("ask"))
    b = _fin(row.get("bid"))
    return {
        "t": float(row.get("t") or 0.0),
        "bid": b,
        "ask": a,
        "bid_qty": _fin(row.get("bid_qty")),
        "ask_qty": _fin(row.get("ask_qty")),
        "special": bool(row.get("special")),
        "fresh_sec": _fin(row.get("fresh_sec")),
        "executable": bool(row.get("executable")),
        "board_execution_state": str(row.get("board_execution_state") or ""),
        "AskSign": str(row.get("AskSign") or ""),
        "BidSign": str(row.get("BidSign") or ""),
        "OpeningPrice": _fin(row.get("OpeningPrice")),
        "CurrentPrice": _fin(row.get("kabu_CurrentPrice") or row.get("CurrentPrice")),
        "CalcPrice": _fin(row.get("CalcPrice")),
        "CurrentPriceStatus": row.get("CurrentPriceStatus"),
        "locked_or_crossed": bool(row.get("locked_or_crossed")),
        "TradingVolume": _fin(row.get("TradingVolume")),
    }


def classify_groups(
    *,
    exe0: bool,
    became_exe: bool,
    opening_transition: bool,
) -> str:
    """Primary groups: A executable at t0; B wait-window becomes executable; else C.

    GROUP B is named OPENS_WITHIN_1S in the audit brief. Implementation uses the
    fill SoT: continuous executable appears inside the 1s wait window. Literal
    OpeningPrice appearance is stored as opening_transition.
    """
    if exe0:
        return GROUP_A
    if became_exe:
        return GROUP_B
    return GROUP_C


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = {_bare(s) for s in (payload.get("universe") or [])}
    t_wall = time.perf_counter()
    wait = float(payload.get("wait_sec") or WAIT_SEC or FILL_WAIT)
    try:
        labels = [hm_label(h, m) for h, m in UNIFORM10]
        t0s = np.asarray([hm_epoch(day, h, m) for h, m in UNIFORM10], dtype=float)
        n_a = int(t0s.size)
        last: dict[str, dict[str, Any]] = {}
        last_mid: dict[str, dict[str, Any]] = {}
        snaps: dict[tuple[str, int], Optional[dict[str, Any]]] = {}
        mid_snaps: dict[tuple[str, int], Optional[dict[str, Any]]] = {}
        win_exe: dict[tuple[str, int], bool] = {}
        win_open: dict[tuple[str, int], bool] = {}
        k_close = 0
        events_n = 0

        def close_anchor(idx: int) -> None:
            for sym, row in last.items():
                key = (sym, idx)
                if key not in snaps:
                    snaps[key] = row
                    mid_snaps[key] = last_mid.get(sym)

        for rec in iter_push(capture):
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            et = capture_event_epoch(rec, pay)
            if et is None:
                continue
            et = float(et)
            sym = _bare(rec.get("symbol") or pay.get("Symbol"))
            if universe and sym not in universe:
                continue
            events_n += 1
            while k_close < n_a and et > float(t0s[k_close]) + 1e-12:
                close_anchor(k_close)
                k_close += 1
            calc = _fin(pay.get("CalculationPrice"))
            if calc is None:
                calc = _fin(pay.get("CalcPrice"))
            raw = extract_board_row(pay, et)
            raw["CalcPrice"] = calc
            row = _slim(raw)
            j = int(np.searchsorted(t0s, et, side="right") - 1)
            if j >= 0 and et > float(t0s[j]) + 1e-12 and et <= float(t0s[j]) + wait + 1e-12:
                key = (sym, j)
                if bool(row.get("executable")):
                    win_exe[key] = True
                if _opened(row):
                    win_open[key] = True
            last[sym] = row
            if _mid_ok(row):
                last_mid[sym] = row

        while k_close < n_a:
            close_anchor(k_close)
            k_close += 1

        out_rows: list[dict[str, Any]] = []
        seen_sym = set(last) | {s for s, _i in snaps}
        for sym in sorted(seen_sym):
            if universe and sym not in universe:
                continue
            for i, lab in enumerate(labels):
                snap = snaps.get((sym, i))
                midr = mid_snaps.get((sym, i))
                if snap is None:
                    continue
                exe0 = bool(snap.get("executable"))
                became = bool(win_exe.get((sym, i)))
                opened0 = _opened(snap)
                opening_tr = (not opened0) and bool(win_open.get((sym, i)))
                src = t0_price_source(midr)
                a = _fin((midr or {}).get("ask")) if midr else None
                b = _fin((midr or {}).get("bid")) if midr else None
                t0_mid = (a + b) / 2.0 if a and b and a > 0 and b > 0 else None
                out_rows.append(
                    {
                        "date": day,
                        "anchor": lab,
                        "symbol": sym,
                        "group": classify_groups(exe0=exe0, became_exe=became, opening_transition=opening_tr),
                        "executable_at_t0": exe0,
                        "becomes_executable_within_1s": became,
                        "opening_transition_within_1s": opening_tr,
                        "opened_at_t0": opened0,
                        "t0_price_source": src,
                        "itayose_like_t0_mid": src in ITAYOSE_SOURCES,
                        "t0_mid": t0_mid,
                        "t0_bid": _fin(snap.get("bid")),
                        "t0_ask": _fin(snap.get("ask")),
                        "t0_CurrentPrice": _fin(snap.get("CurrentPrice")),
                        "t0_OpeningPrice": _fin(snap.get("OpeningPrice")),
                        "t0_CalcPrice": _fin(snap.get("CalcPrice")),
                        "t0_board_state": str(snap.get("board_execution_state") or ""),
                        "t0_AskSign": str(snap.get("AskSign") or ""),
                        "t0_locked_or_crossed": bool(snap.get("locked_or_crossed")),
                        "t0_special": bool(snap.get("special")),
                        "mid_src_executable": bool(midr.get("executable")) if midr else None,
                        "mid_src_state": str(midr.get("board_execution_state") or "") if midr else None,
                        "mid_src_locked": bool(midr.get("locked_or_crossed")) if midr else None,
                    }
                )
        del last, last_mid, snaps, mid_snaps
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": "CLASSIFY",
            "rows": out_rows,
            "events_n": events_n,
            "elapsed_sec": round(time.perf_counter() - t_wall, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": "CLASSIFY",
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t_wall, 3),
        }
