"""Compare the freshness clock with Buy1.Time on the three unfilled exits only."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from research.anchor_vs_event_driven.run_comparison import capture_event_epoch
from research.current50_v16_complete_strategy_risk_foundation_v1.foundation import _capture_dir
from research.e1_x34a_execution_policy.executable_board import is_executable_continuous_board
from research.event_time_volume_confirmed_impulse import FRESH_SEC
from research.simple_tech_entry_family.harvest import _board_row, _parse_iso

JST = timezone(timedelta(hours=9))
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "v16_exit_execution_evidence_gap_rca_v1"

CASES = (
    {
        "day": "20260825",
        "symbol": "2351",
        "uid": "20260825|PM|2351.T|89",
        "entry_t": 1787636883.012,
        "seq_after": 0,
        "notional": 32300.0,
        "entry_px": 323.0,
    },
    {
        "day": "20260902",
        "symbol": "7376",
        "uid": "20260902|PM|7376.T|463",
        "entry_t": 1788326551.453,
        "seq_after": 0,
        "notional": 82300.0,
        "entry_px": 823.0,
    },
    {
        "day": "20261009",
        "symbol": "5246",
        "uid": "20261009|PM|5246.T|13516",
        "entry_t": 1791522163.379,
        "seq_after": 678498,
        "notional": 105700.0,
        "entry_px": 1057.0,
    },
)


def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(float(stamp), JST).isoformat(timespec="milliseconds")


def _age(event_t: float, raw: Any) -> Optional[float]:
    parsed = _parse_iso(raw)
    if parsed is None:
        return None
    return float(event_t) - float(parsed)


def _lvl(payload: dict[str, Any], name: str) -> dict[str, Any]:
    level = payload.get(name)
    if not isinstance(level, dict):
        return {"present": False}
    return {
        "present": True,
        "price": level.get("Price"),
        "qty": level.get("Qty"),
        "sign": level.get("Sign"),
        "time": level.get("Time"),
    }


def _fresh_source(payload: dict[str, Any]) -> str:
    if payload.get("board_age_sec") not in (None, ""):
        return "board_age_sec"
    if payload.get("fresh_sec") not in (None, ""):
        return "fresh_sec"
    if _parse_iso(payload.get("CurrentPriceTime")) is not None:
        return "CurrentPriceTime"
    if _parse_iso(payload.get("AskTime")) is not None:
        return "AskTime"
    if _parse_iso(payload.get("BidTime")) is not None:
        return "BidTime"
    return "default_0"


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _blank() -> dict[str, int]:
    return {
        "n": 0,
        "model_fresh_le_5": 0,
        "model_stale": 0,
        "stale_but_buy1_time_le_5": 0,
        "stale_but_bid_time_le_5": 0,
        "stale_and_buy1_time_gt_5": 0,
        "stale_and_buy1_time_missing": 0,
        "buy1_missing": 0,
        "qty_changed_same_price": 0,
        "price_changed": 0,
        "executable": 0,
        "special_state": 0,
    }


def _take(bucket: dict[str, int], row: dict[str, Any], prev: Optional[dict[str, Any]]) -> None:
    bucket["n"] += 1
    fresh = row["model_fresh"]
    buy_age = row["buy1_time_age"]
    bid_age = row["bid_time_age"]
    if fresh is not None and fresh <= FRESH_SEC + 1e-9:
        bucket["model_fresh_le_5"] += 1
    if fresh is None or fresh > FRESH_SEC + 1e-9:
        bucket["model_stale"] += 1
        if buy_age is not None and buy_age <= FRESH_SEC + 1e-9:
            bucket["stale_but_buy1_time_le_5"] += 1
        elif buy_age is None:
            bucket["stale_and_buy1_time_missing"] += 1
        elif buy_age > FRESH_SEC + 1e-9:
            bucket["stale_and_buy1_time_gt_5"] += 1
        if bid_age is not None and bid_age <= FRESH_SEC + 1e-9:
            bucket["stale_but_bid_time_le_5"] += 1
    if not row["buy1_present"]:
        bucket["buy1_missing"] += 1
    if row["executable"]:
        bucket["executable"] += 1
    if "SPECIAL" in str(row["state"]):
        bucket["special_state"] += 1
    if prev and prev["buy1_present"] and row["buy1_present"]:
        if prev["buy1_price"] != row["buy1_price"] and row["buy1_price"] is not None:
            bucket["price_changed"] += 1
        elif prev["buy1_qty"] != row["buy1_qty"] and prev["buy1_price"] == row["buy1_price"]:
            bucket["qty_changed_same_price"] += 1


def scan_case(case: dict[str, Any]) -> dict[str, Any]:
    capture = _capture_dir(case["day"])
    if capture is None:
        return {"uid": case["uid"], "error": "NO_CAPTURE"}
    code = case["symbol"]
    needle = f'"symbol":"{code}"'
    rows: list[dict[str, Any]] = []
    sign_changes: list[dict[str, Any]] = []
    prev_sign = None
    parsed = 0
    for part in sorted(capture.glob("push_part_*.jsonl")):
        with part.open(encoding="utf-8") as handle:
            for line in handle:
                if needle not in line:
                    continue
                rec = json.loads(line)
                if str(rec.get("symbol") or "") != code:
                    continue
                if int(rec.get("sequence") or 0) <= int(case["seq_after"]):
                    continue
                payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else {}
                if not payload:
                    continue
                received = str(rec.get("received_at") or "")
                event_t = capture_event_epoch({"payload": payload, "received_at": received}, payload)
                if event_t is None:
                    continue
                if float(event_t) + 1.0 < float(case["entry_t"]):
                    continue
                board = _board_row(payload, float(event_t))
                gate = is_executable_continuous_board(payload, event_t=float(event_t))
                buy = _lvl(payload, "Buy1")
                sell = _lvl(payload, "Sell1")
                row = {
                    "t": float(event_t),
                    "received_at": received,
                    "seq": int(rec.get("sequence") or 0),
                    "model_fresh": board["fresh_sec"],
                    "fresh_source": _fresh_source(payload),
                    "cpt": payload.get("CurrentPriceTime"),
                    "cpt_age": _age(float(event_t), payload.get("CurrentPriceTime")),
                    "bid_time": payload.get("BidTime"),
                    "bid_time_age": _age(float(event_t), payload.get("BidTime")),
                    "ask_time": payload.get("AskTime"),
                    "ask_time_age": _age(float(event_t), payload.get("AskTime")),
                    "buy1_present": bool(buy["present"]),
                    "buy1_price": _num(buy.get("price")),
                    "buy1_qty": _num(buy.get("qty")),
                    "buy1_sign": buy.get("sign"),
                    "buy1_time": buy.get("time"),
                    "buy1_time_age": _age(float(event_t), buy.get("time")),
                    "sell1_price": _num(sell.get("price")),
                    "sell1_qty": _num(sell.get("qty")),
                    "sell1_sign": sell.get("sign"),
                    "sell1_time": sell.get("time"),
                    "sell1_time_age": _age(float(event_t), sell.get("time")),
                    "bid_price_field": _num(payload.get("BidPrice")),
                    "ask_price_field": _num(payload.get("AskPrice")),
                    "bid_qty_field": _num(payload.get("BidQty")),
                    "current_price": _num(payload.get("CurrentPrice")),
                    "state": str(gate.get("state") or ""),
                    "executable": bool(gate.get("ok")),
                    "special_field": bool(gate.get("SpecialQuote")),
                    "model_bid": board["bid"],
                    "model_bid_qty": board["bid_qty"],
                }
                rows.append(row)
                sign = str(row["buy1_sign"] or "")
                if sign != prev_sign:
                    sign_changes.append(
                        {
                            "t": _iso(row["t"]),
                            "sign": sign,
                            "state": row["state"],
                            "buy1_price": row["buy1_price"],
                            "buy1_qty": row["buy1_qty"],
                            "buy1_time": row["buy1_time"],
                            "cpt": row["cpt"],
                        }
                    )
                    prev_sign = sign
                parsed += 1
    entry = min(rows, key=lambda item: abs(item["t"] - float(case["entry_t"]))) if rows else None
    after = [item for item in rows if item["t"] + 1e-9 >= float(case["entry_t"])]
    window = [item for item in after if item["received_at"] >= f"{case['day'][:4]}-{case['day'][4:6]}-{case['day'][6:8]}T15:20:00" and item["received_at"] < f"{case['day'][:4]}-{case['day'][4:6]}-{case['day'][6:8]}T15:25:00"]
    before_flat = [item for item in after if item["received_at"] < f"{case['day'][:4]}-{case['day'][4:6]}-{case['day'][6:8]}T15:20:00"]

    def pack(items: list[dict[str, Any]]) -> dict[str, Any]:
        bucket = _blank()
        prev = None
        examples_new_bid = []
        examples_both_old = []
        last_qty_change = None
        last_price_change = None
        for item in items:
            _take(bucket, item, prev)
            stale = item["model_fresh"] is None or item["model_fresh"] > FRESH_SEC + 1e-9
            buy_age = item["buy1_time_age"]
            if stale and buy_age is not None and buy_age <= FRESH_SEC + 1e-9 and len(examples_new_bid) < 4:
                examples_new_bid.append(_public(item))
            if stale and (buy_age is None or buy_age > FRESH_SEC + 1e-9) and len(examples_both_old) < 3:
                examples_both_old.append(_public(item))
            if prev and prev["buy1_present"] and item["buy1_present"] and prev["buy1_price"] == item["buy1_price"] and prev["buy1_qty"] != item["buy1_qty"]:
                last_qty_change = _public(item)
            if prev and item["buy1_price"] != prev["buy1_price"] and item["buy1_price"] is not None:
                last_price_change = _public(item)
            prev = item
        return {
            "counts": bucket,
            "first": None if not items else _public(items[0]),
            "last": None if not items else _public(items[-1]),
            "examples_model_stale_buy1_time_fresh": examples_new_bid,
            "examples_model_stale_buy1_time_not_fresh": examples_both_old,
            "last_same_price_qty_change": last_qty_change,
            "last_price_change": last_price_change,
        }

    fresh_after = [item for item in after if item["model_fresh"] is not None and item["model_fresh"] <= FRESH_SEC + 1e-9 and item["executable"] and item["buy1_qty"] is not None and item["buy1_qty"] >= 100]
    return {
        "uid": case["uid"],
        "notional": case["notional"],
        "entry_px": case["entry_px"],
        "parsed_after_entry_minus_1s": parsed,
        "entry_event": None if entry is None else _public(entry),
        "after_entry": pack(after),
        "before_1520": pack(before_flat),
        "flatten_window": pack(window),
        "last_model_fresh_buy1_ge_100": None if not fresh_after else _public(fresh_after[-1]),
        "sign_changes": sign_changes,
        "fresh_rule": "board_age_sec, else fresh_sec, else event_t minus CurrentPriceTime, else AskTime, else BidTime, else 0. Buy1.Time is not read.",
        "event_clock": "PUSH received_at via capture_event_epoch. A new receive time is not treated as proof that the quote is valid.",
        "fresh_threshold_sec": FRESH_SEC,
        "fresh_threshold_basis": "Frozen event-time quote gate FRESH_SEC=5 and sealed _bid_ok. This study does not relax it.",
    }


def _public(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    out["t_jst"] = _iso(item["t"])
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    body = {"cases": [scan_case(case) for case in CASES]}
    path = OUT / "clock_facts.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    print(path, path.stat().st_size)


if __name__ == "__main__":
    main()
