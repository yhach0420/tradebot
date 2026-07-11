"""Phase687W10 — Persistent Discord notification dedupe store."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

DEFAULT_DEDUPE_PATH = Path("runtime") / "discord_notification_dedupe.jsonl"


class DedupeStore:
    """Append-only JSONL dedupe; fail-open on corruption (Paper must not stop)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = {}
        self.corrupted = False
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        key = str(obj.get("dedupe_key") or "")
                        if key:
                            self._cache[key] = obj
                    except Exception:
                        self.corrupted = True
        except Exception:
            self.corrupted = True

    def check(self, dedupe_key: str) -> dict[str, Any]:
        if not dedupe_key:
            return {"result": "NO_KEY", "allow": True}
        with self._lock:
            prev = self._cache.get(dedupe_key)
            if not prev:
                return {"result": "NEW", "allow": True}
            status = str(prev.get("status") or "")
            if status == "SENT":
                return {"result": "DEDUPED", "allow": False, "previous": prev}
            if status == "FAILED":
                return {"result": "RETRY_ALLOWED", "allow": True, "previous": prev}
            return {"result": "DEDUPED", "allow": False, "previous": prev}

    def record(
        self,
        *,
        dedupe_key: str,
        status: str,
        notification_id: str = "",
        payload_hash: str = "",
        severity: str = "",
        incident_state: str = "",
    ) -> None:
        if not dedupe_key:
            return
        row = {
            "dedupe_key": dedupe_key,
            "status": status,
            "notification_id": notification_id,
            "payload_hash": payload_hash,
            "severity": severity,
            "incident_state": incident_state,
            "at": datetime.now(JST).isoformat(timespec="seconds"),
            "mono": time.monotonic(),
        }
        with self._lock:
            self._cache[dedupe_key] = row
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            except Exception:
                # fail-open
                pass

    def allow_severity_upgrade(self, dedupe_key: str, new_severity: str, *, new_state: str = "") -> bool:
        order = {"INFO": 0, "NOTICE": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
        with self._lock:
            prev = self._cache.get(dedupe_key) or {}
            old_sev = str(prev.get("severity") or "INFO")
            if order.get(new_severity, 0) > order.get(old_sev, 0):
                return True
            old_state = str(prev.get("incident_state") or "")
            if new_state and new_state != old_state:
                return True
            # 30 min continuation re-notify for CRITICAL
            mono = float(prev.get("mono") or 0)
            if new_severity == "CRITICAL" and mono and (time.monotonic() - mono) >= 1800:
                return True
            return False

    def valid(self) -> bool:
        return not self.corrupted


def default_dedupe_store(native_root: Path) -> DedupeStore:
    return DedupeStore(Path(native_root) / DEFAULT_DEDUPE_PATH)
