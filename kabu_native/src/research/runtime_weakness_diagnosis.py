"""
Phase 52: Runtime weakness diagnosis (structural — not time-band optimization).

Analyzes continuation quality, cap saturation, hold/TAKE/EXIT timing, decay, and
concentration without using session-specific stop rules or threshold tuning.
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
from zoneinfo import ZoneInfo

from research.exposure_gate import (
    REJECT_MAX_CONCURRENT,
    REJECT_OUTSIDE_ALLOWED_TRADING_WINDOW,
)
from research.research_exit_criteria import _as_float
from research.small_paper_performance_review import (
    EXCESSIVE_HOLD_SEC,
    _build_trade_lifecycles,
    _load_events,
    _load_json,
    _parse_dt,
    _parse_ts,
    _profit_factor,
    _replay_observer_judgments,
    quality_band,
)
from small_paper.allowed_trading_windows import windows_summary

JST = ZoneInfo("Asia/Tokyo")

LOSS_STRUCT_TAGS = (
    "momentum_decay",
    "liquidity_thin",
    "cap_blocked_proxy",
    "hold_too_long",
    "take_too_early",
    "quality_decay",
    "quality_inflation",
)


def _feature_bucket(name: str, value: float) -> str:
    if name == "continuation_quality":
        return quality_band(value)
    if name == "hold_duration_sec":
        if value < 120:
            return "lt_2m"
        if value < 300:
            return "2m_5m"
        if value < EXCESSIVE_HOLD_SEC:
            return "5m_10m"
        return "ge_10m"
    if name == "rolling_mfe_pct":
        if value < 0.1:
            return "mfe_lt_0.1"
        if value < 0.5:
            return "mfe_0.1_0.5"
        return "mfe_ge_0.5"
    if name == "rolling_mae_pct":
        if value > -0.2:
            return "mae_gt_-0.2"
        if value > -0.5:
            return "mae_-0.5_-0.2"
        return "mae_le_-0.5"
    if name == "momentum_continuation_score":
        if value < 0.4:
            return "mom_lt_0.4"
        if value < 0.7:
            return "mom_0.4_0.7"
        return "mom_ge_0.7"
    return "other"


def _loss_structure_tags(trade: Mapping[str, Any], *, cap_binding_rate: float) -> list[str]:
    tags: list[str] = []
    pnl = float(trade.get("realized_pnl_pct") or 0)
    if pnl >= 0:
        return tags
    q = float(trade.get("continuation_quality_score") or 0)
    min_q = float(trade.get("min_continuation_quality") or 0.55)
    if min_q > 0 and q - min_q < 0.03:
        tags.append("quality_inflation")
    mom = _as_float(trade.get("momentum_continuation_score"))
    if mom is not None and mom < 0.45:
        tags.append("momentum_decay")
    ticks = int(trade.get("tick_count") or 0)
    if ticks < 5:
        tags.append("liquidity_thin")
    hold = float(trade.get("hold_duration_sec") or 0)
    if hold >= EXCESSIVE_HOLD_SEC:
        tags.append("hold_too_long")
    mfe = float(trade.get("mfe_pct") or 0)
    if mfe > 0.15 and pnl < 0:
        tags.append("take_too_early")
    if cap_binding_rate > 0.25:
        tags.append("cap_blocked_proxy")
    fav = trade.get("favorable_continuation")
    if fav is False or (isinstance(fav, str) and fav.lower() == "false"):
        tags.append("quality_decay")
    return tags or ["unclassified_loss"]


def _viewpoint_quality_inflation(
    events: Sequence[Mapping[str, Any]],
    *,
    min_quality: float,
) -> dict[str, Any]:
    accepted = [e for e in events if e.get("event_type") == "accepted"]
    qs = [float(e.get("continuation_quality_score") or 0) for e in accepted]
    near_floor = [q for q in qs if min_quality <= q < min_quality + 0.05]
    fallback = sum(1 for e in events if e.get("quality_fallback_path"))
    return {
        "accepted_count": len(qs),
        "quality_mean": round(statistics.mean(qs), 4) if qs else None,
        "quality_p50": round(statistics.median(qs), 4) if qs else None,
        "pct_within_0.05_of_threshold": round(100.0 * len(near_floor) / max(1, len(qs)), 2),
        "quality_fallback_events": fallback,
        "assessment": (
            "possible_inflation_near_threshold"
            if qs and len(near_floor) / len(qs) > 0.4
            else "spread_ok"
        ),
    }


def _viewpoint_late_high_quality(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """High quality that appears after several prior ticks on same symbol."""
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if e.get("event_type") != "candidate":
            continue
        sym = str(e.get("symbol") or "")
        if sym:
            by_sym[sym].append(dict(e))
    late_hq = 0
    total_hq = 0
    for rows in by_sym.values():
        rows.sort(key=lambda r: int(r.get("message_index") or 0))
        seen = 0
        for r in rows:
            q = float(r.get("continuation_quality_score") or 0)
            if q >= 0.70:
                total_hq += 1
                if seen >= 3:
                    late_hq += 1
            seen += 1
    return {
        "high_quality_candidate_count": total_hq,
        "late_high_quality_count": late_hq,
        "late_high_quality_rate_pct": round(100.0 * late_hq / max(1, total_hq), 2),
        "assessment": "late_spike_risk" if total_hq and late_hq / total_hq > 0.35 else "ok",
    }


def _viewpoint_cap_saturation(
    events: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    evals = int(summary.get("gate_evaluations") or 0)
    mc = [
        e
        for e in events
        if e.get("event_type") == "rejected"
        and e.get("gate_reject_reason") == REJECT_MAX_CONCURRENT
    ]
    qs = [float(e.get("continuation_quality_score") or 0) for e in mc]
    hq = [q for q in qs if q >= 0.70]
    return {
        "max_concurrent_reject_count": len(mc),
        "saturation_rate_pct": round(100.0 * len(mc) / max(1, evals), 2),
        "blocked_avg_quality": round(statistics.mean(qs), 4) if qs else None,
        "blocked_high_quality_count": len(hq),
        "assessment": (
            "good_candidates_lost_to_cap"
            if len(hq) > 10 and len(mc) / max(1, evals) > 0.15
            else "cap_acceptable"
        ),
    }


def _viewpoint_hold_and_take(
    trades: Sequence[Mapping[str, Any]],
    observer: Mapping[str, Any],
) -> dict[str, Any]:
    pnls = [float(t.get("realized_pnl_pct") or 0) for t in trades]
    long_loss = [
        t
        for t in trades
        if float(t.get("hold_duration_sec") or 0) >= EXCESSIVE_HOLD_SEC
        and float(t.get("realized_pnl_pct") or 0) < 0
    ]
    take_rate = observer.get("take_rate_pct")
    early_take = observer.get("early_take_rate_pct")
    return {
        "avg_hold_sec": round(statistics.mean([float(t.get("hold_duration_sec") or 0) for t in trades]), 1)
        if trades
        else None,
        "long_hold_loss_count": len(long_loss),
        "take_rate_pct": take_rate,
        "early_take_rate_pct": early_take,
        "assessment_hold": "hold_loss_risk" if len(long_loss) > len(trades) * 0.15 else "ok",
        "assessment_take": "take_too_early" if early_take and early_take > 60 else "ok",
    }


def _viewpoint_quality_decay(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    decay_signals = 0
    for e in events:
        if e.get("event_type") != "candidate":
            continue
        fav = e.get("favorable_continuation")
        adv = e.get("adverse_shrinking")
        if fav is False or (isinstance(fav, str) and fav.lower() == "false"):
            decay_signals += 1
        elif adv is False or (isinstance(adv, str) and adv.lower() == "false"):
            decay_signals += 1
    cands = sum(1 for e in events if e.get("event_type") == "candidate")
    return {
        "decay_signal_candidates": decay_signals,
        "candidate_count": cands,
        "decay_signal_rate_pct": round(100.0 * decay_signals / max(1, cands), 2),
        "assessment": "decay_visible" if decay_signals > 0 else "limited_decay_fields",
    }


def _viewpoint_symbol_concentration(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"assessment": "no_trades"}
    counts = Counter(str(t.get("symbol")) for t in trades)
    top_sym, top_n = counts.most_common(1)[0]
    pct = 100.0 * top_n / len(trades)
    return {
        "unique_symbols": len(counts),
        "top_symbol": top_sym,
        "top_symbol_share_pct": round(pct, 2),
        "assessment": "concentrated" if pct > 25 else "diversified",
    }


def _viewpoint_structural_losses(
    trades: Sequence[Mapping[str, Any]],
    *,
    cap_binding_rate: float,
) -> dict[str, Any]:
    losers = [t for t in trades if float(t.get("realized_pnl_pct") or 0) < 0]
    tag_counts: Counter[str] = Counter()
    for t in losers:
        for tag in _loss_structure_tags(t, cap_binding_rate=cap_binding_rate):
            tag_counts[tag] += 1
    return {
        "loser_count": len(losers),
        "loss_tag_counts": dict(tag_counts),
        "note": "Losses attributed by structure (momentum/liquidity/cap/hold/TAKE/decay), not session bucket.",
    }


def _weakness_by_symbol(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_sym: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for t in trades:
        by_sym[str(t.get("symbol"))].append(t)
    rows: list[dict[str, Any]] = []
    for sym, grp in sorted(by_sym.items()):
        pnls = [float(t.get("realized_pnl_pct") or 0) for t in grp]
        qs = [float(t.get("continuation_quality_score") or 0) for t in grp]
        rows.append(
            {
                "symbol": sym,
                "trade_count": len(grp),
                "realized_pnl_sum_pct": round(sum(pnls), 4),
                "avg_pnl_pct": round(statistics.mean(pnls), 4),
                "profit_factor": round(_profit_factor(pnls), 4)
                if _profit_factor(pnls) not in (None, float("inf"))
                else _profit_factor(pnls),
                "avg_quality": round(statistics.mean(qs), 4),
                "avg_hold_sec": round(
                    statistics.mean([float(t.get("hold_duration_sec") or 0) for t in grp]), 1
                ),
            }
        )
    rows.sort(key=lambda r: r.get("realized_pnl_sum_pct", 0))
    return rows


def _weakness_by_feature(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    features = (
        ("continuation_quality", "continuation_quality_score"),
        ("hold_duration_sec", "hold_duration_sec"),
        ("rolling_mfe_pct", "rolling_mfe_at_entry"),
        ("rolling_mae_pct", "rolling_mae_at_entry"),
        ("momentum_continuation_score", "momentum_continuation_score"),
    )
    rows: list[dict[str, Any]] = []
    for feat_name, field in features:
        buckets: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            v = _as_float(t.get(field))
            if v is None:
                continue
            buckets[_feature_bucket(feat_name, float(v))].append(float(t.get("realized_pnl_pct") or 0))
        for bucket, pnls in sorted(buckets.items()):
            rows.append(
                {
                    "feature": feat_name,
                    "bucket": bucket,
                    "trade_count": len(pnls),
                    "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
                    "profit_factor": round(_profit_factor(pnls), 4)
                    if _profit_factor(pnls) not in (None, float("inf"))
                    else _profit_factor(pnls),
                }
            )
    return rows


def _trade_path_examples(trades: Sequence[Mapping[str, Any]], *, cap_rate: float) -> list[dict[str, Any]]:
    enriched = []
    for t in trades:
        row = dict(t)
        row["loss_structure_tags"] = "|".join(
            _loss_structure_tags(row, cap_binding_rate=cap_rate)
        )
        enriched.append(row)
    enriched.sort(key=lambda r: float(r.get("realized_pnl_pct") or 0))
    worst = enriched[:15]
    best = list(reversed(enriched[-5:])) if len(enriched) > 5 else []
    out: list[dict[str, Any]] = []
    for label, grp in (("worst", worst), ("best", best)):
        for r in grp:
            out.append(
                {
                    "example_type": label,
                    "symbol": r.get("symbol"),
                    "entry_time": r.get("entry_time"),
                    "continuation_quality_score": r.get("continuation_quality_score"),
                    "realized_pnl_pct": r.get("realized_pnl_pct"),
                    "hold_duration_sec": r.get("hold_duration_sec"),
                    "mfe_pct": r.get("mfe_pct"),
                    "mae_pct": r.get("mae_pct"),
                    "momentum_continuation_score": r.get("momentum_continuation_score"),
                    "favorable_continuation": r.get("favorable_continuation"),
                    "loss_structure_tags": r.get("loss_structure_tags"),
                }
            )
    return out


def _rejected_outside_window(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for e in events:
        if e.get("event_type") != "rejected":
            continue
        if e.get("gate_reject_reason") not in (
            REJECT_OUTSIDE_ALLOWED_TRADING_WINDOW,
            "outside_allowed_trading_window",
        ):
            continue
        rows.append(
            {
                "symbol": e.get("symbol"),
                "entry_time": e.get("entry_time"),
                "continuation_quality_score": e.get("continuation_quality_score"),
                "gate_reject_reason": e.get("gate_reject_reason"),
                "message_index": e.get("message_index"),
            }
        )
    return rows


def _live_observer_verdict(
    summary: Mapping[str, Any],
    trades: Sequence[Mapping[str, Any]],
    viewpoints: Mapping[str, Any],
) -> dict[str, Any]:
    pf = _profit_factor([float(t.get("realized_pnl_pct") or 0) for t in trades])
    n = len(trades)
    avg = statistics.mean([float(t.get("realized_pnl_pct") or 0) for t in trades]) if trades else 0
    blockers = []
    if viewpoints.get("quality_inflation", {}).get("assessment") == "possible_inflation_near_threshold":
        blockers.append("quality_inflation")
    if viewpoints.get("cap_saturation", {}).get("assessment") == "good_candidates_lost_to_cap":
        blockers.append("cap_saturation")
    if viewpoints.get("hold_and_take", {}).get("assessment_take") == "take_too_early":
        blockers.append("take_too_early")
    if n < 50:
        blockers.append("insufficient_accepted_trades")
    if pf is not None and pf < 1.1:
        blockers.append("pf_below_1.1")
    if avg < 0:
        blockers.append("negative_avg_pnl")
    verdict = "resume_live_observer_trial" if not blockers else "fix_runtime_before_live"
    return {
        "verdict": verdict,
        "blockers": blockers,
        "trade_count": n,
        "profit_factor": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "avg_pnl_pct": round(avg, 4),
        "policy_label": summary.get("policy_label"),
    }


def run_runtime_weakness_diagnosis(
    session_dir: Path,
    *,
    pilot_config: Any = None,
    poll_interval_sec: Optional[float] = None,
) -> dict[str, Any]:
    session_dir = session_dir.resolve()
    summary = _load_json(session_dir / "small_paper_summary.json")
    events = _load_events(session_dir)
    min_q = float(
        summary.get("min_continuation_quality")
        or (pilot_config.min_continuation_quality if pilot_config else 0.55)
    )

    lifecycles = _build_trade_lifecycles(events)
    trade_rows = [
        {
            "symbol": t.symbol,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "continuation_quality_score": t.continuation_quality_score,
            "quality_tier": t.quality_tier,
            "hold_duration_sec": t.hold_duration_sec,
            "realized_pnl_pct": t.realized_pnl_pct,
            "mfe_pct": t.mfe_pct,
            "mae_pct": t.mae_pct,
            "tick_count": t.tick_count,
            "rolling_mfe_at_entry": t.rolling_mfe_at_entry,
            "rolling_mae_at_entry": t.rolling_mae_at_entry,
            "momentum_continuation_score": None,
            "favorable_continuation": None,
            "min_continuation_quality": min_q,
        }
        for t in lifecycles
    ]
    acc_by_key = {
        (str(e.get("symbol")), str(e.get("entry_time"))): e
        for e in events
        if e.get("event_type") == "accepted"
    }
    for row in trade_rows:
        acc = acc_by_key.get((row["symbol"], row["entry_time"]), {})
        row["momentum_continuation_score"] = acc.get("momentum_continuation_score")
        row["favorable_continuation"] = acc.get("favorable_continuation")
        row["rolling_mfe_at_entry"] = acc.get("rolling_mfe_pct") or row["rolling_mfe_at_entry"]
        row["rolling_mae_at_entry"] = acc.get("rolling_mae_pct") or row["rolling_mae_at_entry"]

    cap_view = _viewpoint_cap_saturation(events, summary)
    cap_rate = float(cap_view.get("saturation_rate_pct") or 0) / 100.0

    observer: dict[str, Any] = {}
    if pilot_config is not None:
        interval = poll_interval_sec if poll_interval_sec is not None else float(
            summary.get("poll_interval_sec") or 5.0
        )
        observer = _replay_observer_judgments(
            events, pilot_config=pilot_config, poll_interval_sec=interval
        )

    viewpoints = {
        "quality_inflation": _viewpoint_quality_inflation(events, min_quality=min_q),
        "late_high_quality": _viewpoint_late_high_quality(events),
        "cap_saturation": cap_view,
        "hold_and_take": _viewpoint_hold_and_take(trade_rows, observer),
        "quality_decay": _viewpoint_quality_decay(events),
        "symbol_concentration": _viewpoint_symbol_concentration(trade_rows),
        "structural_losses": _viewpoint_structural_losses(trade_rows, cap_binding_rate=cap_rate),
    }

    allowed = (
        windows_summary(pilot_config.allowed_windows())
        if pilot_config and pilot_config.allowed_windows()
        else windows_summary([])
    )

    outside = _rejected_outside_window(events)
    diagnosis: dict[str, Any] = {
        "phase": 52,
        "mode": "runtime_weakness_diagnosis",
        "session_dir": str(session_dir),
        "structural_only": True,
        "time_band_optimization_forbidden": True,
        "allowed_trading_windows": allowed,
        "outside_window_reject_count": len(outside),
        "viewpoints": viewpoints,
        "session_summary": {
            "gate_evaluations": summary.get("gate_evaluations"),
            "accepted_count": summary.get("accepted_count"),
            "rejected_count": summary.get("rejected_count"),
            "reject_reason_counts": summary.get("reject_reason_counts"),
        },
        "live_observer": _live_observer_verdict(summary, trade_rows, viewpoints),
        "_weakness_by_symbol": _weakness_by_symbol(trade_rows),
        "_weakness_by_feature": _weakness_by_feature(trade_rows),
        "_trade_path_examples": _trade_path_examples(trade_rows, cap_rate=cap_rate),
        "_rejected_outside_window": outside,
    }
    return diagnosis


def write_runtime_weakness_diagnosis(session_dir: Path, diagnosis: Mapping[str, Any]) -> dict[str, Path]:
    session_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    public = {k: v for k, v in diagnosis.items() if not k.startswith("_")}
    json_path = session_dir / "runtime_weakness_diagnosis.json"
    json_path.write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    paths["json"] = json_path

    for key, filename in (
        ("_weakness_by_symbol", "weakness_by_symbol.csv"),
        ("_weakness_by_feature", "weakness_by_feature.csv"),
        ("_trade_path_examples", "trade_path_examples.csv"),
        ("_rejected_outside_window", "rejected_outside_window.csv"),
    ):
        rows = diagnosis.get(key) or []
        if not rows:
            continue
        csv_path = session_dir / filename
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        paths[filename.replace(".csv", "")] = csv_path

    return paths


def build_and_write_runtime_weakness_diagnosis(
    session_dir: Path,
    *,
    pilot_config: Any = None,
    poll_interval_sec: Optional[float] = None,
) -> dict[str, Any]:
    diagnosis = run_runtime_weakness_diagnosis(
        session_dir, pilot_config=pilot_config, poll_interval_sec=poll_interval_sec
    )
    paths = write_runtime_weakness_diagnosis(session_dir, diagnosis)
    public = {k: v for k, v in diagnosis.items() if not k.startswith("_")}
    public["output_files"] = {k: str(v) for k, v in paths.items()}
    paths["json"].write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return public
