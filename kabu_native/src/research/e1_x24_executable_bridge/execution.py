"""Bid/ask board coverage + executable price bridge.

Canonical long execution (kabu inverted English names):
  ENTRY buy  -> Sell1.Price (= true ask)  [kabu BidPrice is Sell1]
  EXIT  sell -> Buy1.Price  (= true bid)  [kabu AskPrice is Buy1]
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

from . import BOARD_DAYS, EXEC_WINDOW_SEC, FORBIDDEN_RISK_FROM, TARGET_DAY

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]


def _dash(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def _ts(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)):
            return float(v)
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST).timestamp()
    except Exception:
        return None


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x or x <= 0:
            return None
        return x
    except (TypeError, ValueError):
        return None


def load_board_events(day: str, symbol: str) -> dict[str, np.ndarray]:
    """Chronological board events with canonical ask/bid."""
    assert day < FORBIDDEN_RISK_FROM or day in BOARD_DAYS
    fp = NATIVE / "data" / "push_jsonl" / _dash(day) / f"{symbol}.T.jsonl"
    if not fp.exists():
        fp = NATIVE / "data" / "push_jsonl" / _dash(day) / f"{symbol}.jsonl"
    if not fp.exists():
        return {
            "t": np.empty(0), "ask": np.empty(0), "bid": np.empty(0),
            "ask_qty": np.empty(0), "bid_qty": np.empty(0),
            "special": np.empty(0, dtype=bool), "spread": np.empty(0),
        }
    ts, asks, bids, aq, bq, specials, spreads = [], [], [], [], [], [], []
    for line in fp.open("rb"):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        recv = _ts(d.get("recorded_at"))
        if recv is None:
            continue
        pay = d.get("payload") or {}
        # canonical: Sell1 = true ask, Buy1 = true bid
        sell1 = pay.get("Sell1") or {}
        buy1 = pay.get("Buy1") or {}
        ask = _f(sell1.get("Price")) if isinstance(sell1, dict) else None
        bid = _f(buy1.get("Price")) if isinstance(buy1, dict) else None
        if ask is None:
            ask = _f(pay.get("BidPrice"))  # kabu BidPrice = Sell1
        if bid is None:
            bid = _f(pay.get("AskPrice"))  # kabu AskPrice = Buy1
        if ask is None or bid is None:
            continue
        ask_q = _f(sell1.get("Qty")) if isinstance(sell1, dict) else _f(pay.get("BidQty"))
        bid_q = _f(buy1.get("Qty")) if isinstance(buy1, dict) else _f(pay.get("AskQty"))
        sq = pay.get("SpecialQuote")
        if sq is None:
            # treat extreme signs / zero qty as blocked when both sides absent qty
            sq_flag = False
        else:
            sq_flag = bool(sq) and str(sq) not in ("", "0", "None", "null")
        if ask_q is not None and ask_q <= 0:
            sq_flag = True
        if bid_q is not None and bid_q <= 0:
            sq_flag = True
        ts.append(recv)
        asks.append(ask)
        bids.append(bid)
        aq.append(ask_q if ask_q is not None else np.nan)
        bq.append(bid_q if bid_q is not None else np.nan)
        specials.append(sq_flag)
        spreads.append((ask - bid) / ((ask + bid) / 2.0) * 10000.0 if ask and bid else np.nan)
    if not ts:
        return {
            "t": np.empty(0), "ask": np.empty(0), "bid": np.empty(0),
            "ask_qty": np.empty(0), "bid_qty": np.empty(0),
            "special": np.empty(0, dtype=bool), "spread": np.empty(0),
        }
    order = np.argsort(np.asarray(ts), kind="mergesort")
    return {
        "t": np.asarray(ts, dtype=float)[order],
        "ask": np.asarray(asks, dtype=float)[order],
        "bid": np.asarray(bids, dtype=float)[order],
        "ask_qty": np.asarray(aq, dtype=float)[order],
        "bid_qty": np.asarray(bq, dtype=float)[order],
        "special": np.asarray(specials, dtype=bool)[order],
        "spread": np.asarray(spreads, dtype=float)[order],
    }


def first_valid_after(
    board: dict[str, np.ndarray],
    signal_t: float,
    *,
    side: str,
    window: float = EXEC_WINDOW_SEC,
) -> dict[str, Any]:
    """First valid ask (ENTRY) or bid (EXIT) at or after signal within window."""
    t = board["t"]
    if t.size == 0:
        return {"status": "EXECUTION_PRICE_UNAVAILABLE", "price": None, "delay_sec": None}
    i0 = int(np.searchsorted(t, signal_t, side="left"))
    lim = signal_t + window
    for i in range(i0, t.size):
        if t[i] > lim + 1e-12:
            break
        if t[i] + 1e-12 < signal_t:
            continue
        if board["special"][i]:
            return {
                "status": "SPECIAL_QUOTE_BLOCKED",
                "price": None,
                "delay_sec": float(t[i] - signal_t),
                "event_time": float(t[i]),
                "spread_bps": float(board["spread"][i]) if np.isfinite(board["spread"][i]) else None,
            }
        px = float(board["ask"][i] if side == "ask" else board["bid"][i])
        return {
            "status": "OK",
            "price": px,
            "delay_sec": float(t[i] - signal_t),
            "event_time": float(t[i]),
            "spread_bps": float(board["spread"][i]) if np.isfinite(board["spread"][i]) else None,
            "board_available": True,
        }
    return {"status": "EXECUTION_PRICE_UNAVAILABLE", "price": None, "delay_sec": None}


def board_coverage_audit(days: tuple[str, ...] = BOARD_DAYS) -> dict[str, Any]:
    """Sample board field availability across days (symbol-level)."""
    from research.e1_x14_board_independent_signal.ticks import list_day_symbols
    rows = []
    total_events = 0
    with_board = 0
    for day in days:
        syms = list_day_symbols(day)
        # sample up to 5 symbols per day for coverage speed
        for sym in syms[:5]:
            b = load_board_events(day, sym)
            n = int(b["t"].size)
            total_events += n
            with_board += n
            rows.append({
                "day": day, "symbol": sym, "events": n,
                "ask_present": n > 0, "bid_present": n > 0,
                "median_spread_bps": float(np.nanmedian(b["spread"])) if n else None,
                "special_quote_events": int(np.sum(b["special"])) if n else 0,
            })
    return {
        "days": list(days),
        "sample_rows": rows,
        "total_sample_events": total_events,
        "fields_checked": [
            "AskPrice", "BidPrice", "AskQty", "BidQty", "AskTime", "BidTime",
            "CurrentPrice", "CurrentPriceTime", "Sell1", "Buy1", "SpecialQuote",
        ],
        "canonical_mapping": {
            "entry_ask": "Sell1.Price (true ask; kabu BidPrice)",
            "exit_bid": "Buy1.Price (true bid; kabu AskPrice)",
        },
        "risk_only_excluded_from": FORBIDDEN_RISK_FROM,
    }


def executable_metrics_for_pair(
    pair_row: dict[str, Any],
    board_cache: dict[tuple[str, str], dict[str, np.ndarray]],
    day: str = TARGET_DAY,
) -> dict[str, Any]:
    """Compute executable PnL for one pair's 20260804 trades."""
    n_ref = int(pair_row["trade_rets_bps"].size) if hasattr(pair_row["trade_rets_bps"], "size") else len(pair_row["trade_rets_bps"])
    if n_ref == 0:
        return {
            "reference_trades": 0, "executable_trades": 0, "execution_coverage": 0.0,
            "executable_status": "EXECUTION_COVERAGE_INSUFFICIENT",
            "avg_executable_pnl_yen_100": None, "executable_PF": None,
            "trades": [],
        }
    symbols = pair_row["symbols"]
    entry_ts = pair_row["entry_epochs"]
    exit_ts = pair_row["exit_epochs"]
    entry_ref = pair_row["entry_ref_px"]
    exit_ref = pair_row["exit_ref_px"]
    cluster_ids = pair_row["cluster_ids"]

    exec_pnls = []
    ref_pnls = []
    delays_e, delays_x = [], []
    ledger = []
    unavailable = blocked = 0

    for i in range(n_ref):
        sym = str(symbols[i])
        key = (day, sym)
        if key not in board_cache:
            board_cache[key] = load_board_events(day, sym)
        board = board_cache[key]
        ent = first_valid_after(board, float(entry_ts[i]), side="ask")
        if ent["status"] == "SPECIAL_QUOTE_BLOCKED":
            blocked += 1
            ledger.append({"cluster_id": str(cluster_ids[i]), "status": ent["status"], "side": "entry"})
            continue
        if ent["status"] != "OK":
            unavailable += 1
            ledger.append({"cluster_id": str(cluster_ids[i]), "status": ent["status"], "side": "entry"})
            continue
        # EXIT must not use price before exit signal; search after exit_t
        exi = first_valid_after(board, float(exit_ts[i]), side="bid")
        if exi["status"] == "SPECIAL_QUOTE_BLOCKED":
            blocked += 1
            ledger.append({"cluster_id": str(cluster_ids[i]), "status": exi["status"], "side": "exit"})
            continue
        if exi["status"] != "OK":
            unavailable += 1
            ledger.append({"cluster_id": str(cluster_ids[i]), "status": exi["status"], "side": "exit"})
            continue
        ask = float(ent["price"])
        bid = float(exi["price"])
        ref_e = float(entry_ref[i])
        ref_x = float(exit_ref[i])
        gross_ref = (ref_x / ref_e - 1.0) * ref_e * 100.0
        exec_pnl = (bid - ask) * 100.0  # long 100 shares
        spread_cost = (ask - bid)  # negative when inverted? ask>bid normally; cost of round trip mid approx
        # use entry ask - exit bid absolute round-trip vs mid
        mid_e = ask  # at entry
        entry_slip = ask - ref_e
        exit_slip = ref_x - bid
        exec_pnls.append(exec_pnl)
        ref_pnls.append(gross_ref)
        delays_e.append(ent["delay_sec"])
        delays_x.append(exi["delay_sec"])
        ledger.append({
            "cluster_id": str(cluster_ids[i]),
            "status": "OK",
            "entry_ask": ask,
            "exit_bid": bid,
            "entry_delay_sec": ent["delay_sec"],
            "exit_delay_sec": exi["delay_sec"],
            "spread_bps_at_entry": ent.get("spread_bps"),
            "entry_slippage": entry_slip,
            "exit_slippage": exit_slip,
            "gross_reference_pnl_yen_100": gross_ref,
            "executable_pnl_yen_100": exec_pnl,
        })

    n_exec = len(exec_pnls)
    coverage = n_exec / n_ref if n_ref else 0.0
    if n_exec == 0:
        status = "EXECUTION_COVERAGE_INSUFFICIENT"
        avg_e = pf = worst = max_dd = None
        med = None
        pos = neg = 0
    else:
        arr = np.asarray(exec_pnls, dtype=float)
        avg_e = float(np.mean(arr))
        med = float(np.median(arr))
        pos = int(np.sum(arr > 0))
        neg = int(np.sum(arr < 0))
        gp = float(np.sum(arr[arr > 0])) if pos else 0.0
        gl = float(abs(np.sum(arr[arr < 0]))) if neg else 0.0
        pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else None)
        worst = float(np.min(arr))
        cum = np.cumsum(arr)
        peak = np.maximum.accumulate(cum)
        max_dd = float(np.min(cum - peak))
        ref_avg = float(np.mean(ref_pnls)) if ref_pnls else None
        if coverage < 0.70:
            status = "EXECUTION_COVERAGE_INSUFFICIENT"
        elif avg_e > 0 and pf is not None and pf > 1:
            status = "EXECUTABLE_EVIDENCE_POSITIVE"
        elif ref_avg is not None and ref_avg > 0 and avg_e <= 0:
            status = "EXECUTION_COST_SENSITIVE"
        else:
            status = "EXECUTABLE_MIXED"

    return {
        "reference_trades": n_ref,
        "executable_trades": n_exec,
        "execution_coverage": coverage,
        "unavailable": unavailable,
        "special_quote_blocked": blocked,
        "avg_executable_pnl_yen_100": avg_e if n_exec else None,
        "median_executable_pnl_yen_100": med if n_exec else None,
        "executable_PF": pf if n_exec else None,
        "worst_executable_trade": worst if n_exec else None,
        "max_executable_drawdown": max_dd if n_exec else None,
        "positive_executable_trades": pos if n_exec else 0,
        "negative_executable_trades": neg if n_exec else 0,
        "avg_entry_delay_sec": float(np.mean(delays_e)) if delays_e else None,
        "avg_exit_delay_sec": float(np.mean(delays_x)) if delays_x else None,
        "avg_reference_pnl_yen_100": float(np.mean(ref_pnls)) if ref_pnls else None,
        "executable_status": status,
        "ledger_sample": ledger[:20],
    }
