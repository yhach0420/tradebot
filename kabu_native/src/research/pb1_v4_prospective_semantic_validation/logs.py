"""Append-only operational logs. Aggregated into the three report files."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
LOG_NAMES = (
    "hash_verification_log",
    "quality_gate_log",
    "session_manifest",
    "prospective_state_log",
)


def _now() -> str:
    return datetime.now(JST).isoformat()


def append_jsonl(cache: Path, name: str, row: dict[str, Any]) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{name}.jsonl"
    payload = {"logged_at": _now(), **row}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def read_jsonl(cache: Path, name: str) -> list[dict[str, Any]]:
    path = cache / f"{name}.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        out.append(json.loads(line))
    return out


def load_batch_state(cache: Path) -> dict[str, Any]:
    path = cache / "batch_state.json"
    if not path.is_file():
        return {
            "eligible_session_n": 0,
            "cumulative_candidate_day_n": 0,
            "sessions": [],
            "closed": False,
            "UNDERPOWERED_NOT_A_PASS": False,
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_batch_state(cache: Path, state: dict[str, Any]) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "batch_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
