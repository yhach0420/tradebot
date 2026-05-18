"""
kabu_native virtual paper-trade Discord notifications ([KABU_PAPER]).

Webhook: KABU_SHADOW_DISCORD_WEBHOOK_URL only — never DISCORD_WEBHOOK_URL (Yahoo).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import requests

log = logging.getLogger("kabu_native.notify.discord")

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]

_JST = ZoneInfo("Asia/Tokyo") if ZoneInfo else None

# 表示用テイク（kabu_exit_v1 に固定 TP が無いため参考値）
_PAPER_DISPLAY_TAKE_PCT = 4.0


@dataclass(frozen=True)
class ShadowDiscordConfig:
    enabled: bool = False
    shadow_notify: bool = False
    paper_trade_notify: bool = False
    webhook_env: str = "KABU_SHADOW_DISCORD_WEBHOOK_URL"
    cooldown_sec: float = 300.0
    dedupe: bool = True
    hard_stop_pct: float = 1.20


def _fmt_num(v: Any, *, digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def _paper_stop_take(entry_price: float, hard_stop_pct: float) -> tuple[float, float]:
    stop = entry_price * (1.0 - float(hard_stop_pct) / 100.0)
    take = entry_price * (1.0 + _PAPER_DISPLAY_TAKE_PCT / 100.0)
    return stop, take


class ShadowDiscordNotifier:
    """Virtual ENTRY/EXIT Discord (one notify per leg per position)."""

    def __init__(self, cfg: ShadowDiscordConfig) -> None:
        self.cfg = cfg
        self._webhook_url = ""
        self._dedupe_keys: set[str] = set()
        self._last_sent_mono: dict[str, float] = {}

    @property
    def active(self) -> bool:
        return bool(
            self.cfg.enabled
            and self.cfg.shadow_notify
            and self.cfg.paper_trade_notify
            and self._resolve_webhook()
        )

    def _resolve_webhook(self) -> str:
        if self._webhook_url:
            return self._webhook_url
        env_name = (self.cfg.webhook_env or "KABU_SHADOW_DISCORD_WEBHOOK_URL").strip()
        url = (os.getenv(env_name) or "").strip()
        if url:
            self._webhook_url = url
        return url

    def _position_key(self, symbol: str, entry_time: datetime) -> str:
        et = entry_time.astimezone(timezone.utc).isoformat()
        return f"{symbol}|{et}"

    def _dedupe_key(self, leg: str, symbol: str, entry_time: datetime) -> str:
        return f"paper_{leg}|{self._position_key(symbol, entry_time)}"

    def _cooldown_ok(self, dedupe_key: str) -> bool:
        last = self._last_sent_mono.get(dedupe_key)
        if last is None:
            return True
        return (time.monotonic() - last) >= float(self.cfg.cooldown_sec)

    def _dedupe_ok(self, dedupe_key: str) -> bool:
        if not self.cfg.dedupe:
            return True
        return dedupe_key not in self._dedupe_keys

    def _post_embed(self, *, title: str, fields: list[dict[str, Any]], color: int) -> bool:
        webhook = self._resolve_webhook()
        if not webhook:
            log.warning("[KABU_NOTIFY] error webhook env %s is empty", self.cfg.webhook_env)
            return False

        payload = {
            "content": title,
            "embeds": [
                {
                    "title": title,
                    "color": color,
                    "fields": fields,
                    "footer": {"text": "kabu_native paper (virtual) · no real orders"},
                }
            ],
        }
        try:
            resp = requests.post(webhook, json=payload, timeout=15)
            if resp.status_code >= 400:
                log.warning(
                    "[KABU_NOTIFY] error HTTP %s body=%s",
                    resp.status_code,
                    (resp.text or "")[:200],
                )
                return False
        except Exception as e:
            log.warning("[KABU_NOTIFY] error %s", e, exc_info=False)
            return False
        return True

    def _mark_sent(self, dedupe_key: str) -> None:
        self._last_sent_mono[dedupe_key] = time.monotonic()
        if self.cfg.dedupe:
            self._dedupe_keys.add(dedupe_key)

    def notify_paper_entry(
        self,
        *,
        symbol: str,
        symbol_name: str,
        entry_price: float,
        entry_time: datetime,
        trigger_level: float,
        rd: dict[str, Any],
    ) -> bool:
        if not self.active:
            return False

        dedupe_key = self._dedupe_key("entry", symbol, entry_time)
        if not self._cooldown_ok(dedupe_key) or not self._dedupe_ok(dedupe_key):
            log.debug("[KABU_NOTIFY] skip entry dedupe/cooldown %s", dedupe_key)
            return False

        stop_px, take_px = _paper_stop_take(entry_price, self.cfg.hard_stop_pct)
        title = f"[KABU_PAPER] ENTRY EXECUTED {symbol}"
        fields = [
            {"name": "symbol", "value": str(symbol), "inline": True},
            {"name": "symbol_name", "value": str(symbol_name or "—"), "inline": True},
            {"name": "entry_price", "value": _fmt_num(entry_price), "inline": True},
            {"name": "trigger_level", "value": _fmt_num(trigger_level), "inline": True},
            {"name": "signal_score", "value": str(rd.get("signal_score", "—")), "inline": True},
            {"name": "tier", "value": str(rd.get("tier", "—")), "inline": True},
            {"name": "vwap_distance_pct", "value": _fmt_num(rd.get("vwap_distance_pct")), "inline": True},
            {"name": "spread_bps", "value": _fmt_num(rd.get("spread_bps")), "inline": True},
            {"name": "board_imbalance", "value": _fmt_num(rd.get("board_imbalance"), digits=3), "inline": True},
            {"name": "stop_price", "value": _fmt_num(stop_px), "inline": True},
            {"name": "take_price", "value": _fmt_num(take_px), "inline": True},
            {"name": "note", "value": "仮想売買 / 発注なし", "inline": False},
        ]
        if not self._post_embed(title=title, fields=fields, color=0x2F855A):
            return False
        self._mark_sent(dedupe_key)
        log.info("[KABU_NOTIFY] paper ENTRY %s @ %s", symbol, entry_price)
        return True

    def notify_paper_exit(
        self,
        *,
        symbol: str,
        entry_price: float,
        exit_price: float,
        entry_time: datetime,
        exit_reason: str,
        pnl_pct: Optional[float],
        mfe_pct: Optional[float],
        elapsed_min: Optional[float],
    ) -> bool:
        if not self.active:
            return False

        dedupe_key = self._dedupe_key("exit", symbol, entry_time)
        if not self._cooldown_ok(dedupe_key) or not self._dedupe_ok(dedupe_key):
            log.debug("[KABU_NOTIFY] skip exit dedupe/cooldown %s", dedupe_key)
            return False

        pnl_pct_f = float(pnl_pct) if pnl_pct is not None else 0.0
        pnl_yen = (float(exit_price) - float(entry_price)) * 100.0

        title = f"[KABU_PAPER] EXIT EXECUTED {symbol}"
        fields = [
            {"name": "symbol", "value": str(symbol), "inline": True},
            {"name": "exit_price", "value": _fmt_num(exit_price), "inline": True},
            {"name": "entry_price", "value": _fmt_num(entry_price), "inline": True},
            {"name": "pnl_pct", "value": _fmt_num(pnl_pct_f), "inline": True},
            {"name": "pnl_yen_100shares", "value": _fmt_num(pnl_yen, digits=0), "inline": True},
            {"name": "exit_reason", "value": str(exit_reason or "—"), "inline": True},
            {"name": "mfe_pct", "value": _fmt_num(mfe_pct), "inline": True},
            {"name": "elapsed_min", "value": _fmt_num(elapsed_min), "inline": True},
            {"name": "note", "value": "仮想決済 / 発注なし", "inline": False},
        ]
        if not self._post_embed(title=title, fields=fields, color=0xC05621):
            return False
        self._mark_sent(dedupe_key)
        log.info("[KABU_NOTIFY] paper EXIT %s reason=%s pnl%%=%s", symbol, exit_reason, pnl_pct_f)
        return True
