"""
Phase545C — Feature engineering for hidden loss cluster (Sub1).

Engineered change/dynamics features. Research only. No Runtime changes.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np
from sklearn.cluster import AgglomerativeClustering, KMeans
from sklearn.metrics import calinski_harabasz_score, davies_bouldin_score, silhouette_score
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso, _optional_float
from research.phase465b_trend_gate_redesign import _cohens_d
from research.phase484_stop_low_mfe_feature_discovery import _load_day_event_snaps, _momentum_slope
from research.phase515b_day_high_breakout_dependency_audit import _bar_index_at, _high_update_stats
from research.phase518_day_high_winner_loser_separation import _percentile, _separation_score
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    _build_bar_cache_for_days,
    _num,
)
from research.phase541_guard_v2_full_period_validation import (
    _discover_live_days,
    _load_canonical_trades_for_day,
)
from research.phase545b_recursive_cluster_refinement import _as_bool
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE545C_VERDICT = "phase545c_feature_engineering_hidden_loss_cluster_done"

ENGINEERED_FEATURES: tuple[str, ...] = (
    "relative_board_ratio",
    "relative_board_delta",
    "board_collapse_1m",
    "board_collapse_3m",
    "board_collapse_5m",
    "relative_volume",
    "volume_accel_1m",
    "volume_accel_3m",
    "volume_accel_5m",
    "momentum_decay_1m",
    "momentum_decay_3m",
    "momentum_decay_5m",
    "breakout_persistence_ratio",
    "vwap_recovery_min",
    "vwap_above_sec",
    "update_interval_median_sec",
    "update_interval_var_sec",
    "update_burst_score",
    "liquidity_burst",
    "exhaustion_score",
)

COHORTS: tuple[str, ...] = (
    "sub1",
    "sub0",
    "cluster1",
    "cluster5",
    "big_winner",
    "mfe0",
    "stop_low_mfe",
)

ENGINEERED_CSV_FIELDS = [
    "symbol",
    "day",
    "entry_time",
    "cohort_tags",
    "cluster_id",
    "subcluster_id",
    "pnl_yen_100",
    "is_winner",
    "is_mfe0",
    "is_big_winner",
    "is_stop_low_mfe",
    *ENGINEERED_FEATURES,
]

SEPARATION_FIELDS = [
    "feature",
    "cohort_a",
    "cohort_b",
    "count_a",
    "count_b",
    "median_a",
    "median_b",
    "p25_a",
    "p75_a",
    "missing_rate_a",
    "missing_rate_b",
    "cohens_d",
    "separation_score",
]

RECLUSTER_FIELDS = [
    "method",
    "k",
    "silhouette",
    "davies_bouldin",
    "calinski_harabasz",
    "profit_separation",
    "mfe0_separation",
    "composite_score",
    "selected",
]

SUBCLUSTER_SUMMARY_FIELDS = [
    "subcluster_id",
    "subcluster_label",
    "trade_count",
    "win_rate",
    "profit_factor",
    "total_pnl_yen_100",
    "avg_pnl_yen_100",
    "mfe0_rate",
    "big_winner_rate",
    "stop_rate",
    "no_progress_rate",
    "avg_hold_sec",
]

SHADOW_FIELDS = [
    "subcluster_id",
    "subcluster_label",
    "shadow_action",
    "rationale",
    "trade_count",
    "profit_factor",
    "mfe0_rate",
    "total_pnl_yen_100",
]


def _load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def _imb_at_or_before(snaps: Sequence[tuple[Any, float]], ts: Any) -> Optional[float]:
    best: Optional[float] = None
    for t, v in snaps:
        if t <= ts:
            best = v
        else:
            break
    return best


def _imb_median_window(snaps: Sequence[tuple[Any, float]], end_ts: Any, minutes: int) -> Optional[float]:
    start = end_ts - timedelta(minutes=minutes)
    vals = [v for t, v in snaps if start <= t <= end_ts]
    return statistics.median(vals) if vals else None


def _rise(trade: Mapping[str, Any], minutes: int) -> Optional[float]:
    for key in (f"entry_rise_{minutes}min_pct", f"return_{minutes}min_pct"):
        v = _optional_float(trade.get(key))
        if v is not None:
            return float(v)
    return None


def _compute_engineered(
    trade: Mapping[str, Any],
    *,
    board_snaps: dict[str, list[tuple[Any, float]]],
    bar_cache: Mapping,
) -> dict[str, Optional[float]]:
    sym = str(trade.get("symbol") or "").replace(".T", "")
    day = str(trade.get("day") or "")[:8]
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    out: dict[str, Optional[float]] = {k: None for k in ENGINEERED_FEATURES}
    if ent is None:
        return out

    series = board_snaps.get(sym, [])
    imb_now = _optional_float(trade.get("board_imbalance"))
    if imb_now is None and series:
        imb_now = _imb_at_or_before(series, ent)
    imb_med10 = _imb_median_window(series, ent, 10) if series else None
    if imb_now is not None and imb_med10 not in (None, 0.0):
        out["relative_board_ratio"] = round(imb_now / imb_med10, 6)
        out["relative_board_delta"] = round(imb_now - imb_med10, 6)

    for mins, key in ((1, "board_collapse_1m"), (3, "board_collapse_3m"), (5, "board_collapse_5m")):
        if imb_now is not None and series:
            past = _imb_at_or_before(series, ent - timedelta(minutes=mins))
            if past is not None:
                drop = round(past - imb_now, 6)
                out[key] = drop if drop > 0 else 0.0

    sym_t = f"{sym}.T"
    cached = bar_cache.get((sym_t, day))
    if cached:
        bars, ind_rows = cached
        ei = _bar_index_at(bars, ent)
        if ei is not None:
            vol_now = float(bars[ei].volume or 0)
            vol_hist = [float(bars[j].volume or 0) for j in range(max(0, ei - 29), ei + 1)]
            vol_med = statistics.median(vol_hist) if vol_hist else None
            if vol_med and vol_med > 0:
                out["relative_volume"] = round(vol_now / vol_med, 6)

            def vol_ratio_at(idx: int) -> Optional[float]:
                if idx < 5:
                    return None
                v = float(bars[idx].volume or 0)
                base = statistics.mean(float(bars[j].volume or 0) for j in range(idx - 5, idx))
                return round(v / base, 6) if base > 0 else None

            vr0 = vol_ratio_at(ei)
            vr1 = vol_ratio_at(max(0, ei - 1))
            vr3 = vol_ratio_at(max(0, ei - 3))
            vr5 = vol_ratio_at(max(0, ei - 5))
            if vr0 is not None and vr1 is not None:
                out["volume_accel_1m"] = round(vr0 - vr1, 6)
            if vr0 is not None and vr3 is not None:
                out["volume_accel_3m"] = round(vr0 - vr3, 6)
            if vr0 is not None and vr5 is not None:
                out["volume_accel_5m"] = round(vr0 - vr5, 6)

            stats = _high_update_stats(bars, ei, ei)
            updates = int(stats.get("day_high_update_count_before_entry") or 0)
            if updates > 0 and ei >= 1:
                day_hi = max(b.high for b in bars[: ei + 1])
                near = 0
                span = 0
                for j in range(max(0, ei - 15), ei + 1):
                    span += 1
                    if day_hi > 0 and bars[j].close >= day_hi * 0.995:
                        near += 1
                out["breakout_persistence_ratio"] = round(near / span, 4) if span else None

            vwap = ind_rows[ei].values.get("VWAP")
            if vwap is not None and float(vwap) > 0:
                above_sec = 0.0
                recovery_min = None
                last_below_idx = None
                for j in range(max(0, ei - 60), ei + 1):
                    v = ind_rows[j].values.get("VWAP")
                    if v is None or float(v) <= 0:
                        continue
                    if bars[j].close >= float(v):
                        above_sec += 60.0
                    else:
                        last_below_idx = j
                if last_below_idx is not None and bars[ei].close >= float(vwap):
                    recovery_min = round((ent - bars[last_below_idx].ts).total_seconds() / 60.0, 4)
                out["vwap_above_sec"] = round(above_sec, 2)
                out["vwap_recovery_min"] = recovery_min

    if series:
        window = [(t, v) for t, v in series if ent - timedelta(minutes=5) <= t <= ent]
        if len(window) >= 3:
            gaps = [(window[i][0] - window[i - 1][0]).total_seconds() for i in range(1, len(window))]
            out["update_interval_median_sec"] = round(statistics.median(gaps), 4)
            out["update_interval_var_sec"] = round(statistics.pvariance(gaps), 4) if len(gaps) > 1 else 0.0
            out["update_burst_score"] = round(len(window) / 5.0, 4)

    r1 = _rise(trade, 1) or _rise(trade, 5)
    r3 = _rise(trade, 3) or (_rise(trade, 5) and _rise(trade, 10) and (_rise(trade, 5) * 0.6))
    r5 = _rise(trade, 5)
    r10 = _rise(trade, 10)
    r15 = _rise(trade, 15)
    if r5 is not None and r1 is not None:
        out["momentum_decay_1m"] = round(max(r1 - r5, 0.0), 6)
    if r5 is not None and r3 is not None:
        out["momentum_decay_3m"] = round(max(r3 - r5, 0.0), 6)
    if r10 is not None and r5 is not None:
        out["momentum_decay_5m"] = round(max(r5 - (r10 - r5), 0.0), 6)
    elif r15 is not None and r5 is not None:
        out["momentum_decay_5m"] = round(max(r5 - (r15 - r5) / 2.0, 0.0), 6)

    buf = _optional_float(trade.get("board_update_frequency")) or 0.0
    rel_vol = out.get("relative_volume") or 1.0
    out["liquidity_burst"] = round(buf * rel_vol, 6)

    parts = [
        out.get("momentum_decay_5m") or 0.0,
        out.get("board_collapse_5m") or 0.0,
        max(0.0, -(out.get("volume_accel_3m") or 0.0)),
        max(0.0, -(_optional_float(trade.get("price_acceleration")) or 0.0)),
    ]
    out["exhaustion_score"] = round(sum(parts) / len(parts), 6)
    return out


def _assign_cohorts(row: Mapping[str, Any]) -> list[str]:
    tags: list[str] = []
    cid = int(row.get("cluster_id") or -1)
    sid = int(row.get("subcluster_id") if row.get("subcluster_id") not in (None, "") else -1)
    if sid == 1:
        tags.append("sub1")
    if sid == 0:
        tags.append("sub0")
    if cid == 1:
        tags.append("cluster1")
    if cid == 5:
        tags.append("cluster5")
    if _as_bool(row.get("is_big_winner")):
        tags.append("big_winner")
    if _as_bool(row.get("is_mfe0")):
        tags.append("mfe0")
    if _as_bool(row.get("is_stop_low_mfe")):
        tags.append("stop_low_mfe")
    return tags


def _feat_vals(rows: Sequence[Mapping[str, Any]], feat: str, cohort: str) -> list[float]:
    out: list[float] = []
    for r in rows:
        if cohort not in (r.get("cohort_tags") or []):
            continue
        v = r.get(feat)
        if v not in (None, ""):
            out.append(float(v))
    return out


def _separation_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    comparisons = [
        ("sub1", "sub0"),
        ("sub1", "cluster1"),
        ("sub1", "cluster5"),
        ("sub1", "big_winner"),
        ("sub1", "mfe0"),
        ("sub1", "stop_low_mfe"),
    ]
    for feat in ENGINEERED_FEATURES:
        for a, b in comparisons:
            va = _feat_vals(rows, feat, a)
            vb = _feat_vals(rows, feat, b)
            na = sum(1 for r in rows if a in (r.get("cohort_tags") or []))
            nb = sum(1 for r in rows if b in (r.get("cohort_tags") or []))
            miss_a = 1.0 - (len(va) / na) if na else 1.0
            miss_b = 1.0 - (len(vb) / nb) if nb else 1.0
            d = _cohens_d(va, vb) if len(va) >= 3 and len(vb) >= 3 else None
            sep = _separation_score(va, vb) if len(va) >= 2 and len(vb) >= 2 else None
            out.append(
                {
                    "feature": feat,
                    "cohort_a": a,
                    "cohort_b": b,
                    "count_a": len(va),
                    "count_b": len(vb),
                    "median_a": round(statistics.median(va), 6) if va else None,
                    "median_b": round(statistics.median(vb), 6) if vb else None,
                    "p25_a": _percentile(va, 25) if va else None,
                    "p75_a": _percentile(va, 75) if va else None,
                    "missing_rate_a": round(miss_a, 4),
                    "missing_rate_b": round(miss_b, 4),
                    "cohens_d": round(d, 4) if d is not None else None,
                    "separation_score": round(sep, 4) if sep is not None else None,
                }
            )
    out.sort(key=lambda r: abs(_num(r.get("separation_score"))), reverse=True)
    return out


def _feature_matrix(rows: Sequence[Mapping[str, Any]]) -> tuple[np.ndarray, list[int], dict[str, float]]:
    medians: dict[str, float] = {}
    for feat in ENGINEERED_FEATURES:
        vals = [float(r[feat]) for r in rows if r.get(feat) not in (None, "")]
        medians[feat] = statistics.median(vals) if vals else 0.0
    matrix: list[list[float]] = []
    valid: list[int] = []
    for i, row in enumerate(rows):
        vec = []
        ok = True
        for feat in ENGINEERED_FEATURES:
            v = row.get(feat)
            if v in (None, ""):
                v = medians[feat]
            fv = float(v)
            if math.isnan(fv) or math.isinf(fv):
                ok = False
                break
            vec.append(fv)
        if ok:
            matrix.append(vec)
            valid.append(i)
    return np.array(matrix, dtype=float), valid, medians


def _profit_mfe0_sep(rows: Sequence[Mapping[str, Any]], labels: np.ndarray, valid_idx: list[int]) -> tuple[float, float]:
    by: dict[int, list[float]] = defaultdict(list)
    mfe: dict[int, list[bool]] = defaultdict(list)
    for arr_i, row_i in enumerate(valid_idx):
        sid = int(labels[arr_i])
        by[sid].append(_num(rows[row_i].get("pnl_yen_100")))
        mfe[sid].append(_as_bool(rows[row_i].get("is_mfe0")))
    if len(by) < 2:
        return 0.0, 0.0
    pnls = [statistics.mean(v) for v in by.values()]
    mfe0s = [sum(v) / len(v) for v in mfe.values() if v]
    p_sep = statistics.pstdev(pnls) if len(pnls) > 1 else 0.0
    m_sep = statistics.pstdev(mfe0s) if len(mfe0s) > 1 else 0.0
    return round(p_sep, 4), round(m_sep, 4)


def _search_recluster(sub1_rows: Sequence[Mapping[str, Any]]) -> tuple[str, int, np.ndarray, list[dict[str, Any]]]:
    x, valid_idx, _ = _feature_matrix(sub1_rows)
    xs = StandardScaler().fit_transform(x)
    compare: list[dict[str, Any]] = []
    best = ("gmm", 2, np.zeros(len(x), dtype=int), -1e9)
    for method in ("kmeans", "hierarchical", "gmm"):
        for k in range(2, min(9, len(x))):
            if method == "kmeans":
                labels = KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(xs)
            elif method == "hierarchical":
                labels = AgglomerativeClustering(n_clusters=k).fit_predict(xs)
            else:
                labels = GaussianMixture(n_components=k, random_state=42, n_init=3).fit_predict(xs)
            if len(set(labels)) < 2:
                continue
            sil = float(silhouette_score(xs, labels))
            db = float(davies_bouldin_score(xs, labels))
            ch = float(calinski_harabasz_score(xs, labels))
            p_sep, m_sep = _profit_mfe0_sep(sub1_rows, labels, valid_idx)
            comp = sil * 0.35 + (1.0 / (1.0 + db)) * 0.25 + min(ch / 500.0, 1.0) * 0.2 + p_sep / 500.0 * 0.1 + m_sep * 0.1
            row = {
                "method": method,
                "k": k,
                "silhouette": round(sil, 4),
                "davies_bouldin": round(db, 4),
                "calinski_harabasz": round(ch, 2),
                "profit_separation": p_sep,
                "mfe0_separation": m_sep,
                "composite_score": round(comp, 4),
                "selected": False,
            }
            compare.append(row)
            if comp > best[3]:
                best = (method, k, labels, comp)
    for row in compare:
        if row["method"] == best[0] and row["k"] == best[1]:
            row["selected"] = True
    compare.sort(key=lambda r: _num(r.get("composite_score")), reverse=True)
    return best[0], best[1], best[2], compare


def _sub_label(centroid: Mapping[str, float], global_med: Mapping[str, float]) -> str:
    if (centroid.get("exhaustion_score") or 0) > (global_med.get("exhaustion_score") or 0) * 1.1:
        return "枯渇型"
    if (centroid.get("board_collapse_5m") or 0) > (global_med.get("board_collapse_5m") or 0) + 0.02:
        return "板崩れ型"
    if (centroid.get("momentum_decay_5m") or 0) > (global_med.get("momentum_decay_5m") or 0) * 1.1:
        return "モメンタム枯渇"
    if (centroid.get("relative_volume") or 0) < (global_med.get("relative_volume") or 1.0) * 0.9:
        return "低流動型"
    if (centroid.get("breakout_persistence_ratio") or 0) > (global_med.get("breakout_persistence_ratio") or 0) * 1.1:
        return "ブレイク維持型"
    return "混合サブ型"


def _centroid(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for feat in ENGINEERED_FEATURES:
        vals = [float(r[feat]) for r in rows if r.get(feat) not in (None, "")]
        out[feat] = round(statistics.median(vals), 6) if vals else 0.0
    return out


def _subcluster_summary(
    rows: Sequence[Mapping[str, Any]], labels: Mapping[int, str]
) -> list[dict[str, Any]]:
    by: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[int(r.get("new_subcluster_id") or 0)].append(r)
    out: list[dict[str, Any]] = []
    for sid in sorted(by):
        items = by[sid]
        pnls = [_num(t.get("pnl_yen_100")) for t in items]
        n = len(items)
        total = round(sum(pnls), 2)
        out.append(
            {
                "subcluster_id": sid,
                "subcluster_label": labels.get(sid, "未分類"),
                "trade_count": n,
                "win_rate": round(sum(1 for t in items if _as_bool(t.get("is_winner"))) / n, 4),
                "profit_factor": _pf(pnls),
                "total_pnl_yen_100": total,
                "avg_pnl_yen_100": round(total / n, 2) if n else 0.0,
                "mfe0_rate": round(sum(1 for t in items if _as_bool(t.get("is_mfe0"))) / n, 4),
                "big_winner_rate": round(sum(1 for t in items if _as_bool(t.get("is_big_winner"))) / n, 4),
                "stop_rate": round(sum(1 for t in items if _as_bool(t.get("is_stop_low_mfe"))) / n, 4),
                "no_progress_rate": round(sum(1 for t in items if _as_bool(t.get("is_no_progress"))) / n, 4),
                "avg_hold_sec": round(statistics.mean(_num(t.get("hold_sec")) for t in items), 1) if items else 0.0,
            }
        )
    return out


def _shadow_rows(summary: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in summary:
        pnl = _num(s.get("total_pnl_yen_100"))
        pf = _num(s.get("profit_factor"))
        mfe0 = _num(s.get("mfe0_rate"))
        if pnl < 0 and pf < 0.9:
            action, why = "shadow_reject", "engineered_loss_subcluster"
        elif pnl > 0 and pf >= 1.1:
            action, why = "shadow_bonus", "engineered_profit_subcluster"
        else:
            action, why = "shadow_hold", "monitor"
        if mfe0 >= 0.4 and pnl < 0:
            action, why = "shadow_reject", "high_mfe0_loss"
        out.append(
            {
                "subcluster_id": s.get("subcluster_id"),
                "subcluster_label": s.get("subcluster_label"),
                "shadow_action": action,
                "rationale": why,
                "trade_count": s.get("trade_count"),
                "profit_factor": pf,
                "mfe0_rate": mfe0,
                "total_pnl_yen_100": pnl,
            }
        )
    return out


def _mandatory_answers(
    *,
    computed: int,
    separation: Sequence[Mapping[str, Any]],
    best_k: int,
    summary: Sequence[Mapping[str, Any]],
    shadow: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    sub1_sep = [r for r in separation if r.get("cohort_a") == "sub1"]
    sub1_sep.sort(key=lambda r: abs(_num(r.get("separation_score"))), reverse=True)
    top_feat = sub1_sep[0].get("feature") if sub1_sep else None

    def _feat_rank(name: str) -> Optional[float]:
        row = next((r for r in sub1_sep if r.get("feature") == name), None)
        return abs(_num(row.get("separation_score"))) if row else None

    loss_top = min(summary, key=lambda s: _num(s.get("total_pnl_yen_100")), default={})
    profit_top = max(summary, key=lambda s: _num(s.get("total_pnl_yen_100")), default={})
    mfe0_top = max(summary, key=lambda s: _num(s.get("mfe0_rate")), default={})
    big_top = max(summary, key=lambda s: _num(s.get("big_winner_rate")), default={})
    reject = [s for s in shadow if s.get("shadow_action") == "shadow_reject"]
    bonus = [s for s in shadow if s.get("shadow_action") == "shadow_bonus"]

    return {
        "1_features_computed": computed > 0,
        "2_sub1_separator_features": [r.get("feature") for r in sub1_sep[:5]],
        "3_relative_board_effective": (_feat_rank("relative_board_delta") or 0) > 0.05,
        "4_board_collapse_effective": max(_feat_rank("board_collapse_1m") or 0, _feat_rank("board_collapse_5m") or 0) > 0.05,
        "5_relative_volume_effective": (_feat_rank("relative_volume") or 0) > 0.05,
        "6_momentum_decay_effective": max(_feat_rank("momentum_decay_3m") or 0, _feat_rank("momentum_decay_5m") or 0) > 0.05,
        "7_breakout_persistence_effective": (_feat_rank("breakout_persistence_ratio") or 0) > 0.05,
        "8_exhaustion_score_effective": (_feat_rank("exhaustion_score") or 0) > 0.05,
        "9_sub1_reseparated": len(summary) >= 2,
        "10_max_loss_subcluster": f"{loss_top.get('subcluster_id')}:{loss_top.get('subcluster_label')}",
        "11_max_profit_subcluster": f"{profit_top.get('subcluster_id')}:{profit_top.get('subcluster_label')}",
        "12_shadow_reject": [f"{s.get('subcluster_id')}:{s.get('subcluster_label')}" for s in reject],
        "13_shadow_bonus": [f"{s.get('subcluster_id')}:{s.get('subcluster_label')}" for s in bonus],
        "14_next_phase": "phase546_entry_cluster_shadow_replay",
        "top_feature": top_feat,
    }


@dataclass
class Phase545CJob:
    repo_root: Path
    period_end: str = "20260625"

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        reports = resolve_reports_dir(repo)
        kabu = resolve_kabu_root(repo)

        c545 = _load_csv(reports / "phase545_cluster_dataset.csv")
        c545b = {
            (str(r.get("symbol") or ""), str(r.get("entry_time") or "")): r
            for r in _load_csv(reports / "phase545b_cluster3_dataset.csv")
        }
        p544 = {
            (str(r.get("symbol") or ""), str(r.get("entry_time") or "")): r
            for r in _load_csv(reports / "phase544_entry_feature_dataset.csv")
        }

        days = _discover_live_days(repo, start="20260616", end=self.period_end)
        trade_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for day in days:
            for t in _load_canonical_trades_for_day(repo, day, all_sessions=True):
                trade_by_key[(str(t.get("symbol") or ""), str(t.get("entry_time") or ""))] = dict(t)

        board_snaps: dict[str, list[tuple[Any, float]]] = {}
        for day in days:
            for sym, snaps in _load_day_event_snaps(kabu, day).items():
                board_snaps.setdefault(sym, []).extend(snaps)
        for sym in board_snaps:
            board_snaps[sym].sort(key=lambda x: x[0])

        symbols = sorted({str(r.get("symbol") or "").replace(".T", "") for r in c545})
        price_idx = _build_price_index_to(kabu, period_end=self.period_end)
        bar_cache = _build_bar_cache_for_days(repo, days=days, symbols=symbols, price_idx=price_idx)

        enriched: list[dict[str, Any]] = []
        for row in c545:
            key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
            r = dict(row)
            r.update({k: v for k, v in (p544.get(key) or {}).items() if k not in r or r.get(k) in (None, "")})
            r.update({k: v for k, v in (trade_by_key.get(key) or {}).items() if k not in r or r.get(k) in (None, "")})
            b = c545b.get(key) or {}
            if b.get("subcluster_id") not in (None, ""):
                r["subcluster_id"] = b.get("subcluster_id")
            eng = _compute_engineered(r, board_snaps=board_snaps, bar_cache=bar_cache)
            r.update(eng)
            r["cohort_tags"] = _assign_cohorts(r)
            enriched.append(r)

        separation = _separation_rows(enriched)
        sub1 = [r for r in enriched if "sub1" in (r.get("cohort_tags") or [])]
        method, best_k, labels, recluster_compare = _search_recluster(sub1)
        x, valid_idx, _ = _feature_matrix(sub1)
        label_map: dict[int, str] = {}
        global_med = _centroid(sub1)
        clustered_sub1: list[dict[str, Any]] = []
        for arr_i, row_i in enumerate(valid_idx):
            r = dict(sub1[row_i])
            sid = int(labels[arr_i])
            r["new_subcluster_id"] = sid
            clustered_sub1.append(r)
        by: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for r in clustered_sub1:
            by[int(r["new_subcluster_id"])].append(r)
        for sid, items in by.items():
            label_map[sid] = _sub_label(_centroid(items), global_med)
        sub_summary = _subcluster_summary(clustered_sub1, label_map)
        shadow = _shadow_rows(sub_summary)

        importance = []
        for sid, items in sorted(by.items()):
            cent = _centroid(items)
            ranked = sorted(
                ENGINEERED_FEATURES,
                key=lambda f: abs((cent.get(f, 0.0) - global_med.get(f, 0.0)) / (abs(global_med.get(f, 0.0)) or 1.0)),
                reverse=True,
            )
            for rank, feat in enumerate(ranked[:6], start=1):
                importance.append(
                    {
                        "subcluster_id": sid,
                        "subcluster_label": label_map.get(sid, ""),
                        "feature": feat,
                        "cluster_median": cent.get(feat),
                        "global_median": global_med.get(feat),
                        "z_score_vs_global": round(
                            (cent.get(feat, 0.0) - global_med.get(feat, 0.0)) / (abs(global_med.get(feat, 0.0)) or 1.0),
                            4,
                        ),
                        "rank": rank,
                    }
                )

        answers = _mandatory_answers(
            computed=len(enriched),
            separation=separation,
            best_k=best_k,
            summary=sub_summary,
            shadow=shadow,
        )

        return {
            "verdict": PHASE545C_VERDICT,
            "generated_at": _now_iso(),
            "trade_count": len(enriched),
            "sub1_count": len(sub1),
            "best_method": method,
            "optimal_recluster_count": best_k,
            "engineered_features": enriched,
            "separation": separation,
            "recluster_compare": recluster_compare,
            "subcluster_summary": sub_summary,
            "importance": importance,
            "shadow_candidates": shadow,
            "mandatory_answers": answers,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "engineered": reports / "phase545c_engineered_features.csv",
            "separation": reports / "phase545c_feature_separation.csv",
            "recluster": reports / "phase545c_recluster_summary.csv",
            "subcluster": reports / "phase545c_subcluster_summary.csv",
            "shadow": reports / "phase545c_shadow_candidates.csv",
            "report": reports / "phase545c_report.json",
            "docs": kabu / "docs" / "operations" / "phase545c_feature_engineering_hidden_loss_cluster.md",
        }
        eng_rows = []
        for r in result.get("engineered_features") or []:
            row = {k: r.get(k) for k in ENGINEERED_CSV_FIELDS}
            row["cohort_tags"] = "|".join(r.get("cohort_tags") or [])
            eng_rows.append(row)
        _write_csv(paths["engineered"], ENGINEERED_CSV_FIELDS, eng_rows)
        _write_csv(paths["separation"], SEPARATION_FIELDS, list(result.get("separation") or []))
        _write_csv(paths["recluster"], RECLUSTER_FIELDS, list(result.get("recluster_compare") or []))
        _write_csv(paths["subcluster"], SUBCLUSTER_SUMMARY_FIELDS, list(result.get("subcluster_summary") or []))
        _write_csv(paths["shadow"], SHADOW_FIELDS, list(result.get("shadow_candidates") or []))
        public = {k: v for k, v in result.items() if k != "engineered_features"}
        paths["report"].write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    sep = list(result.get("separation") or [])[:8]
    sub = list(result.get("subcluster_summary") or [])
    lines = [
        "# Phase545C — Feature Engineering for Hidden Loss Cluster",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Sub1 trades:** {result.get('sub1_count')}",
        f"**Recluster:** {result.get('best_method')} k={result.get('optimal_recluster_count')}",
        "",
        "## Top separation (Sub1 vs others)",
        "",
    ]
    for s in sep:
        lines.append(
            f"- `{s.get('feature')}` vs {s.get('cohort_b')}: sep={s.get('separation_score')} d={s.get('cohens_d')}"
        )
    lines.extend(["", "## New subclusters", ""])
    for s in sub:
        lines.append(
            f"- Sub{s.get('subcluster_id')} **{s.get('subcluster_label')}**: n={s.get('trade_count')} "
            f"PF={s.get('profit_factor')} PnL={s.get('total_pnl_yen_100')} MFE0={s.get('mfe0_rate')}"
        )
    lines.extend(["", "## Mandatory answers", ""])
    for k, v in ma.items():
        lines.append(f"- **{k}:** {v}")
    return "\n".join(lines) + "\n"
