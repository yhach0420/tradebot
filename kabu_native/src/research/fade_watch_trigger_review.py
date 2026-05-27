"""
Phase 128: Restrict fade_watch trigger conditions — review only (no implementation).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.fade_watch_shadow import POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_WATCH_SHADOW
from research.mfe_mae_exit_review import as_float, parse_ts
from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1
from research.structural_observer_review import (
    _load_events,
    _session_end_time,
    replay_combined_structural_exit,
)

IMPROVE_EPS = 0.001
PNL_THRESHOLDS = (0.05, 0.10, 0.15)
MOMENTUM_THRESHOLDS = (0.30, 0.40, 0.50)


def _build_sym_timelines(events: Sequence[Mapping[str, Any]]) -> dict[str, list[tuple[float, dict[str, Any]]]]:
    by_sym: dict[str, list[tuple[float, dict[str, Any]]]] = defaultdict(list)
    for row in events:
        et = str(row.get("event_type") or "")
        if et not in ("accepted", "candidate"):
            continue
        sym = str(row.get("symbol") or "").strip()
        ts = parse_ts(str(row.get("event_time") or row.get("entry_time") or ""))
        if sym and ts > 0:
            by_sym[sym].append((ts, dict(row)))
    for sym in by_sym:
        by_sym[sym].sort(key=lambda x: x[0])
    return by_sym


def _nearest_snapshot(
    by_sym: dict[str, list[tuple[float, dict[str, Any]]]],
    symbol: str,
    target_ts: float,
    *,
    max_delta_sec: float = 15.0,
) -> Optional[dict[str, Any]]:
    items = by_sym.get(symbol)
    if not items:
        return None
    best: Optional[dict[str, Any]] = None
    best_d = 1e18
    for ts, row in items:
        d = abs(ts - target_ts)
        if d <= max_delta_sec and d < best_d:
            best_d = d
            best = row
    return best


def _overlap_replaced_before(
    trades: Sequence[Any],
    *,
    symbol: str,
    entry_ts: float,
) -> bool:
    for t in trades:
        if t.symbol != symbol:
            continue
        close_ts = parse_ts(t.close_time)
        if close_ts >= entry_ts or entry_ts - close_ts > 120:
            continue
        if t.close_reason == "overlap_replaced_review":
            return True
    return False


def _classify_outcome(baseline_pnl: float, shadow_pnl: float) -> str:
    delta = shadow_pnl - baseline_pnl
    if delta > IMPROVE_EPS:
        return "improved"
    if delta < -IMPROVE_EPS:
        return "worsened"
    return "unchanged"


def _mean_feature(rows: Sequence[Mapping[str, Any]], key: str) -> Optional[float]:
    vals = [as_float(r.get(key)) for r in rows]
    nums = [v for v in vals if v is not None]
    return round(statistics.mean(nums), 4) if nums else None


def _rate_bool(rows: Sequence[Mapping[str, Any]], key: str) -> Optional[float]:
    if not rows:
        return None
    trues = sum(1 for r in rows if r.get(key) in (True, "True", "true", 1, "1"))
    return round(trues / len(rows), 4)


def collect_fade_watch_rows(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for sdir in session_dirs:
        sdir = Path(sdir)
        events = _load_events(sdir)
        if not events:
            continue
        session_id = (
            str(sdir.relative_to(sdir.parent.parent))
            if sdir.parent.parent
            else sdir.name
        )
        sym_events = _build_sym_timelines(events)
        interval = float(getattr(pilot_config, "poll_interval_sec", None) or 5.0)
        session_end = _session_end_time(events)

        trades_a, _ = replay_combined_structural_exit(
            events,
            pilot_config=pilot_config,
            poll_interval_sec=interval,
            session_end=session_end,
            structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1,
        )
        trades_b, _ = replay_combined_structural_exit(
            events,
            pilot_config=pilot_config,
            poll_interval_sec=interval,
            session_end=session_end,
            structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_WATCH_SHADOW,
        )
        base_by_key = {(t.symbol, t.entry_time): t for t in trades_a}

        for shadow in trades_b:
            if not shadow.fade_watch_entered:
                continue
            baseline = base_by_key.get((shadow.symbol, shadow.entry_time))
            if baseline is None:
                continue

            baseline_pnl = float(baseline.realized_pnl_pct)
            shadow_pnl = float(shadow.realized_pnl_pct)
            delta = round(shadow_pnl - baseline_pnl, 4)
            outcome = _classify_outcome(baseline_pnl, shadow_pnl)

            fade_ts = parse_ts(shadow.fade_watch_entry_time or baseline.close_time)
            snap = _nearest_snapshot(sym_events, shadow.symbol, fade_ts)
            momentum = as_float(snap.get("momentum_continuation_score")) if snap else None
            rolling_mfe = as_float(snap.get("rolling_mfe_pct")) if snap else None
            rolling_mae = as_float(snap.get("rolling_mae_pct")) if snap else None
            vol_liq = as_float(snap.get("daytrade_suitability_score")) if snap else None

            entry_ts = parse_ts(shadow.entry_time)
            overlap = _overlap_replaced_before(trades_a, symbol=shadow.symbol, entry_ts=entry_ts)
            take_reached = bool(baseline.take_time)

            rows.append(
                {
                    "session_id": session_id,
                    "symbol": shadow.symbol,
                    "entry_time": shadow.entry_time,
                    "fade_watch_entry_time": shadow.fade_watch_entry_time,
                    "fade_watch_initial_reason": shadow.fade_watch_initial_reason,
                    "fade_watch_exit_reason": shadow.fade_watch_exit_reason,
                    "outcome": outcome,
                    "baseline_pnl": baseline_pnl,
                    "shadow_pnl": shadow_pnl,
                    "pnl_delta": delta,
                    "pnl_at_fade": baseline_pnl,
                    "momentum_at_fade": momentum,
                    "mfe_pct": baseline.mfe_pct,
                    "mae_pct": baseline.mae_pct,
                    "rolling_mfe_at_fade": rolling_mfe,
                    "rolling_mae_at_fade": rolling_mae,
                    "hold_sec": shadow.fade_watch_hold_sec,
                    "baseline_hold_sec": baseline.hold_duration_sec,
                    "quality": shadow.entry_quality,
                    "quality_tier": shadow.quality_tier,
                    "take_reached": take_reached,
                    "overlap": overlap,
                    "vol_liq_score": vol_liq,
                    "reacceleration_detected": shadow.reacceleration_detected,
                    "new_high_after_fade": shadow.new_high_after_fade,
                    "new_mfe_created": shadow.new_mfe_created,
                }
            )

    return rows


def compare_improved_vs_worsened(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    improved = [r for r in rows if r.get("outcome") == "improved"]
    worsened = [r for r in rows if r.get("outcome") == "worsened"]
    unchanged = [r for r in rows if r.get("outcome") == "unchanged"]

    def profile(name: str, grp: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return {
            "group": name,
            "count": len(grp),
            "avg_pnl_at_fade": _mean_feature(grp, "pnl_at_fade"),
            "avg_momentum_at_fade": _mean_feature(grp, "momentum_at_fade"),
            "avg_mfe_pct": _mean_feature(grp, "mfe_pct"),
            "avg_mae_pct": _mean_feature(grp, "mae_pct"),
            "avg_hold_sec": _mean_feature(grp, "hold_sec"),
            "avg_quality": _mean_feature(grp, "quality"),
            "avg_vol_liq_score": _mean_feature(grp, "vol_liq_score"),
            "take_reached_rate": _rate_bool(grp, "take_reached"),
            "overlap_rate": _rate_bool(grp, "overlap"),
            "total_pnl_delta": round(sum(float(r.get("pnl_delta") or 0) for r in grp), 4),
            "reacceleration_rate": _rate_bool(grp, "reacceleration_detected"),
            "new_high_rate": _rate_bool(grp, "new_high_after_fade"),
            "new_mfe_rate": _rate_bool(grp, "new_mfe_created"),
        }

    return {
        "improved": profile("improved", improved),
        "worsened": profile("worsened", worsened),
        "unchanged": profile("unchanged", unchanged),
    }


def _rule_matches(row: Mapping[str, Any], rule: Mapping[str, Any]) -> bool:
    pnl_thr = rule.get("pnl_at_fade_gt")
    if pnl_thr is not None:
        pnl = as_float(row.get("pnl_at_fade"))
        if pnl is None or not (pnl > float(pnl_thr)):
            return False
    mom_thr = rule.get("momentum_at_fade_gt")
    if mom_thr is not None:
        mom = as_float(row.get("momentum_at_fade"))
        if mom is None or not (mom > float(mom_thr)):
            return False
    mfe_thr = rule.get("mfe_pct_gt")
    if mfe_thr is not None:
        mfe = as_float(row.get("mfe_pct"))
        if mfe is None or not (mfe > float(mfe_thr)):
            return False
    if rule.get("take_reached") is not None and bool(row.get("take_reached")) != rule["take_reached"]:
        return False
    if rule.get("overlap") is not None and bool(row.get("overlap")) != rule["overlap"]:
        return False
    return True


def _eval_rule(rows: Sequence[Mapping[str, Any]], rule_id: str, rule: dict[str, Any]) -> dict[str, Any]:
    matched = [r for r in rows if _rule_matches(r, rule)]
    improved = sum(1 for r in matched if r.get("outcome") == "improved")
    worsened = sum(1 for r in matched if r.get("outcome") == "worsened")
    unchanged = sum(1 for r in matched if r.get("outcome") == "unchanged")
    total_delta = round(sum(float(r.get("pnl_delta") or 0) for r in matched), 4)
    decided = improved + worsened
    precision = round(improved / decided, 4) if decided else None
    return {
        "rule_id": rule_id,
        "rule": rule,
        "fade_watch_count": len(matched),
        "improved_count": improved,
        "worsened_count": worsened,
        "unchanged_count": unchanged,
        "total_pnl_delta": total_delta,
        "avg_pnl_delta": round(total_delta / len(matched), 4) if matched else None,
        "precision": precision,
        "worsened_rate": round(worsened / len(matched), 4) if matched else None,
        "coverage": round(len(matched) / len(rows), 4) if rows else None,
    }


def build_trigger_rules() -> list[tuple[str, dict[str, Any]]]:
    rules: list[tuple[str, dict[str, Any]]] = []
    rules.append(("unrestricted_all_fade", {}))

    for pnl in PNL_THRESHOLDS:
        rules.append((f"pnl_at_fade_gt_{pnl}", {"pnl_at_fade_gt": pnl}))

    for mom in MOMENTUM_THRESHOLDS:
        rules.append((f"momentum_at_fade_gt_{mom}", {"momentum_at_fade_gt": mom}))

    for pnl in PNL_THRESHOLDS:
        for mom in MOMENTUM_THRESHOLDS:
            rules.append(
                (
                    f"pnl_gt_{pnl}_mom_gt_{mom}",
                    {"pnl_at_fade_gt": pnl, "momentum_at_fade_gt": mom},
                )
            )

    for pnl in (0.05, 0.10, 0.15):
        rules.append(
            (f"pnl_gt_{pnl}_no_overlap", {"pnl_at_fade_gt": pnl, "overlap": False})
        )
        rules.append(
            (f"pnl_gt_{pnl}_take_reached", {"pnl_at_fade_gt": pnl, "take_reached": True})
        )

    for mfe in (0.10, 0.15, 0.20):
        rules.append((f"mfe_gt_{mfe}", {"mfe_pct_gt": mfe}))
        rules.append(
            (f"pnl_gt_0.05_mfe_gt_{mfe}", {"pnl_at_fade_gt": 0.05, "mfe_pct_gt": mfe})
        )

    return rules


def pareto_frontier(sensitivity: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Non-dominated on (fade_watch_count ↑, precision ↑, total_pnl_delta ↑)."""
    candidates = [
        s
        for s in sensitivity
        if int(s.get("fade_watch_count") or 0) > 0
        and s.get("precision") is not None
    ]
    frontier: list[dict[str, Any]] = []
    for a in candidates:
        dominated = False
        for b in candidates:
            if a is b:
                continue
            a_n = int(a.get("fade_watch_count") or 0)
            b_n = int(b.get("fade_watch_count") or 0)
            a_p = float(a.get("precision") or 0)
            b_p = float(b.get("precision") or 0)
            a_d = float(a.get("total_pnl_delta") or 0)
            b_d = float(b.get("total_pnl_delta") or 0)
            if (
                b_n >= a_n
                and b_p >= a_p
                and b_d >= a_d
                and (b_n > a_n or b_p > a_p or b_d > a_d)
            ):
                dominated = True
                break
        if not dominated:
            frontier.append(dict(a))
    frontier.sort(
        key=lambda r: (
            float(r.get("total_pnl_delta") or -1e9),
            float(r.get("precision") or 0),
            int(r.get("fade_watch_count") or 0),
        ),
        reverse=True,
    )
    return frontier


def determine_verdict(
    rows: Sequence[Mapping[str, Any]],
    comparison: Mapping[str, Any],
    sensitivity: Sequence[Mapping[str, Any]],
    frontier: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    n = len(rows)
    if n == 0:
        return "fade_watch_not_worth_it", ["no fade_watch trades"]

    unrestricted = next((s for s in sensitivity if s.get("rule_id") == "unrestricted_all_fade"), None)
    all_delta = float(unrestricted.get("total_pnl_delta") or 0) if unrestricted else 0.0
    notes.append(f"fade_watch_n={n} unrestricted_delta={all_delta:.4f}")

    restricted = [s for s in sensitivity if s.get("rule_id") != "unrestricted_all_fade"]
    positive = [s for s in restricted if float(s.get("total_pnl_delta") or 0) > 0]
    positive.sort(
        key=lambda r: (float(r.get("total_pnl_delta") or 0), float(r.get("precision") or 0)),
        reverse=True,
    )

    imp = comparison.get("improved") or {}
    wor = comparison.get("worsened") or {}
    pnl_sep = abs(float(imp.get("avg_pnl_at_fade") or 0) - float(wor.get("avg_pnl_at_fade") or 0))
    mom_sep = abs(
        float(imp.get("avg_momentum_at_fade") or 0) - float(wor.get("avg_momentum_at_fade") or 0)
    )
    notes.append(
        f"improved={imp.get('count')} worsened={wor.get('count')} "
        f"pnl_sep={pnl_sep:.3f} mom_sep={mom_sep:.3f}"
    )

    if pnl_sep < 0.03 and mom_sep < 0.05 and not positive:
        return "need_more_features", notes + ["improved/worsened not separable on pnl/momentum"]

    if not positive:
        return "fade_watch_not_worth_it", notes + ["no restricted rule with positive total_pnl_delta"]

    best = positive[0]
    notes.append(
        f"best_rule={best.get('rule_id')} delta={best.get('total_pnl_delta')} "
        f"precision={best.get('precision')} n={best.get('fade_watch_count')}"
    )

    if (
        float(best.get("total_pnl_delta") or 0) > 0
        and float(best.get("precision") or 0) >= 0.45
        and int(best.get("fade_watch_count") or 0) >= 20
    ):
        return "restricted_fade_watch_promising", notes

    if float(best.get("total_pnl_delta") or 0) > 0:
        return "trigger_conditions_still_too_weak", notes + ["positive delta but weak precision or coverage"]

    return "trigger_conditions_still_too_weak", notes


def analyze_fade_watch_triggers(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
) -> dict[str, Any]:
    rows = collect_fade_watch_rows(session_dirs, pilot_config=pilot_config)
    comparison = compare_improved_vs_worsened(rows)

    sensitivity: list[dict[str, Any]] = []
    for rule_id, rule in build_trigger_rules():
        sensitivity.append(_eval_rule(rows, rule_id, rule))
    sensitivity.sort(
        key=lambda r: (float(r.get("total_pnl_delta") or -1e9), float(r.get("precision") or 0)),
        reverse=True,
    )

    frontier = pareto_frontier(sensitivity)
    verdict, verdict_notes = determine_verdict(rows, comparison, sensitivity, frontier)

    return {
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "fade_watch_count": len(rows),
        "group_comparison": comparison,
        "sensitivity": sensitivity,
        "pareto_frontier": frontier,
        "improved_vs_worsened_rows": rows,
    }
