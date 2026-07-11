"""
Phase616: CoreRuntimeMode — Paper Trade Core vs Extension Layer separation.
"""

from __future__ import annotations

import os
from dataclasses import replace
from enum import Enum
from typing import Any, Mapping, Optional

from small_paper.config import SmallPaperPilotConfig

CORE_ONLY = "CORE_ONLY"
CORE_PLUS_AUDIT = "CORE_PLUS_AUDIT"
FULL_EXTENSION = "FULL_EXTENSION"


class CoreRuntimeMode(str, Enum):
    CORE_ONLY = CORE_ONLY
    CORE_PLUS_AUDIT = CORE_PLUS_AUDIT
    FULL_EXTENSION = FULL_EXTENSION


# Extension flags forced OFF for CORE_ONLY and CORE_PLUS_AUDIT (audit is separate).
EXTENSION_FLAGS_OFF: dict[str, bool] = {
    "live_order_adapter_enabled": False,
    "live_order_notifier_enabled": False,
    "live_capital_check_enabled": False,
    "entry_freshness_board_fallback_enabled": False,
    "vol_liq_startup_cache_enabled": False,
    "live_order_dry_run_enabled": False,
    "live_order_api_wiring_enabled": False,
    "live_order_jsonl_enabled": False,
    "live_order_safety_sm_enabled": False,
    "volume_gate_relaxation_shadow_enabled": False,
    "entry_latency_trace_enabled": False,
    "exit_shadow_monitor_enabled": False,
    "low_liquidity_shadow_enabled": False,
}

STARTUP_LOG_TEMPLATE = "[PAPER TRADE] core_runtime_mode={mode}"


def env_core_runtime_mode() -> Optional[str]:
    val = os.environ.get("CORE_RUNTIME_MODE", "").strip().upper()
    if val in (CORE_ONLY, CORE_PLUS_AUDIT, FULL_EXTENSION):
        return val
    if os.environ.get("PRE625_RUNTIME_STRUCTURE_MODE", "").strip().lower() in ("1", "true", "yes", "on"):
        return CORE_ONLY
    return None


def resolve_core_runtime_mode(
    config: SmallPaperPilotConfig,
    *,
    cli_mode: Optional[str] = None,
    cli_pre625: bool = False,
) -> CoreRuntimeMode:
    if cli_pre625:
        return CoreRuntimeMode.CORE_ONLY
    if cli_mode:
        return CoreRuntimeMode(str(cli_mode).strip().upper())
    raw_mode = str(getattr(config, "core_runtime_mode", "") or "").strip().upper()
    if raw_mode in (CORE_ONLY, CORE_PLUS_AUDIT, FULL_EXTENSION):
        return CoreRuntimeMode(raw_mode)
    if bool(getattr(config, "pre625_runtime_structure_mode", False)):
        return CoreRuntimeMode.CORE_ONLY
    env = env_core_runtime_mode()
    if env:
        return CoreRuntimeMode(env)
    return CoreRuntimeMode.FULL_EXTENSION


def extension_bus_enabled(mode: CoreRuntimeMode) -> bool:
    return mode != CoreRuntimeMode.CORE_ONLY


def audit_enabled_for_mode(mode: CoreRuntimeMode) -> bool:
    return mode in (CoreRuntimeMode.CORE_PLUS_AUDIT, CoreRuntimeMode.FULL_EXTENSION)


def full_extension_active(mode: CoreRuntimeMode) -> bool:
    return mode == CoreRuntimeMode.FULL_EXTENSION


def apply_core_runtime_mode(
    config: SmallPaperPilotConfig,
    mode: CoreRuntimeMode,
) -> SmallPaperPilotConfig:
    raw = dict(config.raw)
    raw["core_runtime_mode"] = mode.value
    raw["pre625_runtime_structure_mode"] = mode == CoreRuntimeMode.CORE_ONLY
    if mode == CoreRuntimeMode.FULL_EXTENSION:
        defaults = SmallPaperPilotConfig()
        restored = {
            k: bool(raw.get(k, getattr(defaults, k, False)))
            for k in EXTENSION_FLAGS_OFF
        }
        raw.update(restored)
        return replace(
            config,
            core_runtime_mode=mode.value,
            pre625_runtime_structure_mode=False,
            raw=raw,
            **restored,
        )
    raw.update(EXTENSION_FLAGS_OFF)
    return replace(
        config,
        core_runtime_mode=mode.value,
        pre625_runtime_structure_mode=(mode == CoreRuntimeMode.CORE_ONLY),
        raw=raw,
        **EXTENSION_FLAGS_OFF,
    )


def finalize_core_runtime_config(
    config: SmallPaperPilotConfig,
    *,
    cli_mode: Optional[str] = None,
    cli_pre625: bool = False,
) -> SmallPaperPilotConfig:
    mode = resolve_core_runtime_mode(config, cli_mode=cli_mode, cli_pre625=cli_pre625)
    return apply_core_runtime_mode(config, mode)


def get_core_runtime_mode(config: SmallPaperPilotConfig) -> CoreRuntimeMode:
    return resolve_core_runtime_mode(config)


def core_runtime_session_fields(config: SmallPaperPilotConfig) -> dict[str, Any]:
    mode = get_core_runtime_mode(config)
    out: dict[str, Any] = {
        "core_runtime_mode": mode.value,
        "pre625_runtime_structure_mode": bool(getattr(config, "pre625_runtime_structure_mode", False)),
        "extension_bus_enabled": extension_bus_enabled(mode),
        "audit_enabled": audit_enabled_for_mode(mode),
    }
    if mode != CoreRuntimeMode.FULL_EXTENSION:
        out["extension_forced_off"] = dict(EXTENSION_FLAGS_OFF)
    return out


def log_core_runtime_mode(config: SmallPaperPilotConfig) -> None:
    mode = get_core_runtime_mode(config)
    print(STARTUP_LOG_TEMPLATE.format(mode=mode.value), flush=True)
