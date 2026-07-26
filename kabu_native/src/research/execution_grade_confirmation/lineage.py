"""Board quote lineage audit (kabu BidPrice/AskPrice vs Buy1/Sell1)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from research.execution_grade_confirmation.constants import CAPTURE_ROOT


def _lvl(op: dict, side: str, i: int = 1) -> tuple[Optional[float], Optional[float]]:
    lv = op.get(f"{side}{i}")
    if not isinstance(lv, dict):
        return None, None
    try:
        px = float(lv["Price"]) if lv.get("Price") is not None else None
    except (TypeError, ValueError):
        px = None
    try:
        qty = float(lv["Qty"]) if lv.get("Qty") is not None else None
    except (TypeError, ValueError):
        qty = None
    return px, qty


def audit_one_payload(rec: dict[str, Any]) -> dict[str, Any]:
    op = rec.get("original_payload") or {}
    if not isinstance(op, dict):
        op = {}
    bid_price = op.get("BidPrice", rec.get("bid"))
    ask_price = op.get("AskPrice", rec.get("ask"))
    buy1, buy1q = _lvl(op, "Buy", 1)
    sell1, sell1q = _lvl(op, "Sell", 1)
    try:
        bp = float(bid_price) if bid_price is not None else None
        ap = float(ask_price) if ask_price is not None else None
    except (TypeError, ValueError):
        bp = ap = None

    bid_eq_sell1 = bp is not None and sell1 is not None and abs(bp - sell1) < 1e-9
    ask_eq_buy1 = ap is not None and buy1 is not None and abs(ap - buy1) < 1e-9
    kabu_crossed = bp is not None and ap is not None and ap <= bp
    true_ok = buy1 is not None and sell1 is not None and sell1 > buy1

    mapping = "FIELD_MAPPING_ERROR"
    if bid_eq_sell1 and ask_eq_buy1:
        mapping = "KABU_BIDPRICE_IS_SELL1_ASKPRICE_IS_BUY1"
    elif bp is not None and ap is not None and buy1 is None:
        mapping = "NO_DEPTH_LEVELS"

    return {
        "symbol": rec.get("symbol") or op.get("Symbol"),
        "sequence": rec.get("sequence"),
        "received_at_jst": rec.get("received_at_jst"),
        "envelope_bid": rec.get("bid"),
        "envelope_ask": rec.get("ask"),
        "BidPrice": bp,
        "AskPrice": ap,
        "BidQty": op.get("BidQty"),
        "AskQty": op.get("AskQty"),
        "Buy1": buy1,
        "Buy1Qty": buy1q,
        "Sell1": sell1,
        "Sell1Qty": sell1q,
        "BidPrice_eq_Sell1": bid_eq_sell1,
        "AskPrice_eq_Buy1": ask_eq_buy1,
        "kabu_named_crossed": kabu_crossed,
        "true_book_ask_gt_bid": true_ok,
        "same_payload_atomic": bool(op),
        "mapping_class": mapping,
        "true_best_bid": buy1,
        "true_best_ask": sell1,
        "true_bid_qty": buy1q,
        "true_ask_qty": sell1q,
    }


def sample_capture_payloads(day: str, *, n: int = 20) -> list[dict[str, Any]]:
    d = CAPTURE_ROOT / day
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for part in sorted(d.glob("push_part_*.jsonl")):
        if part.stat().st_size == 0:
            continue
        with part.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not rec.get("original_payload"):
                    continue
                out.append(audit_one_payload(rec))
                if len(out) >= n:
                    return out
    return out


def lineage_report(days: list[str]) -> dict[str, Any]:
    samples = []
    for day in days:
        samples.extend(sample_capture_payloads(day, n=15))
    n = len(samples)
    if n == 0:
        return {
            "n_samples": 0,
            "raw_board_atomic": "RAW_BOARD_ATOMIC_NOT_AVAILABLE",
            "quote_lineage": "QUOTE_LINEAGE_BLOCKED",
            "field_mapping": "UNKNOWN",
            "crossed_root_cause": "UNKNOWN",
            "samples": [],
        }
    eq_sell = sum(1 for s in samples if s.get("BidPrice_eq_Sell1"))
    eq_buy = sum(1 for s in samples if s.get("AskPrice_eq_Buy1"))
    kabu_x = sum(1 for s in samples if s.get("kabu_named_crossed"))
    true_ok = sum(1 for s in samples if s.get("true_book_ask_gt_bid"))
    atomic = sum(1 for s in samples if s.get("same_payload_atomic"))
    mapping_pass = eq_sell / n >= 0.95 and eq_buy / n >= 0.95
    return {
        "n_samples": n,
        "BidPrice_eq_Sell1_rate": round(eq_sell / n, 4),
        "AskPrice_eq_Buy1_rate": round(eq_buy / n, 4),
        "kabu_named_crossed_rate": round(kabu_x / n, 4),
        "true_book_valid_rate": round(true_ok / n, 4),
        "same_payload_atomic_rate": round(atomic / n, 4),
        "raw_board_atomic": "RAW_BOARD_ATOMIC_AVAILABLE" if atomic / n >= 0.95 else "RAW_BOARD_ATOMIC_NOT_AVAILABLE",
        "quote_lineage": "QUOTE_LINEAGE_PASS" if mapping_pass and atomic / n >= 0.95 else "QUOTE_LINEAGE_BLOCKED",
        "field_mapping": "KABU_BIDPRICE_IS_SELL1_ASKPRICE_IS_BUY1" if mapping_pass else "FIELD_MAPPING_ERROR",
        "field_mapping_pass": mapping_pass,
        "crossed_root_cause": "FIELD_MAPPING_ERROR",
        "note": (
            "kabu API BidPrice/BidQty = 最良売気配(Sell1); AskPrice/AskQty = 最良買気配(Buy1). "
            "English bid/ask must be reconstructed from Buy1/Sell1 on the same original_payload. "
            "1s aggregation is NOT the root cause of crossed quotes."
        ),
        "aggregation_audit": {
            "uses_last_tick_bid_ask": True,
            "forward_fill": False,
            "aggregation_induced_crossed": "SECONDARY_ONLY",
            "primary_cause": "FIELD_MAPPING_ERROR",
        },
        "samples": samples[:40],
        "field_lineage": [
            {"field": "CurrentPrice", "source": "original_payload.CurrentPrice / envelope.current_price", "same_payload": True},
            {"field": "CurrentPriceTime", "source": "original_payload.CurrentPriceTime", "same_payload": True},
            {"field": "kabu_BidPrice", "source": "original_payload.BidPrice", "maps_to": "Sell1 (true ask)", "same_payload": True},
            {"field": "kabu_AskPrice", "source": "original_payload.AskPrice", "maps_to": "Buy1 (true bid)", "same_payload": True},
            {"field": "true_best_bid", "source": "original_payload.Buy1.Price", "same_payload": True},
            {"field": "true_best_ask", "source": "original_payload.Sell1.Price", "same_payload": True},
            {"field": "bid_qty", "source": "original_payload.Buy1.Qty", "same_payload": True},
            {"field": "ask_qty", "source": "original_payload.Sell1.Qty", "same_payload": True},
            {"field": "received_at", "source": "envelope.received_at_jst", "same_payload": True},
            {"field": "sequence", "source": "envelope.sequence", "same_payload": True},
            {"field": "PushTick.bid/ask", "source": "push_loader literal BidPrice/AskPrice", "inverted": True},
        ],
    }
