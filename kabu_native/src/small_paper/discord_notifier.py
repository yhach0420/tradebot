"""
Phase 46–47: Small paper pilot Discord observer (judgment events — no orders).

Webhooks:
- Trade notify (adopted ENTRY / EXIT / Refresh / Daily Summary):
  KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL, fallback KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL
- Trade cap blocked (ENTRY qualified but position cap full):
  KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL only (no trade-notify fallback)
- Legacy observer (HEARTBEAT / HOLD / TAKE / REJECT / ERROR / SESSION SUMMARY):
  KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL only — not KABU_SHADOW / Yahoo / IssueBot.
"""

from __future__ import annotations

import logging
import os
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import requests

from small_paper.reject_reasons import (
    REJECT_MAX_CONCURRENT,
    is_entry_blocked_discord_notify_reason,
)
from small_paper.discord_message_builder import (
    build_daily_summary_detail,
    build_entry_cap_blocked_detail,
    build_entry_deferred_detail,
    build_entry_detail,
    build_entry_embed_payload,
    build_exit_detail,
    build_exit_embed_payload,
    build_cap_blocked_embed_payload,
    build_summary_embed_payload,
    format_heartbeat_runtime_health_fields,
    summary_notification_labels,
    build_universe_refresh_overview,
    build_universe_screening_overview,
    split_watch_symbols_discord_fields,
    format_slot_usage,
    format_position_slot_pair,
    format_time_hms_jst,
)
from small_paper.discord_symbol_names import format_symbol_display, get_cached_symbol_name_map
from small_paper.discord_entry_delivery import (
    CLASS_HTTP_FAILED,
    CLASS_NOTIFY_NOT_CALLED,
    CLASS_NO_RETRY_TERMINATED,
    CLASS_OTHER,
    CLASS_WEBHOOK_SEND_FAILED,
    DiscordPostResult,
    DeliveryAuditCallback,
    EntryNotifyRetryQueue,
    FINAL_DELIVERED,
    FINAL_FAILED,
    FINAL_SKIPPED,
    FINAL_SUPPRESSED,
    _PendingEntryNotify,
    now_iso,
    webhook_url_hash,
)

log = logging.getLogger("kabu_native.small_paper.discord")

JST = ZoneInfo("Asia/Tokyo")

_DISPLAY_TAKE_PCT = 4.0
_DEFAULT_HARD_STOP_PCT = 1.20
_LEGACY_WEBHOOK_ENV = "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL"
_TRADE_NOTIFY_WEBHOOK_ENV = "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL"
_CAP_BLOCKED_WEBHOOK_ENV = "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL"

ErrorLogger = Callable[[str, str, Mapping[str, Any]], None]


@dataclass(frozen=True)
class SmallPaperDiscordConfig:
    enabled: bool = False
    observer_only: bool = True
    send_rejects: bool = False
    send_entry_deferred_max_concurrent: bool = True
    send_entry_cap_blocked: bool = True
    entry_deferred_cooldown_sec: float = 1800.0
    entry_deferred_min_score_v2: int = 5
    entry_deferred_daily_max: int = 50
    send_universe_refresh: bool = True
    send_daily_summary: bool = True
    max_concurrent_positions: int = 5
    position_cap_mode: bool = False
    heartbeat_min: float = 30.0
    webhook_env: str = _LEGACY_WEBHOOK_ENV
    trade_notify_webhook_env: str = _TRADE_NOTIFY_WEBHOOK_ENV
    trade_cap_blocked_webhook_env: str = _CAP_BLOCKED_WEBHOOK_ENV
    cooldown_sec: float = 60.0
    hard_stop_pct: float = _DEFAULT_HARD_STOP_PCT
    hold_min: float = 15.0
    hold_quality_delta: float = 0.03
    take_quality_drop: float = 0.08


def _fmt_num(v: Any, *, digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def _optional_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _stop_take(entry_price: float, hard_stop_pct: float) -> tuple[float, float]:
    stop = entry_price * (1.0 - float(hard_stop_pct) / 100.0)
    take = entry_price * (1.0 + _DISPLAY_TAKE_PCT / 100.0)
    return stop, take


def _vwap_distance_pct(payload: Mapping[str, Any], entry_price: float) -> Optional[float]:
    vwap = payload.get("VWAP")
    if vwap is None or entry_price <= 0:
        return None
    try:
        return (float(entry_price) - float(vwap)) / float(vwap) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None


class SmallPaperDiscordNotifier:
    """Observer-only Discord embeds; failures never raise."""

    def __init__(
        self,
        cfg: SmallPaperDiscordConfig,
        *,
        profile: str,
        entry_profile: str,
        policy_label: str = "paper_cap5",
        min_continuation_quality: float = 0.55,
        error_logger: Optional[ErrorLogger] = None,
        delivery_audit: Optional[DeliveryAuditCallback] = None,
    ) -> None:
        self.cfg = cfg
        self.profile = profile
        self.entry_profile = entry_profile
        self.policy_label = policy_label
        self.min_continuation_quality = min_continuation_quality
        self._error_logger = error_logger
        self._delivery_audit = delivery_audit
        self.entry_retry_queue = EntryNotifyRetryQueue(max_retries=3)
        self._legacy_webhook_url = ""
        self._trade_webhook_url = ""
        self._trade_webhook_source = ""
        self._cap_blocked_webhook_url = ""
        self._last_sent_mono: dict[str, float] = {}
        self._last_heartbeat_mono: float = 0.0
        self.discord_error_count: int = 0
        self.cap_blocked_notify_attempt_count: int = 0
        self.cap_blocked_notify_sent_count: int = 0
        self._notify_sequence: int = 0
        try:
            from small_paper.env_loader import ensure_repo_dotenv

            ensure_repo_dotenv()
        except Exception:
            pass

    def _ensure_env(self) -> None:
        try:
            from small_paper.env_loader import ensure_repo_dotenv

            ensure_repo_dotenv()
        except Exception:
            pass

    def _next_sequence_id(self) -> int:
        self._notify_sequence += 1
        return self._notify_sequence

    @property
    def active(self) -> bool:
        return bool(
            self.cfg.enabled
            and self.cfg.observer_only
            and (self._resolve_trade_webhook()[0] or self._resolve_legacy_webhook())
        )

    def cap_blocked_notify_enabled(self) -> bool:
        """Cap-blocked channel is independent of trade-notify (no fallback)."""
        return bool(
            self.cfg.enabled
            and self.cfg.observer_only
            and (
                self.cfg.send_entry_cap_blocked
                or self.cfg.send_entry_deferred_max_concurrent
            )
        )

    def cap_blocked_channel_ready(self) -> bool:
        return bool(self._resolve_cap_blocked_webhook())

    def trade_webhook_source(self) -> str:
        """``notify`` | ``legacy_fallback`` | ```` (empty if unresolved)."""
        self._resolve_trade_webhook()
        return self._trade_webhook_source

    def heartbeat_interval_sec(self) -> float:
        return max(60.0, float(self.cfg.heartbeat_min) * 60.0)

    def should_send_heartbeat(self) -> bool:
        if not self.active:
            return False
        if self._last_heartbeat_mono <= 0:
            return True
        return (time.monotonic() - self._last_heartbeat_mono) >= self.heartbeat_interval_sec()

    def _resolve_legacy_webhook(self) -> str:
        self._ensure_env()
        if self._legacy_webhook_url:
            return self._legacy_webhook_url
        env_name = (self.cfg.webhook_env or _LEGACY_WEBHOOK_ENV).strip()
        url = (os.getenv(env_name) or "").strip()
        if url:
            self._legacy_webhook_url = url
        return url

    def _resolve_trade_webhook(self) -> tuple[str, str]:
        self._ensure_env()
        if self._trade_webhook_url:
            return self._trade_webhook_url, self._trade_webhook_source
        notify_env = (self.cfg.trade_notify_webhook_env or _TRADE_NOTIFY_WEBHOOK_ENV).strip()
        url = (os.getenv(notify_env) or "").strip()
        if url:
            self._trade_webhook_url = url
            self._trade_webhook_source = "notify"
            return url, "notify"
        legacy = self._resolve_legacy_webhook()
        if legacy:
            self._trade_webhook_url = legacy
            self._trade_webhook_source = "legacy_fallback"
            return legacy, "legacy_fallback"
        return "", ""

    def _resolve_cap_blocked_webhook(self) -> str:
        self._ensure_env()
        if self._cap_blocked_webhook_url:
            return self._cap_blocked_webhook_url
        env_name = (self.cfg.trade_cap_blocked_webhook_env or _CAP_BLOCKED_WEBHOOK_ENV).strip()
        url = (os.getenv(env_name) or "").strip()
        if url:
            self._cap_blocked_webhook_url = url
        return url

    def _cooldown_ok(self, key: str, *, cooldown_sec: Optional[float] = None) -> bool:
        last = self._last_sent_mono.get(key)
        if last is None:
            return True
        wait = float(cooldown_sec if cooldown_sec is not None else self.cfg.cooldown_sec)
        return (time.monotonic() - last) >= wait

    def _mark_sent(self, key: str) -> None:
        self._last_sent_mono[key] = time.monotonic()

    def _max_slots(self) -> int:
        return max(1, int(self.cfg.max_concurrent_positions))

    def _header_content(self, event_tag: str, title_line: str = "") -> str:
        # Phase687W25C: embed-only cards — no plain-text banner duplication
        del event_tag, title_line
        return ""

    def _default_footer(self, *, test_mode: bool = False) -> str:
        from small_paper.discord_message_builder import PAPER_ONLY_FOOTER, TEST_FOOTER

        return TEST_FOOTER if test_mode else PAPER_ONLY_FOOTER

    def _policy_fields(self) -> list[dict[str, Any]]:
        return [
            {"name": "policy_label", "value": self.policy_label, "inline": True},
            {
                "name": "min_quality",
                "value": _fmt_num(self.min_continuation_quality, digits=2),
                "inline": True,
            },
        ]

    def _log_failure(
        self,
        op: str,
        detail: str,
        extra: Optional[Mapping[str, Any]] = None,
        *,
        error_type: str = "discord_error",
    ) -> None:
        self.discord_error_count += 1
        log.warning("[SMALL_PAPER_DISCORD] %s: %s", op, detail)
        if self._error_logger:
            payload = {"operation": op, **dict(extra or {})}
            payload.setdefault("error_type", error_type)
            self._error_logger(
                "discord_notify",
                detail,
                payload,
            )

    def _log_entry_notify_failure(
        self,
        *,
        symbol: str,
        event_time: str,
        detail: str,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._log_failure(
            "entry_notify",
            detail,
            {
                "symbol": symbol,
                "event_time": event_time,
                **dict(extra or {}),
            },
            error_type="discord_entry_notify_failed",
        )

    def _post(
        self,
        *,
        event_tag: str,
        title_line: str,
        fields: list[dict[str, Any]],
        color: int,
        dedupe_key: Optional[str] = None,
        cooldown_sec: Optional[float] = None,
        trade_notify: bool = False,
        cap_blocked: bool = False,
        sequence_id: Optional[int] = None,
        description: str = "",
        footer_text: Optional[str] = None,
        content: str = "",
    ) -> bool:
        return self._post_with_result(
            event_tag=event_tag,
            title_line=title_line,
            fields=fields,
            color=color,
            dedupe_key=dedupe_key,
            cooldown_sec=cooldown_sec,
            trade_notify=trade_notify,
            cap_blocked=cap_blocked,
            sequence_id=sequence_id,
            description=description,
            footer_text=footer_text,
            content=content,
        ).final_result == FINAL_DELIVERED

    def _post_with_result(
        self,
        *,
        event_tag: str,
        title_line: str,
        fields: list[dict[str, Any]],
        color: int,
        dedupe_key: Optional[str] = None,
        cooldown_sec: Optional[float] = None,
        trade_notify: bool = False,
        cap_blocked: bool = False,
        sequence_id: Optional[int] = None,
        payload_prebuilt: bool = True,
        description: str = "",
        footer_text: Optional[str] = None,
        content: str = "",
    ) -> DiscordPostResult:
        res = DiscordPostResult(payload_built=payload_prebuilt)
        if not self.cfg.enabled or not self.cfg.observer_only:
            res.final_result = FINAL_SKIPPED
            res.suppressed_reason = "discord_disabled_or_not_observer_only"
            res.failure_classification = CLASS_NOTIFY_NOT_CALLED
            return res
        if dedupe_key and not self._cooldown_ok(dedupe_key, cooldown_sec=cooldown_sec):
            res.final_result = FINAL_SUPPRESSED
            res.suppressed_reason = f"cooldown:{dedupe_key}"
            res.failure_classification = CLASS_OTHER
            return res
        if cap_blocked:
            webhook = self._resolve_cap_blocked_webhook()
            env_hint = self.cfg.trade_cap_blocked_webhook_env
            source = "cap_blocked"
        elif trade_notify:
            if not self.active:
                res.final_result = FINAL_SKIPPED
                res.suppressed_reason = "trade_notify_inactive"
                res.failure_classification = CLASS_NOTIFY_NOT_CALLED
                return res
            webhook, source = self._resolve_trade_webhook()
            env_hint = self.cfg.trade_notify_webhook_env
            if source == "legacy_fallback":
                env_hint = f"{self.cfg.trade_notify_webhook_env} (fallback {self.cfg.webhook_env})"
        else:
            if not self.active:
                res.final_result = FINAL_SKIPPED
                res.suppressed_reason = "legacy_inactive"
                res.failure_classification = CLASS_NOTIFY_NOT_CALLED
                return res
            webhook = self._resolve_legacy_webhook()
            env_hint = self.cfg.webhook_env
            source = "legacy"
        if not webhook:
            res.final_result = FINAL_FAILED
            res.failure_classification = CLASS_WEBHOOK_SEND_FAILED
            res.failure_reason = f"webhook_empty:{env_hint}"
            self._log_failure("webhook", f"env {env_hint} empty", {"channel": source})
            return res

        seq = sequence_id if sequence_id is not None else self._next_sequence_id()
        foot = footer_text if footer_text is not None else self._default_footer()
        embed: dict[str, Any] = {
            "title": title_line[:256],
            "color": color,
            "fields": fields,
            "footer": {"text": f"{foot}"[:2048]},
        }
        if description:
            embed["description"] = description[:2048]
        # Embed-only: avoid duplicate banners in message content
        payload: dict[str, Any] = {"embeds": [embed]}
        if content:
            payload["content"] = content[:1800]
        res.webhook_url_hash = webhook_url_hash(webhook)
        res.webhook_called = True
        # Phase687W10: async worker — never block PUSH/ENTRY/EXIT on Discord HTTP
        try:
            from notify.discord_notification_model import (
                ActualOrShadow,
                NotificationCategory,
                Severity,
                build_envelope,
            )
            from notify.discord_notification_router import get_router

            if cap_blocked:
                category = NotificationCategory.CAP_BLOCKED
                aos = ActualOrShadow.NONE
                ownership = "PAPER_RUNTIME"
            elif trade_notify:
                tag = (event_tag or "").upper()
                if tag in ("ENTRY", "EXIT"):
                    category = NotificationCategory.TRADE_ACTUAL
                    aos = ActualOrShadow.ACTUAL
                elif "SUMMARY" in tag or tag in ("AM", "PM", "DAILY"):
                    category = NotificationCategory.SESSION_SUMMARY
                    aos = ActualOrShadow.ACTUAL
                else:
                    category = NotificationCategory.SESSION_SUMMARY
                    aos = ActualOrShadow.ACTUAL
                ownership = "PAPER_RUNTIME"
            else:
                category = NotificationCategory.OPERATIONS
                aos = ActualOrShadow.OPERATIONS
                ownership = "PAPER_RUNTIME"

            native = Path(__file__).resolve().parents[2]
            env = build_envelope(
                category=category,
                severity=Severity.INFO if category != NotificationCategory.CAP_BLOCKED else Severity.NOTICE,
                event_type=event_tag or "NOTIFY",
                title=title_line,
                content=str(payload.get("content") or ""),
                embeds=list(payload.get("embeds") or []),
                dedupe_key=dedupe_key or "",
                actual_or_shadow=aos,
                source_module="discord_notifier",
                ownership=ownership,
            )
            # Prefer configured category webhook; if empty, use already-resolved webhook via direct enqueue
            router = get_router(native)
            outcome = router.publish(env)
            if outcome.get("status") == "SKIPPED_WEBHOOK_NOT_CONFIGURED" and webhook:
                # Compatibility: existing trade/legacy/cap URLs still work via direct enqueue
                env.webhook_env_key = env_hint
                q = router.worker.enqueue(env, webhook)
                outcome = q
                if q.get("queued") and dedupe_key:
                    # Queue ≠ HTTP delivered. Persist QUEUED; worker upgrades to SENT.
                    router.dedupe.record(
                        dedupe_key=dedupe_key,
                        status="QUEUED",
                        notification_id=env.notification_id,
                        payload_hash=env.payload_hash,
                    )
            status = str(outcome.get("status") or "")
            if status in ("QUEUED", "SENT"):
                # Fail-open for trading path: do not block on HTTP. Local cooldown only.
                if dedupe_key:
                    self._mark_sent(dedupe_key)
                res.final_result = FINAL_DELIVERED if status == "SENT" else FINAL_DELIVERED
                res.sent_time = now_iso()
                res.suppressed_reason = None if status == "SENT" else "queued_awaiting_http"
                return res
            if status in ("DEDUPED", "RATE_LIMITED"):
                res.final_result = FINAL_SUPPRESSED
                res.suppressed_reason = status.lower()
                return res
            if status == "SKIPPED_WEBHOOK_NOT_CONFIGURED":
                res.final_result = FINAL_SKIPPED
                res.suppressed_reason = "webhook_not_configured"
                return res
            # DROPPED / FAILED — fail-open for trading (do not raise)
            res.final_result = FINAL_FAILED
            res.failure_reason = status
            res.failure_classification = CLASS_WEBHOOK_SEND_FAILED
            return res
        except Exception as e:
            # Fail-open: trading continues even if notify stack breaks
            from notify.discord_notification_audit import mask_secrets_text

            res.final_result = FINAL_FAILED
            res.failure_classification = CLASS_WEBHOOK_SEND_FAILED
            res.exception_type = type(e).__name__
            # Never store raw exception text (may embed webhook URL / tokens)
            res.exception_message = mask_secrets_text(str(e))[:240]
            if "webhook" in res.exception_message.lower() or "discord.com" in res.exception_message.lower():
                res.exception_message = "[REDACTED]"
            res.failure_reason = res.exception_type
            self._log_failure(
                "post_async",
                res.exception_type,
                {"exception_type": res.exception_type, "error_category": "notify_stack"},
            )
            return res

    def _emit_entry_delivery_audit(
        self,
        *,
        result: DiscordPostResult,
        event: Mapping[str, Any],
        sequence_id: int,
        persisted_to_log: bool,
    ) -> None:
        if self._delivery_audit is None:
            return
        self._delivery_audit(
            result.to_audit_record(
                symbol=str(event.get("symbol") or ""),
                event_time=str(event.get("event_time") or ""),
                position_id=str(event.get("position_id") or ""),
                session_id=str(event.get("session_id") or ""),
                sequence_id=sequence_id,
                persisted_to_log=persisted_to_log,
            )
        )

    def _send_pending_entry_notify(self, item: _PendingEntryNotify) -> DiscordPostResult:
        return self.notify_entry(
            event=item.event,
            payload=item.payload,
            open_slots=item.open_slots,
            session_bucket=item.session_bucket,
            slot_before=item.slot_before,
            score5_candidate_ordinal=item.score5_candidate_ordinal,
            sequence_id=item.sequence_id,
            is_retry=True,
            retry_attempt=item.attempt,
        )

    def flush_entry_notify_retries(self) -> list[DiscordPostResult]:
        return self.entry_retry_queue.flush(
            self._send_pending_entry_notify,
            audit=self._delivery_audit,
        )

    def notify_entry(
        self,
        *,
        event: Mapping[str, Any],
        payload: Mapping[str, Any],
        open_slots: int,
        session_bucket: str,
        score5_candidate_ordinal: Optional[int] = None,
        ux_stats: Optional[DiscordUxSessionStats] = None,
        entry_signal_mono: Optional[float] = None,
        notify_mono: Optional[float] = None,
        slot_before: Optional[int] = None,
        sequence_id: Optional[int] = None,
        is_retry: bool = False,
        retry_attempt: int = 0,
    ) -> DiscordPostResult:
        sym = str(event.get("symbol") or "")
        # Official ENTRY price only: validated entry_price first; never invent 0円
        entry_px = (
            event.get("entry_price")
            or event.get("validated_entry_price")
            or event.get("current_price")
            or payload.get("CurrentPrice")
        )
        try:
            entry_f = float(entry_px) if entry_px is not None and str(entry_px).strip() != "" else None
        except (TypeError, ValueError):
            entry_f = None
        if entry_f is None or entry_f <= 0:
            entry_f = None
        stop_px, _ = (
            _stop_take(entry_f, self.cfg.hard_stop_pct) if entry_f is not None else (None, None)
        )
        merged: dict[str, Any] = {**dict(payload), **dict(event)}
        if entry_signal_mono is not None and notify_mono is not None:
            merged["signal_to_notify_latency_ms"] = round(
                (float(notify_mono) - float(entry_signal_mono)) * 1000.0,
                1,
            )
        v2_raw = merged.get("entry_expectancy_score_v2")
        try:
            v2 = int(v2_raw) if v2_raw is not None and v2_raw != "" else None
        except (TypeError, ValueError):
            v2 = None
        pre_slot = slot_before
        if pre_slot is None and event.get("position_slot_before") is not None:
            try:
                pre_slot = int(event.get("position_slot_before"))
            except (TypeError, ValueError):
                pre_slot = None
        slot = format_position_slot_pair(pre_slot, open_slots, self._max_slots())
        name_map = get_cached_symbol_name_map()
        event_time = str(event.get("event_time") or "")
        seq = sequence_id if sequence_id is not None else self._next_sequence_id()
        # Keep text builder for audit/JSONL callers; Discord uses embed card
        _ = build_entry_detail(
            symbol=sym,
            entry_price=entry_f,
            stop_price=stop_px,
            slot_usage=slot,
            entry_score_v2=v2,
            data=merged,
            score5_candidate_ordinal=score5_candidate_ordinal,
            name_map=name_map,
            entry_time=event_time,
            sent_time="",
            sequence_id=seq,
        )
        reentry_info: dict[str, Any] = {}
        try:
            from pathlib import Path

            from small_paper.daily_symbol_discord_state import get_daily_symbol_state

            native = Path(__file__).resolve().parents[2]
            day_state = get_daily_symbol_state(native_root=native)
            if is_retry:
                st = day_state.get(sym)
                n = int(st.entry_count_today)
                reentry_info = {
                    "entry_count_today_after": n,
                    "is_reentry": n >= 2,
                }
                if n >= 2 and st.previous_exit_at:
                    from small_paper.daily_symbol_discord_state import elapsed_label
                    from small_paper.discord_message_builder import (
                        format_time_hms_jst,
                        humanize_exit_reason,
                    )

                    reentry_info.update(
                        {
                            "previous_exit_reason": st.previous_exit_reason,
                            "previous_exit_reason_ja": humanize_exit_reason(st.previous_exit_reason),
                            "previous_exit_at": st.previous_exit_at,
                            "previous_exit_time_hms": format_time_hms_jst(st.previous_exit_at),
                            "previous_exit_elapsed": elapsed_label(st.previous_exit_at, event_time),
                            "previous_exit_price": st.previous_exit_price,
                        }
                    )
            else:
                reentry_info = day_state.record_accepted_entry(sym, entry_time=event_time)
        except Exception:
            reentry_info = {}
        embed = build_entry_embed_payload(
            symbol=sym,
            entry_price=entry_f,
            slot_usage=slot,
            entry_score_v2=v2,
            data=merged,
            name_map=name_map,
            entry_time=event_time,
            stop_price=stop_px if stop_px else None,
            score5_candidate_ordinal=score5_candidate_ordinal,
            reentry_info=reentry_info,
        )
        post_res = DiscordPostResult(
            notify_entry_called=True,
            payload_built=True,
            retry_count=retry_attempt,
        )
        post_res = self._post_with_result(
            event_tag="ENTRY",
            title_line=str(embed["title"]),
            fields=list(embed.get("fields") or []),
            color=int(embed.get("color") or 0x2F855A),
            description=str(embed.get("description") or ""),
            footer_text=str(embed.get("footer") or ""),
            dedupe_key=f"entry|{sym}|{event.get('message_index')}",
            trade_notify=True,
            sequence_id=seq,
            payload_prebuilt=True,
        )
        post_res.notify_entry_called = True
        post_res.payload_built = True
        post_res.retry_count = retry_attempt
        if post_res.final_result != FINAL_DELIVERED and not is_retry:
            if post_res.final_result == FINAL_FAILED:
                self.entry_retry_queue.enqueue(
                    _PendingEntryNotify(
                        event=dict(event),
                        payload=dict(payload),
                        open_slots=open_slots,
                        session_bucket=session_bucket,
                        slot_before=pre_slot,
                        score5_candidate_ordinal=score5_candidate_ordinal,
                        sequence_id=seq,
                        attempt=0,
                    )
                )
        if post_res.final_result != FINAL_DELIVERED:
            self._log_entry_notify_failure(
                symbol=sym,
                event_time=event_time,
                detail=post_res.failure_reason or "ENTRY Discord notify failed",
                extra={
                    "position_slot_before": pre_slot,
                    "position_slot_after": open_slots,
                    "session_id": event.get("session_id"),
                    "position_id": event.get("position_id"),
                    "sequence_id": seq,
                    "http_status": post_res.http_status,
                    "exception_type": post_res.exception_type,
                    "failure_classification": post_res.failure_classification,
                    "retry_count": post_res.retry_count,
                },
            )
        persisted = False
        if post_res.final_result == FINAL_DELIVERED:
            if ux_stats is not None and v2 is not None and v2 >= self.cfg.entry_deferred_min_score_v2:
                ux_stats.record_score5_entry()
            persisted = True
        self._emit_entry_delivery_audit(
            result=post_res,
            event=event,
            sequence_id=seq,
            persisted_to_log=persisted,
        )
        return post_res

    def notify_entry_cap_blocked(
        self,
        *,
        event: Mapping[str, Any],
        payload: Mapping[str, Any],
        trade_data: Mapping[str, Any],
        open_slots: int,
        score5_candidate_ordinal: Optional[int] = None,
        ux_stats: Optional[DiscordUxSessionStats] = None,
        block_reason: Optional[str] = None,
    ) -> bool:
        if not self.cap_blocked_notify_enabled():
            return False
        self.cap_blocked_notify_attempt_count += 1
        sym = str(event.get("symbol") or "")
        reason = str(
            block_reason
            or event.get("gate_reject_reason")
            or event.get("reject_reason")
            or REJECT_MAX_CONCURRENT
        )
        v2_raw = event.get("entry_expectancy_score_v2") or trade_data.get("entry_expectancy_score_v2")
        v2: Optional[int]
        try:
            v2 = int(v2_raw) if v2_raw is not None and v2_raw != "" else None
        except (TypeError, ValueError):
            v2 = None
        daily_max = int(self.cfg.entry_deferred_daily_max or 0)
        if (
            daily_max > 0
            and ux_stats is not None
            and ux_stats.entry_deferred_notify_count >= daily_max
        ):
            return False
        merged: dict[str, Any] = {**dict(payload), **dict(trade_data), **dict(event)}
        if score5_candidate_ordinal is not None:
            merged["score5_candidate_ordinal"] = score5_candidate_ordinal
        cap = self._max_slots()
        name_map = get_cached_symbol_name_map()
        _ = build_entry_cap_blocked_detail(
            symbol=sym,
            entry_score_v2=v2,
            data=merged,
            active_positions=int(open_slots),
            position_cap=cap,
            name_map=name_map,
            block_reason=reason,
        )
        embed = build_cap_blocked_embed_payload(
            symbol=sym,
            entry_score_v2=v2,
            data=merged,
            active_positions=int(open_slots),
            position_cap=cap,
            name_map=name_map,
            block_reason=reason,
        )
        ok = self._post(
            event_tag="CAP BLOCKED",
            title_line=str(embed["title"]),
            fields=list(embed.get("fields") or []),
            description=str(embed.get("description") or ""),
            footer_text=str(embed.get("footer") or ""),
            color=int(embed.get("color") or 0xDD6B20),
            dedupe_key=f"cap_blocked|{sym}|{reason}",
            cooldown_sec=float(self.cfg.entry_deferred_cooldown_sec),
            cap_blocked=True,
        )
        if ok:
            self.cap_blocked_notify_sent_count += 1
        if ok and ux_stats is not None and v2 is not None:
            ux_stats.record_entry_deferred_notify(symbol=sym, entry_score_v2=v2)
        return ok

    def notify_entry_deferred_max_concurrent(
        self,
        *,
        event: Mapping[str, Any],
        payload: Mapping[str, Any],
        trade_data: Mapping[str, Any],
        open_slots: int,
        open_positions: Sequence[Mapping[str, Any]],
        score5_candidate_ordinal: Optional[int] = None,
        ux_stats: Optional[DiscordUxSessionStats] = None,
    ) -> bool:
        del open_positions
        return self.notify_entry_cap_blocked(
            event=event,
            payload=payload,
            trade_data=trade_data,
            open_slots=open_slots,
            score5_candidate_ordinal=score5_candidate_ordinal,
            ux_stats=ux_stats,
        )

    def notify_hold(self, *, context: Mapping[str, Any]) -> bool:
        comps = context.get("components") or {}
        sym = str(context.get("symbol", ""))
        fields = [
            *self._policy_fields(),
            {
                "name": "continuation_quality",
                "value": _fmt_num(context.get("continuation_quality"), digits=4),
                "inline": True,
            },
            {
                "name": "bullish_continuation",
                "value": _fmt_num(comps.get("bullish_continuation"), digits=4),
                "inline": True,
            },
            {
                "name": "momentum_continuation",
                "value": _fmt_num(comps.get("momentum_continuation"), digits=4),
                "inline": True,
            },
            {
                "name": "favorable_continuation",
                "value": _fmt_num(comps.get("favorable_continuation"), digits=4),
                "inline": True,
            },
            {
                "name": "bearish_accumulation",
                "value": _fmt_num(comps.get("bearish_accumulation"), digits=4),
                "inline": True,
            },
            {
                "name": "current_pnl_pct",
                "value": _fmt_num(context.get("unrealized_pnl_pct")),
                "inline": True,
            },
            {"name": "hold_reason", "value": str(context.get("hold_reason", "—")), "inline": False},
            {
                "name": "continuation_persistence",
                "value": _fmt_num(context.get("continuation_persistence"), digits=4),
                "inline": True,
            },
            {"name": "session_bucket", "value": str(context.get("session_bucket", "—")), "inline": True},
            {"name": "hold_duration_sec", "value": _fmt_num(context.get("hold_duration_sec"), digits=0), "inline": True},
        ]
        return self._post(
            event_tag="HOLD",
            title_line=f"HOLD {sym}",
            fields=fields,
            color=0x3182CE,
            dedupe_key=f"hold|{sym}|{context.get('hold_reason')}",
        )

    def notify_take(self, *, context: Mapping[str, Any]) -> bool:
        sym = str(context.get("symbol", ""))
        fields = [
            *self._policy_fields(),
            {
                "name": "OBSERVER SIGNAL ONLY",
                "value": "NOT EXIT — do not place sell/order from this notification",
                "inline": False,
            },
            {
                "name": "phase54_replay_note",
                "value": "TAKE after-extension rate was high in replay (~79%); signal for review only",
                "inline": False,
            },
            {"name": "symbol", "value": sym, "inline": True},
            {
                "name": "unrealized_pnl_pct",
                "value": _fmt_num(context.get("unrealized_pnl_pct")),
                "inline": True,
            },
            {"name": "take_reason", "value": str(context.get("take_reason", "—")), "inline": False},
            {
                "name": "observer_note",
                "value": "TAKE = observation signal only (not auto-sell)",
                "inline": False,
            },
            {
                "name": "continuation_weakening",
                "value": str(context.get("continuation_weakening", False)),
                "inline": True,
            },
            {
                "name": "favorable_fade",
                "value": str(context.get("favorable_fade", False)),
                "inline": True,
            },
            {
                "name": "quality_deterioration",
                "value": str(context.get("quality_deterioration", False)),
                "inline": True,
            },
            {
                "name": "hold_duration_sec",
                "value": _fmt_num(context.get("hold_duration_sec"), digits=0),
                "inline": True,
            },
            {"name": "peak_pnl_pct", "value": _fmt_num(context.get("peak_pnl_pct")), "inline": True},
        ]
        return self._post(
            event_tag="TAKE",
            title_line=f"TAKE {sym} | OBSERVER ONLY — NOT EXIT",
            fields=fields,
            color=0xD69E2E,
            dedupe_key=f"take|{sym}|{context.get('take_reason')}",
        )

    def notify_exit(self, *, context: Mapping[str, Any]) -> bool:
        if not context.get("is_structural_exit"):
            return False
        sym = str(context.get("symbol", ""))
        reason = str(context.get("exit_reason", "—"))
        try:
            exit_px = float(context.get("current_price") or 0)
        except (TypeError, ValueError):
            exit_px = 0.0
        try:
            pnl = float(context.get("realized_pnl_pct") or context.get("unrealized_pnl_pct") or 0)
        except (TypeError, ValueError):
            pnl = 0.0
        try:
            hold_sec = float(context.get("hold_sec") or context.get("hold_duration_sec") or 0)
        except (TypeError, ValueError):
            hold_sec = 0.0
        try:
            entry_px = float(context.get("entry_price") or 0)
        except (TypeError, ValueError):
            entry_px = 0.0
        mfe = context.get("mfe_pct") or context.get("peak_mfe_pct") or context.get("max_favorable")
        mae = context.get("mae_pct") or context.get("max_adverse") or context.get("rolling_mae_pct")
        yen_raw = context.get("pnl_yen_100")
        try:
            pnl_yen_100 = float(yen_raw) if yen_raw is not None else None
        except (TypeError, ValueError):
            pnl_yen_100 = None
        name_map = get_cached_symbol_name_map()
        exit_time = str(
            context.get("exit_time") or context.get("event_time") or context.get("timestamp") or ""
        )
        sent_time = datetime.now(JST).isoformat(timespec="milliseconds")
        seq = self._next_sequence_id()
        _ = build_exit_detail(
            symbol=sym,
            entry_price=entry_px,
            exit_price=exit_px,
            pnl_pct=pnl,
            mfe_pct=float(mfe) if mfe is not None else None,
            mae_pct=float(mae) if mae is not None else None,
            hold_minutes=hold_sec / 60.0,
            exit_reason=reason,
            pnl_yen_100=pnl_yen_100,
            side=str(context.get("side") or "long"),
            board_dynamic_trailing_tier=str(
                context.get("board_dynamic_trailing_tier") or ""
            )
            or None,
            board_dynamic_trailing_activate_pct=context.get(
                "board_dynamic_trailing_activate_pct"
            ),
            board_dynamic_trailing_giveback_frac=context.get(
                "board_dynamic_trailing_giveback_frac"
            ),
            entry_time=str(
                context.get("entry_time")
                or context.get("market_entry_time")
                or ""
            )
            or None,
            exit_time=exit_time,
            name_map=name_map,
            market_time_age_sec=_optional_float(context.get("market_time_age_sec")),
            price_age_sec=_optional_float(context.get("price_age_sec")),
            stale_trade=bool(context.get("stale_trade")),
            price_freshness_source=str(
                context.get("price_freshness_source")
                or context.get("price_source")
                or ""
            )
            or None,
            sent_time=sent_time,
            session_id=str(context.get("session_id") or "") or None,
            position_id=str(context.get("position_id") or "") or None,
            sequence_id=seq,
        )
        symbol_cum: Optional[float] = None
        try:
            from pathlib import Path

            from small_paper.daily_symbol_discord_state import get_daily_symbol_state
            from replay.pnl_yen import resolve_pnl_yen_100 as _resolve_yen

            yen_for_state = pnl_yen_100
            if yen_for_state is None:
                yen_for_state = _resolve_yen(
                    entry_price=entry_px,
                    exit_price=exit_px,
                    side=str(context.get("side") or "long"),
                    pnl_yen_100=None,
                )
            native = Path(__file__).resolve().parents[2]
            day_state = get_daily_symbol_state(native_root=native)
            exit_snap = day_state.record_official_exit(
                sym,
                exit_reason=reason,
                exit_time=exit_time,
                exit_price=exit_px,
                pnl_yen_100=yen_for_state,
            )
            symbol_cum = float(exit_snap.get("realized_pnl_yen_100_today") or 0)
        except Exception:
            symbol_cum = None
        embed = build_exit_embed_payload(
            symbol=sym,
            entry_price=entry_px,
            exit_price=exit_px,
            pnl_pct=pnl,
            mfe_pct=float(mfe) if mfe is not None else None,
            mae_pct=float(mae) if mae is not None else None,
            hold_minutes=hold_sec / 60.0,
            exit_reason=reason,
            pnl_yen_100=pnl_yen_100,
            side=str(context.get("side") or "long"),
            board_dynamic_trailing_tier=str(
                context.get("board_dynamic_trailing_tier") or ""
            )
            or None,
            board_dynamic_trailing_activate_pct=context.get(
                "board_dynamic_trailing_activate_pct"
            ),
            board_dynamic_trailing_giveback_frac=context.get(
                "board_dynamic_trailing_giveback_frac"
            ),
            name_map=name_map,
            entry_time=str(
                context.get("entry_time")
                or context.get("market_entry_time")
                or ""
            )
            or None,
            exit_time=exit_time,
            market_time_age_sec=_optional_float(context.get("market_time_age_sec")),
            price_age_sec=_optional_float(context.get("price_age_sec")),
            board_age_sec=_optional_float(context.get("board_age_sec")),
            stale_trade=bool(context.get("stale_trade")),
            price_freshness_source=str(
                context.get("price_freshness_source")
                or context.get("price_source")
                or ""
            )
            or None,
            session_close=bool(context.get("session_close")),
            position_cap_mode=bool(self.cfg.position_cap_mode),
            symbol_pnl_yen_100_today=symbol_cum,
        )
        # Phase687W59: Forward tags only when present (research context; no false zeros)
        try:
            from small_paper.discord_current_system_summary import extract_exit_forward_tags

            tags = extract_exit_forward_tags(context)
            if tags:
                fields = list(embed.get("fields") or [])
                fields.append(
                    {
                        "name": "Forward tags",
                        "value": "\n".join(tags)[:1020],
                        "inline": False,
                    }
                )
                embed["fields"] = fields
                obs_t = context.get("observer_entry_time") or context.get("entry_time")
                age = context.get("market_time_age_sec")
                extra = []
                if obs_t:
                    extra.append(f"observer entry time: {obs_t}")
                if age is not None:
                    extra.append(f"market time age: {age}s")
                if extra:
                    desc = str(embed.get("description") or "")
                    embed["description"] = (desc + "\n" + "\n".join(extra))[:2048]
        except Exception:
            pass
        return self._post(
            event_tag="EXIT",
            title_line=str(embed["title"]),
            fields=list(embed.get("fields") or []),
            description=str(embed.get("description") or ""),
            footer_text=str(embed.get("footer") or ""),
            color=int(embed.get("color") or 0xC05621),
            dedupe_key=f"exit|{sym}|{reason}|{context.get('exit_time', '')}",
            trade_notify=True,
            sequence_id=seq,
        )

    def notify_universe_refresh(
        self,
        *,
        session_label: str,
        refresh_time: str,
        added_symbols: Sequence[str],
        removed_symbols: Sequence[str],
        watch_symbols: Sequence[str],
        status: str = "completed",
    ) -> bool:
        if not self.cfg.send_universe_refresh:
            return False
        name_map = get_cached_symbol_name_map()
        overview = build_universe_refresh_overview(
            session_label=session_label,
            refresh_time=refresh_time,
            added=added_symbols,
            removed=removed_symbols,
            watch_symbol_count=len(watch_symbols),
            name_map=name_map,
        )
        if status != "completed":
            overview = f"状態: {status}\n{overview}"
        fields: list[dict[str, Any]] = []
        if overview:
            fields.append({"name": "概要", "value": overview[:1020], "inline": False})
        for spec in split_watch_symbols_discord_fields(
            watch_symbols,
            name_map=name_map,
        ):
            fields.append(
                {
                    "name": spec["name"],
                    "value": spec["value"][:1020],
                    "inline": False,
                }
            )
        if not fields:
            fields = [{"name": "詳細", "value": overview[:1020] or "—", "inline": False}]
        return self._post(
            event_tag="Universe Refresh",
            title_line=f"【Universe Refresh】 {session_label} {refresh_time}",
            fields=fields,
            color=0x3182CE,
            dedupe_key=f"refresh|{session_label}|{refresh_time}",
            cooldown_sec=60.0,
            trade_notify=True,
        )

    def notify_universe_screening(
        self,
        *,
        session_label: str,
        watch_symbols: Sequence[str],
        day_stamp: str = "",
        status: str = "completed",
        generated_at: Optional[str] = None,
    ) -> bool:
        """Post initial watch list after AM/PM universe screening (not 10:00/14:30 refresh)."""
        if not self.cfg.send_universe_refresh:
            return False
        name_map = get_cached_symbol_name_map()
        sent_at = datetime.now(JST).isoformat(timespec="milliseconds")
        gen_at = generated_at or sent_at
        seq = self._next_sequence_id()
        overview = build_universe_screening_overview(
            session_label=session_label,
            watch_symbol_count=len(watch_symbols),
            name_map=name_map,
            generated_at=gen_at,
            sent_at=sent_at,
            sequence_id=seq,
        )
        if status != "completed":
            overview = f"状態: {status}\n{overview}"
        fields: list[dict[str, Any]] = []
        if overview:
            fields.append({"name": "概要", "value": overview[:1020], "inline": False})
        for spec in split_watch_symbols_discord_fields(watch_symbols, name_map=name_map):
            fields.append(
                {"name": spec["name"], "value": spec["value"][:1020], "inline": False}
            )
        if not fields:
            fields = [{"name": "詳細", "value": overview[:1020] or "—", "inline": False}]
        dedupe_day = (day_stamp or datetime.now(JST).strftime("%Y%m%d")).strip()
        return self._post(
            event_tag="Universe Screening",
            title_line=f"【Universe Screening】 {session_label}",
            fields=fields,
            color=0x2B6CB0,
            dedupe_key=f"screening|{session_label}|{dedupe_day}",
            cooldown_sec=43200.0,
            trade_notify=True,
            sequence_id=seq,
        )

    def _production_summary_embed(
        self,
        *,
        events: Sequence[Mapping[str, Any]],
        summary: Mapping[str, Any],
        monitored_symbol_count: Optional[int] = None,
        reject_rows: Optional[Sequence[Mapping[str, Any]]] = None,
        ux_stats: Optional[DiscordUxSessionStats] = None,
    ) -> Optional[dict[str, Any]]:
        del events, monitored_symbol_count, reject_rows, ux_stats
        if summary.get("summary_integrity_error"):
            log.warning(
                "Discord summary skipped: integrity error %s",
                summary.get("summary_integrity_error"),
            )
            return None
        canonical = summary.get("canonical_summary")
        if not isinstance(canonical, Mapping):
            log.warning("Discord summary skipped: missing canonical_summary")
            return None
        am_pm = str(summary.get("am_pm") or summary.get("session_kind") or "").upper()
        if not am_pm:
            tag, _ = summary_notification_labels(summary)
            if "AM" in tag.upper():
                am_pm = "AM"
            elif "PM" in tag.upper():
                am_pm = "PM"
        # Operator/debug sections stay in canonical_summary / JSONL — not Discord.
        day_yen = None
        reentry_audit = None
        try:
            from pathlib import Path

            from small_paper.daily_symbol_discord_state import get_daily_symbol_state, trading_date_jst

            native = Path(__file__).resolve().parents[2]
            day_state = get_daily_symbol_state(native_root=native)
            reentry_audit = day_state.summary_audit()
            # 本日累計: canonical only (AM preserved + current session)
            sess = float(canonical.get("total_pnl_yen_100") or 0)
            day_yen = sess
            if str(am_pm).upper() == "PM":
                day = str(summary.get("trading_date") or trading_date_jst())
                am_path = (
                    native
                    / "results"
                    / "small_paper"
                    / day
                    / "small_paper_summary_am.json"
                )
                # also check daily_runner copy
                alt = (
                    native
                    / "results"
                    / "reports"
                    / "daily_runner"
                    / f"daily_summary_am_{day}.json"
                )
                for p in (am_path, alt):
                    if not p.is_file():
                        # scan session dirs for AM copy
                        continue
                    try:
                        import json

                        am_sum = json.loads(p.read_text(encoding="utf-8"))
                        am_can = am_sum.get("canonical_summary") or am_sum
                        if isinstance(am_can, Mapping):
                            day_yen = float(am_can.get("total_pnl_yen_100") or 0) + sess
                            break
                    except Exception:
                        continue
                else:
                    # fallback: session dirs
                    day_dir = native / "results" / "small_paper" / day
                    if day_dir.is_dir():
                        for p in sorted(day_dir.glob("**/small_paper_summary_am.json")):
                            try:
                                import json

                                am_sum = json.loads(p.read_text(encoding="utf-8"))
                                am_can = am_sum.get("canonical_summary") or am_sum
                                if isinstance(am_can, Mapping):
                                    day_yen = float(am_can.get("total_pnl_yen_100") or 0) + sess
                                    break
                            except Exception:
                                continue
            # Attach day total onto metrics view without mutating SoT
        except Exception:
            day_yen = canonical.get("total_pnl_yen_100")
            reentry_audit = None
        research_highlights = None
        if str(am_pm or "").upper() not in ("AM", "PM"):
            # Phase687W60: Daily TODAY'S RESEARCH (fail-open; never blocks summary)
            try:
                from small_paper.discord_current_system_summary import (
                    build_daily_research_highlights,
                )

                research_highlights = build_daily_research_highlights(summary)
            except Exception:
                research_highlights = [
                    "=== TODAY'S RESEARCH ===",
                    "research highlight unavailable",
                ]
        return build_summary_embed_payload(
            canonical,
            am_pm=am_pm,
            day_realized_pnl_yen_100=day_yen,
            reentry_audit=reentry_audit,
            research_highlights=research_highlights,
        )

    def _production_summary_fields(
        self,
        *,
        events: Sequence[Mapping[str, Any]],
        summary: Mapping[str, Any],
        monitored_symbol_count: Optional[int] = None,
        reject_rows: Optional[Sequence[Mapping[str, Any]]] = None,
        ux_stats: Optional[DiscordUxSessionStats] = None,
    ) -> Optional[list[dict[str, Any]]]:
        embed = self._production_summary_embed(
            events=events,
            summary=summary,
            monitored_symbol_count=monitored_symbol_count,
            reject_rows=reject_rows,
            ux_stats=ux_stats,
        )
        if embed is None:
            return None
        return list(embed.get("fields") or [])

    def notify_daily_summary(
        self,
        *,
        events: Sequence[Mapping[str, Any]],
        summary: Mapping[str, Any],
        monitored_symbol_count: Optional[int] = None,
        reject_rows: Optional[Sequence[Mapping[str, Any]]] = None,
        ux_stats: Optional[DiscordUxSessionStats] = None,
    ) -> bool:
        if not self.cfg.send_daily_summary:
            return False
        embed = self._production_summary_embed(
            events=events,
            summary=summary,
            monitored_symbol_count=monitored_symbol_count,
            reject_rows=reject_rows,
            ux_stats=ux_stats,
        )
        if not embed:
            return False
        event_tag, _legacy_title = summary_notification_labels(summary)
        # Phase687W34: day-scoped keys — persistent DedupeStore never expires SENT,
        # so bare "daily_summary" from a prior day blocked all later AM/PM Summaries.
        day = str(
            summary.get("trading_date")
            or summary.get("day_stamp")
            or summary.get("output_date")
            or ""
        ).replace("-", "")[:8]
        if not day:
            try:
                from datetime import datetime as _dt
                from zoneinfo import ZoneInfo as _ZI

                day = _dt.now(_ZI("Asia/Tokyo")).strftime("%Y%m%d")
            except Exception:
                day = "unknown"
        if event_tag == "AM Summary":
            dedupe_key = f"am_summary|{day}"
        elif event_tag == "PM Summary":
            dedupe_key = f"pm_summary|{day}"
        else:
            dedupe_key = f"daily_summary|{day}"
        return self._post(
            event_tag=event_tag,
            title_line=str(embed["title"]),
            fields=list(embed.get("fields") or []),
            description=str(embed.get("description") or ""),
            footer_text=str(embed.get("footer") or ""),
            color=int(embed.get("color") or 0x805AD5),
            dedupe_key=dedupe_key,
            cooldown_sec=300.0,
            trade_notify=True,
        )

    def notify_rejected(
        self,
        *,
        event: Mapping[str, Any],
        payload: Mapping[str, Any],
        open_slots: int,
        session_bucket: str,
    ) -> bool:
        reason = str(event.get("gate_reject_reason") or "")
        if is_entry_blocked_discord_notify_reason(reason) and self.cap_blocked_notify_enabled():
            return False
        if not self.cfg.send_rejects:
            return False
        sym = str(event.get("symbol") or "")
        reason_label = "ゲートにより見送り"
        entry_px = event.get("current_price") or payload.get("CurrentPrice")
        try:
            entry_f = float(entry_px) if entry_px is not None else 0.0
        except (TypeError, ValueError):
            entry_f = 0.0
        fields = [
            {"name": "symbol", "value": sym, "inline": True},
            {"name": "reject_reason", "value": reason, "inline": True},
            {"name": "reject_detail", "value": reason_label, "inline": False},
            {
                "name": "continuation_quality",
                "value": _fmt_num(event.get("continuation_quality_score"), digits=4),
                "inline": True,
            },
            {"name": "profile", "value": str(event.get("profile", self.profile)), "inline": True},
            {"name": "concurrent_positions", "value": str(open_slots), "inline": True},
            {"name": "session_bucket", "value": session_bucket, "inline": True},
        ]
        return self._post(
            event_tag="REJECT",
            title_line=f"REJECT {sym}",
            fields=fields,
            color=0x718096,
            dedupe_key=f"reject|{sym}|{reason}",
        )

    def notify_accepted(self, **kwargs: Any) -> bool:
        return self.notify_entry(**kwargs)

    def notify_heartbeat(self, *, summary: Mapping[str, Any]) -> bool:
        if not self.active:
            return False
        fields = [
            *self._policy_fields(),
            {"name": "runtime_sec", "value": _fmt_num(summary.get("runtime_sec"), digits=0), "inline": True},
            {"name": "entry", "value": str(summary.get("observer_entry_count", summary.get("accepted_count", 0))), "inline": True},
            {"name": "holding", "value": str(summary.get("observer_holding_count", 0)), "inline": True},
            {"name": "exited", "value": str(summary.get("observer_exit_count", 0)), "inline": True},
            {"name": "take_signals", "value": str(summary.get("observer_take_count", 0)), "inline": True},
            {"name": "rejected", "value": str(summary.get("rejected_count", 0)), "inline": True},
            {"name": "open_positions", "value": str(summary.get("observer_holding_count", 0)), "inline": True},
            {"name": "api_errors", "value": str(summary.get("api_error_count", 0)), "inline": True},
            {"name": "stale_ticks", "value": str(summary.get("stale_tick_count", 0)), "inline": True},
            *format_heartbeat_runtime_health_fields(summary),
            {"name": "top_symbols", "value": str(summary.get("top_symbols") or "—"), "inline": False},
            {
                "name": "quality_distribution",
                "value": str(summary.get("quality_distribution", {}))[:900],
                "inline": False,
            },
        ]
        ok = self._post(
            event_tag="HEARTBEAT",
            title_line="HEARTBEAT",
            fields=fields,
            color=0x3182CE,
            dedupe_key=None,
        )
        if ok:
            self._last_heartbeat_mono = time.monotonic()
        return ok

    def notify_session_summary(
        self,
        *,
        events: Sequence[Mapping[str, Any]],
        summary: Mapping[str, Any],
        monitored_symbol_count: Optional[int] = None,
        reject_rows: Optional[Sequence[Mapping[str, Any]]] = None,
        ux_stats: Optional[DiscordUxSessionStats] = None,
    ) -> bool:
        if not self.active:
            return False
        fields = self._production_summary_fields(
            events=events,
            summary=summary,
            monitored_symbol_count=monitored_symbol_count,
            reject_rows=reject_rows,
            ux_stats=ux_stats,
        )
        if not fields:
            return False
        return self._post(
            event_tag="SUMMARY",
            title_line="SESSION END",
            fields=fields,
            color=0x805AD5,
            dedupe_key="session_end",
        )

    def notify_error(self, *, operation: str, message: str, extra: Optional[Mapping[str, Any]] = None) -> bool:
        fields = [
            {"name": "operation", "value": operation, "inline": True},
            {"name": "message", "value": message[:900], "inline": False},
        ]
        if extra:
            fields.append({"name": "detail", "value": str(extra)[:900], "inline": False})
        return self._post(
            event_tag="ERROR",
            title_line=f"ERROR {operation}",
            fields=fields,
            color=0xE53E3E,
            dedupe_key=f"error|{operation}",
        )

    def notify_forward_observers_startup(self, *, lines: Sequence[str]) -> bool:
        """Phase687W59: one-shot [TRADEBOT PAPER START] (observe-only status)."""
        body = "\n".join(str(x) for x in lines if str(x).strip() or x == "")
        if not body.strip():
            return False
        # Embed field limit ~1024; keep overflow in description via multi-field chunks
        chunks: list[str] = []
        cur: list[str] = []
        n = 0
        for ln in body.splitlines():
            if n + len(ln) + 1 > 900 and cur:
                chunks.append("\n".join(cur))
                cur = [ln]
                n = len(ln) + 1
            else:
                cur.append(ln)
                n += len(ln) + 1
        if cur:
            chunks.append("\n".join(cur))
        fields = [
            {"name": f"part{i+1}" if i else "status", "value": c[:1020], "inline": False}
            for i, c in enumerate(chunks[:6])
        ]
        return self._post(
            event_tag="INFO",
            title_line="TRADEBOT PAPER START",
            fields=fields,
            color=0x718096,
            dedupe_key="forward_observers_startup",
        )


def notify_discord_session_end(
    discord: Optional[SmallPaperDiscordNotifier],
    *,
    events: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    monitored_symbol_count: Optional[int] = None,
    reject_rows: Optional[Sequence[Mapping[str, Any]]] = None,
    ux_stats: Optional[DiscordUxSessionStats] = None,
    native_root: Optional[Path] = None,
    output_dir: Optional[Path] = None,
) -> None:
    """Session-end Discord: actual AM/PM/Daily Summary, then RESEARCH_SHADOW AM/PM hook.

    Shadow enqueue is fail-open and never blocks Paper finalize.
    """
    if discord and discord.active:
        if discord.cfg.send_daily_summary:
            discord.notify_daily_summary(
                events=events,
                summary=summary,
                monitored_symbol_count=monitored_symbol_count,
                reject_rows=reject_rows,
                ux_stats=ux_stats,
            )
        else:
            discord.notify_session_summary(
                events=events,
                summary=summary,
                monitored_symbol_count=monitored_symbol_count,
                reject_rows=reject_rows,
                ux_stats=ux_stats,
            )
    # Phase687W10A: real AM/PM finalize path → RESEARCH_SHADOW (ownership RESEARCH)
    try:
        from small_paper.shadow_summary_runtime_hook import enqueue_shadow_summary_for_session

        root = Path(native_root) if native_root else Path(__file__).resolve().parents[2]
        out = Path(output_dir) if output_dir else None
        if out is None:
            raw = summary.get("output_dir") or summary.get("session_dir")
            if raw:
                out = Path(str(raw))
        enqueue_shadow_summary_for_session(
            summary,
            native_root=root,
            output_dir=out,
            session_id=str(summary.get("session_id") or ""),
            trading_date=str(summary.get("trading_date") or "") or None,
        )
    except Exception as exc:
        log.warning("shadow summary runtime hook failed (fail-open): %s", exc)


def build_session_summary_extras(
    *,
    accepted_rows: Sequence[Mapping[str, Any]],
    bucket_summary: Mapping[str, Mapping[str, int]],
    observer_stats: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    qualities = [
        float(r["continuation_quality_score"])
        for r in accepted_rows
        if r.get("continuation_quality_score") is not None
    ]
    top_q = max(qualities) if qualities else None
    sym_counts = Counter(str(r.get("symbol")) for r in accepted_rows if r.get("symbol"))
    accepted_symbols = [s for s, _ in sym_counts.most_common(20)]

    worst_period = "—"
    worst_score = -1
    for bucket, counts in bucket_summary.items():
        if bucket == "outside":
            continue
        score = int(counts.get("rejected", 0)) - int(counts.get("accepted", 0))
        if score > worst_score:
            worst_score = score
            worst_period = bucket

    out: dict[str, Any] = {
        "top_continuation_quality": top_q,
        "worst_period": worst_period,
        "accepted_symbols": accepted_symbols,
        "top_symbols": ", ".join(s for s, _ in sym_counts.most_common(5)),
    }
    if observer_stats:
        durations = observer_stats.get("hold_durations_sec") or []
        out.update(
            {
                "observer_entry_count": observer_stats.get("entry_count", 0),
                "observer_exit_count": observer_stats.get("exit_count", 0),
                "observer_hold_count": observer_stats.get("hold_notify_count", 0),
                "observer_take_count": observer_stats.get("take_count", 0),
                "observer_holding_count": observer_stats.get("holding_count", 0),
                "observer_avg_hold_sec": (
                    sum(durations) / len(durations) if durations else None
                ),
                "structural_exit_policy": observer_stats.get("structural_exit_policy"),
                "structural_exit_count": observer_stats.get("structural_exit_count", 0),
                "structural_exit_reason_counts": observer_stats.get("structural_exit_reason_counts", {}),
                "virtual_hold_expired_ignored_count": observer_stats.get(
                    "virtual_hold_expired_ignored_count", 0
                ),
                "official_exit_count": observer_stats.get("official_exit_count", 0),
                "session_end_exit_count": observer_stats.get("session_end_exit_count", 0),
                "no_progress_exit_count": observer_stats.get("no_progress_exit_count", 0),
            }
        )
    return out


def discord_notifier_from_pilot(
    config: Any,
    *,
    error_logger: Optional[ErrorLogger] = None,
    delivery_audit: Optional[DeliveryAuditCallback] = None,
) -> SmallPaperDiscordNotifier:
    return SmallPaperDiscordNotifier(
        discord_config_from_pilot(config),
        profile=str(config.profile),
        entry_profile=str(config.entry_profile),
        policy_label=str(getattr(config, "policy_label", "paper_cap5") or "paper_cap5"),
        min_continuation_quality=float(getattr(config, "min_continuation_quality", 0.55)),
        error_logger=error_logger,
        delivery_audit=delivery_audit,
    )


def discord_notify_summary_fields(
    notifier: Optional[SmallPaperDiscordNotifier],
) -> dict[str, Any]:
    if notifier is None:
        return {}
    return {
        "discord_error_count": int(notifier.discord_error_count),
        "cap_blocked_notify_attempt_count": int(notifier.cap_blocked_notify_attempt_count),
        "cap_blocked_notify_sent_count": int(notifier.cap_blocked_notify_sent_count),
        "cap_blocked_webhook_configured": bool(notifier.cap_blocked_channel_ready()),
    }


def discord_config_from_pilot(config: Any) -> SmallPaperDiscordConfig:
    return SmallPaperDiscordConfig(
        enabled=bool(config.discord_enabled),
        observer_only=bool(config.discord_observer_only),
        send_rejects=bool(config.discord_send_rejects),
        send_entry_deferred_max_concurrent=bool(
            getattr(config, "discord_send_entry_deferred_max_concurrent", True)
        ),
        send_entry_cap_blocked=bool(
            getattr(
                config,
                "discord_send_entry_cap_blocked",
                getattr(config, "discord_send_entry_deferred_max_concurrent", True),
            )
        ),
        entry_deferred_cooldown_sec=float(
            getattr(config, "discord_entry_deferred_cooldown_sec", 1800.0)
        ),
        entry_deferred_min_score_v2=int(
            getattr(config, "discord_entry_deferred_min_score_v2", 5)
        ),
        entry_deferred_daily_max=int(getattr(config, "discord_entry_deferred_daily_max", 50)),
        send_universe_refresh=bool(getattr(config, "discord_send_universe_refresh", True)),
        send_daily_summary=bool(getattr(config, "discord_send_daily_summary", True)),
        max_concurrent_positions=int(getattr(config, "max_concurrent_positions", 5)),
        position_cap_mode=bool(getattr(config, "position_cap_mode", False)),
        heartbeat_min=float(config.discord_heartbeat_min),
        webhook_env=str(config.discord_webhook_env),
        trade_notify_webhook_env=str(
            getattr(config, "discord_trade_notify_webhook_env", _TRADE_NOTIFY_WEBHOOK_ENV)
        ),
        trade_cap_blocked_webhook_env=str(
            getattr(
                config,
                "discord_trade_cap_blocked_webhook_env",
                _CAP_BLOCKED_WEBHOOK_ENV,
            )
        ),
        cooldown_sec=float(config.discord_cooldown_sec),
        hard_stop_pct=float(config.discord_hard_stop_pct),
        hold_min=float(config.discord_hold_min),
        hold_quality_delta=float(config.discord_hold_quality_delta),
        take_quality_drop=float(config.discord_take_quality_drop),
    )


def observer_tracker_config_from_pilot(config: Any) -> Any:
    from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1
    from small_paper.observer_position_tracker import ObserverTrackerConfig

    policy = str(
        getattr(config, "structural_exit_policy", None)
        or POLICY_COMBINED_STRUCTURAL_EXIT_V1
    )
    return ObserverTrackerConfig(
        hold_min=float(config.discord_hold_min),
        hold_quality_delta=float(config.discord_hold_quality_delta),
        take_quality_drop=float(config.discord_take_quality_drop),
        hard_stop_pct=float(config.discord_hard_stop_pct),
        structural_exit_policy=policy,
        price_momentum_fade_ratio=float(getattr(config, "price_momentum_fade_ratio", 0.85) or 0.85),
        live_session_end=str(getattr(config, "live_session_end", "15:30")),
        no_progress_exit_enabled=bool(getattr(config, "no_progress_exit_enabled", False)),
        exit_shadow_monitor_enabled=bool(getattr(config, "exit_shadow_monitor_enabled", False)),
        exit_shadow_monitor_t2_enabled=bool(getattr(config, "exit_shadow_monitor_t2_enabled", True)),
        exit_shadow_monitor_t3_enabled=bool(getattr(config, "exit_shadow_monitor_t3_enabled", True)),
        flat_weak_range_shadow_enabled=bool(
            getattr(config, "flat_weak_range_shadow_enabled", False)
        ),
    )
