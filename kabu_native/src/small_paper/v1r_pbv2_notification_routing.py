"""V1R_PBV2_NOTIFICATION_ROUTING_ONLY — Discord route fix for PBv2 SHADOW_ONLY.

Scope (STRICT):
  - Reroute classic Paper ENTRY/EXIT Discord away from trade-notify
    → research / [PBV2 SHADOW] webhook.
  - MUST NOT touch Arch E Primary state, occupancy, dual-lane admit/exit,
    pilot_runner register_entry, or submit/cancel/live.

Enable:
  - V1R_PBV2_NOTIFICATION_ROUTING_ONLY=1  (explicit)
  - or unset while V1R_EXIT_V2_LIVE_PRIMARY is on (default ON with Primary)
Disable:
  - V1R_PBV2_NOTIFICATION_ROUTING_ONLY=0
"""
from __future__ import annotations

import os
from typing import Optional

from notify.discord_notification_model import (
    WEBHOOK_ENV_RESEARCH,
    WEBHOOK_ENV_RESEARCH_LEGACY,
)
from notify.discord_notification_router import resolve_webhook_url

ENV_ROUTING_ONLY = "V1R_PBV2_NOTIFICATION_ROUTING_ONLY"
ENV_LIVE_PRIMARY = "V1R_EXIT_V2_LIVE_PRIMARY"
PBV2_SHADOW_PREFIX = "[PBV2 SHADOW]"
ROUTABLE_TRADE_TAGS = frozenset({"ENTRY", "EXIT"})


def _truthy(raw: Optional[str]) -> bool:
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def _explicit_raw(environ: Optional[dict] = None) -> Optional[str]:
    env = environ if environ is not None else os.environ
    if ENV_ROUTING_ONLY not in env:
        return None
    return str(env.get(ENV_ROUTING_ONLY) or "")


def routing_only_enabled(environ: Optional[dict] = None) -> bool:
    """Notification-routing-only gate. Never enables occupancy changes."""
    env = environ if environ is not None else os.environ
    explicit = _explicit_raw(env)
    if explicit is not None:
        return _truthy(explicit)
    # Default ON whenever V1R live Primary contract is active (PBv2=SHADOW_ONLY).
    return _truthy(str(env.get(ENV_LIVE_PRIMARY) or ""))


def should_reroute_trade_event(event_tag: str, *, environ: Optional[dict] = None) -> bool:
    tag = str(event_tag or "").strip().upper()
    return routing_only_enabled(environ) and tag in ROUTABLE_TRADE_TAGS


def resolve_pbv2_shadow_webhook(
    environ: Optional[dict] = None,
) -> tuple[str, str]:
    """Return (url, env_key) for PBv2 SHADOW research channel. No trade-notify fallback."""
    # resolve_webhook_url reads os.environ; tests should patch env before call.
    if environ is not None:
        # Apply overlay without mutating caller permanently when possible
        saved = {k: os.environ.get(k) for k in (WEBHOOK_ENV_RESEARCH, WEBHOOK_ENV_RESEARCH_LEGACY)}
        try:
            for k in (WEBHOOK_ENV_RESEARCH, WEBHOOK_ENV_RESEARCH_LEGACY):
                if k in environ:
                    os.environ[k] = str(environ.get(k) or "")
                elif k in os.environ and k not in environ:
                    pass
            url, key = resolve_webhook_url((WEBHOOK_ENV_RESEARCH, WEBHOOK_ENV_RESEARCH_LEGACY))
            return str(url or ""), str(key or WEBHOOK_ENV_RESEARCH)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    url, key = resolve_webhook_url((WEBHOOK_ENV_RESEARCH, WEBHOOK_ENV_RESEARCH_LEGACY))
    return str(url or ""), str(key or WEBHOOK_ENV_RESEARCH)


def shadow_title(title_line: str) -> str:
    t = str(title_line or "")
    if t.startswith(PBV2_SHADOW_PREFIX):
        return t
    return f"{PBV2_SHADOW_PREFIX} {t}".strip()[:256]


def routing_audit() -> dict:
    """Secret-free status for tests / ops."""
    enabled = routing_only_enabled()
    url, key = resolve_pbv2_shadow_webhook()
    return {
        "mode": ENV_ROUTING_ONLY,
        "enabled": enabled,
        "reroute_tags": sorted(ROUTABLE_TRADE_TAGS),
        "destination_env": key,
        "destination_configured": bool(url),
        "affects_arch_e_occupancy": False,
        "affects_dual_lane": False,
        "affects_submit_cancel_live": False,
    }
