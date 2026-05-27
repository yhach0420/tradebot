"""
Phase 131: Reacceleration shadow replay A/B vs combined_structural_exit_v1.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.reacceleration_shadow import MFE_GATE, POLICY_REACCELERATION_SHADOW
from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1
from research.structural_observer_review import (
    _load_events,
    _session_end_time,
    _summarize_structural_trades,
    replay_combined_structural_exit,
)

IMPROVE_EPS = 0.001
PHASE127_FADE_WATCH_POLICY = "combined_structural_exit_v1_fade_watch_shadow"


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return round(sum(wins) / gl, 4)


def _metrics(trades: list[Any]) -> dict[str, Any]:
    m = _summarize_structural_trades(trades)
    pnls = [t.realized_pnl_pct for t in trades]
    return {
        "total_pnl": round(sum(pnls), 4) if pnls else 0.0,
        "avg_pnl": m.get("structural_avg_pnl"),
        "pf": m.get("structural_pf"),
        "win_rate": m.get("structural_win_rate"),
        "trade_count": m.get("structural_trade_count"),
    }


def _extension_stats(
    shadow_trades: list[Any],
    baseline_trades: list[Any],
) -> dict[str, Any]:
    base_by_key = {(t.symbol, t.entry_time): t for t in baseline_trades}
    extended = [t for t in shadow_trades if getattr(t, "fade_watch_entered", False)]
    improved = worsened = unchanged = 0
    extra_holds: list[float] = []
    capture_rates: list[float] = []

    for t in extended:
        b = base_by_key.get((t.symbol, t.entry_time))
        if not b:
            continue
        delta = t.realized_pnl_pct - b.realized_pnl_pct
        if delta > IMPROVE_EPS:
            improved += 1
        elif delta < -IMPROVE_EPS:
            worsened += 1
        else:
            unchanged += 1
        extra = float(getattr(t, "fade_watch_hold_sec", 0) or 0)
        extra_holds.append(extra)
        mfe = float(b.mfe_pct or 0)
        if mfe > 0.01:
            capture_rates.append(round(t.realized_pnl_pct / mfe, 4))

    n = len(extended)
    decided = improved + worsened
    return {
        "fade_extension_count": n,
        "fade_extension_improved": improved,
        "fade_extension_worsened": worsened,
        "fade_extension_unchanged": unchanged,
        "fade_extension_worsened_rate": round(worsened / decided, 4) if decided else None,
        "fade_extension_precision": round(improved / decided, 4) if decided else None,
        "median_extra_hold_sec": round(statistics.median(extra_holds), 1) if extra_holds else None,
        "capture_rate_avg": round(statistics.mean(capture_rates), 4) if capture_rates else None,
    }


def _trade_details(
    shadow_trades: list[Any],
    baseline_trades: list[Any],
    *,
    scenario: str,
) -> list[dict[str, Any]]:
    base_by_key = {(t.symbol, t.entry_time): t for t in baseline_trades}
    rows: list[dict[str, Any]] = []
    for t in shadow_trades:
        b = base_by_key.get((t.symbol, t.entry_time))
        if not b:
            continue
        extended = bool(getattr(t, "fade_watch_entered", False))
        delta = round(t.realized_pnl_pct - b.realized_pnl_pct, 4)
        rows.append(
            {
                "scenario": scenario,
                "symbol": t.symbol,
                "entry_time": t.entry_time,
                "close_time": t.close_time,
                "baseline_close_reason": b.close_reason,
                "shadow_close_reason": t.close_reason,
                "baseline_pnl": b.realized_pnl_pct,
                "shadow_pnl": t.realized_pnl_pct,
                "pnl_delta": delta,
                "mfe_pct": b.mfe_pct,
                "fade_extension": extended,
                "fade_extension_improved": extended and delta > IMPROVE_EPS,
                "fade_extension_worsened": extended and delta < -IMPROVE_EPS,
                "extra_hold_sec": getattr(t, "fade_watch_hold_sec", 0),
                "reacceleration_detected": getattr(t, "reacceleration_detected", False),
                "new_high_after_fade": getattr(t, "new_high_after_fade", False),
                "new_mfe_created": getattr(t, "new_mfe_created", False),
                "momentum_recovery": getattr(t, "momentum_recovery", False),
                "breakdown_detected": getattr(t, "breakdown_detected", False),
                "quality": t.entry_quality,
            }
        )
    return rows


def _load_phase127_baseline(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def determine_verdict(
    comparison: Mapping[str, Any],
    *,
    insufficient_extension_rate: float,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    delta = float(comparison.get("delta_total_pnl") or 0)
    worsened = float(comparison.get("fade_extension_worsened_rate") or 0)
    ext_n = int(comparison.get("fade_extension_count") or 0)
    notes.append(
        f"delta={delta:.4f} ext={ext_n} worsened={worsened:.1%} "
        f"phase127_delta={comparison.get('phase127_delta_total_pnl')}"
    )

    if ext_n < 10:
        return "data_density_insufficient", notes + ["fade_extension_count too low"]

    if insufficient_extension_rate > 0.5:
        return "data_density_insufficient", notes + ["high insufficient tick rate in extensions"]

    if delta > 0.5 and worsened <= 0.15:
        return "reacceleration_shadow_promising", notes

    if delta > 0 and worsened <= 0.25:
        return "reacceleration_shadow_promising", notes + ["marginal replay gain"]

    if delta <= 0:
        return "review_only_gain_not_reproducible", notes + ["replay delta not positive"]

    if worsened > 0.30:
        return "current_exit_still_best", notes + ["worsened rate too high"]

    return "review_only_gain_not_reproducible", notes


def analyze_reacceleration_shadow(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
    phase127_report_path: Optional[Path] = None,
    include_phase127_fade_watch: bool = False,
) -> dict[str, Any]:
    from research.fade_watch_shadow import POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_WATCH_SHADOW

    all_a: list[Any] = []
    all_b: list[Any] = []
    all_p127: list[Any] = []
    per_session: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []

    for sdir in session_dirs:
        sdir = Path(sdir)
        events = _load_events(sdir)
        if not events:
            continue
        session_id = (
            str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        )
        interval = float(getattr(pilot_config, "poll_interval_sec", None) or 5.0)
        session_end = _session_end_time(events)

        trades_a, _ = replay_combined_structural_exit(
            events,
            pilot_config=pilot_config,
            poll_interval_sec=interval,
            session_end=session_end,
            structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1,
        )
        trades_b, log_b = replay_combined_structural_exit(
            events,
            pilot_config=pilot_config,
            poll_interval_sec=interval,
            session_end=session_end,
            structural_exit_policy=POLICY_REACCELERATION_SHADOW,
        )

        trades_p127: list[Any] = []
        if include_phase127_fade_watch:
            trades_p127, _ = replay_combined_structural_exit(
                events,
                pilot_config=pilot_config,
                poll_interval_sec=interval,
                session_end=session_end,
                structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_WATCH_SHADOW,
            )
            all_p127.extend(trades_p127)

        all_a.extend(trades_a)
        all_b.extend(trades_b)
        ma = _metrics(trades_a)
        mb = _metrics(trades_b)
        ext = _extension_stats(trades_b, trades_a)
        details.extend(_trade_details(trades_b, trades_a, scenario="reacceleration_shadow"))

        per_session.append(
            {
                "session_id": session_id,
                "A_combined_v1": ma,
                "B_reacceleration_shadow": mb,
                **ext,
                "delta_total_pnl": round(float(mb["total_pnl"]) - float(ma["total_pnl"]), 4),
                "reaccel_events": sum(
                    1 for e in log_b if str(e.get("event_kind", "")).startswith("reaccel")
                ),
            }
        )

    ma = _metrics(all_a)
    mb = _metrics(all_b)
    ext = _extension_stats(all_b, all_a)

    comparison = {
        **ma,
        "B_total_pnl": mb["total_pnl"],
        "B_avg_pnl": mb["avg_pnl"],
        "B_pf": mb["pf"],
        "B_win_rate": mb["win_rate"],
        "B_trade_count": mb["trade_count"],
        "delta_total_pnl": round(float(mb["total_pnl"]) - float(ma["total_pnl"]), 4),
        **ext,
        "mfe_gate": MFE_GATE,
        "gate_conditions": "mfe_pct > 0.15 AND NOT breakdown_at_fade",
    }

    p127_report = _load_phase127_baseline(phase127_report_path) if phase127_report_path else None
    if p127_report:
        p127_comp = p127_report.get("comparison") or {}
        comparison["phase127_fade_watch_total_pnl"] = p127_comp.get("B_total_pnl")
        comparison["phase127_delta_total_pnl"] = p127_comp.get("delta_total_pnl")
        comparison["phase127_fade_watch_count"] = p127_comp.get("fade_watch_count")
        comparison["phase127_worsened_count"] = p127_comp.get("fade_watch_worsened_count")

    if include_phase127_fade_watch and all_p127:
        mp127 = _metrics(all_p127)
        comparison["phase127_rerun_total_pnl"] = mp127["total_pnl"]
        comparison["phase127_rerun_delta"] = round(
            float(mp127["total_pnl"]) - float(ma["total_pnl"]), 4
        )

    ext_n = int(ext.get("fade_extension_count") or 0)
    insufficient_rate = 0.0
    if ext_n:
        short = sum(
            1 for d in details if d.get("fade_extension") and float(d.get("extra_hold_sec") or 0) <= 0
        )
        insufficient_rate = short / ext_n

    verdict, notes = determine_verdict(comparison, insufficient_extension_rate=insufficient_rate)

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "comparison": comparison,
        "sessions": per_session,
        "trade_details": details,
        "session_count": len(per_session),
    }
