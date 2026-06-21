"""
Phase483 — PBv2 stop_low_mfe Root Cause Audit (research only).

Entry-time structural audit of stop_low_mfe vs strong_winner vs normal.
No replay — entry-level feasibility only.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from pathlib import Path

from research.market_sector_heat import _write_csv
from research.phase365_production_stack_validation import phase364_blocked_only
from research.phase382_capital_constrained_backtest import _float, _position_key
from research.phase400_holding_time_audit import normalize_exit_reason
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase446_momentum_score_audit import _decompose_momentum_score
from research.phase451_entry_shape_tournament import (
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _now_iso,
    _optional_float,
)
from research.phase463_trend_pullback_population_tournament import (
    _board_bucket,
    _fill_close_proxy_shadows,
    _filter_replay_pool,
    _weak_shape_block,
)
from research.phase464_pre_gate_archetype_audit import _vwap_above_ratio, _vwap_dev
from research.phase465b_trend_gate_redesign import (
    _cohens_d,
    _day_high_distance,
    _high_update_age,
    _mi_median_split,
)
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _ensure_enriched, _load_replay_pool
from research.phase480_pbv2_loss_cluster_audit import _assign_cluster, _mfe_mae_to_exit
from research.phase481_stop_low_mfe_reduction_tournament import _build_trade_rows
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.phase470_momentum_necessity_tournament import late_chase_block
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_expectancy_score_shadow import (
    MOMENTUM_SCORE_CUTOFF_P33,
    active_score_tokens_v2,
    compute_entry_expectancy_score_fields,
)

FOCUS_SYMBOLS = ("6976", "4062", "6920", "3441", "6492", "7256", "7600")
STRONG_MFE_PCT = 1.5
PERCENTILE_CANDIDATES = (0.25, 0.33, 0.40)

OUTCOME_FEATURES = frozenset({"mfe_pct", "mae_pct", "pnl_yen", "exit_reason", "hold_sec"})

ENTRY_FEATURES = (
    "momentum_continuation_score",
    "price_mom",
    "vwap_part",
    "mfe_proxy",
    "board_imbalance",
    "r5",
    "r10",
    "r15",
    "r30",
    "vwap_dev_pct",
    "vwap_above_ratio",
    "consecutive_above_ticks",
    "day_high_distance",
    "high_update_age",
    "high_update_count_30m",
    "high_update_count_session",
    "entry_score_v2",
)

PATTERN_FEATURES = (
    "board_imbalance",
    "momentum_continuation_score",
    "vwap_part",
    "r10",
    "r30",
    "day_high_distance",
    "high_update_age",
    "vwap_above_ratio",
)

ROOT_CAUSE_FIELDS = [
    "position_key",
    "cohort",
    "symbol",
    "day",
    "entry_time",
    "pnl_yen",
    "exit_reason",
    "mfe_pct",
    "mae_pct",
    "hold_sec",
    "board_bucket",
    "momentum_continuation_score",
    "price_mom",
    "vwap_part",
    "mfe_proxy",
    "board_imbalance",
    "r5",
    "r10",
    "r15",
    "r30",
    "vwap_dev_pct",
    "vwap_above_ratio",
    "consecutive_above_ticks",
    "day_high_distance",
    "high_update_age",
    "high_update_count_30m",
    "high_update_count_session",
    "active_score_tokens_v2",
    "entry_score_v2",
    "high_drift_pass",
    "weak_shape_pass",
    "late_chase_pass",
    "near_day_high_guard_flag",
]

RANKING_FIELDS = [
    "feature",
    "cohort",
    "n",
    "mean",
    "median",
    "missing_rate",
    "cohens_d_vs_strong_winner",
    "ks_statistic_vs_strong_winner",
    "mutual_information_vs_strong_winner",
    "feature_direction",
    "is_outcome_variable",
    "rank_by_abs_cohens_d_entry",
]

PATTERN_FIELDS = [
    "pattern_id",
    "condition_count",
    "conditions",
    "threshold_summary",
    "matched_stop_low_mfe",
    "matched_strong_winner",
    "matched_normal",
    "matched_total",
    "slm_capture_rate",
    "strong_winner_fp_rate",
    "separation_score",
    "blocked_stop_low_mfe",
    "blocked_winners",
    "blocked_total",
    "blocked_pnl",
    "expected_delta",
    "impact_6976_blocked",
    "impact_4062_blocked",
    "rank_by_separation",
]


def _rx(trade: Mapping[str, Any], key: str) -> Optional[float]:
    if key == "momentum_continuation_score":
        return _float(trade.get("momentum_continuation_score"))
    if key == "price_mom":
        return _decompose_momentum_score(trade).get("price_mom_component")
    if key == "vwap_part":
        return _decompose_momentum_score(trade).get("vwap_part_component")
    if key == "mfe_proxy":
        return _decompose_momentum_score(trade).get("mfe_proxy_component")
    if key == "board_imbalance":
        return _float(trade.get("entry_order_book_imbalance"))
    if key == "r5":
        return _optional_float(trade.get("return_5min_pct")) or _optional_float(trade.get("entry_rise_5min_pct"))
    if key == "r10":
        return _optional_float(trade.get("return_10min_pct")) or _optional_float(trade.get("entry_rise_10min_pct"))
    if key == "r15":
        return _optional_float(trade.get("return_15min_pct")) or _optional_float(trade.get("entry_rise_15min_pct"))
    if key == "r30":
        return _optional_float(trade.get("return_30min_pct")) or _optional_float(trade.get("entry_rise_30min_pct"))
    if key == "vwap_dev_pct":
        return _vwap_dev(trade)
    if key == "vwap_above_ratio":
        return _vwap_above_ratio(trade)
    if key == "consecutive_above_ticks":
        return _float(trade.get("consecutive_above_ticks"))
    if key == "day_high_distance":
        return _day_high_distance(trade)
    if key == "high_update_age":
        return _high_update_age(trade)
    if key == "high_update_count_30m":
        return _float(trade.get("high_update_count_30m"))
    if key == "high_update_count_session":
        return _float(trade.get("high_update_count_session"))
    if key == "entry_score_v2":
        return _float(trade.get("entry_expectancy_score_v2"))
    return _float(trade.get(key))


def _is_stop_low_mfe(row: Mapping[str, Any]) -> bool:
    cid, _ = _assign_cluster(row)
    return cid == "A"


def _pnl_p80(rows: Sequence[Mapping[str, Any]]) -> float:
    pnls = sorted(float(r.get("pnl_yen") or 0) for r in rows)
    if not pnls:
        return 0.0
    idx = min(len(pnls) - 1, max(0, int(round(0.80 * (len(pnls) - 1)))))
    return pnls[idx]


def _cohort_label(row: Mapping[str, Any], *, pnl_threshold: float) -> str:
    if _is_stop_low_mfe(row):
        return "stop_low_mfe"
    pnl = float(row.get("pnl_yen") or 0)
    mfe = float(row.get("mfe_pct") or 0)
    if pnl >= pnl_threshold or mfe >= STRONG_MFE_PCT:
        return "strong_winner"
    return "normal"


def _guard_flags(trade: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "high_drift_pass": not guard_high_drift(trade),
        "weak_shape_pass": not _weak_shape_block(trade),
        "late_chase_pass": not late_chase_block(trade),
        "near_day_high_guard_flag": phase364_blocked_only(trade),
    }


def _ks_stat(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    if not a or not b:
        return None
    sa = sorted(float(x) for x in a)
    sb = sorted(float(x) for x in b)
    na, nb = len(sa), len(sb)
    vals = sorted(set(sa) | set(sb))
    ia = ib = 0
    max_d = 0.0
    for v in vals:
        while ia < na and sa[ia] <= v:
            ia += 1
        while ib < nb and sb[ib] <= v:
            ib += 1
        d = abs(ia / na - ib / nb)
        if d > max_d:
            max_d = d
    return round(max_d, 6)


def _feature_direction(slm_mean: Optional[float], win_mean: Optional[float]) -> str:
    if slm_mean is None or win_mean is None:
        return "unknown"
    if slm_mean > win_mean:
        return "higher_in_stop_low_mfe"
    if slm_mean < win_mean:
        return "lower_in_stop_low_mfe"
    return "equal"


def _feature_ranking(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    slm = [r for r in rows if r.get("cohort") == "stop_low_mfe"]
    sw = [r for r in rows if r.get("cohort") == "strong_winner"]
    norm = [r for r in rows if r.get("cohort") == "normal"]
    all_feats = list(ENTRY_FEATURES) + ["mfe_pct", "mae_pct", "hold_sec", "pnl_yen"]
    out: list[dict[str, Any]] = []
    entry_ranking: list[dict[str, Any]] = []

    for feat in all_feats:
        cohort_vals: dict[str, list[float]] = {}
        cohort_miss: dict[str, int] = {}
        for cohort_name, bucket in (
            ("stop_low_mfe", slm),
            ("strong_winner", sw),
            ("normal", norm),
        ):
            vals: list[float] = []
            miss = 0
            for r in bucket:
                if feat in ("mfe_pct", "mae_pct", "hold_sec", "pnl_yen"):
                    v = r.get(feat)
                else:
                    tr = r.get("trade") or r
                    v = _rx(tr, feat)
                if v is None:
                    miss += 1
                else:
                    vals.append(float(v))
            cohort_vals[cohort_name] = vals
            cohort_miss[cohort_name] = miss

        slm_vals = cohort_vals["stop_low_mfe"]
        sw_vals = cohort_vals["strong_winner"]
        d_sw = _cohens_d(slm_vals, sw_vals)
        ks = _ks_stat(slm_vals, sw_vals)
        mi = _mi_median_split(sw_vals, slm_vals) if sw_vals and slm_vals else None
        slm_mean = statistics.mean(slm_vals) if slm_vals else None
        sw_mean = statistics.mean(sw_vals) if sw_vals else None
        direction = _feature_direction(slm_mean, sw_mean)
        is_outcome = feat in OUTCOME_FEATURES

        for cohort_name in ("stop_low_mfe", "strong_winner", "normal"):
            vals = cohort_vals[cohort_name]
            bucket_len = len(slm if cohort_name == "stop_low_mfe" else sw if cohort_name == "strong_winner" else norm)
            out.append(
                {
                    "feature": feat,
                    "cohort": cohort_name,
                    "n": bucket_len,
                    "mean": round(statistics.mean(vals), 6) if vals else None,
                    "median": round(statistics.median(vals), 6) if vals else None,
                    "missing_rate": round(cohort_miss[cohort_name] / bucket_len, 4) if bucket_len else 0.0,
                    "cohens_d_vs_strong_winner": d_sw if cohort_name == "stop_low_mfe" else None,
                    "ks_statistic_vs_strong_winner": ks if cohort_name == "stop_low_mfe" else None,
                    "mutual_information_vs_strong_winner": mi if cohort_name == "stop_low_mfe" else None,
                    "feature_direction": direction if cohort_name == "stop_low_mfe" else None,
                    "is_outcome_variable": is_outcome,
                    "rank_by_abs_cohens_d_entry": None,
                }
            )

        if d_sw is not None and not is_outcome:
            entry_ranking.append(
                {
                    "feature": feat,
                    "cohens_d": d_sw,
                    "ks": ks or 0.0,
                    "mi": mi or 0.0,
                    "direction": direction,
                }
            )

    entry_ranking.sort(key=lambda r: abs(float(r.get("cohens_d") or 0)), reverse=True)
    rank_map = {r["feature"]: i + 1 for i, r in enumerate(entry_ranking)}
    for row in out:
        if row["cohort"] == "stop_low_mfe" and not row["is_outcome_variable"]:
            row["rank_by_abs_cohens_d_entry"] = rank_map.get(row["feature"])
    return out, entry_ranking


def _build_audit_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        tr = r.get("trade") or r
        flags = _guard_flags(tr)
        try:
            score_fields = compute_entry_expectancy_score_fields(trade=tr)
        except Exception:
            score_fields = {}
        tokens = active_score_tokens_v2(tr)
        row: dict[str, Any] = {
            "position_key": r.get("position_key"),
            "cohort": r.get("cohort"),
            "symbol": r.get("symbol"),
            "day": r.get("day"),
            "entry_time": r.get("entry_time"),
            "pnl_yen": r.get("pnl_yen"),
            "exit_reason": r.get("exit_reason"),
            "mfe_pct": r.get("mfe_pct"),
            "mae_pct": r.get("mae_pct"),
            "hold_sec": r.get("hold_sec"),
            "board_bucket": r.get("board_bucket"),
            "active_score_tokens_v2": ";".join(tokens),
            "entry_score_v2": score_fields.get("entry_expectancy_score_v2"),
            **flags,
        }
        for feat in ENTRY_FEATURES:
            if feat == "entry_score_v2":
                continue
            row[feat] = _rx(tr, feat)
        out.append(row)
    return out


def _percentile(vals: Sequence[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return s[idx]


def _pool_values(rows: Sequence[Mapping[str, Any]], key: str) -> list[float]:
    out: list[float] = []
    for r in rows:
        tr = r.get("trade") or r
        v = _rx(tr, key)
        if v is not None:
            out.append(float(v))
    return out


def _best_threshold(
    key: str,
    rows: Sequence[Mapping[str, Any]],
    slm_keys: set[str],
) -> tuple[float, str, str]:
    vals = _pool_values(rows, key)
    if not vals:
        return 0.0, f"{key}<=0", "lt"
    slm_vals = []
    sw_vals = []
    for r in rows:
        tr = r.get("trade") or r
        v = _rx(tr, key)
        if v is None:
            continue
        if r.get("position_key") in slm_keys:
            slm_vals.append(float(v))
        elif r.get("cohort") == "strong_winner":
            sw_vals.append(float(v))
    direction = "lt" if (statistics.mean(slm_vals) if slm_vals else 0) < (statistics.mean(sw_vals) if sw_vals else 0) else "gt"
    best_thr = _percentile(vals, PERCENTILE_CANDIDATES[0])
    best_p = PERCENTILE_CANDIDATES[0]
    best_score = -1e18

    def _rejects(v: float, thr: float) -> bool:
        return v < thr if direction == "lt" else v > thr

    for p in PERCENTILE_CANDIDATES:
        thr = _percentile(vals, p)
        blocked_slm = blocked_sw = 0
        for r in rows:
            tr = r.get("trade") or r
            v = _rx(tr, key)
            if v is None or not _rejects(float(v), thr):
                continue
            if r.get("position_key") in slm_keys:
                blocked_slm += 1
            elif r.get("cohort") == "strong_winner":
                blocked_sw += 1
        score = blocked_slm - 0.75 * blocked_sw
        if score > best_score:
            best_score = score
            best_p = p
            best_thr = thr
    op = "<" if direction == "lt" else ">"
    return best_thr, f"{key}{op}{best_thr:.4f}@p{int(best_p * 100)}", direction


def _reject_fn(key: str, thr: float, direction: str) -> Callable[[Mapping[str, Any]], bool]:
    def fn(r: Mapping[str, Any]) -> bool:
        tr = r.get("trade") or r
        v = _rx(tr, key)
        if v is None:
            return False
        return float(v) < thr if direction == "lt" else float(v) > thr

    return fn


def _eval_pattern(
    rows: Sequence[Mapping[str, Any]],
    reject_fns: Sequence[Callable[[Mapping[str, Any]], bool]],
    *,
    pattern_id: str,
    conditions: str,
    threshold_summary: str,
) -> dict[str, Any]:
    slm = sw = norm = 0
    blocked: list[Mapping[str, Any]] = []
    for r in rows:
        if not all(fn(r) for fn in reject_fns):
            continue
        blocked.append(r)
        c = r.get("cohort")
        if c == "stop_low_mfe":
            slm += 1
        elif c == "strong_winner":
            sw += 1
        else:
            norm += 1

    total_slm = sum(1 for r in rows if r.get("cohort") == "stop_low_mfe")
    total_sw = sum(1 for r in rows if r.get("cohort") == "strong_winner")
    slm_cap = round(slm / total_slm, 4) if total_slm else 0.0
    sw_fp = round(sw / total_sw, 4) if total_sw else 0.0
    sep = round(slm_cap - sw_fp, 4)
    blocked_pnl = round(sum(float(r.get("pnl_yen") or 0) for r in blocked), 2)
    impact6976 = sum(1 for r in blocked if r.get("symbol") == "6976")
    impact4062 = sum(1 for r in blocked if r.get("symbol") == "4062")

    return {
        "pattern_id": pattern_id,
        "condition_count": len(reject_fns),
        "conditions": conditions,
        "threshold_summary": threshold_summary,
        "matched_stop_low_mfe": slm,
        "matched_strong_winner": sw,
        "matched_normal": norm,
        "matched_total": len(blocked),
        "slm_capture_rate": slm_cap,
        "strong_winner_fp_rate": sw_fp,
        "separation_score": sep,
        "blocked_stop_low_mfe": slm,
        "blocked_winners": sw,
        "blocked_total": len(blocked),
        "blocked_pnl": blocked_pnl,
        "expected_delta": round(-blocked_pnl, 2),
        "impact_6976_blocked": impact6976,
        "impact_4062_blocked": impact4062,
    }


def _build_patterns(
    rows: Sequence[Mapping[str, Any]],
    entry_ranking: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    slm_keys = {r["position_key"] for r in rows if r.get("cohort") == "stop_low_mfe"}
    patterns: list[dict[str, Any]] = []

    thresholds: dict[str, tuple[float, str, str]] = {}
    for feat in PATTERN_FEATURES:
        thr, summary, direction = _best_threshold(feat, rows, slm_keys)
        thresholds[feat] = (thr, summary, direction)

    for feat in PATTERN_FEATURES:
        thr, summary, direction = thresholds[feat]
        fn = _reject_fn(feat, thr, direction)
        patterns.append(
            _eval_pattern(
                rows,
                [fn],
                pattern_id=f"P1_{feat}",
                conditions=summary,
                threshold_summary=summary,
            )
        )

    ranked_feats = sorted(
        PATTERN_FEATURES,
        key=lambda f: abs(
            float(next((r.get("cohens_d") for r in entry_ranking if r.get("feature") == f), 0) or 0)
        ),
        reverse=True,
    )
    for i, f1 in enumerate(ranked_feats):
        for f2 in ranked_feats[i + 1 :]:
            t1, s1, d1 = thresholds[f1]
            t2, s2, d2 = thresholds[f2]
            fn1 = _reject_fn(f1, t1, d1)
            fn2 = _reject_fn(f2, t2, d2)
            patterns.append(
                _eval_pattern(
                    rows,
                    [fn1, fn2],
                    pattern_id=f"P2_{f1}_{f2}",
                    conditions=f"{s1} AND {s2}",
                    threshold_summary=f"{s1};{s2}",
                )
            )

    patterns.sort(key=lambda p: (float(p.get("separation_score") or 0), int(p.get("blocked_stop_low_mfe") or 0)), reverse=True)
    for i, p in enumerate(patterns, start=1):
        p["rank_by_separation"] = i
    return patterns


def _failure_explanation(
    rows: Sequence[Mapping[str, Any]],
    entry_ranking: Sequence[Mapping[str, Any]],
    slm_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    sw = [r for r in rows if r.get("cohort") == "strong_winner"]
    slm_mom = [_rx(r.get("trade") or r, "momentum_continuation_score") for r in slm_rows]
    slm_mom = [float(x) for x in slm_mom if x is not None]
    sw_mom = [_rx(r.get("trade") or r, "momentum_continuation_score") for r in sw]
    sw_mom = [float(x) for x in sw_mom if x is not None]

    board_mid = sum(1 for r in slm_rows if r.get("board_bucket") == "mid")
    board_high = sum(1 for r in slm_rows if r.get("board_bucket") == "high")

    near_cutoff = sum(1 for v in slm_mom if v > MOMENTUM_SCORE_CUTOFF_P33 * 0.85)
    late_chase_would = sum(1 for r in slm_rows if not _guard_flags(r.get("trade") or r)["late_chase_pass"])
    high_drift_would = sum(1 for r in slm_rows if not _guard_flags(r.get("trade") or r)["high_drift_pass"])
    weak_shape_would = sum(1 for r in slm_rows if not _guard_flags(r.get("trade") or r)["weak_shape_pass"])

    r10_high = sum(
        1
        for r in slm_rows
        if (_rx(r.get("trade") or r, "r10") or 0) > (statistics.median(_pool_values(sw, "r10")) if sw else 0)
    )
    dhd_low = sum(
        1
        for r in slm_rows
        if (_rx(r.get("trade") or r, "day_high_distance") or 999) < 1.5
    )
    vwap_trap = sum(
        1
        for r in slm_rows
        if (_rx(r.get("trade") or r, "vwap_above_ratio") or 0) >= 0.7
        and (_rx(r.get("trade") or r, "vwap_part") or 0) < (statistics.median(_pool_values(sw, "vwap_part")) if sw else 0)
    )

    top_entry = entry_ranking[0] if entry_ranking else {}

    return {
        "1_score_cutoff_loose": {
            "slm_mean_momentum": round(statistics.mean(slm_mom), 4) if slm_mom else None,
            "sw_mean_momentum": round(statistics.mean(sw_mom), 4) if sw_mom else None,
            "cutoff": MOMENTUM_SCORE_CUTOFF_P33,
            "slm_near_cutoff_count": near_cutoff,
            "verdict": near_cutoff > len(slm_mom) * 0.5 if slm_mom else False,
        },
        "2_board_mid_high_weak": {
            "slm_mid": board_mid,
            "slm_high": board_high,
            "board_mid_share": round(board_mid / len(slm_rows), 4) if slm_rows else 0,
        },
        "3_late_chase_miss": {
            "would_late_chase_block": late_chase_would,
            "all_passed": late_chase_would == 0,
        },
        "4_high_drift_weak_shape_miss": {
            "would_high_drift_block": high_drift_would,
            "would_weak_shape_block": weak_shape_would,
            "all_passed": high_drift_would == 0 and weak_shape_would == 0,
        },
        "5_high_chase": {
            "elevated_r10_count": r10_high,
            "near_day_high_count": dhd_low,
        },
        "6_vwap_deceleration": {
            "vwap_above_low_part_count": vwap_trap,
        },
        "top_entry_separator": top_entry,
        "primary_root_cause": _primary_root_cause(
            slm_rows, entry_ranking, near_cutoff, late_chase_would, high_drift_would, weak_shape_would, r10_high, vwap_trap
        ),
    }


def _primary_root_cause(
    slm_rows: Sequence[Mapping[str, Any]],
    entry_ranking: Sequence[Mapping[str, Any]],
    near_cutoff: int,
    late_chase: int,
    high_drift: int,
    weak_shape: int,
    r10_high: int,
    vwap_trap: int,
) -> str:
    n = len(slm_rows) or 1
    if not entry_ranking or abs(float(entry_ranking[0].get("cohens_d") or 0)) < 0.20:
        return "no_entry_separation_at_accept_time"
    if r10_high / n >= 0.4 or vwap_trap / n >= 0.4:
        return "late_chase_after_rally_vwap_trap"
    if near_cutoff / n >= 0.5:
        return "momentum_cutoff_too_loose"
    if late_chase > 0:
        return "late_chase_guard_threshold_gap"
    if high_drift > 0 or weak_shape > 0:
        return "drift_shape_guard_gap"
    return f"entry_feature_gap_{entry_ranking[0].get('feature')}"


def _verdict(
    *,
    entry_ranking: Sequence[Mapping[str, Any]],
    patterns: Sequence[Mapping[str, Any]],
    explanation: Mapping[str, Any],
) -> str:
    top_d = abs(float(entry_ranking[0].get("cohens_d") or 0)) if entry_ranking else 0.0
    best_pat = patterns[0] if patterns else None

    if best_pat and float(best_pat.get("separation_score") or 0) >= 0.15 and int(best_pat.get("blocked_stop_low_mfe") or 0) >= 3:
        if float(best_pat.get("expected_delta") or 0) > 0 and int(best_pat.get("blocked_winners") or 0) <= 8:
            return "guard_candidate_found"

    if top_d >= 0.25 and explanation.get("primary_root_cause") != "no_entry_separation_at_accept_time":
        return "entry_root_cause_found"

    if top_d < 0.20:
        return "no_entry_separation"

    if top_d >= 0.20:
        return "needs_new_feature"

    return "no_entry_separation"


def run_phase483(*, repo_root: Path, parallel: bool = False, max_workers: int = 2) -> dict[str, Any]:
    del parallel, max_workers
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)
    trade_by_key = {_position_key(t): t for t in replay_pool}

    st = simulate_capacity_replay(
        replay_pool,
        runtime_shadows,
        mode="phase483_pbv2",
        entry_block_fn=_entry_block(pass_pbv2),
        baseline_accepted_keys=set(),
    )
    baseline_rows = _build_trade_rows(st, trade_by_key=trade_by_key, price_idx=price_idx)
    p80 = _pnl_p80(baseline_rows)
    for r in baseline_rows:
        r["cohort"] = _cohort_label(r, pnl_threshold=p80)

    slm_rows = [r for r in baseline_rows if r.get("cohort") == "stop_low_mfe"]
    slm_pnl = round(sum(float(r.get("pnl_yen") or 0) for r in slm_rows), 2)

    audit_rows = _build_audit_rows(baseline_rows)
    ranking_rows, entry_ranking = _feature_ranking(baseline_rows)
    patterns = _build_patterns(baseline_rows, entry_ranking)
    explanation = _failure_explanation(baseline_rows, entry_ranking, slm_rows)

    best_pat = patterns[0] if patterns else {}
    best_2cond = next((p for p in patterns if int(p.get("condition_count") or 0) == 2), best_pat)
    verdict = _verdict(entry_ranking=entry_ranking, patterns=patterns, explanation=explanation)

    top_entry = entry_ranking[0] if entry_ranking else {}
    top_outcome = next((r for r in ranking_rows if r.get("feature") == "mae_pct" and r.get("cohort") == "stop_low_mfe"), {})
    board_slm = Counter(r.get("board_bucket") for r in slm_rows)
    board_sw = Counter(r.get("board_bucket") for r in baseline_rows if r.get("cohort") == "strong_winner")

    sym6976_blocked = int(best_pat.get("impact_6976_blocked") or 0)
    sym4062_blocked = int(best_pat.get("impact_4062_blocked") or 0)
    slm6976 = [r for r in slm_rows if r.get("symbol") == "6976"]
    slm4062 = [r for r in slm_rows if r.get("symbol") == "4062"]

    mandatory = {
        "1_stop_low_mfe_count": len(slm_rows),
        "2_stop_low_mfe_total_loss": slm_pnl,
        "3_primary_root_cause": explanation.get("primary_root_cause"),
        "4_top_entry_separator": top_entry.get("feature"),
        "4b_top_entry_cohens_d": top_entry.get("cohens_d"),
        "5_most_different_from_strong_winner": top_entry.get("feature"),
        "6_board_effective": {
            "slm_mid_share": round(board_slm.get("mid", 0) / len(slm_rows), 4) if slm_rows else 0,
            "sw_mid_share": round(board_sw.get("mid", 0) / sum(board_sw.values()), 4) if board_sw else 0,
            "verdict": "board_does_not_prevent_slm",
        },
        "7_momentum_cutoff_loose": explanation.get("1_score_cutoff_loose"),
        "8_late_chase_miss": explanation.get("3_late_chase_miss"),
        "9_drift_shape_gap": explanation.get("4_high_drift_weak_shape_miss"),
        "10_best_2condition_pattern": {
            "pattern_id": best_2cond.get("pattern_id"),
            "conditions": best_2cond.get("conditions"),
            "separation_score": best_2cond.get("separation_score"),
        },
        "11_blocked_stop_low_mfe": best_pat.get("blocked_stop_low_mfe"),
        "12_blocked_winners": best_pat.get("blocked_winners"),
        "13_expected_delta": best_pat.get("expected_delta"),
        "14_6976_impact": {
            "slm_count": len(slm6976),
            "slm_pnl": round(sum(float(r.get("pnl_yen") or 0) for r in slm6976), 2),
            "best_pattern_blocked": sym6976_blocked,
        },
        "15_4062_impact": {
            "slm_count": len(slm4062),
            "slm_pnl": round(sum(float(r.get("pnl_yen") or 0) for r in slm4062), 2),
            "best_pattern_blocked": sym4062_blocked,
        },
        "16_runtime_candidate": verdict == "guard_candidate_found",
        "17_next_actions": _next_actions(verdict, explanation, best_pat, top_entry),
        "verdict": verdict,
        "accepted_count": len(baseline_rows),
        "strong_winner_count": sum(1 for r in baseline_rows if r.get("cohort") == "strong_winner"),
        "normal_count": sum(1 for r in baseline_rows if r.get("cohort") == "normal"),
        "pnl_p80_threshold": p80,
        "top_outcome_separator": {
            "feature": top_outcome.get("feature"),
            "cohens_d": top_outcome.get("cohens_d_vs_strong_winner"),
        },
        "failure_explanation": explanation,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_audit_rows": audit_rows,
        "_ranking_rows": ranking_rows,
        "_pattern_rows": patterns,
    }


def _next_actions(
    verdict: str,
    explanation: Mapping[str, Any],
    best_pat: Mapping[str, Any],
    top_entry: Mapping[str, Any],
) -> list[str]:
    actions = [f"Verdict: {verdict}"]
    cause = explanation.get("primary_root_cause")
    actions.append(f"Primary root cause: {cause}")
    if verdict == "guard_candidate_found":
        actions.append(f"Shadow feasibility: {best_pat.get('pattern_id')} — {best_pat.get('conditions')}")
        actions.append("Run Phase484 CAP replay before any runtime change")
    elif verdict == "entry_root_cause_found":
        actions.append(f"Entry separator: {top_entry.get('feature')} (d={top_entry.get('cohens_d')})")
        actions.append("Design new entry feature or tighten guard - replay required")
    elif verdict == "needs_new_feature":
        actions.append("Weak entry separation — current feature set insufficient")
        actions.append("Consider board tick archive or post-entry quality signal")
    else:
        actions.append("No stable entry-time separation — maintain PBv2 baseline")
        actions.append("stop_low_mfe is largely post-entry path outcome (mae/mfe dominate)")
    return actions


@dataclass
class Phase483Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 2

    def run(self) -> dict[str, Any]:
        return run_phase483(repo_root=self.repo_root, parallel=self.parallel, max_workers=self.max_workers)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "root_cause": reports / "phase483_stop_low_mfe_root_cause.csv",
            "ranking": reports / "phase483_stop_low_mfe_feature_ranking.csv",
            "patterns": reports / "phase483_stop_low_mfe_patterns.csv",
            "summary": reports / "phase483_summary.json",
        }
        _write_csv(paths["root_cause"], ROOT_CAUSE_FIELDS, list(result.get("_audit_rows") or []))
        _write_csv(paths["ranking"], RANKING_FIELDS, list(result.get("_ranking_rows") or []))
        _write_csv(paths["patterns"], PATTERN_FIELDS, list(result.get("_pattern_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase483_stop_low_mfe_root_cause_audit.md"
        self._write_report(report, result)
        paths["report"] = report
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        patterns = list(result.get("_pattern_rows") or [])[:8]
        lines = [
            "# Phase483 — PBv2 stop_low_mfe Root Cause Audit",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')}–{result.get('period_end')}",
            "",
            "## 必須回答",
            "",
            "| # | 項目 | 結果 |",
            "|---|------|------|",
            f"| 1 | stop_low_mfe件数 | **{m.get('1_stop_low_mfe_count')}** |",
            f"| 2 | stop_low_mfe合計損失 | **{m.get('2_stop_low_mfe_total_loss')}** |",
            f"| 3 | 主因 | **{m.get('3_primary_root_cause')}** |",
            f"| 4 | 最分離ENTRY特徴 | **{m.get('4_top_entry_separator')}** (d={m.get('4b_top_entry_cohens_d')}) |",
            f"| 5 | strong_winnerと最違い | **{m.get('5_most_different_from_strong_winner')}** |",
            f"| 6 | Board効果 | **{m.get('6_board_effective')}** |",
            f"| 7 | cutoff甘さ | **{m.get('7_momentum_cutoff_loose')}** |",
            f"| 8 | Late Chase取逃 | **{m.get('8_late_chase_miss')}** |",
            f"| 9 | Drift/Shape gap | **{m.get('9_drift_shape_gap')}** |",
            f"| 10 | 最良2条件 | **{m.get('10_best_2condition_pattern')}** |",
            f"| 11 | blocked_slm | **{m.get('11_blocked_stop_low_mfe')}** |",
            f"| 12 | blocked_winners | **{m.get('12_blocked_winners')}** |",
            f"| 13 | expected_delta | **{m.get('13_expected_delta')}** |",
            f"| 14 | 6976 | **{m.get('14_6976_impact')}** |",
            f"| 15 | 4062 | **{m.get('15_4062_impact')}** |",
            f"| 16 | Runtime候補 | **{m.get('16_runtime_candidate')}** |",
            f"| 17 | 次アクション | {m.get('17_next_actions')} |",
            "",
            "## Top patterns",
            "",
        ]
        for p in patterns:
            lines.append(
                f"- **{p.get('pattern_id')}**: sep {p.get('separation_score')} "
                f"slm {p.get('blocked_stop_low_mfe')} sw {p.get('blocked_winners')} "
                f"Δ {p.get('expected_delta')}"
            )
        lines.extend(["", f"**判定:** `{result.get('verdict')}`", ""])
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
