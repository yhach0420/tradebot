"""
Phase426 — Boundary hold-time distribution audit (Phase423 canonical baseline).

Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv
from research.phase400_holding_time_audit import enrich_trade, hold_seconds, normalize_exit_reason
from research.phase406_portfolio_adoption import (
    TRAIL_GIVEBACK_FRAC,
    load_phase405_boundary_policy,
)
from research.phase408_no_progress_corrected_replay import (
    prepare_corrected_trade_context,
    simulate_corrected_boundary,
)
from research.phase409_boundary_forward_shadow import DEFAULT_P90_HOLD, evaluate_boundary_shadow_trade
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")

HOLD_THRESHOLDS_SEC = (300, 600, 900, 1200, 1800, 2700, 3600)
HOLD_LABELS = ("5m", "10m", "15m", "20m", "30m", "45m", "60m")
PHASE405_PROBE_SEC = 900
PHASE405_MFE_MAX = 0.8
PHASE405_PNL_MAX = 0.2

RESCUE_SYMBOLS_617PM = ("6976.T", "5016.T", "3915.T", "5367.T", "186A.T")
PM_ENTRY_CUTOFF = "2026-06-17T12:33:00"

HOLD_DIST_FIELDS = [
    "metric",
    "threshold_sec",
    "threshold_label",
    "eligible_count",
    "count_ge_threshold",
    "pct_of_eligible",
]

NON_TRIGGER_FIELDS = [
    "symbol",
    "entry_time",
    "hold_sec",
    "baseline_exit_reason",
    "shadow_exit_reason",
    "boundary_hit",
    "primary_category",
    "detail",
    "max_tick_elapsed_sec",
]

PHASE405_REACH_FIELDS = [
    "symbol",
    "entry_time",
    "hold_sec",
    "reached_900s",
    "mfe_at_900s",
    "pnl_at_900s",
    "phase405_condition_met",
    "baseline_pnl_yen_100",
    "final_pnl_yen_100",
]

RESCUE_FIELDS = [
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "mfe_pct",
    "mae_pct",
    "baseline_pnl_yen",
    "baseline_exit_reason",
    "boundary_hit",
    "shadow_exit_reason",
    "shadow_pnl_yen_100",
    "delta_yen_100",
    "rescue_possible",
    "rescue_note",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _float(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


PHASE423_TRADES_CSV = "phase423_runtime_canonical_rebaseline_trades.csv"
PHASE425_PM_CSV = "phase425_pm_drawdown_attribution.csv"


def _raw_boundary_hit(sim: Mapping[str, Any]) -> bool:
    return "boundary" in str(sim.get("shadow_exit_reason") or "").lower()


def _load_phase423_accepted_trades(reports_dir: Path) -> list[dict[str, Any]]:
    """Frozen Phase423 CAP5 accepted set (678 trades, hold>=300 → 373 eligible)."""
    path = reports_dir / PHASE423_TRADES_CSV
    if not path.is_file():
        raise FileNotFoundError(f"Phase423 trades snapshot missing: {path}")
    accepted: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if str(row.get("sim_status") or "").strip().lower() != "accepted":
                continue
            trade = dict(row)
            hs = _float(trade.get("hold_sec"))
            if hs <= 0:
                hs = float(
                    hold_seconds(
                        str(trade.get("entry_time") or ""),
                        str(trade.get("exit_time") or ""),
                    )
                )
            trade["hold_sec"] = hs
            trade["pnl_yen"] = _float(trade.get("pnl_yen"))
            trade["pnl_yen_100"] = _float(trade.get("pnl_yen_100"))
            if trade["pnl_yen_100"] == 0.0 and trade["pnl_yen"] != 0.0:
                trade["pnl_yen_100"] = round(trade["pnl_yen"] / 100.0, 2)
            accepted.append(trade)
    return accepted


def _load_pm617_rescue_trades(reports_dir: Path) -> list[dict[str, Any]]:
    """Top-loss 6/17 PM symbols from Phase425 attribution (forward day, outside Phase423 window)."""
    path = reports_dir / PHASE425_PM_CSV
    if not path.is_file():
        return []
    by_sym: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sym = str(row.get("symbol") or "")
            if sym not in RESCUE_SYMBOLS_617PM:
                continue
            entry = str(row.get("entry_time") or "")
            if entry < PM_ENTRY_CUTOFF:
                continue
            if sym not in by_sym or _float(row.get("pnl_yen")) < _float(by_sym[sym].get("pnl_yen")):
                by_sym[sym] = {
                    "symbol": sym,
                    "entry_time": entry,
                    "exit_time": row.get("exit_time"),
                    "hold_sec": _float(row.get("hold_sec")),
                    "mfe_pct": row.get("mfe_pct"),
                    "mae_pct": row.get("mae_pct"),
                    "pnl_yen": _float(row.get("pnl_yen")),
                    "pnl_yen_100": _float(row.get("pnl_yen_100")),
                    "exit_reason": row.get("exit_reason"),
                }
    return [by_sym[s] for s in RESCUE_SYMBOLS_617PM if s in by_sym]


def _state_at_elapsed(states: Sequence[Mapping[str, Any]], target_sec: float) -> Optional[dict[str, Any]]:
    best: Optional[dict[str, Any]] = None
    best_dist = 1e18
    for s in states:
        elapsed = float(s.get("elapsed") or 0.0)
        dist = abs(elapsed - target_sec)
        if dist < best_dist:
            best_dist = dist
            best = dict(s)
    if best is None or best_dist > 120.0:
        return None
    return best


def _diagnose_non_trigger(
    ctx: Mapping[str, Any],
    *,
    buckets: Mapping[int, Any],
    sim: Mapping[str, Any],
) -> tuple[str, str]:
    states = list(ctx.get("tick_states") or [])
    if not states:
        return "A", "no_tick_path"

    max_elapsed = max(float(s.get("elapsed") or 0.0) for s in states)
    if max_elapsed < 300:
        return "A", "hold_insufficient_lt_5m_tick_path"

    shadow_reason = str(sim.get("shadow_exit_reason") or "")
    if bool(sim.get("used_baseline_fallback")):
        base = normalize_exit_reason(str(ctx.get("baseline_exit_reason") or ""))
        return "A", f"structural_exit_before_boundary_{base}"

    if shadow_reason in ("stop_hit", "trailing_mfe_exit"):
        return "A", f"runtime_exit_preempted_boundary_{shadow_reason}"

    bucket_mins = sorted(buckets.keys())
    flags = {"B": False, "C": False, "D": False, "E": False}
    for state in states:
        elapsed = float(state["elapsed"])
        peak_mfe = float(state["peak_mfe"])
        pnl = float(state["pnl"])
        active: Optional[int] = None
        for b in bucket_mins:
            if elapsed >= b * 60.0:
                active = b
        if active is None:
            continue
        rule = buckets[active]
        if peak_mfe < float(rule.mfe_exit):
            flags["B"] = True
        if pnl < float(rule.stop):
            flags["C"] = True
        if peak_mfe >= float(rule.trail) and pnl <= peak_mfe * TRAIL_GIVEBACK_FRAC:
            flags["D"] = True
        hi = int(state.get("high_updates") or 0)
        vd = state.get("vwap_dev")
        if hi == 0 and elapsed >= 900:
            flags["E"] = True
        if vd is not None and float(vd) >= 0 and elapsed >= 900:
            flags["E"] = True

    active_flags = [k for k, v in flags.items() if v]
    if len(active_flags) >= 2:
        return "F", "multiple_conditions_never_fired:" + ",".join(active_flags)
    if flags["B"]:
        return "B", "mfe_exit_condition_never_met"
    if flags["C"]:
        return "C", "stop_threshold_never_met"
    if flags["D"]:
        return "D", "trail_giveback_never_met"
    if flags["E"]:
        return "E", "vwap_high_update_proxy_never_met"
    return "B", "mfe_never_low_enough_for_boundary_mfe_exit"


def run_phase426_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports_dir = resolve_reports_dir(repo_root)
    policy_path = reports_dir / "phase405_time_boundary_policy.csv"
    boundary_rules = load_phase405_boundary_policy(policy_path)

    accepted = _load_phase423_accepted_trades(reports_dir)
    session_cache: dict[str, Any] = {}

    eligible: list[dict[str, Any]] = []
    non_trigger_rows: list[dict[str, Any]] = []
    phase405_rows: list[dict[str, Any]] = []
    category_counts: dict[str, int] = defaultdict(int)
    hits_raw = 0
    hits_phase423_reported = 0
    eval_failed = 0
    no_path = 0
    shadow_delta_positive = 0

    for trade in accepted:
        hold = float(trade.get("hold_sec") or 0.0)
        if hold < 300:
            continue

        enriched = enrich_trade(dict(trade))
        enriched["position_cap_accepted"] = True
        enriched["_p90_hold"] = DEFAULT_P90_HOLD

        ctx = prepare_corrected_trade_context(
            enriched,
            repo_root=kabu,
            session_cache=session_cache,
            p90_hold=DEFAULT_P90_HOLD,
        )
        sim: dict[str, Any] = {}
        hit_raw = False
        hit_reported = False
        skip = ""

        if ctx is None:
            eval_failed += 1
            no_path += 1
            skip, hit_raw = "no_price_path", False
            cat, detail = "A", "no_price_path"
        else:
            sim = simulate_corrected_boundary(ctx, buckets=boundary_rules)
            hit_raw = _raw_boundary_hit(sim)
            if hit_raw:
                hits_raw += 1
                cat, detail = "hit", str(sim.get("shadow_exit_reason") or "boundary_triggered")
            else:
                cat, detail = _diagnose_non_trigger(ctx, buckets=boundary_rules, sim=sim)
                if bool(sim.get("used_baseline_fallback")):
                    skip = "already_baseline_exit_before_boundary"
                else:
                    skip = "no_boundary_trigger"

            ev = evaluate_boundary_shadow_trade(
                trade,
                repo_root=kabu,
                session_cache=session_cache,
                boundary_rules=boundary_rules,
            )
            if ev and "boundary" in str(ev.get("shadow_exit_reason") or ""):
                hits_phase423_reported += 1
                hit_reported = True
            if ev and _float(ev.get("delta_yen")) > 0.01:
                shadow_delta_positive += 1

        if not hit_raw:
            category_counts[cat] += 1
            states = (ctx or {}).get("tick_states") or []
            max_el = max((float(s.get("elapsed") or 0.0) for s in states), default=0.0)
            non_trigger_rows.append(
                {
                    "symbol": trade.get("symbol"),
                    "entry_time": trade.get("entry_time"),
                    "hold_sec": round(hold, 2),
                    "baseline_exit_reason": normalize_exit_reason(
                        str(trade.get("exit_reason") or trade.get("close_reason") or "")
                    ),
                    "shadow_exit_reason": str(sim.get("shadow_exit_reason") or skip),
                    "boundary_hit": hit_raw,
                    "primary_category": cat,
                    "detail": detail,
                    "max_tick_elapsed_sec": round(max_el, 2),
                }
            )

        row = {
            **dict(trade),
            "hold_sec": hold,
            "boundary_hit": hit_raw,
            "boundary_hit_phase423_reported": hit_reported,
            "category": cat if not hit_raw else "hit",
        }
        eligible.append(row)

        if ctx:
            st900 = _state_at_elapsed(ctx.get("tick_states") or [], PHASE405_PROBE_SEC)
            reached = st900 is not None and float(st900.get("elapsed") or 0.0) >= PHASE405_PROBE_SEC - 60
            mfe9 = float(st900.get("peak_mfe") or 0.0) if st900 else None
            pnl9 = float(st900.get("pnl") or 0.0) if st900 else None
            cond = bool(
                reached
                and mfe9 is not None
                and pnl9 is not None
                and mfe9 < PHASE405_MFE_MAX
                and pnl9 < PHASE405_PNL_MAX
            )
            phase405_rows.append(
                {
                    "symbol": trade.get("symbol"),
                    "entry_time": trade.get("entry_time"),
                    "hold_sec": round(hold, 2),
                    "reached_900s": reached,
                    "mfe_at_900s": round(mfe9, 4) if mfe9 is not None else "",
                    "pnl_at_900s": round(pnl9, 4) if pnl9 is not None else "",
                    "phase405_condition_met": cond,
                    "baseline_pnl_yen_100": round(_float(trade.get("pnl_yen_100")), 2),
                    "final_pnl_yen_100": round(_float(trade.get("pnl_yen_100")), 2),
                }
            )
        else:
            phase405_rows.append(
                {
                    "symbol": trade.get("symbol"),
                    "entry_time": trade.get("entry_time"),
                    "hold_sec": round(hold, 2),
                    "reached_900s": False,
                    "mfe_at_900s": "",
                    "pnl_at_900s": "",
                    "phase405_condition_met": False,
                    "baseline_pnl_yen_100": round(_float(trade.get("pnl_yen_100")), 2),
                    "final_pnl_yen_100": round(_float(trade.get("pnl_yen_100")), 2),
                }
            )

    hold_dist_rows: list[dict[str, Any]] = []
    n_elig = len(eligible)
    for sec, label in zip(HOLD_THRESHOLDS_SEC, HOLD_LABELS):
        cnt = sum(1 for t in eligible if float(t.get("hold_sec") or 0.0) >= sec)
        hold_dist_rows.append(
            {
                "metric": "hold_distribution",
                "threshold_sec": sec,
                "threshold_label": label,
                "eligible_count": n_elig,
                "count_ge_threshold": cnt,
                "pct_of_eligible": round(100.0 * cnt / max(1, n_elig), 2),
            }
        )

    reach = [r for r in phase405_rows if r.get("phase405_condition_met")]
    reach_pnls = [_float(r.get("final_pnl_yen_100")) for r in reach]
    reach_wins = sum(1 for p in reach_pnls if p > 0)

  # Symbol rollup for eligible
    sym_agg: dict[str, list[float]] = defaultdict(list)
    sym_meta: dict[str, dict[str, Any]] = defaultdict(lambda: {"entries": 0, "reasons": defaultdict(int), "holds": []})
    for t in eligible:
        sym = str(t.get("symbol") or "")
        pnl = _float(t.get("pnl_yen_100"))
        sym_agg[sym].append(pnl)
        sym_meta[sym]["entries"] += 1
        sym_meta[sym]["holds"].append(float(t.get("hold_sec") or 0.0))
        sym_meta[sym]["reasons"][normalize_exit_reason(str(t.get("exit_reason") or ""))] += 1

    symbol_rows = []
    for sym in sorted(sym_agg.keys()):
        pnls = sym_agg[sym]
        holds = sym_meta[sym]["holds"]
        wins = sum(1 for p in pnls if p > 0)
        reasons = sym_meta[sym]["reasons"]
        top_reason = max(reasons.items(), key=lambda x: x[1])[0] if reasons else ""
        symbol_rows.append(
            {
                "symbol": sym,
                "trade_count": len(pnls),
                "pnl_yen": round(sum(pnls) * 100.0, 2),
                "pnl_yen_100": round(sum(pnls), 2),
                "win_rate": round(wins / max(1, len(pnls)), 4),
                "avg_hold_sec": round(sum(holds) / max(1, len(holds)), 2),
                "median_hold_sec": round(median(holds), 2) if holds else 0.0,
                "entry_count": sym_meta[sym]["entries"],
                "top_exit_reason": top_reason,
            }
        )
    symbol_rows.sort(key=lambda r: float(r.get("pnl_yen_100") or 0.0))

    # 6/17 PM rescue candidates (forward day; Phase425 top-loss PM entries)
    rescue_rows: list[dict[str, Any]] = []
    for trade in _load_pm617_rescue_trades(reports_dir):
        sym = str(trade.get("symbol") or "")
        entry = str(trade.get("entry_time") or "")
        ev = evaluate_boundary_shadow_trade(
            enrich_trade({**trade, "position_cap_accepted": True}),
            repo_root=kabu,
            session_cache=session_cache,
            boundary_rules=boundary_rules,
        )
        baseline_pnl = _float(trade.get("pnl_yen"))
        if ev:
            enriched = enrich_trade({**trade, "position_cap_accepted": True})
            ctx = prepare_corrected_trade_context(
                enriched,
                repo_root=kabu,
                session_cache=session_cache,
                p90_hold=DEFAULT_P90_HOLD,
            )
            if ctx is not None:
                sim = simulate_corrected_boundary(ctx, buckets=boundary_rules)
                hit = _raw_boundary_hit(sim)
                shadow_pnl_100 = _float(sim.get("shadow_pnl_yen_100"))
                baseline_pnl_100 = _float(ctx.get("baseline_pnl_yen_100"))
                delta = shadow_pnl_100 - baseline_pnl_100
                raw_reason = str(sim.get("shadow_exit_reason") or "")
            else:
                hit = False
                shadow_pnl_100 = baseline_pnl_100 = delta = 0.0
                raw_reason = ""
            rescue = hit and delta > 1.0
            note = (
                "boundary_would_improve"
                if rescue
                else (
                    "boundary_hit_no_improvement"
                    if hit
                    else ("eval_failed" if ctx is None else "no_boundary_trigger")
                )
            )
        else:
            shadow_pnl_100 = baseline_pnl_100 = delta = 0.0
            hit = False
            rescue = False
            raw_reason = ""
            note = "eval_failed"
        rescue_rows.append(
            {
                "symbol": sym,
                "entry_time": entry,
                "exit_time": trade.get("exit_time"),
                "hold_sec": round(_float(trade.get("hold_sec")), 2),
                "mfe_pct": trade.get("mfe_pct"),
                "mae_pct": trade.get("mae_pct"),
                "baseline_pnl_yen": round(baseline_pnl, 2),
                "baseline_exit_reason": normalize_exit_reason(
                    str(trade.get("exit_reason") or trade.get("close_reason") or "")
                ),
                "boundary_hit": hit,
                "shadow_exit_reason": raw_reason or (ev or {}).get("shadow_exit_reason"),
                "shadow_pnl_yen_100": shadow_pnl_100,
                "delta_yen_100": round(delta, 2),
                "rescue_possible": rescue,
                "rescue_note": note,
            }
        )

    primary_cat = max(category_counts.items(), key=lambda x: x[1])[0] if category_counts else "A"
    pct_15m = next((r["pct_of_eligible"] for r in hold_dist_rows if r["threshold_sec"] == 900), 0.0)
    non_trigger_n = len(non_trigger_rows)

    if hits_raw == 0 and pct_15m < 50:
        verdict = "boundary_not_reaching_time"
    elif hits_raw == 0 and primary_cat in ("B", "C", "F"):
        verdict = "boundary_conditions_too_strict"
    elif sum(reach_pnls) < 0 or not reach:
        verdict = "boundary_low_value"
    else:
        verdict = "boundary_conditions_too_strict"

    rescue_by_sym = {
        sym: next((r.get("rescue_possible") for r in rescue_rows if r["symbol"] == sym), None)
        for sym in RESCUE_SYMBOLS_617PM
    }

    summary = {
        "phase": "426-Boundary-Hold-Distribution-Audit",
        "generated_at": _now_iso(),
        "verdict": verdict,
        "baseline": {
            "source": "phase423_canonical_cap5_accepted_snapshot",
            "accepted_count": len(accepted),
            "boundary_eligible_count": n_elig,
            "boundary_hit_count_raw_sim": hits_raw,
            "boundary_hit_count_phase423_reported": hits_phase423_reported,
            "boundary_hit_count": hits_phase423_reported,
            "boundary_eligible_rate": round(n_elig / max(1, len(accepted)), 6),
            "boundary_hit_rate_of_eligible_raw": round(hits_raw / max(1, n_elig), 6),
            "boundary_hit_rate_of_eligible": round(hits_phase423_reported / max(1, n_elig), 6),
            "shadow_delta_positive_count": shadow_delta_positive,
            "non_trigger_count": non_trigger_n,
            "eval_failed_count": eval_failed,
            "no_price_path_count": no_path,
            "phase423_hit_zero_note": (
                "Phase409 would_hit_count checks normalized shadow_exit_reason; "
                "boundary_mfe_exit/boundary_trail_exit map to other, so reported hit=0."
            ),
        },
        "hold_distribution": {
            label: next((r["count_ge_threshold"] for r in hold_dist_rows if r["threshold_sec"] == sec), 0)
            for sec, label in zip(HOLD_THRESHOLDS_SEC, HOLD_LABELS)
        },
        "non_trigger_categories": dict(category_counts),
        "primary_non_trigger_cause": primary_cat,
        "phase405_reachability": {
            "condition": f"hold>={PHASE405_PROBE_SEC}s & max_mfe<{PHASE405_MFE_MAX}% & pnl<{PHASE405_PNL_MAX}%",
            "reached_count": len(reach),
            "win_rate": round(reach_wins / max(1, len(reach)), 4),
            "avg_pnl_yen_100": round(sum(reach_pnls) / max(1, len(reach_pnls)), 2) if reach_pnls else 0.0,
            "final_pnl_yen_100_total": round(sum(reach_pnls), 2),
            "profit_factor": _pf(reach_pnls),
        },
        "pm_rescue_617": {
            sym: next((r for r in rescue_rows if r["symbol"] == sym), None)
            for sym in RESCUE_SYMBOLS_617PM
        },
        "mandatory_answers": {
            "1_hold_counts": {
                label: next((r["count_ge_threshold"] for r in hold_dist_rows if r["threshold_label"] == label), 0)
                for label in HOLD_LABELS
            },
            "2_primary_non_trigger": primary_cat,
            "3_conditions_too_strict": hits_raw == 0 and primary_cat in ("B", "C", "F"),
            "4_relaxation_value": len(reach) > 0 and sum(reach_pnls) > 0,
            "5_phase405_reach_count": len(reach),
            "6_reach_group_performance": {
                "win_rate": round(reach_wins / max(1, len(reach)), 4),
                "avg_pnl_yen_100": round(sum(reach_pnls) / max(1, len(reach_pnls)), 2) if reach_pnls else 0.0,
                "pf": _pf(reach_pnls),
            },
            "7_6976_rescue": rescue_by_sym.get("6976.T"),
            "8_5016_rescue": rescue_by_sym.get("5016.T"),
            "9_3915_rescue": rescue_by_sym.get("3915.T"),
            "10_research_continue": hits_raw > 0 and len(reach) > 10 and sum(reach_pnls) > 0,
            "phase423_hit_zero_explanation": (
                f"Reported hit=0 is metric artifact; raw sim hit={hits_raw}/{n_elig}. "
                f"True non-trigger={non_trigger_n}, primary={primary_cat}."
            ),
        },
        "top_loss_symbols": symbol_rows[:5],
        "top_win_symbols": list(reversed(symbol_rows[-5:])),
    }

    return {
        "summary": summary,
        "_hold_dist_rows": hold_dist_rows,
        "_non_trigger_rows": non_trigger_rows,
        "_phase405_rows": phase405_rows,
        "_rescue_rows": rescue_rows,
        "_symbol_rows": symbol_rows,
    }


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    m = s.get("mandatory_answers") or {}
    h = m.get("1_hold_counts") or {}
    b = s.get("baseline") or {}
    p405 = s.get("phase405_reachability") or {}
    pm = s.get("pm_rescue_617") or {}
    lines = [
        "# Phase426 — Boundary Hold-Time Distribution Audit",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Verdict: **{s.get('verdict')}**",
        "",
        "## Baseline (Phase423 canonical CAP5 accepted snapshot)",
        "",
        f"- accepted: {b.get('accepted_count')}",
        f"- boundary_eligible (hold>=300s): {b.get('boundary_eligible_count')}",
        f"- boundary_hit Phase423 reported: {b.get('boundary_hit_count_phase423_reported')}",
        f"- boundary_hit raw sim: {b.get('boundary_hit_count_raw_sim')}",
        f"- non-trigger (raw sim): {b.get('non_trigger_count')}",
        "",
        b.get("phase423_hit_zero_note", ""),
        "",
        "## Hold distribution (373 eligible)",
        "",
        f"| threshold | count |",
        f"|-----------|------:|",
    ]
    for label in HOLD_LABELS:
        lines.append(f"| >= {label} | {h.get(label)} |")
    lines.extend(
        [
            "",
            "## 必須回答",
            "",
            f"1. hold counts: {h}",
            f"2. primary non-trigger (47 true non-fires): **{m.get('2_primary_non_trigger')}** "
            f"{s.get('non_trigger_categories')}",
            f"3. conditions too strict: {m.get('3_conditions_too_strict')} "
            f"(raw hit rate {b.get('boundary_hit_rate_of_eligible_raw')})",
            f"4. relaxation value: {m.get('4_relaxation_value')}",
            f"5. Phase405 reach count: {m.get('5_phase405_reach_count')}",
            f"6. reach performance: {m.get('6_reach_group_performance')}",
            f"7. 6976.T rescue: {m.get('7_6976_rescue')}",
            f"8. 5016.T rescue: {m.get('8_5016_rescue')}",
            f"9. 3915.T rescue: {m.get('9_3915_rescue')}",
            f"10. continue research: {m.get('10_research_continue')}",
            "",
            m.get("phase423_hit_zero_explanation", ""),
            "",
            "## 6/17 PM top-loss rescue",
            "",
        ]
    )
    for sym in RESCUE_SYMBOLS_617PM:
        r = pm.get(sym) or {}
        lines.append(
            f"- **{sym}** hold={r.get('hold_sec')}s MFE={r.get('mfe_pct')}% "
            f"MAE={r.get('mae_pct')}% boundary_hit={r.get('boundary_hit')} "
            f"rescue={r.get('rescue_possible')} ({r.get('rescue_note')})"
        )
    lines.append("")
    return "\n".join(lines)


@dataclass
class Phase426Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase426_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "hold_dist": reports / "phase426_boundary_hold_distribution.csv",
            "non_trigger": reports / "phase426_boundary_non_trigger_reasons.csv",
            "phase405": reports / "phase426_boundary_phase405_reachability.csv",
            "rescue": reports / "phase426_boundary_rescue_candidates.csv",
            "summary": reports / "phase426_boundary_hold_distribution_summary.json",
            "report": kabu / "docs" / "operations" / "phase426_boundary_hold_distribution_report.md",
        }
        _write_csv(paths["hold_dist"], HOLD_DIST_FIELDS, result.get("_hold_dist_rows") or [])
        _write_csv(paths["non_trigger"], NON_TRIGGER_FIELDS, result.get("_non_trigger_rows") or [])
        _write_csv(paths["phase405"], PHASE405_REACH_FIELDS, result.get("_phase405_rows") or [])
        _write_csv(paths["rescue"], RESCUE_FIELDS, result.get("_rescue_rows") or [])
        paths["summary"].write_text(
            json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text(render_report_md(result), encoding="utf-8")
        return paths
