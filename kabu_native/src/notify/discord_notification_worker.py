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
        dedupe: Any = None,
    ) -> None:
        try:
            from small_paper.env_loader import ensure_repo_dotenv

            ensure_repo_dotenv()
        except Exception:
            pass
        self.audit = audit
        self.dedupe = dedupe
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

    def queue_depth(self) -> int:
        try:
            return int(self._q.qsize())
        except Exception:
            return 0

    def stop(self, *, flush_sec: float = 2.0) -> dict[str, Any]:
        """Drain queue then stop worker. Bounded wait — never hangs Paper finalize.

        Returns flush telemetry. Remaining items after timeout are marked TIMEOUT
        and discarded so subprocess/process can exit cleanly.
        """
        flush_sec = float(max(0.0, flush_sec))
        depth0 = self.queue_depth()
        self.audit.record_event(
            {
                "status": "WORKER_FLUSH_START",
                "queue_depth": depth0,
                "flush_sec": flush_sec,
            }
        )
        deadline = time.monotonic() + flush_sec
        timed_out = False
        # Prefer draining before signaling stop so in-flight + queued HTTP can complete.
        while time.monotonic() < deadline:
            if self._q.empty():
                # brief settle for in-flight send
                time.sleep(0.05)
                if self._q.empty():
                    break
            time.sleep(0.05)
        else:
            timed_out = True

        # Anything still queued after deadline → TIMEOUT (do not claim SENT).
        timed_out_n = 0
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                break
            timed_out_n += 1
            env = getattr(item, "envelope", None)
            self.failed += 1
            self.audit.record_event(
                {
                    "notification_id": getattr(env, "notification_id", ""),
                    "category": getattr(env, "category", ""),
                    "webhook_key": getattr(env, "webhook_env_key", ""),
                    "status": "TIMEOUT",
                    "dedupe_key": getattr(env, "dedupe_key", ""),
                    "payload_hash": getattr(env, "payload_hash", ""),
                    "reason": "flush_deadline_exceeded",
                }
            )
            if self.dedupe is not None and getattr(env, "dedupe_key", None):
                try:
                    self.dedupe.record(
                        dedupe_key=str(env.dedupe_key),
                        status="TIMEOUT",
                        notification_id=str(getattr(env, "notification_id", "") or ""),
                        payload_hash=str(getattr(env, "payload_hash", "") or ""),
                    )
                except Exception:
                    pass
            try:
                self._q.task_done()
            except Exception:
                pass

        self._stop.set()
        join_budget = max(0.5, min(flush_sec, 5.0)) if flush_sec > 0 else 0.5
        if self._thread:
            self._thread.join(timeout=join_budget)
        alive = bool(self._thread and self._thread.is_alive())
        remaining = self.queue_depth()
        self.last_status = "STOPPED" if not alive else "KILLED"
        if alive:
            # Daemon thread may linger briefly; do not block Paper. Record explicitly.
            self.audit.record_event(
                {
                    "status": "KILLED",
                    "reason": "worker_thread_still_alive_after_join",
                    "queue_depth": remaining,
                }
            )
        self.audit.record_event(
            {
                "status": "WORKER_FLUSH_DONE",
                "remaining": remaining,
                "timed_out": timed_out or timed_out_n > 0,
                "timed_out_count": timed_out_n,
                "worker_alive": alive,
            }
        )
        try:
            self.audit.write_summary()
        except Exception:
            pass
        return {
            "status": self.last_status,
            "queue_depth_start": depth0,
            "remaining": remaining,
            "timed_out": bool(timed_out or timed_out_n > 0),
            "timed_out_count": timed_out_n,
            "worker_alive": alive,
            "flush_sec": flush_sec,
        }

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

    def _http_timeout(self) -> tuple[float, float]:
        """(connect, read) timeouts — never hang forever on stuck sockets."""
        read = float(self.timeout_sec or 8.0)
        connect = min(5.0, max(1.0, read))
        return (connect, read)

    def _send_with_retry(self, item: _QItem) -> None:
        env: NotificationEnvelope = item.envelope
        payload = env.discord_payload()
        attempt = item.attempt
        last_err = ""
        self.audit.record_event(
            {
                "notification_id": env.notification_id,
                "category": env.category,
                "webhook_key": env.webhook_env_key,
                "status": "SENDING",
                "dedupe_key": env.dedupe_key,
                "payload_hash": env.payload_hash,
            }
        )
        while attempt < self.max_retries:
            attempt += 1
            t0 = time.perf_counter()
            try:
                resp = requests.post(
                    item.webhook_url,
                    json=payload,
                    timeout=self._http_timeout(),
                )
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
                        "dedupe_key": env.dedupe_key,
                        "response_body_len": len(resp.text or ""),
                        # Discord webhook success is typically 204 No Content (empty body).
                        "response_body_preview": (resp.text or "")[:200],
                        "event": "HTTP_SENT",
                    }
                )
                if self.dedupe is not None and env.dedupe_key:
                    try:
                        self.dedupe.record(
                            dedupe_key=str(env.dedupe_key),
                            status="SENT",
                            notification_id=str(env.notification_id or ""),
                            payload_hash=str(env.payload_hash or ""),
                            severity=str(getattr(env, "severity", "") or ""),
                            incident_state=str(getattr(env, "incident_state", "") or ""),
                        )
                    except Exception:
                        pass
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
        fail_status = "TIMEOUT" if last_err == "TIMEOUT" else "FAILED"
        self.audit.record_dead_letter(
            {
                "notification_id": env.notification_id,
                "category": env.category,
                "webhook_key": env.webhook_env_key,
                "status": fail_status,
                "error_category": last_err,
                "retry_count": attempt,
                "payload_hash": env.payload_hash,
                "dedupe_key": env.dedupe_key,
            }
        )
        if self.dedupe is not None and env.dedupe_key:
            try:
                self.dedupe.record(
                    dedupe_key=str(env.dedupe_key),
                    status=fail_status,
                    notification_id=str(env.notification_id or ""),
                    payload_hash=str(env.payload_hash or ""),
                    severity=str(getattr(env, "severity", "") or ""),
                )
            except Exception:
                pass

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
