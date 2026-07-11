"""Phase687W10 — Discord notification router (ownership + webhook resolve + fail-open)."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Mapping, Optional

from notify.discord_notification_audit import NotificationAudit
from notify.discord_notification_dedupe import DedupeStore, default_dedupe_store
from notify.discord_notification_formatter import truncate_for_discord
from notify.discord_notification_model import (
    CATEGORY_WEBHOOK_KEYS,
    CRITICAL_OPERATIONS_FALLBACK_DEFAULT,
    NotificationCategory,
    NotificationEnvelope,
    Severity,
    WEBHOOK_ENV_OPERATIONS,
    build_envelope,
)
from notify.discord_notification_rate_limit import RateLimiter
from notify.discord_notification_worker import NotificationWorker

_LOCK = threading.Lock()
_ROUTER: Optional["DiscordNotificationRouter"] = None


def resolve_webhook_url(env_keys: tuple[str, ...]) -> tuple[str, str]:
    """Return (url, env_key_used). Never log URL. Loads repo `.env` first (OS wins)."""
    try:
        from small_paper.env_loader import ensure_repo_dotenv

        ensure_repo_dotenv()
    except Exception:
        pass
    for key in env_keys:
        url = (os.environ.get(key) or "").strip()
        if url:
            return url, key
    return "", ""


class DiscordNotificationRouter:
    def __init__(self, native_root: Path, *, enable_worker: bool = True) -> None:
        try:
            from small_paper.env_loader import ensure_repo_dotenv

            ensure_repo_dotenv()
        except Exception:
            pass
        self.native_root = Path(native_root)
        self.audit = NotificationAudit(self.native_root)
        self.dedupe = default_dedupe_store(self.native_root)
        self.rate = RateLimiter()
        self.worker = NotificationWorker(audit=self.audit)
        self.critical_ops_fallback = CRITICAL_OPERATIONS_FALLBACK_DEFAULT
        self.enable_worker = enable_worker
        self.publish_count = 0
        self.external_test_sends = 0
        if enable_worker:
            try:
                self.worker.start()
            except Exception:
                self.audit.record_failure({"status": "FAILED", "error_category": "worker_start_failed"})

    def configured_categories(self) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for cat, keys in CATEGORY_WEBHOOK_KEYS.items():
            url, _ = resolve_webhook_url(keys)
            if cat == NotificationCategory.CRITICAL_SAFETY and not url and self.critical_ops_fallback:
                url, _ = resolve_webhook_url((WEBHOOK_ENV_OPERATIONS,))
            out[cat.value] = bool(url)
        return out

    def publish(self, envelope: NotificationEnvelope) -> dict[str, Any]:
        """Route + dedupe + rate-limit + enqueue. Never raises. Never blocks trading."""
        self.publish_count += 1
        try:
            return self._publish_inner(envelope)
        except Exception as exc:
            self.audit.record_failure(
                {
                    "notification_id": envelope.notification_id,
                    "status": "FAILED",
                    "error_category": type(exc).__name__,
                }
            )
            return {"status": "FAILED", "error": type(exc).__name__, "queued": False}

    def _publish_inner(self, envelope: NotificationEnvelope) -> dict[str, Any]:
        cat = envelope.category
        # split long content (max 3 messages handled by truncate; enqueue extras)
        if envelope.content and len(envelope.content) > 1900:
            from notify.discord_notification_model import ActualOrShadow as AoS

            parts = truncate_for_discord(envelope.content)
            envelope.content = parts[0]
            for i, part in enumerate(parts[1:], start=2):
                follow = build_envelope(
                    category=NotificationCategory(cat),
                    severity=Severity(envelope.severity),
                    event_type=f"{envelope.event_type}_part{i}",
                    title=envelope.title,
                    content=part,
                    trading_date=envelope.trading_date,
                    session_id=envelope.session_id,
                    source_module=envelope.source_module,
                    dedupe_key=f"{envelope.dedupe_key}|part{i}" if envelope.dedupe_key else "",
                    actual_or_shadow=AoS(envelope.actual_or_shadow),
                    ownership=envelope.ownership,
                )
                self._enqueue_only(follow)

        # severity upgrade bypass for CRITICAL
        dedupe_key = envelope.dedupe_key
        if dedupe_key:
            chk = self.dedupe.check(dedupe_key)
            if not chk.get("allow"):
                if envelope.severity == Severity.CRITICAL.value and self.dedupe.allow_severity_upgrade(
                    dedupe_key, envelope.severity, new_state=envelope.incident_state
                ):
                    pass
                else:
                    self.audit.record_event(
                        {
                            "notification_id": envelope.notification_id,
                            "category": cat,
                            "status": "DEDUPED",
                            "dedupe_key": dedupe_key,
                            "dedupe_result": chk.get("result"),
                        }
                    )
                    return {"status": "DEDUPED", "queued": False}

        state_key = envelope.state_version or envelope.incident_state or envelope.event_type or "default"
        # TRADE_ACTUAL / SESSION_SUMMARY: no rate limit beyond dedupe
        if cat not in (
            NotificationCategory.TRADE_ACTUAL.value,
            NotificationCategory.SESSION_SUMMARY.value,
            NotificationCategory.CAP_BLOCKED.value,
            NotificationCategory.RESEARCH_SHADOW.value,
        ):
            rl = self.rate.allow(category=cat, state_key=state_key, severity=envelope.severity)
            if not rl.get("allow"):
                # CRITICAL first already handled by dedupe upgrade; suppress continuation
                if envelope.severity == Severity.CRITICAL.value and rl.get("reason", "").startswith("first"):
                    pass
                elif envelope.severity == Severity.CRITICAL.value and self.dedupe.allow_severity_upgrade(
                    dedupe_key or state_key, envelope.severity, new_state=envelope.incident_state
                ):
                    pass
                else:
                    self.audit.record_event(
                        {
                            "notification_id": envelope.notification_id,
                            "category": cat,
                            "status": "SKIPPED",
                            "rate_limit_result": rl.get("reason"),
                        }
                    )
                    return {"status": "RATE_LIMITED", "queued": False}

        keys = CATEGORY_WEBHOOK_KEYS.get(NotificationCategory(cat), ())
        url, key_used = resolve_webhook_url(keys)
        if not url and cat == NotificationCategory.CRITICAL_SAFETY.value and self.critical_ops_fallback:
            url, key_used = resolve_webhook_url((WEBHOOK_ENV_OPERATIONS,))
        envelope.webhook_env_key = key_used
        if not url:
            self.audit.record_event(
                {
                    "notification_id": envelope.notification_id,
                    "category": cat,
                    "webhook_key": keys[0] if keys else "",
                    "status": "SKIPPED_WEBHOOK_NOT_CONFIGURED",
                    "dedupe_key": dedupe_key,
                }
            )
            return {"status": "SKIPPED_WEBHOOK_NOT_CONFIGURED", "queued": False}

        result = self.worker.enqueue(envelope, url)
        if result.get("queued") and dedupe_key:
            self.dedupe.record(
                dedupe_key=dedupe_key,
                status="SENT",
                notification_id=envelope.notification_id,
                payload_hash=envelope.payload_hash,
                severity=envelope.severity,
                incident_state=envelope.incident_state,
            )
        elif result.get("status") == "DROPPED" and dedupe_key:
            self.dedupe.record(
                dedupe_key=dedupe_key,
                status="FAILED",
                notification_id=envelope.notification_id,
                payload_hash=envelope.payload_hash,
                severity=envelope.severity,
            )
        return result

    def _enqueue_only(self, envelope: NotificationEnvelope) -> None:
        keys = CATEGORY_WEBHOOK_KEYS.get(NotificationCategory(envelope.category), ())
        url, key_used = resolve_webhook_url(keys)
        envelope.webhook_env_key = key_used
        if url:
            self.worker.enqueue(envelope, url)

    # ── Convenience publishers (ownership-aware) ──────────────────────────

    def publish_paper_blocked(
        self,
        *,
        failed_step: str,
        reason: str,
        next_action: str,
        capture_status: str = "",
        capture_pid: Any = "",
        capture_output: str = "",
        capture_continues: bool = False,
        trading_date: str = "",
    ) -> dict[str, Any]:
        from notify.discord_notification_formatter import format_paper_blocked
        from notify.discord_notification_model import ActualOrShadow

        content = format_paper_blocked(
            failed_step=failed_step,
            reason=reason,
            next_action=next_action,
            capture_status=capture_status,
            capture_pid=capture_pid,
            capture_output=capture_output,
            capture_continues=capture_continues,
        )
        env = build_envelope(
            category=NotificationCategory.OPERATIONS,
            severity=Severity.WARNING,
            event_type="PAPER_BLOCKED",
            title="[PAPER BLOCKED - CAPTURE CONTINUES]" if capture_continues else "[PAPER BLOCKED]",
            content=content,
            trading_date=trading_date or None,
            dedupe_key=f"ops|paper_blocked|{trading_date}|{failed_step}|{reason}",
            actual_or_shadow=ActualOrShadow.OPERATIONS,
            source_module="paper_trade_checked_runner",
            ownership="CHECKED_RUNNER",
            state_version=f"{failed_step}:{reason}",
        )
        return self.publish(env)

    def publish_capture(
        self,
        *,
        event_type: str,
        content: str,
        capture_session_id: str,
        trading_date: str = "",
        severity: Severity = Severity.INFO,
        state_version: str = "",
    ) -> dict[str, Any]:
        from notify.discord_notification_model import ActualOrShadow

        env = build_envelope(
            category=NotificationCategory.MARKET_CAPTURE,
            severity=severity,
            event_type=event_type,
            title=event_type,
            content=content,
            trading_date=trading_date or None,
            session_id=capture_session_id,
            dedupe_key=f"capture|{capture_session_id}|{event_type}|{state_version or '1'}",
            actual_or_shadow=ActualOrShadow.CAPTURE,
            source_module="market_capture_sidecar",
            ownership="MARKET_CAPTURE",
            state_version=state_version or event_type,
        )
        return self.publish(env)

    def publish_critical(
        self,
        *,
        incident_id: str,
        failure_type: str,
        content: str,
        severity: Severity = Severity.CRITICAL,
        incident_state: str = "active",
        artifact_path: str = "",
        trading_date: str = "",
    ) -> dict[str, Any]:
        from notify.discord_notification_model import ActualOrShadow

        env = build_envelope(
            category=NotificationCategory.CRITICAL_SAFETY,
            severity=severity,
            event_type=failure_type,
            title="[CRITICAL SAFETY]",
            content=content,
            trading_date=trading_date or None,
            dedupe_key=f"critical|{incident_id}|{incident_state}",
            actual_or_shadow=ActualOrShadow.NONE,
            source_module="critical_safety",
            ownership="CRITICAL_SAFETY",
            action_required=True,
            artifact_path=artifact_path,
            incident_id=incident_id,
            incident_state=incident_state,
        )
        return self.publish(env)

    def readiness(self) -> dict[str, Any]:
        env_public: dict[str, Any] = {}
        try:
            from small_paper.env_loader import ensure_repo_dotenv

            env_public = ensure_repo_dotenv().as_public_dict()
        except Exception as exc:
            env_public = {"dotenv_loaded": False, "error": type(exc).__name__}
        cfg = self.configured_categories()
        missing = [k for k, v in cfg.items() if not v]
        blockers: list[str] = []
        try:
            probe = self.audit.dir / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)  # type: ignore[arg-type]
            audit_ok = True
        except Exception:
            audit_ok = False
            blockers.append("audit_not_writable")
        dedupe_ok = self.dedupe.valid()
        if not dedupe_ok:
            blockers.append("dedupe_store_corrupted")
        ws = self.worker.status()
        return {
            "configured_categories": cfg,
            "missing_webhook_categories": missing,
            "env": env_public,
            "webhook_configured": env_public.get("webhook_configured") or {},
            "queue_writable": True,
            "audit_output_writable": audit_ok,
            "dedupe_store_valid": dedupe_ok,
            "worker_status": ws,
            "last_send_status": ws.get("worker_status"),
            "rate_limit_state": self.rate.snapshot(),
            "secret_masking": True,
            "notification_ready": audit_ok and dedupe_ok,
            "blockers": blockers,
            "webhook_missing_does_not_block_paper": True,
            "external_send_default": 0,
        }


def get_router(native_root: Optional[Path] = None) -> DiscordNotificationRouter:
    global _ROUTER
    with _LOCK:
        if _ROUTER is None:
            root = native_root or Path(__file__).resolve().parents[2]
            _ROUTER = DiscordNotificationRouter(root)
        return _ROUTER


def reset_router_for_tests() -> None:
    global _ROUTER
    with _LOCK:
        if _ROUTER is not None:
            try:
                _ROUTER.worker.stop(flush_sec=0.1)
            except Exception:
                pass
        _ROUTER = None
