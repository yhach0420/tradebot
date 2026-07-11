"""Discord ENTRY notification delivery audit + retry queue."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

DeliveryAuditCallback = Callable[[Mapping[str, Any]], None]

# Failure classification (Phase663A4)
CLASS_NOTIFY_NOT_CALLED = "A"
CLASS_PAYLOAD_BUILD_FAILED = "B"
CLASS_WEBHOOK_SEND_FAILED = "C"
CLASS_HTTP_FAILED = "D"
CLASS_NO_RETRY_TERMINATED = "E"
CLASS_SENT_TIME_PERSIST_FAILED = "F"
CLASS_DELIVERED_LOG_MISSING = "G"
CLASS_OTHER = "H"

FINAL_DELIVERED = "delivered"
FINAL_FAILED = "failed"
FINAL_SKIPPED = "skipped"
FINAL_SUPPRESSED = "suppressed"
FINAL_UNPROVABLE = "unprovable"


def webhook_url_hash(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    return hashlib.sha256(u.encode("utf-8")).hexdigest()[:16]


def now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


@dataclass
class DiscordPostResult:
    notify_entry_called: bool = False
    payload_built: bool = False
    webhook_called: bool = False
    webhook_url_hash: str = ""
    http_status: Optional[int] = None
    http_response_body: str = ""
    exception_type: str = ""
    exception_message: str = ""
    retry_count: int = 0
    final_result: str = FINAL_SKIPPED
    failure_classification: str = ""
    sent_time: str = ""
    discord_message_id: str = ""
    suppressed_reason: str = ""
    failure_reason: str = ""

    def to_audit_record(
        self,
        *,
        symbol: str,
        event_time: str,
        position_id: str = "",
        session_id: str = "",
        sequence_id: Optional[int] = None,
        persisted_to_log: bool = False,
    ) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "position_id": position_id,
            "session_id": session_id,
            "event_time": event_time,
            "sequence_id": sequence_id,
            "notify_entry_called": self.notify_entry_called,
            "payload_built": self.payload_built,
            "webhook_called": self.webhook_called,
            "webhook_url_hash": self.webhook_url_hash,
            "http_status": self.http_status,
            "http_response_body": (self.http_response_body or "")[:100],
            "exception_type": self.exception_type,
            "exception_message": (self.exception_message or "")[:200],
            "retry_count": self.retry_count,
            "final_result": self.final_result,
            "failure_classification": self.failure_classification,
            "sent_time": self.sent_time,
            "persisted_to_log": persisted_to_log,
            "discord_message_id": self.discord_message_id,
            "failure_reason": self.failure_reason,
            "suppressed_reason": self.suppressed_reason,
            "recorded_at": now_iso(),
        }


@dataclass
class _PendingEntryNotify:
    event: dict[str, Any]
    payload: dict[str, Any]
    open_slots: int
    session_bucket: str
    slot_before: Optional[int]
    score5_candidate_ordinal: Optional[int]
    sequence_id: int
    attempt: int = 0


@dataclass
class EntryNotifyRetryQueue:
    max_retries: int = 3
    pending: list[_PendingEntryNotify] = field(default_factory=list)

    def enqueue(self, item: _PendingEntryNotify) -> None:
        self.pending.append(item)

    def flush(
        self,
        send_fn: Callable[[_PendingEntryNotify], DiscordPostResult],
        *,
        audit: Optional[DeliveryAuditCallback] = None,
    ) -> list[DiscordPostResult]:
        if not self.pending:
            return []
        still: list[_PendingEntryNotify] = []
        results: list[DiscordPostResult] = []
        for item in list(self.pending):
            item.attempt += 1
            res = send_fn(item)
            res.retry_count = item.attempt
            if res.final_result == FINAL_DELIVERED:
                results.append(res)
                continue
            if item.attempt >= self.max_retries:
                res.final_result = FINAL_FAILED
                res.failure_classification = CLASS_NO_RETRY_TERMINATED
                res.failure_reason = res.failure_reason or "retry_exhausted"
                if audit:
                    audit(
                        res.to_audit_record(
                            symbol=str(item.event.get("symbol") or ""),
                            event_time=str(item.event.get("event_time") or ""),
                            position_id=str(item.event.get("position_id") or ""),
                            session_id=str(item.event.get("session_id") or ""),
                            sequence_id=item.sequence_id,
                            persisted_to_log=True,
                        )
                    )
                results.append(res)
            else:
                still.append(item)
        self.pending = still
        return results
