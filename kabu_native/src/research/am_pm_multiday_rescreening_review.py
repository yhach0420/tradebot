"""
Phase 146: Multi-day AM/PM rescreening review (Core10+Dynamic40, review only).
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.remaining_issues_review import (
    _day_stamp,
    _events_by_symbol,
    _pnl_by_symbol,
    _post_pm_pnl_proxy,
    _symbol_sets_from_universe,
    find_live_session,
)
from universe.am_pm_universe import _norm, compare_am_pm
from universe.core10_dynamic40 import (
    build_am_universe,
    build_pm_universe,
    universe_am_path,
    universe_pm_path,
    write_universe_csv,
)
from universe.core_watchlist import load_core_watchlist
from universe.daily_features import features_csv_path, generate_features_csv, load_features_csv
from universe.dynamic_build import load_dynamic_config, resolve_symbol_master

TARGET_TRADE_DATES = (
    "2026-05-19",
    "2026-05-20",
    "2026-05-21",
    "2026-05-22",
)

PNL_WIN_EPS = 0.05
ACCEPTED_WIN_RATIO = 1.25


def _load_master(repo_root: Path) -> tuple[list[str], dict[str, dict[str, Any]], list[str]]:
    cfg = load_dynamic_config(repo_root / "kabu_native" / "configs" / "universe_dynamic_trial.yaml")
    _, master_entries = resolve_symbol_master(repo_root, cfg.symbol_master_paths)
    symbol_meta: dict[str, dict[str, Any]] = {}
    master_symbols: list[str] = []
    for e in master_entries:
        sym = f"{e.parsed.code}.T"
        master_symbols.append(sym)
        symbol_meta[sym] = {
            "exchange": e.parsed.exchange,
            "symbol_key": e.parsed.symbol_key,
            "market": e.market,
        }
    core_symbols, _ = load_core_watchlist(repo_root)
    return master_symbols, symbol_meta, core_symbols


def generate_features_for_days(
    trade_dates: Sequence[str],
    *,
    repo_root: Path,
    reports_dir: Path,
    master_symbols: Sequence[str],
    symbol_meta: Mapping[str, Mapping[str, Any]],
    force: bool = False,
) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    for trade_date in trade_dates:
        stamp = _day_stamp(trade_date)
        path = features_csv_path(reports_dir, stamp)
        if path.is_file() and not force:
            rows = load_features_csv(path)
            logs.append(
                {
                    "trade_date": trade_date,
                    "status": "cached",
                    "path": str(path),
                    "row_count": len(rows),
                    "valid_vol_liq": sum(
                        1 for r in rows if str(r.get("volatility_liquidity_score") or "").strip()
                    ),
                }
            )
            continue
        td = date.fromisoformat(trade_date)
        summary = generate_features_csv(
            symbols=master_symbols,
            trade_date=td,
            symbol_meta=symbol_meta,
            out_path=path,
        )
        logs.append(
            {
                "trade_date": trade_date,
                "status": "generated",
                "path": str(path),
                **summary,
            }
        )
    return logs


def rebuild_universes_for_days(
    trade_dates: Sequence[str],
    *,
    repo_root: Path,
    reports_dir: Path,
    push_root: Path,
    core_symbols: Sequence[str],
    symbol_meta: Mapping[str, Mapping[str, Any]],
    force: bool = False,
) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    for trade_date in trade_dates:
        stamp = _day_stamp(trade_date)
        feat_path = features_csv_path(reports_dir, stamp)
        features = load_features_csv(feat_path) if feat_path.is_file() else []
        am_csv = universe_am_path(reports_dir, stamp)
        pm_csv = universe_pm_path(reports_dir, stamp)
        push_dir = push_root / trade_date

        if not features:
            logs.append({"trade_date": trade_date, "status": "skipped_no_features"})
            continue

        if not force and am_csv.is_file() and pm_csv.is_file():
            logs.append(
                {
                    "trade_date": trade_date,
                    "status": "cached",
                    "am_csv": str(am_csv),
                    "pm_csv": str(pm_csv),
                }
            )
            continue

        am_rows = build_am_universe(
            core_symbols=core_symbols, feature_rows=features, symbol_meta=symbol_meta
        )
        pm_rows = build_pm_universe(
            core_symbols=core_symbols,
            feature_rows=features,
            symbol_meta=symbol_meta,
            push_day_dir=push_dir,
        )
        write_universe_csv(am_csv, am_rows)
        write_universe_csv(pm_csv, pm_rows)
        logs.append(
            {
                "trade_date": trade_date,
                "status": "rebuilt",
                "am_count": len(am_rows),
                "pm_count": len(pm_rows),
                "am_csv": str(am_csv),
                "pm_csv": str(pm_csv),
            }
        )
    return logs


def _load_univ_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _symbol_perf_rows(
    *,
    trade_date: str,
    symbols: Sequence[str],
    bucket: str,
    events: Mapping[str, Mapping[str, int]],
    pnl_map: Mapping[str, float],
    post_pm: Mapping[str, Optional[float]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sym in symbols:
        ev = events.get(sym, {})
        rows.append(
            {
                "trade_date": trade_date,
                "symbol": sym,
                "bucket": bucket,
                "candidate_count": ev.get("candidate", 0),
                "accepted_count": ev.get("accepted", 0),
                "structural_pnl_proxy": round(pnl_map[sym], 4) if sym in pnl_map else None,
                "post_pm_pnl_proxy": post_pm.get(sym),
            }
        )
    return rows


def analyze_multiday_am_pm(
    trade_dates: Sequence[str],
    *,
    repo_root: Path,
    reports_dir: Path,
    small_paper_root: Path,
    push_root: Path,
) -> dict[str, Any]:
    master_symbols, symbol_meta, core_symbols = _load_master(repo_root)

    daily_rows: list[dict[str, Any]] = []
    pm_added_perf: list[dict[str, Any]] = []
    am_removed_perf: list[dict[str, Any]] = []
    per_day_detail: dict[str, Any] = {}

    for trade_date in trade_dates:
        stamp = _day_stamp(trade_date)
        feat_path = features_csv_path(reports_dir, stamp)
        features = load_features_csv(feat_path) if feat_path.is_file() else []
        push_dir = push_root / trade_date
        session_dir = find_live_session(small_paper_root, stamp)

        am_rows = _load_univ_csv(universe_am_path(reports_dir, stamp))
        pm_rows = _load_univ_csv(universe_pm_path(reports_dir, stamp))
        am_set = _symbol_sets_from_universe(am_rows)
        pm_set = _symbol_sets_from_universe(pm_rows)
        cmp = compare_am_pm(am_rows, pm_rows) if am_rows and pm_rows else {}

        added = list(cmp.get("added_symbols") or [])
        removed = list(cmp.get("removed_symbols") or [])
        stayed = sorted(am_set & pm_set)

        events: dict[str, dict[str, int]] = {}
        pnl_map: dict[str, float] = {}
        if session_dir:
            events = _events_by_symbol(session_dir)
            pnl_map = _pnl_by_symbol(session_dir)

        post_pm_added = _post_pm_pnl_proxy(added, push_day_dir=push_dir, trade_date=trade_date)
        post_pm_removed = _post_pm_pnl_proxy(removed, push_day_dir=push_dir, trade_date=trade_date)

        pm_added_perf.extend(
            _symbol_perf_rows(
                trade_date=trade_date,
                symbols=added,
                bucket="pm_added",
                events=events,
                pnl_map=pnl_map,
                post_pm=post_pm_added,
            )
        )
        am_removed_perf.extend(
            _symbol_perf_rows(
                trade_date=trade_date,
                symbols=removed,
                bucket="am_removed",
                events=events,
                pnl_map=pnl_map,
                post_pm=post_pm_removed,
            )
        )

        def _bucket(symbols: Sequence[str], post: Mapping[str, Optional[float]], label: str) -> dict[str, Any]:
            cand = acc = 0
            pnls: list[float] = []
            post_pnls: list[float] = []
            syms_with_acc = 0
            for sym in symbols:
                ev = events.get(sym, {})
                cand += ev.get("candidate", 0)
                acc += ev.get("accepted", 0)
                if ev.get("accepted", 0) > 0:
                    syms_with_acc += 1
                if sym in pnl_map:
                    pnls.append(pnl_map[sym])
                if post.get(sym) is not None:
                    post_pnls.append(float(post[sym]))
            return {
                f"{label}_symbol_count": len(symbols),
                f"{label}_symbols_with_accepted": syms_with_acc,
                f"{label}_candidate_count": cand,
                f"{label}_accepted_count": acc,
                f"{label}_structural_pnl_sum": round(sum(pnls), 4) if pnls else None,
                f"{label}_structural_pnl_avg": round(sum(pnls) / len(pnls), 4) if pnls else None,
                f"{label}_post_pm_pnl_avg": round(sum(post_pnls) / len(post_pnls), 4) if post_pnls else None,
                f"{label}_post_pm_n": len(post_pnls),
            }

        row = {
            "trade_date": trade_date,
            "features_available": bool(features),
            "features_row_count": len(features),
            "am_universe_count": len(am_set),
            "pm_universe_count": len(pm_set),
            "overlap_count": int(cmp.get("overlap_count") or 0),
            "overlap_rate": round(int(cmp.get("overlap_count") or 0) / max(len(am_set), 1), 4),
            "pm_added_count": len(added),
            "am_removed_count": len(removed),
            "churn_rate": cmp.get("churn_rate"),
            "push_dir_exists": push_dir.is_dir(),
            "session_found": bool(session_dir),
            **_bucket(stayed, {}, "stayed"),
            **_bucket(added, post_pm_added, "pm_added"),
            **_bucket(removed, post_pm_removed, "am_removed"),
        }

        pa = row.get("pm_added_structural_pnl_avg")
        sa = row.get("stayed_structural_pnl_avg")
        row["pm_added_beats_stayed_pnl"] = (
            pa is not None and sa is not None and float(pa) > float(sa) + PNL_WIN_EPS
        )
        pac = int(row.get("pm_added_accepted_count") or 0)
        sac = int(row.get("stayed_accepted_count") or 0)
        row["pm_added_beats_stayed_accepted"] = pac > sac * ACCEPTED_WIN_RATIO if sac > 0 else pac > 0
        row["may21_reference_day"] = trade_date == "2026-05-21"

        daily_rows.append(row)
        per_day_detail[trade_date] = {
            "comparison": cmp,
            "added_symbols": added,
            "removed_symbols": removed,
            "stayed_symbols": stayed,
        }

    verdict, verdict_notes, aggregate = determine_multiday_verdict(daily_rows, pm_added_perf, am_removed_perf)

    return {
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "aggregate": aggregate,
        "daily_rows": daily_rows,
        "pm_added_symbol_performance": pm_added_perf,
        "am_removed_symbol_performance": am_removed_perf,
        "per_day_detail": per_day_detail,
        "core_count": len(core_symbols),
        "master_symbol_count": len(master_symbols),
    }


def determine_multiday_verdict(
    daily_rows: Sequence[Mapping[str, Any]],
    pm_added_perf: Sequence[Mapping[str, Any]],
    am_removed_perf: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str], dict[str, Any]]:
    notes: list[str] = []
    valid = [r for r in daily_rows if r.get("features_available") and int(r.get("am_universe_count") or 0) >= 50]

    if len(valid) < len(daily_rows):
        notes.append(f"features/universe complete on {len(valid)}/{len(daily_rows)} days")

    if len(valid) < 2:
        return "need_more_intraday_data", notes + ["insufficient multi-day features/universe"], {}

    pnl_wins = sum(1 for r in valid if r.get("pm_added_beats_stayed_pnl"))
    acc_wins = sum(1 for r in valid if r.get("pm_added_beats_stayed_accepted"))

    pm_pnl_avgs = [float(r["pm_added_structural_pnl_avg"]) for r in valid if r.get("pm_added_structural_pnl_avg") is not None]
    st_pnl_avgs = [float(r["stayed_structural_pnl_avg"]) for r in valid if r.get("stayed_structural_pnl_avg") is not None]
    agg_pm_pnl = sum(pm_pnl_avgs) / len(pm_pnl_avgs) if pm_pnl_avgs else None
    agg_st_pnl = sum(st_pnl_avgs) / len(st_pnl_avgs) if st_pnl_avgs else None

    pm_acc = sum(int(r.get("pm_added_accepted_count") or 0) for r in valid)
    st_acc = sum(int(r.get("stayed_accepted_count") or 0) for r in valid)
    pm_cand = sum(int(r.get("pm_added_candidate_count") or 0) for r in valid)
    st_cand = sum(int(r.get("stayed_candidate_count") or 0) for r in valid)

    removed_post = [
        float(r["post_pm_pnl_proxy"])
        for r in am_removed_perf
        if r.get("post_pm_pnl_proxy") is not None
    ]
    added_post = [
        float(r["post_pm_pnl_proxy"])
        for r in pm_added_perf
        if r.get("post_pm_pnl_proxy") is not None
    ]

    may21 = next((r for r in valid if r.get("trade_date") == "2026-05-21"), None)
    may21_reproduced = bool(may21 and may21.get("pm_added_beats_stayed_pnl"))

    avg_overlap = sum(float(r["overlap_rate"] or 0) for r in valid) / len(valid)
    avg_churn = sum(float(r["churn_rate"] or 0) for r in valid) / len(valid)

    aggregate = {
        "days_analyzed": len(valid),
        "avg_overlap_rate": round(avg_overlap, 4),
        "avg_churn_rate": round(avg_churn, 4),
        "days_pm_added_beats_stayed_pnl": pnl_wins,
        "days_pm_added_beats_stayed_accepted": acc_wins,
        "aggregate_pm_added_structural_pnl_avg": round(agg_pm_pnl, 4) if agg_pm_pnl is not None else None,
        "aggregate_stayed_structural_pnl_avg": round(agg_st_pnl, 4) if agg_st_pnl is not None else None,
        "aggregate_pm_added_accepted": pm_acc,
        "aggregate_stayed_accepted": st_acc,
        "aggregate_pm_added_candidate": pm_cand,
        "aggregate_stayed_candidate": st_cand,
        "pm_added_post_pm_pnl_avg": round(sum(added_post) / len(added_post), 4) if added_post else None,
        "am_removed_post_pm_pnl_avg": round(sum(removed_post) / len(removed_post), 4) if removed_post else None,
        "pm_added_post_pm_coverage": len(added_post),
        "am_removed_post_pm_coverage": len(removed_post),
        "may21_signal_reproduced": may21_reproduced,
    }

    notes.append(
        f"days={len(valid)} pnl_wins={pnl_wins}/{len(valid)} acc_wins={acc_wins}/{len(valid)} "
        f"avg_overlap={avg_overlap:.1%} avg_churn={avg_churn:.1%}"
    )
    if agg_pm_pnl is not None and agg_st_pnl is not None:
        notes.append(
            f"aggregate structural pnl avg pm_added={agg_pm_pnl:.3f}% stayed={agg_st_pnl:.3f}%"
        )
    notes.append(f"may21_signal_reproduced={may21_reproduced}")

    push_thin = len(added_post) < len(pm_added_perf) * 0.15 and len(valid) >= 2
    if push_thin:
        notes.append("post_pm push coverage thin on PM-added symbols")
        return "need_more_intraday_data", notes, aggregate

    if pnl_wins >= len(valid) * 0.75 and agg_pm_pnl is not None and agg_st_pnl is not None:
        if agg_pm_pnl > agg_st_pnl + PNL_WIN_EPS:
            notes.append("PM-added stronger on most days and in aggregate")
            return "am_pm_rescreening_worthwhile", notes, aggregate

    if pnl_wins >= 2 and acc_wins >= 2 and may21_reproduced:
        if agg_pm_pnl is not None and agg_st_pnl is not None and agg_pm_pnl > agg_st_pnl:
            notes.append("multi-day PM-added lift with May21 signal reproduced")
            return "am_pm_rescreening_worthwhile", notes, aggregate

    if pnl_wins == 0 and acc_wins <= 1:
        notes.append("PM-added does not beat stayed on any day")
        return "am_pm_rescreening_not_needed", notes, aggregate

    if avg_overlap >= 0.85 and avg_churn < 0.12:
        notes.append("high overlap low churn — PM rescreen adds little")
        return "am_pm_rescreening_not_needed", notes, aggregate

    if 0 < pnl_wins < len(valid) and may21_reproduced != (pnl_wins >= len(valid) // 2 + 1):
        return "mixed_result", notes + ["May21 pattern not consistent across all days"], aggregate

    if pnl_wins >= 1 and pnl_wins < len(valid):
        return "mixed_result", notes, aggregate

    notes.append("PM rescreen churn present but performance lift unclear")
    return "am_pm_rescreening_not_needed", notes, aggregate


def run_phase146_review(
    *,
    repo_root: Path,
    reports_dir: Path,
    small_paper_root: Path,
    push_root: Path,
    trade_dates: Optional[Sequence[str]] = None,
    force_features: bool = False,
    force_universe: bool = False,
) -> dict[str, Any]:
    dates = list(trade_dates or TARGET_TRADE_DATES)
    master_symbols, symbol_meta, core_symbols = _load_master(repo_root)

    feature_logs = generate_features_for_days(
        dates,
        repo_root=repo_root,
        reports_dir=reports_dir,
        master_symbols=master_symbols,
        symbol_meta=symbol_meta,
        force=force_features,
    )
    universe_logs = rebuild_universes_for_days(
        dates,
        repo_root=repo_root,
        reports_dir=reports_dir,
        push_root=push_root,
        core_symbols=core_symbols,
        symbol_meta=symbol_meta,
        force=force_universe or force_features,
    )
    analysis = analyze_multiday_am_pm(
        dates,
        repo_root=repo_root,
        reports_dir=reports_dir,
        small_paper_root=small_paper_root,
        push_root=push_root,
    )
    return {
        "trade_dates": dates,
        "feature_generation": feature_logs,
        "universe_rebuild": universe_logs,
        **analysis,
    }
