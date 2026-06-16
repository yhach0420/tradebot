"""
Phase405: Time-based MFE / STOP boundary inference.

Inverse inference of time-bucket exit boundaries from Phase399 trades.
Research only — no Runtime / YAML / Entry / Exit changes.
"""

from __future__ import annotations

import bisect
import json
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key, _write_csv
from research.phase400_holding_time_audit import enrich_trade, load_phase399_trades, normalize_exit_reason
from research.phase401_long_hold_loser_forensic import (
    _accepted_lookup,
    _load_structural_lookup,
    _session_dir,
)
from research.phase402_time_decay_exit_shadow import _saved_lost_yen
from research.runtime_pilot_policy_review import _build_price_index
from research.small_paper_performance_review import _load_events

JST = ZoneInfo("Asia/Tokyo")
PERIOD_START = "20260529"
PERIOD_END = "20260615"

TIME_BUCKETS_MIN = (5, 10, 15, 20, 30, 45, 60)
MFE_EXIT_THRESHOLDS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0)
STOP_EXIT_THRESHOLDS = (-0.2, -0.4, -0.6, -0.8, -1.0, -1.2)
MFE_TRAIL_THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.8, 1.0)
TRAIL_GIVEBACK_FRAC = 0.5

INFERENCE_FIELDS = [
    "time_bucket_min",
    "rule_type",
    "threshold_pct",
    "trades_at_bucket",
    "trades_below_threshold",
    "final_loser_rate_below",
    "win_rate_below_threshold",
    "expected_delta_yen",
    "saved_loss_yen",
    "lost_upside_yen",
    "net_delta_yen",
    "affected_trade_count",
]

POLICY_FIELDS = [
    "time_bucket_min",
    "recommended_mfe_exit_threshold",
    "recommended_stop_threshold",
    "recommended_mfe_trail_threshold",
    "win_rate_below_mfe_threshold",
    "final_loser_rate_below_mfe",
    "expected_delta_yen_mfe_rule",
    "expected_delta_yen_stop_rule",
    "expected_delta_yen_combined_estimate",
    "saved_loss_yen_mfe",
    "lost_upside_yen_mfe",
    "affected_trade_count_mfe",
    "saved_loss_yen_stop",
    "lost_upside_yen_stop",
    "affected_trade_count_stop",
]

SNAPSHOT_FIELDS = [
    "day",
    "session",
    "symbol",
    "entry_time",
    "time_bucket_min",
    "current_pnl_pct",
    "max_mfe_so_far_pct",
    "mae_so_far_pct",
    "checkpoint_pnl_yen_100",
    "final_pnl_yen_100",
    "final_is_winner",
    "final_exit_reason",
    "remaining_hold_sec",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _pnl_pct(entry: float, px: float) -> float:
    if entry <= 0:
        return 0.0
    return round((px - entry) / entry * 100.0, 4)


def _price_at_ts(series: Sequence[tuple[float, float]], target_ts: float) -> Optional[float]:
    if not series:
        return None
    times = [t for t, _ in series]
    idx = bisect.bisect_left(times, target_ts)
    if idx >= len(series):
        return series[-1][1]
    if idx == 0:
        return series[0][1]
    prev_ts, prev_px = series[idx - 1]
    next_ts, next_px = series[idx]
    if abs(target_ts - prev_ts) <= abs(next_ts - target_ts):
        return prev_px
    return next_px


def _metrics_until(
    series: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    entry_price: float,
    until_ts: float,
) -> dict[str, float]:
    peak = 0.0
    trough = 0.0
    last_px = entry_price
    for ts, px in series:
        if ts < entry_ts:
            continue
        if ts > until_ts:
            break
        if px <= 0:
            continue
        pnl = _pnl_pct(entry_price, px)
        peak = max(peak, pnl)
        trough = min(trough, pnl)
        last_px = px
    current = _pnl_pct(entry_price, last_px)
    return {
        "current_pnl_pct": current,
        "max_mfe_so_far_pct": round(peak, 4),
        "mae_so_far_pct": round(trough, 4),
        "checkpoint_price": last_px,
    }


def _session_end_ts(series: Sequence[tuple[float, float]], fallback: float) -> float:
    if not series:
        return fallback
    return max(ts for ts, _ in series)


def _prepare_trades(
    accepted: Sequence[Mapping[str, Any]],
    *,
    repo_root: Path,
) -> list[dict[str, Any]]:
    session_cache: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []

    for trade in accepted:
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
            continue

        ent_ts = ent_dt.timestamp()
        series = cache["price_index"].get(sym, [])
        ex_dt = _parse_ts(str(trade.get("exit_time") or ""))
        hold_sec = float(trade.get("hold_sec") or 0.0)
        fallback_end = ex_dt.timestamp() if ex_dt else ent_ts + hold_sec

        from replay.pnl_yen import compute_pnl_yen_100

        baseline_yen = float(trade.get("pnl_yen_100_float") or 0.0)
        out.append(
            {
                "day": day,
                "session": session,
                "symbol": sym,
                "entry_time": entry_time,
                "exit_time": trade.get("exit_time"),
                "hold_sec": hold_sec,
                "entry_price": entry_px,
                "entry_ts": ent_ts,
                "price_series": series,
                "baseline_pnl_yen_100": baseline_yen,
                "final_is_winner": bool(trade.get("is_winner")),
                "final_is_loser": bool(trade.get("is_loser")),
                "final_exit_reason": normalize_exit_reason(str(trade.get("exit_reason") or "")),
            }
        )
    return out


def build_bucket_snapshot(
    trade: Mapping[str, Any],
    *,
    bucket_min: int,
) -> Optional[dict[str, Any]]:
    from replay.pnl_yen import compute_pnl_yen_100

    bucket_sec = bucket_min * 60.0
    hold_sec = float(trade.get("hold_sec") or 0.0)
    if hold_sec < bucket_sec:
        return None

    entry_ts = float(trade["entry_ts"])
    entry_px = float(trade["entry_price"])
    until_ts = entry_ts + bucket_sec
    metrics = _metrics_until(
        trade["price_series"],
        entry_ts=entry_ts,
        entry_price=entry_px,
        until_ts=until_ts,
    )
    ck_yen = round(compute_pnl_yen_100(entry_px, metrics["checkpoint_price"]), 2)

    return {
        "day": trade.get("day"),
        "session": trade.get("session"),
        "symbol": trade.get("symbol"),
        "entry_time": trade.get("entry_time"),
        "time_bucket_min": bucket_min,
        "current_pnl_pct": metrics["current_pnl_pct"],
        "max_mfe_so_far_pct": metrics["max_mfe_so_far_pct"],
        "mae_so_far_pct": metrics["mae_so_far_pct"],
        "checkpoint_pnl_yen_100": ck_yen,
        "final_pnl_yen_100": float(trade["baseline_pnl_yen_100"]),
        "final_is_winner": trade.get("final_is_winner"),
        "final_exit_reason": trade.get("final_exit_reason"),
        "remaining_hold_sec": round(hold_sec - bucket_sec, 2),
        "baseline_pnl_yen_100": float(trade["baseline_pnl_yen_100"]),
    }


def _evaluate_mfe_exit_rule(
    trades: Sequence[Mapping[str, Any]],
    snapshots: Sequence[Mapping[str, Any]],
    *,
    bucket_min: int,
    threshold: float,
) -> dict[str, Any]:
    snap_by_key = {
        (str(s.get("symbol")), str(s.get("entry_time"))): s for s in snapshots
    }
    baseline_pnls = [float(t["baseline_pnl_yen_100"]) for t in trades]
    shadow_pnls: list[float] = []
    below_rows: list[Mapping[str, Any]] = []
    affected = 0

    for t in trades:
        key = (str(t.get("symbol")), str(t.get("entry_time")))
        snap = snap_by_key.get(key)
        base = float(t["baseline_pnl_yen_100"])
        if snap is None:
            shadow_pnls.append(base)
            continue
        if float(snap["max_mfe_so_far_pct"]) < threshold:
            sh = float(snap["checkpoint_pnl_yen_100"])
            shadow_pnls.append(sh)
            below_rows.append(snap)
            if abs(sh - base) > 0.01:
                affected += 1
        else:
            shadow_pnls.append(base)

    saved, lost = _saved_lost_yen(baseline_pnls, shadow_pnls)
    net_delta = round(sum(shadow_pnls) - sum(baseline_pnls), 2)
    losers_below = sum(1 for s in below_rows if not s.get("final_is_winner"))
    win_below = sum(1 for s in below_rows if s.get("final_is_winner"))
    n_below = len(below_rows)
    trades_at_bucket = len(snapshots)

    return {
        "time_bucket_min": bucket_min,
        "rule_type": "mfe_exit_if_below",
        "threshold_pct": threshold,
        "trades_at_bucket": trades_at_bucket,
        "trades_below_threshold": n_below,
        "final_loser_rate_below": round(losers_below / n_below, 4) if n_below else None,
        "win_rate_below_threshold": round(win_below / n_below, 4) if n_below else None,
        "expected_delta_yen": net_delta,
        "saved_loss_yen": saved,
        "lost_upside_yen": lost,
        "net_delta_yen": net_delta,
        "affected_trade_count": affected,
    }


def _evaluate_stop_exit_rule(
    trades: Sequence[Mapping[str, Any]],
    snapshots: Sequence[Mapping[str, Any]],
    *,
    bucket_min: int,
    threshold: float,
) -> dict[str, Any]:
    snap_by_key = {
        (str(s.get("symbol")), str(s.get("entry_time"))): s for s in snapshots
    }
    baseline_pnls = [float(t["baseline_pnl_yen_100"]) for t in trades]
    shadow_pnls: list[float] = []
    below_rows: list[Mapping[str, Any]] = []
    affected = 0

    for t in trades:
        key = (str(t.get("symbol")), str(t.get("entry_time")))
        snap = snap_by_key.get(key)
        base = float(t["baseline_pnl_yen_100"])
        if snap is None:
            shadow_pnls.append(base)
            continue
        if float(snap["current_pnl_pct"]) < threshold:
            sh = float(snap["checkpoint_pnl_yen_100"])
            shadow_pnls.append(sh)
            below_rows.append(snap)
            if abs(sh - base) > 0.01:
                affected += 1
        else:
            shadow_pnls.append(base)

    saved, lost = _saved_lost_yen(baseline_pnls, shadow_pnls)
    net_delta = round(sum(shadow_pnls) - sum(baseline_pnls), 2)
    losers_below = sum(1 for s in below_rows if not s.get("final_is_winner"))
    win_below = sum(1 for s in below_rows if s.get("final_is_winner"))
    n_below = len(below_rows)

    return {
        "time_bucket_min": bucket_min,
        "rule_type": "stop_exit_if_below",
        "threshold_pct": threshold,
        "trades_at_bucket": len(snapshots),
        "trades_below_threshold": n_below,
        "final_loser_rate_below": round(losers_below / n_below, 4) if n_below else None,
        "win_rate_below_threshold": round(win_below / n_below, 4) if n_below else None,
        "expected_delta_yen": net_delta,
        "saved_loss_yen": saved,
        "lost_upside_yen": lost,
        "net_delta_yen": net_delta,
        "affected_trade_count": affected,
    }


def _evaluate_mfe_trail_rule(
    trades: Sequence[Mapping[str, Any]],
    snapshots: Sequence[Mapping[str, Any]],
    *,
    bucket_min: int,
    activate_threshold: float,
) -> dict[str, Any]:
    """At bucket: if peak >= activate and current <= peak*giveback, exit at checkpoint."""
    snap_by_key = {
        (str(s.get("symbol")), str(s.get("entry_time"))): s for s in snapshots
    }
    baseline_pnls = [float(t["baseline_pnl_yen_100"]) for t in trades]
    shadow_pnls: list[float] = []
    triggered: list[Mapping[str, Any]] = []
    affected = 0

    for t in trades:
        key = (str(t.get("symbol")), str(t.get("entry_time")))
        snap = snap_by_key.get(key)
        base = float(t["baseline_pnl_yen_100"])
        if snap is None:
            shadow_pnls.append(base)
            continue
        peak = float(snap["max_mfe_so_far_pct"])
        cur = float(snap["current_pnl_pct"])
        if peak >= activate_threshold and cur <= peak * TRAIL_GIVEBACK_FRAC:
            sh = float(snap["checkpoint_pnl_yen_100"])
            shadow_pnls.append(sh)
            triggered.append(snap)
            if abs(sh - base) > 0.01:
                affected += 1
        else:
            shadow_pnls.append(base)

    saved, lost = _saved_lost_yen(baseline_pnls, shadow_pnls)
    net_delta = round(sum(shadow_pnls) - sum(baseline_pnls), 2)
    n_trig = len(triggered)
    win_below = sum(1 for s in triggered if s.get("final_is_winner"))

    return {
        "time_bucket_min": bucket_min,
        "rule_type": "mfe_trail_at_bucket",
        "threshold_pct": activate_threshold,
        "trades_at_bucket": len(snapshots),
        "trades_below_threshold": n_trig,
        "final_loser_rate_below": round(
            sum(1 for s in triggered if not s.get("final_is_winner")) / n_trig, 4
        )
        if n_trig
        else None,
        "win_rate_below_threshold": round(win_below / n_trig, 4) if n_trig else None,
        "expected_delta_yen": net_delta,
        "saved_loss_yen": saved,
        "lost_upside_yen": lost,
        "net_delta_yen": net_delta,
        "affected_trade_count": affected,
    }


def _pick_best(rows: Sequence[Mapping[str, Any]], *, rule_type: str) -> Optional[dict[str, Any]]:
    candidates = [r for r in rows if r.get("rule_type") == rule_type]
    if not candidates:
        return None
    viable = [
        r
        for r in candidates
        if float(r.get("net_delta_yen") or 0) > 0
        and float(r.get("saved_loss_yen") or 0) > float(r.get("lost_upside_yen") or 0)
    ]
    pool = viable or candidates
    pool = sorted(
        pool,
        key=lambda r: (
            -float(r.get("net_delta_yen") or 0),
            float(r.get("lost_upside_yen") or 1e18),
        ),
    )
    return pool[0]


def _build_policy_row(
    bucket_min: int,
    inference_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    bucket_rows = [r for r in inference_rows if int(r.get("time_bucket_min") or 0) == bucket_min]
    best_mfe = _pick_best(bucket_rows, rule_type="mfe_exit_if_below")
    best_stop = _pick_best(bucket_rows, rule_type="stop_exit_if_below")
    best_trail = _pick_best(bucket_rows, rule_type="mfe_trail_at_bucket")

    mfe_delta = float(best_mfe.get("net_delta_yen") or 0) if best_mfe else 0.0
    stop_delta = float(best_stop.get("net_delta_yen") or 0) if best_stop else 0.0

    return {
        "time_bucket_min": bucket_min,
        "recommended_mfe_exit_threshold": best_mfe.get("threshold_pct") if best_mfe else None,
        "recommended_stop_threshold": best_stop.get("threshold_pct") if best_stop else None,
        "recommended_mfe_trail_threshold": best_trail.get("threshold_pct") if best_trail else None,
        "win_rate_below_mfe_threshold": best_mfe.get("win_rate_below_threshold") if best_mfe else None,
        "final_loser_rate_below_mfe": best_mfe.get("final_loser_rate_below") if best_mfe else None,
        "expected_delta_yen_mfe_rule": mfe_delta,
        "expected_delta_yen_stop_rule": stop_delta,
        "expected_delta_yen_combined_estimate": round(mfe_delta + stop_delta, 2),
        "saved_loss_yen_mfe": best_mfe.get("saved_loss_yen") if best_mfe else 0,
        "lost_upside_yen_mfe": best_mfe.get("lost_upside_yen") if best_mfe else 0,
        "affected_trade_count_mfe": best_mfe.get("affected_trade_count") if best_mfe else 0,
        "saved_loss_yen_stop": best_stop.get("saved_loss_yen") if best_stop else 0,
        "lost_upside_yen_stop": best_stop.get("lost_upside_yen") if best_stop else 0,
        "affected_trade_count_stop": best_stop.get("affected_trade_count") if best_stop else 0,
    }


def _infer_loser_mfe_boundary(snapshots: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """Heuristic: MFE level where final loser rate exceeds winner rate among snapshots."""
    if not snapshots:
        return None
    for thr in sorted(MFE_EXIT_THRESHOLDS):
        below = [s for s in snapshots if float(s["max_mfe_so_far_pct"]) < thr]
        if len(below) < 10:
            continue
        loser_rate = sum(1 for s in below if not s.get("final_is_winner")) / len(below)
        if loser_rate >= 0.55:
            return thr
    return MFE_EXIT_THRESHOLDS[2]


def _rule_text(policy_rows: Sequence[Mapping[str, Any]]) -> list[str]:
    rules: list[str] = []
    for row in policy_rows:
        b = int(row["time_bucket_min"])
        mfe = row.get("recommended_mfe_exit_threshold")
        stop = row.get("recommended_stop_threshold")
        trail = row.get("recommended_mfe_trail_threshold")
        if mfe is not None:
            rules.append(
                f"At {b}min: if max_mfe_so_far < {mfe}% → exit (loser_rate_below={row.get('final_loser_rate_below_mfe')})"
            )
        if stop is not None:
            rules.append(f"At {b}min: if current_pnl < {stop}% → stop exit")
        if trail is not None:
            rules.append(
                f"At {b}min: if peak>={trail}% and pnl<=peak*{TRAIL_GIVEBACK_FRAC} → trail exit"
            )
    return rules


def _render_report(summary: Mapping[str, Any]) -> str:
    ma = summary.get("mandatory_answers") or {}
    policies = summary.get("policy_by_bucket") or {}
    lines = [
        "# Phase405 — Time-Based MFE / STOP Boundary Inference",
        "",
        f"Generated: {summary.get('generated_at')}",
        f"Period: {summary.get('period_start')} – {summary.get('period_end')}",
        f"Trades analyzed: {summary.get('trade_count')}",
        "",
        "## Mandatory answers",
        "",
    ]
    for key in ("10m", "15m", "20m", "30m"):
        ans = ma.get(key) or {}
        lines.append(
            f"### {key}: MFE exit < {ans.get('recommended_mfe_exit_threshold')}% | "
            f"STOP < {ans.get('recommended_stop_threshold')}% | "
            f"trail activate {ans.get('recommended_mfe_trail_threshold')}%"
        )
        lines.append(
            f"- MFE rule delta: ¥{ans.get('expected_delta_yen_mfe_rule')} | "
            f"STOP rule delta: ¥{ans.get('expected_delta_yen_stop_rule')}"
        )
        lines.append("")
    lines.extend(
        [
            f"**Most effective time bucket:** {summary.get('most_effective_time_bucket_min')}min "
            f"(combined estimate ¥{summary.get('most_effective_combined_delta_yen')})",
            "",
            "## Inferred rules",
            "",
        ]
    )
    for rule in summary.get("inferred_rules") or []:
        lines.append(f"- {rule}")
    lines.extend(["", "## Policy table", "", "| bucket | MFE< | STOP< | trail | mfe_Δ | stop_Δ |", "|--------|------|-------|-------|-------|--------|"])
    for b in TIME_BUCKETS_MIN:
        p = policies.get(str(b)) or {}
        lines.append(
            f"| {b}m | {p.get('recommended_mfe_exit_threshold')} | {p.get('recommended_stop_threshold')} | "
            f"{p.get('recommended_mfe_trail_threshold')} | ¥{p.get('expected_delta_yen_mfe_rule')} | "
            f"¥{p.get('expected_delta_yen_stop_rule')} |"
        )
    lines.extend(["", "- shadow / research only", ""])
    return "\n".join(lines)


def run_phase405_inference(
    *,
    repo_root: Path,
    trades_path: Optional[Path] = None,
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

    raw = load_phase399_trades(trades_path)
    accepted = [
        enrich_trade(r)
        for r in raw
        if str(r.get("day") or "") >= period_start
        and str(r.get("day") or "") <= period_end
        and str(r.get("position_cap_accepted") or "").lower() in ("true", "1", "yes")
    ]

    trades = _prepare_trades(accepted, repo_root=repo_root)
    snapshots_by_bucket: dict[int, list[dict[str, Any]]] = {}
    all_snapshots: list[dict[str, Any]] = []

    for bucket_min in TIME_BUCKETS_MIN:
        snaps: list[dict[str, Any]] = []
        for t in trades:
            snap = build_bucket_snapshot(t, bucket_min=bucket_min)
            if snap:
                snaps.append(snap)
        snapshots_by_bucket[bucket_min] = snaps
        all_snapshots.extend(snaps)

    inference_rows: list[dict[str, Any]] = []
    policy_rows: list[dict[str, Any]] = []

    for bucket_min in TIME_BUCKETS_MIN:
        snaps = snapshots_by_bucket[bucket_min]
        for thr in MFE_EXIT_THRESHOLDS:
            inference_rows.append(
                _evaluate_mfe_exit_rule(trades, snaps, bucket_min=bucket_min, threshold=thr)
            )
        for thr in STOP_EXIT_THRESHOLDS:
            inference_rows.append(
                _evaluate_stop_exit_rule(trades, snaps, bucket_min=bucket_min, threshold=thr)
            )
        for thr in MFE_TRAIL_THRESHOLDS:
            inference_rows.append(
                _evaluate_mfe_trail_rule(trades, snaps, bucket_min=bucket_min, activate_threshold=thr)
            )
        policy_rows.append(_build_policy_row(bucket_min, inference_rows))

    best_bucket_row = max(
        policy_rows,
        key=lambda r: float(r.get("expected_delta_yen_combined_estimate") or 0),
    )

    policy_by_bucket = {str(r["time_bucket_min"]): r for r in policy_rows}
    mandatory = {
        f"{b}m": policy_by_bucket.get(str(b), {})
        for b in (10, 15, 20, 30)
    }

    inferred_rules = _rule_text(policy_rows)

    summary = {
        "phase": 405,
        "generated_at": _now_iso(),
        "period_start": period_start,
        "period_end": period_end,
        "source_trades": str(trades_path),
        "trade_count": len(trades),
        "snapshot_count": len(all_snapshots),
        "inference_row_count": len(inference_rows),
        "policy_by_bucket": policy_by_bucket,
        "mandatory_answers": mandatory,
        "most_effective_time_bucket_min": best_bucket_row.get("time_bucket_min"),
        "most_effective_combined_delta_yen": best_bucket_row.get("expected_delta_yen_combined_estimate"),
        "inferred_rules": inferred_rules,
        "headline": (
            f"Phase405: best bucket {best_bucket_row.get('time_bucket_min')}min "
            f"MFE<{best_bucket_row.get('recommended_mfe_exit_threshold')}% "
            f"STOP<{best_bucket_row.get('recommended_stop_threshold')}% "
            f"est_Δ¥{best_bucket_row.get('expected_delta_yen_combined_estimate')}"
        ),
    }

    inference_path = output_dir / "phase405_time_boundary_inference.csv"
    policy_path = output_dir / "phase405_time_boundary_policy.csv"
    _write_csv(inference_path, inference_rows, INFERENCE_FIELDS)
    _write_csv(policy_path, policy_rows, POLICY_FIELDS)

    summary_path = output_dir / "phase405_time_boundary_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    report_path = repo_root / "docs" / "operations" / "phase405_time_boundary_inference_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_report(summary), encoding="utf-8")

    return {
        "summary": summary,
        "inference_path": str(inference_path),
        "policy_path": str(policy_path),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
    }
