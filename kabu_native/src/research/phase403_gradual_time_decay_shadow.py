"""
Phase403: Gradual time-decay MFE shadow exit replay.

Linear / exponential activation decay vs Phase402 step decay.
Research / shadow only — no Runtime / YAML / Entry / Exit changes.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate
from research.phase382_capital_constrained_backtest import _parse_ts, _write_csv
from research.phase400_holding_time_audit import enrich_trade, load_phase399_trades
from research.phase402_time_decay_exit_shadow import (
    BAD_LONG_HOLD_SYMBOLS,
    GOOD_LONG_HOLD_SYMBOLS,
    HARD_STOP_PCT,
    _max_drawdown_yen,
    _normalize_shadow_exit,
    _prepare_trade_context,
    _saved_lost_yen,
)
from small_paper.board_dynamic_trailing_shadow import trailing_params_for_board_tier

JST = ZoneInfo("Asia/Tokyo")
PERIOD_START = "20260529"
PERIOD_END = "20260615"

POLICY_BASELINE = "baseline_phase399"
POLICY_LINEAR = "linear_decay"
POLICY_LINEAR_SLOW = "linear_decay_slow"
POLICY_LINEAR_FAST = "linear_decay_fast"
POLICY_EXP = "exp_decay"

DECAY_START_SEC = (600, 900, 1200)
INITIAL_MFE_PCT = (0.6, 0.8, 1.0)
FLOOR_MFE_PCT = (0.2, 0.3, 0.4)
LINEAR_DECAY_PER_MIN = (0.02, 0.03, 0.05)
EXP_DECAY_LAMBDA = (0.01, 0.02, 0.03)

SLOW_DECAY_PER_MIN = 0.02
FAST_DECAY_PER_MIN = 0.05

PHASE402_BEST = {
    "policy_id": "time_decay_mfe",
    "time_threshold_sec": 900.0,
    "mfe_activation_after_time": 0.3,
    "net_delta_yen": 204112.4,
    "profit_factor": 1.2541,
    "long_hold_loser_delta": -1,
    "symbol_3905_damage_yen": -21999.71,
    "symbol_4062_damage_yen": -7500.78,
    "symbol_4078_rescue_yen": 10999.84,
    "good_long_hold_damage_yen": -29500.62,
}

GRID_FIELDS = [
    "policy_id",
    "decay_start_sec",
    "initial_mfe_pct",
    "floor_mfe_pct",
    "linear_decay_per_min",
    "exp_decay_lambda",
    "total_pnl_yen_100",
    "profit_factor",
    "trade_count",
    "win_rate",
    "max_drawdown_yen_100",
    "long_hold_loser_count",
    "saved_loss_yen",
    "lost_upside_yen",
    "net_delta_yen",
    "affected_trade_count",
    "long_hold_loser_delta",
    "symbol_3905_damage_yen",
    "symbol_4062_damage_yen",
    "symbol_4078_rescue_yen",
    "good_long_hold_damage_yen",
    "bad_long_hold_rescue_yen",
    "better_than_phase402_pnl",
    "less_damage_than_phase402_3905",
    "less_damage_than_phase402_4062",
    "adopt_candidate",
]

TRADE_FIELDS = [
    "policy_id",
    "decay_start_sec",
    "initial_mfe_pct",
    "floor_mfe_pct",
    "linear_decay_per_min",
    "exp_decay_lambda",
    "day",
    "session",
    "symbol",
    "entry_time",
    "exit_time",
    "baseline_exit_time",
    "hold_sec",
    "baseline_pnl_yen_100",
    "shadow_pnl_yen_100",
    "delta_yen",
    "baseline_exit_reason",
    "shadow_exit_reason",
    "is_long_hold_loser",
    "is_good_long_hold",
    "is_bad_long_hold",
    "focus_symbol",
]


@dataclass(frozen=True)
class GradualPolicySpec:
    policy_id: str
    decay_start_sec: float
    initial_mfe_pct: float
    floor_mfe_pct: float
    linear_decay_per_min: Optional[float] = None
    exp_decay_lambda: Optional[float] = None

    @property
    def grid_key(self) -> str:
        return (
            f"{self.policy_id}|{self.decay_start_sec}|{self.initial_mfe_pct}|"
            f"{self.floor_mfe_pct}|{self.linear_decay_per_min}|{self.exp_decay_lambda}"
        )


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _pnl_pct(entry: float, px: float) -> float:
    if entry <= 0:
        return 0.0
    return round((px - entry) / entry * 100.0, 4)


def iter_policy_grid() -> list[GradualPolicySpec]:
    specs: list[GradualPolicySpec] = []
    for start in DECAY_START_SEC:
        for initial in INITIAL_MFE_PCT:
            for floor in FLOOR_MFE_PCT:
                if floor > initial:
                    continue
                for rate in LINEAR_DECAY_PER_MIN:
                    specs.append(
                        GradualPolicySpec(
                            POLICY_LINEAR,
                            float(start),
                            float(initial),
                            float(floor),
                            linear_decay_per_min=float(rate),
                        )
                    )
    for start in DECAY_START_SEC:
        for initial in INITIAL_MFE_PCT:
            for floor in FLOOR_MFE_PCT:
                if floor > initial:
                    continue
                specs.append(
                    GradualPolicySpec(
                        POLICY_LINEAR_SLOW,
                        float(start),
                        float(initial),
                        float(floor),
                        linear_decay_per_min=SLOW_DECAY_PER_MIN,
                    )
                )
                specs.append(
                    GradualPolicySpec(
                        POLICY_LINEAR_FAST,
                        float(start),
                        float(initial),
                        float(floor),
                        linear_decay_per_min=FAST_DECAY_PER_MIN,
                    )
                )
    for start in DECAY_START_SEC:
        for initial in INITIAL_MFE_PCT:
            for floor in FLOOR_MFE_PCT:
                if floor > initial:
                    continue
                for lam in EXP_DECAY_LAMBDA:
                    specs.append(
                        GradualPolicySpec(
                            POLICY_EXP,
                            float(start),
                            float(initial),
                            float(floor),
                            exp_decay_lambda=float(lam),
                        )
                    )
    return specs


def activation_mfe_at_elapsed(
    elapsed_sec: float,
    *,
    policy: GradualPolicySpec,
    board_activate: float,
) -> float:
    if elapsed_sec < policy.decay_start_sec:
        return max(board_activate, policy.initial_mfe_pct)

    minutes_after = (elapsed_sec - policy.decay_start_sec) / 60.0

    if policy.policy_id == POLICY_EXP and policy.exp_decay_lambda is not None:
        span = policy.initial_mfe_pct - policy.floor_mfe_pct
        return round(
            policy.floor_mfe_pct + span * math.exp(-policy.exp_decay_lambda * minutes_after),
            6,
        )

    rate = policy.linear_decay_per_min or 0.03
    activated = policy.initial_mfe_pct - rate * minutes_after
    return round(max(policy.floor_mfe_pct, activated), 6)


def simulate_gradual_decay_exit(
    series: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    entry_price: float,
    session_end_ts: float,
    imb_pct: Optional[float],
    policy: GradualPolicySpec,
) -> dict[str, Any]:
    from replay.pnl_yen import compute_pnl_yen_100

    activate_base, giveback_frac, _tier = trailing_params_for_board_tier(imb_pct)
    hard_stop_px = entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    peak_pnl = 0.0
    last_ts = entry_ts
    last_px = entry_price

    usable = [(ts, px) for ts, px in series if ts >= entry_ts and px > 0]
    if not usable:
        return {
            "shadow_exit_reason": "no_ticks",
            "shadow_exit_ts": entry_ts,
            "shadow_pnl_pct": 0.0,
            "shadow_pnl_yen_100": 0.0,
            "shadow_exit_price": entry_price,
        }

    for ts, px in usable:
        if ts > session_end_ts:
            break
        elapsed = ts - entry_ts
        pnl = _pnl_pct(entry_price, px)
        peak_pnl = max(peak_pnl, pnl)
        last_ts = ts
        last_px = px

        if px <= hard_stop_px:
            return _exit_result(entry_price, px, ts, pnl, "stop_hit")

        activate = activation_mfe_at_elapsed(
            elapsed,
            policy=policy,
            board_activate=activate_base,
        )
        if peak_pnl >= activate and pnl <= peak_pnl * giveback_frac:
            return _exit_result(entry_price, px, ts, pnl, "trailing_mfe_exit")

    final_pnl = _pnl_pct(entry_price, last_px)
    return {
        "shadow_exit_reason": "session_close",
        "shadow_exit_ts": last_ts,
        "shadow_pnl_pct": final_pnl,
        "shadow_pnl_yen_100": round(compute_pnl_yen_100(entry_price, last_px), 2),
        "shadow_exit_price": round(last_px, 4),
    }


def _exit_result(
    entry_price: float,
    px: float,
    ts: float,
    pnl: float,
    reason: str,
) -> dict[str, Any]:
    from replay.pnl_yen import compute_pnl_yen_100

    return {
        "shadow_exit_reason": reason,
        "shadow_exit_ts": ts,
        "shadow_pnl_pct": pnl,
        "shadow_pnl_yen_100": round(compute_pnl_yen_100(entry_price, px), 2),
        "shadow_exit_price": round(px, 4),
    }


def _symbol_delta(
    trade_results: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    policy_key: str,
) -> float:
    total = 0.0
    for t in trade_results:
        if str(t.get("symbol") or "") != symbol:
            continue
        sh = float(t["shadow_by_policy"][policy_key]["shadow_pnl_yen_100"])
        base = float(t["baseline_pnl_yen_100"])
        total += sh - base
    return round(total, 2)


def aggregate_policy_results(
    trade_results: Sequence[Mapping[str, Any]],
    *,
    policy: GradualPolicySpec,
    p90_hold: float,
    baseline_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    baseline_pnls = [float(t["baseline_pnl_yen_100"]) for t in trade_results]
    key = policy.grid_key
    shadow_pnls = [float(t["shadow_by_policy"][key]["shadow_pnl_yen_100"]) for t in trade_results]

    saved, lost = _saved_lost_yen(baseline_pnls, shadow_pnls)
    deltas = [s - b for b, s in zip(baseline_pnls, shadow_pnls)]
    affected = sum(1 for d in deltas if abs(d) > 0.01)

    long_hold_losers = sum(
        1
        for t, s in zip(trade_results, shadow_pnls)
        if float(t.get("hold_sec") or 0) >= p90_hold and s < 0
    )
    baseline_long_hold_losers = int(baseline_metrics.get("long_hold_loser_count") or 0)
    baseline_pf = float(baseline_metrics.get("profit_factor") or 0.0)
    baseline_total = float(baseline_metrics.get("total_pnl_yen_100") or 0.0)

    good_damage = round(
        sum(
            min(0.0, float(t["shadow_by_policy"][key]["shadow_pnl_yen_100"]) - float(t["baseline_pnl_yen_100"]))
            for t in trade_results
            if t.get("is_good_long_hold")
        ),
        2,
    )
    bad_rescue = round(
        sum(
            max(0.0, float(t["shadow_by_policy"][key]["shadow_pnl_yen_100"]) - float(t["baseline_pnl_yen_100"]))
            for t in trade_results
            if t.get("is_bad_long_hold")
        ),
        2,
    )

    sym3905 = _symbol_delta(trade_results, symbol="3905.T", policy_key=key)
    sym4062 = _symbol_delta(trade_results, symbol="4062.T", policy_key=key)
    sym4078 = _symbol_delta(trade_results, symbol="4078.T", policy_key=key)

    sort_keys = [
        (_parse_ts(str(t.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST), i)
        for i, t in enumerate(trade_results)
    ]
    order = [i for _, i in sorted(sort_keys, key=lambda x: (x[0], x[1]))]
    chron_shadow = [shadow_pnls[i] for i in order]

    total_pnl = round(sum(shadow_pnls), 2)
    shadow_pf = _pf(shadow_pnls)
    max_dd = _max_drawdown_yen(chron_shadow)

    better_pnl = total_pnl > float(PHASE402_BEST["net_delta_yen"]) + baseline_total
    less_3905 = sym3905 > float(PHASE402_BEST["symbol_3905_damage_yen"])
    less_4062 = sym4062 > float(PHASE402_BEST["symbol_4062_damage_yen"])

    adopt = (
        total_pnl > baseline_total
        and (shadow_pf or 0.0) > baseline_pf
        and long_hold_losers < baseline_long_hold_losers
        and lost < saved
        and less_3905
        and less_4062
    )

    return {
        "policy_id": policy.policy_id,
        "decay_start_sec": policy.decay_start_sec,
        "initial_mfe_pct": policy.initial_mfe_pct,
        "floor_mfe_pct": policy.floor_mfe_pct,
        "linear_decay_per_min": policy.linear_decay_per_min,
        "exp_decay_lambda": policy.exp_decay_lambda,
        "total_pnl_yen_100": total_pnl,
        "profit_factor": shadow_pf,
        "trade_count": len(trade_results),
        "win_rate": _win_rate(shadow_pnls),
        "max_drawdown_yen_100": max_dd,
        "long_hold_loser_count": long_hold_losers,
        "saved_loss_yen": saved,
        "lost_upside_yen": lost,
        "net_delta_yen": round(total_pnl - baseline_total, 2),
        "affected_trade_count": affected,
        "long_hold_loser_delta": long_hold_losers - baseline_long_hold_losers,
        "symbol_3905_damage_yen": sym3905,
        "symbol_4062_damage_yen": sym4062,
        "symbol_4078_rescue_yen": sym4078,
        "good_long_hold_damage_yen": good_damage,
        "bad_long_hold_rescue_yen": bad_rescue,
        "better_than_phase402_pnl": better_pnl,
        "less_damage_than_phase402_3905": less_3905,
        "less_damage_than_phase402_4062": less_4062,
        "adopt_candidate": adopt,
    }


def _baseline_row(baseline_metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "policy_id": POLICY_BASELINE,
        "decay_start_sec": None,
        "initial_mfe_pct": None,
        "floor_mfe_pct": None,
        "linear_decay_per_min": None,
        "exp_decay_lambda": None,
        "total_pnl_yen_100": baseline_metrics.get("total_pnl_yen_100"),
        "profit_factor": baseline_metrics.get("profit_factor"),
        "trade_count": baseline_metrics.get("trade_count"),
        "win_rate": baseline_metrics.get("win_rate"),
        "max_drawdown_yen_100": baseline_metrics.get("max_drawdown_yen_100"),
        "long_hold_loser_count": baseline_metrics.get("long_hold_loser_count"),
        "saved_loss_yen": 0.0,
        "lost_upside_yen": 0.0,
        "net_delta_yen": 0.0,
        "affected_trade_count": 0,
        "long_hold_loser_delta": 0,
        "symbol_3905_damage_yen": 0.0,
        "symbol_4062_damage_yen": 0.0,
        "symbol_4078_rescue_yen": 0.0,
        "good_long_hold_damage_yen": 0.0,
        "bad_long_hold_rescue_yen": 0.0,
        "better_than_phase402_pnl": False,
        "less_damage_than_phase402_3905": False,
        "less_damage_than_phase402_4062": False,
        "adopt_candidate": False,
    }


def _trade_row(
    t: Mapping[str, Any],
    policy: GradualPolicySpec,
    shadow_pnl: float,
    shadow_reason: str,
    *,
    shadow_exit_ts: Optional[float] = None,
) -> dict[str, Any]:
    baseline = float(t["baseline_pnl_yen_100"])
    exit_time = t.get("exit_time")
    if shadow_exit_ts and shadow_exit_ts > 0:
        exit_time = datetime.fromtimestamp(shadow_exit_ts, tz=JST).isoformat(timespec="seconds")
    return {
        "policy_id": policy.policy_id,
        "decay_start_sec": policy.decay_start_sec,
        "initial_mfe_pct": policy.initial_mfe_pct,
        "floor_mfe_pct": policy.floor_mfe_pct,
        "linear_decay_per_min": policy.linear_decay_per_min,
        "exp_decay_lambda": policy.exp_decay_lambda,
        "day": t.get("day"),
        "session": t.get("session"),
        "symbol": t.get("symbol"),
        "entry_time": t.get("entry_time"),
        "exit_time": exit_time,
        "baseline_exit_time": t.get("exit_time"),
        "hold_sec": t.get("hold_sec"),
        "baseline_pnl_yen_100": baseline,
        "shadow_pnl_yen_100": round(shadow_pnl, 2),
        "delta_yen": round(shadow_pnl - baseline, 2),
        "baseline_exit_reason": t.get("baseline_exit_reason"),
        "shadow_exit_reason": shadow_reason,
        "is_long_hold_loser": t.get("is_long_hold_loser"),
        "is_good_long_hold": t.get("is_good_long_hold"),
        "is_bad_long_hold": t.get("is_bad_long_hold"),
        "focus_symbol": t.get("focus_symbol"),
    }


def _mandatory_answers(
    best: Optional[Mapping[str, Any]],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    if not best:
        return {
            "1_best_policy": None,
            "2_pnl_improvement_yen": 0.0,
            "3_pf_improvement": 0.0,
            "4_long_hold_loser_reduction": 0,
            "5_symbol_3905_damage_yen": None,
            "6_symbol_4062_damage_yen": None,
            "7_symbol_4078_rescue_yen": None,
            "8_better_than_phase402": False,
            "9_adopt_candidate": False,
        }
    base_pf = float(baseline.get("profit_factor") or 0.0)
    best_pf = float(best.get("profit_factor") or 0.0)
    return {
        "1_best_policy": {
            "policy_id": best.get("policy_id"),
            "decay_start_sec": best.get("decay_start_sec"),
            "initial_mfe_pct": best.get("initial_mfe_pct"),
            "floor_mfe_pct": best.get("floor_mfe_pct"),
            "linear_decay_per_min": best.get("linear_decay_per_min"),
            "exp_decay_lambda": best.get("exp_decay_lambda"),
        },
        "2_pnl_improvement_yen": best.get("net_delta_yen"),
        "3_pf_improvement": round(best_pf - base_pf, 4),
        "4_long_hold_loser_reduction": -int(best.get("long_hold_loser_delta") or 0),
        "5_symbol_3905_damage_yen": best.get("symbol_3905_damage_yen"),
        "6_symbol_4062_damage_yen": best.get("symbol_4062_damage_yen"),
        "7_symbol_4078_rescue_yen": best.get("symbol_4078_rescue_yen"),
        "8_better_than_phase402": bool(
            best
            and (
                float(best.get("net_delta_yen") or 0) > float(PHASE402_BEST["net_delta_yen"])
                or (
                    best.get("less_damage_than_phase402_3905")
                    and best.get("less_damage_than_phase402_4062")
                )
            )
        ),
        "8a_pnl_better_than_phase402": bool(
            best and float(best.get("net_delta_yen") or 0) > float(PHASE402_BEST["net_delta_yen"])
        ),
        "8b_3905_less_damage_than_phase402": bool(best and best.get("less_damage_than_phase402_3905")),
        "8c_4062_less_damage_than_phase402": bool(best and best.get("less_damage_than_phase402_4062")),
        "9_adopt_candidate": best.get("adopt_candidate"),
    }


def _render_report(summary: Mapping[str, Any]) -> str:
    baseline = summary.get("baseline") or {}
    best = summary.get("best_policy")
    ma = summary.get("mandatory_answers") or {}
    p402 = summary.get("phase402_reference") or {}
    lines = [
        "# Phase403 — Gradual Time-Decay MFE Shadow",
        "",
        f"Generated: {summary.get('generated_at')}",
        f"Period: {summary.get('period_start')} – {summary.get('period_end')}",
        f"Verdict: **{summary.get('verdict')}**",
        "",
        summary.get("headline") or "",
        "",
        "## Mandatory answers",
        "",
        f"1. Best policy: `{ma.get('1_best_policy')}`",
        f"2. PnL improvement: ¥{ma.get('2_pnl_improvement_yen')}",
        f"3. PF improvement: {ma.get('3_pf_improvement')}",
        f"4. long_hold_loser reduction: {ma.get('4_long_hold_loser_reduction')}",
        f"5. 3905.T damage: ¥{ma.get('5_symbol_3905_damage_yen')}",
        f"6. 4062.T damage: ¥{ma.get('6_symbol_4062_damage_yen')}",
        f"7. 4078.T rescue: ¥{ma.get('7_symbol_4078_rescue_yen')}",
        f"8. Better than Phase402: {ma.get('8_better_than_phase402')} "
        f"(PnL: {ma.get('8a_pnl_better_than_phase402')}, "
        f"3905: {ma.get('8b_3905_less_damage_than_phase402')}, "
        f"4062: {ma.get('8c_4062_less_damage_than_phase402')})",
        f"9. Adopt candidate: {ma.get('9_adopt_candidate')}",
        "",
        "## Baseline (Phase399 position_cap)",
        "",
        f"| total_pnl_yen_100 | ¥{baseline.get('total_pnl_yen_100')} |",
        f"| profit_factor | {baseline.get('profit_factor')} |",
        f"| long_hold_loser_count | {baseline.get('long_hold_loser_count')} |",
        "",
        "## Phase402 reference (step decay best)",
        "",
        f"| net_delta_yen | ¥{p402.get('net_delta_yen')} |",
        f"| 3905.T damage | ¥{p402.get('symbol_3905_damage_yen')} |",
        f"| 4062.T damage | ¥{p402.get('symbol_4062_damage_yen')} |",
        f"| 4078.T rescue | ¥{p402.get('symbol_4078_rescue_yen')} |",
        "",
        "## Best gradual policy",
        "",
    ]
    if best:
        lines.extend(
            [
                f"- policy: `{best.get('policy_id')}`",
                f"- decay_start_sec: {best.get('decay_start_sec')}",
                f"- initial_mfe_pct: {best.get('initial_mfe_pct')}",
                f"- floor_mfe_pct: {best.get('floor_mfe_pct')}",
                f"- linear_decay_per_min: {best.get('linear_decay_per_min')}",
                f"- exp_decay_lambda: {best.get('exp_decay_lambda')}",
                f"- net_delta_yen: ¥{best.get('net_delta_yen')}",
                f"- adopt_candidate: {best.get('adopt_candidate')}",
            ]
        )
    else:
        lines.append("No adopt candidate found.")

    best_lhl = summary.get("best_long_hold_loser_improvement")
    best_3905 = summary.get("best_3905_preservation")
    if best_lhl:
        lines.extend(
            [
                "",
                "## Best policy with long_hold_loser improvement",
                "",
                f"- `{best_lhl.get('policy_id')}` start={best_lhl.get('decay_start_sec')}s "
                f"initial={best_lhl.get('initial_mfe_pct')}% floor={best_lhl.get('floor_mfe_pct')}%",
                f"- net_delta: ¥{best_lhl.get('net_delta_yen')} | long_hold_loser Δ{best_lhl.get('long_hold_loser_delta')}",
                f"- 3905 damage: ¥{best_lhl.get('symbol_3905_damage_yen')} | 4062: ¥{best_lhl.get('symbol_4062_damage_yen')}",
            ]
        )
    if best_3905:
        lines.extend(
            [
                "",
                "## Best 3905.T preservation",
                "",
                f"- `{best_3905.get('policy_id')}` start={best_3905.get('decay_start_sec')}s "
                f"lambda={best_3905.get('exp_decay_lambda')}",
                f"- 3905 damage: ¥{best_3905.get('symbol_3905_damage_yen')} | net_delta: ¥{best_3905.get('net_delta_yen')}",
            ]
        )

    lines.extend(
        [
            "",
            "## Focus symbol comparison (best vs Phase402)",
            "",
            "| symbol | Phase402 delta | Phase403 best delta |",
            "|--------|----------------|---------------------|",
        ]
    )
    for sym, key in (
        ("3905.T", "symbol_3905_damage_yen"),
        ("4062.T", "symbol_4062_damage_yen"),
        ("4047.T", None),
        ("9984.T", None),
        ("4078.T", "symbol_4078_rescue_yen"),
        ("6055.T", None),
        ("3915.T", None),
        ("7220.T", None),
    ):
        p403_val = best.get(key) if best and key else "n/a"
        p402_val = p402.get(key) if key else "n/a"
        lines.append(f"| {sym} | ¥{p402_val} | ¥{p403_val} |")

    lines.extend(
        [
            "",
            "## Constraints",
            "",
            "- Runtime / YAML / Entry / Exit / Discord 変更なし",
            "- shadow / research のみ",
            "",
        ]
    )
    return "\n".join(lines)


def run_phase403_shadow(
    *,
    repo_root: Path,
    trades_path: Optional[Path] = None,
    phase400_summary_path: Optional[Path] = None,
    output_dir: Path,
    period_start: str = PERIOD_START,
    period_end: str = PERIOD_END,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    trades_path = trades_path or (
        repo_root / "results" / "reports" / "phase399_historical_position_cap_backfill_trades.csv"
    )
    phase400_summary_path = phase400_summary_path or (
        repo_root / "results" / "reports" / "phase400_holding_time_summary.json"
    )

    p90_hold = 1290.6
    if phase400_summary_path.is_file():
        p400 = json.loads(phase400_summary_path.read_text(encoding="utf-8"))
        p90_hold = float(p400.get("hold_duration_sec", {}).get("p90_hold_sec") or p90_hold)

    raw = load_phase399_trades(trades_path)
    accepted = [
        enrich_trade(r)
        for r in raw
        if str(r.get("day") or "") >= period_start
        and str(r.get("day") or "") <= period_end
        and str(r.get("position_cap_accepted") or "").lower() in ("true", "1", "yes")
    ]
    for t in accepted:
        t["_p90_hold"] = p90_hold

    policies = iter_policy_grid()
    session_cache: dict[str, dict[str, Any]] = {}
    trade_results: list[dict[str, Any]] = []

    for trade in accepted:
        ctx = _prepare_trade_context(trade, repo_root=repo_root, session_cache=session_cache)
        if ctx is None:
            continue
        shadow_by_policy: dict[str, dict[str, Any]] = {}
        for policy in policies:
            sim = simulate_gradual_decay_exit(
                ctx["price_series"],
                entry_ts=ctx["entry_ts"],
                entry_price=ctx["entry_price"],
                session_end_ts=ctx["session_end_ts"],
                imb_pct=ctx["imb_pct"],
                policy=policy,
            )
            shadow_by_policy[policy.grid_key] = sim
        trade_results.append({**ctx, "shadow_by_policy": shadow_by_policy})

    baseline_pnls = [float(t["baseline_pnl_yen_100"]) for t in trade_results]
    sort_keys = [
        (_parse_ts(str(t.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST), i)
        for i, t in enumerate(trade_results)
    ]
    order = [i for _, i in sorted(sort_keys, key=lambda x: (x[0], x[1]))]
    baseline_metrics = {
        "total_pnl_yen_100": round(sum(baseline_pnls), 2),
        "profit_factor": _pf(baseline_pnls),
        "trade_count": len(trade_results),
        "win_rate": _win_rate(baseline_pnls),
        "max_drawdown_yen_100": _max_drawdown_yen([baseline_pnls[i] for i in order]),
        "long_hold_loser_count": sum(1 for t in trade_results if t.get("is_long_hold_loser")),
    }

    grid_rows: list[dict[str, Any]] = [_baseline_row(baseline_metrics)]
    for policy in policies:
        grid_rows.append(
            aggregate_policy_results(
                trade_results,
                policy=policy,
                p90_hold=p90_hold,
                baseline_metrics=baseline_metrics,
            )
        )

    adopt_rows = [r for r in grid_rows if r.get("adopt_candidate")]
    adopt_rows.sort(
        key=lambda r: (
            -float(r.get("net_delta_yen") or 0),
            float(r.get("symbol_3905_damage_yen") or -1e18),
            float(r.get("symbol_4062_damage_yen") or -1e18),
        )
    )

    ranked = sorted(
        [r for r in grid_rows if r.get("policy_id") != POLICY_BASELINE],
        key=lambda r: -float(r.get("net_delta_yen") or 0),
    )
    best_overall = ranked[0] if ranked else None
    best_adopt = adopt_rows[0] if adopt_rows else None

    lhl_improved = [
        r
        for r in grid_rows
        if r.get("policy_id") != POLICY_BASELINE
        and int(r.get("long_hold_loser_delta") or 0) < 0
        and float(r.get("net_delta_yen") or 0) > 0
        and float(r.get("profit_factor") or 0) > float(baseline_metrics.get("profit_factor") or 0)
        and float(r.get("lost_upside_yen") or 0) < float(r.get("saved_loss_yen") or 0)
    ]
    lhl_improved.sort(key=lambda r: -float(r.get("net_delta_yen") or 0))
    best_lhl = lhl_improved[0] if lhl_improved else None

    sym3905_ranked = sorted(
        [r for r in grid_rows if r.get("policy_id") != POLICY_BASELINE],
        key=lambda r: float(r.get("symbol_3905_damage_yen") or -1e18),
        reverse=True,
    )
    best_3905 = sym3905_ranked[0] if sym3905_ranked else None

    best_for_summary = best_adopt or best_overall

    trade_rows: list[dict[str, Any]] = []
    if best_for_summary:
        pk = (
            f"{best_for_summary['policy_id']}|{best_for_summary['decay_start_sec']}|"
            f"{best_for_summary['initial_mfe_pct']}|{best_for_summary['floor_mfe_pct']}|"
            f"{best_for_summary.get('linear_decay_per_min')}|{best_for_summary.get('exp_decay_lambda')}"
        )
        best_policy = next(p for p in policies if p.grid_key == pk)
        for t in trade_results:
            if not (t.get("focus_symbol") or t.get("is_long_hold_loser")):
                continue
            sh = t["shadow_by_policy"][pk]
            trade_rows.append(
                _trade_row(
                    t,
                    best_policy,
                    sh["shadow_pnl_yen_100"],
                    _normalize_shadow_exit(sh["shadow_exit_reason"]),
                    shadow_exit_ts=sh.get("shadow_exit_ts"),
                )
            )

    grid_path = output_dir / "phase403_gradual_time_decay_grid.csv"
    trades_path_out = output_dir / "phase403_gradual_time_decay_trades.csv"
    _write_csv(grid_path, grid_rows, GRID_FIELDS)
    _write_csv(trades_path_out, trade_rows, TRADE_FIELDS)

    mandatory = _mandatory_answers(best_for_summary, baseline_metrics)
    verdict = "adopt_candidate_found" if best_adopt else "no_adopt_candidate"

    summary = {
        "phase": 403,
        "generated_at": _now_iso(),
        "period_start": period_start,
        "period_end": period_end,
        "source_trades": str(trades_path),
        "position_cap_accepted_trade_count": len(trade_results),
        "p90_hold_sec": p90_hold,
        "baseline": baseline_metrics,
        "phase402_reference": PHASE402_BEST,
        "grid_row_count": len(grid_rows),
        "policy_variant_count": len(policies),
        "adopt_candidate_count": len(adopt_rows),
        "best_policy": best_for_summary,
        "best_adopt_policy": best_adopt,
        "best_overall_by_pnl": best_overall,
        "best_long_hold_loser_improvement": best_lhl,
        "best_3905_preservation": best_3905,
        "long_hold_loser_improvement_policy_count": len(lhl_improved),
        "mandatory_answers": mandatory,
        "verdict": verdict,
        "headline": _headline(best_for_summary, best_adopt, mandatory),
    }

    summary_path = output_dir / "phase403_gradual_time_decay_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    report_path = repo_root / "docs" / "operations" / "phase403_gradual_time_decay_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_report(summary), encoding="utf-8")

    return {
        "summary": summary,
        "grid_path": str(grid_path),
        "trades_path": str(trades_path_out),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
    }


def _headline(
    best: Optional[Mapping[str, Any]],
    best_adopt: Optional[Mapping[str, Any]],
    mandatory: Mapping[str, Any],
) -> str:
    if not best:
        return "Phase403: gradual decay — no results"
    adopt_tag = "ADOPT" if best_adopt else "no_adopt"
    return (
        f"Phase403: {best.get('policy_id')} start={best.get('decay_start_sec')}s "
        f"initial={best.get('initial_mfe_pct')}% floor={best.get('floor_mfe_pct')}% "
        f"delta=¥{best.get('net_delta_yen')} 3905=¥{best.get('symbol_3905_damage_yen')} "
        f"4062=¥{best.get('symbol_4062_damage_yen')} vs402={mandatory.get('8_better_than_phase402')} "
        f"{adopt_tag}"
    )
