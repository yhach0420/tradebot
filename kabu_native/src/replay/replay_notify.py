"""
Discord notifications for kabu_native batch replay ([KABU_PAPER][REPLAY]).

Separate from realtime shadow notify (market.yahoo / shadow runner).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence

import requests

log = logging.getLogger("kabu_native.replay.notify")

_DEFAULT_WEBHOOK_ENV = "KABU_SHADOW_DISCORD_WEBHOOK_URL"
_REPLAY_SOURCE = "kabu_native.run_replay"


@dataclass(frozen=True)
class ReplayNotifyConfig:
    enabled: bool = False
    webhook_env: str = _DEFAULT_WEBHOOK_ENV
    max_messages: int = 50
    send_delay_sec: float = 0.2
    replay_source: str = _REPLAY_SOURCE


@dataclass
class ReplayNotifyStats:
    messages_sent: int = 0
    trades_notified: int = 0
    truncated: bool = False
    errors: int = 0


def _fmt_num(v: Any, *, digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def _trade_symbol(trade: Any) -> str:
    return str(getattr(trade, "symbol", "") or "")


def _trade_date(trade: Any) -> str:
    if hasattr(trade, "trade_date"):
        return str(trade.trade_date)
    return ""


def _replay_time_str(ts: Any) -> str:
    if ts is None:
        return "—"
    if isinstance(ts, datetime):
        return ts.isoformat(timespec="seconds")
    return str(ts)


class ReplayDiscordNotifier:
    def __init__(self, cfg: ReplayNotifyConfig) -> None:
        self.cfg = cfg
        self._webhook_url = ""
        self.stats = ReplayNotifyStats()

    @property
    def active(self) -> bool:
        return bool(self.cfg.enabled and self._resolve_webhook())

    def _resolve_webhook(self) -> str:
        if self._webhook_url:
            return self._webhook_url
        env_name = (self.cfg.webhook_env or _DEFAULT_WEBHOOK_ENV).strip()
        url = (os.getenv(env_name) or "").strip()
        if url:
            self._webhook_url = url
        return url

    def _under_limit(self) -> bool:
        return self.stats.messages_sent < int(self.cfg.max_messages)

    def _sleep_between(self) -> None:
        delay = max(0.0, float(self.cfg.send_delay_sec))
        if delay > 0:
            time.sleep(delay)

    def _post(self, *, title: str, fields: list[dict[str, Any]], color: int) -> bool:
        if not self._under_limit():
            self.stats.truncated = True
            return False
        webhook = self._resolve_webhook()
        if not webhook:
            log.warning("[KABU_REPLAY_NOTIFY] error webhook env %s empty", self.cfg.webhook_env)
            self.stats.errors += 1
            return False

        payload = {
            "content": title,
            "embeds": [
                {
                    "title": title,
                    "color": color,
                    "fields": fields,
                    "footer": {"text": f"{self.cfg.replay_source} · replay only · no orders"},
                }
            ],
        }
        try:
            resp = requests.post(webhook, json=payload, timeout=15)
            if resp.status_code >= 400:
                log.warning(
                    "[KABU_REPLAY_NOTIFY] error HTTP %s %s",
                    resp.status_code,
                    (resp.text or "")[:200],
                )
                self.stats.errors += 1
                return False
        except Exception as e:
            log.warning("[KABU_REPLAY_NOTIFY] error %s", e, exc_info=False)
            self.stats.errors += 1
            return False

        self.stats.messages_sent += 1
        return True

    def _base_fields(
        self,
        trade: Any,
        *,
        replay_day: str,
        replay_time: str,
        entry_price: Any,
        exit_price: Any,
        pnl_pct: Any,
        exit_reason: Any,
        mfe_pct: Any,
    ) -> list[dict[str, Any]]:
        return [
            {"name": "symbol", "value": _trade_symbol(trade), "inline": True},
            {"name": "replay_day", "value": replay_day or "—", "inline": True},
            {"name": "replay_time", "value": replay_time, "inline": True},
            {"name": "entry_price", "value": _fmt_num(entry_price), "inline": True},
            {"name": "exit_price", "value": _fmt_num(exit_price), "inline": True},
            {"name": "pnl_pct", "value": _fmt_num(pnl_pct), "inline": True},
            {"name": "exit_reason", "value": str(exit_reason or "—"), "inline": True},
            {"name": "mfe_pct", "value": _fmt_num(mfe_pct), "inline": True},
            {"name": "replay_source", "value": self.cfg.replay_source, "inline": True},
            {"name": "note", "value": "replay simulation only / 発注なし", "inline": False},
        ]

    def notify_entry(self, trade: Any) -> bool:
        sym = _trade_symbol(trade)
        day = _trade_date(trade)
        title = f"[KABU_PAPER][REPLAY] ENTRY EXECUTED {sym}"
        fields = self._base_fields(
            trade,
            replay_day=day,
            replay_time=_replay_time_str(getattr(trade, "entry_time", None)),
            entry_price=getattr(trade, "entry_price", None),
            exit_price="—",
            pnl_pct="—",
            exit_reason="—",
            mfe_pct="—",
        )
        ok = self._post(title=title, fields=fields, color=0x2F855A)
        if ok:
            self._sleep_between()
        return ok

    def notify_exit(self, trade: Any) -> bool:
        sym = _trade_symbol(trade)
        day = _trade_date(trade)
        title = f"[KABU_PAPER][REPLAY] EXIT EXECUTED {sym}"
        mfe = getattr(trade, "max_favorable_excursion_pct", None)
        fields = self._base_fields(
            trade,
            replay_day=day,
            replay_time=_replay_time_str(getattr(trade, "exit_time", None)),
            entry_price=getattr(trade, "entry_price", None),
            exit_price=getattr(trade, "exit_price", None),
            pnl_pct=getattr(trade, "pnl_pct", None),
            exit_reason=getattr(trade, "exit_reason", None),
            mfe_pct=mfe,
        )
        ok = self._post(title=title, fields=fields, color=0xC05621)
        if ok:
            self._sleep_between()
        return ok

    def notify_trade(self, trade: Any) -> bool:
        """Send ENTRY then EXIT for one closed trade. Returns True if any message sent."""
        if not self.active:
            return False
        if not self._under_limit():
            self.stats.truncated = True
            return False

        entry_ok = self.notify_entry(trade)
        if not self._under_limit():
            self.stats.truncated = True
            if entry_ok:
                self.stats.trades_notified += 1
            return entry_ok

        exit_ok = self.notify_exit(trade)
        if entry_ok or exit_ok:
            self.stats.trades_notified += 1
        return entry_ok or exit_ok


def notify_replay_trades(
    trades: Sequence[Any],
    *,
    cfg: ReplayNotifyConfig,
) -> ReplayNotifyStats:
    notifier = ReplayDiscordNotifier(cfg)
    if not notifier.active:
        log.info("[KABU_REPLAY_NOTIFY] disabled or webhook missing; skip %d trades", len(trades))
        return notifier.stats

    log.info(
        "[KABU_REPLAY_NOTIFY] sending up to %d messages for %d trades (delay=%.2fs)",
        cfg.max_messages,
        len(trades),
        cfg.send_delay_sec,
    )
    for trade in trades:
        if notifier.stats.messages_sent >= int(cfg.max_messages):
            notifier.stats.truncated = True
            break
        notifier.notify_trade(trade)

    if notifier.stats.truncated:
        log.warning(
            "[KABU_REPLAY_NOTIFY] truncated at max_messages=%s (sent=%s)",
            cfg.max_messages,
            notifier.stats.messages_sent,
        )
    else:
        log.info(
            "[KABU_REPLAY_NOTIFY] done sent=%s trades=%s errors=%s",
            notifier.stats.messages_sent,
            notifier.stats.trades_notified,
            notifier.stats.errors,
        )
    return notifier.stats
