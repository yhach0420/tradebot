"""
Phase498 — Chase Quality Audit (research only).

Decomposes chase entries (high r10) across all PBv2 accepted trades; counterfactual guards.
No Runtime / YAML / Entry / Exit / Order / Discord changes.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase443_full_runtime_combined_capital_sim import CapacityReplayState
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase463_trend_pullback_population_tournament import _fill_close_proxy_shadows
from research.phase464_pre_gate_archetype_audit import _vwap_above_ratio
from research.phase465b_trend_gate_redesign import _cohens_d, _high_update_age, _mi_median_split
from research.phase473_trend_entry_architecture import _entry_block, _rise, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _ensure_enriched, _load_replay_pool
from research.phase483_stop_low_mfe_root_cause_audit import _ks_stat
from research.phase484_stop_low_mfe_feature_discovery import (
    _board_features,
    _compute_base_features,
    _load_day_event_snaps,
)
from research.phase488_current_runtime_replay import (
    REPLAY_MODE,
    _filter_period,
    _filter_replay_pool_safe,
    _simulate_runtime_replay,
    _summary_metrics,
)
from research.phase493_global_entry_failure_audit import (
    DAY_622,
    PERIOD_END,
    PERIOD_START,
    _enrich_trade_row,
    _exit_reason,
    _replay_with_extra_block,
    _top_pct_threshold,
)
from research.phase494_new_feature_discovery import _compute_new_features
from research.phase495_new_feature_guard_replay import _counterfactual_row, _rows_from_state, _session_bucket
from research.phase496_mst_near_high_optimization import _distance_from_day_high
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

R10_FLOOR = 1.0
R10_TOP_PCT = 70.0  # top 30% => >= 70th percentile

BASE_FEATURES = (
    "r5", "r10", "r15", "r30", "r5_over_r10", "r15_minus_r5", "r30_minus_r5",
    "vwap_dev_pct", "vwap_structure_score", "vwap_above_duration",
    "day_high_distance", "MST_near_day_high_flag", "high_update_age",
    "high_update_count_30m", "time_since_last_high",
    "board_imbalance", "board_change_5m", "board_change_10m",
    "momentum_continuation_score",
    "RSY_r5_minus_symbol_median", "RSY_r10_zscore_in_day", "RSY_composite_strength_pct",
    "PBQ_board_supported_dip", "PBQ_negative_r5_board_midhigh",
    "EXH_chase_intensity", "EXH_inverse_day_high_dist", "EXH_rally_decay_r15_r5",
)

CHASE_QUALITY_FEATURES = (
    "chase_followthrough_score",
    "chase_decay_score",
    "chase_near_high_exhaustion",
    "chase_board_confirmation",
    "chase_vwap_confirmation",
    "chase_failure_score",
    "chase_pullback_quality",
)

ALL_FEATURES = BASE_FEATURES + CHASE_QUALITY_FEATURES

AUDIT_FIELDS = [
    "position_key", "symbol", "day", "cohort", "is_chase", "exit_reason",
    "pnl_yen_100", "mfe_pct", "session_bucket", *ALL_FEATURES,
]

RANKING_FIELDS = [
    "rank", "feature_id", "feature_type", "is_new",
    "w_chase_mean", "w_chase_median", "l_chase_mean", "l_chase_median",
    "missing_rate_w", "missing_rate_l",
    "cohens_d", "ks_statistic", "mutual_information", "feature_direction",
    "loo_min_abs_d", "loo_stable_days_pct", "loo_robust",
    "exclude_6976_abs_d", "exclude_top_day_abs_d",
]

COUNTERFACTUAL_FIELDS = [
    "scenario", "total_pnl_yen_100", "profit_factor", "maxDD_yen_100", "delta_maxDD_yen_100",
    "delta_pnl_yen_100", "baseline_pnl_yen_100", "baseline_PF",
    "accepted", "blocked_total", "blocked_winners", "blocked_losers", "blocked_pnl_yen_100",
    "impact_6976", "impact_6981", "impact_4062", "impact_6522", "impact_20260622",
    "impact_AM", "impact_PM",
]

ROBUSTNESS_FIELDS = [
    "test", "scenario", "total_pnl_yen_100", "profit_factor",
    "delta_pnl_vs_baseline", "blocked_winners", "trade_count",
]


def _float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_ratio(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or abs(b) < 1e-9:
        return None
    return round(a / b, 6)


def _feature_direction(wm: Optional[float], lm: Optional[float]) -> str:
    if wm is None or lm is None:
        return "unknown"
    if lm > wm:
        return "higher_in_loser"
    if lm < wm:
        return "lower_in_loser"
    return "equal"


def _is_chase(row: Mapping[str, Any], *, r10_thr_top30: float) -> bool:
    r10 = _float(row.get("r10"))
    if r10 is None:
        return False
    return r10 >= R10_FLOOR or r10 >= r10_thr_top30


def _is_l_chase(row: Mapping[str, Any], *, r10_thr_top30: float) -> bool:
    if not _is_chase(row, r10_thr_top30=r10_thr_top30):
        return False
    pnl = float(row.get("pnl_yen") or row.get("pnl_yen_100") or 0)
    reason = _exit_reason(row)
    return reason in ("stop_hit", "no_progress_exit") or pnl < 0


def _is_w_chase(row: Mapping[str, Any], *, r10_thr_top30: float) -> bool:
    if not _is_chase(row, r10_thr_top30=r10_thr_top30):
        return False
    if _is_l_chase(row, r10_thr_top30=r10_thr_top30):
        return False
    pnl = float(row.get("pnl_yen") or row.get("pnl_yen_100") or 0)
    reason = _exit_reason(row)
    return reason == "trailing_mfe_exit" or pnl > 0


def _assign_cohort(row: Mapping[str, Any], *, r10_thr_top30: float) -> str:
    if not _is_chase(row, r10_thr_top30=r10_thr_top30):
        return "non_chase"
    if _is_l_chase(row, r10_thr_top30=r10_thr_top30):
        return "L_chase"
    if _is_w_chase(row, r10_thr_top30=r10_thr_top30):
        return "W_chase"
    return "chase_other"


def _build_context(replay_pool: Sequence[Mapping[str, Any]]) -> tuple[dict[str, float], dict[str, tuple[float, float]], dict[str, float]]:
    sym_r5: dict[str, list[float]] = defaultdict(list)
    day_r10: dict[str, list[float]] = defaultdict(list)
    for trade in replay_pool:
        if not pass_pbv2(trade):
            continue
        day = str(trade.get("day") or "")[:8]
        row = _enrich_trade_row({"trade": trade, "day": day, "pnl_yen": 0, "exit_reason": ""})
        v5, v10 = _float(row.get("r5")), _float(row.get("r10"))
        if v5 is not None:
            sym_r5[str(row["symbol"])].append(v5)
        if v10 is not None:
            day_r10[str(row["day"])].append(v10)
    sym_median = {s: statistics.median(v) for s, v in sym_r5.items() if v}
    day_stats = {d: (statistics.mean(v), statistics.pstdev(v) or 1e-9) for d, v in day_r10.items() if len(v) >= 2}
    composite_raw: dict[str, float] = {}
    for trade in replay_pool:
        if not pass_pbv2(trade):
            continue
        day = str(trade.get("day") or "")[:8]
        row = _enrich_trade_row({"trade": trade, "day": day, "pnl_yen": 0, "exit_reason": ""})
        v10, vd = _float(row.get("r10")), _float(row.get("vwap_dev_pct"))
        if v10 is not None and vd is not None:
            composite_raw[_position_key(trade)] = v10 + vd
    composite_pct: dict[str, float] = {}
    if composite_raw:
        vals = sorted(composite_raw.values())
        for pk, val in composite_raw.items():
            composite_pct[pk] = round(100.0 * sum(1 for x in vals if x <= val) / len(vals), 4)
    return sym_median, day_stats, composite_pct


def _enrich_row(
    log: Mapping[str, Any],
    *,
    sym_median: Mapping[str, float],
    day_stats: Mapping[str, tuple[float, float]],
    composite_pct: Mapping[str, float],
    day_snaps: Mapping[str, Mapping[str, list]],
    r10_thr_top30: float,
) -> dict[str, Any]:
    base = _enrich_trade_row(log)
    tr = dict(base.get("_trade") or {})
    day = str(base["day"])[:8]
    base_feats = _compute_base_features(tr)
    board_feats = _board_features(tr, day_snaps.get(day, {}))
    r5, r10, r15, r30 = _rise(tr, 5), _rise(tr, 10), _rise(tr, 15), _rise(tr, 30)
    dhd = _distance_from_day_high(tr)
    age = _high_update_age(tr)
    hu30 = _float(tr.get("high_update_count_30m"))
    vwap_above = _vwap_above_ratio(tr)
    cat = _float(tr.get("consecutive_above_ticks"))
    vwap_struct = _float(tr.get("vwap_structure_score"))
    board = _float(base.get("board_imbalance"))
    bc5 = board_feats.get("D1_board_change_5m")
    bc10 = board_feats.get("D2_board_change_10m")
    mom = _float(tr.get("momentum_continuation_score"))
    vwap_dev = base_feats.get("B1_vwap_dev_pct")

    vwap_dur = round((cat or 0) * (vwap_above or 0), 6) if cat is not None and vwap_above is not None else None
    near_high_score = round(1.0 / max(dhd or 0.05, 0.05), 6) if dhd is not None else None
    chase_intensity = round(r10 / max(dhd or 0.2, 0.2), 6) if r10 is not None and dhd is not None else None

    ext = _compute_new_features(
        {**base, "r5": r5, "r10": r10, "r15": r15, "r30": r30, "vwap_dev_pct": vwap_dev,
         "day_high_distance": dhd, "high_update_age": age, "high_update_count": hu30,
         "board_imbalance": board, "momentum_continuation_score": mom,
         "board_change_5m": bc5, "board_change_10m": bc10, "vwap_extension_rate": base_feats.get("B2_vwap_extension_rate")},
        symbol_r5_median=sym_median,
        day_r10_stats=day_stats,
        composite_pct={str(base["position_key"]): composite_pct.get(_position_key(tr), 0)},
    )

    chase_decay = round(r10 - r5, 6) if r10 is not None and r5 is not None else None
    chase_follow = _safe_ratio(r5, r10)
    pbq_dip = ext.get("PBQ_board_supported_dip")

    rec: dict[str, Any] = {
        "position_key": base["position_key"],
        "symbol": base["symbol"],
        "day": base["day"],
        "exit_reason": base.get("exit_reason"),
        "pnl_yen_100": base.get("pnl_yen"),
        "mfe_pct": base.get("mfe_pct"),
        "session_bucket": base.get("session_bucket") or _session_bucket(tr.get("entry_time")),
        "r5": r5, "r10": r10, "r15": r15, "r30": r30,
        "r5_over_r10": chase_follow,
        "r15_minus_r5": base_feats.get("A2_r15_minus_r5"),
        "r30_minus_r5": base_feats.get("A1_r30_minus_r5"),
        "vwap_dev_pct": vwap_dev,
        "vwap_structure_score": vwap_struct,
        "vwap_above_duration": vwap_dur,
        "day_high_distance": dhd,
        "MST_near_day_high_flag": 1.0 if dhd is not None and dhd <= 1.0 else 0.0 if dhd is not None else None,
        "high_update_age": age,
        "high_update_count_30m": hu30,
        "time_since_last_high": age,
        "board_imbalance": board,
        "board_change_5m": bc5,
        "board_change_10m": bc10,
        "momentum_continuation_score": mom,
        "RSY_r5_minus_symbol_median": ext.get("RSY_r5_minus_symbol_median"),
        "RSY_r10_zscore_in_day": ext.get("RSY_r10_zscore_in_day"),
        "RSY_composite_strength_pct": ext.get("RSY_composite_strength_pct"),
        "PBQ_board_supported_dip": pbq_dip,
        "PBQ_negative_r5_board_midhigh": ext.get("PBQ_negative_r5_board_midhigh"),
        "EXH_chase_intensity": chase_intensity or ext.get("EXH_chase_intensity"),
        "EXH_inverse_day_high_dist": ext.get("EXH_inverse_day_high_dist"),
        "EXH_rally_decay_r15_r5": ext.get("EXH_rally_decay_r15_r5"),
        "chase_followthrough_score": chase_follow,
        "chase_decay_score": chase_decay,
        "chase_near_high_exhaustion": (
            round(chase_intensity * near_high_score, 6)
            if chase_intensity is not None and near_high_score is not None
            else None
        ),
        "chase_board_confirmation": (
            round((bc10 or 0) + (board or 0), 6) if bc10 is not None and board is not None else None
        ),
        "chase_vwap_confirmation": (
            round((vwap_struct or 0) + (vwap_dur or 0), 6)
            if vwap_struct is not None and vwap_dur is not None
            else None
        ),
        "chase_failure_score": (
            round((age or 0) + (chase_decay or 0), 6) if age is not None and chase_decay is not None else None
        ),
        "chase_pullback_quality": (
            round((pbq_dip or 0) - (chase_intensity or 0), 6)
            if pbq_dip is not None and chase_intensity is not None
            else None
        ),
        "_trade": tr,
    }
    rec["cohort"] = _assign_cohort(rec, r10_thr_top30=r10_thr_top30)
    rec["is_chase"] = rec["cohort"] in ("W_chase", "L_chase", "chase_other")
    return rec


def _rank_features(rows: Sequence[Mapping[str, Any]], *, days: Sequence[str]) -> list[dict[str, Any]]:
    w_rows = [r for r in rows if r.get("cohort") == "W_chase"]
    l_rows = [r for r in rows if r.get("cohort") == "L_chase"]
    day_pnl = defaultdict(float)
    for r in rows:
        day_pnl[str(r["day"])] += float(r.get("pnl_yen_100") or 0)
    top_day = max(day_pnl, key=lambda d: abs(day_pnl[d])) if day_pnl else ""
    ranking: list[dict[str, Any]] = []

    for feat in ALL_FEATURES:
        wv = [float(r[feat]) for r in w_rows if r.get(feat) is not None]
        lv = [float(r[feat]) for r in l_rows if r.get(feat) is not None]
        if not wv and not lv:
            continue
        wm = statistics.mean(wv) if wv else None
        lm = statistics.mean(lv) if lv else None
        d = _cohens_d(lv, wv)
        ks = _ks_stat(lv, wv)
        mi = _mi_median_split(wv, lv) if wv and lv else None

        loo_ds: list[float] = []
        stable = 0
        for day in days:
            sw = [float(r[feat]) for r in w_rows if r.get("day") != day and r.get(feat) is not None]
            sl = [float(r[feat]) for r in l_rows if r.get("day") != day and r.get(feat) is not None]
            if len(sw) < 2 or len(sl) < 2:
                continue
            ld = abs(float(_cohens_d(sl, sw) or 0))
            loo_ds.append(ld)
            if ld >= 0.12:
                stable += 1
        n_loo = len(loo_ds) or 1

        ex_w = [r for r in w_rows if str(r.get("symbol")) != "6976"]
        ex_l = [r for r in l_rows if str(r.get("symbol")) != "6976"]
        ex6976_d = abs(
            float(
                _cohens_d(
                    [float(r[feat]) for r in ex_l if r.get(feat) is not None],
                    [float(r[feat]) for r in ex_w if r.get(feat) is not None],
                )
                or 0
            )
        )
        ex_dw = [r for r in w_rows if str(r.get("day")) != top_day]
        ex_dl = [r for r in l_rows if str(r.get("day")) != top_day]
        ex_day_d = abs(
            float(
                _cohens_d(
                    [float(r[feat]) for r in ex_dl if r.get(feat) is not None],
                    [float(r[feat]) for r in ex_dw if r.get(feat) is not None],
                )
                or 0
            )
        )

        ranking.append(
            {
                "feature_id": feat,
                "feature_type": "chase_quality" if feat in CHASE_QUALITY_FEATURES else "base",
                "is_new": feat in CHASE_QUALITY_FEATURES,
                "w_chase_mean": round(wm, 6) if wm is not None else None,
                "w_chase_median": round(statistics.median(wv), 6) if wv else None,
                "l_chase_mean": round(lm, 6) if lm is not None else None,
                "l_chase_median": round(statistics.median(lv), 6) if lv else None,
                "missing_rate_w": round(sum(1 for r in w_rows if r.get(feat) is None) / max(1, len(w_rows)), 4),
                "missing_rate_l": round(sum(1 for r in l_rows if r.get(feat) is None) / max(1, len(l_rows)), 4),
                "cohens_d": d,
                "ks_statistic": ks,
                "mutual_information": mi,
                "feature_direction": _feature_direction(wm, lm),
                "loo_min_abs_d": round(min(loo_ds), 6) if loo_ds else 0.0,
                "loo_stable_days_pct": round(stable / n_loo, 4),
                "loo_robust": (min(loo_ds) if loo_ds else 0) >= 0.12 and abs(float(d or 0)) >= 0.20,
                "exclude_6976_abs_d": round(ex6976_d, 6),
                "exclude_top_day_abs_d": round(ex_day_d, 6),
            }
        )

    ranking.sort(key=lambda r: abs(float(r.get("cohens_d") or 0)), reverse=True)
    for i, row in enumerate(ranking, start=1):
        row["rank"] = i
    return ranking


def _pattern_medians(rows: Sequence[Mapping[str, Any]], *, cohort: str, feats: Sequence[str]) -> dict[str, Any]:
    grp = [r for r in rows if r.get("cohort") == cohort]
    if not grp:
        return {"cohort": cohort, "count": 0}
    out: dict[str, Any] = {
        "cohort": cohort,
        "count": len(grp),
        "total_pnl": round(sum(float(r.get("pnl_yen_100") or 0) for r in grp), 2),
        "profit_factor": _pf([float(r.get("pnl_yen_100") or 0) for r in grp]),
    }
    for f in feats[:6]:
        vals = [_float(r.get(f)) for r in grp]
        vals_n = [v for v in vals if v is not None]
        out[f"median_{f}"] = round(statistics.median(vals_n), 4) if vals_n else None
    return out


def _verdict(
    *,
    best_cf: Mapping[str, Any],
    best_new_d: float,
    best_existing_d: float,
    overfit_flags: bool,
) -> str:
    delta = float(best_cf.get("delta_pnl_yen_100") or 0)
    bw = int(best_cf.get("blocked_winners") or 0)
    if overfit_flags and delta < 20000:
        return "overfit_chase_feature"
    if best_new_d > best_existing_d + 0.05:
        return "new_chase_feature_found"
    if delta >= 8000 and float(best_cf.get("profit_factor") or 0) > float(best_cf.get("baseline_PF") or 0):
        return "chase_quality_guard_candidate"
    if delta >= 3000:
        return "chase_quality_guard_candidate" if bw <= 12 else "new_chase_feature_found"
    return "no_chase_edge"


def run_phase498(*, repo_root: Path, parallel: bool = False, max_workers: int = 2) -> dict[str, Any]:
    max_workers = min(max(2, max_workers), 4)
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    replay_pool = _filter_period(replay_pool, start=PERIOD_START, end=PERIOD_END)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool_safe(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)

    baseline_state = _simulate_runtime_replay(
        replay_pool,
        runtime_shadows,
        mode=f"{REPLAY_MODE}_phase498_base",
        entry_block_fn=_entry_block(pass_pbv2),
        initial_equity=1_500_000.0,
    )
    baseline_met = _summary_metrics(baseline_state, initial_equity=1_500_000.0)
    baseline_pnl = float(baseline_met["total_pnl_yen"])
    baseline_pf = baseline_met["profit_factor"]
    baseline_max_dd = float(baseline_met["max_drawdown_yen"])

    sym_median, day_stats, composite_pct = _build_context(replay_pool)
    days_needed = sorted({str(log.get("day") or "")[:8] for log in baseline_state.trade_log})
    day_snaps = {day: _load_day_event_snaps(kabu, day) for day in days_needed}

    r10_vals = []
    for log in baseline_state.trade_log:
        tr = dict(log.get("trade") or {})
        r10 = _rise(tr, 10)
        if r10 is not None:
            r10_vals.append(r10)
    r10_thr_top30 = _top_pct_threshold(r10_vals, R10_TOP_PCT)

    rows = [
        _enrich_row(
            log,
            sym_median=sym_median,
            day_stats=day_stats,
            composite_pct=composite_pct,
            day_snaps=day_snaps,
            r10_thr_top30=r10_thr_top30,
        )
        for log in baseline_state.trade_log
    ]
    days = sorted({str(r["day"]) for r in rows})
    ranking = _rank_features(rows, days=days)

    chase_rows = [r for r in rows if r.get("is_chase")]
    non_chase = [r for r in rows if r.get("cohort") == "non_chase"]
    chase_pnls = [float(r.get("pnl_yen_100") or 0) for r in chase_rows]
    non_pnls = [float(r.get("pnl_yen_100") or 0) for r in non_chase]

    top_feats = [r["feature_id"] for r in ranking[:8]]
    w_pat = _pattern_medians(rows, cohort="W_chase", feats=top_feats)
    l_pat = _pattern_medians(rows, cohort="L_chase", feats=top_feats)

    existing_ranked = [r for r in ranking if not r.get("is_new")]
    new_ranked = [r for r in ranking if r.get("is_new")]
    best_existing_d = abs(float(existing_ranked[0].get("cohens_d") or 0)) if existing_ranked else 0.0
    best_new_d = abs(float(new_ranked[0].get("cohens_d") or 0)) if new_ranked else 0.0
    stronger_new = best_new_d > best_existing_d

    # Pool thresholds for guards
    pool_feats: list[dict[str, Any]] = []
    for trade in replay_pool:
        if not pass_pbv2(trade):
            continue
        pool_feats.append(
            _enrich_row(
                {"trade": trade, "day": str(trade.get("day") or "")[:8], "pnl_yen": 0, "exit_reason": ""},
                sym_median=sym_median,
                day_stats=day_stats,
                composite_pct=composite_pct,
                day_snaps=day_snaps,
                r10_thr_top30=r10_thr_top30,
            )
        )

    def vals(key: str) -> list[float]:
        return [float(r[key]) for r in pool_feats if _float(r.get(key)) is not None]

    def bottom_thr(key: str) -> float:
        ranked = sorted(vals(key))
        if not ranked:
            return 0.0
        idx = max(0, int(round(0.20 * (len(ranked) - 1))))
        return ranked[idx]

    thresholds = {
        "chase_decay_score": _top_pct_threshold(vals("chase_decay_score"), 80.0),
        "chase_followthrough_score": bottom_thr("chase_followthrough_score"),
        "chase_near_high_exhaustion": _top_pct_threshold(vals("chase_near_high_exhaustion"), 80.0),
        "chase_failure_score": _top_pct_threshold(vals("chase_failure_score"), 80.0),
        "chase_board_confirmation": bottom_thr("chase_board_confirmation"),
        "chase_vwap_confirmation": bottom_thr("chase_vwap_confirmation"),
    }

    def feat_row(trade: Mapping[str, Any]) -> dict[str, Any]:
        return _enrich_row(
            {"trade": trade, "day": str(trade.get("day") or "")[:8], "pnl_yen": 0, "exit_reason": ""},
            sym_median=sym_median,
            day_stats=day_stats,
            composite_pct=composite_pct,
            day_snaps=day_snaps,
            r10_thr_top30=r10_thr_top30,
        )

    def block_top(key: str, thr: float) -> Callable[[Mapping[str, Any]], bool]:
        def _b(trade: Mapping[str, Any]) -> bool:
            v = _float(feat_row(trade).get(key))
            return v is not None and v >= thr
        return _b

    def block_bottom(key: str, thr: float) -> Callable[[Mapping[str, Any]], bool]:
        def _b(trade: Mapping[str, Any]) -> bool:
            v = _float(feat_row(trade).get(key))
            return v is not None and v <= thr
        return _b

    def block_and(*fns: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
        return lambda t: all(fn(t) for fn in fns)

    guard_specs: list[tuple[str, Callable[[Mapping[str, Any]], bool]]] = [
        ("A_chase_decay_top20", block_top("chase_decay_score", thresholds["chase_decay_score"])),
        ("B_followthrough_bottom20", block_bottom("chase_followthrough_score", thresholds["chase_followthrough_score"])),
        ("C_near_high_exhaustion_top20", block_top("chase_near_high_exhaustion", thresholds["chase_near_high_exhaustion"])),
        ("D_chase_failure_top20", block_top("chase_failure_score", thresholds["chase_failure_score"])),
        ("E_board_confirmation_bottom20", block_bottom("chase_board_confirmation", thresholds["chase_board_confirmation"])),
        ("F_vwap_confirmation_bottom20", block_bottom("chase_vwap_confirmation", thresholds["chase_vwap_confirmation"])),
    ]

    baseline_rows = [{**r, "pnl_yen": r.get("pnl_yen_100")} for r in rows]
    medians_empty: dict[str, float] = {}
    uni: list[tuple[str, float, Callable[[Mapping[str, Any]], bool]]] = []
    for name, fn in guard_specs[:6]:
        blocked_pnl = 0.0
        for r in baseline_rows:
            tr = dict(r.get("_trade") or {})
            if fn(tr):
                blocked_pnl += float(r.get("pnl_yen_100") or 0)
        uni.append((name, -blocked_pnl, fn))
    uni.sort(key=lambda x: x[1], reverse=True)
    guard_specs.append((f"G_conservative_{uni[0][0]}_{uni[1][0]}", block_and(uni[0][2], uni[1][2])))

    cf_rows: list[dict[str, Any]] = []

    def _run_cf(name: str, block_fn: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
        st = _replay_with_extra_block(replay_pool, runtime_shadows, extra_block=block_fn, mode_suffix=name[:14])
        cf = _counterfactual_row(
            st, baseline_state,
            scenario=name,
            baseline_pnl=baseline_pnl,
            baseline_pf=baseline_pf,
            baseline_max_dd=baseline_max_dd,
            baseline_rows=baseline_rows,
            medians=medians_empty,
        )
        return cf

    if parallel and len(guard_specs) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_run_cf, n, b): n for n, b in guard_specs}
            for fut in as_completed(futs):
                cf_rows.append(fut.result())
    else:
        for name, block_fn in guard_specs:
            cf_rows.append(_run_cf(name, block_fn))

    cf_rows.sort(key=lambda r: float(r.get("delta_pnl_yen_100") or 0), reverse=True)
    best_cf = cf_rows[0] if cf_rows else {}
    best_name = str(best_cf.get("scenario") or "")
    best_block = dict(guard_specs).get(best_name, guard_specs[0][1])

    day622_pnl = sum(float(r.get("pnl_yen_100") or 0) for r in rows if r.get("day") == DAY_622)
    day622_share = abs(day622_pnl / baseline_pnl) if baseline_pnl else 0.0

    robustness_rows: list[dict[str, Any]] = []

    def _rob(test: str, pool: Sequence[Mapping[str, Any]], *, suffix: str) -> None:
        st_base = _simulate_runtime_replay(
            pool, runtime_shadows,
            mode=f"{REPLAY_MODE}_b_{suffix}",
            entry_block_fn=_entry_block(pass_pbv2),
            initial_equity=1_500_000.0,
        )
        base_met = _summary_metrics(st_base, initial_equity=1_500_000.0)
        st_g = _replay_with_extra_block(pool, runtime_shadows, extra_block=best_block, mode_suffix=f"g_{suffix}")
        g_met = _summary_metrics(st_g, initial_equity=1_500_000.0)
        sub_base = _rows_from_state(st_base)
        cf = _counterfactual_row(
            st_g, st_base, scenario=test,
            baseline_pnl=float(base_met["total_pnl_yen"]),
            baseline_pf=base_met["profit_factor"],
            baseline_max_dd=float(base_met["max_drawdown_yen"]),
            baseline_rows=sub_base,
            medians=medians_empty,
        )
        robustness_rows.append(
            {
                "test": test,
                "scenario": best_name,
                "total_pnl_yen_100": round(float(g_met["total_pnl_yen"]), 2),
                "profit_factor": g_met["profit_factor"],
                "delta_pnl_vs_baseline": round(float(g_met["total_pnl_yen"]) - float(base_met["total_pnl_yen"]), 2),
                "blocked_winners": cf.get("blocked_winners"),
                "trade_count": g_met["trade_count"],
            }
        )

    for day in days:
        pool_day = [t for t in replay_pool if str(t.get("day") or "")[:8] != day]
        if len(pool_day) < 50:
            continue
        _rob(f"LOO_day_{day}", pool_day, suffix=f"loo_{day}")

    _rob("exclude_6976", [t for t in replay_pool if str(t.get("symbol") or "") != "6976.T"], suffix="ex6976")
    _rob("exclude_6_22", [t for t in replay_pool if str(t.get("day") or "")[:8] != DAY_622], suffix="ex622")
    sym_counts = Counter(str(r["symbol"]) for r in rows)
    top_sym = sym_counts.most_common(1)[0][0] if sym_counts else ""
    _rob("exclude_top_symbol", [t for t in replay_pool if str(t.get("symbol") or "").replace(".T", "") != top_sym], suffix="extop")
    _rob("AM_only", [t for t in replay_pool if _session_bucket(t.get("entry_time")) == "AM"], suffix="am")
    _rob("PM_only", [t for t in replay_pool if _session_bucket(t.get("entry_time")) == "PM"], suffix="pm")

    loo_pos = sum(1 for r in robustness_rows if str(r.get("test", "")).startswith("LOO_day_") and float(r.get("delta_pnl_vs_baseline") or 0) > 0)
    loo_n = sum(1 for r in robustness_rows if str(r.get("test", "")).startswith("LOO_day_"))
    overfit = day622_share > 0.35 and abs(float(best_cf.get("impact_20260622") or 0)) > abs(float(best_cf.get("delta_pnl_yen_100") or 0)) * 0.4
    overfit = overfit or (loo_n > 0 and loo_pos < loo_n * 0.5)

    verdict = _verdict(
        best_cf=best_cf,
        best_new_d=best_new_d,
        best_existing_d=best_existing_d,
        overfit_flags=overfit,
    )

    top = ranking[0] if ranking else {}
    mandatory = {
        "1_chase_cohort_count": len(chase_rows),
        "2_chase_cohort_pnl_pf": {"pnl": round(sum(chase_pnls), 2), "pf": _pf(chase_pnls)},
        "3_non_chase_pnl_pf": {"pnl": round(sum(non_pnls), 2), "pf": _pf(non_pnls)},
        "4_winning_chase_traits": w_pat,
        "5_losing_chase_traits": l_pat,
        "6_strongest_chase_quality_feature": top.get("feature_id"),
        "6_cohens_d": top.get("cohens_d"),
        "7_new_beats_existing": stronger_new,
        "7_best_new_feature": new_ranked[0].get("feature_id") if new_ranked else None,
        "7_best_existing_feature": existing_ranked[0].get("feature_id") if existing_ranked else None,
        "8_best_counterfactual_guard": best_name,
        "9_delta_pnl": best_cf.get("delta_pnl_yen_100"),
        "10_blocked_winners": best_cf.get("blocked_winners"),
        "11_blocked_losers": best_cf.get("blocked_losers"),
        "12_impact_6976": best_cf.get("impact_6976"),
        "13_day622_dependent": day622_share > 0.25 and abs(float(best_cf.get("impact_20260622") or 0)) > 5000,
        "14_hurts_am": float(best_cf.get("impact_AM") or 0) < -5000,
        "15_improves_pm": float(best_cf.get("impact_PM") or 0) > 0,
        "16_overfit_risk": "high" if overfit else "moderate" if int(best_cf.get("blocked_winners") or 0) > 15 else "low",
        "17_runtime_candidate": verdict == "chase_quality_guard_candidate" and int(best_cf.get("blocked_winners") or 0) <= 5,
        "18_shadow_candidate": verdict in ("chase_quality_guard_candidate", "new_chase_feature_found"),
        "19_next_action": (
            f"Forward-shadow {best_name} + chase_decay_score on all chase entries"
            if verdict != "no_chase_edge"
            else "No chase guard; monitor r10 cohort in daily summary"
        ),
        "verdict": verdict,
        "w_chase_count": sum(1 for r in rows if r.get("cohort") == "W_chase"),
        "l_chase_count": sum(1 for r in rows if r.get("cohort") == "L_chase"),
        "r10_threshold_top30": r10_thr_top30,
        "thresholds": thresholds,
    }

    audit_export = [{k: r.get(k) for k in AUDIT_FIELDS} for r in rows]

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_audit": audit_export,
        "_ranking": ranking,
        "_counterfactual": cf_rows,
        "_robustness": robustness_rows,
    }


@dataclass
class Phase498Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 2

    def run(self) -> dict[str, Any]:
        return run_phase498(repo_root=self.repo_root, parallel=self.parallel, max_workers=self.max_workers)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        paths = {
            "audit": reports / "phase498_chase_quality_audit.csv",
            "ranking": reports / "phase498_chase_feature_ranking.csv",
            "counterfactual": reports / "phase498_chase_counterfactual.csv",
            "robustness": reports / "phase498_chase_robustness.csv",
            "summary": reports / "phase498_summary.json",
            "report": doc_root / "docs" / "operations" / "phase498_chase_quality_audit.md",
        }
        _write_csv(paths["audit"], AUDIT_FIELDS, list(result.get("_audit") or []))
        _write_csv(paths["ranking"], RANKING_FIELDS, list(result.get("_ranking") or []))
        _write_csv(paths["counterfactual"], COUNTERFACTUAL_FIELDS, list(result.get("_counterfactual") or []))
        _write_csv(paths["robustness"], ROBUSTNESS_FIELDS, list(result.get("_robustness") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        self._write_report(paths["report"], result)
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        lines = [
            "# Phase498 — Chase Quality Audit",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')} — {result.get('period_end')}",
            "",
            "## 必須回答",
            "",
        ]
        for key in (
            "1_chase_cohort_count", "2_chase_cohort_pnl_pf", "3_non_chase_pnl_pf",
            "4_winning_chase_traits", "5_losing_chase_traits", "6_strongest_chase_quality_feature",
            "7_new_beats_existing", "8_best_counterfactual_guard", "9_delta_pnl",
            "10_blocked_winners", "11_blocked_losers", "12_impact_6976", "13_day622_dependent",
            "14_hurts_am", "15_improves_pm", "16_overfit_risk", "17_runtime_candidate",
            "18_shadow_candidate", "19_next_action",
        ):
            lines.append(f"- **{key}:** {m.get(key)}")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
