"""Phase687W10 — Discord notification envelope model."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
PAYLOAD_SCHEMA_VERSION = "687W10.1"


class NotificationCategory(str, Enum):
    TRADE_ACTUAL = "TRADE_ACTUAL"
    SESSION_SUMMARY = "SESSION_SUMMARY"
    OPERATIONS = "OPERATIONS"
    MARKET_CAPTURE = "MARKET_CAPTURE"
    CAP_BLOCKED = "CAP_BLOCKED"
    RESEARCH_SHADOW = "RESEARCH_SHADOW"
    CRITICAL_SAFETY = "CRITICAL_SAFETY"


class Severity(str, Enum):
    INFO = "INFO"
    NOTICE = "NOTICE"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ActualOrShadow(str, Enum):
    ACTUAL = "ACTUAL"
    SHADOW = "SHADOW"
    OPERATIONS = "OPERATIONS"
    CAPTURE = "CAPTURE"
    NONE = "NONE"


# Webhook env keys (never log URL values)
WEBHOOK_ENV_TRADE = "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL"
WEBHOOK_ENV_LEGACY = "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL"
WEBHOOK_ENV_CAP = "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL"
WEBHOOK_ENV_V1R_ENTRY = "KABU_V1R_ENTRY_WEBHOOK_URL"
WEBHOOK_ENV_OPERATIONS = "KABU_DISCORD_OPERATIONS_WEBHOOK_URL"
WEBHOOK_ENV_CAPTURE = "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL"
WEBHOOK_ENV_CAPTURE_LEGACY = "KABU_MARKET_CAPTURE_WEBHOOK_URL"
WEBHOOK_ENV_RESEARCH = "KABU_DISCORD_RESEARCH_WEBHOOK_URL"
WEBHOOK_ENV_RESEARCH_LEGACY = "KABU_SHADOW_DISCORD_WEBHOOK_URL"
WEBHOOK_ENV_CRITICAL = "KABU_DISCORD_CRITICAL_WEBHOOK_URL"

CATEGORY_WEBHOOK_KEYS: dict[NotificationCategory, tuple[str, ...]] = {
    NotificationCategory.TRADE_ACTUAL: (WEBHOOK_ENV_TRADE, WEBHOOK_ENV_LEGACY),
    NotificationCategory.SESSION_SUMMARY: (WEBHOOK_ENV_TRADE, WEBHOOK_ENV_LEGACY),
    NotificationCategory.CAP_BLOCKED: (WEBHOOK_ENV_CAP,),
    NotificationCategory.OPERATIONS: (WEBHOOK_ENV_OPERATIONS,),
    NotificationCategory.MARKET_CAPTURE: (WEBHOOK_ENV_CAPTURE, WEBHOOK_ENV_CAPTURE_LEGACY),
    NotificationCategory.RESEARCH_SHADOW: (WEBHOOK_ENV_RESEARCH, WEBHOOK_ENV_RESEARCH_LEGACY),
    NotificationCategory.CRITICAL_SAFETY: (WEBHOOK_ENV_CRITICAL,),
}

# Default: CRITICAL does NOT fall back to operations
CRITICAL_OPERATIONS_FALLBACK_DEFAULT = False


@dataclass
class NotificationEnvelope:
    notification_id: str
    category: str
    severity: str
    event_type: str
    title: str
    trading_date: str
    session_id: str = ""
    am_pm: str = ""
    symbol: str = ""
    occurred_at_jst: str = ""
    source_module: str = ""
    dedupe_key: str = ""
    correlation_id: str = ""
    actual_or_shadow: str = ActualOrShadow.NONE.value
    action_required: bool = False
    operator_action: str = ""
    artifact_path: str = ""
    payload_schema_version: str = PAYLOAD_SCHEMA_VERSION
    payload_hash: str = ""
    content: str = ""
    embeds: list[dict[str, Any]] = field(default_factory=list)
    webhook_env_key: str = ""
    ownership: str = ""
    incident_id: str = ""
    incident_state: str = ""
    state_version: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # never include secrets
        d.pop("webhook_url", None)
        return d

    def discord_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.content:
            payload["content"] = self.content[:1900]
        if self.embeds:
            payload["embeds"] = self.embeds[:3]
        if not payload:
            payload["content"] = (self.title or self.event_type)[:1900]
        return payload


def new_notification_id() -> str:
    return f"n_{uuid.uuid4().hex[:16]}"


def now_jst_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def trading_date_jst() -> str:
    return datetime.now(JST).strftime("%Y%m%d")


def compute_payload_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_envelope(
    *,
    category: NotificationCategory,
    severity: Severity,
    event_type: str,
    title: str,
    content: str = "",
    embeds: Optional[list[dict[str, Any]]] = None,
    trading_date: Optional[str] = None,
    session_id: str = "",
    am_pm: str = "",
    symbol: str = "",
    source_module: str = "",
    dedupe_key: str = "",
    correlation_id: str = "",
    actual_or_shadow: ActualOrShadow = ActualOrShadow.NONE,
    action_required: bool = False,
    operator_action: str = "",
    artifact_path: str = "",
    ownership: str = "",
    incident_id: str = "",
    incident_state: str = "",
    state_version: str = "",
    extra: Optional[Mapping[str, Any]] = None,
) -> NotificationEnvelope:
    embeds_l = list(embeds or [])
    payload = {"content": content, "embeds": embeds_l, "title": title, "event_type": event_type}
    return NotificationEnvelope(
        notification_id=new_notification_id(),
        category=category.value,
        severity=severity.value,
        event_type=event_type,
        title=title,
        trading_date=trading_date or trading_date_jst(),
        session_id=session_id,
        am_pm=am_pm,
        symbol=symbol,
        occurred_at_jst=now_jst_iso(),
        source_module=source_module,
        dedupe_key=dedupe_key,
        correlation_id=correlation_id or new_notification_id(),
        actual_or_shadow=actual_or_shadow.value,
        action_required=action_required,
        operator_action=operator_action,
        artifact_path=artifact_path,
        payload_hash=compute_payload_hash(payload),
        content=content,
        embeds=embeds_l,
        ownership=ownership,
        incident_id=incident_id,
        incident_state=incident_state,
        state_version=state_version,
        extra=dict(extra or {}),
    )
