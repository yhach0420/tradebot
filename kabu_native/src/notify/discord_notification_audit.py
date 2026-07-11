"""Phase687W10 — Local Discord notification audit (no webhook URLs / secrets)."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

SECRET_RE = re.compile(
    r"(?i)("
    r"discord\.com/api/webhooks/\S+"
    r"|https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\S+"
    r"|password|api[_-]?password|token|authorization|holdid|account"
    r")"
)


def mask_secrets_text(text: str) -> str:
    return SECRET_RE.sub("[REDACTED]", text or "")


class NotificationAudit:
    def __init__(self, native_root: Path, trading_date: Optional[str] = None) -> None:
        day = trading_date or datetime.now(JST).strftime("%Y%m%d")
        self.dir = Path(native_root) / "results" / "notifications" / day
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.counts = {
            "sent": 0,
            "skipped": 0,
            "failed": 0,
            "deduped": 0,
            "queued": 0,
            "dropped": 0,
            "dead_letter": 0,
        }

    def _append(self, name: str, row: Mapping[str, Any]) -> None:
        path = self.dir / name
        clean = {k: v for k, v in dict(row).items() if k not in ("webhook_url", "url", "Authorization")}
        text = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
        text = mask_secrets_text(text)
        with self._lock:
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(text + "\n")

    def record_event(self, row: Mapping[str, Any]) -> None:
        status = str(row.get("status") or "")
        with self._lock:
            if status in self.counts:
                self.counts[status] += 1
            elif status == "SENT":
                self.counts["sent"] += 1
            elif status == "SKIPPED_WEBHOOK_NOT_CONFIGURED":
                self.counts["skipped"] += 1
            elif status == "DEDUPED":
                self.counts["deduped"] += 1
            elif status == "QUEUED":
                self.counts["queued"] += 1
            elif status == "DROPPED":
                self.counts["dropped"] += 1
            elif status == "FAILED":
                self.counts["failed"] += 1
        self._append("notification_events.jsonl", {**dict(row), "at": datetime.now(JST).isoformat(timespec="seconds")})

    def record_failure(self, row: Mapping[str, Any]) -> None:
        with self._lock:
            self.counts["failed"] += 1
        self._append("notification_failures.jsonl", {**dict(row), "at": datetime.now(JST).isoformat(timespec="seconds")})

    def record_dead_letter(self, row: Mapping[str, Any]) -> None:
        with self._lock:
            self.counts["dead_letter"] += 1
        self._append(
            "notification_dead_letter.jsonl",
            {**dict(row), "at": datetime.now(JST).isoformat(timespec="seconds")},
        )

    def write_summary(self) -> Path:
        path = self.dir / "notification_summary.json"
        payload = {
            "counts": dict(self.counts),
            "dir": str(self.dir),
            "secrets_present": False,
            "updated_at": datetime.now(JST).isoformat(timespec="seconds"),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
