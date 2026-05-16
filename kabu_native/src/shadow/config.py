"""
Load and validate kabu_native shadow configuration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

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
    discord_notify: bool = False
    place_orders: bool = False
    connect_yahoo_watch: bool = False


@dataclass
class ShadowConfig:
    rules: ShadowRules = field(default_factory=ShadowRules)
    watchlist: ShadowWatchlistConfig = field(default_factory=ShadowWatchlistConfig)
    runtime: ShadowRuntime = field(default_factory=ShadowRuntime)
    safety: ShadowSafety = field(default_factory=ShadowSafety)
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


def load_shadow_config(path: Path) -> ShadowConfig:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"shadow config must be a mapping: {path}")

    _warn_deprecated_keys(raw)
    for section in ("rules", "watchlist", "runtime", "session"):
        sub = raw.get(section)
        if isinstance(sub, dict):
            _warn_deprecated_keys(sub, prefix=section)

    rules_raw = raw.get("rules") or {}
    wl_raw = raw.get("watchlist") or {}
    rt_raw = raw.get("runtime") or {}
    sf_raw = raw.get("safety") or {}

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

    def _sf_false(*keys: str) -> bool:
        for k in keys:
            if k in sf_raw:
                return bool(sf_raw[k])
        return False

    safety = ShadowSafety(
        discord_notify=_sf_false("discord_notify", "discord_enabled"),
        place_orders=_sf_false("place_orders", "order_enabled", "orders_enabled"),
        connect_yahoo_watch=_sf_false(
            "connect_yahoo_watch", "legacy_yahoo_watch_enabled", "yahoo_watch_enabled"
        ),
    )
    if safety.discord_notify or safety.place_orders or safety.connect_yahoo_watch:
        raise ValueError("shadow safety flags must be false (no notify/orders/yahoo_watch)")

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
        raw=raw,
    )
