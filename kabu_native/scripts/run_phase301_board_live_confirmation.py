#!/usr/bin/env python3
"""
Phase301: live confirmation that Board:mid pre-gate works on real PUSH data.

Read-only probe; no production logic changes.
Output: kabu_native/results/reports/phase301_board_live_confirmation.json
"""

from __future__ import annotations

import json
import socket
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase301_board_live_confirmation.json"
JST = ZoneInfo("Asia/Tokyo")
DEFAULT_SYMBOL = "9984"
DEFAULT_EXCHANGE = 1
PUSH_WAIT_SEC = 45.0


def _bootstrap() -> None:
    native = REPO / "kabu_native" / "src"
    for p in (native, REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _tcp_reachable(base_url: str, *, timeout: float = 3.0) -> dict[str, Any]:
    host = urlparse(base_url).hostname or "localhost"
    port = urlparse(base_url).port or 18080
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ok = True
            err = ""
    except OSError as exc:
        ok = False
        err = str(exc)
    return {"host": host, "port": port, "reachable": ok, "error": err}


def _pregate_from_payload(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    """Mirror pilot_runner gate-pregate path (no production edits)."""
    from small_paper.board_imbalance_shadow import compute_entry_order_book_imbalance_field
    from small_paper.daytrade_suitability_gate import attach_entry_metrics_to_trade
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields
    from small_paper.extended_entry_shadow import compute_entry_high_break_recent_field
    from small_paper.live_feature_bridge import LiveFeatureBridge
    from research.continuation_quality_ranking import continuation_quality_score

    bridge = LiveFeatureBridge()
    snap = bridge.update(symbol, payload)
    enriched = bridge.enrich_payload(payload, snap)

    trade: dict[str, Any] = {
        "profile": "momentum_volume_v13_combined",
        "symbol": symbol,
        "current_price": enriched.get("CurrentPrice") or enriched.get("current_price"),
        "momentum_continuation_score": snap.momentum_continuation_score,
        "max_continuation_duration": snap.max_continuation_duration,
    }
    attach_entry_metrics_to_trade(trade, enriched)
    trade["continuation_quality_score"] = round(continuation_quality_score(trade), 4)
    trade.update(LiveFeatureBridge.trade_quality_extras(trade, snap))

    try:
        px = float(payload.get("CurrentPrice") or 0)
    except (TypeError, ValueError):
        px = 0.0
    trade.update(
        compute_entry_high_break_recent_field(
            trade=trade,
            payload=payload,
            price_ring=[],
            entry_ts=time.time(),
        )
    )
    board_pregate = compute_entry_order_book_imbalance_field(payload=enriched)
    trade.update(board_pregate)
    trade.update(compute_entry_expectancy_score_fields(trade=trade))

    candidate_event = {
        "event_type": "candidate",
        "symbol": trade.get("symbol"),
        "entry_order_book_imbalance": trade.get("entry_order_book_imbalance"),
        "entry_board_mid_token_active": trade.get("entry_board_mid_token_active"),
        "entry_expectancy_score_v2": trade.get("entry_expectancy_score_v2"),
    }
    reject_event = {
        "event_type": "rejected",
        "gate_reject_reason": "entry_score_v2_below_threshold",
        "symbol": trade.get("symbol"),
        "entry_order_book_imbalance": trade.get("entry_order_book_imbalance"),
        "entry_board_mid_token_active": trade.get("entry_board_mid_token_active"),
        "entry_expectancy_score_v2": trade.get("entry_expectancy_score_v2"),
    }
    return {
        "enriched_bid_qty": enriched.get("BidQty"),
        "enriched_ask_qty": enriched.get("AskQty"),
        "gate_pregate_log": {
            "entry_order_book_imbalance": trade.get("entry_order_book_imbalance"),
            "entry_board_mid_token_active": trade.get("entry_board_mid_token_active"),
            "entry_expectancy_score_v2": trade.get("entry_expectancy_score_v2"),
        },
        "candidate_event_sample": candidate_event,
        "reject_event_sample": reject_event,
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    from api.kabu_register import register_symbols_cleared
    from api.push_client import KabuNativePushClient
    from api.rest_client import (
        KabuNativeApiError,
        KabuNativeRestClient,
        build_symbol_key,
        default_base_url,
        load_kabu_env,
    )

    base_url = default_base_url().rstrip("/")
    symbol_code = DEFAULT_SYMBOL
    exchange = DEFAULT_EXCHANGE
    symbol_key = build_symbol_key(symbol_code, exchange)
    symbol = f"{symbol_code}.T"

    report: dict[str, Any] = {
        "phase": 301,
        "title": "board_live_confirmation",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "constraint": "confirmation only; no production logic changes",
        "symbol": symbol,
        "symbol_key": symbol_key,
        "base_url": base_url,
    }

    tcp = _tcp_reachable(base_url)
    report["1_tcp_connection"] = tcp
    if not tcp["reachable"]:
        report["status"] = "failed_tcp_unreachable"
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {OUT} status=failed_tcp_unreachable", flush=True)
        return 1

    load_kabu_env(repo_root=REPO)
    rest = KabuNativeRestClient(base_url=base_url, timeout=15.0, max_retries=2)
    token: Optional[str] = None
    try:
        token = rest.issue_token_from_env()
        report["2_token_issue"] = {"ok": True}
    except KabuNativeApiError as exc:
        report["2_token_issue"] = {"ok": False, "error": str(exc)}
        report["status"] = "failed_token"
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {OUT} status=failed_token", flush=True)
        return 1

    rest_board: dict[str, Any] = {}
    try:
        board = rest.get_board(symbol_key, token=token)
        rest_board = {
            "BidQty": board.get("BidQty"),
            "AskQty": board.get("AskQty"),
            "CurrentPrice": board.get("CurrentPrice"),
        }
        report["2b_rest_board_sample"] = rest_board
    except KabuNativeApiError as exc:
        report["2b_rest_board_sample"] = {"error": str(exc)}

    push_client = KabuNativePushClient(rest, token)
    register_symbols_cleared(push_client, [(symbol_code, exchange)])
    report["3_push_register"] = {"symbol": symbol_code, "exchange": exchange, "ok": True}

    selected_payload: Optional[dict[str, Any]] = None
    messages_seen = 0
    payloads_with_bid_ask: list[dict[str, Any]] = []
    push_err: Optional[str] = None
    started = time.monotonic()

    async def _collect_push_timed() -> tuple[list[dict[str, Any]], Optional[str]]:
        import asyncio

        import websockets

        out: list[dict[str, Any]] = []
        deadline = time.monotonic() + PUSH_WAIT_SEC
        ws_url = push_client.websocket_url
        try:
            async with websockets.connect(
                ws_url,
                open_timeout=10.0,
                close_timeout=5.0,
                ping_timeout=None,
            ) as ws:
                while time.monotonic() < deadline:
                    remaining = max(0.5, deadline - time.monotonic())
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=min(3.0, remaining))
                    except asyncio.TimeoutError:
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    payload = json.loads(raw)
                    if isinstance(payload, dict):
                        out.append(payload)
                        sym = str(payload.get("Symbol") or "")
                        if sym == symbol_code and payload.get("BidQty") is not None and payload.get("AskQty") is not None:
                            return out, None
        except Exception as exc:
            return out, str(exc)
        return out, "push_wait_timeout"

    try:
        import asyncio

        push_messages, push_err = asyncio.run(_collect_push_timed())
        for msg in push_messages:
            messages_seen += 1
            if str(msg.get("Symbol") or "") not in (symbol_code, symbol_code.replace(".T", "")):
                continue
            bid = msg.get("BidQty")
            ask = msg.get("AskQty")
            if bid is not None and ask is not None:
                payloads_with_bid_ask.append(
                    {
                        "message_index": messages_seen,
                        "BidQty": bid,
                        "AskQty": ask,
                        "CurrentPrice": msg.get("CurrentPrice"),
                        "CurrentPriceTime": msg.get("CurrentPriceTime"),
                    }
                )
                if selected_payload is None:
                    selected_payload = dict(msg)
                    break
    finally:
        try:
            push_client.unregister_all()
        except Exception:
            pass

    report["4_push_receive"] = {
        "messages_seen": messages_seen,
        "wait_sec": round(time.monotonic() - started, 2),
        "push_error": push_err,
        "payloads_with_bid_ask_count": len(payloads_with_bid_ask),
        "first_payloads_with_bid_ask": payloads_with_bid_ask[:5],
    }

    if selected_payload is None and rest_board.get("BidQty") is not None:
        selected_payload = {
            "Symbol": symbol_code,
            "CurrentPrice": rest_board.get("CurrentPrice"),
            "CurrentPriceTime": datetime.now(JST).isoformat(),
            "BidQty": rest_board.get("BidQty"),
            "AskQty": rest_board.get("AskQty"),
            "_source": "rest_board_fallback",
        }
        report["4_push_receive"]["fallback_used"] = "rest_board"

    if selected_payload is None:
        report["status"] = "failed_no_payload_with_bid_ask"
        report["confirmed_values"] = None
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {OUT} status=failed_no_payload", flush=True)
        return 1

    pregate = _pregate_from_payload(selected_payload, symbol)
    report["5_gate_pregate_log"] = pregate["gate_pregate_log"]
    report["6_event_samples"] = {
        "candidate": pregate["candidate_event_sample"],
        "rejected": pregate["reject_event_sample"],
    }
    report["confirmed_values"] = {
        "payload_BidQty": selected_payload.get("BidQty"),
        "payload_AskQty": selected_payload.get("AskQty"),
        "entry_order_book_imbalance": pregate["gate_pregate_log"]["entry_order_book_imbalance"],
        "entry_board_mid_token_active": pregate["gate_pregate_log"]["entry_board_mid_token_active"],
        "payload_source": selected_payload.get("_source", "push"),
    }
    report["checks"] = {
        "payload_has_bid_qty": selected_payload.get("BidQty") is not None,
        "payload_has_ask_qty": selected_payload.get("AskQty") is not None,
        "entry_order_book_imbalance_not_null": pregate["gate_pregate_log"]["entry_order_book_imbalance"] is not None,
        "entry_board_mid_token_active_is_bool": isinstance(
            pregate["gate_pregate_log"]["entry_board_mid_token_active"], bool
        ),
        "reject_event_imbalance_not_null": pregate["reject_event_sample"]["entry_order_book_imbalance"] is not None,
        "candidate_event_imbalance_not_null": pregate["candidate_event_sample"]["entry_order_book_imbalance"] is not None,
    }
    ok = all(report["checks"].values())
    report["status"] = "confirmed" if ok else "partial"
    report["verdict"] = (
        "Board pre-gate fields populated on live data"
        if ok
        else "Live payload received but one or more board checks failed"
    )

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    print(f"status={report['status']} imb={report['confirmed_values']['entry_order_book_imbalance']}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
