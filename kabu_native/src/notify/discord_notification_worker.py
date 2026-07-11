"""Phase687W10 — Async Discord notification worker (fail-open for trading/capture)."""

from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import requests

from notify.discord_notification_audit import NotificationAudit
from notify.discord_notification_model import NotificationEnvelope, Severity

SEVERITY_PRIORITY = {
    "CRITICAL": 0,
    "ERROR": 1,
    "WARNING": 2,
    "NOTICE": 3,
    "INFO": 4,
}


@dataclass(order=True)
class _QItem:
    priority: int
    seq: int
    envelope: Any = field(compare=False)
    webhook_url: str = field(compare=False, default="")
    attempt: int = field(compare=False, default=0)


class NotificationWorker:
    """Bounded priority queue + background HTTP sender. Never raises into callers."""

    def __init__(
        self,
        *,
        audit: NotificationAudit,
        queue_max: int = 2000,
        timeout_sec: float = 8.0,
        max_retries: int = 3,
    ) -> None:
        try:
            from small_paper.env_loader import ensure_repo_dotenv

            ensure_repo_dotenv()
        except Exception:
            pass
        self.audit = audit
        self.queue_max = queue_max
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self._q: queue.PriorityQueue = queue.PriorityQueue(maxsize=queue_max)
        self._seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.dropped = 0
        self.sent = 0
        self.failed = 0
        self.last_status: str = "IDLE"
        self.external_send_count = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="discord-notify-worker", daemon=True)
        self._thread.start()
        self.last_status = "RUNNING"

    def stop(self, *, flush_sec: float = 2.0) -> None:
        """Graceful: wait briefly for queue drain at session finalize only."""
        deadline = time.monotonic() + max(0.0, flush_sec)
        while time.monotonic() < deadline and not self._q.empty():
            time.sleep(0.05)
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(0.5, flush_sec))
        self.last_status = "STOPPED"
        try:
            self.audit.write_summary()
        except Exception:
            pass

    def enqueue(self, envelope: NotificationEnvelope, webhook_url: str) -> dict[str, Any]:
        """Non-blocking enqueue. On overflow: keep CRITICAL, drop INFO/NOTICE."""
        if not webhook_url:
            return {"status": "SKIPPED_WEBHOOK_NOT_CONFIGURED", "queued": False}
        with self._lock:
            self._seq += 1
            seq = self._seq
        pri = SEVERITY_PRIORITY.get(envelope.severity, 4)
        item = _QItem(priority=pri, seq=seq, envelope=envelope, webhook_url=webhook_url, attempt=0)
        try:
            self._q.put_nowait(item)
            self.audit.record_event(
                {
                    "notification_id": envelope.notification_id,
                    "category": envelope.category,
                    "webhook_key": envelope.webhook_env_key,
                    "status": "QUEUED",
                    "dedupe_key": envelope.dedupe_key,
                    "payload_hash": envelope.payload_hash,
                }
            )
            return {"status": "QUEUED", "queued": True}
        except queue.Full:
            # drop low severity
            if envelope.severity in (Severity.INFO.value, Severity.NOTICE.value):
                self.dropped += 1
                self.audit.record_event(
                    {
                        "notification_id": envelope.notification_id,
                        "category": envelope.category,
                        "status": "DROPPED",
                        "reason": "queue_overflow",
                    }
                )
                return {"status": "DROPPED", "queued": False}
            # try to make room by discarding one INFO if present — best effort put
            try:
                # force: temporarily increase by getting and requeue only higher
                self._q.put(item, timeout=0.05)
                return {"status": "QUEUED", "queued": True}
            except Exception:
                self.dropped += 1
                self.audit.record_dead_letter(
                    {
                        "notification_id": envelope.notification_id,
                        "category": envelope.category,
                        "status": "DROPPED",
                        "reason": "queue_overflow_critical_path",
                    }
                )
                return {"status": "DROPPED", "queued": False}

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item: _QItem = self._q.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._send_with_retry(item)
            except Exception as exc:
                self.failed += 1
                self.audit.record_failure(
                    {
                        "notification_id": getattr(item.envelope, "notification_id", ""),
                        "error_category": type(exc).__name__,
                        "status": "FAILED",
                    }
                )
            finally:
                self._q.task_done()

    def _send_with_retry(self, item: _QItem) -> None:
        env: NotificationEnvelope = item.envelope
        payload = env.discord_payload()
        attempt = item.attempt
        last_err = ""
        while attempt < self.max_retries:
            attempt += 1
            t0 = time.perf_counter()
            try:
                resp = requests.post(item.webhook_url, json=payload, timeout=self.timeout_sec)
                latency_ms = round((time.perf_counter() - t0) * 1000, 1)
                self.external_send_count += 1
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After") or 1.0)
                    time.sleep(min(30.0, max(0.5, retry_after)))
                    last_err = "HTTP_429"
                    continue
                if resp.status_code >= 400:
                    last_err = f"HTTP_{resp.status_code}"
                    # exponential backoff
                    time.sleep(min(8.0, 0.5 * (2 ** (attempt - 1))))
                    continue
                self.sent += 1
                self.last_status = "OK"
                self.audit.record_event(
                    {
                        "notification_id": env.notification_id,
                        "category": env.category,
                        "webhook_key": env.webhook_env_key,
                        "status": "SENT",
                        "http_status": resp.status_code,
                        "retry_count": attempt - 1,
                        "send_latency_ms": latency_ms,
                        "payload_hash": env.payload_hash,
                    }
                )
                return
            except requests.Timeout:
                last_err = "TIMEOUT"
                time.sleep(min(8.0, 0.5 * (2 ** (attempt - 1))))
            except requests.ConnectionError:
                last_err = "CONNECTION"
                time.sleep(min(8.0, 0.5 * (2 ** (attempt - 1))))
            except Exception as exc:
                last_err = type(exc).__name__
                break
        self.failed += 1
        self.last_status = "FAILED"
        self.audit.record_dead_letter(
            {
                "notification_id": env.notification_id,
                "category": env.category,
                "webhook_key": env.webhook_env_key,
                "status": "FAILED",
                "error_category": last_err,
                "retry_count": attempt,
                "payload_hash": env.payload_hash,
            }
        )

    def status(self) -> dict[str, Any]:
        return {
            "worker_status": self.last_status,
            "queue_size": self._q.qsize(),
            "queue_max": self.queue_max,
            "sent": self.sent,
            "failed": self.failed,
            "dropped": self.dropped,
            "external_send_count": self.external_send_count,
            "alive": bool(self._thread and self._thread.is_alive()),
        }
