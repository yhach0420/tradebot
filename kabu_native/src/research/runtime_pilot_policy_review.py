"""
Phase 50: Runtime pilot policy what-if review (no new ENTRY/EXIT logic).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from unittest.mock import patch
from zoneinfo import ZoneInfo

from research.exposure_gate import REJECT_MAX_CONCURRENT, ExposureGate, ExposureGateConfig
from research.research_exit_criteria import _as_float
from research.small_paper_performance_review import (
    _load_events,
    _load_json,
    _parse_dt,
    _parse_ts,
    _profit_factor,
    quality_band,
    session_bucket_at,
)
from small_paper.discord_notifier import observer_tracker_config_from_pilot
from small_paper.observer_position_tracker import OBSERVER_EXIT, OBSERVER_TAKE

JST = ZoneInfo("Asia/Tokyo")

QUALITY_WHAT_IF = (0.55, 0.60, 0.65, 0.70, 0.75)
CAP_WHAT_IF = (3, 4, 5)
COMBINED_GRID = (
    (0.65, 3),
    (0.65, 4),
    (0.70, 3),
    (0.70, 4),
    (0.75, 3),
)

MIN_PF_CANDIDATE = 1.2
MIN_TRADES_CANDIDATE = 50
MAX_LOSS_ACCEPTABLE_PCT = -2.5
HIGH_QUALITY_FLOOR = 0.55
EARLY_TAKE_WARN_RATE_PCT = 50.0


def _build_price_index(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, list[tuple[float, float]]]:
    by_sym: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for e in events:
        if e.get("event_type") != "candidate":
            continue
        sym = str(e.get("symbol") or "")
        px = _as_float(e.get("current_price"))
        if sym and px and px > 0:
            by_sym[sym].append((_parse_ts(str(e.get("entry_time") or "")), float(px)))
    for sym in by_sym:
        by_sym[sym].sort(key=lambda x: x[0])
    return by_sym


def _virtual_hold_pnl(row: Mapping[str, Any], price_index: Mapping[str, list[tuple[float, float]]]) -> float:
    sym = str(row.get("symbol") or "")
    ent_ts = _parse_ts(str(row.get("entry_time") or ""))
    ex_ts = _parse_ts(str(row.get("exit_time") or "")) or ent_ts + 300
    entry_px = _as_float(row.get("current_price")) or 0.0
    if entry_px <= 0:
        return 0.0
    ticks = [px for ts, px in price_index.get(sym, []) if ent_ts <= ts <= ex_ts]
    exit_px = ticks[-1] if ticks else entry_px
    return round((exit_px - entry_px) / entry_px * 100.0, 4)


def _candidates_from_events(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(e) for e in events if e.get("event_type") == "candidate"]
    rows.sort(key=lambda r: int(r.get("message_index") or 0))
    return rows


def _trade_from_candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "profile": row.get("profile"),
        "symbol": row.get("symbol"),
        "entry_time": row.get("entry_time"),
        "exit_time": row.get("exit_time"),
        "trade_date": str(row.get("entry_time") or "")[:10],
        "pnl_pct": 0.0,
        "exit_reason": row.get("exit_reason") or "live_virtual_hold",
        "momentum_continuation_score": row.get("momentum_continuation_score"),
        "favorable_continuation": row.get("favorable_continuation"),
        "max_favorable_excursion_pct": row.get("rolling_mfe_pct") or row.get("max_favorable_excursion_pct"),
        "max_adverse_excursion_pct": row.get("rolling_mae_pct") or row.get("max_adverse_excursion_pct"),
        "max_continuation_duration": row.get("max_continuation_duration"),
        "continuation_quality_score": row.get("continuation_quality_score"),
    }


@dataclass
class PolicySimResult:
    min_quality: float
    max_concurrent: int
    accepted_count: int
    rejected_count: int
    metrics: dict[str, Any]


def _simulate_policy(
    candidates: Sequence[Mapping[str, Any]],
    *,
    min_quality: float,
    max_concurrent: int,
    profile: str,
    price_index: Mapping[str, list[tuple[float, float]]],
    baseline_cap: int = 3,
    allowed_windows: Optional[Sequence[Any]] = None,
) -> PolicySimResult:
    cfg = ExposureGateConfig(
        profile=profile,
        min_continuation_quality=min_quality,
        max_concurrent_positions=max_concurrent,
        reject_below_quality=True,
    )
    gate = ExposureGate(cfg, allowed_windows=allowed_windows)
    accepted_rows: list[dict[str, Any]] = []
    reject_reasons: Counter[str] = Counter()
    hq_blocked = 0
    eval_count = 0
    peak_open = 0
    saturation_evals = 0

    for row in candidates:
        trade = _trade_from_candidate(row)
        q = float(row.get("continuation_quality_score") or 0)
        eval_count += 1
        if len(gate.state.open_slots) >= max_concurrent:
            saturation_evals += 1
        decision = gate.evaluate_entry(trade)
        if decision.accept:
            # Gate state: match live pilot (virtual hold pnl not applied at accept time).
            gate.record_accepted(trade)
            pnl = _virtual_hold_pnl(row, price_index)
            acc = dict(row)
            acc["realized_pnl_pct"] = pnl
            acc["gate_reject_reason"] = ""
            accepted_rows.append(acc)
            peak_open = max(peak_open, len(gate.state.open_slots))
        else:
            reason = decision.reason or "unknown"
            reject_reasons[reason] += 1
            if reason == REJECT_MAX_CONCURRENT and q >= HIGH_QUALITY_FLOOR:
                hq_blocked += 1

    pnls = [float(r["realized_pnl_pct"]) for r in accepted_rows]
    symbols = {str(r.get("symbol")) for r in accepted_rows}
    all_symbols = {str(r.get("symbol")) for r in candidates}
    sym_counts = Counter(str(r.get("symbol")) for r in accepted_rows)
    top_sym, top_n = sym_counts.most_common(1)[0] if sym_counts else ("", 0)

    cumulative = 0.0
    peak_cum = 0.0
    max_dd = 0.0
    max_consec_loss = 0
    cur_loss = 0
    for p in pnls:
        cumulative += p
        peak_cum = max(peak_cum, cumulative)
        max_dd = min(max_dd, cumulative - peak_cum)
        if p < 0:
            cur_loss += 1
            max_consec_loss = max(max_consec_loss, cur_loss)
        else:
            cur_loss = 0

    tier_dist = Counter(quality_band(float(r.get("continuation_quality_score") or 0)) for r in accepted_rows)
    bucket_dist = Counter(
        session_bucket_at(_parse_dt(str(r.get("entry_time") or ""))) for r in accepted_rows
    )

    metrics = {
        "trade_count": len(accepted_rows),
        "evaluations": eval_count,
        "realized_pnl_sum_pct": round(sum(pnls), 4) if pnls else 0.0,
        "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
        "profit_factor": round(_profit_factor(pnls), 4)
        if _profit_factor(pnls) not in (None, float("inf"))
        else _profit_factor(pnls),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
        "max_loss_pct": round(min(pnls), 4) if pnls else None,
        "max_gain_pct": round(max(pnls), 4) if pnls else None,
        "symbols_coverage_count": len(symbols),
        "symbols_coverage_pct": round(100.0 * len(symbols) / max(1, len(all_symbols)), 2),
        "top_symbol": top_sym,
        "top_symbol_concentration_pct": round(100.0 * top_n / max(1, len(accepted_rows)), 2),
        "session_bucket_distribution": dict(bucket_dist),
        "accepted_quality_tier_drift": dict(tier_dist),
        "reject_reason_counts": dict(reject_reasons),
        "high_quality_blocked_count": hq_blocked,
        "high_quality_blocked_rate_pct": round(100.0 * hq_blocked / max(1, eval_count), 2),
        "peak_open_slots": peak_open,
        "concurrent_saturation_rate_pct": round(100.0 * saturation_evals / max(1, eval_count), 2),
        "drawdown_proxy_pct": round(max_dd, 4),
        "max_consecutive_losers": max_consec_loss,
        "exposure_risk_note": "drawdown_proxy from virtual-hold PnL sequence; not live fills",
    }

    if baseline_cap == max_concurrent and min_quality == 0.55:
        metrics["baseline_comparison"] = "current_production_policy"

    return PolicySimResult(
        min_quality=min_quality,
        max_concurrent=max_concurrent,
        accepted_count=len(accepted_rows),
        rejected_count=sum(reject_reasons.values()),
        metrics=metrics,
    )


def _policy_row(sim: PolicySimResult, *, scenario: str) -> dict[str, Any]:
    m = sim.metrics
    return {
        "scenario": scenario,
        "min_quality": sim.min_quality,
        "max_concurrent": sim.max_concurrent,
        "trade_count": m.get("trade_count"),
        "accepted_count": sim.accepted_count,
        "avg_pnl_pct": m.get("avg_pnl_pct"),
        "profit_factor": m.get("profit_factor"),
        "win_rate": m.get("win_rate"),
        "max_loss_pct": m.get("max_loss_pct"),
        "symbols_coverage_pct": m.get("symbols_coverage_pct"),
        "top_symbol_concentration_pct": m.get("top_symbol_concentration_pct"),
        "high_quality_blocked_count": m.get("high_quality_blocked_count"),
        "concurrent_saturation_rate_pct": m.get("concurrent_saturation_rate_pct"),
        "peak_open_slots": m.get("peak_open_slots"),
        "drawdown_proxy_pct": m.get("drawdown_proxy_pct"),
        "max_consecutive_losers": m.get("max_consecutive_losers"),
        "accepted_quality_tier_drift": json.dumps(m.get("accepted_quality_tier_drift") or {}, ensure_ascii=False),
        "session_bucket_distribution": json.dumps(m.get("session_bucket_distribution") or {}, ensure_ascii=False),
    }


def _build_take_observer_review(
    events: Sequence[Mapping[str, Any]],
    *,
    pilot_config: Any,
    poll_interval_sec: float,
) -> dict[str, Any]:
    """Detailed TAKE signal review (observation only — not trade execution)."""
    import small_paper.observer_position_tracker as ot

    from small_paper.observer_position_tracker import ObserverPositionTracker

    tracker = ObserverPositionTracker(observer_tracker_config_from_pilot(pilot_config))
    ordered = sorted(events, key=lambda e: int(e.get("message_index") or 0))
    price_index = _build_price_index(events)
    mono = [0.0]
    take_rows: list[dict[str, Any]] = []
    exit_by_sym: dict[str, dict[str, Any]] = {}

    def _mono() -> float:
        return mono[0]

    for ev in ordered:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        ent_raw = str(ev.get("entry_time") or "")
        as_of = _parse_dt(ent_raw) if ent_raw else datetime.now(JST)
        mono[0] += max(poll_interval_sec, 0.001)
        trade = dict(ev)
        price = _as_float(ev.get("current_price"))

        with patch.object(ot.time, "monotonic", _mono):
            with patch.object(ot, "datetime") as mdt:
                mdt.now.return_value = as_of
                mdt.combine = datetime.combine
                mdt.fromisoformat = datetime.fromisoformat

                if ev.get("event_type") == "accepted" and price and price > 0:
                    tracker.register_entry(
                        trade=trade,
                        payload=trade,
                        quality_tier=str(ev.get("quality_tier") or ""),
                        entry_price=float(price),
                    )
                elif ev.get("event_type") == "candidate" and tracker.has_open(sym):
                    for oe in tracker.on_tick(
                        symbol=sym,
                        trade=trade,
                        payload=trade,
                        current_price=price,
                        session_bucket=session_bucket_at(as_of),
                    ):
                        if oe.kind == OBSERVER_TAKE:
                            ctx = oe.context
                            take_rows.append(
                                {
                                    "symbol": sym,
                                    "take_time": ent_raw,
                                    "take_pnl_pct": ctx.get("unrealized_pnl_pct"),
                                    "take_quality": ctx.get("continuation_quality"),
                                    "take_reason": ctx.get("take_reason"),
                                    "entry_price": ctx.get("entry_price"),
                                    "peak_pnl_at_take": ctx.get("peak_pnl_pct"),
                                }
                            )
                        elif oe.kind == OBSERVER_EXIT:
                            exit_by_sym[sym] = {
                                "exit_time": ent_raw,
                                "exit_pnl_pct": ctx.get("unrealized_pnl_pct", ctx.get("realized_pnl_pct")),
                                "exit_reason": ctx.get("exit_reason"),
                            }

    with patch.object(ot.time, "monotonic", _mono):
        with patch.object(ot, "datetime") as mdt:
            mdt.now.return_value = datetime.now(JST)
            mdt.combine = datetime.combine
            for oe in tracker.close_all(reason="policy_review_end"):
                if oe.kind == OBSERVER_EXIT:
                    exit_by_sym[oe.symbol] = {
                        "exit_time": oe.context.get("timestamp"),
                        "exit_pnl_pct": oe.context.get("realized_pnl_pct"),
                        "exit_reason": oe.context.get("exit_reason"),
                    }

    extended = 0
    early_warn = 0
    for i, tr in enumerate(take_rows):
        sym = str(tr["symbol"])
        take_ts = _parse_ts(str(tr.get("take_time") or ""))
        entry_px = float(tr.get("entry_price") or 0)
        take_pnl = float(tr.get("take_pnl_pct") or 0)
        post = [px for ts, px in price_index.get(sym, []) if ts >= take_ts]
        max_up_after = 0.0
        if entry_px > 0 and post:
            max_up_after = round(max((p - entry_px) / entry_px * 100.0 for p in post), 4)
        extended_flag = max_up_after > take_pnl + 0.05
        if extended_flag:
            extended += 1
        if take_pnl < max_up_after * 0.5 and max_up_after > 0.1:
            early_warn += 1
        ex = exit_by_sym.get(sym, {})
        exit_pnl = _as_float(ex.get("exit_pnl_pct"))
        tr.update(
            {
                "max_upside_after_take_pct": max_up_after,
                "extended_after_take": extended_flag,
                "early_take_warning": extended_flag,
                "exit_time": ex.get("exit_time"),
                "exit_pnl_pct": exit_pnl,
                "take_to_exit_pnl_delta": round((exit_pnl or 0) - take_pnl, 4) if exit_pnl is not None else None,
            }
        )
        take_rows[i] = tr

    n_take = len(take_rows)
    return {
        "phase": 50,
        "note": "TAKE is an observer notification signal only — not a sell/order instruction.",
        "take_count": n_take,
        "take_extended_after_take_count": extended,
        "take_extended_after_take_rate_pct": round(100.0 * extended / max(1, n_take), 2),
        "early_take_warning_count": early_warn,
        "early_take_warning_rate_pct": round(100.0 * early_warn / max(1, n_take), 2),
        "avg_max_upside_after_take_pct": round(
            statistics.mean(float(r.get("max_upside_after_take_pct") or 0) for r in take_rows), 4
        )
        if take_rows
        else None,
        "avg_take_to_exit_pnl_delta": round(
            statistics.mean(
                float(r["take_to_exit_pnl_delta"])
                for r in take_rows
                if r.get("take_to_exit_pnl_delta") is not None
            ),
            4,
        )
        if any(r.get("take_to_exit_pnl_delta") is not None for r in take_rows)
        else None,
        "take_events_sample": take_rows[:50],
        "guidance": [
            "High extended_after_take_rate suggests TAKE fires before local peak — tune observer only.",
            "Compare take_pnl_pct vs exit_pnl_pct; large positive delta means late EXIT vs early TAKE warning.",
        ],
    }


def _score_policy_candidate(m: Mapping[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    trade_count = int(m.get("trade_count") or 0)
    avg_pnl = float(m.get("avg_pnl_pct") or 0)
    pf = m.get("profit_factor")
    pf_val = float(pf) if isinstance(pf, (int, float)) else 0.0
    max_loss = float(m.get("max_loss_pct") or 0)
    hq_block_rate = float(m.get("high_quality_blocked_rate_pct") or 0)

    if trade_count < MIN_TRADES_CANDIDATE:
        failures.append("trade_count_below_50")
    if avg_pnl <= 0:
        failures.append("avg_pnl_not_positive")
    if pf_val < MIN_PF_CANDIDATE:
        failures.append("profit_factor_below_1_2")
    if max_loss < MAX_LOSS_ACCEPTABLE_PCT:
        failures.append("max_loss_exceeds_threshold")
    if hq_block_rate > 25.0:
        failures.append("high_quality_over_blocked")
    dd = float(m.get("drawdown_proxy_pct") or 0)
    if dd < -5.0:
        failures.append("drawdown_proxy_high")

    return len(failures) == 0, failures


def _recommend_policy(grid_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for row in grid_rows:
        ok, failures = _score_policy_candidate(row)
        entry = {
            "scenario": row.get("scenario"),
            "min_quality": row.get("min_quality"),
            "max_concurrent": row.get("max_concurrent"),
            "trade_count": row.get("trade_count"),
            "profit_factor": row.get("profit_factor"),
            "avg_pnl_pct": row.get("avg_pnl_pct"),
            "high_quality_blocked_count": row.get("high_quality_blocked_count"),
            "eligible": ok,
            "failures": failures,
        }
        if ok:
            candidates.append(entry)

    candidates.sort(
        key=lambda r: (float(r.get("profit_factor") or 0), float(r.get("avg_pnl_pct") or 0)),
        reverse=True,
    )

    baseline = next((r for r in grid_rows if float(r.get("min_quality", 0)) == 0.55 and int(r.get("max_concurrent", 0)) == 3), None)

    return {
        "recommend_policy_candidate": candidates[0] if candidates else None,
        "eligible_candidates": candidates,
        "baseline_policy_0_55_cap3": baseline,
        "live_resume_guidance": {
            "keep_threshold_0_55": bool(
                baseline and float(baseline.get("profit_factor") or 0) >= MIN_PF_CANDIDATE
            ),
            "consider_raise_threshold_to_0_65": any(
                float(c.get("min_quality") or 0) >= 0.65 for c in candidates
            ),
            "consider_cap_4_if_blocked": any(int(c.get("max_concurrent") or 0) >= 4 for c in candidates),
            "take_observer": "Review take_observer_review.json before live; do not treat TAKE as auto-exit.",
        },
        "note": "What-if only — do not change production yaml without explicit approval.",
    }


def run_runtime_policy_review(
    session_dir: Path,
    *,
    pilot_config: Any,
    profile: Optional[str] = None,
    poll_interval_sec: Optional[float] = None,
) -> dict[str, Any]:
    session_dir = session_dir.resolve()
    summary = _load_json(session_dir / "small_paper_summary.json")
    events = _load_events(session_dir)
    candidates = _candidates_from_events(events)
    price_index = _build_price_index(events)
    prof = profile or str(summary.get("profile") or pilot_config.profile)
    interval = poll_interval_sec if poll_interval_sec is not None else float(summary.get("poll_interval_sec") or 5.0)
    allowed_windows = pilot_config.allowed_windows() if pilot_config else None

    take_review = _build_take_observer_review(events, pilot_config=pilot_config, poll_interval_sec=interval)

    review: dict[str, Any] = {
        "phase": 50,
        "mode": "runtime_pilot_policy_review",
        "session_dir": str(session_dir),
        "what_if_only": True,
        "production_policy_unchanged": {
            "min_continuation_quality": 0.55,
            "max_concurrent_positions": 3,
            "order_enabled": False,
            "v13_frozen": True,
        },
        "quality_threshold_what_if": [],
        "max_concurrent_what_if": [],
        "combined_policy_grid": [],
    }

    for q in QUALITY_WHAT_IF:
        sim = _simulate_policy(
            candidates,
            min_quality=q,
            max_concurrent=3,
            profile=prof,
            price_index=price_index,
            allowed_windows=allowed_windows,
        )
        review["quality_threshold_what_if"].append(
            {"min_quality": q, "max_concurrent": 3, **sim.metrics, "policy_key": f"q{q}_cap3"}
        )
    review["max_concurrent_what_if"] = []
    for cap in CAP_WHAT_IF:
        sim = _simulate_policy(
            candidates,
            min_quality=0.55,
            max_concurrent=cap,
            profile=prof,
            price_index=price_index,
            allowed_windows=allowed_windows,
        )
        review["max_concurrent_what_if"].append(
            {"min_quality": 0.55, "max_concurrent": cap, **sim.metrics, "policy_key": f"q0.55_cap{cap}"}
        )
    review["combined_policy_grid"] = []
    for q, cap in COMBINED_GRID:
        sim = _simulate_policy(
            candidates,
            min_quality=q,
            max_concurrent=cap,
            profile=prof,
            price_index=price_index,
            allowed_windows=allowed_windows,
        )
        review["combined_policy_grid"].append(
            {"min_quality": q, "max_concurrent": cap, **sim.metrics, "policy_key": f"q{q}_cap{cap}"}
        )

    review["_grid_csv_rows"] = []
    for block, scenario in (
        (review["quality_threshold_what_if"], "quality_threshold_what_if"),
        (review["max_concurrent_what_if"], "max_concurrent_what_if"),
        (review["combined_policy_grid"], "combined_grid"),
    ):
        for m in block:
            review["_grid_csv_rows"].append(
                {
                    "scenario": scenario,
                    "min_quality": m.get("min_quality"),
                    "max_concurrent": m.get("max_concurrent"),
                    "trade_count": m.get("trade_count"),
                    "avg_pnl_pct": m.get("avg_pnl_pct"),
                    "profit_factor": m.get("profit_factor"),
                    "win_rate": m.get("win_rate"),
                    "max_loss_pct": m.get("max_loss_pct"),
                    "symbols_coverage_pct": m.get("symbols_coverage_pct"),
                    "top_symbol_concentration_pct": m.get("top_symbol_concentration_pct"),
                    "high_quality_blocked_count": m.get("high_quality_blocked_count"),
                    "concurrent_saturation_rate_pct": m.get("concurrent_saturation_rate_pct"),
                    "peak_open_slots": m.get("peak_open_slots"),
                    "drawdown_proxy_pct": m.get("drawdown_proxy_pct"),
                    "max_consecutive_losers": m.get("max_consecutive_losers"),
                    "accepted_quality_tier_drift": json.dumps(
                        m.get("accepted_quality_tier_drift") or {}, ensure_ascii=False
                    ),
                    "session_bucket_distribution": json.dumps(
                        m.get("session_bucket_distribution") or {}, ensure_ascii=False
                    ),
                }
            )

    review["_take_observer_review"] = take_review
    review["take_observer_review_summary"] = {
        "take_extended_after_take_rate_pct": take_review.get("take_extended_after_take_rate_pct"),
        "early_take_warning_rate_pct": take_review.get("early_take_warning_rate_pct"),
        "take_count": take_review.get("take_count"),
        "note": take_review.get("note"),
    }
    review["recommendation"] = _recommend_policy(review["_grid_csv_rows"])
    return review


def write_runtime_policy_review(session_dir: Path, review: Mapping[str, Any]) -> dict[str, Path]:
    session_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}

    public = {k: v for k, v in review.items() if not k.startswith("_")}
    json_path = session_dir / "runtime_policy_review.json"
    json_path.write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    out["json"] = json_path

    grid = review.get("_grid_csv_rows") or []
    if grid:
        csv_path = session_dir / "runtime_policy_grid.csv"
        fields = list(grid[0].keys())
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in grid:
                w.writerow(r)
        out["grid_csv"] = csv_path

    take = review.get("_take_observer_review") or {}
    take_path = session_dir / "take_observer_review.json"
    take_path.write_text(json.dumps(take, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    out["take_json"] = take_path

    return out


def build_and_write_runtime_policy_review(
    session_dir: Path,
    *,
    pilot_config: Any,
    poll_interval_sec: Optional[float] = None,
) -> dict[str, Any]:
    review = run_runtime_policy_review(
        session_dir, pilot_config=pilot_config, poll_interval_sec=poll_interval_sec
    )
    paths = write_runtime_policy_review(session_dir, review)
    public = {k: v for k, v in review.items() if not k.startswith("_")}
    public["output_files"] = {k: str(v) for k, v in paths.items()}
    paths["json"].write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return public
