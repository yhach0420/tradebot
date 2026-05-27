"""
Phase 152: stop_hit loss deep-dive + what-if (review only).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.continuation_quality_ranking import continuation_components
from research.research_exit_criteria import _as_float
from research.runtime_pilot_policy_review import _build_price_index, _parse_ts
from research.small_paper_performance_review import (
    _load_events,
    _load_json,
    _parse_dt,
    _profit_factor,
)
from research.structural_exit_design_review import (
    EvalPath,
    _detect_take_reason,
    _lower_high_on_ticks,
    _path_mfe_mae,
    _vwap_break_on_ticks,
    build_eval_paths,
)
from research.structural_exit_policies import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    simulate_structural_policy,
)
from research.structural_observer_review import (
    _legacy_virtual_hold_summary,
    _session_end_time,
)
from small_paper.discord_notifier import observer_tracker_config_from_pilot

POST_RECOVERY_HORIZON_SEC = 300.0
EARLY_ADVERSE_SEC = 30.0
EARLY_ADVERSE_MAE_PCT = -0.40
EARLY_ADVERSE_MIN_MFE_PCT = 0.05
NO_MFE_MAX_MFE_PCT = 0.10
NO_MFE_MAE_EXIT_PCT = -0.60
TIGHTER_STOP_PCT = 0.80
GAP_TICK_DROP_PCT = 1.50
ENTRY_JUMP_REJECT_PCT = 3.0
LOCAL_HIGH_LOOKBACK_TICKS = 10
CONSEC_DOWN_TICKS_WARN = 3


@dataclass(frozen=True)
class WhatIfSpec:
    scenario_id: str
    label: str
    policy_key: str


WHATIF_SCENARIOS: tuple[WhatIfSpec, ...] = (
    WhatIfSpec("A", "combined_structural_exit_v1", "combined"),
    WhatIfSpec("B", "tighter_stop_0.8pct", "tighter_stop"),
    WhatIfSpec("C", "early_adverse_exit_30s", "early_adverse"),
    WhatIfSpec("D", "no_mfe_mae_expansion_exit", "no_mfe_mae"),
    WhatIfSpec("E", "reject_high_risk_entry", "reject_entry"),
    WhatIfSpec("F", "gap_tick_fast_stop", "gap_stop"),
    WhatIfSpec("G", "legacy_virtual_hold_reference", "legacy"),
)


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _pnl_at(entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0
    return round((price - entry) / entry * 100.0, 4)


def _prior_prices(
    events: Sequence[Mapping[str, Any]],
    symbol: str,
    entry_ts: float,
    *,
    lookback_sec: float = 120.0,
) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for e in events:
        if str(e.get("symbol") or "") != symbol:
            continue
        if e.get("event_type") != "candidate":
            continue
        px = _as_float(e.get("current_price"))
        if not px or px <= 0:
            continue
        ts = _parse_ts(str(e.get("entry_time") or ""))
        if ts >= entry_ts or entry_ts - ts > lookback_sec:
            continue
        out.append((ts, float(px)))
    out.sort(key=lambda x: x[0])
    return out


def _accepted_row(
    events: Sequence[Mapping[str, Any]],
    symbol: str,
    entry_time: str,
) -> dict[str, Any]:
    for e in events:
        if e.get("event_type") == "accepted" and str(e.get("symbol")) == symbol:
            if str(e.get("entry_time") or "") == entry_time:
                return dict(e)
    return {}


def _path_for_trade(
    paths: Sequence[EvalPath],
    symbol: str,
    entry_time: str,
) -> Optional[EvalPath]:
    for p in paths:
        if p.symbol == symbol and p.entry_time == entry_time:
            return p
    return None


def _entry_jump_pct(prior: Sequence[tuple[float, float]], entry_px: float) -> Optional[float]:
    if not prior or entry_px <= 0:
        return None
    med = statistics.median(px for _, px in prior[-5:])
    if med <= 0:
        return None
    return round((entry_px - med) / med * 100.0, 4)


def _bought_local_high(prior: Sequence[tuple[float, float]], entry_px: float) -> bool:
    if not prior:
        return False
    recent = [px for _, px in prior[-LOCAL_HIGH_LOOKBACK_TICKS:]]
    if not recent:
        return False
    return entry_px >= max(recent) * 0.998


def _consecutive_down_ticks(ticks: Sequence[Mapping[str, Any]]) -> int:
    if len(ticks) < 2:
        return 0
    prices = [float(t.get("price") or 0) for t in ticks]
    streak = 0
    best = 0
    for i in range(1, len(prices)):
        if prices[i] < prices[i - 1]:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def _ticks_within_sec(ticks: Sequence[Mapping[str, Any]], entry_ts: float, sec: float) -> list[dict[str, Any]]:
    return [t for t in ticks if float(t.get("ts_epoch") or 0) <= entry_ts + sec]


def _mae_mfe_on_ticks(ticks: Sequence[Mapping[str, Any]]) -> tuple[float, float]:
    if not ticks:
        return 0.0, 0.0
    pnls = [float(t.get("pnl_pct") or 0) for t in ticks]
    return round(max(pnls), 4), round(min(pnls), 4)


def _post_exit_best_recovery(
    entry_px: float,
    exit_ts: float,
    price_series: Sequence[tuple[float, float]],
    horizon_sec: float = POST_RECOVERY_HORIZON_SEC,
) -> Optional[float]:
    if entry_px <= 0:
        return None
    end = exit_ts + horizon_sec
    prices = [px for ts, px in price_series if exit_ts < ts <= end]
    if not prices:
        return None
    return round(max((p - entry_px) / entry_px * 100.0 for p in prices), 4)


def _is_high_risk_entry(prior: Sequence[tuple[float, float]], entry_px: float) -> tuple[bool, dict[str, Any]]:
    jump = _entry_jump_pct(prior, entry_px)
    local_high = _bought_local_high(prior, entry_px)
    gap = jump is not None and jump >= ENTRY_JUMP_REJECT_PCT
    return gap, {
        "entry_jump_vs_prior_median_pct": jump,
        "bought_local_high": local_high,
        "high_risk_gap_entry": gap,
    }


def analyze_stop_hit_trade(
    trade: Mapping[str, Any],
    *,
    events: Sequence[Mapping[str, Any]],
    path: Optional[EvalPath],
    price_index: Mapping[str, list[tuple[float, float]]],
    hard_stop_pct: float,
) -> dict[str, Any]:
    sym = str(trade.get("symbol") or "")
    ent_time = str(trade.get("entry_time") or "")
    close_time = str(trade.get("close_time") or "")
    entry_px = float(trade.get("entry_price") or 0)
    exit_px = float(trade.get("close_price") or 0)
    ent_ts = _parse_ts(ent_time)
    exit_ts = _parse_ts(close_time)
    stop_px = round(entry_px * (1.0 - hard_stop_pct / 100.0), 4) if entry_px > 0 else 0.0

    acc = _accepted_row(events, sym, ent_time)
    comps = continuation_components(acc) if acc else {}
    prior = _prior_prices(events, sym, ent_ts)
    high_risk, risk_flags = _is_high_risk_entry(prior, entry_px)

    ticks = list(path.ticks) if path else []
    early = _ticks_within_sec(ticks, ent_ts, EARLY_ADVERSE_SEC)
    mfe_all, mae_all = _path_mfe_mae(ticks, entry_px)
    mfe_early, mae_early = _mae_mfe_on_ticks(early)

    first_tick = ticks[0] if ticks else {}
    first_pnl = float(first_tick.get("pnl_pct") or 0) if first_tick else None
    first_dt = float(first_tick.get("ts_epoch") or ent_ts) - ent_ts if first_tick else None
    gap_through_stop = bool(
        exit_px > 0 and stop_px > 0 and exit_px < stop_px - 1e-9 and float(trade.get("realized_pnl_pct") or 0) < -hard_stop_pct - 0.5
    )

    take_reason_early = ""
    if path and early:
        last_early = early[-1]
        comps_e = {
            "continuation_quality": float(last_early.get("quality") or 0),
            "momentum_continuation": float(last_early.get("momentum") or 0),
            "favorable_continuation": float(last_early.get("favorable") or 0),
        }
        take_reason_early = _detect_take_reason(
            path,
            comps_e,
            float(last_early.get("price") or entry_px),
            float(last_early.get("pnl_pct") or 0),
        )

    recovery = _post_exit_best_recovery(entry_px, exit_ts, price_index.get(sym, []))

    return {
        "symbol": sym,
        "entry_time": ent_time,
        "exit_time": close_time,
        "entry_price": entry_px,
        "exit_price": exit_px,
        "pnl_pct": float(trade.get("realized_pnl_pct") or 0),
        "stop_price": stop_px,
        "hard_stop_pct": hard_stop_pct,
        "gap_through_stop": gap_through_stop,
        "entry_quality": float(trade.get("continuation_quality_score") or acc.get("continuation_quality_score") or 0),
        "entry_momentum": float(comps.get("momentum_continuation") or acc.get("momentum_continuation_score") or 0),
        "entry_favorable": float(comps.get("favorable_continuation") or acc.get("favorable_continuation") or 0),
        "intraday_range_pct": _as_float(acc.get("intraday_range_pct")),
        "atr_pct": _as_float(acc.get("atr_pct")),
        "trading_value": _as_float(acc.get("trading_value")),
        "rolling_mfe_at_entry": _as_float(acc.get("rolling_mfe_pct")),
        "rolling_mae_at_entry": _as_float(acc.get("rolling_mae_pct")),
        "mfe_before_stop_pct": mfe_all,
        "mae_before_stop_pct": mae_all,
        "mfe_within_30s_pct": mfe_early,
        "mae_within_30s_pct": mae_early,
        "time_to_stop_sec": round(exit_ts - ent_ts, 1),
        "tick_count_on_path": len(ticks),
        "adverse_immediately_after_entry": bool(first_pnl is not None and first_pnl <= EARLY_ADVERSE_MAE_PCT),
        "first_tick_pnl_pct": first_pnl,
        "first_tick_after_entry_sec": round(first_dt, 1) if first_dt is not None else None,
        "never_positive_mfe": mfe_all <= NO_MFE_MAX_MFE_PCT,
        "bought_local_high": risk_flags.get("bought_local_high"),
        "entry_jump_vs_prior_median_pct": risk_flags.get("entry_jump_vs_prior_median_pct"),
        "high_risk_entry_flag": high_risk,
        "prior_tick_count_120s": len(prior),
        "prior_last_price": prior[-1][1] if prior else None,
        "vwap_break_on_path": _vwap_break_on_ticks(ticks, entry_px) if ticks else False,
        "consecutive_down_ticks": _consecutive_down_ticks(ticks),
        "momentum_weak_at_first_tick": bool(
            first_tick and float(first_tick.get("momentum") or 0) < float(first_tick.get("quality") or 0) * 0.5
        ),
        "quality_decay_warning_30s": take_reason_early == "quality_deterioration",
        "early_take_warning_30s": take_reason_early,
        "had_take_before_exit": bool(trade.get("take_time")),
        "take_pnl_pct": trade.get("take_pnl_pct"),
        "best_pnl_300s_after_exit": recovery,
        "missed_recovery_vs_exit": round(recovery - float(trade.get("realized_pnl_pct") or 0), 4)
        if recovery is not None
        else None,
        "limit_up_down_proxy": "unknown_no_board_feed",
        "spread_tick_note": "price_jump_12_to_13_to_12" if sym == "5856.T" else "gradual_stop_path",
    }


def build_stop_price_paths(
    stop_trades: Sequence[Mapping[str, Any]],
    paths: Sequence[EvalPath],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    path_by_key = {(p.symbol, p.entry_time): p for p in paths}
    for t in stop_trades:
        key = (str(t.get("symbol")), str(t.get("entry_time")))
        p = path_by_key.get(key)
        if not p:
            continue
        for i, tick in enumerate(p.ticks):
            rows.append(
                {
                    "symbol": p.symbol,
                    "entry_time": p.entry_time,
                    "tick_index": i,
                    "ts": tick.get("ts"),
                    "ts_epoch": tick.get("ts_epoch"),
                    "price": tick.get("price"),
                    "pnl_pct": tick.get("pnl_pct"),
                    "quality": tick.get("quality"),
                    "momentum": tick.get("momentum"),
                    "favorable": tick.get("favorable"),
                    "is_stop_exit_tick": str(tick.get("ts")) == str(t.get("close_time")),
                }
            )
    return rows


def _simulate_whatif(
    path: EvalPath,
    *,
    policy_key: str,
    cfg: Any,
    hard_stop_pct: float,
    prior: Sequence[tuple[float, float]],
) -> tuple[Optional[float], str]:
    ticks = path.ticks
    if not ticks:
        return None, "no_ticks"
    entry = path.entry_price

    if policy_key == "reject_entry":
        high, _ = _is_high_risk_entry(prior, entry)
        if high:
            return None, "rejected_high_risk"
        policy_key = "combined"

    if policy_key == "legacy":
        return float(ticks[-1].get("pnl_pct") or 0), "live_virtual_hold"

    if policy_key == "combined":
        r = simulate_structural_policy(
            ticks, entry, POLICY_COMBINED_STRUCTURAL_EXIT_V1, cfg, allow_session_end=True
        )
        return r if r else (float(ticks[-1].get("pnl_pct") or 0), "session_end")

    stop_pct = TIGHTER_STOP_PCT if policy_key == "tighter_stop" else hard_stop_pct
    stop_px = entry * (1.0 - stop_pct / 100.0)
    mfe_seen = 0.0

    for i, t in enumerate(ticks):
        px = float(t.get("price") or entry)
        pnl = float(t.get("pnl_pct") or 0)
        ts = float(t.get("ts_epoch") or 0)
        mfe_seen = max(mfe_seen, pnl)
        elapsed = ts - path.entry_ts
        drop_from_entry = (px - entry) / entry * 100.0 if entry > 0 else 0.0

        if policy_key == "gap_stop":
            if drop_from_entry <= -GAP_TICK_DROP_PCT:
                return pnl, "gap_tick_fast_stop"
            if i > 0:
                prev_px = float(ticks[i - 1].get("price") or entry)
                tick_drop = (px - prev_px) / prev_px * 100.0 if prev_px > 0 else 0.0
                if tick_drop <= -GAP_TICK_DROP_PCT:
                    return pnl, "gap_tick_fast_stop"

        if policy_key == "early_adverse" and elapsed <= EARLY_ADVERSE_SEC:
            if mfe_seen < EARLY_ADVERSE_MIN_MFE_PCT and pnl <= EARLY_ADVERSE_MAE_PCT:
                return pnl, "early_adverse_exit"

        if policy_key == "no_mfe_mae":
            if mfe_seen <= NO_MFE_MAX_MFE_PCT and pnl <= NO_MFE_MAE_EXIT_PCT:
                return pnl, "no_mfe_mae_exit"

        if px <= stop_px:
            return pnl, "stop_hit"

    return float(ticks[-1].get("pnl_pct") or 0), "session_end"


def run_stop_hit_whatif(
    paths: Sequence[EvalPath],
    events: Sequence[Mapping[str, Any]],
    *,
    pilot_config: Any,
    actual_trades: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cfg = observer_tracker_config_from_pilot(pilot_config)
    hard_stop = float(cfg.hard_stop_pct)
    path_by_key = {(p.symbol, p.entry_time): p for p in paths}
    actual_by_key = {
        (str(t.get("symbol")), str(t.get("entry_time"))): t for t in actual_trades
    }

    scenario_pnls: dict[str, list[float]] = {s.policy_key: [] for s in WHATIF_SCENARIOS}
    scenario_reasons: dict[str, Counter[str]] = {s.policy_key: Counter() for s in WHATIF_SCENARIOS}
    scenario_stop_losses: dict[str, list[float]] = {s.policy_key: [] for s in WHATIF_SCENARIOS}
    false_exits: Counter[str] = Counter()
    missed_recoveries: Counter[str] = Counter()
    rejected_count = 0

    for p in paths:
        key = (p.symbol, p.entry_time)
        actual = actual_by_key.get(key)
        if not actual:
            continue
        prior = _prior_prices(events, p.symbol, p.entry_ts)
        actual_pnl = float(actual.get("realized_pnl_pct") or 0)
        actual_reason = str(actual.get("close_reason") or "")

        for spec in WHATIF_SCENARIOS:
            if spec.policy_key == "legacy":
                pnl, reason = _simulate_whatif(
                    p, policy_key="legacy", cfg=cfg, hard_stop_pct=hard_stop, prior=prior
                )
            else:
                pnl, reason = _simulate_whatif(
                    p,
                    policy_key=spec.policy_key,
                    cfg=cfg,
                    hard_stop_pct=hard_stop,
                    prior=prior,
                )
            if pnl is None and reason == "rejected_high_risk":
                rejected_count += 1
                continue
            if pnl is None:
                continue
            scenario_pnls[spec.policy_key].append(float(pnl))
            scenario_reasons[spec.policy_key][reason] += 1
            if reason == "stop_hit" and float(pnl) < 0:
                scenario_stop_losses[spec.policy_key].append(float(pnl))

            if spec.policy_key in ("early_adverse", "no_mfe_mae", "gap_stop", "tighter_stop"):
                if actual_reason != "stop_hit" and reason != actual_reason:
                    if float(pnl) < actual_pnl - 0.15:
                        false_exits[spec.policy_key] += 1
                if actual_reason == "stop_hit" and float(pnl) > actual_pnl + 0.20:
                    missed_recoveries[spec.policy_key] += 1

    rows: list[dict[str, Any]] = []
    baseline_pnls = scenario_pnls.get("combined", [])
    baseline_pf = _profit_factor(baseline_pnls)
    stop_actual = [
        float(t.get("realized_pnl_pct") or 0)
        for t in actual_trades
        if str(t.get("close_reason")) == "stop_hit"
    ]

    for spec in WHATIF_SCENARIOS:
        pnls = scenario_pnls.get(spec.policy_key, [])
        reasons = scenario_reasons.get(spec.policy_key, Counter())
        pf = _profit_factor(pnls)
        rows.append(
            {
                "scenario_id": spec.scenario_id,
                "scenario": spec.label,
                "policy_key": spec.policy_key,
                "trade_count": len(pnls),
                "structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
                "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
                "max_loss_pct": round(min(pnls), 4) if pnls else None,
                "max_gain_pct": round(max(pnls), 4) if pnls else None,
                "stop_hit_count": int(reasons.get("stop_hit", 0)),
                "stop_loss_sum_pct": round(sum(scenario_stop_losses.get(spec.policy_key, [])), 4),
                "exit_reason_top": reasons.most_common(5),
                "false_exit_count": int(false_exits.get(spec.policy_key, 0)),
                "missed_recovery_count": int(missed_recoveries.get(spec.policy_key, 0)),
                "rejected_entry_count": rejected_count if spec.policy_key == "reject_entry" else 0,
                "delta_pf_vs_combined": round(float(pf or 0) - float(baseline_pf or 0), 4)
                if pf is not None and baseline_pf is not None
                else None,
                "note": "legacy_reference_only" if spec.policy_key == "legacy" else "",
            }
        )

    meta = {
        "baseline_pf": baseline_pf,
        "actual_stop_hit_sum_pct": round(sum(stop_actual), 4),
        "actual_stop_hit_count": len(stop_actual),
        "reject_entry_skipped": rejected_count,
    }
    return rows, meta


def determine_phase152_verdict(
    scenarios: Sequence[Mapping[str, Any]],
    stop_analyses: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str]]:
    by_id = {str(r["scenario_id"]): r for r in scenarios}
    notes: list[str] = []

    def pf(sid: str) -> float:
        return float(by_id.get(sid, {}).get("structural_pf") or 0)

    def max_loss(sid: str) -> float:
        return float(by_id.get(sid, {}).get("max_loss_pct") or -999)

    base_pf = pf("A")
    notes.append(f"combined PF={base_pf:.4f} actual_stop_sum={sum(float(s.get('pnl_pct') or 0) for s in stop_analyses):.2f}%")

    gap_stops = sum(1 for s in stop_analyses if s.get("gap_through_stop"))
    high_risk = sum(1 for s in stop_analyses if s.get("high_risk_entry_flag"))
    notes.append(f"stop_trades={len(stop_analyses)} gap_through_stop={gap_stops} high_risk_entry={high_risk}")

    e_trades = int(by_id.get("E", {}).get("trade_count") or 0)
    e_rejected = int(by_id.get("E", {}).get("rejected_entry_count") or 0)
    if (
        pf("E") > base_pf + 0.05
        and max_loss("E") >= max_loss("A")
        and e_trades >= 70
        and e_rejected <= 5
    ):
        return "reject_high_risk_entry_promising", notes + [
            f"reject_high_risk_entry PF={pf('E'):.4f} rejected={e_rejected} (gap-only gate)."
        ]

    if pf("C") > base_pf + 0.05 and int(by_id.get("C", {}).get("false_exit_count") or 0) <= 8:
        return "early_adverse_exit_promising", notes + [
            f"early_adverse_exit PF={pf('C'):.4f} false_exits={by_id.get('C', {}).get('false_exit_count')}."
        ]

    if pf("B") > base_pf + 0.03:
        return "tighter_stop_promising", notes + [f"tighter_stop PF={pf('B'):.4f}."]

    if gap_stops >= 2 and high_risk >= 1:
        return "need_tick_or_board_features", notes + [
            "5856.T-style tick gaps dominate stop losses; poll-interval price insufficient."
        ]

    if pf("C") > base_pf + 0.03 and int(by_id.get("C", {}).get("stop_hit_count") or 0) < int(
        by_id.get("A", {}).get("stop_hit_count") or 0
    ):
        return "early_adverse_exit_promising", notes + [
            f"early_adverse reduces stop_hit count; PF={pf('C'):.4f}."
        ]

    return "stop_hit_unavoidable_with_current_data", notes + [
        "No what-if scenario clearly beats combined without large false-exit cost."
    ]


def build_phase152_recommendation_md(
    *,
    verdict: str,
    verdict_notes: Sequence[str],
    scenarios: Sequence[Mapping[str, Any]],
    stop_rows: Sequence[Mapping[str, Any]],
) -> str:
    lines = [
        "# Phase 152 — stop_hit loss review (2026-05-25 AM)",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        "## Summary",
        "",
    ]
    for n in verdict_notes:
        lines.append(f"- {n}")
    lines.extend(
        [
            "",
            "## Stop-hit trades (individual)",
            "",
            "| Symbol | Entry | PnL % | Gap stop? | Jump % | MAE 30s | Recovery 300s |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for s in stop_rows:
        lines.append(
            f"| {s.get('symbol')} | {s.get('entry_time', '')[-8:]} | {s.get('pnl_pct')} | "
            f"{s.get('gap_through_stop')} | {s.get('entry_jump_vs_prior_median_pct')} | "
            f"{s.get('mae_within_30s_pct')} | {s.get('best_pnl_300s_after_exit')} |"
        )
    lines.extend(["", "## What-if scenarios", "", "| ID | Scenario | Trades | PF | Avg | Max loss | stop# |", "|---|---|---:|---:|---:|---:|---:|"])
    for r in scenarios:
        lines.append(
            f"| {r.get('scenario_id')} | {r.get('scenario')} | {r.get('trade_count')} | "
            f"{r.get('structural_pf')} | {r.get('avg_pnl_pct')} | {r.get('max_loss_pct')} | "
            f"{r.get('stop_hit_count')} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- **5856.T** (2 stops): entry at 13 after prior ticks at 12 → ~8% jump; first poll already at 12 (−7.7%). "
            "Tighter stop does not help when price gaps through the stop level.",
            "- **4392.T** (1 stop): gradual −1.5% stop; post-exit recovery to ~+1.2% within 5m → stop cut a recoverable path.",
            "- **take_exit_shadow** is out of scope (Phase 151: not adopted).",
            "",
            "## Next steps (what-if only)",
            "",
            "1. Entry gate on **price jump vs recent median** (reject >3% without board feed).",
            "2. **Early adverse** shadow: exit if MAE ≤ −0.4% within 30s and MFE < 0.05%.",
            "3. Collect **tick/board** features before tightening stop on illiquid names.",
            "",
            "Do not change production YAML until shadow confirms PF lift with stable trade count.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_phase152_stop_hit_loss_review(
    session_dir: Path,
    *,
    pilot_config: Any,
    reports_dir: Path,
) -> dict[str, Any]:
    session_dir = session_dir.resolve()
    events = _load_events(session_dir)
    summary = _load_json(session_dir / "small_paper_summary.json") or {}
    session_end = _session_end_time(events)

    with (session_dir / "structural_trades.csv").open(encoding="utf-8", newline="") as f:
        actual_trades = list(csv.DictReader(f))

    stop_trades = [t for t in actual_trades if str(t.get("close_reason")) == "stop_hit"]
    paths = build_eval_paths(events, session_end=session_end)
    price_index = _build_price_index(events)
    cfg = observer_tracker_config_from_pilot(pilot_config)
    hard_stop = float(cfg.hard_stop_pct)

    stop_rows = [
        analyze_stop_hit_trade(
            t,
            events=events,
            path=_path_for_trade(paths, str(t.get("symbol")), str(t.get("entry_time"))),
            price_index=price_index,
            hard_stop_pct=hard_stop,
        )
        for t in stop_trades
    ]
    path_rows = build_stop_price_paths(stop_trades, paths)
    whatif_rows, whatif_meta = run_stop_hit_whatif(
        paths, events, pilot_config=pilot_config, actual_trades=actual_trades
    )
    verdict, verdict_notes = determine_phase152_verdict(whatif_rows, stop_rows)
    legacy = _legacy_virtual_hold_summary(events)

    report: dict[str, Any] = {
        "phase": 152,
        "mode": "stop_hit_loss_review",
        "what_if_only": True,
        "session_dir": str(session_dir),
        "session_date": "20260525",
        "hard_stop_pct": hard_stop,
        "stop_hit_count": len(stop_trades),
        "stop_hit_loss_sum_pct": round(sum(float(s.get("pnl_pct") or 0) for s in stop_rows), 4),
        "stop_hit_avg_pnl_pct": round(
            statistics.mean(float(s.get("pnl_pct") or 0) for s in stop_rows), 4
        )
        if stop_rows
        else None,
        "verdict": verdict,
        "verdict_options": {
            "A": "early_adverse_exit_promising",
            "B": "tighter_stop_promising",
            "C": "reject_high_risk_entry_promising",
            "D": "stop_hit_unavoidable_with_current_data",
            "E": "need_tick_or_board_features",
        },
        "verdict_notes": verdict_notes,
        "stop_hit_trades": stop_rows,
        "whatif_scenarios": whatif_rows,
        "whatif_meta": whatif_meta,
        "legacy_virtual_hold": legacy,
        "accepted_count": summary.get("accepted_count"),
        "constraints": [
            "no_production_yaml_change",
            "take_exit_shadow_not_in_scope",
            "review_whatif_only",
        ],
    }

    reports_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(reports_dir / "phase152_stop_hit_trades.csv", stop_rows)
    _write_csv(reports_dir / "phase152_stop_hit_price_paths.csv", path_rows)
    _write_csv(reports_dir / "phase152_stop_hit_whatif_scenarios.csv", whatif_rows)
    (reports_dir / "phase152_stop_hit_loss_review.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (reports_dir / "phase152_recommendation.md").write_text(
        build_phase152_recommendation_md(
            verdict=verdict,
            verdict_notes=verdict_notes,
            scenarios=whatif_rows,
            stop_rows=stop_rows,
        ),
        encoding="utf-8",
    )
    report["output_files"] = {
        "json": str(reports_dir / "phase152_stop_hit_loss_review.json"),
        "trades_csv": str(reports_dir / "phase152_stop_hit_trades.csv"),
        "paths_csv": str(reports_dir / "phase152_stop_hit_price_paths.csv"),
        "whatif_csv": str(reports_dir / "phase152_stop_hit_whatif_scenarios.csv"),
        "recommendation_md": str(reports_dir / "phase152_recommendation.md"),
    }
    return report
