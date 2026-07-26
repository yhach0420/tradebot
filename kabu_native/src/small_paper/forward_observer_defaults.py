"""Phase687W58 — Paper Runtime defaults for observe-only Forward observers.

Priority: explicit env → config → Paper default ON.

Non-paper (replay / unit tests / unset paper flag): default OFF unless env/config ON.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional

PAPER_RUNTIME_ENV = "KABU_PAPER_RUNTIME"
COST_AWARE_ENV = "COST_AWARE_ENTRY_SHADOW"
COST_AWARE_V2_ENV = "COST_AWARE_ENTRY_V2_SHADOW"
PULLBACK_VOLUME_ENV = "PULLBACK_VOLUME_FORWARD"

_TRUE = frozenset({"1", "true", "TRUE", "yes", "YES", "on", "ON"})
_FALSE = frozenset({"0", "false", "FALSE", "no", "NO", "off", "OFF"})


def parse_env_bool(name: str) -> Optional[bool]:
    """Return True/False if env is set, else None when unset/empty."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None


def is_paper_runtime(cfg: Any = None) -> bool:
    """True only for Paper Runtime context (not pytest/replay by default)."""
    env = parse_env_bool(PAPER_RUNTIME_ENV)
    if env is not None:
        return env
    if cfg is None:
        return False
    if isinstance(cfg, Mapping):
        if cfg.get("paper_runtime") is True or cfg.get("is_paper_runtime") is True:
            return True
        mode = str(cfg.get("runtime_mode") or cfg.get("source") or "").lower()
        if mode == "paper":
            return True
        return False
    if bool(getattr(cfg, "paper_runtime", False)):
        return True
    if bool(getattr(cfg, "is_paper_runtime", False)):
        return True
    return False


def _config_flag(cfg: Any, block_key: str) -> Optional[bool]:
    if cfg is None:
        return None
    if isinstance(cfg, Mapping):
        block = cfg.get(block_key)
        if isinstance(block, Mapping) and "enabled" in block:
            return bool(block.get("enabled"))
        if block_key in cfg and isinstance(cfg.get(block_key), bool):
            return bool(cfg.get(block_key))
        return None
    block = getattr(cfg, block_key, None)
    if isinstance(block, Mapping) and "enabled" in block:
        return bool(block.get("enabled"))
    attr = f"{block_key}_enabled"
    if hasattr(cfg, attr):
        return bool(getattr(cfg, attr))
    return None


def resolve_observer_enabled(
    env_name: str,
    *,
    config_block: str,
    cfg: Any = None,
    paper_default: bool = True,
) -> tuple[bool, str]:
    """Resolve enable flag. Returns (enabled, source) where source is env|config|default."""
    env_v = parse_env_bool(env_name)
    if env_v is not None:
        return env_v, "env"
    cfg_v = _config_flag(cfg, config_block)
    if cfg_v is not None:
        return cfg_v, "config"
    if is_paper_runtime(cfg) and paper_default:
        return True, "default"
    return False, "default"


def resolve_cost_aware_entry_shadow(cfg: Any = None) -> tuple[bool, str]:
    """RETIRED — Paper default OFF; explicit env/config still honored for rollback."""
    return resolve_observer_enabled(
        COST_AWARE_ENV,
        config_block="cost_aware_entry_shadow",
        cfg=cfg,
        paper_default=False,
    )


def is_live_or_real_order_context(cfg: Any = None) -> bool:
    """Live / real-order context → Cost-Aware V2 must stay OFF."""
    if parse_env_bool("LIVE_TRADING") is True:
        return True
    if parse_env_bool("KABU_LIVE_RUNTIME") is True:
        return True
    if cfg is None:
        return False
    if isinstance(cfg, Mapping):
        if cfg.get("live_trading_enabled") is True:
            return True
        if cfg.get("order_enabled") is True:
            return True
        if cfg.get("real_order_enabled") is True:
            return True
        mode = str(cfg.get("runtime_mode") or cfg.get("source") or "").lower()
        return mode in ("live", "real", "production_live")
    if bool(getattr(cfg, "live_trading_enabled", False)):
        return True
    if bool(getattr(cfg, "order_enabled", False)):
        return True
    if bool(getattr(cfg, "real_order_enabled", False)):
        return True
    mode = str(getattr(cfg, "runtime_mode", "") or getattr(cfg, "source", "") or "").lower()
    return mode in ("live", "real", "production_live")


def resolve_cost_aware_entry_v2_shadow(cfg: Any = None) -> tuple[bool, str]:
    """DISABLED_RESEARCH — default OFF; Live/real-order force OFF; explicit env only."""
    if is_live_or_real_order_context(cfg):
        return False, "live_force_off"
    return resolve_observer_enabled(
        COST_AWARE_V2_ENV,
        config_block="cost_aware_entry_v2_shadow",
        cfg=cfg,
        paper_default=False,
    )


def resolve_pullback_volume_forward(cfg: Any = None) -> tuple[bool, str]:
    return resolve_observer_enabled(
        PULLBACK_VOLUME_ENV,
        config_block="pullback_volume_forward",
        cfg=cfg,
        paper_default=True,
    )


def mark_paper_runtime() -> None:
    """Mark this process (and inherited children) as Paper Runtime."""
    if parse_env_bool(PAPER_RUNTIME_ENV) is None:
        os.environ[PAPER_RUNTIME_ENV] = "1"


def ensure_paper_forward_observer_env() -> dict[str, str]:
    """Paper launch helper: mark paper + set logger/observer env only when unset.

    Cost-Aware v1/v2 are RETIRED — do not auto-enable.
    Pullback Volume remains LOGGER_ONLY (Paper default ON when unset).
    Does not overwrite explicit 0/1 from the user or parent shell.
    """
    mark_paper_runtime()
    applied: dict[str, str] = {}
    for name in (PULLBACK_VOLUME_ENV,):
        if parse_env_bool(name) is None:
            os.environ[name] = "1"
            applied[name] = "1"
    return applied


def format_forward_observers_startup_lines(
    *,
    cost_aware_enabled: bool,
    cost_aware_source: str,
    pullback_enabled: bool,
    pullback_source: str,
    cost_aware_v2_enabled: bool = False,
    cost_aware_v2_source: str = "default",
) -> list[str]:
    def _label(on: bool, source: str) -> str:
        if on:
            return "ON"
        if source == "env":
            return "OFF (explicit)"
        if source == "config":
            return "OFF (config)"
        if source == "live_force_off":
            return "OFF (live_force)"
        return "OFF"

    return [
        "[FORWARD OBSERVERS]",
        f"Cost-Aware Entry: {_label(cost_aware_enabled, cost_aware_source)}",
        f"Cost-Aware Entry V2: {_label(cost_aware_v2_enabled, cost_aware_v2_source)}",
        f"Pullback Volume: {_label(pullback_enabled, pullback_source)}",
        "mode: observe-only",
        "runtime impact: none",
    ]


def forward_observer_status_block(cfg: Any = None) -> dict[str, Any]:
    ca_on, ca_src = resolve_cost_aware_entry_shadow(cfg)
    ca2_on, ca2_src = resolve_cost_aware_entry_v2_shadow(cfg)
    pv_on, pv_src = resolve_pullback_volume_forward(cfg)
    warn = None
    if is_paper_runtime(cfg):
        # Cost-Aware v1/v2 RETIRED — default OFF is expected (no warning).
        pv_unintended_off = (not pv_on) and pv_src not in ("env", "config")
        if pv_unintended_off:
            warn = "FORWARD_OBSERVER_DISABLED_WARNING"
    return {
        "cost_aware_entry_shadow_enabled": ca_on,
        "cost_aware_entry_shadow_source": ca_src,
        "cost_aware_entry_v2_shadow_enabled": ca2_on,
        "cost_aware_entry_v2_shadow_source": ca2_src,
        "pullback_volume_forward_enabled": pv_on,
        "pullback_volume_forward_source": pv_src,
        "observe_only": True,
        "new_reject": False,
        "new_permit": False,
        "gate_decision_unchanged": True,
        "fail_open": True,
        "paper_runtime": is_paper_runtime(cfg),
        "warning": warn,
        "discord_lines": format_forward_observers_startup_lines(
            cost_aware_enabled=ca_on,
            cost_aware_source=ca_src,
            pullback_enabled=pv_on,
            pullback_source=pv_src,
            cost_aware_v2_enabled=ca2_on,
            cost_aware_v2_source=ca2_src,
        ),
    }
