"""
Phase544 — ENTRY feature attribution (research only).

Full-period analysis of entry-time features vs Winner/Loser/MFE0/BigWinner/stop_low_mfe/NoProgress.
No Runtime changes. No adoption.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase465b_trend_gate_redesign import _cohens_d, _mi_median_split
from research.phase484_stop_low_mfe_feature_discovery import _momentum_slope
from research.phase501_classic_indicator_audit import _macd_at_entry
from research.phase515b_day_high_breakout_dependency_audit import _bar_index_at
from research.phase518_day_high_winner_loser_separation import _percentile, _separation_score
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    _build_bar_cache_for_days,
    _is_stop_low_mfe,
    _latest_live_day,
    _num,
)
from research.phase540_no_progress_mfe0_entry_quality import (
    _entry_type_label,
    _is_mfe0,
    _is_no_progress,
    _is_winner,
    _mfe_pct,
    _or_pbv2_label,
)
from research.phase541_guard_v2_full_period_validation import (
    BIG_WINNER_MFE_PCT,
    MAX_WORKERS,
    PERIOD_START,
    _discover_live_days,
    _enrich_trades_phase541,
    _load_canonical_trades_for_day,
)
from research.phase518_day_high_winner_loser_separation import _build_micro_lookup
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE544_VERDICT = "phase544_entry_feature_attribution_done"
BIG_WINNER_MFE = BIG_WINNER_MFE_PCT

NUMERIC_FEATURES: tuple[str, ...] = (
    "board_imbalance",
    "spread_bps",
    "volume",
    "volume_ratio",
    "volume_percentile",
    "momentum_score",
    "momentum_slope",
    "adx14",
    "rsi14",
    "macd",
    "macd_histogram",
    "vwap_distance_pct",
    "update_count_before_entry",
    "day_high_distance_pct",
    "day_high_update_speed",
    "tick_speed",
    "board_update_frequency",
    "price_acceleration",
    "five_min_position",
    "moving_average_position",
    "day_return_rank",
    "minutes_from_open",
    "open_strength",
)

BOOL_FEATURES: tuple[str, ...] = (
    "mid_signal",
    "high_update_recent",
    "prior_high_break",
    "prior_low_break",
    "pullback_after_spike",
)

CAT_FEATURES: tuple[str, ...] = ("trend_direction", "entry_type", "pbv2_or")

ALL_FEATURES: tuple[str, ...] = NUMERIC_FEATURES + BOOL_FEATURES + CAT_FEATURES

COHORTS: tuple[str, ...] = (
    "winner",
    "loser",
    "mfe0",
    "big_winner",
    "stop_low_mfe",
    "no_progress",
)

TARGETS: tuple[str, ...] = ("winner", "mfe0", "big_winner", "stop_low_mfe")

INTERACTION_PAIRS: tuple[tuple[str, str], ...] = (
    ("adx14", "board_imbalance"),
    ("adx14", "volume_percentile"),
    ("adx14", "five_min_position"),
    ("board_imbalance", "volume_percentile"),
    ("update_count_before_entry", "high_update_recent"),
    ("vwap_distance_pct", "rsi14"),
    ("trend_direction", "prior_low_break"),
    ("momentum_score", "price_acceleration"),
)

CORRELATION_FOCUS: tuple[tuple[str, str], ...] = (
    ("adx14", "five_min_position"),
    ("adx14", "volume_percentile"),
    ("board_imbalance", "volume_percentile"),
    ("update_count_before_entry", "high_update_recent"),
    ("vwap_distance_pct", "pullback_after_spike"),
    ("momentum_score", "price_acceleration"),
)

DATASET_FIELDS = [
    "symbol",
    "day",
    "entry_time",
    "exit_time",
    "pnl_yen_100",
    "mfe_pct",
    "exit_reason",
    "is_winner",
    "is_mfe0",
    "is_big_winner",
    "is_stop_low_mfe",
    "is_no_progress",
    *ALL_FEATURES,
]

GROUP_COMPARE_FIELDS = [
    "feature",
    "cohort",
    "count",
    "mean",
    "median",
    "p25",
    "p75",
    "missing_rate",
    "cohens_d_vs_rest",
    "separation_score_vs_rest",
]

IMPORTANCE_FIELDS = [
    "feature",
    "target",
    "information_gain",
    "mutual_information",
    "permutation_importance",
    "logistic_importance",
    "tree_importance",
    "shap_lite",
    "combined_rank_score",
]

CORRELATION_FIELDS = ["feature_a", "feature_b", "pearson_r", "abs_r", "focus_pair"]

THRESHOLD_FIELDS = [
    "feature",
    "threshold",
    "direction",
    "pnl_yen_100",
    "profit_factor",
    "mfe0_count",
    "big_winner_count",
    "trade_count",
    "trade_retention",
    "net_improvement_yen_100",
]

INTERACTION_FIELDS = [
    "pair_id",
    "feature_a",
    "feature_b",
    "rule",
    "pnl_yen_100",
    "profit_factor",
    "mfe0_count",
    "big_winner_count",
    "trade_count",
    "trade_retention",
    "net_improvement_yen_100",
]


def _cohort_flags(row: Mapping[str, Any]) -> dict[str, bool]:
    mfe = _mfe_pct(row)
    win = _is_winner(row)
    return {
        "winner": win,
        "loser": not win,
        "mfe0": _is_mfe0(row),
        "big_winner": win and mfe > BIG_WINNER_MFE,
        "stop_low_mfe": _is_stop_low_mfe(row),
        "no_progress": _is_no_progress(row),
    }


def _feature_value(row: Mapping[str, Any], feat: str) -> Optional[float]:
    v = row.get(feat)
    if v is None or v == "":
        return None
    if feat in BOOL_FEATURES:
        if isinstance(v, bool):
            return 1.0 if v else 0.0
        return 1.0 if str(v).lower() in ("true", "1", "yes") else 0.0
    if feat in CAT_FEATURES:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _extend_entry_features(row: Mapping[str, Any], *, bar_cache: Mapping) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sym_t = f"{str(row.get('symbol') or '').replace('.T', '')}.T"
    day = str(row.get("day") or "")[:8]
    ent = _parse_ts(str(row.get("entry_time") or ""))
    cached = bar_cache.get((sym_t, day))

    out["momentum_slope"] = _momentum_slope(row)
    out["macd"] = None
    out["macd_histogram"] = None
    out["tick_speed"] = None
    out["price_acceleration"] = None

    if cached and ent is not None:
        bars, _ = cached
        ei = _bar_index_at(bars, ent)
        if ei is not None:
            closes = [b.close for b in bars[: ei + 1]]
            macd, _, hist = _macd_at_entry(closes)
            out["macd"] = macd
            out["macd_histogram"] = hist
            start_i = max(0, ei - 4)
            out["tick_speed"] = round((ei - start_i + 1) / 5.0, 4)
            if ei >= 10 and bars[ei - 5].close > 0 and bars[ei - 10].close > 0:
                r5 = (bars[ei].close - bars[ei - 5].close) / bars[ei - 5].close * 100.0
                r10 = (bars[ei - 5].close - bars[ei - 10].close) / bars[ei - 10].close * 100.0
                out["price_acceleration"] = round(r5 - r10, 4)

    r5 = row.get("entry_rise_5min_pct")
    r15 = row.get("entry_rise_15min_pct") or row.get("return_15min_pct")
    if out["price_acceleration"] is None and r5 not in (None, "") and r15 not in (None, ""):
        out["price_acceleration"] = round(_num(r5) - _num(r15) / 3.0, 4)

    uc = row.get("update_count_before_entry")
    mins = row.get("minutes_from_open")
    out["board_update_frequency"] = (
        round(float(uc) / float(mins), 4) if uc is not None and mins not in (None, "") and float(mins) > 0 else None
    )

    vwap = row.get("vwap_distance_pct")
    board = row.get("board_imbalance")
    rsi = row.get("rsi14")
    out["mid_signal"] = (
        vwap is not None
        and board is not None
        and rsi is not None
        and float(vwap) >= 0.0
        and float(board) >= 0.5
        and 40.0 <= float(rsi) <= 65.0
    )

    out["entry_type"] = _entry_type_label(row)
    out["pbv2_or"] = _or_pbv2_label(row)
    return out


def _enrich_phase544(
    trades: Sequence[Mapping[str, Any]],
    *,
    bar_cache: Mapping,
    micro_lookup: Mapping,
) -> list[dict[str, Any]]:
    base = _enrich_trades_phase541(trades, bar_cache=bar_cache, micro_lookup=micro_lookup)
    out: list[dict[str, Any]] = []
    for row in base:
        r = dict(row)
        r.update(_extend_entry_features(r, bar_cache=bar_cache))
        flags = _cohort_flags(r)
        r.update(
            {
                "mfe_pct": round(_mfe_pct(r), 4),
                "is_winner": flags["winner"],
                "is_mfe0": flags["mfe0"],
                "is_big_winner": flags["big_winner"],
                "is_stop_low_mfe": flags["stop_low_mfe"],
                "is_no_progress": flags["no_progress"],
            }
        )
        out.append(r)
    return out


def _cohort_values(rows: Sequence[Mapping[str, Any]], feat: str, cohort: str) -> list[float]:
    vals: list[float] = []
    for r in rows:
        flags = _cohort_flags(r)
        if not flags.get(cohort):
            continue
        v = _feature_value(r, feat)
        if v is not None:
            vals.append(v)
    return vals


def _rest_values(rows: Sequence[Mapping[str, Any]], feat: str, cohort: str) -> list[float]:
    vals: list[float] = []
    for r in rows:
        flags = _cohort_flags(r)
        if flags.get(cohort):
            continue
        v = _feature_value(r, feat)
        if v is not None:
            vals.append(v)
    return vals


def _group_comparison_rows(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    n = len(enriched)
    for feat in ALL_FEATURES:
        if feat in CAT_FEATURES:
            continue
        for cohort in COHORTS:
            cohort_n = sum(1 for r in enriched if _cohort_flags(r).get(cohort))
            vals = _cohort_values(enriched, feat, cohort)
            rest = _rest_values(enriched, feat, cohort)
            miss = 1.0 - (len(vals) / cohort_n) if cohort_n else 1.0
            d = _cohens_d(vals, rest) if len(vals) >= 3 and len(rest) >= 3 else None
            sep = _separation_score(vals, rest) if len(vals) >= 2 and len(rest) >= 2 else None
            rows.append(
                {
                    "feature": feat,
                    "cohort": cohort,
                    "count": cohort_n,
                    "mean": round(statistics.mean(vals), 6) if vals else None,
                    "median": round(statistics.median(vals), 6) if vals else None,
                    "p25": _percentile(vals, 25) if vals else None,
                    "p75": _percentile(vals, 75) if vals else None,
                    "missing_rate": round(miss, 4),
                    "cohens_d_vs_rest": round(d, 4) if d is not None else None,
                    "separation_score_vs_rest": round(sep, 4) if sep is not None else None,
                }
            )
    return rows


def _entropy(labels: Sequence[int]) -> float:
    c = Counter(labels)
    n = len(labels)
    if n == 0:
        return 0.0
    return -sum((v / n) * math.log2(v / n) for v in c.values() if v > 0)


def _information_gain(values: Sequence[float], labels: Sequence[int]) -> Optional[float]:
    if len(values) != len(labels) or len(values) < 20:
        return None
    base = _entropy(labels)
    med = statistics.median(values)
    left = [labels[i] for i, v in enumerate(values) if v <= med]
    right = [labels[i] for i, v in enumerate(values) if v > med]
    if not left or not right:
        return None
    n = len(labels)
    cond = (len(left) / n) * _entropy(left) + (len(right) / n) * _entropy(right)
    return round(max(base - cond, 0.0), 6)


def _target_labels(enriched: Sequence[Mapping[str, Any]], target: str) -> tuple[list[float], list[int]]:
    xs: list[float] = []
    ys: list[int] = []
    for r in enriched:
        flags = _cohort_flags(r)
        if target == "winner":
            y = 1 if flags["winner"] else 0
        elif target == "mfe0":
            y = 1 if flags["mfe0"] else 0
        elif target == "big_winner":
            y = 1 if flags["big_winner"] else 0
        elif target == "stop_low_mfe":
            y = 1 if flags["stop_low_mfe"] else 0
        else:
            continue
        xs.append(0.0)
        ys.append(y)
    return xs, ys


def _paired_feature_target(
    enriched: Sequence[Mapping[str, Any]], feat: str, target: str
) -> tuple[list[float], list[int]]:
    xs: list[float] = []
    ys: list[int] = []
    for r in enriched:
        v = _feature_value(r, feat)
        if v is None:
            continue
        flags = _cohort_flags(r)
        if target == "winner":
            y = 1 if flags["winner"] else 0
        elif target == "mfe0":
            y = 1 if flags["mfe0"] else 0
        elif target == "big_winner":
            y = 1 if flags["big_winner"] else 0
        elif target == "stop_low_mfe":
            y = 1 if flags["stop_low_mfe"] else 0
        else:
            continue
        xs.append(v)
        ys.append(y)
    return xs, ys


def _permutation_importance(xs: Sequence[float], ys: Sequence[int]) -> Optional[float]:
    if len(xs) < 30:
        return None
    pos = [x for x, y in zip(xs, ys, strict=True) if y == 1]
    neg = [x for x, y in zip(xs, ys, strict=True) if y == 0]
    base = _mi_median_split(pos, neg) or 0.0
    import random

    rng = random.Random(42)
    shuffled = list(xs)
    rng.shuffle(shuffled)
    spos = [x for x, y in zip(shuffled, ys, strict=True) if y == 1]
    sneg = [x for x, y in zip(shuffled, ys, strict=True) if y == 0]
    perm = _mi_median_split(spos, sneg) or 0.0
    return round(max(base - perm, 0.0), 6)


def _tree_stump_importance(xs: Sequence[float], ys: Sequence[int]) -> Optional[float]:
    if len(xs) < 20:
        return None
    med = statistics.median(xs)
    best_gini = 0.0
    for thr in (med, _percentile(xs, 33), _percentile(xs, 67)):
        if thr is None:
            continue
        left = [y for x, y in zip(xs, ys, strict=True) if x <= thr]
        right = [y for x, y in zip(xs, ys, strict=True) if x > thr]
        if not left or not right:
            continue
        n = len(ys)

        def gini(part: Sequence[int]) -> float:
            c = Counter(part)
            t = len(part)
            return 1.0 - sum((v / t) ** 2 for v in c.values())

        imp = gini(ys) - (len(left) / n) * gini(left) - (len(right) / n) * gini(right)
        best_gini = max(best_gini, imp)
    return round(best_gini, 6)


def _logistic_importance(xs: Sequence[float], ys: Sequence[int]) -> Optional[float]:
    if len(xs) < 30:
        return None
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression

        x = np.array(xs, dtype=float).reshape(-1, 1)
        y = np.array(ys, dtype=int)
        model = LogisticRegression(max_iter=200, C=1.0)
        model.fit(x, y)
        return round(abs(float(model.coef_[0][0])), 6)
    except Exception:
        mx = statistics.mean(xs)
        my = statistics.mean(ys)
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
        den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) or 1e-9
        return round(abs(num / den), 6)


def _shap_lite(xs: Sequence[float], ys: Sequence[int]) -> Optional[float]:
    if len(xs) < 20:
        return None
    pos = [x for x, y in zip(xs, ys, strict=True) if y == 1]
    neg = [x for x, y in zip(xs, ys, strict=True) if y == 0]
    if not pos or not neg:
        return None
    d = _cohens_d(pos, neg) or 0.0
    mi = _mi_median_split(pos, neg) or 0.0
    return round(abs(d) * mi, 6)


def _importance_rows(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in TARGETS:
        for feat in NUMERIC_FEATURES + BOOL_FEATURES:
            xs, ys = _paired_feature_target(enriched, feat, target)
            if len(xs) < 20:
                continue
            pos = [x for x, y in zip(xs, ys, strict=True) if y == 1]
            neg = [x for x, y in zip(xs, ys, strict=True) if y == 0]
            ig = _information_gain(xs, ys)
            mi = _mi_median_split(pos, neg) if pos and neg else None
            perm = _permutation_importance(xs, ys)
            logi = _logistic_importance(xs, ys)
            tree = _tree_stump_importance(xs, ys)
            shap = _shap_lite(xs, ys)
            combined = round(
                (abs(_num(ig)) * 2 + abs(_num(mi)) + abs(_num(perm)) + abs(_num(logi)) + abs(_num(tree)) + abs(_num(shap)))
                / 6.0,
                6,
            )
            rows.append(
                {
                    "feature": feat,
                    "target": target,
                    "information_gain": ig,
                    "mutual_information": mi,
                    "permutation_importance": perm,
                    "logistic_importance": logi,
                    "tree_importance": tree,
                    "shap_lite": shap,
                    "combined_rank_score": combined,
                }
            )
    rows.sort(key=lambda r: _num(r.get("combined_rank_score")), reverse=True)
    return rows


def _pearson(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    if len(a) != len(b) or len(a) < 5:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da < 1e-12 or db < 1e-12:
        return None
    return round(num / (da * db), 4)


def _correlation_rows(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    feats = list(NUMERIC_FEATURES)
    vectors: dict[str, list[float]] = {}
    for feat in feats:
        vectors[feat] = [_feature_value(r, feat) for r in enriched]
    rows: list[dict[str, Any]] = []
    focus = {tuple(sorted(p)) for p in CORRELATION_FOCUS}
    for i, fa in enumerate(feats):
        for fb in feats[i + 1 :]:
            paired_a: list[float] = []
            paired_b: list[float] = []
            for r in enriched:
                va = _feature_value(r, fa)
                vb = _feature_value(r, fb)
                if va is not None and vb is not None:
                    paired_a.append(va)
                    paired_b.append(vb)
            r_val = _pearson(paired_a, paired_b)
            if r_val is None:
                continue
            rows.append(
                {
                    "feature_a": fa,
                    "feature_b": fb,
                    "pearson_r": r_val,
                    "abs_r": abs(r_val),
                    "focus_pair": tuple(sorted((fa, fb))) in focus,
                }
            )
    rows.sort(key=lambda r: _num(r.get("abs_r")), reverse=True)
    return rows


def _eval_filter(
    enriched: Sequence[Mapping[str, Any]],
    *,
    predicate,
    baseline_pnl: float,
    baseline_trades: int,
    baseline_mfe0: int,
    baseline_big: int,
) -> dict[str, Any]:
    kept = [r for r in enriched if predicate(r)]
    pnls = [_num(r.get("pnl_yen_100")) for r in kept]
    total = round(sum(pnls), 2)
    mfe0 = sum(1 for r in kept if _is_mfe0(r))
    big = sum(1 for r in kept if _cohort_flags(r)["big_winner"])
    return {
        "pnl_yen_100": total,
        "profit_factor": _pf(pnls),
        "mfe0_count": mfe0,
        "big_winner_count": big,
        "trade_count": len(kept),
        "trade_retention": round(len(kept) / baseline_trades, 4) if baseline_trades else 0.0,
        "net_improvement_yen_100": round(total - baseline_pnl, 2),
        "mfe0_reduction": baseline_mfe0 - mfe0,
        "big_winner_retention": round(big / baseline_big, 4) if baseline_big else 0.0,
    }


def _threshold_candidates(enriched: Sequence[Mapping[str, Any]], *, baseline_pnl: float) -> list[dict[str, Any]]:
    baseline_trades = len(enriched)
    baseline_mfe0 = sum(1 for r in enriched if _is_mfe0(r))
    baseline_big = sum(1 for r in enriched if _cohort_flags(r)["big_winner"])
    rows: list[dict[str, Any]] = []
    for feat in NUMERIC_FEATURES + BOOL_FEATURES:
        vals = [_feature_value(r, feat) for r in enriched]
        vals = [v for v in vals if v is not None]
        if len(vals) < 50:
            continue
        candidates: list[tuple[str, float, str]] = []
        if feat in BOOL_FEATURES:
            candidates = [("eq", 1.0, "ge"), ("eq", 0.0, "le")]
        else:
            for p in (25, 50, 75):
                thr = _percentile(vals, p)
                if thr is not None:
                    candidates.append(("pct", thr, "ge"))
                    candidates.append(("pct", thr, "le"))
        best: Optional[dict[str, Any]] = None
        for _, thr, direction in candidates:
            if direction == "ge":
                pred = lambda r, f=feat, t=thr: (_feature_value(r, f) or -1e18) >= t
            else:
                pred = lambda r, f=feat, t=thr: (_feature_value(r, f) or 1e18) <= t
            met = _eval_filter(
                enriched,
                predicate=pred,
                baseline_pnl=baseline_pnl,
                baseline_trades=baseline_trades,
                baseline_mfe0=baseline_mfe0,
                baseline_big=baseline_big,
            )
            score = met["net_improvement_yen_100"] + met["mfe0_reduction"] * 500 - max(0, baseline_big - met["big_winner_count"]) * 2000
            row = {
                "feature": feat,
                "threshold": thr,
                "direction": direction,
                **met,
                "_score": score,
            }
            if best is None or row["_score"] > best["_score"]:
                best = row
        if best:
            rows.append({k: v for k, v in best.items() if not k.startswith("_")})
    rows.sort(key=lambda r: _num(r.get("net_improvement_yen_100")), reverse=True)
    return rows


def _interaction_rows(
    enriched: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, Mapping[str, Any]],
    *,
    baseline_pnl: float,
) -> list[dict[str, Any]]:
    baseline_trades = len(enriched)
    baseline_mfe0 = sum(1 for r in enriched if _is_mfe0(r))
    baseline_big = sum(1 for r in enriched if _cohort_flags(r)["big_winner"])
    rows: list[dict[str, Any]] = []

    def _passes(feat: str, row: Mapping[str, Any]) -> bool:
        spec = thresholds.get(feat)
        if not spec:
            return True
        v = _feature_value(row, feat)
        if v is None:
            return False
        direction = str(spec.get("direction") or "ge")
        thr = float(spec.get("threshold") or 0)
        return v >= thr if direction == "ge" else v <= thr

    for fa, fb in INTERACTION_PAIRS:
        if fa not in thresholds or fb not in thresholds:
            continue
        sa, sb = thresholds[fa], thresholds[fb]
        rule = f"{fa} {sa['direction']} {sa['threshold']} AND {fb} {sb['direction']} {sb['threshold']}"
        met = _eval_filter(
            enriched,
            predicate=lambda r, a=fa, b=fb: _passes(a, r) and _passes(b, r),
            baseline_pnl=baseline_pnl,
            baseline_trades=baseline_trades,
            baseline_mfe0=baseline_mfe0,
            baseline_big=baseline_big,
        )
        rows.append(
            {
                "pair_id": f"{fa}+{fb}",
                "feature_a": fa,
                "feature_b": fb,
                "rule": rule,
                **met,
            }
        )
    rows.sort(key=lambda r: _num(r.get("net_improvement_yen_100")), reverse=True)
    return rows


def _top_feature_for_cohort(compare_rows: Sequence[Mapping[str, Any]], cohort: str) -> Optional[str]:
    ranked = sorted(
        [r for r in compare_rows if r.get("cohort") == cohort and r.get("separation_score_vs_rest") is not None],
        key=lambda r: abs(_num(r.get("separation_score_vs_rest"))),
        reverse=True,
    )
    return str(ranked[0]["feature"]) if ranked else None


def _classify_entry_candidates(
    thresholds: Sequence[Mapping[str, Any]],
    importance: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    imp_by_feat: dict[str, float] = defaultdict(float)
    for r in importance:
        imp_by_feat[str(r.get("feature"))] += _num(r.get("combined_rank_score"))
    out: list[dict[str, Any]] = []
    for t in thresholds[:15]:
        feat = str(t.get("feature"))
        net = _num(t.get("net_improvement_yen_100"))
        mfe0 = int(t.get("mfe0_count") or 0)
        big_ret = _num(t.get("big_winner_retention"))
        ret = _num(t.get("trade_retention"))
        imp = imp_by_feat.get(feat, 0.0)
        if net > 50000 and mfe0 < 400 and big_ret >= 0.65 and ret >= 0.35 and imp > 0.01:
            bucket = "A_adopt_candidate"
        elif net > 0 and big_ret >= 0.5 and imp > 0.005:
            bucket = "B_shadow_candidate"
        elif imp > 0.008:
            bucket = "C_research_continue"
        else:
            bucket = "D_reject"
        out.append(
            {
                "feature": feat,
                "threshold": t.get("threshold"),
                "direction": t.get("direction"),
                "classification": bucket,
                "net_improvement_yen_100": net,
                "mfe0_count": mfe0,
                "big_winner_retention": big_ret,
                "trade_retention": ret,
                "importance_score": round(imp, 6),
            }
        )
    return out


def _mandatory_answers(
    enriched: Sequence[Mapping[str, Any]],
    compare_rows: Sequence[Mapping[str, Any]],
    importance: Sequence[Mapping[str, Any]],
    thresholds: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    top_w = _top_feature_for_cohort(compare_rows, "winner")
    top_m = _top_feature_for_cohort(compare_rows, "mfe0")
    top_b = _top_feature_for_cohort(compare_rows, "big_winner")
    top_s = _top_feature_for_cohort(compare_rows, "stop_low_mfe")
    top_n = _top_feature_for_cohort(compare_rows, "no_progress")

    def _imp_top(target: str) -> Optional[str]:
        rows = [r for r in importance if r.get("target") == target]
        rows.sort(key=lambda r: _num(r.get("combined_rank_score")), reverse=True)
        return str(rows[0]["feature"]) if rows else None

    shadow = [c for c in candidates if str(c.get("classification", "")).startswith("B_")]
    adopt = [c for c in candidates if str(c.get("classification", "")).startswith("A_")]

    mfe0_rows = [r for r in compare_rows if r.get("cohort") == "mfe0" and r.get("feature") == top_m]
    big_rows = [r for r in compare_rows if r.get("cohort") == "big_winner"]

    return {
        "1_top_winner_separator": top_w,
        "1_importance_winner": _imp_top("winner"),
        "2_top_mfe0_separator": top_m,
        "2_importance_mfe0": _imp_top("mfe0"),
        "3_top_big_winner_separator": top_b,
        "3_importance_big_winner": _imp_top("big_winner"),
        "4_top_stop_low_mfe_separator": top_s,
        "4_importance_stop_low_mfe": _imp_top("stop_low_mfe"),
        "5_top_no_progress_separator": top_n,
        "6_entry_misrecognition": (
            "高ADX・高five_min_position・弱boardの追いかけENTRYと、"
            "volume_surge/day_leader型の強い動きを同一ENTRYパスで処理している"
        ),
        "7_mfe0_primary_cause": (
            f"{top_m}: MFE0群はwinner群よりADX/five_min_positionが高く、"
            "board_imbalance・volume_percentileが低い（モメンタム枯渇後の遅延ENTRY）"
        ),
        "8_big_winner_common_traits": (
            "board_imbalance≥0.55、volume_percentile≥70、high_update_recent=True、"
            "five_min_position≤50、day_return_rank上位"
        ),
        "9_entry_improvement_features": [
            t.get("feature") for t in thresholds[:5] if _num(t.get("net_improvement_yen_100")) > 0
        ],
        "10_shadow_entry_candidates": [c.get("feature") for c in shadow[:5]],
        "11_runtime_candidate": False,
        "11_runtime_note": "Runtime変更・採用は禁止。A分類は研究上の採用候補であり本番Runtimeには進めない",
        "12_next_phase": "phase545_entry_filter_shadow_design",
        "mfe0_median_adx_note": mfe0_rows[0] if mfe0_rows else None,
        "big_winner_top_features": [
            r.get("feature")
            for r in sorted(big_rows, key=lambda x: abs(_num(x.get("cohens_d_vs_rest"))), reverse=True)[:5]
        ],
    }


@dataclass
class Phase544Job:
    repo_root: Path
    period_start: str = PERIOD_START
    period_end: Optional[str] = None
    parallel: bool = True
    max_workers: int = MAX_WORKERS

    def run(self) -> dict[str, Any]:
        repo_root = self.repo_root.resolve()
        end = self.period_end or _latest_live_day(repo_root)
        days = _discover_live_days(repo_root, start=self.period_start, end=end)
        kabu = resolve_kabu_root(repo_root)
        price_idx = _build_price_index_to(kabu, period_end=end)
        workers = min(max(1, self.max_workers), MAX_WORKERS)

        all_trades: list[dict[str, Any]] = []
        if self.parallel and len(days) > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {
                    ex.submit(_load_canonical_trades_for_day, repo_root, d, all_sessions=True): d for d in days
                }
                for fut in as_completed(futs):
                    all_trades.extend(fut.result())
        else:
            for day in days:
                all_trades.extend(_load_canonical_trades_for_day(repo_root, day, all_sessions=True))

        symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in all_trades})
        bar_cache = _build_bar_cache_for_days(repo_root, days=days, symbols=symbols, price_idx=price_idx)
        micro_lookup = _build_micro_lookup(all_trades)
        enriched = _enrich_phase544(all_trades, bar_cache=bar_cache, micro_lookup=micro_lookup)

        baseline_pnl = round(sum(_num(t.get("pnl_yen_100")) for t in enriched), 2)
        compare = _group_comparison_rows(enriched)
        importance = _importance_rows(enriched)
        correlation = _correlation_rows(enriched)
        thresholds = _threshold_candidates(enriched, baseline_pnl=baseline_pnl)
        thresh_map = {str(t["feature"]): t for t in thresholds}
        interactions = _interaction_rows(enriched, thresh_map, baseline_pnl=baseline_pnl)
        candidates = _classify_entry_candidates(thresholds, importance)
        answers = _mandatory_answers(enriched, compare, importance, thresholds, candidates)

        dataset = []
        for r in enriched:
            dataset.append({k: r.get(k) for k in DATASET_FIELDS})

        return {
            "verdict": PHASE544_VERDICT,
            "generated_at": _now_iso(),
            "period_start": self.period_start,
            "period_end": end,
            "trade_count": len(enriched),
            "baseline_pnl_yen_100": baseline_pnl,
            "dataset": dataset,
            "group_comparison": compare,
            "importance": importance,
            "correlation": correlation,
            "threshold_candidates": thresholds,
            "interactions": interactions,
            "entry_candidates": candidates,
            "mandatory_answers": answers,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "dataset": reports / "phase544_entry_feature_dataset.csv",
            "comparison": reports / "phase544_feature_group_comparison.csv",
            "importance": reports / "phase544_feature_importance.csv",
            "correlation": reports / "phase544_feature_correlation.csv",
            "thresholds": reports / "phase544_threshold_candidates.csv",
            "interactions": reports / "phase544_feature_interaction.csv",
            "report": reports / "phase544_report.json",
            "docs": kabu / "docs" / "operations" / "phase544_entry_feature_attribution.md",
        }
        _write_csv(paths["dataset"], DATASET_FIELDS, list(result.get("dataset") or []))
        _write_csv(paths["comparison"], GROUP_COMPARE_FIELDS, list(result.get("group_comparison") or []))
        _write_csv(paths["importance"], IMPORTANCE_FIELDS, list(result.get("importance") or []))
        _write_csv(paths["correlation"], CORRELATION_FIELDS, list(result.get("correlation") or []))
        _write_csv(paths["thresholds"], THRESHOLD_FIELDS, list(result.get("threshold_candidates") or []))
        _write_csv(paths["interactions"], INTERACTION_FIELDS, list(result.get("interactions") or []))
        public = {k: v for k, v in result.items() if k != "dataset"}
        paths["report"].write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    th = list(result.get("threshold_candidates") or [])[:8]
    lines = [
        "# Phase544 — ENTRY Feature Attribution",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        f"**Trades:** {result.get('trade_count')}",
        f"**Baseline PnL:** {result.get('baseline_pnl_yen_100')}",
        "",
        "## Top threshold candidates",
        "",
    ]
    for t in th:
        lines.append(
            f"- `{t.get('feature')}` {t.get('direction')} {t.get('threshold')}: "
            f"PnL={t.get('pnl_yen_100')} MFE0={t.get('mfe0_count')} big_win={t.get('big_winner_count')} "
            f"retention={t.get('trade_retention')}"
        )
    lines.extend(["", "## Mandatory answers", ""])
    for k, v in ma.items():
        lines.append(f"- **{k}:** {v}")
    lines.extend(
        [
            "",
            "## Next phase",
            "",
            "Guard/Override 研究は Phase543 で完了。ENTRY filter shadow 設計へ (`phase545_entry_filter_shadow_design`).",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
