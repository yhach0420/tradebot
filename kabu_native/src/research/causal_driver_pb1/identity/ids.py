"""Deterministic identity helpers. Rerun must not invent new ids."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from research.causal_driver_pb1.contracts.time import iso


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_obj(obj: Any) -> str:
    return sha256_text(dumps(obj))


def driver_observation_id(
    *,
    source_id: str,
    instrument: str,
    event_time: datetime,
    resolution: str,
) -> str:
    return sha256_obj(
        {
            "source_id": source_id,
            "instrument": instrument,
            "event_time": iso(event_time, field="event_time"),
            "resolution": resolution,
        }
    )


def event_id(*, event_type: str, identity: str, event_time: datetime, source: str) -> str:
    return sha256_obj(
        {
            "event_type": event_type,
            "identity": identity,
            "event_time": iso(event_time, field="event_time"),
            "source": source,
        }
    )


def alpha_id(
    *,
    mechanism_id: str,
    driver_observation_ids: tuple[str, ...],
    generated_at: datetime,
    direction: str,
) -> str:
    return sha256_obj(
        {
            "mechanism_id": mechanism_id,
            "driver_observation_ids": list(driver_observation_ids),
            "generated_at": iso(generated_at, field="generated_at"),
            "direction": direction,
        }
    )
