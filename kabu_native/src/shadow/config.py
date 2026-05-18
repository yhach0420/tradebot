"""
Load and validate kabu_native shadow configuration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from notify.discord import ShadowDiscordConfig

log = logging.getLogger("kabu_native.shadow")


@dataclass
class ShadowRules:
    market_session_control: bool = True
    bf_confirm_count: int = 2
    fail_window_min: float = 2.0
    fail_buffer_pct: float = 0.12
    hard_stop_pct: float = 1.20
    tier: str = "B"
    entry_score_min: int = 60
    require_timing_ok: bool = True


@dataclass
class ShadowWatchlistConfig:
    source: str = "morning_screen"
    path: str | None = None
    universe_path: str = "kabu_native/data/universe/universe_intraday_full.csv"
    top_n: int = 10
    passed_only: bool = True


@dataclass
class ShadowRuntime:
    poll_interval_sec: float = 15.0
    use_push: bool = False
    max_polls: int | None = None
    continue_on_error: bool = True


@dataclass
class ShadowSafety:
    discord_enabled: bool = False
    discord_notify: bool = False
    place_orders: bool = False
    order_enabled: bool = False
    connect_yahoo_watch: bool = False
    legacy_yahoo_watch_enabled: bool = False


@dataclass
class ShadowConfig:
    rules: ShadowRules = field(default_factory=ShadowRules)
    watchlist: ShadowWatchlistConfig = field(default_factory=ShadowWatchlistConfig)
    runtime: ShadowRuntime = field(default_factory=ShadowRuntime)
    safety: ShadowSafety = field(default_factory=ShadowSafety)
    discord: ShadowDiscordConfig = field(default_factory=ShadowDiscordConfig)
    raw: dict[str, Any] = field(default_factory=dict)


def _warn_deprecated_keys(raw: Mapping[str, Any], *, prefix: str = "") -> None:
    banned = ("no_entry_until", "opening_gate", "gate_0930")
    for key in raw:
        if key in banned or "no_entry" in key.lower():
            log.warning(
                "shadow config %s: deprecated key %r ignored — use market_session_control",
                prefix or "root",
                key,
            )
        if isinstance(raw[key], Mapping):
            _warn_deprecated_keys(raw[key], prefix=f"{prefix}.{key}" if prefix else key)


def _bool_from_section(section: Mapping[str, Any], *keys: str, default: bool = False) -> bool:
    for k in keys:
        if k in section:
            return bool(section[k])
    return default


def load_shadow_config(path: Path) -> ShadowConfig:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"shadow config must be a mapping: {path}")

    _warn_deprecated_keys(raw)
    for section in ("rules", "watchlist", "runtime", "session", "safety", "discord"):
        sub = raw.get(section)
        if isinstance(sub, dict):
            _warn_deprecated_keys(sub, prefix=section)

    rules_raw = raw.get("rules") or {}
    wl_raw = raw.get("watchlist") or {}
    rt_raw = raw.get("runtime") or {}
    sf_raw = raw.get("safety") or {}
    dc_raw = raw.get("discord") if isinstance(raw.get("discord"), dict) else {}

    rules = ShadowRules(
        market_session_control=bool(rules_raw.get("market_session_control", True)),
        bf_confirm_count=int(rules_raw.get("bf_confirm_count", 2)),
        fail_window_min=float(rules_raw.get("fail_window_min", 2)),
        fail_buffer_pct=float(rules_raw.get("fail_buffer_pct", 0.12)),
        hard_stop_pct=float(rules_raw.get("hard_stop_pct", 1.20)),
        tier=str(rules_raw.get("tier", "B")).upper(),
        entry_score_min=int(rules_raw.get("entry_score_min", 60)),
        require_timing_ok=bool(rules_raw.get("require_timing_ok", True)),
    )

    if not rules.market_session_control:
        log.warning("shadow: market_session_control=false overrides Phase 13 adopted rules")

    discord_enabled = _bool_from_section(
        raw,
        "discord_enabled",
        default=_bool_from_section(dc_raw, "enabled", default=False),
    )
    if not discord_enabled:
        discord_enabled = _bool_from_section(sf_raw, "discord_enabled", "discord_notify", default=False)

    discord_shadow_notify = _bool_from_section(
        raw,
        "discord_shadow_notify",
        default=_bool_from_section(dc_raw, "shadow_notify", default=False),
    )
    discord_paper_trade_notify = _bool_from_section(
        raw,
        "discord_paper_trade_notify",
        default=_bool_from_section(dc_raw, "paper_trade_notify", default=False),
    )

    place_orders = _bool_from_section(
        sf_raw, "place_orders", "order_enabled", "orders_enabled", default=False
    )
    connect_yahoo = _bool_from_section(
        sf_raw,
        "connect_yahoo_watch",
        "legacy_yahoo_watch_enabled",
        "yahoo_watch_enabled",
        default=False,
    )

    if place_orders:
        raise ValueError("shadow safety: order_enabled / place_orders must be false (no real orders)")
    if connect_yahoo:
        raise ValueError("shadow safety: legacy_yahoo_watch must stay false")
    notify_on = discord_enabled and discord_shadow_notify and discord_paper_trade_notify
    if place_orders and notify_on:
        raise ValueError("shadow safety: cannot enable discord paper notify together with orders")

    discord = ShadowDiscordConfig(
        enabled=discord_enabled,
        shadow_notify=discord_shadow_notify,
        paper_trade_notify=discord_paper_trade_notify,
        webhook_env=str(
            raw.get("discord_webhook_env")
            or dc_raw.get("webhook_env")
            or "KABU_SHADOW_DISCORD_WEBHOOK_URL"
        ),
        cooldown_sec=float(raw.get("discord_cooldown_sec", dc_raw.get("cooldown_sec", 300))),
        dedupe=bool(raw.get("discord_dedupe", dc_raw.get("dedupe", True))),
        hard_stop_pct=float(
            raw.get("discord_hard_stop_pct", dc_raw.get("hard_stop_pct", rules.hard_stop_pct))
        ),
    )

    if notify_on:
        log.info(
            "shadow: [KABU_PAPER] Discord virtual paper notify ON (webhook env=%s)",
            discord.webhook_env,
        )
    else:
        log.debug("shadow: Discord virtual paper notify OFF (default)")

    safety = ShadowSafety(
        discord_enabled=discord.enabled,
        discord_notify=discord.shadow_notify,
        place_orders=place_orders,
        order_enabled=place_orders,
        connect_yahoo_watch=connect_yahoo,
        legacy_yahoo_watch_enabled=connect_yahoo,
    )

    return ShadowConfig(
        rules=rules,
        watchlist=ShadowWatchlistConfig(
            source=str(wl_raw.get("source", "morning_screen")).strip().lower(),
            path=str(wl_raw["path"]) if wl_raw.get("path") else None,
            universe_path=str(
                wl_raw.get("universe_path", "kabu_native/data/universe/universe_intraday_full.csv")
            ),
            top_n=int(wl_raw.get("top_n", 10)),
            passed_only=bool(wl_raw.get("passed_only", True)),
        ),
        runtime=ShadowRuntime(
            poll_interval_sec=float(rt_raw.get("poll_interval_sec", 15)),
            use_push=bool(rt_raw.get("use_push", False)),
            max_polls=int(rt_raw["max_polls"]) if rt_raw.get("max_polls") is not None else None,
            continue_on_error=bool(rt_raw.get("continue_on_error", True)),
        ),
        safety=safety,
        discord=discord,
        raw=raw,
    )
