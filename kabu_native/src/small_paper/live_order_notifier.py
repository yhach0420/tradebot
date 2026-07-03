"""
Phase594: Live order notifier — JSONL / Discord visibility for live order pipeline.

Never raises; failures are logged to live_order_error.jsonl.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

EVENT_ENTRY_SIGNAL = "ENTRY_SIGNAL"
EVENT_CAPITAL_CHECK_PASS = "CAPITAL_CHECK_PASS"
EVENT_CAPITAL_CHECK_BLOCK = "CAPITAL_CHECK_BLOCK"
EVENT_ORDER_PREPARED = "ORDER_PREPARED"
EVENT_ORDER_WOULD_SEND = "ORDER_WOULD_SEND"
EVENT_ORDER_ACCEPTED_DRYRUN = "ORDER_ACCEPTED_DRYRUN"
EVENT_PARTIAL_FILLED_DRYRUN = "PARTIAL_FILLED_DRYRUN"
EVENT_FILLED_DRYRUN = "FILLED_DRYRUN"
EVENT_OPEN_POSITION_DRYRUN = "OPEN_POSITION_DRYRUN"
EVENT_EXIT_SIGNAL = "EXIT_SIGNAL"
EVENT_EXIT_ORDER_PREPARED = "EXIT_ORDER_PREPARED"
EVENT_EXIT_WOULD_SEND = "EXIT_WOULD_SEND"
EVENT_EXIT_FILLED_DRYRUN = "EXIT_FILLED_DRYRUN"
EVENT_CLOSED_DRYRUN = "CLOSED_DRYRUN"
EVENT_SAFE_STOP = "SAFE_STOP"
EVENT_API_ERROR = "API_ERROR"
EVENT_POSITION_MISMATCH = "POSITION_MISMATCH"
EVENT_CANCEL_FAILED = "CANCEL_FAILED"

ALL_EVENT_TYPES = (
    EVENT_ENTRY_SIGNAL,
    EVENT_CAPITAL_CHECK_PASS,
    EVENT_CAPITAL_CHECK_BLOCK,
    EVENT_ORDER_PREPARED,
    EVENT_ORDER_WOULD_SEND,
    EVENT_ORDER_ACCEPTED_DRYRUN,
    EVENT_PARTIAL_FILLED_DRYRUN,
    EVENT_FILLED_DRYRUN,
    EVENT_OPEN_POSITION_DRYRUN,
    EVENT_EXIT_SIGNAL,
    EVENT_EXIT_ORDER_PREPARED,
    EVENT_EXIT_WOULD_SEND,
    EVENT_EXIT_FILLED_DRYRUN,
    EVENT_CLOSED_DRYRUN,
    EVENT_SAFE_STOP,
    EVENT_API_ERROR,
    EVENT_POSITION_MISMATCH,
    EVENT_CANCEL_FAILED,
)

EVENT_CSV_FIELDS = (
    "timestamp",
    "event_type",
    "symbol",
    "side",
    "state_from",
    "state_to",
    "can_enter",
    "reject_reason",
    "required_margin",
    "available_margin",
    "cap_used",
    "cap_limit",
    "qty",
    "price",
    "latency_ms",
    "dry_run",
    "order_enabled",
    "linked_paper_trade_id",
    "detail",
)


def notifier_enabled(config: Any) -> bool:
    if bool(getattr(config, "live_trading_enabled", False)):
        return False
    return bool(getattr(config, "live_order_notifier_enabled", True))


def jsonl_enabled(config: Any) -> bool:
    return bool(getattr(config, "live_order_jsonl_enabled", True))


def discord_enabled(config: Any) -> bool:
    return bool(getattr(config, "live_order_discord_enabled", False))


def _iso_now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _fmt_yen(v: Any) -> str:
    try:
        return f"{float(v):,.0f}円"
    except (TypeError, ValueError):
        return str(v)


def format_discord_message(event_type: str, data: Mapping[str, Any]) -> str:
    sym = str(data.get("symbol") or "")
    side = str(data.get("side") or "ENTRY").upper()

    if event_type == EVENT_CAPITAL_CHECK_PASS:
        return "\n".join(
            [
                "[LIVE ORDER]",
                f"{sym} {side}",
                "",
                "Capital: PASS",
                f"Required: {_fmt_yen(data.get('required_margin'))}",
                f"Available: {_fmt_yen(data.get('available_margin'))}",
                f"CAP: {data.get('cap_used')}/{data.get('cap_limit')}",
                f"Order: {data.get('order_phase') or 'WOULD_SEND'}",
                f"Qty: {data.get('qty') or 100}",
                f"Price: {_fmt_yen(data.get('price'))}",
                f"Latency: {data.get('latency_ms')}ms",
            ]
        )

    if event_type == EVENT_CAPITAL_CHECK_BLOCK:
        req = float(data.get("required_margin") or 0)
        avail = float(data.get("available_margin") or 0)
        shortage = max(0.0, req - avail)
        return "\n".join(
            [
                "[LIVE CAPITAL BLOCK]",
                f"{sym} {side}",
                "",
                f"Required: {_fmt_yen(req)}",
                f"Available: {_fmt_yen(avail)}",
                f"Shortage: {_fmt_yen(shortage)}",
                f"Reason: {data.get('reject_reason') or 'blocked'}",
            ]
        )

    if event_type == EVENT_SAFE_STOP:
        return "\n".join(
            [
                "[LIVE SAFE STOP]",
                f"Reason: {data.get('reason') or data.get('detail') or 'unknown'}",
                "New ENTRY: blocked",
                "Manual check required",
            ]
        )

    if event_type in (EVENT_ORDER_WOULD_SEND, EVENT_EXIT_WOULD_SEND):
        return "\n".join(
            [
                "[LIVE ORDER]",
                f"{sym} {event_type.replace('_', ' ')}",
                f"Qty: {data.get('qty') or 100}",
                f"Price: {_fmt_yen(data.get('price'))}",
                f"dry_run={data.get('dry_run', True)}",
            ]
        )

    return f"[LIVE ORDER] {event_type} {sym} {data.get('detail') or ''}".strip()


@dataclass
class LiveOrderNotifier:
    events: list[dict[str, Any]] = field(default_factory=list)
    discord_messages: list[str] = field(default_factory=list)
    error_count: int = 0

    def emit(
        self,
        event_type: str,
        data: Mapping[str, Any],
        *,
        writer: Any,
        config: Any,
        discord_send: Optional[Callable[[str], None]] = None,
    ) -> None:
        if not notifier_enabled(config):
            return
        row = {
            "timestamp": str(data.get("timestamp") or _iso_now()),
            "event_type": event_type,
            "symbol": data.get("symbol"),
            "side": data.get("side"),
            "state_from": data.get("state_from"),
            "state_to": data.get("state_to"),
            "can_enter": data.get("can_enter"),
            "reject_reason": data.get("reject_reason"),
            "required_margin": data.get("required_margin"),
            "available_margin": data.get("available_margin"),
            "cap_used": data.get("cap_used"),
            "cap_limit": data.get("cap_limit"),
            "qty": data.get("qty"),
            "price": data.get("price"),
            "latency_ms": data.get("latency_ms"),
            "dry_run": bool(getattr(config, "dry_run", True)),
            "order_enabled": bool(getattr(config, "order_enabled", False)),
            "linked_paper_trade_id": data.get("linked_paper_trade_id"),
            "detail": data.get("detail"),
            "payload": data.get("payload"),
        }
        self.events.append(dict(row))
        try:
            if jsonl_enabled(config) and writer is not None:
                writer.append_live_order_event(row)
                if data.get("state_to"):
                    writer.append_live_order_state(
                        {
                            "timestamp": row["timestamp"],
                            "symbol": row.get("symbol"),
                            "state": data.get("state_to"),
                            "event": event_type,
                            "quantity": row.get("qty"),
                            "linked_paper_trade_id": row.get("linked_paper_trade_id"),
                            "detail": row.get("detail"),
                        }
                    )
        except Exception as exc:
            self._log_error(writer, event_type=event_type, error=str(exc), data=data)

        if discord_enabled(config) and discord_send is not None:
            try:
                msg = format_discord_message(event_type, {**data, **row})
                discord_send(msg)
                self.discord_messages.append(msg)
            except Exception as exc:
                self._log_error(writer, event_type="discord", error=str(exc), data=data)

    def _log_error(self, writer: Any, *, event_type: str, error: str, data: Mapping[str, Any]) -> None:
        self.error_count += 1
        record = {
            "timestamp": _iso_now(),
            "component": "live_order_notifier",
            "event_type": event_type,
            "error": error,
            "symbol": data.get("symbol"),
        }
        try:
            if writer is not None:
                writer.append_live_order_error(record)
        except Exception:
            pass


def notifier_summary_fields(notifier: Optional[LiveOrderNotifier]) -> dict[str, Any]:
    if notifier is None:
        return {"live_order_notifier_enabled": False}
    counts: dict[str, int] = {}
    for ev in notifier.events:
        et = str(ev.get("event_type") or "")
        counts[et] = counts.get(et, 0) + 1
    return {
        "live_order_notifier_enabled": True,
        "live_order_event_count": len(notifier.events),
        "live_order_notifier_errors": notifier.error_count,
        "live_order_event_counts": counts,
    }
