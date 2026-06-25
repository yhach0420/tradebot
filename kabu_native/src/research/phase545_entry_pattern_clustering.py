"""
Phase545 — ENTRY pattern clustering & strategy attribution (research only).

Uses Phase544 entry feature dataset. No Runtime changes.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np
from sklearn.cluster import AgglomerativeClustering, DBSCAN, KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase451_entry_shape_tournament import _now_iso
from research.phase451b_entry_shape_tournament_mid_high import _v2_entry_score
from research.phase524_live_reentry_guard_and_stop_low_mfe import _is_stop_low_mfe, _num
from research.phase540_no_progress_mfe0_entry_quality import _is_mfe0, _is_no_progress, _is_winner, _mfe_pct
from research.phase541_guard_v2_full_period_validation import (
    BIG_WINNER_MFE_PCT,
    _discover_live_days,
    _load_canonical_trades_for_day,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE545_VERDICT = "phase545_entry_pattern_clustering_done"
BIG_WINNER_MFE = BIG_WINNER_MFE_PCT

CLUSTER_FEATURES: tuple[str, ...] = (
    "board_imbalance",
    "board_update_frequency",
    "update_count_before_entry",
    "volume_percentile",
    "volume_surge",
    "day_return_rank",
    "five_min_position",
    "day_high_distance_pct",
    "adx14",
    "momentum_score",
    "price_acceleration",
    "tick_speed",
    "vwap_distance_pct",
    "return_since_open",
    "entry_score_v2",
)

DATASET_FIELDS = [
    "symbol",
    "day",
    "entry_time",
    "cluster_id",
    "cluster_method",
    "cluster_label",
    "pnl_yen_100",
    "mfe_pct",
    "is_winner",
    "is_mfe0",
    "is_big_winner",
    "is_stop_low_mfe",
    "is_no_progress",
    "hold_sec",
    *CLUSTER_FEATURES,
]

SUMMARY_FIELDS = [
    "cluster_id",
    "cluster_label",
    "trade_count",
    "win_rate",
    "profit_factor",
    "total_pnl_yen_100",
    "avg_pnl_yen_100",
    "big_winner_rate",
    "mfe0_rate",
    "stop_rate",
    "no_progress_rate",
    "avg_hold_sec",
]

IMPORTANCE_FIELDS = [
    "cluster_id",
    "cluster_label",
    "feature",
    "cluster_median",
    "global_median",
    "z_score_vs_global",
    "direction",
]

PROFIT_SOURCE_FIELDS = [
    "cluster_id",
    "cluster_label",
    "total_pnl_yen_100",
    "profit_contribution_pct",
    "loss_contribution_pct",
    "net_contribution_pct",
]

SHADOW_FIELDS = [
    "cluster_id",
    "cluster_label",
    "shadow_action",
    "rationale",
    "trade_count",
    "profit_factor",
    "mfe0_rate",
    "big_winner_rate",
    "total_pnl_yen_100",
    "avg_pnl_yen_100",
]


def _hold_sec(row: Mapping[str, Any]) -> float:
    ent = _parse_ts(str(row.get("entry_time") or ""))
    ex = _parse_ts(str(row.get("exit_time") or ""))
    if ent and ex:
        return max(0.0, (ex - ent).total_seconds())
    return 0.0


def _float_or_none(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load_phase544_dataset(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append(dict(row))
    return rows


def _merge_trade_scores(repo_root: Path, rows: Sequence[Mapping[str, Any]], *, period_end: str) -> None:
    days = _discover_live_days(repo_root, start="20260616", end=period_end)
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for day in days:
        for t in _load_canonical_trades_for_day(repo_root, day, all_sessions=True):
            key = (str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
            by_key[key] = dict(t)
    for row in rows:
        key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
        trade = by_key.get(key, {})
        row["entry_score_v2"] = _v2_entry_score(trade) if trade else None
        r_open = _float_or_none(trade.get("return_since_open_pct"))
        if r_open is None:
            r_open = _float_or_none(trade.get("entry_rise_5min_pct"))
        if r_open is None and row.get("day_return_rank") not in (None, ""):
            rank = float(row["day_return_rank"])
            r_open = round((100.0 - rank) / 20.0, 4)
        row["return_since_open"] = r_open


def _augment_features(row: dict[str, Any]) -> None:
    vp = _float_or_none(row.get("volume_percentile"))
    vr = _float_or_none(row.get("volume_ratio"))
    row["volume_surge"] = (
        1.0 if vp is not None and vr is not None and vp >= 80.0 and vr >= 1.2 else 0.0
    )
    if row.get("return_since_open") in (None, ""):
        rank = _float_or_none(row.get("day_return_rank"))
        row["return_since_open"] = round((100.0 - rank) / 20.0, 4) if rank is not None else None
    if row.get("entry_score_v2") in (None, ""):
        row["entry_score_v2"] = 0.0
    row["hold_sec"] = _hold_sec(row)
    row["is_winner"] = str(row.get("is_winner", "")).lower() in ("true", "1") or _is_winner(row)
    row["is_mfe0"] = str(row.get("is_mfe0", "")).lower() in ("true", "1") or _is_mfe0(row)
    row["is_big_winner"] = str(row.get("is_big_winner", "")).lower() in ("true", "1") or (
        row["is_winner"] and _mfe_pct(row) > BIG_WINNER_MFE
    )
    row["is_stop_low_mfe"] = str(row.get("is_stop_low_mfe", "")).lower() in ("true", "1") or _is_stop_low_mfe(row)
    row["is_no_progress"] = str(row.get("is_no_progress", "")).lower() in ("true", "1") or _is_no_progress(row)


def _feature_matrix(rows: Sequence[Mapping[str, Any]]) -> tuple[np.ndarray, list[int], dict[str, float]]:
    medians: dict[str, float] = {}
    for feat in CLUSTER_FEATURES:
        vals = [_float_or_none(r.get(feat)) for r in rows]
        nums = [v for v in vals if v is not None]
        medians[feat] = statistics.median(nums) if nums else 0.0
    matrix: list[list[float]] = []
    valid_idx: list[int] = []
    for i, row in enumerate(rows):
        vec: list[float] = []
        ok = True
        for feat in CLUSTER_FEATURES:
            v = _float_or_none(row.get(feat))
            if v is None:
                v = medians[feat]
            if math.isnan(v) or math.isinf(v):
                ok = False
                break
            vec.append(v)
        if ok:
            matrix.append(vec)
            valid_idx.append(i)
    return np.array(matrix, dtype=float), valid_idx, medians


def _silhouette_safe(x: np.ndarray, labels: np.ndarray) -> Optional[float]:
    if len(set(labels)) < 2 or len(labels) < 10:
        return None
    if (labels == -1).any() and len(set(labels)) - (1 if -1 in labels else 0) < 2:
        return None
    mask = labels >= 0
    if mask.sum() < 10 or len(set(labels[mask])) < 2:
        return None
    try:
        return float(silhouette_score(x[mask], labels[mask]))
    except Exception:
        return None


def _cluster_id_val(row: Mapping[str, Any]) -> int:
    v = row.get("cluster_id")
    if v is None or v == "":
        return -1
    return int(v)


def _pick_kmeans(x: np.ndarray) -> tuple[np.ndarray, int, float]:
    scored: list[tuple[float, int, np.ndarray]] = []
    for k in range(2, min(13, len(x))):
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = km.fit_predict(x)
        sil = _silhouette_safe(x, labels)
        if sil is not None:
            scored.append((sil, k, labels))
    if not scored:
        return np.zeros(len(x), dtype=int), 2, -1.0
    best_sil = max(s for s, _, _ in scored)
    close = [item for item in scored if item[0] >= best_sil - 0.02]
    sil, best_k, best_labels = max(close, key=lambda item: item[1])
    return best_labels, best_k, sil


def _pick_hierarchical(x: np.ndarray, k: int) -> tuple[np.ndarray, float]:
    hc = AgglomerativeClustering(n_clusters=k)
    labels = hc.fit_predict(x)
    sil = _silhouette_safe(x, labels) or -1.0
    return labels, sil


def _pick_dbscan(x: np.ndarray) -> tuple[np.ndarray, float]:
    best_labels = np.full(len(x), -1, dtype=int)
    best_sil = -1.0
    for eps in (0.4, 0.6, 0.8, 1.0, 1.2, 1.5, 2.0):
        for ms in (5, 8, 12, 20):
            db = DBSCAN(eps=eps, min_samples=ms)
            labels = db.fit_predict(x)
            n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
            if n_clusters < 2:
                continue
            noise_rate = float((labels == -1).sum()) / len(labels)
            if noise_rate > 0.35:
                continue
            sil = _silhouette_safe(x, labels)
            if sil is not None and sil > best_sil:
                best_sil = sil
                best_labels = labels
    return best_labels, best_sil


def _cluster_label(centroid: Mapping[str, float], global_med: Mapping[str, float]) -> str:
    def z(feat: str) -> float:
        g = global_med.get(feat) or 0.0
        c = centroid.get(feat) or 0.0
        denom = abs(g) if abs(g) > 1e-6 else 1.0
        return (c - g) / denom

    if z("minutes_from_open") < -0.25 and z("volume_percentile") > 0.15 and z("five_min_position") < 0:
        return "初動型"
    if z("update_count_before_entry") > 0.2 and z("board_imbalance") > 0.05 and z("volume_surge") > 0.1:
        return "ブレイクアウト型"
    if z("adx14") > 0.15 and z("five_min_position") > 0.15 and z("board_imbalance") < 0:
        return "遅延追いかけ型"
    if z("vwap_distance_pct") < -0.2 or z("day_high_distance_pct") < -0.2:
        return "リバウンド型"
    if z("momentum_score") < -0.1 and z("price_acceleration") < 0:
        return "ダマシ型"
    if z("board_imbalance") > 0.1 and z("volume_percentile") > 0.1:
        return "強度型"
    return "混合型"


def _centroid(rows: Sequence[Mapping[str, Any]], feats: Sequence[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for feat in feats:
        vals = [_float_or_none(r.get(feat)) for r in rows]
        nums = [v for v in vals if v is not None]
        out[feat] = round(statistics.median(nums), 6) if nums else 0.0
    mins = [_float_or_none(r.get("minutes_from_open")) for r in rows]
    mins_n = [v for v in mins if v is not None]
    out["minutes_from_open"] = round(statistics.median(mins_n), 6) if mins_n else 0.0
    return out


def _cluster_summary(rows: Sequence[Mapping[str, Any]], labels: Mapping[int, str]) -> list[dict[str, Any]]:
    by_cluster: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        cid = _cluster_id_val(r)
        by_cluster.setdefault(cid, []).append(dict(r))
    out: list[dict[str, Any]] = []
    for cid in sorted(by_cluster):
        items = by_cluster[cid]
        pnls = [_num(t.get("pnl_yen_100")) for t in items]
        wins = sum(1 for t in items if t.get("is_winner"))
        holds = [_num(t.get("hold_sec")) for t in items]
        n = len(items)
        out.append(
            {
                "cluster_id": cid,
                "cluster_label": labels.get(cid, "未分類"),
                "trade_count": n,
                "win_rate": round(wins / n, 4) if n else 0.0,
                "profit_factor": _pf(pnls),
                "total_pnl_yen_100": round(sum(pnls), 2),
                "avg_pnl_yen_100": round(sum(pnls) / n, 2) if n else 0.0,
                "big_winner_rate": round(sum(1 for t in items if t.get("is_big_winner")) / n, 4) if n else 0.0,
                "mfe0_rate": round(sum(1 for t in items if t.get("is_mfe0")) / n, 4) if n else 0.0,
                "stop_rate": round(sum(1 for t in items if t.get("is_stop_low_mfe")) / n, 4) if n else 0.0,
                "no_progress_rate": round(sum(1 for t in items if t.get("is_no_progress")) / n, 4) if n else 0.0,
                "avg_hold_sec": round(statistics.mean(holds), 1) if holds else 0.0,
            }
        )
    return out


def _cluster_importance(
    rows: Sequence[Mapping[str, Any]],
    labels: Mapping[int, str],
    global_med: Mapping[str, float],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    all_feats = list(CLUSTER_FEATURES) + ["minutes_from_open"]
    by_cluster: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by_cluster.setdefault(_cluster_id_val(r), []).append(dict(r))
    for cid, items in sorted(by_cluster.items()):
        cent = _centroid(items, all_feats)
        scored: list[tuple[float, str, str]] = []
        for feat in all_feats:
            g = global_med.get(feat, cent.get(feat, 0.0))
            c = cent.get(feat, 0.0)
            denom = abs(g) if abs(g) > 1e-6 else 1.0
            z = (c - g) / denom
            direction = "high" if z > 0.05 else ("low" if z < -0.05 else "neutral")
            scored.append((abs(z), feat, direction))
        scored.sort(reverse=True)
        for _, feat, direction in scored[:6]:
            out.append(
                {
                    "cluster_id": cid,
                    "cluster_label": labels.get(cid, "未分類"),
                    "feature": feat,
                    "cluster_median": cent.get(feat),
                    "global_median": round(global_med.get(feat, 0.0), 6),
                    "z_score_vs_global": round((cent.get(feat, 0.0) - global_med.get(feat, 0.0)) / (abs(global_med.get(feat, 0.0)) or 1.0), 4),
                    "direction": direction,
                }
            )
    return out


def _profit_source(summary: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pos_total = sum(_num(s.get("total_pnl_yen_100")) for s in summary if _num(s.get("total_pnl_yen_100")) > 0)
    neg_total = sum(_num(s.get("total_pnl_yen_100")) for s in summary if _num(s.get("total_pnl_yen_100")) < 0)
    net_total = sum(_num(s.get("total_pnl_yen_100")) for s in summary)
    out: list[dict[str, Any]] = []
    for s in summary:
        pnl = _num(s.get("total_pnl_yen_100"))
        out.append(
            {
                "cluster_id": s.get("cluster_id"),
                "cluster_label": s.get("cluster_label"),
                "total_pnl_yen_100": pnl,
                "profit_contribution_pct": round(pnl / pos_total, 4) if pnl > 0 and pos_total else 0.0,
                "loss_contribution_pct": round(abs(pnl) / abs(neg_total), 4) if pnl < 0 and neg_total else 0.0,
                "net_contribution_pct": round(pnl / net_total, 4) if net_total else 0.0,
            }
        )
    out.sort(key=lambda r: _num(r.get("total_pnl_yen_100")), reverse=True)
    return out


def _shadow_candidates(summary: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in summary:
        cid = s.get("cluster_id")
        pf = _num(s.get("profit_factor"))
        mfe0 = _num(s.get("mfe0_rate"))
        big = _num(s.get("big_winner_rate"))
        pnl = _num(s.get("total_pnl_yen_100"))
        action = "hold"
        rationale = "neutral"
        if pnl < 0 and (mfe0 >= 0.35 or pf < 0.85):
            action = "shadow_reject"
            rationale = "loss_cluster_high_mfe0_or_low_pf"
        elif pnl > 0 and pf >= 1.15 and big >= 0.15:
            action = "shadow_bonus"
            rationale = "profit_cluster_strong_pf_big_winner"
        elif mfe0 >= 0.45:
            action = "shadow_reject"
            rationale = "mfe0_dominant_cluster"
        out.append(
            {
                "cluster_id": cid,
                "cluster_label": s.get("cluster_label"),
                "shadow_action": action,
                "rationale": rationale,
                "trade_count": s.get("trade_count"),
                "profit_factor": pf,
                "mfe0_rate": mfe0,
                "big_winner_rate": big,
                "total_pnl_yen_100": pnl,
                "avg_pnl_yen_100": s.get("avg_pnl_yen_100"),
            }
        )
    return out


def _mandatory_answers(
    *,
    best_k: int,
    best_method: str,
    summary: Sequence[Mapping[str, Any]],
    labels: Mapping[int, str],
    importance: Sequence[Mapping[str, Any]],
    profit_source: Sequence[Mapping[str, Any]],
    shadow: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    profit_top = max(summary, key=lambda s: _num(s.get("total_pnl_yen_100")), default={})
    loss_top = min(summary, key=lambda s: _num(s.get("total_pnl_yen_100")), default={})
    reject = [s for s in shadow if s.get("shadow_action") == "shadow_reject"]
    bonus = [s for s in shadow if s.get("shadow_action") == "shadow_bonus"]
    cluster_traits = {
        int(s["cluster_id"]): {
            "label": s.get("cluster_label"),
            "top_features": [
                r.get("feature")
                for r in importance
                if _cluster_id_val(r) == _cluster_id_val(s)
            ][:4],
        }
        for s in summary
    }
    return {
        "1_optimal_cluster_count": best_k,
        "1_best_method": best_method,
        "2_cluster_traits": cluster_traits,
        "3_cluster_pf": {int(s["cluster_id"]): s.get("profit_factor") for s in summary},
        "4_cluster_pnl": {int(s["cluster_id"]): s.get("total_pnl_yen_100") for s in summary},
        "5_cluster_big_winner_rate": {int(s["cluster_id"]): s.get("big_winner_rate") for s in summary},
        "6_cluster_mfe0_rate": {int(s["cluster_id"]): s.get("mfe0_rate") for s in summary},
        "7_profit_source_cluster": f"{profit_top.get('cluster_id')} ({profit_top.get('cluster_label')})",
        "8_loss_source_cluster": f"{loss_top.get('cluster_id')} ({loss_top.get('cluster_label')})",
        "9_shadow_reject_clusters": [f"{s.get('cluster_id')}:{s.get('cluster_label')}" for s in reject],
        "10_shadow_bonus_clusters": [f"{s.get('cluster_id')}:{s.get('cluster_label')}" for s in bonus],
        "11_runtime_adopt": False,
        "12_next_phase": "phase546_entry_cluster_shadow_replay",
    }


@dataclass
class Phase545Job:
    repo_root: Path
    dataset_path: Optional[Path] = None
    period_end: str = "20260625"

    def run(self) -> dict[str, Any]:
        repo_root = self.repo_root.resolve()
        reports = resolve_reports_dir(repo_root)
        ds_path = self.dataset_path or (reports / "phase544_entry_feature_dataset.csv")
        rows = _load_phase544_dataset(ds_path)
        _merge_trade_scores(repo_root, rows, period_end=self.period_end)
        for row in rows:
            _augment_features(row)

        x, valid_idx, medians = _feature_matrix(rows)
        scaler = StandardScaler()
        xs = scaler.fit_transform(x)

        km_labels, best_k, km_sil = _pick_kmeans(xs)
        hc_labels, hc_sil = _pick_hierarchical(xs, best_k)
        db_labels, db_sil = _pick_dbscan(xs)

        candidates = [
            ("kmeans", km_labels, km_sil - 0.0, best_k),
            ("hierarchical", hc_labels, hc_sil - 0.0, best_k),
            (
                "dbscan",
                db_labels,
                (db_sil if db_sil > 0 else -1.0)
                - float((db_labels == -1).sum()) / len(db_labels) * 0.2,
                len(set(db_labels)) - (1 if -1 in db_labels else 0),
            ),
        ]
        best_method, final_labels, best_sil, opt_k = max(candidates, key=lambda c: c[2])

        global_med = _centroid(rows, list(CLUSTER_FEATURES) + ["minutes_from_open"])
        label_map: dict[int, str] = {}
        clustered_rows: list[dict[str, Any]] = []
        for arr_i, row_i in enumerate(valid_idx):
            row = dict(rows[row_i])
            cid = int(final_labels[arr_i])
            row["cluster_id"] = cid
            row["cluster_method"] = best_method
            clustered_rows.append(row)

        by_c: dict[int, list[dict[str, Any]]] = {}
        for r in clustered_rows:
            by_c.setdefault(int(r["cluster_id"]), []).append(r)
        for cid, items in by_c.items():
            label_map[cid] = _cluster_label(_centroid(items, list(CLUSTER_FEATURES)), global_med)
        for r in clustered_rows:
            r["cluster_label"] = label_map.get(int(r["cluster_id"]), "未分類")

        summary = _cluster_summary(clustered_rows, label_map)
        importance = _cluster_importance(clustered_rows, label_map, global_med)
        profit_src = _profit_source(summary)
        shadow = _shadow_candidates(summary)
        answers = _mandatory_answers(
            best_k=opt_k,
            best_method=best_method,
            summary=summary,
            labels=label_map,
            importance=importance,
            profit_source=profit_src,
            shadow=shadow,
        )

        return {
            "verdict": PHASE545_VERDICT,
            "generated_at": _now_iso(),
            "trade_count": len(clustered_rows),
            "valid_for_clustering": len(valid_idx),
            "best_method": best_method,
            "optimal_cluster_count": opt_k,
            "silhouette_score": round(best_sil, 4),
            "method_comparison": {
                "kmeans": {"k": best_k, "silhouette": round(km_sil, 4)},
                "hierarchical": {"k": best_k, "silhouette": round(hc_sil, 4)},
                "dbscan": {"k": len(set(db_labels)) - (1 if -1 in db_labels else 0), "silhouette": round(db_sil, 4)},
            },
            "cluster_dataset": clustered_rows,
            "cluster_summary": summary,
            "cluster_importance": importance,
            "profit_source": profit_src,
            "shadow_candidates": shadow,
            "mandatory_answers": answers,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "dataset": reports / "phase545_cluster_dataset.csv",
            "summary": reports / "phase545_cluster_summary.csv",
            "importance": reports / "phase545_cluster_importance.csv",
            "profit_source": reports / "phase545_cluster_profit_source.csv",
            "shadow": reports / "phase545_cluster_shadow_candidates.csv",
            "report": reports / "phase545_report.json",
            "docs": kabu / "docs" / "operations" / "phase545_entry_pattern_clustering.md",
        }
        _write_csv(paths["dataset"], DATASET_FIELDS, list(result.get("cluster_dataset") or []))
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("cluster_summary") or []))
        _write_csv(paths["importance"], IMPORTANCE_FIELDS, list(result.get("cluster_importance") or []))
        _write_csv(paths["profit_source"], PROFIT_SOURCE_FIELDS, list(result.get("profit_source") or []))
        _write_csv(paths["shadow"], SHADOW_FIELDS, list(result.get("shadow_candidates") or []))
        public = {k: v for k, v in result.items() if k != "cluster_dataset"}
        paths["report"].write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    summary = list(result.get("cluster_summary") or [])
    lines = [
        "# Phase545 — ENTRY Pattern Clustering",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Best method:** {result.get('best_method')} (k={result.get('optimal_cluster_count')}, silhouette={result.get('silhouette_score')})",
        f"**Trades clustered:** {result.get('trade_count')}",
        "",
        "## Cluster summary",
        "",
    ]
    for s in summary:
        lines.append(
            f"- Cluster {s.get('cluster_id')} **{s.get('cluster_label')}**: "
            f"trades={s.get('trade_count')} PF={s.get('profit_factor')} PnL={s.get('total_pnl_yen_100')} "
            f"big_win={s.get('big_winner_rate')} mfe0={s.get('mfe0_rate')}"
        )
    lines.extend(["", "## Mandatory answers", ""])
    for k, v in ma.items():
        lines.append(f"- **{k}:** {v}")
    return "\n".join(lines) + "\n"
