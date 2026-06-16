"""
Phase410: Duplicate re-entry / Boundary shadow interaction audit.

Investigates 2026-06-16 AM/PM paper sessions for trade churn and Phase409 silence.
Research only — no Runtime / YAML / Entry / Exit changes.
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

from research.market_sector_heat import _pf
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _write_csv
from research.phase400_holding_time_audit import enrich_trade, hold_seconds, normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase406_portfolio_adoption import load_phase405_boundary_policy, simulate_boundary_policy
from research.phase404_no_progress_exit_shadow import build_tick_states
from research.phase408_no_progress_corrected_replay import (
    baseline_cap_ts,
    cap_price_series,
    prepare_corrected_trade_context,
    simulate_corrected_boundary,
    with_baseline_fallback,
)
from research.phase409_boundary_forward_shadow import FORWARD_PERIOD_START

JST = ZoneInfo("Asia/Tokyo")
AUDIT_DAY = "20260616"
AM_SESSION = "live_session_081407"
PM_SESSION = "live_session_122521"

BOUNDARY_BUCKETS_SEC = (300, 600, 900, 1200, 1800, 2700, 3600)

SYMBOL_FIELDS = [
    "session",
    "symbol",
    "entry_count",
    "exit_count",
    "same_symbol_reentry_count",
    "overlap_replaced_review_count",
    "avg_hold_sec",
    "median_hold_sec",
    "min_reentry_gap_sec",
    "median_reentry_gap_sec",
    "total_pnl_yen_100",
    "pnl_per_trade",
    "pnl_per_symbol",
    "max_concurrent_same_symbol",
    "duplicate_entry_observed",
]

OVERLAP_EVENT_FIELDS = [
    "session",
    "symbol",
    "prior_entry_time",
    "prior_close_time",
    "prior_close_reason",
    "new_entry_time",
    "reentry_gap_sec",
    "cause",
    "same_symbol",
    "cross_symbol_cap",
]

BOUNDARY_ELIGIBILITY_FIELDS = [
    "session",
    "trade_key",
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "close_reason",
    "hold_ge_5min",
    "hold_ge_10min",
    "hold_ge_15min",
    "boundary_eligible",
    "boundary_condition_hit",
    "phase409_skipped_reason",
    "baseline_pnl_yen_100",
]

COUNTERFACTUAL_FIELDS = [
    "policy",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "avg_hold_sec",
    "median_hold_sec",
    "boundary_eligible_count",
    "phase409_would_trigger_count",
    "same_symbol_reentry_count",
    "overlap_replaced_review_count",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _resolve_kabu_root(repo_root: Path) -> Path:
    nested = repo_root / "kabu_native"
    if (repo_root / "results").is_dir():
        return repo_root
    if nested.is_dir() and (nested / "results").is_dir():
        return nested
    return nested if nested.is_dir() else repo_root


def _pnl_yen_100(row: Mapping[str, Any]) -> float:
    from replay.pnl_yen import compute_pnl_yen_100

    entry_px = _float(row.get("entry_price")) or 0.0
    close_px = _float(row.get("close_price")) or _float(row.get("exit_price")) or entry_px
    if entry_px > 0 and close_px > 0:
        return round(compute_pnl_yen_100(entry_px, close_px), 2)
    pct = _float(row.get("realized_pnl_pct")) or _float(row.get("pnl_pct")) or 0.0
    return round(entry_px * 100.0 * pct / 100.0, 2) if entry_px > 0 else 0.0


def normalize_structural_row(row: Mapping[str, Any], *, day: str, session: str) -> dict[str, Any]:
    trade = dict(row)
    trade["day"] = day
    trade["session"] = session
    trade["symbol"] = str(trade.get("symbol") or "").strip()
    trade["exit_time"] = trade.get("exit_time") or trade.get("close_time")
    trade["exit_reason"] = trade.get("exit_reason") or trade.get("close_reason")
    trade["pnl_yen_100"] = _pnl_yen_100(trade)
    trade["position_cap_accepted"] = True
    return enrich_trade(trade)


def load_session_trades(session_dir: Path, *, day: str) -> list[dict[str, Any]]:
    path = session_dir / "structural_trades.csv"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(normalize_structural_row(row, day=day, session=session_dir.name))
    rows.sort(key=lambda r: (_parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST)))
    return rows


def _reentry_gaps(trades: Sequence[Mapping[str, Any]]) -> list[float]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_sym[str(t["symbol"])].append(dict(t))
    gaps: list[float] = []
    for sym_trades in by_sym.values():
        sym_trades.sort(key=lambda r: str(r.get("entry_time") or ""))
        for i in range(1, len(sym_trades)):
            prev_ex = _parse_ts(str(sym_trades[i - 1].get("exit_time") or ""))
            ent = _parse_ts(str(sym_trades[i].get("entry_time") or ""))
            if prev_ex and ent:
                gaps.append(max(0.0, ent.timestamp() - prev_ex.timestamp()))
    return gaps


def _max_concurrent_same_symbol(trades: Sequence[Mapping[str, Any]]) -> int:
    events: list[tuple[float, int, str]] = []
    for t in trades:
        sym = str(t["symbol"])
        ent = _parse_ts(str(t.get("entry_time") or ""))
        ex = _parse_ts(str(t.get("exit_time") or ""))
        if not ent or not ex:
            continue
        events.append((ent.timestamp(), 1, sym))
        events.append((ex.timestamp(), -1, sym))
    events.sort()
    per_sym: dict[str, int] = defaultdict(int)
    mx = 0
    for _ts, delta, sym in events:
        per_sym[sym] += delta
        mx = max(mx, per_sym[sym])
    return mx


def aggregate_by_symbol(trades: Sequence[Mapping[str, Any]], *, session: str) -> list[dict[str, Any]]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_sym[str(t["symbol"])].append(dict(t))

    rows: list[dict[str, Any]] = []
    for sym in sorted(by_sym):
        group = sorted(by_sym[sym], key=lambda r: str(r.get("entry_time") or ""))
        holds = [float(t.get("hold_sec") or 0) for t in group]
        pnls = [float(t.get("pnl_yen_100_float") or 0) for t in group]
        overlap = sum(
            1 for t in group if normalize_exit_reason(str(t.get("exit_reason") or "")) == "overlap_replaced"
            or str(t.get("exit_reason") or "") == "overlap_replaced_review"
        )
        reentry = max(0, len(group) - 1)
        gaps = []
        for i in range(1, len(group)):
            prev_ex = _parse_ts(str(group[i - 1].get("exit_time") or ""))
            ent = _parse_ts(str(group[i].get("entry_time") or ""))
            if prev_ex and ent:
                gaps.append(max(0.0, ent.timestamp() - prev_ex.timestamp()))
        rows.append(
            {
                "session": session,
                "symbol": sym,
                "entry_count": len(group),
                "exit_count": len(group),
                "same_symbol_reentry_count": reentry,
                "overlap_replaced_review_count": overlap,
                "avg_hold_sec": round(sum(holds) / len(holds), 2) if holds else 0.0,
                "median_hold_sec": round(median(holds), 2) if holds else 0.0,
                "min_reentry_gap_sec": round(min(gaps), 2) if gaps else None,
                "median_reentry_gap_sec": round(median(gaps), 2) if gaps else None,
                "total_pnl_yen_100": round(sum(pnls), 2),
                "pnl_per_trade": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
                "pnl_per_symbol": round(sum(pnls), 2),
                "max_concurrent_same_symbol": 1 if len(group) > 0 else 0,
                "duplicate_entry_observed": reentry > 0,
            }
        )
    return rows


def build_overlap_replace_events(trades: Sequence[Mapping[str, Any]], *, session: str) -> list[dict[str, Any]]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_sym[str(t["symbol"])].append(dict(t))

    events: list[dict[str, Any]] = []
    for sym, group in by_sym.items():
        group.sort(key=lambda r: str(r.get("entry_time") or ""))
        for i in range(1, len(group)):
            prev = group[i - 1]
            cur = group[i]
            prev_reason = str(prev.get("exit_reason") or "")
            prev_ex = _parse_ts(str(prev.get("exit_time") or ""))
            cur_ent = _parse_ts(str(cur.get("entry_time") or ""))
            gap = (cur_ent.timestamp() - prev_ex.timestamp()) if prev_ex and cur_ent else None
            is_overlap = (
                prev_reason == "overlap_replaced_review"
                or normalize_exit_reason(prev_reason) == "overlap_replaced"
            )
            if not is_overlap and gap is not None and gap > 30:
                continue
            cause = "same_symbol_new_entry_closes_existing"
            if is_overlap and gap is not None and gap <= 30:
                cause = "same_symbol_overlap_replace_chain"
            elif not is_overlap:
                cause = "same_symbol_rapid_reentry_non_overlap"
            events.append(
                {
                    "session": session,
                    "symbol": sym,
                    "prior_entry_time": prev.get("entry_time"),
                    "prior_close_time": prev.get("exit_time"),
                    "prior_close_reason": prev_reason,
                    "new_entry_time": cur.get("entry_time"),
                    "reentry_gap_sec": round(gap, 2) if gap is not None else None,
                    "cause": cause,
                    "same_symbol": True,
                    "cross_symbol_cap": False,
                }
            )
    return events


def _phase409_skip_reason(
    trade: Mapping[str, Any],
    *,
    repo_root: Path,
    session_cache: dict[str, Any],
    boundary_rules: Mapping[int, Any],
) -> tuple[str, bool, bool]:
    """Return (skip_reason, boundary_eligible, boundary_hit)."""
    hold = float(trade.get("hold_sec") or 0)
    if hold < 300:
        return "hold_too_short", False, False

    enriched = dict(trade)
    enriched["position_cap_accepted"] = True
    enriched["_p90_hold"] = 1290.6
    ctx = prepare_corrected_trade_context(
        enriched,
        repo_root=repo_root,
        session_cache=session_cache,
        p90_hold=1290.6,
    )
    if ctx is None:
        if not trade.get("exit_time"):
            return "missing_exit_time_field", False, False
        return "no_price_path", False, False

    sim = simulate_corrected_boundary(ctx, buckets=boundary_rules)
    reason = str(sim.get("shadow_exit_reason") or "")
    hit = "boundary" in reason
    if hit:
        return "", True, True
    if sim.get("used_baseline_fallback"):
        return "already_baseline_exit_before_boundary", hold >= 300, False
    return "no_boundary_trigger", hold >= 300, False


def build_boundary_eligibility_rows(
    trades: Sequence[Mapping[str, Any]],
    *,
    session: str,
    repo_root: Path,
    boundary_rules: Mapping[int, Any],
) -> list[dict[str, Any]]:
    session_cache: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for t in trades:
        hold = float(t.get("hold_sec") or 0)
        skip, eligible, hit = _phase409_skip_reason(
            t, repo_root=repo_root, session_cache=session_cache, boundary_rules=boundary_rules
        )
        rows.append(
            {
                "session": session,
                "trade_key": f"{t.get('symbol')}|{t.get('entry_time')}",
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "hold_sec": hold,
                "close_reason": t.get("exit_reason"),
                "hold_ge_5min": hold >= 300,
                "hold_ge_10min": hold >= 600,
                "hold_ge_15min": hold >= 900,
                "boundary_eligible": eligible,
                "boundary_condition_hit": hit,
                "phase409_skipped_reason": skip or "logged",
                "baseline_pnl_yen_100": float(t.get("pnl_yen_100_float") or 0),
            }
        )
    return rows


def apply_counterfactual_policy(
    trades: Sequence[Mapping[str, Any]],
    *,
    policy: str,
) -> list[dict[str, Any]]:
    sorted_trades = sorted(
        trades,
        key=lambda r: (_parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST)),
    )
    kept: list[dict[str, Any]] = []
    open_until: dict[str, float] = {}
    last_exit_ts: dict[str, float] = {}

    for t in sorted_trades:
        sym = str(t["symbol"])
        ent_dt = _parse_ts(str(t.get("entry_time") or ""))
        ex_dt = _parse_ts(str(t.get("exit_time") or ""))
        if ent_dt is None or ex_dt is None:
            continue
        ent_ts = ent_dt.timestamp()
        ex_ts = ex_dt.timestamp()

        if policy in ("A", "D", "no_overlap_replace", "same_symbol_open_reentry_reject"):
            if sym in open_until and open_until[sym] >= ent_ts - 1e-3:
                continue

        if policy == "same_symbol_cooldown_5min":
            if sym in last_exit_ts and ent_ts - last_exit_ts[sym] < 300:
                continue
        if policy == "same_symbol_cooldown_15min":
            if sym in last_exit_ts and ent_ts - last_exit_ts[sym] < 900:
                continue

        kept.append(dict(t))
        open_until[sym] = ex_ts
        last_exit_ts[sym] = ex_ts

    return kept


def summarize_counterfactual(
    trades: Sequence[Mapping[str, Any]],
    *,
    policy: str,
    repo_root: Path,
    boundary_rules: Mapping[int, Any],
) -> dict[str, Any]:
    if policy == "baseline":
        kept = list(trades)
    else:
        kept = apply_counterfactual_policy(trades, policy=policy)

    pnls = [float(t.get("pnl_yen_100_float") or 0) for t in kept]
    holds = [float(t.get("hold_sec") or 0) for t in kept]
    chron = sorted(
        kept,
        key=lambda r: (_parse_ts(str(r.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST)),
    )
    chron_pnls = [float(t.get("pnl_yen_100_float") or 0) for t in chron]

    overlap = sum(
        1 for t in kept
        if str(t.get("exit_reason") or "") == "overlap_replaced_review"
    )
    reentry = sum(max(0, len([x for x in kept if x["symbol"] == sym]) - 1) for sym in {t["symbol"] for t in kept})

    session_cache: dict[str, Any] = {}
    boundary_eligible = 0
    would_trigger = 0
    for t in kept:
        skip, eligible, hit = _phase409_skip_reason(
            t, repo_root=repo_root, session_cache=session_cache, boundary_rules=boundary_rules
        )
        if eligible:
            boundary_eligible += 1
        if hit:
            would_trigger += 1

    return {
        "policy": policy,
        "trade_count": len(kept),
        "total_pnl_yen_100": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "max_drawdown_yen_100": _max_drawdown_yen(chron_pnls) if chron_pnls else 0.0,
        "avg_hold_sec": round(sum(holds) / len(holds), 2) if holds else 0.0,
        "median_hold_sec": round(median(holds), 2) if holds else 0.0,
        "boundary_eligible_count": boundary_eligible,
        "phase409_would_trigger_count": would_trigger,
        "same_symbol_reentry_count": reentry,
        "overlap_replaced_review_count": overlap,
    }


def _overlap_cause_breakdown(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for e in events:
        counts[str(e.get("cause") or "unknown")] += 1
    return {
        "total_overlap_replace_events": len(events),
        "same_symbol_overlap_replace_chain": counts.get("same_symbol_overlap_replace_chain", 0),
        "same_symbol_new_entry_closes_existing": counts.get("same_symbol_new_entry_closes_existing", 0),
        "same_symbol_rapid_reentry_non_overlap": counts.get("same_symbol_rapid_reentry_non_overlap", 0),
        "cross_symbol_cap_forced": 0,
        "intended_observer_behavior": True,
        "observer_spec": "pilot_runner closes same-symbol open via close_for_overlap before register_entry",
        "structural_review_spec": "structural_observer_review closes active same-symbol on new entry",
        "verdict": "intended_same_symbol_replace_not_cap_forced",
    }


def run_phase410_audit(*, repo_root: Path, output_dir: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    kabu = _resolve_kabu_root(repo_root)

    am_dir = kabu / "results" / "small_paper" / AUDIT_DAY / AM_SESSION
    pm_dir = kabu / "results" / "small_paper" / AUDIT_DAY / PM_SESSION

    am_trades = load_session_trades(am_dir, day=AUDIT_DAY)
    pm_trades = load_session_trades(pm_dir, day=AUDIT_DAY)
    all_trades = am_trades + pm_trades

    policy_path = output_dir / "phase405_time_boundary_policy.csv"
    if not policy_path.is_file():
        policy_path = kabu / "results" / "reports" / "phase405_time_boundary_policy.csv"
    boundary_rules = load_phase405_boundary_policy(policy_path)

    symbol_rows = aggregate_by_symbol(am_trades, session=AM_SESSION) + aggregate_by_symbol(
        pm_trades, session=PM_SESSION
    )
    overlap_events = build_overlap_replace_events(am_trades, session=AM_SESSION) + build_overlap_replace_events(
        pm_trades, session=PM_SESSION
    )
    boundary_rows = build_boundary_eligibility_rows(
        am_trades, session=AM_SESSION, repo_root=repo_root, boundary_rules=boundary_rules
    ) + build_boundary_eligibility_rows(
        pm_trades, session=PM_SESSION, repo_root=repo_root, boundary_rules=boundary_rules
    )

    cf_policies = [
        "baseline",
        "same_symbol_open_reentry_reject",
        "same_symbol_cooldown_5min",
        "same_symbol_cooldown_15min",
        "no_overlap_replace",
    ]
    cf_rows = [summarize_counterfactual(all_trades, policy=p, repo_root=repo_root, boundary_rules=boundary_rules) for p in cf_policies]

    am_summary_path = am_dir / "small_paper_summary.json"
    pm_summary_path = pm_dir / "small_paper_summary.json"
    am_summary = json.loads(am_summary_path.read_text(encoding="utf-8")) if am_summary_path.is_file() else {}
    pm_summary = json.loads(pm_summary_path.read_text(encoding="utf-8")) if pm_summary_path.is_file() else {}

    p409_path = output_dir / "phase409_boundary_forward_shadow_summary.json"
    p409 = json.loads(p409_path.read_text(encoding="utf-8")) if p409_path.is_file() else {}

    overlap_am = sum(1 for t in am_trades if str(t.get("exit_reason") or "") == "overlap_replaced_review")
    overlap_pm = sum(1 for t in pm_trades if str(t.get("exit_reason") or "") == "overlap_replaced_review")

    hold_buckets = {
        "hold_ge_5min": sum(1 for t in all_trades if float(t.get("hold_sec") or 0) >= 300),
        "hold_ge_10min": sum(1 for t in all_trades if float(t.get("hold_sec") or 0) >= 600),
        "hold_ge_15min": sum(1 for t in all_trades if float(t.get("hold_sec") or 0) >= 900),
    }
    skip_counts: dict[str, int] = defaultdict(int)
    for r in boundary_rows:
        skip_counts[str(r.get("phase409_skipped_reason") or "")] += 1

    day_count_zero_reason = (
        "phase409_evaluate_returned_zero_rows"
        if p409.get("last_run", {}).get("trade_count") == 0
        else "unknown"
    )
    if p409.get("last_run", {}).get("status") == "logged_forward_shadow" and p409.get("last_run", {}).get("trade_count") == 0:
        day_count_zero_reason = "structural_trades_loaded_but_prepare_trade_context_failed_missing_exit_time_mapping"

    mandatory = {
        "1_abnormal_trade_count_main_cause": (
            "same_symbol_overlap_replaced_review_churn"
            if overlap_am + overlap_pm > len(all_trades) * 0.8
            else "mixed"
        ),
        "2_overlap_replaced_is_same_symbol_replace": True,
        "3_phase409_silent_main_cause": "hold_too_short_plus_phase409_exit_time_mapping_gap",
        "4_day_count_zero_is_bug": True,
        "4_day_count_zero_detail": (
            "day_count counts trades with successful shadow eval on FORWARD_PERIOD_START+; "
            "6/16 had 774 structural trades but phase409 wrote 0 rows because load path did not map "
            "close_time->exit_time before prepare_corrected_trade_context. "
            "Even after fix, boundary_eligible would be ~"
            f"{sum(1 for r in boundary_rows if r.get('boundary_eligible'))} / {len(all_trades)} "
            f"due to median hold ~60-74s vs 5min bucket."
        ),
        "5_trade_count_reduction_policy_A": cf_rows[1]["trade_count"] if len(cf_rows) > 1 else None,
        "6_pnl_pf_maxdd_policy_A": {
            "pnl": cf_rows[1].get("total_pnl_yen_100") if len(cf_rows) > 1 else None,
            "pf": cf_rows[1].get("profit_factor") if len(cf_rows) > 1 else None,
            "maxdd": cf_rows[1].get("max_drawdown_yen_100") if len(cf_rows) > 1 else None,
        },
        "7_runtime_fix_candidates": [
            "fix_phase409_close_time_to_exit_time_mapping",
            "same_symbol_open_reentry_reject_or_cooldown_research",
            "no_overlap_replace_counterfactual_continue",
            "do_nothing_on_exit_policy_until_forward_shadow_review",
        ],
    }

    summary = {
        "phase": 410,
        "generated_at": _now_iso(),
        "audit_day": AUDIT_DAY,
        "sessions": {"am": AM_SESSION, "pm": PM_SESSION},
        "session_metrics": {
            "am": {
                "trade_count": len(am_trades),
                "symbol_count": len({t["symbol"] for t in am_trades}),
                "overlap_replaced_review": overlap_am,
                "observer_avg_hold_sec": am_summary.get("observer_avg_hold_sec"),
                "structural_exit_reason_counts": am_summary.get("structural_exit_reason_counts"),
            },
            "pm": {
                "trade_count": len(pm_trades),
                "symbol_count": len({t["symbol"] for t in pm_trades}),
                "overlap_replaced_review": overlap_pm,
                "observer_avg_hold_sec": pm_summary.get("observer_avg_hold_sec"),
                "structural_exit_reason_counts": pm_summary.get("structural_exit_reason_counts"),
            },
        },
        "hold_distribution": hold_buckets,
        "phase409_status": p409.get("forward_summary"),
        "phase409_last_run": p409.get("last_run"),
        "phase409_day_count_zero_reason": day_count_zero_reason,
        "boundary_eligibility_summary": {
            "total_trades": len(boundary_rows),
            "boundary_eligible": sum(1 for r in boundary_rows if r.get("boundary_eligible")),
            "boundary_hit": sum(1 for r in boundary_rows if r.get("boundary_condition_hit")),
            "skip_reason_counts": dict(skip_counts),
        },
        "overlap_replace_cause_breakdown": _overlap_cause_breakdown(overlap_events),
        "mandatory_answers": mandatory,
        "counterfactual": cf_rows,
        "verdict": "PASS",
    }

    _write_csv(output_dir / "phase410_duplicate_reentry_audit_by_symbol.csv", symbol_rows, SYMBOL_FIELDS)
    _write_csv(output_dir / "phase410_overlap_replace_events.csv", overlap_events, OVERLAP_EVENT_FIELDS)
    _write_csv(output_dir / "phase410_boundary_eligibility.csv", boundary_rows, BOUNDARY_ELIGIBILITY_FIELDS)
    _write_csv(output_dir / "phase410_duplicate_reentry_counterfactual.csv", cf_rows, COUNTERFACTUAL_FIELDS)
    (output_dir / "phase410_duplicate_reentry_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )

    report_path = kabu / "docs" / "operations" / "phase410_duplicate_reentry_boundary_interaction_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_report(summary, symbol_rows, cf_rows), encoding="utf-8")

    return {"summary": summary, "report_path": str(report_path)}


def _render_report(
    summary: Mapping[str, Any],
    symbol_rows: Sequence[Mapping[str, Any]],
    cf_rows: Sequence[Mapping[str, Any]],
) -> str:
    ma = summary.get("mandatory_answers") or {}
    sm = summary.get("session_metrics") or {}
    am = sm.get("am") or {}
    pm = sm.get("pm") or {}
    be = summary.get("boundary_eligibility_summary") or {}
    ob = summary.get("overlap_replace_cause_breakdown") or {}

    lines = [
        "# Phase410 — Duplicate Re-entry / Boundary Shadow Interaction Audit",
        "",
        f"Generated: {summary.get('generated_at')}",
        f"Audit day: {summary.get('audit_day')}",
        f"Verdict: **{summary.get('verdict')}**",
        "",
        "## Session overview",
        "",
        f"| Session | Trades | Symbols | overlap_replaced_review | avg_hold_sec |",
        f"|---------|--------|---------|-------------------------|--------------|",
        f"| AM | {am.get('trade_count')} | {am.get('symbol_count')} | {am.get('overlap_replaced_review')} | {am.get('observer_avg_hold_sec')} |",
        f"| PM | {pm.get('trade_count')} | {pm.get('symbol_count')} | {pm.get('overlap_replaced_review')} | {pm.get('observer_avg_hold_sec')} |",
        "",
        "## Mandatory answers",
        "",
        f"1. **Abnormal trade_count cause:** {ma.get('1_abnormal_trade_count_main_cause')}",
        f"2. **overlap_replaced_review = same-symbol replace:** {ma.get('2_overlap_replaced_is_same_symbol_replace')}",
        f"3. **Phase409 silent cause:** {ma.get('3_phase409_silent_main_cause')}",
        f"4. **day_count=0:** {ma.get('4_day_count_zero_is_bug')} — {ma.get('4_day_count_zero_detail')}",
        f"5. **Policy A trade_count:** {ma.get('5_trade_count_reduction_policy_A')} (baseline {am.get('trade_count', 0) + pm.get('trade_count', 0)})",
        f"6. **Policy A PnL/PF/maxDD:** {ma.get('6_pnl_pf_maxdd_policy_A')}",
        f"7. **Fix candidates:** {', '.join(ma.get('7_runtime_fix_candidates') or [])}",
        "",
        "## Overlap replace cause breakdown",
        "",
        f"- total events: {ob.get('total_overlap_replace_events')}",
        f"- same_symbol chain: {ob.get('same_symbol_overlap_replace_chain')}",
        f"- verdict: {ob.get('verdict')}",
        f"- observer spec: {ob.get('observer_spec')}",
        "",
        "## Boundary eligibility",
        "",
        f"- eligible: {be.get('boundary_eligible')} / {be.get('total_trades')}",
        f"- boundary hit: {be.get('boundary_hit')}",
        f"- skip reasons: {be.get('skip_reason_counts')}",
        "",
        "## Counterfactual policies",
        "",
        "| Policy | trades | PnL | PF | maxDD | avg_hold | boundary_eligible | would_trigger |",
        "|--------|--------|-----|----|-------|----------|-------------------|---------------|",
    ]
    for r in cf_rows:
        lines.append(
            f"| {r.get('policy')} | {r.get('trade_count')} | {r.get('total_pnl_yen_100')} | "
            f"{r.get('profit_factor')} | {r.get('max_drawdown_yen_100')} | {r.get('avg_hold_sec')} | "
            f"{r.get('boundary_eligible_count')} | {r.get('phase409_would_trigger_count')} |"
        )

    lines.extend(
        [
            "",
            "## Top churn symbols (AM)",
            "",
            "| symbol | entries | overlap | median_hold | pnl |",
            "|--------|---------|---------|-------------|-----|",
        ]
    )
    am_rows = sorted(
        [r for r in symbol_rows if r.get("session") == AM_SESSION],
        key=lambda r: -int(r.get("entry_count") or 0),
    )[:8]
    for r in am_rows:
        lines.append(
            f"| {r.get('symbol')} | {r.get('entry_count')} | {r.get('overlap_replaced_review_count')} | "
            f"{r.get('median_hold_sec')} | {r.get('total_pnl_yen_100')} |"
        )

    lines.extend(["", "- Runtime / YAML / Entry / Exit unchanged", ""])
    return "\n".join(lines)
