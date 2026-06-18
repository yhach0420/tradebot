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
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import requests

from research.exposure_gate import REJECT_MAX_CONCURRENT
from small_paper.discord_message_builder import (
    build_daily_summary_detail,
    build_entry_cap_blocked_detail,
    build_entry_deferred_detail,
    build_entry_detail,
    build_exit_detail,
    summary_notification_labels,
    build_universe_refresh_overview,
    build_universe_screening_overview,
    split_watch_symbols_discord_fields,
    format_slot_usage,
    format_time_hms_jst,
)
from small_paper.discord_symbol_names import format_symbol_display, get_cached_symbol_name_map
from small_paper.discord_ux_session import DiscordUxSessionStats

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
        self._legacy_webhook_url = ""
        self._trade_webhook_url = ""
        self._trade_webhook_source = ""
        self._cap_blocked_webhook_url = ""
        self._last_sent_mono: dict[str, float] = {}
        self._last_heartbeat_mono: float = 0.0

    @property
    def active(self) -> bool:
        return bool(
            self.cfg.enabled
            and self.cfg.observer_only
            and (self._resolve_trade_webhook()[0] or self._resolve_legacy_webhook())
        )

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
        if self._legacy_webhook_url:
            return self._legacy_webhook_url
        env_name = (self.cfg.webhook_env or _LEGACY_WEBHOOK_ENV).strip()
        url = (os.getenv(env_name) or "").strip()
        if url:
            self._legacy_webhook_url = url
        return url

    def _resolve_trade_webhook(self) -> tuple[str, str]:
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
        cooldown_sec: Optional[float] = None,
        trade_notify: bool = False,
        cap_blocked: bool = False,
    ) -> bool:
        if not self.cfg.enabled or not self.cfg.observer_only:
            return False
        if dedupe_key and not self._cooldown_ok(dedupe_key, cooldown_sec=cooldown_sec):
            return False
        if cap_blocked:
            webhook = self._resolve_cap_blocked_webhook()
            env_hint = self.cfg.trade_cap_blocked_webhook_env
            source = "cap_blocked"
        elif trade_notify:
            if not self.active:
                return False
            webhook, source = self._resolve_trade_webhook()
            env_hint = self.cfg.trade_notify_webhook_env
            if source == "legacy_fallback":
                env_hint = f"{self.cfg.trade_notify_webhook_env} (fallback {self.cfg.webhook_env})"
        else:
            if not self.active:
                return False
            webhook = self._resolve_legacy_webhook()
            env_hint = self.cfg.webhook_env
            source = "legacy"
        if not webhook:
            self._log_failure("webhook", f"env {env_hint} empty", {"channel": source})
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
        score5_candidate_ordinal: Optional[int] = None,
        ux_stats: Optional[DiscordUxSessionStats] = None,
        entry_signal_mono: Optional[float] = None,
        notify_mono: Optional[float] = None,
    ) -> bool:
        sym = str(event.get("symbol") or "")
        entry_px = event.get("current_price") or payload.get("CurrentPrice")
        try:
            entry_f = float(entry_px) if entry_px is not None else 0.0
        except (TypeError, ValueError):
            entry_f = 0.0
        stop_px, _ = _stop_take(entry_f, self.cfg.hard_stop_pct) if entry_f > 0 else (0.0, 0.0)
        merged: dict[str, Any] = {**dict(payload), **dict(event)}
        if entry_signal_mono is not None and notify_mono is not None:
            merged["signal_to_notify_latency_ms"] = round(
                (float(notify_mono) - float(entry_signal_mono)) * 1000.0,
                1,
            )
        if notify_mono is not None:
            merged["discord_sent_ts"] = datetime.now(JST).isoformat(timespec="milliseconds")
        v2_raw = merged.get("entry_expectancy_score_v2")
        try:
            v2 = int(v2_raw) if v2_raw is not None and v2_raw != "" else None
        except (TypeError, ValueError):
            v2 = None
        slot = format_slot_usage(open_slots, self._max_slots())
        name_map = get_cached_symbol_name_map()
        display = format_symbol_display(sym, name_map=name_map)
        event_time = str(event.get("event_time") or "")
        detail = build_entry_detail(
            symbol=sym,
            entry_price=entry_f,
            stop_price=stop_px,
            slot_usage=slot,
            entry_score_v2=v2,
            data=merged,
            score5_candidate_ordinal=score5_candidate_ordinal,
            name_map=name_map,
            entry_time=event_time,
        )
        ok = self._post(
            event_tag="ENTRY",
            title_line=f"【ENTRY】 {display}",
            fields=[
                {"name": "詳細", "value": detail[:1020], "inline": False},
                {"name": "session", "value": session_bucket, "inline": True},
                {"name": "時刻", "value": format_time_hms_jst(event_time), "inline": True},
            ],
            color=0x2F855A,
            dedupe_key=f"entry|{sym}|{event.get('message_index')}",
            trade_notify=True,
        )
        if ok and ux_stats is not None and v2 is not None and v2 >= self.cfg.entry_deferred_min_score_v2:
            ux_stats.record_score5_entry()
        return ok

    def notify_entry_cap_blocked(
        self,
        *,
        event: Mapping[str, Any],
        payload: Mapping[str, Any],
        trade_data: Mapping[str, Any],
        open_slots: int,
        score5_candidate_ordinal: Optional[int] = None,
        ux_stats: Optional[DiscordUxSessionStats] = None,
    ) -> bool:
        if not (self.cfg.send_entry_cap_blocked or self.cfg.send_entry_deferred_max_concurrent):
            return False
        sym = str(event.get("symbol") or "")
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
        display = format_symbol_display(sym, name_map=name_map)
        detail = build_entry_cap_blocked_detail(
            symbol=sym,
            entry_score_v2=v2,
            data=merged,
            active_positions=int(open_slots),
            position_cap=cap,
            name_map=name_map,
        )
        event_time = str(event.get("event_time") or "")
        ok = self._post(
            event_tag="CAP BLOCKED",
            title_line=display,
            fields=[
                {"name": "詳細", "value": detail[:1020], "inline": False},
                {"name": "時刻", "value": format_time_hms_jst(event_time), "inline": True},
            ],
            color=0xDD6B20,
            dedupe_key=f"cap_blocked|{sym}",
            cooldown_sec=float(self.cfg.entry_deferred_cooldown_sec),
            cap_blocked=True,
        )
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
        display = format_symbol_display(sym, name_map=name_map)
        exit_time = str(
            context.get("exit_time") or context.get("event_time") or context.get("timestamp") or ""
        )
        detail = build_exit_detail(
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
            exit_time=exit_time,
            name_map=name_map,
        )
        if self.cfg.position_cap_mode:
            detail += "\nExit source: structural_observer"
            if context.get("session_close"):
                detail += "\nSession close: position-cap slot released"
        fields = [
            {
                "name": "観測のみ",
                "value": "発注なし — 構造EXITの通知",
                "inline": False,
            },
            {"name": "詳細", "value": detail[:1020], "inline": False},
        ]
        return self._post(
            event_tag="EXIT",
            title_line=f"【EXIT】 {display}",
            fields=fields,
            color=0xC05621,
            dedupe_key=f"exit|{sym}|{reason}|{context.get('exit_time', '')}",
            trade_notify=True,
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
    ) -> bool:
        """Post initial watch list after AM/PM universe screening (not 10:00/14:30 refresh)."""
        if not self.cfg.send_universe_refresh:
            return False
        name_map = get_cached_symbol_name_map()
        overview = build_universe_screening_overview(
            session_label=session_label,
            watch_symbol_count=len(watch_symbols),
            name_map=name_map,
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
        detail = build_daily_summary_detail(canonical, name_map=get_cached_symbol_name_map())
        fields: list[dict[str, Any]] = []
        from small_paper.discord_message_builder import format_research_shadow_daily_summary_lines

        research_shadow = format_research_shadow_daily_summary_lines(summary)
        if research_shadow:
            fields.append(
                {
                    "name": "Research Shadow",
                    "value": "\n".join(research_shadow)[:1020],
                    "inline": False,
                }
            )
        chunk = detail
        idx = 1
        while chunk:
            fields.append(
                {
                    "name": "詳細" if idx == 1 and len(detail) <= 1020 else f"詳細({idx})",
                    "value": chunk[:1020],
                    "inline": False,
                }
            )
            chunk = chunk[1020:]
            idx += 1
        return fields

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
        fields = self._production_summary_fields(
            events=events,
            summary=summary,
            monitored_symbol_count=monitored_symbol_count,
            reject_rows=reject_rows,
            ux_stats=ux_stats,
        )
        if not fields:
            return False
        event_tag, title_line = summary_notification_labels(summary)
        dedupe_key = "daily_summary"
        if event_tag == "AM Summary":
            dedupe_key = "am_summary"
        elif event_tag == "PM Summary":
            dedupe_key = "pm_summary"
        return self._post(
            event_tag=event_tag,
            title_line=title_line,
            fields=fields,
            color=0x805AD5,
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
        if reason == REJECT_MAX_CONCURRENT and (
            self.cfg.send_entry_cap_blocked or self.cfg.send_entry_deferred_max_concurrent
        ):
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


def notify_discord_session_end(
    discord: Optional[SmallPaperDiscordNotifier],
    *,
    events: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    monitored_symbol_count: Optional[int] = None,
    reject_rows: Optional[Sequence[Mapping[str, Any]]] = None,
    ux_stats: Optional[DiscordUxSessionStats] = None,
) -> None:
    """Session-end Discord: Daily Summary (Phase276+) or legacy SUMMARY."""
    if not discord or not discord.active:
        return
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
        max_concurrent_positions=int(getattr(config, "max_concurrent_positions", 3)),
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
    )
