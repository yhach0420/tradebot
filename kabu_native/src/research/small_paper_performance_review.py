"""
Phase 49: Push-replay small paper performance review (analysis only — no new trading logic).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from unittest.mock import patch
from zoneinfo import ZoneInfo

from research.research_exit_criteria import _as_float
from small_paper.discord_notifier import observer_tracker_config_from_pilot
from small_paper.observer_position_tracker import (
    OBSERVER_EXIT,
    OBSERVER_HOLD,
    OBSERVER_TAKE,
    ObserverPositionTracker,
)
JST = ZoneInfo("Asia/Tokyo")

QUALITY_TIER_BANDS = (
    ("0.55_0.65", 0.55, 0.65),
    ("0.65_0.75", 0.65, 0.75),
    ("ge_0.75", 0.75, 1.01),
)

VERDICT_MOVE = "move_to_live_observer_again"
VERDICT_FIX = "fix_runtime_before_live"

MAX_LOSS_ACCEPTABLE_PCT = -2.5
EXCESSIVE_HOLD_SEC = 600.0
MIN_TRADES_FOR_LIVE = 100
MIN_PF_FOR_LIVE = 1.2


def _parse_ts(ts: str) -> float:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _parse_dt(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(JST)


def session_bucket_at(dt: datetime) -> str:
    t = dt.timetz() if hasattr(dt, "timetz") else dt.time()
    from small_paper.session_schedule import AFTERNOON_END, MIDDAY_END, MORNING_END, parse_hhmm

    start = parse_hhmm("09:00")
    if t < start or t > AFTERNOON_END:
        return "outside"
    if t < MORNING_END:
        return "morning"
    if t < MIDDAY_END:
        return "midday"
    return "afternoon"


def quality_band(score: float) -> str:
    if score < 0.55:
        return "lt_0.55"
    if score < 0.65:
        return "0.55_0.65"
    if score < 0.75:
        return "0.65_0.75"
    return "ge_0.75"


def _load_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_events(session_dir: Path) -> list[dict[str, str]]:
    jsonl = session_dir / "small_paper_events.jsonl"
    if jsonl.is_file():
        rows: list[dict[str, str]] = []
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows
    return _load_csv(session_dir / "small_paper_events.csv")


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return sum(wins) / gl


@dataclass
class TradeLifecycle:
    symbol: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    continuation_quality_score: float
    quality_tier: str
    session_bucket: str
    hold_duration_sec: float
    realized_pnl_pct: float
    mfe_pct: float
    mae_pct: float
    tick_count: int
    rolling_mfe_at_entry: float
    rolling_mae_at_entry: float
    exit_reason: str
    message_index: int


def _build_trade_lifecycles(
    events: Sequence[Mapping[str, Any]],
) -> list[TradeLifecycle]:
    accepted = [e for e in events if e.get("event_type") == "accepted"]
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if e.get("event_type") != "candidate":
            continue
        sym = str(e.get("symbol") or "")
        if sym:
            by_sym[sym].append(dict(e))
    for sym in by_sym:
        by_sym[sym].sort(key=lambda r: _parse_ts(str(r.get("entry_time") or "")))

    trades: list[TradeLifecycle] = []
    for acc in accepted:
        sym = str(acc.get("symbol") or "")
        ent_ts = _parse_ts(str(acc.get("entry_time") or ""))
        ex_ts = _parse_ts(str(acc.get("exit_time") or "")) or ent_ts + 300
        entry_px = _as_float(acc.get("current_price")) or 0.0
        if entry_px <= 0:
            continue
        ticks = [
            t
            for t in by_sym.get(sym, [])
            if ent_ts <= _parse_ts(str(t.get("entry_time") or "")) <= ex_ts
        ]
        prices = [_as_float(t.get("current_price")) for t in ticks]
        prices = [p for p in prices if p is not None and p > 0]
        exit_px = prices[-1] if prices else entry_px
        mfe = max(((p - entry_px) / entry_px * 100.0) for p in prices) if prices else 0.0
        mae = min(((p - entry_px) / entry_px * 100.0) for p in prices) if prices else 0.0
        ent_dt = _parse_dt(str(acc.get("entry_time") or ""))
        trades.append(
            TradeLifecycle(
                symbol=sym,
                entry_time=str(acc.get("entry_time") or ""),
                exit_time=str(acc.get("exit_time") or ""),
                entry_price=entry_px,
                exit_price=exit_px,
                continuation_quality_score=float(acc.get("continuation_quality_score") or 0),
                quality_tier=str(acc.get("quality_tier") or ""),
                session_bucket=session_bucket_at(ent_dt),
                hold_duration_sec=max(0.0, ex_ts - ent_ts),
                realized_pnl_pct=round((exit_px - entry_px) / entry_px * 100.0, 4),
                mfe_pct=round(mfe, 4),
                mae_pct=round(mae, 4),
                tick_count=len(prices),
                rolling_mfe_at_entry=float(acc.get("rolling_mfe_pct") or 0),
                rolling_mae_at_entry=float(acc.get("rolling_mae_pct") or 0),
                exit_reason=str(acc.get("exit_reason") or "live_virtual_hold"),
                message_index=int(acc.get("message_index") or 0),
            )
        )
    return trades


def _trade_rows_csv(trades: Sequence[TradeLifecycle]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": t.symbol,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "continuation_quality_score": t.continuation_quality_score,
            "quality_tier": t.quality_tier,
            "quality_band": quality_band(t.continuation_quality_score),
            "session_bucket": t.session_bucket,
            "hold_duration_sec": t.hold_duration_sec,
            "realized_pnl_pct": t.realized_pnl_pct,
            "mfe_pct": t.mfe_pct,
            "mae_pct": t.mae_pct,
            "tick_count": t.tick_count,
            "rolling_mfe_at_entry": t.rolling_mfe_at_entry,
            "rolling_mae_at_entry": t.rolling_mae_at_entry,
            "exit_reason": t.exit_reason,
            "message_index": t.message_index,
        }
        for t in trades
    ]


def _summarize_trades(trades: Sequence[TradeLifecycle]) -> dict[str, Any]:
    if not trades:
        return {"trade_count": 0}
    pnls = [t.realized_pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    holds = [t.hold_duration_sec for t in trades]
    return {
        "trade_count": len(trades),
        "realized_pnl_sum_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(statistics.mean(pnls), 4),
        "median_pnl_pct": round(statistics.median(pnls), 4),
        "profit_factor": round(_profit_factor(pnls), 4) if _profit_factor(pnls) not in (None, float("inf")) else _profit_factor(pnls),
        "win_rate": round(len(wins) / len(pnls), 4),
        "max_loss_pct": round(min(pnls), 4),
        "max_gain_pct": round(max(pnls), 4),
        "avg_mfe_pct": round(statistics.mean([t.mfe_pct for t in trades]), 4),
        "avg_mae_pct": round(statistics.mean([t.mae_pct for t in trades]), 4),
        "avg_hold_duration_sec": round(statistics.mean(holds), 1),
        "median_hold_duration_sec": round(statistics.median(holds), 1),
        "max_hold_duration_sec": round(max(holds), 1),
        "exit_reason": Counter(t.exit_reason for t in trades),
    }


def _band_summary(trades: Sequence[TradeLifecycle]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, lo, hi in QUALITY_TIER_BANDS:
        grp = [t for t in trades if lo <= t.continuation_quality_score < hi]
        out[name] = _summarize_trades(grp)
    return out


def _bucket_summary(trades: Sequence[TradeLifecycle]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for bucket in ("morning", "midday", "afternoon", "outside"):
        grp = [t for t in trades if t.session_bucket == bucket]
        out[bucket] = _summarize_trades(grp)
    return out


def _analyze_rejects(
    events: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    rejects = [e for e in events if e.get("event_type") == "rejected"]
    by_reason: dict[str, list[float]] = defaultdict(list)
    for r in rejects:
        reason = str(r.get("gate_reject_reason") or "unknown")
        q = _as_float(r.get("continuation_quality_score"))
        if q is not None:
            by_reason[reason].append(float(q))

    reason_stats: dict[str, Any] = {}
    for reason, qs in sorted(by_reason.items()):
        reason_stats[reason] = {
            "count": len(qs),
            "avg_quality": round(statistics.mean(qs), 4) if qs else None,
            "pct_ge_0_55": round(100.0 * sum(1 for q in qs if q >= 0.55) / max(1, len(qs)), 2),
            "quality_p50": round(statistics.median(qs), 4) if qs else None,
        }

    return {
        "reject_reason_counts": dict(summary.get("reject_reason_counts") or {}),
        "by_reason_quality": reason_stats,
        "total_rejected": len(rejects),
    }


def _analyze_exposure(
    events: Sequence[Mapping[str, Any]],
    positions: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    max_conc_rejects = [
        e
        for e in events
        if e.get("event_type") == "rejected" and e.get("gate_reject_reason") == "max_concurrent"
    ]
    mc_qs = [_as_float(e.get("continuation_quality_score")) or 0.0 for e in max_conc_rejects]
    evaluations = int(summary.get("gate_evaluations") or 0)
    accepted = int(summary.get("accepted_count") or 0)

    return {
        "peak_open_slots": summary.get("peak_open_slots"),
        "max_concurrent_positions": summary.get("max_concurrent_positions"),
        "open_slots_end": summary.get("open_slots_end"),
        "concurrent_saturation_rate_pct": round(
            100.0 * len(max_conc_rejects) / max(1, evaluations),
            2,
        ),
        "max_concurrent_reject_count": len(max_conc_rejects),
        "max_concurrent_reject_avg_quality": round(statistics.mean(mc_qs), 4) if mc_qs else None,
        "max_concurrent_reject_pct_ge_0_55": round(
            100.0 * sum(1 for q in mc_qs if q >= 0.55) / max(1, len(mc_qs)),
            2,
        ),
        "accepted_while_cap_full_proxy": accepted,
        "cap_3_assessment": _assess_cap_three(
            len(max_conc_rejects), evaluations, statistics.mean(mc_qs) if mc_qs else 0.0
        ),
        "position_rows": len(positions),
    }


def _assess_cap_three(mc_rejects: int, evaluations: int, avg_mc_quality: float) -> str:
    rate = mc_rejects / max(1, evaluations)
    if rate > 0.35 and avg_mc_quality >= 0.60:
        return "cap_binding_high_quality_blocked"
    if rate > 0.25:
        return "cap_frequently_binding_review_optional"
    return "cap_acceptable"


def _replay_observer_judgments(
    events: Sequence[Mapping[str, Any]],
    *,
    pilot_config: Any,
    poll_interval_sec: float = 5.0,
) -> dict[str, Any]:
    """Re-run ObserverPositionTracker on recorded events (review-only replay clock)."""
    import small_paper.observer_position_tracker as ot

    tracker = ObserverPositionTracker(observer_tracker_config_from_pilot(pilot_config))
    ordered = sorted(events, key=lambda e: int(e.get("message_index") or 0))
    mono = [0.0]
    judgments: list[dict[str, Any]] = []
    take_followups: list[dict[str, Any]] = []

    def _mono() -> float:
        return mono[0]

    open_at_take: dict[str, dict[str, Any]] = {}

    for ev in ordered:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        ent_raw = str(ev.get("entry_time") or "")
        as_of = _parse_dt(ent_raw) if ent_raw else datetime.now(JST)
        mono[0] += max(poll_interval_sec, 0.001)
        bucket = session_bucket_at(as_of)
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
                    judgments.append(
                        {"kind": "entry", "symbol": sym, "entry_time": ent_raw, "quality": trade.get("continuation_quality_score")}
                    )
                elif ev.get("event_type") == "candidate" and tracker.has_open(sym):
                    obs_events = tracker.on_tick(
                        symbol=sym,
                        trade=trade,
                        payload=trade,
                        current_price=price,
                        session_bucket=bucket,
                    )
                    for oe in obs_events:
                        judgments.append({"kind": oe.kind, "symbol": sym, "context": oe.context})
                        if oe.kind == OBSERVER_TAKE:
                            open_at_take[sym] = {
                                "take_quality": oe.context.get("continuation_quality"),
                                "take_pnl": float(oe.context.get("unrealized_pnl_pct") or 0),
                                "take_ts": _parse_ts(ent_raw),
                                "entry_price": float(oe.context.get("entry_price") or 0),
                            }

    with patch.object(ot.time, "monotonic", _mono):
        with patch.object(ot, "datetime") as mdt:
            mdt.now.return_value = datetime.now(JST)
            mdt.combine = datetime.combine
            exit_events = tracker.close_all(reason="push_replay_review_end")
            for oe in exit_events:
                judgments.append({"kind": oe.kind, "symbol": oe.symbol, "context": oe.context})

    # TAKE follow-up: peak pnl after TAKE from subsequent candidate ticks
    by_sym = defaultdict(list)
    for ev in ordered:
        if ev.get("event_type") == "candidate":
            sym = str(ev.get("symbol") or "")
            px = _as_float(ev.get("current_price"))
            if sym and px:
                by_sym[sym].append((_parse_ts(str(ev.get("entry_time") or "")), px))

    extended_after_take = 0
    take_count = len(open_at_take)
    for sym, meta in open_at_take.items():
        take_ts = float(meta.get("take_ts") or 0)
        entry_px = float(meta.get("entry_price") or 0)
        if entry_px <= 0:
            continue
        post = [px for ts, px in by_sym.get(sym, []) if ts >= take_ts]
        if not post:
            continue
        take_pnl = float(meta.get("take_pnl") or 0)
        peak_after = max((p - entry_px) / entry_px * 100.0 for p in post)
        if peak_after > take_pnl + 0.05:
            extended_after_take += 1

    hold_count = sum(1 for j in judgments if j.get("kind") == OBSERVER_HOLD)
    exit_count = sum(1 for j in judgments if j.get("kind") == OBSERVER_EXIT)
    entry_count = sum(1 for j in judgments if j.get("kind") == "entry")

    holds = tracker.stats.hold_durations_sec
    avg_hold = statistics.mean(holds) if holds else None

    return {
        "entry_count": entry_count,
        "hold_count": hold_count,
        "take_count": take_count,
        "exit_count": exit_count,
        "take_extended_after_take_count": extended_after_take,
        "take_extended_after_take_rate_pct": round(
            100.0 * extended_after_take / max(1, take_count), 2
        ),
        "avg_hold_duration_sec": round(avg_hold, 1) if avg_hold is not None else None,
        "excessive_hold_flag": bool(avg_hold and avg_hold > EXCESSIVE_HOLD_SEC),
        "note": "Observer replayed offline with poll-interval clock; Discord was off during push-replay.",
    }


def _reject_quality_rows(events: Sequence[Mapping[str, Any]], *, sample_per_reason: int = 5000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if e.get("event_type") != "rejected":
            continue
        reason = str(e.get("gate_reject_reason") or "unknown")
        by_reason[reason].append(
            {
                "symbol": e.get("symbol"),
                "entry_time": e.get("entry_time"),
                "continuation_quality_score": e.get("continuation_quality_score"),
                "quality_tier": e.get("quality_tier"),
                "quality_band": quality_band(float(e.get("continuation_quality_score") or 0)),
                "gate_reject_reason": reason,
                "rolling_mfe_pct": e.get("rolling_mfe_pct"),
                "rolling_mae_pct": e.get("rolling_mae_pct"),
                "live_feature_complete": e.get("live_feature_complete"),
                "quality_fallback_path": e.get("quality_fallback_path"),
            }
        )
    for reason, grp in by_reason.items():
        grp.sort(key=lambda r: float(r.get("continuation_quality_score") or 0), reverse=True)
        for r in grp[:sample_per_reason]:
            rows.append(r)
        rows.append(
            {
                "symbol": "_AGGREGATE_",
                "entry_time": "",
                "continuation_quality_score": round(
                    statistics.mean(float(x.get("continuation_quality_score") or 0) for x in grp), 4
                )
                if grp
                else "",
                "quality_tier": "",
                "quality_band": "",
                "gate_reject_reason": reason,
                "rolling_mfe_pct": "",
                "rolling_mae_pct": "",
                "live_feature_complete": f"count={len(grp)}",
                "quality_fallback_path": "",
            }
        )
    return rows


def _compute_verdict(
    trade_summary: Mapping[str, Any],
    exposure: Mapping[str, Any],
    observer: Mapping[str, Any],
    session_summary: Mapping[str, Any],
) -> dict[str, Any]:
    trade_count = int(trade_summary.get("trade_count") or 0)
    avg_pnl = float(trade_summary.get("avg_pnl_pct") or 0)
    pf = trade_summary.get("profit_factor")
    pf_val = float(pf) if isinstance(pf, (int, float)) else 0.0
    max_loss = float(trade_summary.get("max_loss_pct") or 0)
    avg_hold = float(trade_summary.get("avg_hold_duration_sec") or 0)

    checks = {
        "trade_count_ge_100": trade_count >= MIN_TRADES_FOR_LIVE,
        "avg_pnl_positive": avg_pnl > 0,
        "profit_factor_ge_1_2": pf_val >= MIN_PF_FOR_LIVE,
        "max_loss_acceptable": max_loss >= MAX_LOSS_ACCEPTABLE_PCT,
        "hold_duration_ok": avg_hold <= EXCESSIVE_HOLD_SEC,
        "cap_acceptable": exposure.get("cap_3_assessment") == "cap_acceptable"
        or exposure.get("cap_3_assessment") == "cap_frequently_binding_review_optional",
        "feature_bridge_ok": bool(session_summary.get("live_feature_bridge")),
        "quality_fallback_low": float(session_summary.get("quality_fallback_rate_pct") or 100) < 5.0,
    }

    failures: list[str] = []
    if not checks["trade_count_ge_100"]:
        failures.append("trade_count_below_100")
    if not checks["avg_pnl_positive"]:
        failures.append("avg_pnl_not_positive")
    if not checks["profit_factor_ge_1_2"]:
        failures.append("profit_factor_below_1_2")
    if not checks["max_loss_acceptable"]:
        failures.append("max_loss_exceeds_threshold")
    if not checks["hold_duration_ok"]:
        failures.append("excessive_virtual_hold")
    if not checks["cap_acceptable"]:
        failures.append("max_concurrent_cap_too_binding")
    if observer.get("take_extended_after_take_rate_pct", 0) > 50:
        failures.append("observer_take_too_early")
    if observer.get("excessive_hold_flag"):
        failures.append("observer_hold_too_long")

    move = all(
        [
            checks["trade_count_ge_100"],
            checks["avg_pnl_positive"],
            checks["profit_factor_ge_1_2"],
            checks["max_loss_acceptable"],
            checks["hold_duration_ok"],
            checks["feature_bridge_ok"],
        ]
    ) and not failures

    verdict = VERDICT_MOVE if move else VERDICT_FIX
    rationale: list[str] = []
    if move:
        rationale.append("Push-replay accepted trades meet PF/avg_pnl/trade_count/hold-risk thresholds.")
        if not checks["cap_acceptable"]:
            rationale.append("max_concurrent=3 is binding but not blocking live observer trial.")
    else:
        if not checks["feature_bridge_ok"] or not checks["quality_fallback_low"]:
            rationale.append("Feature bridge or fallback rate needs review before live.")
        if not checks["avg_pnl_positive"] or not checks["profit_factor_ge_1_2"]:
            rationale.append("Virtual-hold PnL path does not support edge at quality>=0.55.")
        if not checks["cap_acceptable"]:
            rationale.append("max_concurrent=3 rejects many high-quality candidates.")
        if failures:
            rationale.append(f"Failed checks: {', '.join(failures)}")

    return {
        "verdict": verdict,
        "checks": checks,
        "failures": failures,
        "rationale": rationale,
    }


def run_push_replay_performance_review(
    session_dir: Path,
    *,
    pilot_config: Optional[Any] = None,
    poll_interval_sec: Optional[float] = None,
) -> dict[str, Any]:
    session_dir = session_dir.resolve()
    summary = _load_json(session_dir / "small_paper_summary.json")
    events = _load_events(session_dir)
    positions = _load_csv(session_dir / "small_paper_positions.csv")

    if pilot_config is None:
        from small_paper.config import load_pilot_config

        native = session_dir
        while native.name and native.parent != native:
            if (native / "configs" / "small_paper_pilot.yaml").is_file():
                pilot_config = load_pilot_config(native / "configs" / "small_paper_pilot.yaml")
                break
            native = native.parent
        else:
            pilot_config = None

    interval = poll_interval_sec
    if interval is None:
        interval = float(summary.get("poll_interval_sec") or 5.0)

    trades = _build_trade_lifecycles(events)
    trade_summary = _summarize_trades(trades)
    if isinstance(trade_summary.get("exit_reason"), Counter):
        trade_summary["exit_reason"] = dict(trade_summary["exit_reason"])

    review = {
        "phase": 49,
        "mode": "push_replay_performance_review",
        "session_dir": str(session_dir),
        "source": summary.get("source", "push-replay"),
        "session_summary": summary,
        "accepted_trade_performance": trade_summary,
        "quality_tier_bands": _band_summary(trades),
        "session_bucket_performance": _bucket_summary(trades),
        "reject_analysis": _analyze_rejects(events, summary),
        "exposure_analysis": _analyze_exposure(events, positions, summary),
        "observer_notification_review": _replay_observer_judgments(
            events, pilot_config=pilot_config, poll_interval_sec=interval
        )
        if pilot_config
        else {"note": "pilot config not found; observer replay skipped"},
        "verdict": {},
    }
    review["verdict"] = _compute_verdict(
        trade_summary,
        review["exposure_analysis"],
        review["observer_notification_review"],
        summary,
    )
    return review


def write_push_replay_performance_review(
    session_dir: Path,
    review: Mapping[str, Any],
) -> dict[str, Path]:
    session_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}

    json_path = session_dir / "small_paper_performance_review.json"
    json_path.write_text(json.dumps(review, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    out["json"] = json_path

    trades = review.get("accepted_trade_performance", {})
    # rebuild trade rows from review if stored — write from trades in review
    trade_rows = review.get("_trade_rows") or []
    if trade_rows:
        csv_path = session_dir / "small_paper_trades_review.csv"
        fields = list(trade_rows[0].keys())
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in trade_rows:
                w.writerow(r)
        out["trades_csv"] = csv_path

    reject_rows = review.get("_reject_quality_rows") or []
    if reject_rows:
        rpath = session_dir / "small_paper_reject_quality_review.csv"
        fields = list(reject_rows[0].keys())
        with rpath.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in reject_rows:
                w.writerow(r)
        out["reject_csv"] = rpath

    return out


def build_and_write_review(
    session_dir: Path,
    *,
    pilot_config: Optional[Any] = None,
    poll_interval_sec: Optional[float] = None,
) -> dict[str, Any]:
    review = run_push_replay_performance_review(
        session_dir, pilot_config=pilot_config, poll_interval_sec=poll_interval_sec
    )
    events = _load_events(session_dir)
    trades = _build_trade_lifecycles(events)
    review["_trade_rows"] = _trade_rows_csv(trades)
    review["_reject_quality_rows"] = _reject_quality_rows(events)
    paths = write_push_replay_performance_review(session_dir, review)
    review["output_files"] = {k: str(v) for k, v in paths.items()}
    # rewrite json without private keys
    public = {k: v for k, v in review.items() if not k.startswith("_")}
    paths["json"].write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return public
