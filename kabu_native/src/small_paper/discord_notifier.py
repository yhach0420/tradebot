"""
Phase 46–47: Small paper pilot Discord observer (judgment events — no orders).

Webhook: KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL only — not KABU_SHADOW / Yahoo paper_trade.
"""

from __future__ import annotations

import logging
import os
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import requests

from research.exposure_gate import (
    REJECT_DAILY_LOSS,
    REJECT_LOW_QUALITY,
    REJECT_MAX_CONCURRENT,
    REJECT_OUTSIDE_ALLOWED_TRADING_WINDOW,
    REJECT_RISK_CLUSTER,
)

log = logging.getLogger("kabu_native.small_paper.discord")

JST = ZoneInfo("Asia/Tokyo")

_DISPLAY_TAKE_PCT = 4.0
_DEFAULT_HARD_STOP_PCT = 1.20

REJECT_LABELS = {
    REJECT_LOW_QUALITY: "low_quality — below min continuation_quality",
    REJECT_MAX_CONCURRENT: "max_concurrent — slot cap reached",
    REJECT_RISK_CLUSTER: "risk_cluster_block — consecutive loss guard",
    REJECT_DAILY_LOSS: "daily_loss_guard — day PnL floor",
    REJECT_OUTSIDE_ALLOWED_TRADING_WINDOW: "outside_allowed_trading_window — not in allowed hours",
}

ErrorLogger = Callable[[str, str, Mapping[str, Any]], None]


@dataclass(frozen=True)
class SmallPaperDiscordConfig:
    enabled: bool = False
    observer_only: bool = True
    send_rejects: bool = False
    heartbeat_min: float = 30.0
    webhook_env: str = "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL"
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
        policy_label: str = "q055_cap3",
        min_continuation_quality: float = 0.55,
        error_logger: Optional[ErrorLogger] = None,
    ) -> None:
        self.cfg = cfg
        self.profile = profile
        self.entry_profile = entry_profile
        self.policy_label = policy_label
        self.min_continuation_quality = min_continuation_quality
        self._error_logger = error_logger
        self._webhook_url = ""
        self._last_sent_mono: dict[str, float] = {}
        self._last_heartbeat_mono: float = 0.0

    @property
    def active(self) -> bool:
        return bool(self.cfg.enabled and self.cfg.observer_only and self._resolve_webhook())

    def heartbeat_interval_sec(self) -> float:
        return max(60.0, float(self.cfg.heartbeat_min) * 60.0)

    def should_send_heartbeat(self) -> bool:
        if not self.active:
            return False
        if self._last_heartbeat_mono <= 0:
            return True
        return (time.monotonic() - self._last_heartbeat_mono) >= self.heartbeat_interval_sec()

    def _resolve_webhook(self) -> str:
        if self._webhook_url:
            return self._webhook_url
        env_name = (self.cfg.webhook_env or "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL").strip()
        url = (os.getenv(env_name) or "").strip()
        if url:
            self._webhook_url = url
        return url

    def _cooldown_ok(self, key: str) -> bool:
        last = self._last_sent_mono.get(key)
        if last is None:
            return True
        return (time.monotonic() - last) >= float(self.cfg.cooldown_sec)

    def _mark_sent(self, key: str) -> None:
        self._last_sent_mono[key] = time.monotonic()

    def _header_content(self, event_tag: str, title_line: str = "") -> str:
        lines = ["[SMALL PAPER DRY RUN]", f"[{event_tag}]", "[NO ORDER]"]
        lines.append(f"[policy: {self.policy_label}]")
        if title_line:
            lines.append(title_line)
        return "\n".join(lines)

    def _policy_fields(self) -> list[dict[str, Any]]:
        return [
            {"name": "policy_label", "value": self.policy_label, "inline": True},
            {
                "name": "min_quality",
                "value": _fmt_num(self.min_continuation_quality, digits=2),
                "inline": True,
            },
        ]

    def _log_failure(self, op: str, detail: str, extra: Optional[Mapping[str, Any]] = None) -> None:
        log.warning("[SMALL_PAPER_DISCORD] %s: %s", op, detail)
        if self._error_logger:
            self._error_logger(
                "discord_notify",
                detail,
                {"operation": op, **dict(extra or {})},
            )

    def _post(
        self,
        *,
        event_tag: str,
        title_line: str,
        fields: list[dict[str, Any]],
        color: int,
        dedupe_key: Optional[str] = None,
    ) -> bool:
        if not self.active:
            return False
        if dedupe_key and not self._cooldown_ok(dedupe_key):
            return False
        webhook = self._resolve_webhook()
        if not webhook:
            self._log_failure("webhook", f"env {self.cfg.webhook_env} empty")
            return False

        payload = {
            "content": self._header_content(event_tag, title_line),
            "embeds": [
                {
                    "title": title_line,
                    "color": color,
                    "fields": fields,
                    "footer": {
                        "text": "observer only · dry-run · no real orders",
                    },
                }
            ],
        }
        try:
            resp = requests.post(webhook, json=payload, timeout=15)
            if resp.status_code >= 400:
                self._log_failure(
                    "http",
                    f"HTTP {resp.status_code}",
                    {"body": (resp.text or "")[:200]},
                )
                return False
        except Exception as e:
            self._log_failure("post", str(e))
            return False
        if dedupe_key:
            self._mark_sent(dedupe_key)
        return True

    def notify_entry(
        self,
        *,
        event: Mapping[str, Any],
        payload: Mapping[str, Any],
        open_slots: int,
        session_bucket: str,
    ) -> bool:
        sym = str(event.get("symbol") or "")
        entry_px = event.get("current_price") or payload.get("CurrentPrice")
        try:
            entry_f = float(entry_px) if entry_px is not None else 0.0
        except (TypeError, ValueError):
            entry_f = 0.0
        stop_px, take_px = _stop_take(entry_f, self.cfg.hard_stop_pct) if entry_f > 0 else (0.0, 0.0)
        momentum = payload.get("momentum_continuation_score") or payload.get("momentum_continuation")
        fields = [
            *self._policy_fields(),
            {"name": "symbol", "value": sym, "inline": True},
            {
                "name": "continuation_quality",
                "value": _fmt_num(event.get("continuation_quality_score"), digits=4),
                "inline": True,
            },
            {"name": "profile", "value": str(event.get("profile", self.profile)), "inline": True},
            {"name": "continuation_tier", "value": str(event.get("quality_tier") or "—"), "inline": True},
            {"name": "entry_price", "value": _fmt_num(entry_f), "inline": True},
            {"name": "stop_price", "value": _fmt_num(stop_px), "inline": True},
            {"name": "take_price", "value": _fmt_num(take_px), "inline": True},
            {"name": "momentum", "value": _fmt_num(momentum, digits=4), "inline": True},
            {
                "name": "vwap_distance_pct",
                "value": _fmt_num(_vwap_distance_pct(payload, entry_f)),
                "inline": True,
            },
            {"name": "concurrent_positions", "value": str(open_slots), "inline": True},
            {"name": "session_bucket", "value": session_bucket, "inline": True},
            {"name": "timestamp", "value": str(event.get("event_time", "")), "inline": False},
        ]
        return self._post(
            event_tag="ENTRY",
            title_line=f"ENTRY {sym}",
            fields=fields,
            color=0x2F855A,
            dedupe_key=f"entry|{sym}|{event.get('message_index')}",
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
        sym = str(context.get("symbol", ""))
        comps = context.get("components") or {}
        fields = [
            *self._policy_fields(),
            {"name": "symbol", "value": sym, "inline": True},
            {
                "name": "realized_pnl_pct",
                "value": _fmt_num(context.get("realized_pnl_pct") or context.get("unrealized_pnl_pct")),
                "inline": True,
            },
            {"name": "exit_reason", "value": str(context.get("exit_reason", "—")), "inline": True},
            {
                "name": "hold_duration_sec",
                "value": _fmt_num(context.get("hold_duration_sec"), digits=0),
                "inline": True,
            },
            {
                "name": "continuation_breakdown",
                "value": str(context.get("continuation_breakdown", False)),
                "inline": True,
            },
            {
                "name": "bearish_accumulation",
                "value": _fmt_num(context.get("bearish_accumulation") or comps.get("bearish_accumulation")),
                "inline": True,
            },
            {"name": "max_favorable", "value": _fmt_num(context.get("max_favorable")), "inline": True},
            {"name": "max_adverse", "value": _fmt_num(context.get("max_adverse")), "inline": True},
        ]
        return self._post(
            event_tag="EXIT",
            title_line=f"EXIT {sym}",
            fields=fields,
            color=0xC05621,
            dedupe_key=f"exit|{sym}|{context.get('exit_reason')}",
        )

    def notify_rejected(
        self,
        *,
        event: Mapping[str, Any],
        payload: Mapping[str, Any],
        open_slots: int,
        session_bucket: str,
    ) -> bool:
        if not self.cfg.send_rejects:
            return False
        sym = str(event.get("symbol") or "")
        reason = str(event.get("gate_reject_reason") or "")
        reason_label = REJECT_LABELS.get(reason, reason or "unknown")
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

    def notify_session_summary(self, *, summary: Mapping[str, Any]) -> bool:
        if not self.active:
            return False
        avg_hold = summary.get("observer_avg_hold_sec")
        fields = [
            *self._policy_fields(),
            {"name": "ENTRY_count", "value": str(summary.get("observer_entry_count", 0)), "inline": True},
            {"name": "EXIT_count", "value": str(summary.get("observer_exit_count", 0)), "inline": True},
            {"name": "HOLD_notifications", "value": str(summary.get("observer_hold_count", 0)), "inline": True},
            {"name": "TAKE_signals", "value": str(summary.get("observer_take_count", 0)), "inline": True},
            {"name": "avg_hold_sec", "value": _fmt_num(avg_hold, digits=0), "inline": True},
            {"name": "max_concurrent", "value": str(summary.get("peak_open_slots", "—")), "inline": True},
            {
                "name": "continuation_quality_distribution",
                "value": str(summary.get("quality_distribution", {}))[:900],
                "inline": False,
            },
            {
                "name": "reject_reason_counts",
                "value": str(summary.get("reject_reason_counts", {}))[:900],
                "inline": False,
            },
            {
                "name": "top_continuation_quality",
                "value": _fmt_num(summary.get("top_continuation_quality"), digits=4),
                "inline": True,
            },
            {"name": "worst_period", "value": str(summary.get("worst_period", "—")), "inline": True},
            {"name": "top_symbols", "value": str(summary.get("top_symbols") or "—"), "inline": False},
            {"name": "api_error_count", "value": str(summary.get("api_error_count", 0)), "inline": True},
        ]
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
            }
        )
    return out


def discord_notifier_from_pilot(
    config: Any,
    *,
    error_logger: Optional[ErrorLogger] = None,
) -> SmallPaperDiscordNotifier:
    return SmallPaperDiscordNotifier(
        discord_config_from_pilot(config),
        profile=str(config.profile),
        entry_profile=str(config.entry_profile),
        policy_label=str(getattr(config, "policy_label", "q055_cap3")),
        min_continuation_quality=float(getattr(config, "min_continuation_quality", 0.55)),
        error_logger=error_logger,
    )


def discord_config_from_pilot(config: Any) -> SmallPaperDiscordConfig:
    return SmallPaperDiscordConfig(
        enabled=bool(config.discord_enabled),
        observer_only=bool(config.discord_observer_only),
        send_rejects=bool(config.discord_send_rejects),
        heartbeat_min=float(config.discord_heartbeat_min),
        webhook_env=str(config.discord_webhook_env),
        cooldown_sec=float(config.discord_cooldown_sec),
        hard_stop_pct=float(config.discord_hard_stop_pct),
        hold_min=float(config.discord_hold_min),
        hold_quality_delta=float(config.discord_hold_quality_delta),
        take_quality_drop=float(config.discord_take_quality_drop),
    )


def observer_tracker_config_from_pilot(config: Any) -> Any:
    from small_paper.observer_position_tracker import ObserverTrackerConfig

    return ObserverTrackerConfig(
        hold_min=float(config.discord_hold_min),
        hold_quality_delta=float(config.discord_hold_quality_delta),
        take_quality_drop=float(config.discord_take_quality_drop),
        hard_stop_pct=float(config.discord_hard_stop_pct),
    )
