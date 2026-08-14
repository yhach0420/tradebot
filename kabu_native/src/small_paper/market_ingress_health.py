"""Ingress health / heartbeat helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from small_paper.ingress_run_identity import atomic_write_json
from small_paper.market_ingress_protocol import now_iso


def write_heartbeat(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(payload)
    row.setdefault("at", now_iso())
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_status_json(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_json(Path(path), dict(payload))


def build_ingress_heartbeat(
    *,
    ingress_session_id: str,
    pid: int,
    state: str,
    last_push_at: str,
    push_age_sec: float,
    connection_generation: int,
    registration_generation: int,
    desired_symbol_count: int,
    registered_symbol_count: int,
    raw_last_sequence: int,
    raw_last_write_at: str,
    publisher_last_sequence: int,
    paper_consumer_last_ack: int,
    paper_consumer_lag: int,
    reconnect_attempt: int,
    recovery_count: int,
    recovery_success_count: int,
    storage_error_count: int,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    out = {
        "ingress_session_id": ingress_session_id,
        "pid": pid,
        "state": state,
        "last_push_at": last_push_at,
        "push_age_sec": push_age_sec,
        "connection_generation": connection_generation,
        "registration_generation": registration_generation,
        "desired_symbol_count": desired_symbol_count,
        "registered_symbol_count": registered_symbol_count,
        "raw_last_sequence": raw_last_sequence,
        "raw_last_write_at": raw_last_write_at,
        "publisher_last_sequence": publisher_last_sequence,
        "paper_consumer_last_ack": paper_consumer_last_ack,
        "paper_consumer_lag": paper_consumer_lag,
        "reconnect_attempt": reconnect_attempt,
        "recovery_count": recovery_count,
        "recovery_success_count": recovery_success_count,
        "storage_error_count": storage_error_count,
        "at": now_iso(),
    }
    if extra:
        out.update(dict(extra))
    return out
