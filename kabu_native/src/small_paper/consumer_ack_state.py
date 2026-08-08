"""Persistent Paper consumer ACK cursor (per ingress_session_id).

Survives AM→PM Paper PID changes. Ingress in-memory state is authoritative while
connected; this file is the Paper-side resume source when reconnecting.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from small_paper.market_ingress_protocol import now_iso

CONSUMER_ID_PAPER = "paper_runtime"
ACK_STATE_SCHEMA = "paper_consumer_ack_state_v1"


def default_ack_state_path(native_root: Path) -> Path:
    return Path(native_root) / "runtime" / "paper_consumer_ack_state.json"


@dataclass
class ConsumerAckState:
    schema_version: str
    consumer_id: str
    ingress_session_id: str
    trading_date: str
    last_ack_sequence: int
    updated_at: str
    publisher_last_sequence_at_update: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "consumer_id": self.consumer_id,
            "ingress_session_id": self.ingress_session_id,
            "trading_date": self.trading_date,
            "last_ack_sequence": int(self.last_ack_sequence),
            "updated_at": self.updated_at,
            "publisher_last_sequence_at_update": int(self.publisher_last_sequence_at_update),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ConsumerAckState":
        return cls(
            schema_version=str(d.get("schema_version") or ACK_STATE_SCHEMA),
            consumer_id=str(d.get("consumer_id") or CONSUMER_ID_PAPER),
            ingress_session_id=str(d.get("ingress_session_id") or ""),
            trading_date=str(d.get("trading_date") or ""),
            last_ack_sequence=int(d.get("last_ack_sequence") or 0),
            updated_at=str(d.get("updated_at") or ""),
            publisher_last_sequence_at_update=int(d.get("publisher_last_sequence_at_update") or 0),
            reason=str(d.get("reason") or ""),
        )


def load_ack_state(path: Path) -> Optional[ConsumerAckState]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return ConsumerAckState.from_dict(raw)
    except Exception:
        return None


def save_ack_state(path: Path, state: ConsumerAckState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix="ack_state_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def write_ack_checkpoint(
    native_root: Path,
    *,
    ingress_session_id: str,
    trading_date: str,
    last_ack_sequence: int,
    publisher_last_sequence: int = 0,
    reason: str = "",
    consumer_id: str = CONSUMER_ID_PAPER,
    path: Optional[Path] = None,
) -> ConsumerAckState:
    st = ConsumerAckState(
        schema_version=ACK_STATE_SCHEMA,
        consumer_id=consumer_id,
        ingress_session_id=str(ingress_session_id or ""),
        trading_date=str(trading_date or ""),
        last_ack_sequence=int(last_ack_sequence),
        updated_at=now_iso(),
        publisher_last_sequence_at_update=int(publisher_last_sequence or 0),
        reason=str(reason or ""),
    )
    save_ack_state(path or default_ack_state_path(native_root), st)
    return st


def resolve_resume_ack(
    *,
    native_root: Path,
    ingress_session_id: str,
    trading_date: str,
    ingress_hint_ack: int = 0,
    path: Optional[Path] = None,
) -> tuple[int, str]:
    """Return (resume_ack, source). Never invent session-start (0) when state is missing
    for a live session with positive publisher progress — caller must REALTIME_RESYNC.
    """
    disk = load_ack_state(path or default_ack_state_path(native_root))
    hint = int(ingress_hint_ack or 0)
    if disk is None:
        return hint, "ingress_hint_only_no_disk"
    if disk.ingress_session_id and ingress_session_id and disk.ingress_session_id != ingress_session_id:
        return hint, "stale_session_ignored"
    if disk.trading_date and trading_date and disk.trading_date != trading_date:
        return hint, "stale_date_ignored"
    disk_ack = int(disk.last_ack_sequence or 0)
    # Prefer the higher contiguous watermark when same session.
    if disk_ack >= hint:
        return disk_ack, "disk"
    return hint, "ingress_hint"
