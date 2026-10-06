"""LEGACY_DEV Capture scan. Field existence / coverage / timestamps only. No outcomes."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.anchor_vs_event_driven.run_comparison import _bare, find_capture_dir, iter_push
from research.am_c0_indicator_exit.isolation import TODAY
from research.new_entry_breakout_continuation_v1.harvest import INGRESS_KEYS
from research.new_full_strategy_implementation_and_dev_eval_v1.quotes import payload_of
from research.opening_logic_input_capability_audit_v1 import (
    BURNED_HOLDOUT_DAYS,
    DEVELOPMENT_DAYS,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    STRESS_DAYS,
)
from research.opening_logic_input_capability_audit_v1.isolation import CACHE
from universe.filters import KNOWN_ETF_CODES

JST = ZoneInfo("Asia/Tokyo")
SCAN_STOP_HM = (9, 2, 0)
INGRESS_FALLBACK_KEYS = ("event_time",)

AUDIT = {
    "HOLDOUT_READ_N": 0,
    "STRESS_READ_N": 0,
    "FUTURE_DATA_N": 0,
    "QUARANTINE_READ_N": 0,
    "PROSPECTIVE_READ_N": 0,
    "PAPER_20260907_READ_N": 0,
    "PNL_COMPUTED_N": 0,
    "OUTCOME_INFORMATION_COMPUTED_N": 0,
    "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N": 0,
    "RAW_MARKET_DATES_OPENED": [],
}

INTERVAL_IDS = (
    "before_08:45",
    "08:45:00-08:49:59",
    "08:50:00-08:54:59",
    "08:55:00-08:57:59",
    "08:58:00-08:58:59",
    "08:59:00-08:59:29",
    "08:59:30-08:59:49",
    "08:59:50-08:59:59",
    "09:00_onward",
)

# Requested names that are not kabu flat keys.
ALIASED = {
    "OVER": "OverSellQty",
    "UNDER": "UnderBuyQty",
}

MISSING_FLAT_QTY = tuple(f"{side}Qty{i}" for side in ("Buy", "Sell") for i in range(1, 11))
LEVEL_KEYS = tuple(f"{side}{i}" for side in ("Buy", "Sell") for i in range(1, 11))

ABSENT_CANDIDATES = (
    "SpecialQuote",
    "IndicativePrice",
    "ExpectedPrice",
    "ExpectedOpen",
    "TradingStatus",
    "Phase",
    "SessionStatus",
    "PreviousOpen",
    "PreviousHigh",
    "PreviousLow",
    "PreviousVWAP",
)

SCALAR_FIELDS = (
    "BidPrice",
    "AskPrice",
    "BidQty",
    "AskQty",
    "OverSellQty",
    "UnderBuyQty",
    "CurrentPrice",
    "CurrentPriceTime",
    "BidTime",
    "AskTime",
    "TradingVolume",
    "TradingValue",
    "TradingVolumeTime",
    "OpeningPrice",
    "OpeningPriceTime",
    "HighPrice",
    "HighPriceTime",
    "LowPrice",
    "LowPriceTime",
    "PreviousClose",
    "PreviousCloseTime",
    "CalcPrice",
    "BidSign",
    "AskSign",
    "MarketOrderBuyQty",
    "MarketOrderSellQty",
    "CurrentPriceStatus",
    "CurrentPriceChangeStatus",
    "ChangePreviousClose",
    "ChangePreviousClosePer",
    "VWAP",
    "Exchange",
    "SecurityType",
)

ETF_CODES = set(KNOWN_ETF_CODES) | {"1570", "1357", "1360"}
FUTURES_TOKEN_HINTS = ("NK225", "TOPIX", "JPX", "OSE", "FUT", "N225", "NKY")


def assert_dev_only_day(day: str) -> None:
    d = str(day)
    if d in STRESS_DAYS:
        AUDIT["STRESS_READ_N"] += 1
        raise RuntimeError("STRESS_READ")
    if d in BURNED_HOLDOUT_DAYS:
        AUDIT["HOLDOUT_READ_N"] += 1
        raise RuntimeError("HOLDOUT_BURNED_READ")
    if d in FORBIDDEN_INPUT_DAYS or d >= "20260903":
        AUDIT["QUARANTINE_READ_N"] += 1
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"QUARANTINE_OR_FUTURE:{d}")
    if d > MAX_RESEARCH_DATE:
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"FUTURE:{d}")
    if d not in DEVELOPMENT_DAYS:
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"NON_DEV:{d}")
    if d == str(TODAY):
        AUDIT["PROSPECTIVE_READ_N"] += 1
        raise RuntimeError(f"ACTIVE_DAY:{d}")


def day_epoch(day: str, h: int, m: int, s: int = 0) -> float:
    return datetime(int(day[:4]), int(day[4:6]), int(day[6:8]), h, m, s, tzinfo=JST).timestamp()


def interval_bounds(day: str) -> list[tuple[str, float, float]]:
    am0 = day_epoch(day, 9, 0, 0)
    stop = day_epoch(day, SCAN_STOP_HM[0], SCAN_STOP_HM[1], SCAN_STOP_HM[2])
    return [
        ("before_08:45", day_epoch(day, 0, 0, 0), day_epoch(day, 8, 45, 0)),
        ("08:45:00-08:49:59", day_epoch(day, 8, 45, 0), day_epoch(day, 8, 50, 0)),
        ("08:50:00-08:54:59", day_epoch(day, 8, 50, 0), day_epoch(day, 8, 55, 0)),
        ("08:55:00-08:57:59", day_epoch(day, 8, 55, 0), day_epoch(day, 8, 58, 0)),
        ("08:58:00-08:58:59", day_epoch(day, 8, 58, 0), day_epoch(day, 8, 59, 0)),
        ("08:59:00-08:59:29", day_epoch(day, 8, 59, 0), day_epoch(day, 8, 59, 30)),
        ("08:59:30-08:59:49", day_epoch(day, 8, 59, 30), day_epoch(day, 8, 59, 50)),
        ("08:59:50-08:59:59", day_epoch(day, 8, 59, 50), am0),
        ("09:00_onward", am0, stop),
    ]


def interval_of(t: float, bounds: list[tuple[str, float, float]]) -> Optional[str]:
    for name, lo, hi in bounds:
        if lo <= float(t) < hi:
            return name
    return None


def _parse_iso(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST).timestamp()
    except Exception:
        return None


def ingress_epoch(rec: dict[str, Any], pay: dict[str, Any]) -> Optional[float]:
    for obj in (rec, pay):
        if not isinstance(obj, dict):
            continue
        for k in INGRESS_KEYS + INGRESS_FALLBACK_KEYS:
            t = _parse_iso(obj.get(k))
            if t is not None:
                return float(t)
    return None


def _populated(v: Any) -> bool:
    if v is None or v == "":
        return False
    if isinstance(v, dict):
        return any(_populated(v.get(k)) for k in ("Price", "Qty", "Sign", "Time") if k in v) or any(
            _populated(x) for x in v.values()
        )
    return True


def _canon_hash(v: Any) -> str:
    blob = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _sym(rec: dict[str, Any], pay: dict[str, Any]) -> str:
    return _bare(rec.get("symbol") or rec.get("Symbol") or pay.get("Symbol") or pay.get("symbol") or "")


def _field_value(pay: dict[str, Any], fid: str) -> Any:
    if fid in ALIASED:
        return pay.get(ALIASED[fid])
    if fid in ("OVER", "UNDER"):
        return pay.get(ALIASED[fid])
    return pay.get(fid)


def _new_field_row(fid: str, *, exists: bool, note: str, ts_field: str, conf: str) -> dict[str, Any]:
    return {
        "FIELD_ID": fid,
        "FIELD_EXISTS": bool(exists),
        "PREOPEN_POPULATED_DAY_N": 0,
        "PREOPEN_POPULATED_SYMBOL_N": 0,
        "VALUE_CAN_CHANGE_PREOPEN": False,
        "SOURCE_TIMESTAMP_FIELD": ts_field,
        "INGRESS_TIMESTAMP_AVAILABLE": True,
        "CAUSAL_BEFORE_OPEN": True,
        "REPLAYABLE": True,
        "SEMANTIC_CONFIDENCE": conf,
        "NOTE": note,
        "_pop_days": set(),
        "_pop_syms": set(),
        "_last": {},
    }


def _timestamp_for_field(fid: str) -> str:
    if fid in ("BidPrice", "BidQty", "BidSign"):
        return "BidTime"
    if fid in ("AskPrice", "AskQty", "AskSign"):
        return "AskTime"
    if fid == "Buy1":
        return "Buy1.Time"
    if fid == "Sell1":
        return "Sell1.Time"
    if fid.startswith("Buy") or fid.startswith("Sell"):
        return "CARRYING_EVENT_INGRESS"
    if fid in ("CurrentPrice", "CurrentPriceStatus", "CurrentPriceChangeStatus"):
        return "CurrentPriceTime"
    if fid in ("OpeningPrice",):
        return "OpeningPriceTime"
    if fid in ("HighPrice",):
        return "HighPriceTime"
    if fid in ("LowPrice",):
        return "LowPriceTime"
    if fid in ("PreviousClose",):
        return "PreviousCloseTime"
    if fid in ("TradingVolume", "TradingValue"):
        return "TradingVolumeTime"
    return "INGRESS_RECEIVE_TIME"


def init_field_rows() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    notes = {
        "OVER": "kabu key OverSellQty (not OVER)",
        "UNDER": "kabu key UnderBuyQty (not UNDER)",
        "CalcPrice": "kabu 計算値; not a documented JPX itayose match price",
        "BidSign": "kabu BoardSuccess sign including 0107/0116/0117 preopen itayose",
        "AskSign": "kabu BoardSuccess sign including 0107/0116/0117 preopen itayose",
        "SpecialQuote": "dedicated SpecialQuote key not in BoardSuccess; phase via BidSign/AskSign",
        "IndicativePrice": "no dedicated indicative/expected-open field on kabu BoardSuccess",
        "ExpectedPrice": "absent",
        "ExpectedOpen": "absent",
        "TradingStatus": "absent; phase inferred from signs + OpeningPrice",
        "Phase": "absent",
        "SessionStatus": "absent",
        "PreviousOpen": "absent on current board",
        "PreviousHigh": "absent on current board",
        "PreviousLow": "absent on current board",
        "PreviousVWAP": "absent on current board",
        "CurrentPriceTime": "print clock only; forbidden as Bid/Ask freshness",
    }
    conf = {
        "BidPrice": "HIGH",
        "AskPrice": "HIGH",
        "BidQty": "HIGH",
        "AskQty": "HIGH",
        "BidTime": "HIGH",
        "AskTime": "HIGH",
        "Buy1": "HIGH",
        "Sell1": "HIGH",
        "OverSellQty": "HIGH",
        "UnderBuyQty": "HIGH",
        "OVER": "HIGH",
        "UNDER": "HIGH",
        "MarketOrderBuyQty": "HIGH",
        "MarketOrderSellQty": "HIGH",
        "PreviousClose": "HIGH",
        "OpeningPrice": "HIGH",
        "OpeningPriceTime": "HIGH",
        "BidSign": "HIGH",
        "AskSign": "HIGH",
        "CurrentPrice": "HIGH",
        "CurrentPriceTime": "HIGH",
        "CalcPrice": "MEDIUM",
        "SpecialQuote": "HIGH",
        "IndicativePrice": "HIGH",
        "TradingStatus": "HIGH",
    }
    for fid in SCALAR_FIELDS:
        rows[fid] = _new_field_row(
            fid,
            exists=True,
            note=notes.get(fid, "kabu BoardSuccess"),
            ts_field=_timestamp_for_field(fid),
            conf=conf.get(fid, "HIGH"),
        )
    rows["OVER"] = _new_field_row("OVER", exists=True, note=notes["OVER"], ts_field="INGRESS_RECEIVE_TIME", conf="HIGH")
    rows["UNDER"] = _new_field_row("UNDER", exists=True, note=notes["UNDER"], ts_field="INGRESS_RECEIVE_TIME", conf="HIGH")
    for fid in LEVEL_KEYS:
        rows[fid] = _new_field_row(
            fid,
            exists=True,
            note="Buy1/Sell1 include Time/Sign; ranks 2-10 Price/Qty only",
            ts_field=_timestamp_for_field(fid),
            conf="HIGH",
        )
    for fid in MISSING_FLAT_QTY:
        rows[fid] = _new_field_row(
            fid,
            exists=False,
            note=f"not a flat key; use {fid.replace('Qty', '')}.Qty",
            ts_field="CARRYING_EVENT_INGRESS",
            conf="HIGH",
        )
        rows[fid]["CAUSAL_BEFORE_OPEN"] = False
        rows[fid]["REPLAYABLE"] = False
        rows[fid]["INGRESS_TIMESTAMP_AVAILABLE"] = False
    for fid in ABSENT_CANDIDATES:
        rows[fid] = _new_field_row(
            fid,
            exists=False,
            note=notes.get(fid, "absent from captured BoardSuccess"),
            ts_field="",
            conf=conf.get(fid, "HIGH"),
        )
        rows[fid]["CAUSAL_BEFORE_OPEN"] = False
        rows[fid]["REPLAYABLE"] = False
        rows[fid]["INGRESS_TIMESTAMP_AVAILABLE"] = False
    return rows


def _touch_field(row: dict[str, Any], *, day: str, sym: str, value: Any, preopen: bool) -> None:
    if not preopen:
        return
    if not _populated(value):
        return
    row["_pop_days"].add(day)
    if sym:
        row["_pop_syms"].add(sym)
    if not sym:
        return
    h = _canon_hash(value)
    prev = row["_last"].get(sym)
    if prev is None:
        row["_last"][sym] = h
    elif prev != h:
        row["VALUE_CAN_CHANGE_PREOPEN"] = True
        row["_last"][sym] = h


def _load_json(path: Path) -> dict[str, Any]:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def _registered_symbols(cap: Path) -> list[str]:
    out: list[str] = []
    for p in (cap / "manifest.json", cap / "capture_manifest.json", cap.parent / "capture_manifest.json"):
        body = _load_json(p)
        raw = body.get("registered_symbols") or body.get("actual_symbols") or body.get("symbols") or []
        for s in raw:
            b = _bare(s)
            if b:
                out.append(b)
        if out:
            break
    return list(dict.fromkeys(out))


def _looks_futures(code: str) -> bool:
    u = str(code or "").upper()
    if any(tok in u for tok in FUTURES_TOKEN_HINTS) and u not in ETF_CODES:
        if u.isdigit() and len(u) <= 4:
            return False
        if any(tok in u for tok in ("NK225", "N225", "FUT", "OSE")):
            return True
    if u.isdigit() and len(u) >= 9:
        return True
    return False


def scan_day(day: str, fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    assert_dev_only_day(day)
    if day not in AUDIT["RAW_MARKET_DATES_OPENED"]:
        AUDIT["RAW_MARKET_DATES_OPENED"].append(day)
    cap = find_capture_dir(day)
    bounds = interval_bounds(day)
    am0 = day_epoch(day, 9, 0, 0)
    stop = day_epoch(day, SCAN_STOP_HM[0], SCAN_STOP_HM[1], SCAN_STOP_HM[2])
    rec = {
        "date": day,
        "capture_path": str(cap) if cap is not None else "",
        "ok": cap is not None,
        "events_scanned": 0,
        "preopen_n": 0,
        "postopen_n": 0,
        "ingress_n": 0,
        "sequence_n": 0,
        "bidtime_n": 0,
        "asktime_n": 0,
        "currentpricetime_n": 0,
        "board_fresh_bid_or_ask_n": 0,
        "board_fresh_ingress_fallback_n": 0,
        "payload_key_union": [],
        "registered_symbols": _registered_symbols(cap) if cap is not None else [],
        "security_types": [],
        "earliest_ingress": None,
        "latest_preopen_ingress": None,
        "EVENT_N_BY_INTERVAL": {k: 0 for k in INTERVAL_IDS},
        "SYMBOL_N_BY_INTERVAL": {k: 0 for k in INTERVAL_IDS},
        "opening_price_populated_symbol_n": 0,
        "opening_price_time_populated_symbol_n": 0,
        "first_post_open_currentprice_symbol_n": 0,
        "first_1m_input_symbol_n": 0,
        "preopen_bidask_symbol_n": 0,
        "preopen_depth10_symbol_n": 0,
        "preopen_overunder_symbol_n": 0,
        "preopen_mo_symbol_n": 0,
        "preopen_calc_symbol_n": 0,
        "preopen_prevclose_symbol_n": 0,
        "trajectory_symbol_n": 0,
        "xs_late_preopen_symbol_n": 0,
        "sequence_monotonic": True,
        "current_price_time_used_as_event_clock": 0,
        "scan_truncated_at": "09:02:00",
    }
    if cap is None:
        rec["ok"] = False
        return rec
    interval_syms: dict[str, set[str]] = {k: set() for k in INTERVAL_IDS}
    open_px_syms: set[str] = set()
    open_t_syms: set[str] = set()
    post_px_syms: set[str] = set()
    bar_open_min_syms: set[str] = set()
    bar_next_min_syms: set[str] = set()
    pre_bidask: set[str] = set()
    pre_d10: set[str] = set()
    pre_ou: set[str] = set()
    pre_mo: set[str] = set()
    pre_calc: set[str] = set()
    pre_pc: set[str] = set()
    sec_types: set[str] = set()
    key_union: set[str] = set()
    last_seq: Optional[int] = None
    last_ing: Optional[float] = None
    for rec_push in iter_push(cap):
        rec["events_scanned"] += 1
        pay = payload_of(rec_push)
        if not pay:
            continue
        ing = ingress_epoch(rec_push, pay)
        if ing is None:
            continue
        if float(ing) >= stop:
            break
        rec["ingress_n"] += 1
        seq = rec_push.get("sequence")
        if seq is not None:
            rec["sequence_n"] += 1
            try:
                si = int(seq)
                if last_seq is not None and si < last_seq:
                    rec["sequence_monotonic"] = False
                last_seq = si
            except (TypeError, ValueError):
                pass
        if last_ing is not None and float(ing) < float(last_ing) - 1.0:
            rec["sequence_monotonic"] = False
        last_ing = float(ing)
        if rec["earliest_ingress"] is None or float(ing) < float(rec["earliest_ingress"]):
            rec["earliest_ingress"] = float(ing)
        iv = interval_of(float(ing), bounds)
        if iv is None:
            continue
        rec["EVENT_N_BY_INTERVAL"][iv] += 1
        sym = _sym(rec_push, pay)
        if sym:
            interval_syms[iv].add(sym)
        preopen = float(ing) < am0
        if preopen:
            rec["preopen_n"] += 1
            rec["latest_preopen_ingress"] = float(ing)
        else:
            rec["postopen_n"] += 1
        if rec["events_scanned"] <= 400:
            key_union.update(str(k) for k in pay.keys())
        st = pay.get("SecurityType")
        if st is not None:
            sec_types.add(str(st))
        if _populated(pay.get("BidTime")):
            rec["bidtime_n"] += 1
        if _populated(pay.get("AskTime")):
            rec["asktime_n"] += 1
        if _populated(pay.get("CurrentPriceTime")):
            rec["currentpricetime_n"] += 1
        if _populated(pay.get("BidTime")) or _populated(pay.get("AskTime")):
            rec["board_fresh_bid_or_ask_n"] += 1
        elif preopen:
            rec["board_fresh_ingress_fallback_n"] += 1
        if preopen and sym:
            if _populated(pay.get("BidPrice")) or _populated(pay.get("AskPrice")) or isinstance(pay.get("Buy1"), dict) or isinstance(pay.get("Sell1"), dict):
                pre_bidask.add(sym)
            if isinstance(pay.get("Buy10"), dict) and isinstance(pay.get("Sell10"), dict):
                pre_d10.add(sym)
            if _populated(pay.get("OverSellQty")) or _populated(pay.get("UnderBuyQty")):
                pre_ou.add(sym)
            if _populated(pay.get("MarketOrderBuyQty")) or _populated(pay.get("MarketOrderSellQty")):
                pre_mo.add(sym)
            if _populated(pay.get("CalcPrice")):
                pre_calc.add(sym)
            if _populated(pay.get("PreviousClose")):
                pre_pc.add(sym)
        if not preopen and sym:
            if _populated(pay.get("OpeningPrice")):
                open_px_syms.add(sym)
            if _populated(pay.get("OpeningPriceTime")):
                open_t_syms.add(sym)
            if _populated(pay.get("CurrentPrice")):
                post_px_syms.add(sym)
                if float(ing) < day_epoch(day, 9, 1, 0):
                    bar_open_min_syms.add(sym)
                else:
                    bar_next_min_syms.add(sym)
        if preopen:
            for fid in SCALAR_FIELDS:
                _touch_field(fields[fid], day=day, sym=sym, value=pay.get(fid), preopen=True)
            _touch_field(fields["OVER"], day=day, sym=sym, value=pay.get("OverSellQty"), preopen=True)
            _touch_field(fields["UNDER"], day=day, sym=sym, value=pay.get("UnderBuyQty"), preopen=True)
            for fid in LEVEL_KEYS:
                _touch_field(fields[fid], day=day, sym=sym, value=pay.get(fid), preopen=True)
            for fid in ABSENT_CANDIDATES:
                if fid in pay:
                    fields[fid]["FIELD_EXISTS"] = True
                    _touch_field(fields[fid], day=day, sym=sym, value=pay.get(fid), preopen=True)
            for fid in MISSING_FLAT_QTY:
                if fid in pay:
                    fields[fid]["FIELD_EXISTS"] = True
                    _touch_field(fields[fid], day=day, sym=sym, value=pay.get(fid), preopen=True)
    for k, ss in interval_syms.items():
        rec["SYMBOL_N_BY_INTERVAL"][k] = len(ss)
    late_ids = (
        "08:55:00-08:57:59",
        "08:58:00-08:58:59",
        "08:59:00-08:59:29",
        "08:59:30-08:59:49",
        "08:59:50-08:59:59",
    )
    late_syms: set[str] = set()
    for k in late_ids:
        late_syms |= interval_syms[k]
    traj = 0
    pre_ids = [k for k in INTERVAL_IDS if k not in ("09:00_onward",)]
    all_pre_syms: set[str] = set()
    for k in pre_ids:
        all_pre_syms |= interval_syms[k]
    for sy in all_pre_syms:
        n_iv = sum(1 for k in pre_ids if sy in interval_syms[k])
        if n_iv >= 3:
            traj += 1
    rec["payload_key_union"] = sorted(key_union)[:120]
    rec["security_types"] = sorted(sec_types)
    rec["opening_price_populated_symbol_n"] = len(open_px_syms)
    rec["opening_price_time_populated_symbol_n"] = len(open_t_syms)
    rec["first_post_open_currentprice_symbol_n"] = len(post_px_syms)
    rec["first_1m_input_symbol_n"] = len(bar_open_min_syms & bar_next_min_syms)
    rec["preopen_bidask_symbol_n"] = len(pre_bidask)
    rec["preopen_depth10_symbol_n"] = len(pre_d10)
    rec["preopen_overunder_symbol_n"] = len(pre_ou)
    rec["preopen_mo_symbol_n"] = len(pre_mo)
    rec["preopen_calc_symbol_n"] = len(pre_calc)
    rec["preopen_prevclose_symbol_n"] = len(pre_pc)
    rec["trajectory_symbol_n"] = int(traj)
    rec["xs_late_preopen_symbol_n"] = len(late_syms)
    rec["ok"] = rec["preopen_n"] > 0
    rec["earliest_ingress_iso"] = (
        datetime.fromtimestamp(float(rec["earliest_ingress"]), JST).isoformat() if rec["earliest_ingress"] else None
    )
    rec["latest_preopen_ingress_iso"] = (
        datetime.fromtimestamp(float(rec["latest_preopen_ingress"]), JST).isoformat() if rec["latest_preopen_ingress"] else None
    )
    rec["registered_symbol_n"] = len(rec["registered_symbols"])
    rec["registered_etf_n"] = sum(1 for s in rec["registered_symbols"] if s in ETF_CODES)
    rec["registered_futures_like_n"] = sum(1 for s in rec["registered_symbols"] if _looks_futures(s))
    return rec


def finalize_fields(fields: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for fid, row in fields.items():
        row["PREOPEN_POPULATED_DAY_N"] = len(row.pop("_pop_days"))
        row["PREOPEN_POPULATED_SYMBOL_N"] = len(row.pop("_pop_syms"))
        row.pop("_last", None)
        if fid == "CurrentPriceTime":
            row["NOTE"] = "print clock only; forbidden as Bid/Ask freshness"
        out.append(row)
    return out


def audit_capture() -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    fields = init_field_rows()
    days = []
    for day in DEVELOPMENT_DAYS:
        print(f"SCAN {day}", flush=True)
        days.append(scan_day(str(day), fields))
    field_rows = finalize_fields(fields)
    return {
        "ok": all(r.get("ok") for r in days),
        "days": days,
        "field_rows": field_rows,
        "AUDIT": dict(AUDIT),
        "OUTCOME_INFORMATION_COMPUTED": False,
        "PNL_COMPUTED": False,
    }


assert KNOWN_ETF_CODES
