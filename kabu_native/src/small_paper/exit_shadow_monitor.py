"""
Phase563: EXIT shadow daily monitor (T3 primary, T2 secondary).

Observer-only counterfactual trailing replay. Does not change actual EXIT policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from replay.pnl_yen import compute_pnl_yen_100
from small_paper.board_dynamic_trailing_shadow import (
    board_tier_from_percentile,
    simulate_board_dynamic_shadow_exit,
    trailing_params_for_board_tier,
)

PHASE563_VERDICT = "phase563_shadow_exit_daily_monitor_pilot_ready"

EARLY_RULE_MFE = 1.0
EARLY_RULE_MAX_PNL = 0.4
MFE1_THRESHOLD = 1.0

EXIT_EFFICIENCY_FIELD_KEYS = (
    "exit_mfe_capture_ratio",
    "exit_opportunity_loss_avg",
    "exit_early_profit_take_count",
    "exit_trailing_exit_count",
    "exit_stop_hit_after_mfe1_count",
    "exit_overlap_replaced_after_mfe1_count",
    "exit_board_high_trailing_pnl",
    "exit_board_low_trailing_pnl",
)

SHADOW_EXIT_FIELD_KEYS = (
    "shadow_exit_t2_pnl",
    "shadow_exit_t3_pnl",
    "shadow_exit_t2_delta",
    "shadow_exit_t3_delta",
    "shadow_exit_t2_worse_profit_day",
    "shadow_exit_t3_worse_loss_day",
)

SUMMARY_FIELD_KEYS = (
    "exit_shadow_monitor_enabled",
    "exit_shadow_monitor_t2_enabled",
    "exit_shadow_monitor_t3_enabled",
    *EXIT_EFFICIENCY_FIELD_KEYS,
    *SHADOW_EXIT_FIELD_KEYS,
    "exit_shadow_monitor_status",
)

PER_TRADE_FIELD_KEYS = (
    "exit_shadow_t2_pnl_yen_100",
    "exit_shadow_t3_pnl_yen_100",
)


def _float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _trailing_params_t2(imb_pct: Optional[float]) -> tuple[float, float, str]:
    act, gb, tier = trailing_params_for_board_tier(imb_pct)
    return max(0.1, act - 0.2), min(max(gb - 0.10, 0.05), 0.95), tier


def _trailing_params_t3(imb_pct: Optional[float]) -> tuple[float, float, str]:
    tier = board_tier_from_percentile(imb_pct)
    if tier == "board_high":
        return 1.2, 0.70, tier
    return trailing_params_for_board_tier(imb_pct)


def _normalize_exit_reason(reason: str) -> str:
    r = str(reason or "").strip().lower()
    if r in ("trailing_mfe_exit", "trailing_mfe"):
        return "trailing_mfe"
    if r == "overlap_replaced_review":
        return "overlap_replaced"
    return r


def _is_early_profit_take(mfe_pct: float, realized_pct: float) -> bool:
    return mfe_pct >= EARLY_RULE_MFE and realized_pct < EARLY_RULE_MAX_PNL


def _empty_summary(*, enabled: bool = False, t2: bool = False, t3: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "exit_shadow_monitor_enabled": enabled,
        "exit_shadow_monitor_t2_enabled": t2,
        "exit_shadow_monitor_t3_enabled": t3,
        "exit_shadow_monitor_status": "disabled" if not enabled else "ok",
    }
    for key in EXIT_EFFICIENCY_FIELD_KEYS:
        if key.endswith("_count"):
            out[key] = 0
        elif key.endswith("_pnl"):
            out[key] = 0.0
        else:
            out[key] = 0.0
    for key in SHADOW_EXIT_FIELD_KEYS:
        if key.endswith("_worse_profit_day") or key.endswith("_worse_loss_day"):
            out[key] = False
        else:
            out[key] = 0.0
    return out


@dataclass(frozen=True)
class ExitShadowMonitorConfig:
    enabled: bool = False
    t2_enabled: bool = True
    t3_enabled: bool = True


def config_from_pilot(config: Any) -> ExitShadowMonitorConfig:
    enabled = bool(getattr(config, "exit_shadow_monitor_enabled", False))
    return ExitShadowMonitorConfig(
        enabled=enabled,
        t2_enabled=bool(getattr(config, "exit_shadow_monitor_t2_enabled", True)),
        t3_enabled=bool(getattr(config, "exit_shadow_monitor_t3_enabled", True)),
    )


def enrich_exit_shadow_monitor_fields(
    *,
    rich_ticks: Sequence[Mapping[str, Any]],
    entry_price: float,
    hard_stop_pct: float,
    entry_imbalance_percentile: Optional[float],
    actual_exit_time: float,
    actual_exit_price: float,
    actual_pnl_pct: float,
    monitor: ExitShadowMonitorConfig,
) -> dict[str, Any]:
    """Per-trade shadow fields appended to observer_exit context (logging only)."""
    if not monitor.enabled or not rich_ticks or entry_price <= 0:
        return {}
    out: dict[str, Any] = {}
    cutoff = actual_exit_time if actual_exit_time > 0 else None
    if monitor.t3_enabled:
        act, gb, tier = _trailing_params_t3(entry_imbalance_percentile)
        t3 = simulate_board_dynamic_shadow_exit(
            rich_ticks,
            entry_price=entry_price,
            hard_stop_pct=hard_stop_pct,
            entry_imbalance_percentile=entry_imbalance_percentile,
            cutoff_ts=cutoff,
            activate_pct=act,
            giveback_frac=gb,
            tier_label=tier,
        )
        out["exit_shadow_t3_pnl_yen_100"] = t3.get("shadow_pnl_yen_100")
        out["exit_shadow_t3_exit_reason"] = t3.get("shadow_exit_reason")
    if monitor.t2_enabled:
        act, gb, tier = _trailing_params_t2(entry_imbalance_percentile)
        t2 = simulate_board_dynamic_shadow_exit(
            rich_ticks,
            entry_price=entry_price,
            hard_stop_pct=hard_stop_pct,
            entry_imbalance_percentile=entry_imbalance_percentile,
            cutoff_ts=cutoff,
            activate_pct=act,
            giveback_frac=gb,
            tier_label=tier,
        )
        out["exit_shadow_t2_pnl_yen_100"] = t2.get("shadow_pnl_yen_100")
        out["exit_shadow_t2_exit_reason"] = t2.get("shadow_exit_reason")
    return out


def _observer_exits(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(e)
        for e in events
        if e.get("event_type") == "observer_exit" and e.get("pnl_pct") is not None
    ]


def finalize_session_exit_shadow_monitor(
    events: Sequence[Mapping[str, Any]],
    *,
    monitor: ExitShadowMonitorConfig,
) -> dict[str, Any]:
    """Aggregate session EXIT efficiency + shadow monitor fields for Daily Summary."""
    if not monitor.enabled:
        return _empty_summary(enabled=False)

    exits = _observer_exits(events)
    if not exits:
        return _empty_summary(enabled=True, t2=monitor.t2_enabled, t3=monitor.t3_enabled)

    actual_total = 0.0
    t2_total = 0.0
    t3_total = 0.0
    capture_ratios: list[float] = []
    opp_losses: list[float] = []
    early_count = 0
    trailing_count = 0
    stop_after_mfe1 = 0
    overlap_after_mfe1 = 0
    board_high_trailing_pnl = 0.0
    board_low_trailing_pnl = 0.0
    t2_replay_count = 0
    t3_replay_count = 0

    for row in exits:
        pnl_pct = _float(row.get("pnl_pct")) or 0.0
        mfe = _float(row.get("peak_mfe_pct")) or _float(row.get("rolling_mfe_pct")) or 0.0
        entry_px = _float(row.get("entry_price")) or 0.0
        exit_px = _float(row.get("exit_price")) or 0.0
        if entry_px > 0 and exit_px > 0:
            actual_yen = round(compute_pnl_yen_100(entry_px, exit_px), 2)
        else:
            actual_yen = round(entry_px * 100.0 * pnl_pct / 100.0, 2) if entry_px > 0 else 0.0
        actual_total += actual_yen

        if mfe > 0:
            capture_ratios.append(round(pnl_pct / mfe, 4))
            opp_losses.append(round(max(0.0, mfe - pnl_pct), 4))
        if _is_early_profit_take(mfe, pnl_pct):
            early_count += 1

        reason = _normalize_exit_reason(str(row.get("exit_reason") or ""))
        if reason == "trailing_mfe":
            trailing_count += 1
            tier = board_tier_from_percentile(_float(row.get("entry_imbalance_percentile")))
            if tier == "board_high":
                board_high_trailing_pnl += actual_yen
            else:
                board_low_trailing_pnl += actual_yen
        if reason == "stop_hit" and mfe >= MFE1_THRESHOLD:
            stop_after_mfe1 += 1
        if reason in ("overlap_replaced", "overlap_replaced_review") and mfe >= MFE1_THRESHOLD:
            overlap_after_mfe1 += 1

        if monitor.t2_enabled:
            t2_yen = _float(row.get("exit_shadow_t2_pnl_yen_100"))
            if t2_yen is not None:
                t2_total += t2_yen
                t2_replay_count += 1
        if monitor.t3_enabled:
            t3_yen = _float(row.get("exit_shadow_t3_pnl_yen_100"))
            if t3_yen is not None:
                t3_total += t3_yen
                t3_replay_count += 1

    t2_delta = round(t2_total - actual_total, 2) if monitor.t2_enabled and t2_replay_count else 0.0
    t3_delta = round(t3_total - actual_total, 2) if monitor.t3_enabled and t3_replay_count else 0.0
    is_profit_day = actual_total > 0
    is_loss_day = actual_total < 0

    return {
        "exit_shadow_monitor_enabled": True,
        "exit_shadow_monitor_t2_enabled": monitor.t2_enabled,
        "exit_shadow_monitor_t3_enabled": monitor.t3_enabled,
        "exit_mfe_capture_ratio": round(sum(capture_ratios) / len(capture_ratios), 4) if capture_ratios else 0.0,
        "exit_opportunity_loss_avg": round(sum(opp_losses) / len(opp_losses), 4) if opp_losses else 0.0,
        "exit_early_profit_take_count": early_count,
        "exit_trailing_exit_count": trailing_count,
        "exit_stop_hit_after_mfe1_count": stop_after_mfe1,
        "exit_overlap_replaced_after_mfe1_count": overlap_after_mfe1,
        "exit_board_high_trailing_pnl": round(board_high_trailing_pnl, 2),
        "exit_board_low_trailing_pnl": round(board_low_trailing_pnl, 2),
        "shadow_exit_t2_pnl": round(t2_total, 2),
        "shadow_exit_t3_pnl": round(t3_total, 2),
        "shadow_exit_t2_delta": t2_delta,
        "shadow_exit_t3_delta": t3_delta,
        "shadow_exit_t2_worse_profit_day": bool(is_profit_day and t2_delta < 0),
        "shadow_exit_t3_worse_loss_day": bool(is_loss_day and t3_delta < 0),
        "exit_shadow_monitor_status": "ok",
        "exit_shadow_monitor_trade_count": len(exits),
        "exit_shadow_monitor_t2_replay_count": t2_replay_count,
        "exit_shadow_monitor_t3_replay_count": t3_replay_count,
    }


def finalize_session_exit_shadow_monitor_safe(
    events: Sequence[Mapping[str, Any]],
    *,
    monitor: ExitShadowMonitorConfig,
) -> dict[str, Any]:
    try:
        return finalize_session_exit_shadow_monitor(events, monitor=monitor)
    except Exception as exc:
        base = _empty_summary(
            enabled=monitor.enabled,
            t2=monitor.t2_enabled,
            t3=monitor.t3_enabled,
        )
        base["exit_shadow_monitor_status"] = "warning"
        base["exit_shadow_monitor_warning"] = str(exc)
        return base


def format_exit_shadow_monitor_discord_lines(summary: Mapping[str, Any]) -> list[str]:
    """Compact EXIT Monitor block for Daily Summary Discord."""
    if not summary.get("exit_shadow_monitor_enabled"):
        return []
    capture = summary.get("exit_mfe_capture_ratio")
    opp = summary.get("exit_opportunity_loss_avg")
    early = summary.get("exit_early_profit_take_count")
    lines = [
        "EXIT Monitor:",
        f"capture={capture} opp_loss={opp}% early={early}",
    ]
    if summary.get("exit_shadow_monitor_t3_enabled"):
        lines.append(
            "T3 shadow: "
            f"{summary.get('shadow_exit_t3_pnl')}円 "
            f"delta={summary.get('shadow_exit_t3_delta')}円"
        )
    if summary.get("exit_shadow_monitor_t2_enabled"):
        warn = ""
        if summary.get("shadow_exit_t2_worse_profit_day"):
            warn = " warn_profit_day=true"
        lines.append(
            "T2 shadow: "
            f"{summary.get('shadow_exit_t2_pnl')}円 "
            f"delta={summary.get('shadow_exit_t2_delta')}円{warn}"
        )
    if summary.get("shadow_exit_t3_worse_loss_day"):
        lines.append("T3 warn_loss_day=true")
    status = summary.get("exit_shadow_monitor_status")
    if status and status != "ok":
        lines.append(f"exit_shadow_monitor_status={status}")
    return lines
