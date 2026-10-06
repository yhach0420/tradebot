"""DEV-only Capture schema presence. No returns. No Holdout/Stress/20260903+."""
from __future__ import annotations

import json
from typing import Any, Optional

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    capture_event_epoch,
    find_capture_dir,
    iter_push,
)
from research.am_c0_indicator_exit.isolation import TODAY
from research.discovery_information_object_expansion_decision_v1 import (
    BURNED_HOLDOUT_DAYS,
    DEVELOPMENT_DAYS,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    STRESS_DAYS,
)
from research.discovery_information_object_expansion_decision_v1.isolation import CACHE
from research.new_full_strategy_implementation_and_dev_eval_v1.quotes import payload_of

MAX_EVENTS_PER_DAY = 250000
TARGET_AM = 400
TARGET_PREOPEN = 80
AUDIT = {
    "HOLDOUT_READ_N": 0,
    "STRESS_READ_N": 0,
    "FUTURE_DATA_N": 0,
    "PAPER_20260907_READ_N": 0,
    "PNL_READ_N": 0,
    "MARKOUT_READ_N": 0,
}


def assert_dev_only_day(day: str) -> None:
    d = str(day)
    if d in STRESS_DAYS:
        AUDIT["STRESS_READ_N"] += 1
        raise RuntimeError("STRESS_READ")
    if d in BURNED_HOLDOUT_DAYS:
        AUDIT["HOLDOUT_READ_N"] += 1
        raise RuntimeError("HOLDOUT_BURNED_READ")
    if d in FORBIDDEN_INPUT_DAYS or d > MAX_RESEARCH_DATE or d >= "20260903":
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"FUTURE:{d}")
    if d not in DEVELOPMENT_DAYS:
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"NON_DEV:{d}")
    if d == str(TODAY):
        raise RuntimeError(f"ACTIVE_DAY:{d}")


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _level_qty(pay: dict[str, Any], key: str) -> Optional[float]:
    lv = pay.get(key)
    if not isinstance(lv, dict):
        return None
    return _f(lv.get("Qty"))


def _sym(rec: dict[str, Any], pay: dict[str, Any]) -> str:
    return str(rec.get("symbol") or rec.get("Symbol") or pay.get("Symbol") or pay.get("symbol") or "")


def _scan_day(day: str) -> dict[str, Any]:
    assert_dev_only_day(day)
    cap = find_capture_dir(day)
    am0 = hm_epoch(day, 9, 0)
    am1 = hm_epoch(day, 11, 30)
    rec = {
        "date": day,
        "capture_path": str(cap) if cap is not None else "",
        "ok": cap is not None,
        "events_scanned": 0,
        "preopen_n": 0,
        "am_n": 0,
        "buy10_key_am": 0,
        "sell10_key_am": 0,
        "buy10_qty_pos_am": 0,
        "sell10_qty_pos_am": 0,
        "buy1_key_am": 0,
        "previous_close_am": 0,
        "opening_price_nonnull_am": 0,
        "mo_buy_key_preopen": 0,
        "mo_buy_key_am": 0,
        "mo_buy_pos_preopen": 0,
        "mo_sell_pos_preopen": 0,
        "over_sell_key_preopen": 0,
        "under_buy_key_preopen": 0,
        "preopen_buy10_key": 0,
        "symbols_am_buy10": [],
        "timestamp_ingress_ok": 0,
        "asktime_or_bidtime": 0,
    }
    if cap is None:
        return rec
    symbols: set[str] = set()
    for rec_push in iter_push(cap):
        rec["events_scanned"] += 1
        if rec["events_scanned"] > MAX_EVENTS_PER_DAY:
            break
        pay = payload_of(rec_push)
        if not pay:
            continue
        et = capture_event_epoch(rec_push, pay)
        if et is None:
            continue
        rec["timestamp_ingress_ok"] += 1
        if pay.get("AskTime") or pay.get("BidTime"):
            rec["asktime_or_bidtime"] += 1
        pre = float(et) < float(am0)
        am = float(am0) - 1e-9 <= float(et) < float(am1)
        if pre and rec["preopen_n"] >= TARGET_PREOPEN and rec["am_n"] < TARGET_AM:
            continue
        if rec["am_n"] >= TARGET_AM and rec["preopen_n"] >= TARGET_PREOPEN:
            break
        if pre:
            rec["preopen_n"] += 1
            if "MarketOrderBuyQty" in pay:
                rec["mo_buy_key_preopen"] += 1
            if (_f(pay.get("MarketOrderBuyQty")) or 0) > 0:
                rec["mo_buy_pos_preopen"] += 1
            if (_f(pay.get("MarketOrderSellQty")) or 0) > 0:
                rec["mo_sell_pos_preopen"] += 1
            if "OverSellQty" in pay:
                rec["over_sell_key_preopen"] += 1
            if "UnderBuyQty" in pay:
                rec["under_buy_key_preopen"] += 1
            if isinstance(pay.get("Buy10"), dict):
                rec["preopen_buy10_key"] += 1
        if am:
            rec["am_n"] += 1
            if "MarketOrderBuyQty" in pay:
                rec["mo_buy_key_am"] += 1
            if isinstance(pay.get("Buy1"), dict):
                rec["buy1_key_am"] += 1
            if isinstance(pay.get("Buy10"), dict):
                rec["buy10_key_am"] += 1
                sy = _sym(rec_push, pay)
                if sy:
                    symbols.add(sy)
            if isinstance(pay.get("Sell10"), dict):
                rec["sell10_key_am"] += 1
            bq = _level_qty(pay, "Buy10")
            sq = _level_qty(pay, "Sell10")
            if bq is not None and bq > 0:
                rec["buy10_qty_pos_am"] += 1
            if sq is not None and sq > 0:
                rec["sell10_qty_pos_am"] += 1
            if _f(pay.get("PreviousClose")) is not None:
                rec["previous_close_am"] += 1
            if _f(pay.get("OpeningPrice")) is not None:
                rec["opening_price_nonnull_am"] += 1
        if rec["am_n"] >= TARGET_AM and rec["preopen_n"] >= TARGET_PREOPEN:
            break
        if rec["am_n"] >= TARGET_AM and rec["events_scanned"] >= 25000 and rec["preopen_n"] == 0:
            break
    rec["symbols_am_buy10"] = sorted(symbols)[:80]
    rec["symbol_n_am_buy10"] = len(symbols)
    rec["full_depth_am"] = rec["buy10_key_am"] > 0 and rec["sell10_key_am"] > 0
    rec["preopen_present"] = rec["preopen_n"] > 0 or rec["mo_buy_key_preopen"] > 0
    rec["auction_fields_present"] = rec["mo_buy_key_preopen"] > 0 or rec.get("mo_buy_key_am", 0) > 0
    rec["prior_session_present"] = rec["previous_close_am"] > 0
    rec["event_flow_present"] = rec["timestamp_ingress_ok"] >= 10
    rec["ok"] = bool(cap is not None and rec["am_n"] > 0)
    return rec


def _coverage_from_days(days: list[dict[str, Any]]) -> dict[str, Any]:
    depth_days = [r["date"] for r in days if r.get("full_depth_am")]
    l1_days = [r["date"] for r in days if int(r.get("buy1_key_am") or 0) > 0]
    pre_days = [r["date"] for r in days if r.get("preopen_present")]
    auc_days = [r["date"] for r in days if r.get("auction_fields_present") or r.get("preopen_present")]
    prev_days = [r["date"] for r in days if r.get("prior_session_present")]
    ev_days = [r["date"] for r in days if r.get("event_flow_present")]
    vol_days = [r["date"] for r in days if int(r.get("am_n") or 0) > 0]
    open_days = [r["date"] for r in days if int(r.get("opening_price_nonnull_am") or 0) > 0]
    mo_days = [r["date"] for r in days if int(r.get("mo_buy_key_preopen") or 0) > 0]
    symbols = set()
    for r in days:
        for s in r.get("symbols_am_buy10") or []:
            symbols.add(s)
    broad_depth = len(depth_days) >= 8 and len(symbols) >= 8
    ts = all(int(r.get("timestamp_ingress_ok") or 0) > 0 for r in days if r.get("ok"))

    def pack(stored: bool, day_ids: list[str], broad: bool, causal: bool, session: str) -> dict[str, Any]:
        return {
            "STORED": bool(stored),
            "DEV_DAY_N": len(day_ids),
            "DAYS": list(day_ids),
            "SYMBOL_COVERAGE": len(symbols) if stored else 0,
            "SESSION_COVERAGE": session,
            "BROAD": bool(broad and stored),
            "CAUSALLY_TIMESTAMPED": bool(causal and stored),
        }

    default = pack(True, [r["date"] for r in days if r.get("ok")], True, ts, "AM")
    cov = {
        "PRICE_PATH": pack(True, vol_days, len(vol_days) >= 8, ts, "AM"),
        "TRADED_ACTIVITY": pack(True, vol_days, len(vol_days) >= 8, ts, "AM"),
        "TOP_OF_BOOK_STATE": pack(True, l1_days, len(l1_days) >= 8, ts, "AM"),
        "FULL_DEPTH_GEOMETRY": pack(True, depth_days, broad_depth, ts, "AM_continuous"),
        "DEPTH_MIGRATION": pack(True, depth_days, broad_depth, ts, "AM_continuous"),
        "QUOTE_UPDATE_DYNAMICS": pack(True, ev_days, len(ev_days) >= 8, ts, "AM"),
        "TRADE_FLOW_DYNAMICS": pack(True, vol_days, len(vol_days) >= 8, ts, "AM"),
        "ABSORPTION_REPLENISHMENT": pack(True, l1_days, len(l1_days) >= 8, ts, "AM"),
        "OPENING_AUCTION_CONTEXT": pack(True, sorted(set(pre_days + mo_days + auc_days)), len(auc_days) >= 8 or len(pre_days) >= 1, ts, "PREOPEN_context_or_persisted_MOQ"),
        "PRIOR_SESSION_CONTEXT": pack(True, sorted(set(prev_days + open_days)), len(prev_days) >= 8, ts, "AM+PreviousClose"),
        "CROSS_SECTIONAL_FLOW_STATE": pack(True, vol_days, len(symbols) >= 8, ts, "AM_universe"),
        "PORTFOLIO_STATE": pack(True, vol_days, True, True, "internal_occupancy"),
        "EVENT_FLOW_DYNAMICS": pack(True, ev_days, len(ev_days) >= 8, ts, "AM_event_clock"),
    }
    return {
        "coverage_by_object": cov,
        "default_coverage": default,
        "full_depth_available": len(depth_days) >= 8,
        "event_flow_available": len(ev_days) >= 8,
        "preopen_available": len(pre_days) >= 1 or len(auc_days) >= 1,
        "prior_session_available": len(prev_days) >= 8,
        "symbol_n_sampled": len(symbols),
        "timestamp_semantics_proven": bool(ts and len(depth_days) >= 8),
    }


def audit_dev_schema(*, use_cache: bool = True) -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE / "schema_audit.json"
    if use_cache and cache_path.is_file():
        prev = json.loads(cache_path.read_text(encoding="utf-8"))
        if list(prev.get("days_requested") or []) == list(DEVELOPMENT_DAYS) and prev.get("ok"):
            return prev
    days = [_scan_day(str(d)) for d in DEVELOPMENT_DAYS]
    cov = _coverage_from_days(days)
    out = {
        "ok": all(r.get("ok") for r in days) and bool(cov["full_depth_available"]),
        "days_requested": list(DEVELOPMENT_DAYS),
        "days": days,
        "AUDIT": dict(AUDIT),
        "OUTCOME_READ_N": 0,
        **cov,
    }
    cache_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return out
