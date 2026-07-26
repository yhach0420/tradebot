"""Phase687W59 — Discord current-system summary render (observe-only).

Builds structured JSON + Discord text for startup / shadow / daily.
Does not change GateDecision, ENTRY rank, CAP, EXIT, or Shadow predicates.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

from small_paper.forward_observer_defaults import (
    COST_AWARE_ENV,
    PULLBACK_VOLUME_ENV,
    forward_observer_status_block,
    is_paper_runtime,
    parse_env_bool,
)
from small_paper.pullback_volume_forward_logger import disk_usage_pct

JST = ZoneInfo("Asia/Tokyo")
DISCORD_SOFT_LIMIT = 1800
OWNERSHIP = "RESEARCH"

OBSERVER_KEYS = (
    "cost_aware_entry",
    "flat_weak_range",
    "pullback_misread",
    "pullback_volume",
)
OBSERVER_LABELS = {
    "cost_aware_entry": "Cost-Aware Entry",
    "flat_weak_range": "Flat Weak + Range",
    "pullback_misread": "PullbackMisread",
    "pullback_volume": "Pullback Volume",
}


def _yen(v: Any) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "n/a"
    sign = "+" if x >= 0 else ""
    return f"{sign}{int(round(x)):,}円"


def _pct(v: Any, digits: int = 1) -> str:
    try:
        return f"{100.0 * float(v):.{digits}f}%"
    except (TypeError, ValueError):
        return "n/a"


def _on_off(enabled: bool, source: str, *, unexpected: bool = False) -> str:
    if enabled:
        return "ON"
    if source == "env":
        return "OFF (explicit)"
    if source == "config":
        return "OFF (config)"
    if unexpected:
        return "OFF (unexpected)"
    return "OFF"


def _cfg_flag(cfg: Any, *names: str) -> bool:
    for n in names:
        if cfg is None:
            continue
        if isinstance(cfg, Mapping) and n in cfg:
            return bool(cfg.get(n))
        if hasattr(cfg, n):
            return bool(getattr(cfg, n))
    return False


def _cfg_flag_opt(cfg: Any, *names: str) -> Optional[bool]:
    """Return explicit bool from config, or None when unset (do not invent OFF)."""
    for n in names:
        if cfg is None:
            continue
        if isinstance(cfg, Mapping) and n in cfg and cfg.get(n) is not None:
            return bool(cfg.get(n))
        if hasattr(cfg, n) and getattr(cfg, n) is not None:
            return bool(getattr(cfg, n))
    return None


def lookup_runtime_observer_flag(observer_name: str, cfg: Any = None) -> Optional[bool]:
    """Runtime/env/config SoT. None = unresolved (never invent OFF from missing default)."""
    if observer_name == "cost_aware_entry":
        env_v = parse_env_bool(COST_AWARE_ENV)
        if env_v is not None:
            return env_v
        if isinstance(cfg, Mapping):
            block = cfg.get("cost_aware_entry_shadow")
            if isinstance(block, Mapping) and "enabled" in block:
                return bool(block.get("enabled"))
            if isinstance(cfg.get("cost_aware_entry_shadow_enabled"), bool):
                return bool(cfg.get("cost_aware_entry_shadow_enabled"))
        elif cfg is not None:
            block = getattr(cfg, "cost_aware_entry_shadow", None)
            if isinstance(block, Mapping) and "enabled" in block:
                return bool(block.get("enabled"))
            if hasattr(cfg, "cost_aware_entry_shadow_enabled"):
                return bool(getattr(cfg, "cost_aware_entry_shadow_enabled"))
        if is_paper_runtime(cfg):
            return True
        return None
    if observer_name == "pullback_volume":
        env_v = parse_env_bool(PULLBACK_VOLUME_ENV)
        if env_v is not None:
            return env_v
        if isinstance(cfg, Mapping):
            block = cfg.get("pullback_volume_forward")
            if isinstance(block, Mapping) and "enabled" in block:
                return bool(block.get("enabled"))
            if isinstance(cfg.get("pullback_volume_forward_enabled"), bool):
                return bool(cfg.get("pullback_volume_forward_enabled"))
        elif cfg is not None:
            block = getattr(cfg, "pullback_volume_forward", None)
            if isinstance(block, Mapping) and "enabled" in block:
                return bool(block.get("enabled"))
            if hasattr(cfg, "pullback_volume_forward_enabled"):
                return bool(getattr(cfg, "pullback_volume_forward_enabled"))
        if is_paper_runtime(cfg):
            return True
        return None
    if observer_name == "flat_weak_range":
        return _cfg_flag_opt(cfg, "flat_weak_range_shadow_enabled")
    if observer_name == "pullback_misread":
        explicit = _cfg_flag_opt(
            cfg,
            "pullback_misread_guard_shadow_enabled",
            "pullback_misread_entry_guard_shadow_enabled",
        )
        if explicit is not None:
            return explicit
        # Paper stack always includes Dynamic40 PullbackMisread shadow (config default ON)
        if is_paper_runtime(cfg):
            return True
        return None
    return None


def extract_summary_observer_enabled(
    observer_name: str, summary: Optional[Mapping[str, Any]]
) -> Optional[bool]:
    """Explicit enabled from structured summary / metadata only (never from hits)."""
    if not isinstance(summary, Mapping):
        return None
    if observer_name == "cost_aware_entry":
        ca = summary.get("cost_aware_entry_shadow")
        if isinstance(ca, Mapping) and "enabled" in ca:
            return bool(ca.get("enabled"))
        if summary.get("cost_aware_entry_shadow_enabled") is not None:
            return bool(summary.get("cost_aware_entry_shadow_enabled"))
        return None
    if observer_name == "flat_weak_range":
        if summary.get("flat_weak_range_shadow_enabled") is not None:
            return bool(summary.get("flat_weak_range_shadow_enabled"))
        nested = summary.get("flat_weak_range")
        if isinstance(nested, Mapping) and "enabled" in nested:
            return bool(nested.get("enabled"))
        return None
    if observer_name == "pullback_misread":
        if summary.get("pullback_misread_guard_shadow_enabled") is not None:
            return bool(summary.get("pullback_misread_guard_shadow_enabled"))
        nested = summary.get("pullback_misread_entry_guard_shadow")
        if isinstance(nested, Mapping) and "enabled" in nested:
            return bool(nested.get("enabled"))
        nested2 = summary.get("pullback_misread")
        if isinstance(nested2, Mapping) and "enabled" in nested2:
            return bool(nested2.get("enabled"))
        return None
    if observer_name == "pullback_volume":
        pv = summary.get("pullback_volume_forward")
        if isinstance(pv, Mapping) and "enabled" in pv:
            return bool(pv.get("enabled"))
        if summary.get("pullback_volume_forward_enabled") is not None:
            return bool(summary.get("pullback_volume_forward_enabled"))
        nested = summary.get("pullback_volume")
        if isinstance(nested, Mapping) and "enabled" in nested:
            return bool(nested.get("enabled"))
        return None
    return None


def _count_int(v: Any) -> int:
    try:
        if v is None or v == "":
            return 0
        return int(v)
    except (TypeError, ValueError):
        return 0


def observer_data_present(observer_name: str, summary: Mapping[str, Any]) -> tuple[bool, int]:
    """Return (has_observation_data, primary_count). Hits never imply enabled."""
    if observer_name == "cost_aware_entry":
        ca = summary.get("cost_aware_entry_shadow") if isinstance(summary.get("cost_aware_entry_shadow"), Mapping) else {}
        n = max(
            _count_int(ca.get("candidates") or ca.get("shadow_eligible") or summary.get("cost_aware_evaluated_count")),
            _count_int(ca.get("selection_cycles")),
            _count_int(ca.get("official_entry_mismatch") or ca.get("shadow_different_choice")),
            _count_int(ca.get("n_closed") or ca.get("virtual_outcomes_completed")),
            _count_int(ca.get("official_entry_match")),
        )
        return n > 0, n
    if observer_name == "flat_weak_range":
        n = max(
            _count_int(summary.get("flat_weak_range_shadow_target_count")),
            _count_int(summary.get("flat_weak_range_shadow_block_count")),
            _count_int(summary.get("flat_weak_range_shadow_kept_count")),
            _count_int(summary.get("flat_weak_range_shadow_completed")),
        )
        return n > 0, n
    if observer_name == "pullback_misread":
        n = max(
            _count_int(summary.get("pullback_misread_guard_shadow_blocked_count")),
            _count_int(summary.get("pullback_misread_completed")),
            _count_int(summary.get("pullback_misread_blocked_losers")),
        )
        return n > 0, n
    if observer_name == "pullback_volume":
        pv = summary.get("pullback_volume_forward") if isinstance(summary.get("pullback_volume_forward"), Mapping) else {}
        vh = pv.get("volume_high") if isinstance(pv.get("volume_high"), Mapping) else {}
        vm = pv.get("volume_mid") if isinstance(pv.get("volume_mid"), Mapping) else {}
        vl = pv.get("volume_low") if isinstance(pv.get("volume_low"), Mapping) else {}
        n = max(
            _count_int(pv.get("hits") or pv.get("total_pullback_hits")),
            _count_int(pv.get("pullback_volume_eligible_count") or pv.get("eligible")),
            _count_int(pv.get("pullback_volume_recorded_count") or pv.get("recorded") or pv.get("rows")),
            _count_int(pv.get("volume_high_n") or vh.get("n")),
            _count_int(pv.get("volume_mid_n") or vm.get("n")),
            _count_int(pv.get("volume_low_n") or vl.get("n")),
        )
        return n > 0, n
    return False, 0


def resolve_observer_enabled(
    observer_name: str,
    runtime_config: Any,
    observer_summary: Optional[Mapping[str, Any]],
) -> Optional[bool]:
    """Priority: runtime config → structured summary enabled → unresolved (None).

    Never infers enabled from hits/evaluations.
    """
    config_value = lookup_runtime_observer_flag(observer_name, runtime_config)
    if config_value is not None:
        return bool(config_value)
    explicit = extract_summary_observer_enabled(observer_name, observer_summary)
    if explicit is not None:
        return bool(explicit)
    return None


def resolve_all_observer_states(
    summary: Optional[Mapping[str, Any]] = None,
    cfg: Any = None,
) -> dict[str, dict[str, Any]]:
    """Build per-observer enabled/data/mismatch state from one SoT path."""
    summary = summary if isinstance(summary, Mapping) else {}
    out: dict[str, dict[str, Any]] = {}
    for key in OBSERVER_KEYS:
        cfg_v = lookup_runtime_observer_flag(key, cfg)
        sum_v = extract_summary_observer_enabled(key, summary)
        enabled = resolve_observer_enabled(key, cfg, summary)
        has_data, count = observer_data_present(key, summary)
        config_summary_conflict = (
            cfg_v is not None and sum_v is not None and bool(cfg_v) != bool(sum_v)
        )
        mismatch = bool(
            (enabled is False and has_data)
            or (enabled is None and has_data)
            or config_summary_conflict
        )
        if cfg_v is not None:
            source = "env" if (
                (key == "cost_aware_entry" and parse_env_bool(COST_AWARE_ENV) is not None)
                or (key == "pullback_volume" and parse_env_bool(PULLBACK_VOLUME_ENV) is not None)
            ) else "config"
        elif sum_v is not None:
            source = "summary"
        elif enabled is True and is_paper_runtime(cfg):
            source = "default"
        else:
            source = "unresolved"

        if enabled is True:
            label = "ON"
        elif enabled is False:
            if has_data:
                label = "OFF / DATA PRESENT"
            elif source == "env":
                label = "OFF (explicit)"
            elif source == "config":
                label = "OFF (config)"
            else:
                label = "OFF"
        else:
            label = "UNKNOWN / DATA PRESENT" if has_data else "UNKNOWN"
        out[key] = {
            "enabled": enabled,
            "source": source,
            "label": label,
            "data_present": has_data,
            "data_count": count,
            "mismatch": mismatch,
            "config_value": cfg_v,
            "summary_enabled": sum_v,
            "config_summary_conflict": config_summary_conflict,
            "display_name": OBSERVER_LABELS[key],
        }
    return out


def build_runtime_status(
    cfg: Any = None,
    *,
    trading_date: str = "",
    summary: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Resolve live mainline + observer flags from config/env/summary (no hit-inference)."""
    day = trading_date or datetime.now(JST).strftime("%Y-%m-%d")
    obs = forward_observer_status_block(cfg)
    states = resolve_all_observer_states(summary, cfg)

    disk = disk_usage_pct("C:/")
    cap = 5
    if cfg is not None:
        for key in ("max_concurrent_positions", "position_cap", "cap"):
            if isinstance(cfg, Mapping) and cfg.get(key) is not None:
                try:
                    cap = int(cfg.get(key))
                    break
                except (TypeError, ValueError):
                    pass
            elif hasattr(cfg, key) and getattr(cfg, key) is not None:
                try:
                    cap = int(getattr(cfg, key))
                    break
                except (TypeError, ValueError):
                    pass

    observers = {
        k: {
            "enabled": states[k]["enabled"],
            "source": states[k]["source"],
            "label": states[k]["label"],
            "data_present": states[k]["data_present"],
            "mismatch": states[k]["mismatch"],
        }
        for k in OBSERVER_KEYS
    }

    return {
        "trading_date": day,
        "mode": "PAPER ONLY",
        "real_orders": "DISABLED",
        "universe": "Core10 + Dynamic40",
        "cap": cap,
        "refresh": "10:00 / 14:30",
        "entry_mainline": {
            "pbv2": True,
            "flat_band_mainline": _cfg_flag(cfg, "pbv2_flat_band_mainline_enabled"),
            "entry_price_risk_guard": _cfg_flag(cfg, "entry_price_risk_guard_enabled"),
            "entry_expectancy": True,
            "classic_late_chase_rsi80": _cfg_flag(cfg, "classic_late_chase_rsi_guard_enabled"),
        },
        "exit_mainline": {
            "board_dynamic_trailing": _cfg_flag(
                cfg, "board_dynamic_trailing_enabled", "trailing_mfe_enabled"
            )
            or True,
            "hard_stop_pct": float(getattr(cfg, "hard_stop_pct", 1.2) or 1.2)
            if cfg is not None and hasattr(cfg, "hard_stop_pct")
            else 1.2,
        },
        "observers": observers,
        "observer_states": states,
        "observe_only": True,
        "runtime_impact": "none",
        "w43f_pipeline": "READY / collecting forward",
        "disk_usage_pct": disk,
        "disk_status": "warning only" if isinstance(disk, (int, float)) and disk > 75 else "ok",
        "observer_status_block": obs,
    }


def render_paper_start_lines(status: Mapping[str, Any]) -> list[str]:
    em = status.get("entry_mainline") or {}
    xm = status.get("exit_mainline") or {}
    obs = status.get("observers") or {}

    def _o(key: str) -> str:
        block = obs.get(key) or {}
        if block.get("label"):
            # Strip DATA PRESENT suffix on startup (status-only ON/OFF/UNKNOWN)
            lab = str(block.get("label"))
            return lab.split(" / ")[0]
        en = block.get("enabled")
        if en is None:
            return "UNKNOWN"
        return _on_off(bool(en), str(block.get("source") or "default"))

    stop = float(xm.get("hard_stop_pct") or 1.2)
    disk = status.get("disk_usage_pct")
    disk_s = f"{disk:.1f}% used" if isinstance(disk, (int, float)) and disk >= 0 else "n/a"
    return [
        "[TRADEBOT PAPER START]",
        "",
        f"date: {status.get('trading_date')}",
        f"mode: {status.get('mode')}",
        f"real orders: {status.get('real_orders')}",
        "",
        "Universe:",
        str(status.get("universe")),
        f"CAP: {status.get('cap')}",
        f"Refresh: {status.get('refresh')}",
        "",
        "ENTRY mainline:",
        "PBv2",
        f"flat_band_mainline: {'ON' if em.get('flat_band_mainline') else 'OFF'}",
        f"entry_price_risk_guard: {'ON' if em.get('entry_price_risk_guard') else 'OFF'}",
        f"entry_expectancy: {'ON' if em.get('entry_expectancy') else 'OFF'}",
        f"classic_late_chase_rsi80: {'ON' if em.get('classic_late_chase_rsi80') else 'OFF'}",
        "",
        "EXIT mainline:",
        f"board_dynamic_trailing: {'ON' if xm.get('board_dynamic_trailing') else 'OFF'}",
        f"hard_stop: -{stop:g}%",
        "",
        "Forward Observers:",
        f"Cost-Aware Entry: {_o('cost_aware_entry')}",
        f"Flat Weak + Range: {_o('flat_weak_range')}",
        f"PullbackMisread: {_o('pullback_misread')}",
        f"Pullback Volume: {_o('pullback_volume')}",
        "",
        "Observer mode:",
        "observe-only",
        "runtime impact: none",
        "",
        "W43F pipeline:",
        str(status.get("w43f_pipeline")),
        "",
        "Disk:",
        disk_s,
        f"status: {status.get('disk_status')}",
    ]


def render_observer_status_section(summary: Mapping[str, Any], cfg: Any = None) -> list[str]:
    states = resolve_all_observer_states(summary, cfg)
    errors = int(summary.get("observer_errors") or summary.get("observer_exception_count") or 0)
    lines = [
        "--- Observer Status ---",
        "",
        f"Cost-Aware Entry: {states['cost_aware_entry']['label']}",
        f"Flat Weak + Range: {states['flat_weak_range']['label']}",
        f"PullbackMisread: {states['pullback_misread']['label']}",
        f"Pullback Volume: {states['pullback_volume']['label']}",
        "",
        "mode: observe-only",
        "runtime ENTRY changed: NO",
        "runtime EXIT changed: NO",
        "new Reject: NO",
        "new Permit: NO",
        "",
        f"observer errors: {errors}",
    ]
    return lines


def _section_for_observer_state(
    *,
    title: str,
    state: Mapping[str, Any],
    normal_lines: list[str],
) -> list[str]:
    enabled = state.get("enabled")
    has_data = bool(state.get("data_present"))
    count = int(state.get("data_count") or 0)
    if enabled is False and not has_data:
        return [title, "", "status: OFF"]
    if enabled is False and has_data:
        return [
            title,
            "",
            f"unexpected records: {count}",
            "status: CONFIG/DATA MISMATCH",
        ]
    if enabled is None and not has_data:
        return []
    if enabled is None and has_data:
        return [
            title,
            "",
            f"unexpected records: {count}",
            "status: CONFIG/DATA MISMATCH",
        ]
    return normal_lines


def render_cost_aware_section(summary: Mapping[str, Any], cfg: Any = None) -> list[str]:
    state = resolve_all_observer_states(summary, cfg)["cost_aware_entry"]
    block = summary.get("cost_aware_entry_shadow")
    if not isinstance(block, Mapping):
        return _section_for_observer_state(
            title="--- Cost-Aware ENTRY ---",
            state=state,
            normal_lines=[],
        )
    # Phase722: prefer top-level enabled; fall back to nested block.enabled
    enabled = state.get("enabled")
    if enabled is None:
        enabled = bool(block.get("enabled"))
    if enabled is not True and summary.get("cost_aware_entry_shadow_enabled") is True:
        enabled = True
    if enabled is not True:
        return _section_for_observer_state(
            title="--- Cost-Aware ENTRY ---",
            state=state,
            normal_lines=[],
        )
    cycles = int(block.get("selection_cycles") or 0)
    same = int(block.get("official_entry_match") or 0)
    diff = int(block.get("official_entry_mismatch") or 0)
    completed = int(block.get("n_closed") or 0)
    rt = summary.get("cost_aware_runtime_compatible_pnl", block.get("runtime_compatible_pnl"))
    sh = summary.get("cost_aware_shadow_pnl_after_5bps", block.get("pnl_after_5bps_30m"))
    status = str(summary.get("cost_aware_status") or block.get("status") or "collecting")
    rt_pf = block.get("runtime_compatible_pf_5bps")
    sh_pf = block.get("shadow_pf_5bps_30m") or block.get("fixed_30m_pf_5bps")
    incomplete = ""
    if status == "PARTIAL_PIPELINE":
        missing = []
        if rt is None:
            missing.append("runtime_compatible_pnl")
        if sh is None:
            missing.append("shadow_5bps_pnl")
        if int(block.get("recovery_finalize_count") or 0) > 0:
            missing.append("force_finalize_without_price_path")
        incomplete = ", ".join(missing) if missing else "partial metrics"
    lines = [
        "--- Cost-Aware ENTRY ---",
        "",
        f"status: {status}",
        f"evaluations: {int(block.get('candidates') or block.get('shadow_eligible') or 0)}",
        f"eligible: {int(block.get('eligible') or block.get('shadow_eligible') or 0)}",
        f"selection_cycles: {cycles}",
        f"shadow_entries: {int(block.get('shadow_entries') or 0)}",
        f"stop_risk_reject: {int(block.get('stop_risk_reject') or 0)}",
        "",
        f"runtime selected: {same + diff}",
        f"shadow different choice: {diff}",
        f"same choice: {same}",
        f"virtual outcomes completed: {completed}",
        "",
        f"runtime_compatible_pnl: {_yen(rt) if isinstance(rt, (int, float)) else 'n/a'}",
        f"shadow_pnl_after_5bps: {_yen(sh) if isinstance(sh, (int, float)) else 'n/a'}",
    ]
    if isinstance(rt, (int, float)) and isinstance(sh, (int, float)):
        lines.append(f"delta: {_yen(float(sh) - float(rt))}")
    else:
        lines.append("delta: n/a")
    lines.append(f"runtime PF: {rt_pf if rt_pf is not None else 'n/a'}")
    lines.append(f"shadow PF: {sh_pf if sh_pf is not None else 'n/a'}")
    if incomplete:
        lines.append(f"incomplete reason: {incomplete}")
    lines.extend(
        [
            "",
            f"STOP avoided: {int(block.get('stop_risk_reject') or 0)}",
            f"Winner missed: {int(block.get('never_filled') or 0)}",
            f"Winner captured: {int(block.get('later_fill') or 0)}",
            "",
            "runtime ENTRY unchanged: YES",
        ]
    )
    return lines


def render_flat_weak_range_section(summary: Mapping[str, Any], cfg: Any = None) -> list[str]:
    state = resolve_all_observer_states(summary, cfg)["flat_weak_range"]
    if state.get("enabled") is not True:
        return _section_for_observer_state(
            title="--- Flat Weak + Range ---",
            state=state,
            normal_lines=[],
        )
    if summary.get("flat_weak_range_shadow_enabled") is None and summary.get(
        "flat_weak_range_shadow_target_count"
    ) is None:
        return []
    cand = int(summary.get("flat_weak_range_shadow_target_count") or 0)
    block = int(summary.get("flat_weak_range_shadow_block_count") or 0)
    keep = int(summary.get("flat_weak_range_shadow_kept_count") or 0)
    bw = int(summary.get("flat_weak_range_shadow_blocked_winners") or 0)
    bl = int(summary.get("flat_weak_range_shadow_blocked_losers") or 0)
    completed = bw + bl
    if completed == 0 and summary.get("flat_weak_range_shadow_completed") is not None:
        completed = int(summary.get("flat_weak_range_shadow_completed") or 0)
    return [
        "--- Flat Weak + Range ---",
        "",
        f"candidates: {cand}",
        f"would block: {block}",
        f"would keep: {keep}",
        "",
        f"completed: {completed}",
        f"blocked losers: {bl}",
        f"blocked winners: {bw}",
        "",
        f"runtime PnL: {_yen(summary.get('flat_weak_range_shadow_actual_total_pnl_yen_100'))}",
        f"shadow PnL: {_yen(summary.get('flat_weak_range_shadow_total_pnl_yen_100'))}",
        f"delta: {_yen(summary.get('flat_weak_range_shadow_delta_yen'))}",
        "",
        "status: collecting" if cand else "status: no comparable candidates",
    ]


def render_pullback_misread_section(summary: Mapping[str, Any], cfg: Any = None) -> list[str]:
    state = resolve_all_observer_states(summary, cfg)["pullback_misread"]
    local = dict(summary)
    hits = local.get("pullback_misread_guard_shadow_blocked_count")
    if hits is None:
        pb = local.get("pullback_misread_entry_guard_shadow")
        if isinstance(pb, Mapping):
            hits = pb.get("pullback_misread_guard_shadow_blocked_count")
            local = {**local, **pb}
    if state.get("enabled") is not True:
        return _section_for_observer_state(
            title="--- PullbackMisread ---",
            state=state,
            normal_lines=[],
        )
    if hits is None and local.get("pullback_misread_guard_shadow_delta_yen") is None:
        return []
    hits_i = int(hits or 0)
    actual = local.get("pullback_misread_guard_shadow_actual_total_pnl_yen_100")
    shadow = local.get("pullback_misread_guard_shadow_total_pnl_yen_100")
    delta = local.get("pullback_misread_guard_shadow_delta_yen")
    bl = int(local.get("pullback_misread_blocked_losers") or 0)
    bw = int(local.get("pullback_misread_blocked_winners") or 0)
    completed = int(local.get("pullback_misread_completed") or (bl + bw) or 0)
    return [
        "--- PullbackMisread ---",
        "",
        f"hits: {hits_i}",
        f"would block: {hits_i}",
        f"completed: {completed}",
        "",
        f"blocked losers: {bl}",
        f"blocked winners: {bw}",
        "",
        f"runtime PnL: {_yen(actual)}",
        f"shadow PnL: {_yen(shadow)}",
        f"delta: {_yen(delta)}",
        "",
        "AM / PM rule:",
        "none",
        "",
        "status: collecting",
    ]


def render_pullback_volume_section(summary: Mapping[str, Any], cfg: Any = None) -> list[str]:
    state = resolve_all_observer_states(summary, cfg)["pullback_volume"]
    block = summary.get("pullback_volume_forward")
    if state.get("enabled") is not True:
        return _section_for_observer_state(
            title="--- Pullback Volume Forward ---",
            state=state,
            normal_lines=[],
        )
    if not isinstance(block, Mapping):
        return []
    hits = int(block.get("hits") or block.get("total_pullback_hits") or 0)
    vh = block.get("volume_high") if isinstance(block.get("volume_high"), Mapping) else {}
    vm = block.get("volume_mid") if isinstance(block.get("volume_mid"), Mapping) else {}
    vl = block.get("volume_low") if isinstance(block.get("volume_low"), Mapping) else {}
    bv = block.get("board_volume") if isinstance(block.get("board_volume"), Mapping) else {}
    down = bv.get("board_down_vol_low") if isinstance(bv.get("board_down_vol_low"), Mapping) else {}
    cum = block.get("cumulative") if isinstance(block.get("cumulative"), Mapping) else {}
    gate = cum.get("sample_gate") if isinstance(cum.get("sample_gate"), Mapping) else {}
    return [
        "--- Pullback Volume Forward ---",
        "",
        f"hits: {hits}",
        "",
        "volume high:",
        f"n={int(block.get('volume_high_n') or vh.get('n') or 0)}",
        f"healthy: {int(round((vh.get('healthy_rate') or 0) * (vh.get('n') or 0))) if vh.get('healthy_rate') is not None else 'n/a'}",
        f"collapse: {int(round((vh.get('collapse_rate') or 0) * (vh.get('n') or 0))) if vh.get('collapse_rate') is not None else 'n/a'}",
        "",
        "volume mid:",
        f"n={int(block.get('volume_mid_n') or vm.get('n') or 0)}",
        "",
        "volume low:",
        f"n={int(block.get('volume_low_n') or vl.get('n') or 0)}",
        f"healthy: {int(round((vl.get('healthy_rate') or 0) * (vl.get('n') or 0))) if vl.get('healthy_rate') is not None else 'n/a'}",
        f"collapse: {int(round((vl.get('collapse_rate') or 0) * (vl.get('n') or 0))) if vl.get('collapse_rate') is not None else 'n/a'}",
        "",
        "board worsening x volume low:",
        f"n={int(down.get('n') or 0)}",
        "",
        "cumulative:",
        f"days: {gate.get('trading_days', cum.get('trading_days', 'n/a'))}",
        f"symbols: {gate.get('symbols', cum.get('symbols', 'n/a'))}",
        f"vol high: {gate.get('volume_high_n', block.get('volume_high_n', 0))} / 50",
        f"vol low: {gate.get('volume_low_n', block.get('volume_low_n', 0))} / 50",
        f"max sector: {_pct(gate.get('max_sector_share')) if gate.get('max_sector_share') is not None else 'n/a'}",
        "",
        "status: collecting",
    ]


def _as_int_opt(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def resolve_pullback_volume_counts(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve PV eligible/recorded without using PullbackMisread hits as denominator."""
    pv = summary.get("pullback_volume_forward") if isinstance(summary.get("pullback_volume_forward"), Mapping) else {}

    def _first_opt(*vals: Any) -> Optional[int]:
        for v in vals:
            if v is not None and v != "":
                return _as_int_opt(v)
        return None

    has_explicit_eligible = any(
        v is not None and v != ""
        for v in (
            summary.get("pullback_volume_eligible_count"),
            pv.get("pullback_volume_eligible_count"),
            pv.get("eligible_count"),
            pv.get("eligible"),
        )
    )
    eligible = (
        _first_opt(
            summary.get("pullback_volume_eligible_count"),
            pv.get("pullback_volume_eligible_count"),
            pv.get("eligible_count"),
            pv.get("eligible"),
        )
        if has_explicit_eligible
        else None
    )
    recorded = _first_opt(
        summary.get("pullback_volume_recorded_count"),
        pv.get("pullback_volume_recorded_count"),
        pv.get("recorded_count"),
        pv.get("recorded"),
        pv.get("rows"),
        pv.get("hits"),
        pv.get("total_pullback_hits"),
    )

    if eligible is None:
        ratio = "n/a"
        status = "n/a"
    elif eligible == 0:
        ratio = "0 / 0"
        status = "complete"
    else:
        rec = int(recorded or 0)
        ratio = f"{rec} / {eligible}"
        if rec < eligible:
            status = "incomplete"
        elif rec == eligible:
            status = "complete"
        else:
            status = "counter_mismatch"
    return {
        "eligible": eligible,
        "recorded": recorded,
        "ratio": ratio,
        "status": status,
        "has_explicit_eligible": has_explicit_eligible,
    }


def render_data_completeness_section(summary: Mapping[str, Any], cfg: Any = None) -> list[str]:
    states = resolve_all_observer_states(summary, cfg)
    official = int(
        summary.get("official_entry_count")
        or summary.get("entry_integrity_official_entry")
        or summary.get("accepted_count")
        or 0
    )
    ca = summary.get("cost_aware_entry_shadow") if isinstance(summary.get("cost_aware_entry_shadow"), Mapping) else {}
    ca_eval = int(ca.get("candidates") or ca.get("shadow_eligible") or summary.get("cost_aware_evaluated_count") or 0)
    if states["cost_aware_entry"]["enabled"] is True and ca_eval == 0 and official:
        ca_eval = official
    fwr_tag = int(
        summary.get("flat_weak_range_shadow_target_count")
        or summary.get("flat_weak_range_tagged")
        or 0
    )
    pb_hits = int(summary.get("pullback_misread_guard_shadow_blocked_count") or 0)
    pv = summary.get("pullback_volume_forward") if isinstance(summary.get("pullback_volume_forward"), Mapping) else {}
    pv_counts = resolve_pullback_volume_counts(summary)
    exit_join = int(summary.get("observer_exit_count") or summary.get("exit_join_count") or official)
    dups = int(summary.get("duplicate_records") or pv.get("duplicate_skipped") or 0)
    leak = int(summary.get("leak_invalid_days") or pv.get("future_leak_suspects") or 0)
    obs_err = int(summary.get("observer_errors") or 0)
    pv_status = str(pv_counts.get("status") or "n/a")
    any_mismatch = any(bool(states[k]["mismatch"]) for k in OBSERVER_KEYS)
    incomplete = bool(
        (states["pullback_volume"]["enabled"] is True and pv_status in ("incomplete", "counter_mismatch"))
        or dups > 0
        or leak > 0
        or obs_err > 0
        or any_mismatch
    )
    if any_mismatch:
        status_label = "CONFIG/DATA MISMATCH"
    elif states["pullback_volume"]["enabled"] is True and pv_status == "counter_mismatch":
        status_label = "COUNTER MISMATCH"
    elif incomplete:
        status_label = "INCOMPLETE"
    else:
        status_label = "COMPLETE"

    lines = [
        "--- Data Completeness ---",
        "",
        f"official entries: {official}",
        "",
    ]
    if states["cost_aware_entry"]["enabled"] is True:
        lines.extend(
            [
                "cost-aware evaluated:",
                f"{ca_eval} / {official}" if official else f"{ca_eval}",
                "",
            ]
        )
    elif states["cost_aware_entry"]["enabled"] is False:
        lines.extend(["cost-aware completeness:", "not applicable (observer OFF)", ""])

    if states["flat_weak_range"]["enabled"] is True:
        lines.extend(
            [
                "flat weak range tagged:",
                f"{fwr_tag} / {official}" if official else f"{fwr_tag}",
                "",
            ]
        )
    elif states["flat_weak_range"]["enabled"] is False:
        lines.extend(["flat weak range completeness:", "not applicable (observer OFF)", ""])

    if states["pullback_misread"]["enabled"] is True:
        lines.extend(["PullbackMisread hits:", f"{pb_hits}", ""])
    elif states["pullback_misread"]["enabled"] is False:
        lines.extend(["PullbackMisread completeness:", "not applicable (observer OFF)", ""])

    if states["pullback_volume"]["enabled"] is True:
        if pv_counts.get("eligible") is not None:
            lines.extend(
                [
                    "Pullback Volume eligible:",
                    f"{pv_counts['eligible']}",
                    "",
                    "Pullback Volume recorded:",
                    str(pv_counts["ratio"]),
                    "",
                ]
            )
        else:
            rec = pv_counts.get("recorded")
            lines.extend(
                [
                    "Pullback Volume records:",
                    f"{rec if rec is not None else 0}",
                    "",
                    "Pullback Volume completeness:",
                    "n/a",
                    "",
                ]
            )
    elif states["pullback_volume"]["enabled"] is False:
        lines.extend(
            [
                "Pullback Volume completeness:",
                "not applicable (observer OFF)",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "Pullback Volume completeness:",
                "n/a",
                "",
            ]
        )

    lines.extend(
        [
            "exit join:",
            f"{exit_join} / {official}" if official else f"{exit_join}",
            "",
            f"duplicate records: {dups}",
            f"leak invalid days: {leak}",
            "",
            f"status: {status_label}",
        ]
    )
    return lines


def build_shadow_summary_structured(
    summary: Mapping[str, Any],
    *,
    am_pm: str,
    cfg: Any = None,
) -> dict[str, Any]:
    """Shadow Portfolio Cleanup: Discord shows ≤3 PnL shadows only.

    E1_X5 / Flat Weak + Range / Board Dynamic Monitor.
    Logger / Mainline Component / Retired are omitted from Shadow Summary.
    Deprecated section renderers remain for reader compatibility when called directly.
    """
    from small_paper.shadow_registry import format_shadow_portfolio_startup_lines

    e1 = summary.get("e1_x5_forward_shadow") if isinstance(summary.get("e1_x5_forward_shadow"), Mapping) else {}
    e1_enabled = bool(summary.get("e1_x5_forward_shadow_enabled", e1.get("enabled")))
    e1_trades = int(summary.get("e1_x5_forward_shadow_trades") or e1.get("trades") or 0)
    e1_pnl = summary.get("e1_x5_forward_shadow_total_pnl_yen_100", e1.get("total_pnl_yen_100"))
    e1_pf = summary.get("e1_x5_forward_shadow_profit_factor_yen_100", e1.get("profit_factor_yen_100"))
    e1_open = int(summary.get("e1_x5_forward_shadow_open_positions") or e1.get("open_positions") or 0)

    fwr_lines = render_flat_weak_range_section(summary, cfg)
    # Board Dynamic: one-line monitor summary
    bd_exits = int(summary.get("board_dynamic_shadow_exit_count") or 0)
    bd_delta = summary.get("board_dynamic_shadow_total_delta_yen")
    bd_on = bool(summary.get("board_dynamic_shadow_enabled", True))
    bd_line = (
        f"Board Dynamic Monitor: {'ON' if bd_on else 'OFF'} "
        f"exits={bd_exits} delta={bd_delta if bd_delta is not None else 'n/a'}"
    )

    e1_block = [
        "--- E1_X5 ---",
        f"status: {'ON' if e1_enabled else 'OFF'}",
        f"trades: {e1_trades}",
        f"PnL: {e1_pnl if e1_pnl is not None else 'n/a'}",
        f"PF: {e1_pf if e1_pf is not None else 'n/a'}",
        "delta: n/a (independent CAP5)",
        f"open: {e1_open}",
        "gate progress: Forward 5 sessions / 30 trades",
    ]

    sections = {
        "portfolio": format_shadow_portfolio_startup_lines(),
        "e1_x5": e1_block,
        "flat_weak_range": fwr_lines,
        "board_dynamic": [bd_line],
        # deprecated (kept for callers; not emitted in discord_text)
        "observer_status": render_observer_status_section(summary, cfg),
        "cost_aware": [],
        "pullback_misread": [],
        "pullback_volume": [],
        "data_completeness": render_data_completeness_section(summary, cfg),
    }
    lines: list[str] = [f"[SHADOW SUMMARY - {am_pm.upper()}]", ""]
    for key in ("e1_x5", "flat_weak_range", "board_dynamic"):
        part = sections[key]
        if part:
            lines.extend(part)
            lines.append("")
    text = "\n".join(lines).rstrip() + "\n"
    return {
        "am_pm": am_pm,
        "sections": {k: "\n".join(v) if isinstance(v, list) else str(v) for k, v in sections.items()},
        "discord_text": text,
        "char_len": len(text),
        "discord_visible_count": 3,
    }


def split_discord_messages(text: str, *, limit: int = DISCORD_SOFT_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    # Prefer Actual vs Shadow split markers
    marker = "--- Observer Status ---"
    if marker in text:
        i = text.index(marker)
        return [text[:i].rstrip(), text[i:].rstrip()]
    # hard split
    return [text[:limit], text[limit: limit * 2]]


def resolve_entry_quantity(payload: Mapping[str, Any], config: Any = None) -> Optional[int]:
    """Resolve official ENTRY quantity. qty=0 is valid (not missing)."""
    src = dict(payload or {})
    for key in (
        "quantity",
        "qty",
        "order_quantity",
        "position_quantity",
        "shares",
        "paper_qty",
    ):
        if key not in src or src.get(key) is None or src.get(key) == "":
            continue
        try:
            return int(src[key])
        except (TypeError, ValueError):
            continue
    if config is not None:
        for key in ("paper_quantity", "quantity", "lot_size", "default_quantity"):
            val = None
            if isinstance(config, Mapping):
                val = config.get(key)
            elif hasattr(config, key):
                val = getattr(config, key)
            if val is None or val == "":
                continue
            try:
                return int(val)
            except (TypeError, ValueError):
                continue
        # Configured paper lot default when a config object is present
        try:
            from small_paper.live_order_dry_run_adapter import LOT_SIZE

            return int(LOT_SIZE)
        except Exception:
            pass
    return None


def render_entry_quantity_line(quantity: Optional[int]) -> str:
    if quantity is None:
        return "qty: n/a"
    return f"qty: {int(quantity)}"


def render_official_entry_lines(
    payload: Mapping[str, Any],
    *,
    config: Any = None,
    audit_missing: bool = True,
) -> list[str]:
    """Common official ENTRY text path (qty always present as value or n/a)."""
    qty = resolve_entry_quantity(payload, config)
    if qty is None and audit_missing:
        log.warning(
            "entry_quantity_missing symbol=%s position_id=%s",
            payload.get("symbol"),
            payload.get("position_id") or payload.get("observer_position_id"),
        )
    price = payload.get("entry_price") or payload.get("validated_entry_price") or payload.get("current_price")
    try:
        price_s = f"{int(round(float(price))):,}円" if price is not None else "n/a"
    except (TypeError, ValueError):
        price_s = "n/a"
    stage = str(payload.get("accept_stage") or payload.get("stage") or "official_entry")
    return [
        "[ENTRY]",
        str(payload.get("symbol") or ""),
        str(payload.get("entry_time") or payload.get("event_time") or ""),
        f"price: {price_s}",
        render_entry_quantity_line(qty),
        f"stage: {stage}",
    ]


def render_entry_aborted_lines(acc: Mapping[str, Any], *, reason: str, stage: str) -> list[str]:
    lines = [
        "[ENTRY ABORTED]",
        "",
        f"symbol: {acc.get('symbol')}",
        f"stage: {stage}",
        f"reason: {reason}",
        "official entry: NOT CREATED",
        "position registered: NO",
        "order adapter: NOT CALLED",
    ]
    # Optional reference qty — never implies official ENTRY
    qty = resolve_entry_quantity(acc, config=None)
    if qty is not None:
        lines.append(render_entry_quantity_line(qty))
    return lines


def extract_exit_forward_tags(context: Mapping[str, Any]) -> list[str]:
    """Only include tags when present (hit / evaluated). Never invent false."""
    tags: list[str] = []
    if context.get("cost_aware_shadow_rank") is not None:
        tags.append(f"cost_aware: rank={context.get('cost_aware_shadow_rank')}")
    elif context.get("cost_aware_lower_priority"):
        tags.append("cost_aware: lower_priority")
    if context.get("flat_weak_range_shadow_candidate") in (True, "true", "1", 1):
        if context.get("flat_weak_range_shadow_block") in (True, "true", "1", 1):
            tags.append("flat_weak_range: block")
        else:
            tags.append("flat_weak_range: keep")
    if context.get("pullback_misread_guard_shadow_blocked") in (True, "true", "1", 1) or context.get(
        "pullback_misread_dynamic40_guard_blocked"
    ) in (True, "true", "1", 1):
        tags.append("pullback_misread: hit")
    bucket = context.get("pullback_volume_bucket")
    if bucket and str(bucket) not in ("", "missing"):
        tags.append(f"pullback_volume: {bucket}")
    return tags


def render_canonical_integrity_lines(summary: Mapping[str, Any]) -> list[str]:
    ei = summary.get("entry_integrity") if isinstance(summary.get("entry_integrity"), Mapping) else {}
    delivery = summary.get("discord_delivery") if isinstance(summary.get("discord_delivery"), Mapping) else {}
    pipe = summary.get("evaluation_reachability_summary") if isinstance(
        summary.get("evaluation_reachability_summary"), Mapping
    ) else summary.get("pipeline") if isinstance(summary.get("pipeline"), Mapping) else {}
    lines = [
        "",
        "ENTRY integrity:",
        f"gate accepted: {ei.get('gate_accepted', summary.get('gate_accepted_count', 'n/a'))}",
        f"payload valid: {ei.get('payload_valid', summary.get('payload_valid_count', 'n/a'))}",
        f"registered: {ei.get('registered', summary.get('position_registered_count', 'n/a'))}",
        f"official entry: {ei.get('official_entry', summary.get('official_entry_count', 'n/a'))}",
        f"aborted: {ei.get('aborted', summary.get('entry_aborted_count', 0))}",
        f"ghost: {ei.get('ghost', summary.get('ghost_accept_count', 0))}",
        "",
        "Discord delivery:",
        f"entry delivered: {delivery.get('entry_delivered', delivery.get('delivered', 'n/a'))}",
        f"retry success: {delivery.get('retry_success', 0)}",
        f"failed: {delivery.get('failed', 0)}",
        f"unconfirmed: {delivery.get('unconfirmed', 0)}",
    ]
    if pipe:
        lines.extend(
            [
                "",
                "Pipeline:",
                f"active symbols: {pipe.get('active_symbols', pipe.get('watch_symbols', 'n/a'))}",
                f"evaluation attempted: {pipe.get('evaluation_attempted', pipe.get('evaluations', 'n/a'))}",
                f"pipeline integrity errors: {pipe.get('pipeline_integrity_errors', 0)}",
            ]
        )
    lines.extend(
        [
            "",
            "Runtime:",
            f"pipeline errors: {summary.get('pipeline_errors', 0)}",
            f"observer errors: {summary.get('observer_errors', 0)}",
        ]
    )
    return lines


def _ca_delta_yen(ca: Mapping[str, Any]) -> Any:
    if ca.get("shadow_pnl_yen_100") is not None and ca.get("runtime_pnl_yen_100") is not None:
        try:
            return float(ca["shadow_pnl_yen_100"]) - float(ca["runtime_pnl_yen_100"])
        except (TypeError, ValueError):
            pass
    for key in ("delta_yen", "pullback_misread_guard_shadow_delta_yen"):
        if ca.get(key) is not None:
            return ca.get(key)
    return None


def _as_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float_opt(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _yen_or_na(v: Any) -> str:
    if v is None:
        return "n/a"
    return _yen(v)


HIGHLIGHT_MAX_ITEMS = 3
HIGHLIGHT_MAX_LINES = 12


def _has_meaningful_text(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return any(isinstance(item, str) and item.strip() for item in value)
    if isinstance(value, Mapping):
        return any(_has_meaningful_text(v) for v in value.values())
    return False


def _highlight_item(title: str, body: str, *, score: float = 0.0, key: str = "") -> Optional[dict[str, Any]]:
    t = str(title or "").strip()
    b = str(body or "").strip()
    if not t or not b:
        return None
    return {"key": key, "title": t, "body": b, "score": float(score)}


def collect_data_warnings(
    summary: Mapping[str, Any],
    *,
    data_completeness: Optional[Mapping[str, Any]] = None,
    cfg: Any = None,
) -> list[str]:
    """Display-only data / observer warnings (not trading signals)."""
    warns: list[str] = []
    dc = data_completeness if isinstance(data_completeness, Mapping) else {}
    obs_err = _as_int(summary.get("observer_errors") or dc.get("observer_errors"))
    if obs_err > 0:
        warns.append(f"observer errors {obs_err}")
    dups = _as_int(summary.get("duplicate_records") or dc.get("duplicate_records"))
    if dups > 0:
        warns.append(f"duplicate records {dups}")
    leak = _as_int(summary.get("leak_invalid_days") or dc.get("leak_invalid_days"))
    if leak > 0:
        warns.append(f"leak invalid days {leak}")
    pv = summary.get("pullback_volume_forward")
    if not isinstance(pv, Mapping):
        pv = {}
    pv_dups = _as_int(pv.get("duplicate_skipped") or summary.get("pullback_volume_duplicate_records"))
    if pv_dups > 0:
        warns.append(f"duplicate pullback volume records {pv_dups}")

    states = resolve_all_observer_states(summary, cfg)
    for key in OBSERVER_KEYS:
        st = states[key]
        name = st["display_name"]
        if st["enabled"] is False and st["data_present"]:
            unit = "evaluations" if key == "cost_aware_entry" else "records" if key == "pullback_volume" else "observations"
            if key == "flat_weak_range":
                unit = "candidates"
            elif key == "pullback_misread":
                unit = "hits"
            warns.append(f"{name} is OFF but {st['data_count']} {unit} exist")
        elif st["enabled"] is None and st["data_present"]:
            warns.append(f"Observer status unresolved: {name}")
        elif st.get("config_summary_conflict"):
            warns.append(f"{name} config/summary enabled conflict")

    pv_on = states["pullback_volume"]["enabled"] is True
    pv_counts = resolve_pullback_volume_counts(summary)
    eligible = pv_counts.get("eligible")
    recorded = pv_counts.get("recorded")
    if pv_on and eligible is not None and recorded is not None:
        if int(recorded) != int(eligible):
            warns.append(f"Pullback Volume records {int(recorded)} / eligible {int(eligible)}")

    fwr_on = states["flat_weak_range"]["enabled"] is True
    fwr_block = _as_int(summary.get("flat_weak_range_shadow_block_count"))
    fwr_done = _as_int(
        summary.get("flat_weak_range_shadow_completed")
        or summary.get("flat_weak_range_shadow_exit_join_count")
    )
    fwr_miss = _as_int(summary.get("flat_weak_range_shadow_exit_join_miss_count"))
    exits = _as_int(summary.get("observer_exit_count") or summary.get("exit_count"))
    if fwr_on and fwr_block > 0 and fwr_done == 0 and exits > 0:
        warns.append("Flat Weak Range JOIN INCOMPLETE")
    elif fwr_on and fwr_miss > 0 and fwr_block > 0:
        warns.append(f"Flat Weak Range exit join miss {fwr_miss}")
    official = _as_int(summary.get("official_entry_count") or summary.get("accepted_count"))
    ca = summary.get("cost_aware_entry_shadow")
    if states["cost_aware_entry"]["enabled"] is True and isinstance(ca, Mapping) and official > 0:
        ca_eval = _as_int(ca.get("candidates") or ca.get("shadow_eligible"))
        if ca_eval > 0 and ca_eval < official * 0.5:
            warns.append(f"cost-aware evaluated {ca_eval} / official {official}")
    return warns


def build_cost_aware_daily_highlight(summary: Mapping[str, Any], cfg: Any = None) -> Optional[dict[str, Any]]:
    if resolve_observer_enabled("cost_aware_entry", cfg, summary) is not True:
        return None
    ca = summary.get("cost_aware_entry_shadow")
    if not isinstance(ca, Mapping):
        return None
    if ca.get("enabled") is False:
        return None
    cycles = _as_int(ca.get("selection_cycles"))
    completed = _as_int(ca.get("n_closed") or ca.get("virtual_outcomes_completed"))
    delta = _as_float_opt(_ca_delta_yen(ca))
    has_yen = ca.get("shadow_pnl_yen_100") is not None or ca.get("delta_yen") is not None
    stop_av = _as_int(ca.get("stop_risk_reject") or ca.get("stop_avoided"))
    win_miss = _as_int(ca.get("never_filled") or ca.get("winner_missed"))
    win_cap = _as_int(ca.get("later_fill") or ca.get("winner_captured"))
    diff = _as_int(ca.get("official_entry_mismatch") or ca.get("shadow_different_choice"))
    cand_n = _as_int(ca.get("candidates") or ca.get("shadow_eligible"))

    if cycles <= 0 and cand_n <= 0:
        body, score = "comparable groupなし", 0.5
    elif completed <= 0 and not has_yen:
        body, score = "completed 0 / collecting", 1.0 + diff * 0.5
    elif delta is None:
        body, score = "n/a / collecting", 1.0
    else:
        parts = [_yen(delta)]
        if delta < 0 and win_miss > 0:
            parts.append(f"winner missed {win_miss}件")
        elif stop_av > 0:
            parts.append(f"STOP回避 {stop_av}件")
        elif win_cap > 0:
            parts.append(f"winner captured {win_cap}件")
        elif diff > 0:
            parts.append(f"choice差 {diff}件")
        body = " / ".join(p for p in parts if _has_meaningful_text(p))
        score = abs(delta) / 1000.0 + stop_av * 3 + win_miss * 4 + win_cap * 2 + diff
    return _highlight_item("Cost-Aware:", body, score=score + 20.0, key="cost_aware")


def build_fwr_daily_highlight(summary: Mapping[str, Any], cfg: Any = None) -> Optional[dict[str, Any]]:
    """FWR Daily highlight with mandatory non-empty body, or None if no candidates."""
    if resolve_observer_enabled("flat_weak_range", cfg, summary) is not True:
        return None
    if summary.get("flat_weak_range_shadow_enabled") is False:
        return None
    candidates = _as_int(summary.get("flat_weak_range_shadow_target_count"))
    would_block = _as_int(summary.get("flat_weak_range_shadow_block_count"))
    completed = _as_int(
        summary.get("flat_weak_range_shadow_completed")
        or summary.get("flat_weak_range_shadow_exit_join_count")
    )
    blocked_losers = _as_int(summary.get("flat_weak_range_shadow_blocked_losers"))
    blocked_winners = _as_int(summary.get("flat_weak_range_shadow_blocked_winners"))
    delta = _as_float_opt(summary.get("flat_weak_range_shadow_delta_yen"))
    exits = _as_int(summary.get("observer_exit_count") or summary.get("exit_count"))
    join_incomplete = bool(
        summary.get("join_incomplete")
        or (would_block > 0 and completed == 0 and exits > 0)
        or (
            _as_int(summary.get("flat_weak_range_shadow_exit_join_miss_count")) > 0
            and would_block > 0
            and completed == 0
        )
    )

    # No observation today → do not emit title-only row
    if (
        summary.get("flat_weak_range_shadow_enabled") is None
        and summary.get("flat_weak_range_shadow_target_count") is None
    ):
        return None
    if candidates <= 0 and would_block <= 0 and completed <= 0 and not join_incomplete:
        return None

    title = "Flat Weak + Range:"
    if join_incomplete:
        return _highlight_item(title, "JOIN INCOMPLETE / 要確認", score=1000.0 + 15.0, key="flat_weak_range")

    if completed > 0 and delta is not None:
        if delta >= 0:
            body = f"{_yen(delta)} / loser回避 {blocked_losers}件"
        else:
            body = f"{_yen(delta)} / winner除外 {blocked_winners}件"
        score = abs(delta) / 1000.0 + blocked_losers * 3 + blocked_winners * 4 + 15.0
        return _highlight_item(title, body, score=score, key="flat_weak_range")

    if completed > 0:
        body = f"completed {completed}件 / loser {blocked_losers} / winner {blocked_winners}"
        return _highlight_item(title, body, score=5.0 + completed + 15.0, key="flat_weak_range")

    if would_block > 0:
        return _highlight_item(
            title,
            f"would block {would_block}件 / outcome pending",
            score=2.0 + would_block + 15.0,
            key="flat_weak_range",
        )

    if candidates > 0:
        return _highlight_item(title, "collecting", score=1.0 + 15.0, key="flat_weak_range")

    return None


def build_pullback_volume_daily_highlight(summary: Mapping[str, Any], cfg: Any = None) -> Optional[dict[str, Any]]:
    if resolve_observer_enabled("pullback_volume", cfg, summary) is not True:
        return None
    pv = summary.get("pullback_volume_forward")
    if not isinstance(pv, Mapping):
        return None
    hits = _as_int(pv.get("hits") or pv.get("total_pullback_hits"))
    if pv.get("enabled") is False:
        return None
    high_n = _as_int(pv.get("volume_high_n") or (pv.get("volume_high") or {}).get("n"))
    low_n = _as_int(pv.get("volume_low_n") or (pv.get("volume_low") or {}).get("n"))
    if hits <= 0 and high_n <= 0 and low_n <= 0:
        return None
    vh = pv.get("volume_high") if isinstance(pv.get("volume_high"), Mapping) else {}
    vl = pv.get("volume_low") if isinstance(pv.get("volume_low"), Mapping) else {}
    h_rate = _as_float_opt(vh.get("healthy_rate"))
    c_rate = _as_float_opt(vl.get("collapse_rate"))
    gate: Mapping[str, Any] = {}
    cum = pv.get("cumulative") if isinstance(pv.get("cumulative"), Mapping) else {}
    if isinstance(cum.get("sample_gate"), Mapping):
        gate = cum["sample_gate"]  # type: ignore[assignment]
    complete = bool(gate.get("forward_sample_gate_pass"))

    if complete:
        body, score = "forward sample complete / review ready", 50.0
    elif low_n > 0 and c_rate is not None and c_rate >= 0.5:
        collapse_n = int(round(c_rate * low_n))
        body, score = f"Low {low_n}件中 collapse {collapse_n}件", 10.0 + collapse_n * 3 + low_n
    elif high_n > 0 and h_rate is not None and h_rate >= 0.5:
        healthy_n = int(round(h_rate * high_n))
        body, score = f"High {high_n}件中 healthy {healthy_n}件", 8.0 + healthy_n * 2 + high_n
    else:
        body, score = f"Low {low_n}件 / High {high_n}件 / collecting", 3.0 + low_n + high_n * 0.5
    return _highlight_item("Pullback Volume:", body, score=score + 10.0, key="pullback_volume")


def build_pullback_misread_daily_highlight(summary: Mapping[str, Any], cfg: Any = None) -> Optional[dict[str, Any]]:
    if resolve_observer_enabled("pullback_misread", cfg, summary) is not True:
        return None
    hits = summary.get("pullback_misread_guard_shadow_blocked_count")
    if hits is None and summary.get("pullback_misread_guard_shadow_delta_yen") is None:
        return None
    delta = _as_float_opt(summary.get("pullback_misread_guard_shadow_delta_yen"))
    bl = _as_int(summary.get("pullback_misread_blocked_losers"))
    bw = _as_int(summary.get("pullback_misread_blocked_winners"))
    if bl == 0 and bw == 0:
        skipped = _as_float_opt(summary.get("pullback_misread_guard_shadow_skipped_trade_pnl_actual"))
        if skipped is not None and skipped < 0:
            bl = max(1, _as_int(hits) // 2) if hits else 0
        elif skipped is not None and skipped > 0:
            bw = max(1, _as_int(hits) // 2) if hits else 0
    if delta is None and bl == 0 and bw == 0 and _as_int(hits) <= 0:
        return None
    if delta is None:
        body, score = "n/a / collecting", 1.0
    else:
        parts = [_yen(delta)]
        if delta < 0 and bw > 0:
            parts.append(f"winner除外 {bw}件")
        elif bl > 0:
            parts.append(f"loser回避 {bl}件")
        elif bw > 0:
            parts.append(f"winner除外 {bw}件")
        body = " / ".join(p for p in parts if _has_meaningful_text(p))
        score = (abs(delta) / 1000.0 + bl * 2 + bw * 3) * 0.85
    return _highlight_item("PullbackMisread:", body, score=score, key="pullback_misread")


# Backward-compatible wrappers used by older tests / callers
def render_research_highlight_cost_aware(summary: Mapping[str, Any]) -> tuple[list[str], float]:
    item = build_cost_aware_daily_highlight(summary)
    if not item:
        return [], 0.0
    return [item["title"], item["body"]], float(item["score"])


def render_research_highlight_flat_weak(summary: Mapping[str, Any]) -> tuple[list[str], float]:
    item = build_fwr_daily_highlight(summary)
    if not item:
        return [], 0.0
    return [item["title"], item["body"]], float(item["score"])


def render_research_highlight_pullback_volume(summary: Mapping[str, Any]) -> tuple[list[str], float]:
    item = build_pullback_volume_daily_highlight(summary)
    if not item:
        return [], 0.0
    return [item["title"], item["body"]], float(item["score"])


def render_research_highlight_pullback_misread(summary: Mapping[str, Any]) -> tuple[list[str], float]:
    item = build_pullback_misread_daily_highlight(summary)
    if not item:
        return [], 0.0
    return [item["title"], item["body"]], float(item["score"])


def rank_research_highlights(
    items: Sequence[Any],
    *,
    max_items: int = HIGHLIGHT_MAX_ITEMS,
) -> list[Any]:
    """Display-only ranking after empty-candidate filter. Does not affect trading."""
    valid: list[Any] = []
    for it in items:
        if isinstance(it, Mapping):
            if _has_meaningful_text(it.get("title")) and _has_meaningful_text(it.get("body")):
                valid.append(it)
            continue
        # legacy tuple (key, lines, score)
        if isinstance(it, tuple) and len(it) >= 3:
            lines = it[1]
            if (
                isinstance(lines, (list, tuple))
                and len(lines) >= 2
                and _has_meaningful_text(lines[0])
                and _has_meaningful_text(lines[1])
            ):
                valid.append(it)
    ranked = sorted(
        valid,
        key=lambda x: float(x["score"]) if isinstance(x, Mapping) else float(x[2]),
        reverse=True,
    )
    return list(ranked[: max(0, max_items)])


def render_research_highlight_lines(selected_items: Sequence[Mapping[str, Any]]) -> list[str]:
    """Final render guard: title+body both required; no trailing blank spam."""
    lines: list[str] = []
    for item in selected_items:
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "").strip()
        if not title or not body:
            continue
        lines.extend([title, body, ""])
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def build_daily_research_highlights(
    observer_summary: Mapping[str, Any],
    data_completeness: Optional[Mapping[str, Any]] = None,
    *,
    max_items: int = HIGHLIGHT_MAX_ITEMS,
    max_lines: int = HIGHLIGHT_MAX_LINES,
) -> list[str]:
    """Build ≤3 short Daily research highlight lines (fail-open caller responsibility)."""
    try:
        return _build_daily_research_highlights_inner(
            observer_summary,
            data_completeness,
            max_items=max_items,
            max_lines=max_lines,
        )
    except Exception:
        return [
            "=== TODAY'S RESEARCH ===",
            "research highlight unavailable",
        ]


def _build_daily_research_highlights_inner(
    observer_summary: Mapping[str, Any],
    data_completeness: Optional[Mapping[str, Any]],
    *,
    max_items: int,
    max_lines: int,
) -> list[str]:
    summary = dict(observer_summary)
    warns = [w for w in collect_data_warnings(summary, data_completeness=data_completeness) if _has_meaningful_text(w)]

    raw_items: list[dict[str, Any]] = []
    for builder in (
        build_cost_aware_daily_highlight,
        build_fwr_daily_highlight,
        build_pullback_volume_daily_highlight,
        build_pullback_misread_daily_highlight,
    ):
        item = builder(summary)
        # Exclude empty / title-only before ranking (do not consume top-3 slots)
        if item and _has_meaningful_text(item.get("title")) and _has_meaningful_text(item.get("body")):
            raw_items.append(item)

    ranked = rank_research_highlights(raw_items, max_items=max_items)
    out: list[str] = ["=== TODAY'S RESEARCH ===", ""]
    if warns:
        warn_body = "; ".join(warns[:2]).strip()
        if _has_meaningful_text(warn_body):
            out.append("DATA WARNING:")
            out.append(warn_body)
            out.append("")
    out.extend(render_research_highlight_lines(ranked))
    while out and out[-1] == "":
        out.pop()
    if len(out) > max_lines:
        out = out[:max_lines]
        # avoid cutting mid-title without body
        if out and out[-1].endswith(":") and out[-1] != "=== TODAY'S RESEARCH ===":
            out.pop()
        while out and out[-1] == "":
            out.pop()
    return out


def render_daily_short_lines(summary: Mapping[str, Any], *, trading_date: str) -> list[str]:
    can = summary.get("canonical_summary") if isinstance(summary.get("canonical_summary"), Mapping) else summary
    ca = summary.get("cost_aware_entry_shadow") if isinstance(summary.get("cost_aware_entry_shadow"), Mapping) else {}
    pv = summary.get("pullback_volume_forward") if isinstance(summary.get("pullback_volume_forward"), Mapping) else {}
    delivery = summary.get("discord_delivery") if isinstance(summary.get("discord_delivery"), Mapping) else {}
    highlights = build_daily_research_highlights(summary)
    lines = [f"[TRADEBOT DAILY - {trading_date}]", ""]
    lines.extend(highlights)
    lines.extend(
        [
            "",
            "Actual:",
            f"trades: {can.get('trade_count', can.get('trades', 'n/a'))}",
            f"PnL: {_yen(can.get('total_pnl_yen_100'))}",
            f"PF: {can.get('profit_factor_yen_100', can.get('profit_factor', 'n/a'))}",
            f"win rate: {_pct(can.get('win_rate_yen_100') or can.get('win_rate'))}",
            "",
            "Forward:",
            f"Cost-Aware delta: {_yen_or_na(_ca_delta_yen(ca))}",
            f"Flat Weak Range delta: {_yen_or_na(summary.get('flat_weak_range_shadow_delta_yen'))}",
            f"PullbackMisread delta: {_yen_or_na(summary.get('pullback_misread_guard_shadow_delta_yen'))}",
            "",
            "Pullback Volume:",
            f"high {pv.get('volume_high_n', 0)} / low {pv.get('volume_low_n', 0)}",
            "",
            "Delivery:",
            f"failed: {delivery.get('failed', 0)}",
            f"unconfirmed: {delivery.get('unconfirmed', 0)}",
            "",
            "status:",
            "PAPER COMPLETE",
            "FORWARD COLLECTING",
        ]
    )
    return lines


def write_session_discord_report(
    out_dir: Path,
    *,
    runtime: Mapping[str, Any],
    canonical: Mapping[str, Any],
    shadow: Mapping[str, Any],
    delivery: Optional[Mapping[str, Any]] = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "runtime": dict(runtime),
        "canonical": dict(canonical),
        "observers": shadow.get("sections") if isinstance(shadow.get("sections"), Mapping) else shadow,
        "delivery": dict(delivery or {}),
        "discord_render": {
            "startup": "\n".join(render_paper_start_lines(runtime)),
            "shadow": shadow.get("discord_text"),
        },
    }
    path = out_dir / "report.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md = out_dir / "report.md"
    md.write_text(
        "# Discord Current System Report\n\n"
        + str(payload["discord_render"].get("startup") or "")
        + "\n\n---\n\n"
        + str(payload["discord_render"].get("shadow") or ""),
        encoding="utf-8",
    )
    return path
