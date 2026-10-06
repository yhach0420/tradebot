"""AUTO50 research capture plan. Paper universe, strategy, and registration stay untouched.

This module never calls /token, /register, or /unregister. A future handoff may
register only after capture_gate allows it, and only through an injected function.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from universe.opening_screen import volatility_liquidity_score

JST = ZoneInfo("Asia/Tokyo")
MODE_ID = "AUTO50_RESEARCH_CAPTURE_ONLY_V1"
MAX_REGISTER = 50
MARKETS = {"prime", "standard", "growth"}
MIN_CLOSE = 300.0
MAX_TICK_RATIO = 5.0
FAIL_CLOSED = "AUTO50_CAPTURE_FAIL_CLOSED"
ProbeFn = Callable[[str], Mapping[str, Any]]

REQUIRED_PAYLOAD_FIELDS = (
    "CurrentPrice",
    "TradingVolume",
    "OpeningPrice",
    "OpeningPriceTime",
    "CurrentPriceStatus",
    "CurrentPriceTime",
    "AskTime",
    "BidTime",
    "AskSign",
    "BidSign",
    "SpecialQuote",
)
REQUIRED_ENVELOPE_FIELDS = ("received_at_jst", "sequence", "symbol", "original_payload")


def bare(value: object) -> str:
    text = str(value or "").strip().upper()
    if text.endswith(".T"):
        text = text[:-2]
    if "@" in text:
        text = text.split("@", 1)[0]
    return text


def tick_yen(price: float) -> float:
    p = float(price)
    if p <= 3000:
        return 1.0
    if p <= 5000:
        return 5.0
    if p <= 30000:
        return 10.0
    if p <= 50000:
        return 50.0
    if p <= 300000:
        return 100.0
    if p <= 500000:
        return 500.0
    if p <= 3_000_000:
        return 1000.0
    if p <= 5_000_000:
        return 5000.0
    if p <= 30_000_000:
        return 10000.0
    return 100000.0


def sha_body(obj: Mapping[str, Any]) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _num(value: object) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:
        return None
    return out


def eligible_feature(row: Mapping[str, str]) -> Optional[dict[str, Any]]:
    if str(row.get("exchange") or "") != "1":
        return None
    if str(row.get("market") or "").strip().lower() not in MARKETS:
        return None
    close = _num(row.get("close"))
    volume = _num(row.get("volume"))
    trading_value = _num(row.get("trading_value"))
    atr_pct = _num(row.get("atr_pct"))
    if close is None or close < MIN_CLOSE or volume is None or trading_value is None or atr_pct is None:
        return None
    if tick_yen(close) / close * 100.0 > MAX_TICK_RATIO:
        return None
    score = volatility_liquidity_score(atr_pct, trading_value)
    if score is None or not math.isfinite(score):
        return None
    symbol = bare(row.get("symbol"))
    if not symbol:
        return None
    return {
        "symbol": symbol,
        "score": float(score),
        "prior_close": close,
        "prior_volume": volume,
        "prior_trading_value": trading_value,
        "prior_range_pct": atr_pct,
        "market": str(row.get("market") or ""),
        "exchange": "1",
    }


def rank_auto50(features_csv: Path, *, fixed: Sequence[str] | None = None) -> dict[str, Any]:
    """Score-descending Top50. Fixed names stay empty unless the caller passes them."""
    chosen_fixed = [bare(s) for s in (fixed or []) if bare(s)]
    rows: list[dict[str, Any]] = []
    with Path(features_csv).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            item = eligible_feature(row)
            if item is not None:
                rows.append(item)
    rows.sort(key=lambda item: (-float(item["score"]), item["symbol"]))
    by_symbol = {item["symbol"]: item for item in rows}
    raw: list[dict[str, Any]] = []
    for symbol in chosen_fixed:
        item = by_symbol.get(symbol)
        if item is None:
            continue
        raw.append({**item, "origin": "FIXED_REQUIRED"})
    for item in rows:
        if len(raw) >= MAX_REGISTER:
            break
        if any(existing["symbol"] == item["symbol"] for existing in raw):
            continue
        raw.append({**item, "origin": "AUTO"})
    for index, item in enumerate(raw, start=1):
        item["rank"] = index
    return {
        "raw_auto50": raw,
        "eligible_n": len(rows),
        "fixed_requested": chosen_fixed,
        "fixed_included": [item["symbol"] for item in raw if item["origin"] == "FIXED_REQUIRED"],
        "score_id": "AUTO50_M0",
    }


def select_valid_auto50(ranked: Sequence[Mapping[str, Any]], probe_fn: ProbeFn) -> dict[str, Any]:
    """Drop terminal-invalid names and refill from the next ranked valid name. No blacklist."""
    valid: list[dict[str, Any]] = []
    drops: list[dict[str, Any]] = []
    refills: list[dict[str, Any]] = []
    primary = {str(item["symbol"]) for item in list(ranked)[:MAX_REGISTER]}
    for item in ranked:
        if len(valid) >= MAX_REGISTER:
            break
        symbol = str(item["symbol"])
        got = dict(probe_fn(symbol) or {})
        verdict = str(got.get("verdict") or "")
        if verdict == "VALID_SYMBOL" or got.get("ok") is True and verdict not in {
            "INVALID_SYMBOL",
            "AUTH_NOT_READY",
            "RATE_LIMIT",
            "TRANSPORT_FAILURE",
            "TEMPORARY_UNKNOWN",
        }:
            kept = dict(item)
            kept["validation_status"] = "VALID_SYMBOL"
            if symbol not in primary:
                kept["refill"] = True
                refills.append({"symbol": symbol, "reason": "REFILL_AFTER_INVALID"})
            valid.append(kept)
            continue
        if verdict == "INVALID_SYMBOL":
            drops.append({"symbol": symbol, "reason": "INVALID_SYMBOL", "rank": item.get("rank")})
            continue
        return {
            "ok": False,
            "reason": verdict or "TEMPORARY_UNKNOWN",
            "fail_closed": True,
            "valid_auto50": valid,
            "drop_reason": drops,
            "refill_reason": refills,
        }
    ok = len(valid) == MAX_REGISTER
    for index, item in enumerate(valid, start=1):
        item["rank"] = index
    return {
        "ok": ok,
        "reason": "" if ok else "EXACT50_NOT_FILLED",
        "fail_closed": not ok,
        "valid_auto50": valid,
        "drop_reason": drops,
        "refill_reason": refills,
    }


def freeze_manifest(day: str, valid_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [
        {
            "date": day,
            "rank": int(item["rank"]),
            "symbol": str(item["symbol"]),
            "score": item["score"],
            "prior_close": item.get("prior_close"),
            "prior_volume": item.get("prior_volume"),
            "prior_trading_value": item.get("prior_trading_value"),
            "prior_range_pct": item.get("prior_range_pct"),
            "validation_status": item.get("validation_status") or "UNVALIDATED",
        }
        for item in valid_rows
    ]
    body = {"MODE": MODE_ID, "MAX_REGISTER": MAX_REGISTER, "rows": rows}
    return {"frozen_at": datetime.now(JST).isoformat(timespec="milliseconds"), "manifest_sha256": sha_body(body), "body": body}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def operational_validation_status(native_root: Path) -> dict[str, Any]:
    closure = Path(native_root) / "results" / "operations" / "20261005_runtime_closure_v1" / "report.json"
    doc = _read_json(closure)
    nxt = str(doc.get("NEXT") or "")
    marker = Path(native_root) / "results" / "operations" / "current_runtime_operational_validation_closed.json"
    if marker.is_file() and _read_json(marker).get("closed") is True:
        return {"closed": True, "reason": "closure_marker", "next": nxt}
    return {
        "closed": False,
        "reason": "NEXT_SESSION_OPERATIONAL_VALIDATION",
        "next": nxt or "NEXT_SESSION_OPERATIONAL_VALIDATION",
        "source": str(closure),
    }


def capture_gate(native_root: Path, trading_date: str) -> dict[str, Any]:
    """Fail closed while Paper or its registration owner is active, or while validation is open."""
    root = Path(native_root)
    validation = operational_validation_status(root)
    owned: dict[str, Any] = {"owned": True, "reason": "ownership_check_failed"}
    try:
        from small_paper.kabu_registration_authority import ingress_owns_kabu_registration

        owned = ingress_owns_kabu_registration(root, str(trading_date))
    except Exception:
        owned = {"owned": True, "reason": "ownership_check_failed"}
    owner_file = _read_json(root / "runtime" / "kabu_registration_owner.json")
    owner_name = str(owner_file.get("owner") or owner_file.get("websocket_owner") or "")
    same_day_owner = str(owner_file.get("trading_date") or "") == str(trading_date) and owner_name not in {"", "NONE"}
    if not validation["closed"] or owned.get("owned") or same_day_owner:
        reason = FAIL_CLOSED
        if not validation["closed"]:
            detail = "CURRENT_PAPER_OPERATIONAL_VALIDATION_FIRST"
        elif owned.get("owned"):
            detail = "PAPER_REGISTRATION_OWNER_ACTIVE"
        else:
            detail = "PAPER_OWNER_FILE_ACTIVE"
        return {
            "allow": False,
            "reason": reason,
            "detail": detail,
            "validation": validation,
            "registration_owned": bool(owned.get("owned")),
            "registration_reason": owned.get("reason"),
            "registered": False,
            "submit": 0,
            "cancel": 0,
            "live": 0,
        }
    return {
        "allow": True,
        "reason": "GATES_CLEAR",
        "detail": "",
        "validation": validation,
        "registration_owned": False,
        "registered": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
    }


def execute_research_capture(
    gate: Mapping[str, Any],
    ranked_rows: Sequence[Mapping[str, Any]],
    *,
    trading_date: str,
    probe_fn: ProbeFn,
    out_dir: Path,
    register_fn: Optional[Callable[[Sequence[str]], None]] = None,
) -> dict[str, Any]:
    """Freeze a research manifest. Registration runs only after the gate allows it."""
    if not gate.get("allow"):
        return {
            "ok": False,
            "reason": gate.get("reason") or FAIL_CLOSED,
            "detail": gate.get("detail") or "",
            "registered": False,
            "kabu_called": False,
            "submit": 0,
            "cancel": 0,
            "live": 0,
        }
    selected = select_valid_auto50(ranked_rows, probe_fn)
    if not selected["ok"]:
        return {
            "ok": False,
            "reason": selected["reason"] or FAIL_CLOSED,
            "raw_auto50": [row["symbol"] for row in ranked_rows[:MAX_REGISTER]],
            "valid_auto50": [row["symbol"] for row in selected["valid_auto50"]],
            "drop_reason": selected["drop_reason"],
            "refill_reason": selected["refill_reason"],
            "registered": False,
            "kabu_called": False,
            "submit": 0,
            "cancel": 0,
            "live": 0,
        }
    manifest = freeze_manifest(trading_date, selected["valid_auto50"])
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"auto50_research_freeze_{trading_date}.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    symbols = [row["symbol"] for row in selected["valid_auto50"]]
    if register_fn is None:
        return {
            "ok": True,
            "reason": "FROZEN_REGISTRATION_NOT_REQUESTED",
            "freeze_path": str(path),
            "manifest_sha256": manifest["manifest_sha256"],
            "valid_auto50": symbols,
            "drop_reason": selected["drop_reason"],
            "refill_reason": selected["refill_reason"],
            "registered": False,
            "kabu_called": False,
            "submit": 0,
            "cancel": 0,
            "live": 0,
        }
    register_fn(symbols)
    return {
        "ok": True,
        "reason": "REGISTERED_BY_INJECTED_OWNER",
        "freeze_path": str(path),
        "manifest_sha256": manifest["manifest_sha256"],
        "valid_auto50": symbols,
        "registered": True,
        "kabu_called": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
    }


def plan_capture(native_root: Path, trading_date: str, features_csv: Path) -> dict[str, Any]:
    """Build the ranked book and refuse registration. Does not touch Kabu."""
    gate = capture_gate(native_root, trading_date)
    ranked = rank_auto50(features_csv, fixed=[])
    return {
        "mode": MODE_ID,
        "trading_date": trading_date,
        "gate": gate,
        "eligible_n": ranked["eligible_n"],
        "raw_n": len(ranked["raw_auto50"]),
        "fixed_required": [],
        "registered": False,
        "kabu_called": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
    }


def exact_capture_record(payload: Mapping[str, Any], *, symbol: str, received_at_jst: str, sequence: int) -> dict[str, Any]:
    """Envelope for a future AUTO50 research tape. Raw PUSH payload stays intact."""
    pay = dict(payload)
    buy = pay.get("Buy1") if isinstance(pay.get("Buy1"), dict) else {}
    sell = pay.get("Sell1") if isinstance(pay.get("Sell1"), dict) else {}
    return {
        "mode": MODE_ID,
        "received_at": received_at_jst,
        "received_at_jst": received_at_jst,
        "sequence": int(sequence),
        "symbol": bare(symbol),
        "CurrentPrice": pay.get("CurrentPrice"),
        "TradingVolume": pay.get("TradingVolume"),
        "Sell1": sell,
        "Buy1": buy,
        "OpeningPrice": pay.get("OpeningPrice"),
        "OpeningPriceTime": pay.get("OpeningPriceTime"),
        "CurrentPriceStatus": pay.get("CurrentPriceStatus"),
        "CurrentPriceTime": pay.get("CurrentPriceTime"),
        "AskTime": pay.get("AskTime"),
        "BidTime": pay.get("BidTime"),
        "AskSign": pay.get("AskSign"),
        "BidSign": pay.get("BidSign"),
        "SpecialQuote": pay.get("SpecialQuote"),
        "original_payload": pay,
    }


def to_x1_event(record: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    payload = record.get("original_payload") if isinstance(record.get("original_payload"), dict) else {}
    if not payload and isinstance(record.get("payload"), dict):
        payload = record["payload"]
    received = record.get("received_at") or record.get("received_at_jst") or record.get("received_at_utc")
    symbol = bare(record.get("symbol") or payload.get("Symbol"))
    if not symbol or not received or not payload:
        return None
    sequence = record.get("sequence")
    return {
        "symbol": symbol,
        "payload": payload,
        "received_at": received,
        "ingest_sequence": sequence,
    }


def contract_gaps(record: Mapping[str, Any]) -> list[str]:
    payload = record.get("original_payload") if isinstance(record.get("original_payload"), dict) else {}
    missing = [name for name in REQUIRED_ENVELOPE_FIELDS if name not in record and name != "received_at_jst"]
    if not (record.get("received_at_jst") or record.get("received_at")):
        missing.append("received_at_jst")
    if "sequence" not in record:
        missing.append("sequence")
    buy = payload.get("Buy1") if isinstance(payload.get("Buy1"), dict) else {}
    sell = payload.get("Sell1") if isinstance(payload.get("Sell1"), dict) else {}
    if "Price" not in sell or "Qty" not in sell:
        missing.append("Sell1")
    if "Price" not in buy or "Qty" not in buy:
        missing.append("Buy1")
    for name in REQUIRED_PAYLOAD_FIELDS:
        if name == "SpecialQuote":
            continue
        if name not in payload:
            missing.append(name)
    for sign_name, nest in (("Buy1.Sign", buy), ("Sell1.Sign", sell)):
        if "Sign" not in nest:
            missing.append(sign_name)
    return missing
