"""
Phase404: No Progress Exit shadow replay.

Time + stagnation (low MFE / no high updates / VWAP) conditional exit.
Research / shadow only — no Runtime / YAML / Entry / Exit changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key, _write_csv
from research.phase400_holding_time_audit import enrich_trade, load_phase399_trades
from research.phase401_long_hold_loser_forensic import (
    _accepted_lookup,
    _load_structural_lookup,
    _mfe_up_to,
    _session_dir,
)
from research.phase402_time_decay_exit_shadow import (
    BAD_LONG_HOLD_SYMBOLS,
    GOOD_LONG_HOLD_SYMBOLS,
    HARD_STOP_PCT,
    _max_drawdown_yen,
    _normalize_shadow_exit,
    _saved_lost_yen,
)
from research.runtime_pilot_policy_review import _build_price_index
from research.small_paper_performance_review import _load_events
from small_paper.board_dynamic_trailing_shadow import trailing_params_for_board_tier

JST = ZoneInfo("Asia/Tokyo")
PERIOD_START = "20260529"
PERIOD_END = "20260615"
POLICY_BASELINE = "baseline_phase399"

HOLD_SEC_THRESHOLDS = (900, 1200, 1500, 1800)
MAX_MFE_THRESHOLDS = (0.2, 0.3, 0.5, 0.8)
CURRENT_PNL_THRESHOLDS = (-0.2, 0.0, 0.1, 0.2)
HIGH_UPDATE_MODES = ("zero", "lte1", "none")
VWAP_DEV_MODES = ("lt0", "lt-0.2", "none")

PHASE402_REFERENCE = {
    "net_delta_yen": 204112.4,
    "symbol_3905_damage_yen": -21999.71,
    "symbol_4062_damage_yen": -7500.78,
    "good_long_hold_damage_yen": -29500.62,
    "long_hold_loser_delta": -1,
}

GRID_FIELDS = [
    "policy_id",
    "hold_sec_threshold",
    "max_mfe_pct_threshold",
    "current_pnl_pct_threshold",
    "high_update_mode",
    "vwap_dev_mode",
    "total_pnl_yen_100",
    "profit_factor",
    "trade_count",
    "win_rate",
    "max_drawdown_yen_100",
    "long_hold_loser_count",
    "long_hold_loser_delta",
    "long_hold_loser_rescued_count",
    "mfe_lt_0p5_improved_count",
    "saved_loss_yen",
    "lost_upside_yen",
    "net_delta_yen",
    "affected_trade_count",
    "symbol_3905_damage_yen",
    "symbol_4062_damage_yen",
    "symbol_4078_rescue_yen",
    "good_long_hold_damage_yen",
    "bad_long_hold_rescue_yen",
    "less_3905_damage_than_phase402",
    "less_4062_damage_than_phase402",
    "adopt_candidate",
]

TRADE_FIELDS = [
    "policy_id",
    "hold_sec_threshold",
    "max_mfe_pct_threshold",
    "current_pnl_pct_threshold",
    "high_update_mode",
    "vwap_dev_mode",
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
    "is_mfe_lt_0p5_loser",
    "is_good_long_hold",
    "is_bad_long_hold",
    "focus_symbol",
]


@dataclass(frozen=True)
class NoProgressPolicySpec:
    hold_sec: float
    max_mfe_pct: float
    current_pnl_pct: float
    high_update_mode: str
    vwap_dev_mode: str

    @property
    def policy_id(self) -> str:
        return "no_progress_exit"

    @property
    def grid_key(self) -> str:
        return (
            f"{self.hold_sec}|{self.max_mfe_pct}|{self.current_pnl_pct}|"
            f"{self.high_update_mode}|{self.vwap_dev_mode}"
        )


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _pnl_pct(entry: float, px: float) -> float:
    if entry <= 0:
        return 0.0
    return round((px - entry) / entry * 100.0, 4)


def iter_policy_grid() -> list[NoProgressPolicySpec]:
    specs: list[NoProgressPolicySpec] = []
    for hold in HOLD_SEC_THRESHOLDS:
        for mfe in MAX_MFE_THRESHOLDS:
            for pnl_thr in CURRENT_PNL_THRESHOLDS:
                for hi_mode in HIGH_UPDATE_MODES:
                    for vwap_mode in VWAP_DEV_MODES:
                        specs.append(
                            NoProgressPolicySpec(
                                hold_sec=float(hold),
                                max_mfe_pct=float(mfe),
                                current_pnl_pct=float(pnl_thr),
                                high_update_mode=hi_mode,
                                vwap_dev_mode=vwap_mode,
                            )
                        )
    return specs


def no_progress_matches(state: Mapping[str, Any], policy: NoProgressPolicySpec) -> bool:
    if float(state["elapsed"]) < policy.hold_sec:
        return False
    if float(state["peak_mfe"]) >= policy.max_mfe_pct:
        return False
    if float(state["pnl"]) >= policy.current_pnl_pct:
        return False
    if policy.high_update_mode == "zero" and int(state["high_updates"]) > 0:
        return False
    if policy.high_update_mode == "lte1" and int(state["high_updates"]) > 1:
        return False
    if policy.vwap_dev_mode == "lt0":
        vd = state.get("vwap_dev")
        if vd is None or float(vd) >= 0:
            return False
    if policy.vwap_dev_mode == "lt-0.2":
        vd = state.get("vwap_dev")
        if vd is None or float(vd) >= -0.2:
            return False
    return True


def build_tick_states(
    series: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    entry_price: float,
    session_end_ts: float,
    entry_vwap_dev_pct: Optional[float],
) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    peak_mfe = 0.0
    session_high: Optional[float] = None
    high_updates = 0

    for ts, px in series:
        if ts < entry_ts or px <= 0:
            continue
        if ts > session_end_ts:
            break
        elapsed = ts - entry_ts
        pnl = _pnl_pct(entry_price, px)
        peak_mfe = max(peak_mfe, pnl)

        if session_high is None:
            session_high = px
        elif px > session_high:
            high_updates += 1
            session_high = px

        vwap_dev: Optional[float] = None
        if entry_vwap_dev_pct is not None:
            vwap_dev = round(pnl - float(entry_vwap_dev_pct), 4)

        states.append(
            {
                "ts": ts,
                "px": px,
                "elapsed": elapsed,
                "pnl": pnl,
                "peak_mfe": peak_mfe,
                "high_updates": high_updates,
                "vwap_dev": vwap_dev,
            }
        )
    return states


def simulate_no_progress_exit(
    states: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_ts: float,
    session_end_ts: float,
    imb_pct: Optional[float],
    policy: NoProgressPolicySpec,
) -> dict[str, Any]:
    from replay.pnl_yen import compute_pnl_yen_100

    activate_base, giveback_frac, _tier = trailing_params_for_board_tier(imb_pct)
    hard_stop_px = entry_price * (1.0 - HARD_STOP_PCT / 100.0)

    if not states:
        return {
            "shadow_exit_reason": "no_ticks",
            "shadow_exit_ts": entry_ts,
            "shadow_pnl_pct": 0.0,
            "shadow_pnl_yen_100": 0.0,
            "shadow_exit_price": entry_price,
        }

    for state in states:
        ts = float(state["ts"])
        px = float(state["px"])
        pnl = float(state["pnl"])
        peak_mfe = float(state["peak_mfe"])

        if no_progress_matches(state, policy):
            return _exit_result(entry_price, px, ts, pnl, "no_progress_exit")

        if px <= hard_stop_px:
            return _exit_result(entry_price, px, ts, pnl, "stop_hit")

        activate = activate_base
        if peak_mfe >= activate and pnl <= peak_mfe * giveback_frac:
            return _exit_result(entry_price, px, ts, pnl, "trailing_mfe_exit")

    last = states[-1]
    final_pnl = float(last["pnl"])
    return {
        "shadow_exit_reason": "session_close",
        "shadow_exit_ts": float(last["ts"]),
        "shadow_pnl_pct": final_pnl,
        "shadow_pnl_yen_100": round(compute_pnl_yen_100(entry_price, float(last["px"])), 2),
        "shadow_exit_price": round(float(last["px"]), 4),
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


def _session_end_ts(series: Sequence[tuple[float, float]], fallback_ts: float) -> float:
    if not series:
        return fallback_ts
    return max(ts for ts, _ in series)


def _prepare_trade_context(
    trade: Mapping[str, Any],
    *,
    repo_root: Path,
    session_cache: dict[str, dict[str, Any]],
    p90_hold: float,
) -> Optional[dict[str, Any]]:
    from research.phase400_holding_time_audit import normalize_exit_reason

    day = str(trade.get("day") or "")
    session = str(trade.get("session") or "")
    sym = str(trade.get("symbol") or "")
    entry_time = str(trade.get("entry_time") or "")
    cache_key = f"{day}/{session}"

    if cache_key not in session_cache:
        sdir = _session_dir(repo_root, day, session)
        events = _load_events(sdir) if sdir.is_dir() else []
        session_cache[cache_key] = {
            "structural": _load_structural_lookup(sdir),
            "accepted": _accepted_lookup(sdir),
            "price_index": _build_price_index(events),
        }

    cache = session_cache[cache_key]
    pos_key = _position_key({"symbol": sym, "entry_time": entry_time})
    struct = cache["structural"].get(pos_key, {})
    acc = cache["accepted"].get((sym, entry_time), {})

    entry_px = _float(struct.get("entry_price")) or _float(acc.get("current_price")) or _float(acc.get("entry_price"))
    ent_dt = _parse_ts(entry_time)
    if entry_px is None or entry_px <= 0 or ent_dt is None:
        return None

    ent_ts = ent_dt.timestamp()
    series = cache["price_index"].get(sym, [])
    ex_dt = _parse_ts(str(trade.get("exit_time") or ""))
    fallback_end = ex_dt.timestamp() if ex_dt else ent_ts + float(trade.get("hold_sec") or 0)
    session_end = _session_end_ts(series, fallback_end)
    imb = _float(acc.get("entry_imbalance_percentile"))
    entry_vwap = _float(acc.get("entry_vwap_dev_pct"))

    until_ts = ex_dt.timestamp() if ex_dt else session_end
    max_mfe = _mfe_up_to(series, ent_ts, entry_px, until_ts)
    struct_mfe = _float(struct.get("mfe_pct"))
    if struct_mfe is not None and struct_mfe > max_mfe:
        max_mfe = struct_mfe

    tick_states = build_tick_states(
        series,
        entry_ts=ent_ts,
        entry_price=entry_px,
        session_end_ts=session_end,
        entry_vwap_dev_pct=entry_vwap,
    )

    baseline_pnl = float(trade.get("pnl_yen_100_float") or 0.0)
    baseline_reason = normalize_exit_reason(str(trade.get("exit_reason") or ""))
    is_long_hold_loser = bool(
        trade.get("is_loser") and float(trade.get("hold_sec") or 0) >= p90_hold
    )

    return {
        "day": day,
        "session": session,
        "symbol": sym,
        "entry_time": entry_time,
        "exit_time": trade.get("exit_time"),
        "hold_sec": float(trade.get("hold_sec") or 0.0),
        "baseline_pnl_yen_100": baseline_pnl,
        "baseline_exit_reason": baseline_reason,
        "entry_price": entry_px,
        "entry_ts": ent_ts,
        "session_end_ts": session_end,
        "price_series": series,
        "tick_states": tick_states,
        "imb_pct": imb,
        "max_mfe_pct": max_mfe,
        "is_long_hold_loser": is_long_hold_loser,
        "is_mfe_lt_0p5_loser": is_long_hold_loser and max_mfe < 0.5,
        "is_good_long_hold": bool(
            sym in GOOD_LONG_HOLD_SYMBOLS
            and trade.get("is_winner")
            and float(trade.get("hold_sec") or 0) >= p90_hold
        ),
        "is_bad_long_hold": bool(
            sym in BAD_LONG_HOLD_SYMBOLS
            and trade.get("is_loser")
            and float(trade.get("hold_sec") or 0) >= p90_hold
        ),
        "focus_symbol": sym.rstrip(".T")
        in {s.rstrip(".T") for s in GOOD_LONG_HOLD_SYMBOLS | BAD_LONG_HOLD_SYMBOLS},
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
    policy: NoProgressPolicySpec,
    p90_hold: float,
    baseline_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    baseline_pnls = [float(t["baseline_pnl_yen_100"]) for t in trade_results]
    key = policy.grid_key
    shadow_pnls = [float(t["shadow_by_policy"][key]["shadow_pnl_yen_100"]) for t in trade_results]

    saved, lost = _saved_lost_yen(baseline_pnls, shadow_pnls)
    affected = sum(1 for b, s in zip(baseline_pnls, shadow_pnls) if abs(s - b) > 0.01)

    long_hold_losers = sum(
        1
        for t, s in zip(trade_results, shadow_pnls)
        if float(t.get("hold_sec") or 0) >= p90_hold and s < 0
    )
    baseline_lhl = int(baseline_metrics.get("long_hold_loser_count") or 0)
    baseline_pf = float(baseline_metrics.get("profit_factor") or 0.0)
    baseline_total = float(baseline_metrics.get("total_pnl_yen_100") or 0.0)

    lhl_rescued = sum(
        1
        for t, s in zip(trade_results, shadow_pnls)
        if t.get("is_long_hold_loser") and s > float(t["baseline_pnl_yen_100"]) + 0.01
    )
    mfe_improved = sum(
        1
        for t, s in zip(trade_results, shadow_pnls)
        if t.get("is_mfe_lt_0p5_loser") and s > float(t["baseline_pnl_yen_100"]) + 0.01
    )

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
    total_pnl = round(sum(shadow_pnls), 2)
    shadow_pf = _pf(shadow_pnls)
    max_dd = _max_drawdown_yen([shadow_pnls[i] for i in order])
    net_delta = round(total_pnl - baseline_total, 2)

    less_3905 = sym3905 > float(PHASE402_REFERENCE["symbol_3905_damage_yen"])
    less_4062 = sym4062 > float(PHASE402_REFERENCE["symbol_4062_damage_yen"])
    comparable_p402 = net_delta >= float(PHASE402_REFERENCE["net_delta_yen"]) * 0.95
    less_good_than_p402 = good_damage > float(PHASE402_REFERENCE["good_long_hold_damage_yen"])

    base_adopt = (
        total_pnl > baseline_total
        and (shadow_pf or 0.0) > baseline_pf
        and long_hold_losers < baseline_lhl
        and saved > lost
    )
    adopt = base_adopt and (
        (less_3905 and less_4062)
        or (comparable_p402 and less_good_than_p402 and less_3905)
    )

    return {
        "policy_id": policy.policy_id,
        "hold_sec_threshold": policy.hold_sec,
        "max_mfe_pct_threshold": policy.max_mfe_pct,
        "current_pnl_pct_threshold": policy.current_pnl_pct,
        "high_update_mode": policy.high_update_mode,
        "vwap_dev_mode": policy.vwap_dev_mode,
        "total_pnl_yen_100": total_pnl,
        "profit_factor": shadow_pf,
        "trade_count": len(trade_results),
        "win_rate": _win_rate(shadow_pnls),
        "max_drawdown_yen_100": max_dd,
        "long_hold_loser_count": long_hold_losers,
        "long_hold_loser_delta": long_hold_losers - baseline_lhl,
        "long_hold_loser_rescued_count": lhl_rescued,
        "mfe_lt_0p5_improved_count": mfe_improved,
        "saved_loss_yen": saved,
        "lost_upside_yen": lost,
        "net_delta_yen": net_delta,
        "affected_trade_count": affected,
        "symbol_3905_damage_yen": sym3905,
        "symbol_4062_damage_yen": sym4062,
        "symbol_4078_rescue_yen": sym4078,
        "good_long_hold_damage_yen": good_damage,
        "bad_long_hold_rescue_yen": bad_rescue,
        "less_3905_damage_than_phase402": less_3905,
        "less_4062_damage_than_phase402": less_4062,
        "adopt_candidate": adopt,
    }


def _baseline_row(baseline_metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "policy_id": POLICY_BASELINE,
        "hold_sec_threshold": None,
        "max_mfe_pct_threshold": None,
        "current_pnl_pct_threshold": None,
        "high_update_mode": None,
        "vwap_dev_mode": None,
        "total_pnl_yen_100": baseline_metrics.get("total_pnl_yen_100"),
        "profit_factor": baseline_metrics.get("profit_factor"),
        "trade_count": baseline_metrics.get("trade_count"),
        "win_rate": baseline_metrics.get("win_rate"),
        "max_drawdown_yen_100": baseline_metrics.get("max_drawdown_yen_100"),
        "long_hold_loser_count": baseline_metrics.get("long_hold_loser_count"),
        "long_hold_loser_delta": 0,
        "long_hold_loser_rescued_count": 0,
        "mfe_lt_0p5_improved_count": 0,
        "saved_loss_yen": 0.0,
        "lost_upside_yen": 0.0,
        "net_delta_yen": 0.0,
        "affected_trade_count": 0,
        "symbol_3905_damage_yen": 0.0,
        "symbol_4062_damage_yen": 0.0,
        "symbol_4078_rescue_yen": 0.0,
        "good_long_hold_damage_yen": 0.0,
        "bad_long_hold_rescue_yen": 0.0,
        "less_3905_damage_than_phase402": False,
        "less_4062_damage_than_phase402": False,
        "adopt_candidate": False,
    }


def _trade_row(
    t: Mapping[str, Any],
    policy: NoProgressPolicySpec,
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
        "hold_sec_threshold": policy.hold_sec,
        "max_mfe_pct_threshold": policy.max_mfe_pct,
        "current_pnl_pct_threshold": policy.current_pnl_pct,
        "high_update_mode": policy.high_update_mode,
        "vwap_dev_mode": policy.vwap_dev_mode,
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
        "is_mfe_lt_0p5_loser": t.get("is_mfe_lt_0p5_loser"),
        "is_good_long_hold": t.get("is_good_long_hold"),
        "is_bad_long_hold": t.get("is_bad_long_hold"),
        "focus_symbol": t.get("focus_symbol"),
    }


def _focus_symbol_summary(
    trade_results: Sequence[Mapping[str, Any]],
    policy_key: str,
) -> dict[str, list[dict[str, Any]]]:
    def _group(symbols: frozenset[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for sym in sorted(symbols):
            trades = [t for t in trade_results if str(t.get("symbol") or "") == sym]
            if not trades:
                continue
            baseline = round(sum(float(t["baseline_pnl_yen_100"]) for t in trades), 2)
            shadow = round(
                sum(float(t["shadow_by_policy"][policy_key]["shadow_pnl_yen_100"]) for t in trades),
                2,
            )
            rows.append(
                {
                    "symbol": sym,
                    "trade_count": len(trades),
                    "baseline_pnl_yen_100": baseline,
                    "shadow_pnl_yen_100": shadow,
                    "delta_yen": round(shadow - baseline, 2),
                }
            )
        return rows

    return {
        "good_long_hold": _group(GOOD_LONG_HOLD_SYMBOLS),
        "bad_long_hold": _group(BAD_LONG_HOLD_SYMBOLS),
    }


def _render_report(summary: Mapping[str, Any]) -> str:
    baseline = summary.get("baseline") or {}
    best = summary.get("best_policy")
    ma = summary.get("mandatory_analysis") or {}
    p402 = summary.get("phase402_reference") or {}
    lines = [
        "# Phase404 — No Progress Exit Shadow",
        "",
        f"Generated: {summary.get('generated_at')}",
        f"Period: {summary.get('period_start')} – {summary.get('period_end')}",
        f"Verdict: **{summary.get('verdict')}**",
        "",
        summary.get("headline") or "",
        "",
        "## Mandatory analysis",
        "",
        f"1. long_hold_loser rescued: {ma.get('long_hold_loser_rescued_count')}/{ma.get('long_hold_loser_cohort_count')}",
        f"2. MFE<0.5% improved: {ma.get('mfe_lt_0p5_improved_count')}/{ma.get('mfe_lt_0p5_cohort_count')}",
        f"3–4. Focus symbols: see tables below",
        f"5. saved_loss_yen: ¥{ma.get('saved_loss_yen')}",
        f"6. lost_upside_yen: ¥{ma.get('lost_upside_yen')}",
        f"7. net_delta_yen: ¥{ma.get('net_delta_yen')}",
        f"8. affected_trade_count: {ma.get('affected_trade_count')}",
        f"9. long_hold_loser_count: {ma.get('long_hold_loser_count')}",
        "",
        "## Baseline",
        "",
        f"| total_pnl_yen_100 | ¥{baseline.get('total_pnl_yen_100')} |",
        f"| profit_factor | {baseline.get('profit_factor')} |",
        f"| long_hold_loser_count | {baseline.get('long_hold_loser_count')} |",
        "",
        "## Phase402 reference",
        "",
        f"| net_delta | ¥{p402.get('net_delta_yen')} |",
        f"| 3905 damage | ¥{p402.get('symbol_3905_damage_yen')} |",
        f"| 4062 damage | ¥{p402.get('symbol_4062_damage_yen')} |",
        "",
    ]
    if best:
        lines.extend(
            [
                "## Best policy",
                "",
                f"- hold_sec={best.get('hold_sec_threshold')} max_mfe<{best.get('max_mfe_pct_threshold')}%",
                f"- current_pnl<{best.get('current_pnl_pct_threshold')}%",
                f"- high_update={best.get('high_update_mode')} vwap={best.get('vwap_dev_mode')}",
                f"- net_delta: ¥{best.get('net_delta_yen')} | adopt: {best.get('adopt_candidate')}",
                f"- long_hold_loser: {best.get('long_hold_loser_count')} (Δ{best.get('long_hold_loser_delta')})",
                f"- rescued in baseline cohort: {best.get('long_hold_loser_rescued_count')}/27",
                f"- 3905: ¥{best.get('symbol_3905_damage_yen')} | 4062: ¥{best.get('symbol_4062_damage_yen')}",
                "",
            ]
        )
    best3905 = summary.get("best_3905_vs_phase402_policy")
    if best3905:
        lines.extend(
            [
                "## Best 3905 preservation (vs Phase402)",
                "",
                f"- hold={best3905.get('hold_sec_threshold')}s mfe<{best3905.get('max_mfe_pct_threshold')}% "
                f"pnl<{best3905.get('current_pnl_pct_threshold')}%",
                f"- net_delta: ¥{best3905.get('net_delta_yen')} | 3905 damage: ¥{best3905.get('symbol_3905_damage_yen')}",
                f"- long_hold_loser_count: {best3905.get('long_hold_loser_count')}",
                "",
            ]
        )
    focus = summary.get("focus_symbol_analysis") or {}
    lines.extend(["### Good long holds", "", "| symbol | baseline | shadow | delta |", "|--------|----------|--------|-------|"])
    for row in focus.get("good_long_hold") or []:
        lines.append(
            f"| {row.get('symbol')} | ¥{row.get('baseline_pnl_yen_100')} | "
            f"¥{row.get('shadow_pnl_yen_100')} | ¥{row.get('delta_yen')} |"
        )
    lines.extend(["", "### Bad long holds", "", "| symbol | baseline | shadow | delta |", "|--------|----------|--------|-------|"])
    for row in focus.get("bad_long_hold") or []:
        lines.append(
            f"| {row.get('symbol')} | ¥{row.get('baseline_pnl_yen_100')} | "
            f"¥{row.get('shadow_pnl_yen_100')} | ¥{row.get('delta_yen')} |"
        )
    lines.extend(["", "## Constraints", "", "- shadow / research のみ", ""])
    return "\n".join(lines)


def run_phase404_shadow(
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

    policies = iter_policy_grid()
    session_cache: dict[str, dict[str, Any]] = {}
    trade_results: list[dict[str, Any]] = []

    for trade in accepted:
        ctx = _prepare_trade_context(trade, repo_root=repo_root, session_cache=session_cache, p90_hold=p90_hold)
        if ctx is None:
            continue
        shadow_by_policy: dict[str, dict[str, Any]] = {}
        for policy in policies:
            sim = simulate_no_progress_exit(
                ctx["tick_states"],
                entry_price=ctx["entry_price"],
                entry_ts=ctx["entry_ts"],
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
        "mfe_lt_0p5_loser_count": sum(1 for t in trade_results if t.get("is_mfe_lt_0p5_loser")),
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
    adopt_rows.sort(key=lambda r: (-float(r.get("net_delta_yen") or 0), float(r.get("symbol_3905_damage_yen") or -1e18)))

    ranked = sorted(
        [r for r in grid_rows if r.get("policy_id") != POLICY_BASELINE],
        key=lambda r: (-float(r.get("net_delta_yen") or 0), float(r.get("long_hold_loser_rescued_count") or 0)),
    )
    best_adopt = adopt_rows[0] if adopt_rows else None
    best_overall = ranked[0] if ranked else None
    best = best_adopt or best_overall

    better_3905 = [
        r
        for r in grid_rows
        if r.get("policy_id") != POLICY_BASELINE
        and float(r.get("symbol_3905_damage_yen") or -1e18) > float(PHASE402_REFERENCE["symbol_3905_damage_yen"])
        and float(r.get("net_delta_yen") or 0) > 0
    ]
    better_3905.sort(key=lambda r: -float(r.get("net_delta_yen") or 0))
    best_3905_policy = better_3905[0] if better_3905 else None

    trade_rows: list[dict[str, Any]] = []
    if best:
        pk = (
            f"{best['hold_sec_threshold']}|{best['max_mfe_pct_threshold']}|"
            f"{best['current_pnl_pct_threshold']}|{best['high_update_mode']}|{best['vwap_dev_mode']}"
        )
        best_policy = next(p for p in policies if p.grid_key == pk)
        for t in trade_results:
            if not (t.get("focus_symbol") or t.get("is_long_hold_loser") or t.get("is_mfe_lt_0p5_loser")):
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

    grid_path = output_dir / "phase404_no_progress_exit_grid.csv"
    trades_path_out = output_dir / "phase404_no_progress_exit_trades.csv"
    _write_csv(grid_path, grid_rows, GRID_FIELDS)
    _write_csv(trades_path_out, trade_rows, TRADE_FIELDS)

    focus_analysis = _focus_symbol_summary(trade_results, pk) if best else {"good_long_hold": [], "bad_long_hold": []}

    mandatory_analysis = {
        "long_hold_loser_cohort_count": baseline_metrics["long_hold_loser_count"],
        "long_hold_loser_rescued_count": best.get("long_hold_loser_rescued_count") if best else 0,
        "mfe_lt_0p5_cohort_count": baseline_metrics["mfe_lt_0p5_loser_count"],
        "mfe_lt_0p5_improved_count": best.get("mfe_lt_0p5_improved_count") if best else 0,
        "saved_loss_yen": best.get("saved_loss_yen") if best else 0,
        "lost_upside_yen": best.get("lost_upside_yen") if best else 0,
        "net_delta_yen": best.get("net_delta_yen") if best else 0,
        "affected_trade_count": best.get("affected_trade_count") if best else 0,
        "long_hold_loser_count": best.get("long_hold_loser_count") if best else baseline_metrics["long_hold_loser_count"],
    }

    verdict = "adopt_candidate_found" if best_adopt else "no_adopt_candidate"
    summary = {
        "phase": 404,
        "generated_at": _now_iso(),
        "period_start": period_start,
        "period_end": period_end,
        "source_trades": str(trades_path),
        "position_cap_accepted_trade_count": len(trade_results),
        "p90_hold_sec": p90_hold,
        "baseline": baseline_metrics,
        "phase402_reference": PHASE402_REFERENCE,
        "grid_row_count": len(grid_rows),
        "policy_variant_count": len(policies),
        "adopt_candidate_count": len(adopt_rows),
        "best_policy": best,
        "best_adopt_policy": best_adopt,
        "best_3905_vs_phase402_policy": best_3905_policy,
        "mandatory_analysis": {
            **mandatory_analysis,
            "long_hold_loser_count_note": (
                "rescued_count is within baseline 27 cohort; long_hold_loser_count can rise "
                "when other trades become p90+ losers under shadow exits"
            ),
        },
        "focus_symbol_analysis": focus_analysis,
        "verdict": verdict,
        "headline": _headline(best, best_adopt, mandatory_analysis),
    }

    summary_path = output_dir / "phase404_no_progress_exit_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    report_path = repo_root / "docs" / "operations" / "phase404_no_progress_exit_report.md"
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
    ma: Mapping[str, Any],
) -> str:
    if not best:
        return "Phase404: no progress exit — no results"
    tag = "ADOPT" if best_adopt else "no_adopt"
    return (
        f"Phase404: hold={best.get('hold_sec_threshold')}s mfe<{best.get('max_mfe_pct_threshold')}% "
        f"pnl<{best.get('current_pnl_pct_threshold')}% hi={best.get('high_update_mode')} "
        f"vwap={best.get('vwap_dev_mode')} delta=¥{best.get('net_delta_yen')} "
        f"rescued={ma.get('long_hold_loser_rescued_count')}/{ma.get('long_hold_loser_cohort_count')} "
        f"3905=¥{best.get('symbol_3905_damage_yen')} {tag}"
    )
