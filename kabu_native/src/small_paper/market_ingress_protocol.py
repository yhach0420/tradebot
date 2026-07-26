"""Market Ingress V2 — shared protocol (envelope, keys, env flag)."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

ENV_MARKET_INGRESS_V2 = "MARKET_INGRESS_V2"
DEFAULT_BUS_HOST = "127.0.0.1"
DEFAULT_BUS_PORT = 18730
SCHEMA_VERSION = "ingress_v2.1"

# Control / health event kinds on the bus (not market PUSH)
KIND_MARKET_PUSH = "market_push"
KIND_ENTRY_BLOCK = "entry_block"
KIND_ENTRY_UNBLOCK = "entry_unblock"
KIND_GAP = "gap"
KIND_HEALTH = "health"
KIND_REGISTRATION = "registration"


def market_ingress_v2_enabled(*, environ: Optional[Mapping[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    raw = str(env.get(ENV_MARKET_INGRESS_V2, "") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return False


def now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def unique_key(ingress_session_id: str, sequence: int) -> str:
    return f"{ingress_session_id}:{int(sequence)}"


@dataclass
class MarketEnvelope:
    """Canonical event published on Local Market Bus after Raw persist."""

    kind: str
    ingress_session_id: str
    sequence: int
    event_time: str
    received_at: str
    persisted_at: str
    published_at: str
    symbol: str
    payload: dict[str, Any]
    connection_generation: int
    registration_generation: int
    capture_part: str = ""
    raw_record_id: str = ""
    schema_version: str = SCHEMA_VERSION
    entry_blocked: bool = False
    entry_block_reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        return unique_key(self.ingress_session_id, self.sequence)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "MarketEnvelope":
        return cls(
            kind=str(d.get("kind") or KIND_MARKET_PUSH),
            ingress_session_id=str(d.get("ingress_session_id") or ""),
            sequence=int(d.get("sequence") or 0),
            event_time=str(d.get("event_time") or ""),
            received_at=str(d.get("received_at") or ""),
            persisted_at=str(d.get("persisted_at") or ""),
            published_at=str(d.get("published_at") or ""),
            symbol=str(d.get("symbol") or ""),
            payload=dict(d.get("payload") or {}),
            connection_generation=int(d.get("connection_generation") or 0),
            registration_generation=int(d.get("registration_generation") or 0),
            capture_part=str(d.get("capture_part") or ""),
            raw_record_id=str(d.get("raw_record_id") or ""),
            schema_version=str(d.get("schema_version") or SCHEMA_VERSION),
            entry_blocked=bool(d.get("entry_blocked")),
            entry_block_reason=str(d.get("entry_block_reason") or ""),
            meta=dict(d.get("meta") or {}),
        )


def kabu_payload_from_envelope(env: MarketEnvelope | Mapping[str, Any]) -> dict[str, Any]:
    """Extract Kabu-shaped board payload for Paper `_process_push_payload`."""
    if isinstance(env, MarketEnvelope):
        return dict(env.payload)
    if isinstance(env, Mapping):
        if "payload" in env and isinstance(env["payload"], dict):
            return dict(env["payload"])
        # Already a Kabu board dict
        return dict(env)
    return {}
