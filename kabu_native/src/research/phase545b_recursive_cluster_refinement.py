"""
Phase545B — Recursive cluster refinement on Phase545 Cluster3 only (research only).

No Runtime changes.
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
from sklearn.metrics import calinski_harabasz_score, davies_bouldin_score, silhouette_score
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

from research.market_sector_heat import _pf, _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase524_live_reentry_guard_and_stop_low_mfe import _num
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE545B_VERDICT = "phase545b_recursive_cluster_refinement_done"
PARENT_CLUSTER_ID = 3

REFINEMENT_FEATURES: tuple[str, ...] = (
    "board_imbalance",
    "board_update_frequency",
    "update_count_before_entry",
    "volume_percentile",
    "volume_ratio",
    "volume_surge",
    "day_return_rank",
    "return_since_open",
    "five_min_position",
    "day_high_distance_pct",
    "adx14",
    "momentum_score",
    "price_acceleration",
    "tick_speed",
    "vwap_distance_pct",
    "high_update_recent",
    "pullback_after_spike",
    "minutes_from_open",
    "trend_direction_enc",
)

DATASET_FIELDS = [
    "symbol",
    "day",
    "entry_time",
    "parent_cluster_id",
    "subcluster_id",
    "subcluster_method",
    "subcluster_label",
    "classification",
    "pnl_yen_100",
    "mfe_pct",
    "is_winner",
    "is_mfe0",
    "is_big_winner",
    "is_stop_low_mfe",
    "is_no_progress",
    "hold_sec",
    *REFINEMENT_FEATURES,
]

SUMMARY_FIELDS = [
    "subcluster_id",
    "subcluster_label",
    "classification",
    "trade_count",
    "win_rate",
    "profit_factor",
    "total_pnl_yen_100",
    "avg_pnl_yen_100",
    "mfe0_count",
    "mfe0_rate",
    "stop_rate",
    "no_progress_rate",
    "big_winner_count",
    "big_winner_rate",
    "avg_hold_sec",
    "loss_contribution_pct",
    "profit_contribution_pct",
]

IMPORTANCE_FIELDS = [
    "subcluster_id",
    "subcluster_label",
    "feature",
    "cluster_median",
    "global_median",
    "z_score_vs_global",
    "rank",
]

SHADOW_FIELDS = [
    "subcluster_id",
    "subcluster_label",
    "shadow_action",
    "rationale",
    "trade_count",
    "profit_factor",
    "mfe0_rate",
    "big_winner_rate",
    "total_pnl_yen_100",
]

METHOD_COMPARE_FIELDS = [
    "method",
    "k",
    "silhouette",
    "davies_bouldin",
    "calinski_harabasz",
    "composite_score",
    "selected",
]


def _float_or_none(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bool_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return 1.0 if str(v).lower() in ("true", "1", "yes") else 0.0


def _trend_enc(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    s = str(v).lower()
    if s == "up":
        return 1.0
    if s == "down":
        return -1.0
    if s == "sideways":
        return 0.0
    return None


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")


def _load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def _merge_phase544(rows: Sequence[Mapping[str, Any]], p544: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_key = {(str(r.get("symbol") or ""), str(r.get("entry_time") or "")): dict(r) for r in p544}
    out: list[dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        ext = by_key.get((str(r.get("symbol") or ""), str(r.get("entry_time") or "")), {})
        for k in (
            "volume_ratio",
            "minutes_from_open",
            "high_update_recent",
            "pullback_after_spike",
            "trend_direction",
            "exit_time",
        ):
            if r.get(k) in (None, "") and ext.get(k) not in (None, ""):
                r[k] = ext.get(k)
        if r.get("volume_surge") in (None, ""):
            vp = _float_or_none(r.get("volume_percentile"))
            vr = _float_or_none(r.get("volume_ratio"))
            r["volume_surge"] = 1.0 if vp is not None and vr is not None and vp >= 80 and vr >= 1.2 else 0.0
        r["trend_direction_enc"] = _trend_enc(r.get("trend_direction"))
        r["high_update_recent"] = _bool_float(r.get("high_update_recent"))
        r["pullback_after_spike"] = _bool_float(r.get("pullback_after_spike"))
        r["is_winner"] = _as_bool(r.get("is_winner"))
        r["is_mfe0"] = _as_bool(r.get("is_mfe0"))
        r["is_big_winner"] = _as_bool(r.get("is_big_winner"))
        r["is_stop_low_mfe"] = _as_bool(r.get("is_stop_low_mfe"))
        r["is_no_progress"] = _as_bool(r.get("is_no_progress"))
        r["parent_cluster_id"] = PARENT_CLUSTER_ID
        out.append(r)
    return out


def _feature_matrix(rows: Sequence[Mapping[str, Any]]) -> tuple[np.ndarray, list[int], dict[str, float]]:
    medians: dict[str, float] = {}
    for feat in REFINEMENT_FEATURES:
        vals = [_float_or_none(r.get(feat)) for r in rows]
        nums = [v for v in vals if v is not None]
        medians[feat] = statistics.median(nums) if nums else 0.0
    matrix: list[list[float]] = []
    valid_idx: list[int] = []
    for i, row in enumerate(rows):
        vec: list[float] = []
        ok = True
        for feat in REFINEMENT_FEATURES:
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


def _metrics(x: np.ndarray, labels: np.ndarray) -> dict[str, Optional[float]]:
    if len(set(labels)) < 2:
        return {"silhouette": None, "davies_bouldin": None, "calinski_harabasz": None}
    try:
        return {
            "silhouette": float(silhouette_score(x, labels)),
            "davies_bouldin": float(davies_bouldin_score(x, labels)),
            "calinski_harabasz": float(calinski_harabasz_score(x, labels)),
        }
    except Exception:
        return {"silhouette": None, "davies_bouldin": None, "calinski_harabasz": None}


def _composite(m: Mapping[str, Optional[float]]) -> float:
    sil = m.get("silhouette")
    db = m.get("davies_bouldin")
    ch = m.get("calinski_harabasz")
    if sil is None or db is None or ch is None:
        return -1e9
    return sil * 0.45 + (1.0 / (1.0 + db)) * 0.30 + min(ch / 500.0, 1.0) * 0.25


def _fit_labels(method: str, x: np.ndarray, k: int) -> np.ndarray:
    if method == "kmeans":
        return KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(x)
    if method == "hierarchical":
        return AgglomerativeClustering(n_clusters=k).fit_predict(x)
    if method == "gmm":
        return GaussianMixture(n_components=k, random_state=42, n_init=3).fit_predict(x)
    db = DBSCAN(eps=0.9, min_samples=12).fit_predict(x)
    return db


def _search_methods(x: np.ndarray) -> tuple[str, int, np.ndarray, list[dict[str, Any]]]:
    compare: list[dict[str, Any]] = []
    scored: list[tuple[float, str, int, np.ndarray]] = []
    for method in ("kmeans", "hierarchical", "gmm"):
        for k in range(2, min(9, len(x))):
            labels = _fit_labels(method, x, k)
            if len(set(labels)) < 2:
                continue
            m = _metrics(x, labels)
            comp = _composite(m)
            compare.append(
                {
                    "method": method,
                    "k": k,
                    "silhouette": round(m["silhouette"], 4) if m["silhouette"] is not None else None,
                    "davies_bouldin": round(m["davies_bouldin"], 4) if m["davies_bouldin"] is not None else None,
                    "calinski_harabasz": round(m["calinski_harabasz"], 2) if m["calinski_harabasz"] is not None else None,
                    "composite_score": round(comp, 4),
                    "selected": False,
                }
            )
            scored.append((comp, method, k, labels))
    if not scored:
        return "kmeans", 2, np.zeros(len(x), dtype=int), compare
    best_comp = max(s[0] for s in scored)
    close = [s for s in scored if s[0] >= best_comp - 0.03]
    _, method, best_k, labels = max(close, key=lambda s: s[2])
    for row in compare:
        if row["method"] == method and row["k"] == best_k:
            row["selected"] = True
    db_labels = _fit_labels("dbscan", x, 0)
    n_cl = len(set(db_labels)) - (1 if -1 in db_labels else 0)
    if n_cl >= 2:
        m = _metrics(x, db_labels[db_labels >= 0] if (db_labels >= 0).sum() > 10 else db_labels)
        if (db_labels >= 0).sum() > 10 and len(set(db_labels[db_labels >= 0])) >= 2:
            m = _metrics(x[db_labels >= 0], db_labels[db_labels >= 0])
            compare.append(
                {
                    "method": "dbscan",
                    "k": n_cl,
                    "silhouette": round(m["silhouette"], 4) if m.get("silhouette") else None,
                    "davies_bouldin": round(m["davies_bouldin"], 4) if m.get("davies_bouldin") else None,
                    "calinski_harabasz": round(m["calinski_harabasz"], 2) if m.get("calinski_harabasz") else None,
                    "composite_score": round(_composite(m), 4) if m.get("silhouette") else None,
                    "selected": False,
                }
            )
    compare.sort(key=lambda r: _num(r.get("composite_score")), reverse=True)
    return method, best_k, labels, compare


def _centroid(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for feat in REFINEMENT_FEATURES:
        vals = [_float_or_none(r.get(feat)) for r in rows]
        nums = [v for v in vals if v is not None]
        out[feat] = round(statistics.median(nums), 6) if nums else 0.0
    return out


def _subcluster_label(centroid: Mapping[str, float], global_med: Mapping[str, float]) -> str:
    def z(f: str) -> float:
        g = global_med.get(f) or 0.0
        c = centroid.get(f) or 0.0
        denom = abs(g) if abs(g) > 1e-6 else 1.0
        return (c - g) / denom

    if z("adx14") > 0.12 and z("five_min_position") > 0.12 and z("volume_percentile") < 0:
        return "遅延追いかけ"
    if z("board_imbalance") < -0.05 and z("update_count_before_entry") < -0.1:
        return "モメンタム枯渇"
    if z("volume_surge") > 0.15 and z("board_imbalance") > 0.05:
        return "初動ブレイク"
    if z("minutes_from_open") > 0.2 and z("day_return_rank") < -0.1:
        return "後場ダマシ"
    if z("vwap_distance_pct") > 0.15:
        return "VWAP乖離追い"
    if z("momentum_score") < -0.1:
        return "弱モメンタム"
    return "混合サブ型"


def _classify_subcluster(summary: Mapping[str, Any]) -> str:
    pnl = _num(summary.get("total_pnl_yen_100"))
    pf = _num(summary.get("profit_factor"))
    mfe0 = _num(summary.get("mfe0_rate"))
    n = int(summary.get("trade_count") or 0)
    if pnl < 0 and pf < 0.9 and mfe0 >= 0.3:
        return "reject_candidate"
    if pnl > 0 and pf >= 1.1 and _num(summary.get("big_winner_rate")) >= 0.15:
        return "bonus_candidate"
    if n < 30:
        return "research_continue"
    return "research_continue"


def _subcluster_summary(
    rows: Sequence[Mapping[str, Any]],
    labels: Mapping[int, str],
) -> list[dict[str, Any]]:
    by: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        sid = int(r.get("subcluster_id") or 0)
        by.setdefault(sid, []).append(dict(r))
    pos_pool = sum(_num(r.get("pnl_yen_100")) for r in rows if _num(r.get("pnl_yen_100")) > 0)
    loss_pool = sum(abs(_num(r.get("pnl_yen_100"))) for r in rows if _num(r.get("pnl_yen_100")) < 0)
    out: list[dict[str, Any]] = []
    for sid in sorted(by):
        items = by[sid]
        pnls = [_num(t.get("pnl_yen_100")) for t in items]
        total = round(sum(pnls), 2)
        n = len(items)
        sub_loss = sum(abs(_num(t.get("pnl_yen_100"))) for t in items if _num(t.get("pnl_yen_100")) < 0)
        sub_profit = sum(_num(t.get("pnl_yen_100")) for t in items if _num(t.get("pnl_yen_100")) > 0)
        row = {
            "subcluster_id": sid,
            "subcluster_label": labels.get(sid, "未分類"),
            "trade_count": n,
            "win_rate": round(sum(1 for t in items if _as_bool(t.get("is_winner"))) / n, 4) if n else 0.0,
            "profit_factor": _pf(pnls),
            "total_pnl_yen_100": total,
            "avg_pnl_yen_100": round(total / n, 2) if n else 0.0,
            "mfe0_count": sum(1 for t in items if _as_bool(t.get("is_mfe0"))),
            "mfe0_rate": round(sum(1 for t in items if _as_bool(t.get("is_mfe0"))) / n, 4) if n else 0.0,
            "stop_rate": round(sum(1 for t in items if _as_bool(t.get("is_stop_low_mfe"))) / n, 4) if n else 0.0,
            "no_progress_rate": round(sum(1 for t in items if _as_bool(t.get("is_no_progress"))) / n, 4) if n else 0.0,
            "big_winner_count": sum(1 for t in items if _as_bool(t.get("is_big_winner"))),
            "big_winner_rate": round(sum(1 for t in items if _as_bool(t.get("is_big_winner"))) / n, 4) if n else 0.0,
            "avg_hold_sec": round(
                statistics.mean(_num(t.get("hold_sec")) for t in items), 1
            )
            if items
            else 0.0,
            "loss_contribution_pct": round(sub_loss / loss_pool, 4) if loss_pool else 0.0,
            "profit_contribution_pct": round(sub_profit / pos_pool, 4) if pos_pool else 0.0,
        }
        row["classification"] = _classify_subcluster(row)
        out.append(row)
    return out


def _importance_rows(
    rows: Sequence[Mapping[str, Any]],
    labels: Mapping[int, str],
    global_med: Mapping[str, float],
) -> list[dict[str, Any]]:
    by: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(int(r.get("subcluster_id") or 0), []).append(dict(r))
    out: list[dict[str, Any]] = []
    for sid, items in sorted(by.items()):
        cent = _centroid(items)
        ranked = sorted(
            REFINEMENT_FEATURES,
            key=lambda f: abs((cent.get(f, 0.0) - global_med.get(f, 0.0)) / (abs(global_med.get(f, 0.0)) or 1.0)),
            reverse=True,
        )
        for rank, feat in enumerate(ranked[:8], start=1):
            g = global_med.get(feat, 0.0)
            c = cent.get(feat, 0.0)
            out.append(
                {
                    "subcluster_id": sid,
                    "subcluster_label": labels.get(sid, ""),
                    "feature": feat,
                    "cluster_median": c,
                    "global_median": round(g, 6),
                    "z_score_vs_global": round((c - g) / (abs(g) or 1.0), 4),
                    "rank": rank,
                }
            )
    return out


def _shadow_rows(summary: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in summary:
        cls = str(s.get("classification") or "")
        pnl = _num(s.get("total_pnl_yen_100"))
        pf = _num(s.get("profit_factor"))
        mfe0 = _num(s.get("mfe0_rate"))
        big = _num(s.get("big_winner_rate"))
        if cls == "reject_candidate":
            action, why = "shadow_reject", "loss_subcluster_low_pf_high_mfe0"
        elif cls == "bonus_candidate":
            action, why = "shadow_bonus", "profit_subcluster_strong_pf_big_winner"
        elif pnl > 0:
            action, why = "shadow_hold", "profit_neutral_monitor"
        else:
            action, why = "shadow_hold", "needs_more_research"
        if mfe0 >= 0.45 and pnl < 0:
            action, why = "shadow_reject", "mfe0_dominant_loss_subcluster"
        out.append(
            {
                "subcluster_id": s.get("subcluster_id"),
                "subcluster_label": s.get("subcluster_label"),
                "shadow_action": action,
                "rationale": why,
                "trade_count": s.get("trade_count"),
                "profit_factor": pf,
                "mfe0_rate": mfe0,
                "big_winner_rate": big,
                "total_pnl_yen_100": pnl,
            }
        )
    return out


def _mandatory_answers(
    *,
    best_k: int,
    summary: Sequence[Mapping[str, Any]],
    shadow: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not summary:
        return {}
    loss_top = min(summary, key=lambda s: _num(s.get("total_pnl_yen_100")))
    profit_top = max(summary, key=lambda s: _num(s.get("total_pnl_yen_100")))
    mfe0_top = max(summary, key=lambda s: _num(s.get("mfe0_rate")))
    big_top = max(summary, key=lambda s: _num(s.get("big_winner_rate")))
    reject = [s for s in shadow if s.get("shadow_action") == "shadow_reject"]
    bonus = [s for s in shadow if s.get("shadow_action") == "shadow_bonus"]
    return {
        "1_optimal_recluster_count": best_k,
        "2_cluster3_split_count": len(summary),
        "3_max_loss_subcluster": f"{loss_top.get('subcluster_id')}:{loss_top.get('subcluster_label')} ({loss_top.get('total_pnl_yen_100')})",
        "4_max_profit_subcluster": f"{profit_top.get('subcluster_id')}:{profit_top.get('subcluster_label')} ({profit_top.get('total_pnl_yen_100')})",
        "5_max_mfe0_subcluster": f"{mfe0_top.get('subcluster_id')}:{mfe0_top.get('subcluster_label')} (mfe0={mfe0_top.get('mfe0_rate')})",
        "6_max_big_winner_subcluster": f"{big_top.get('subcluster_id')}:{big_top.get('subcluster_label')} (big={big_top.get('big_winner_rate')})",
        "7_shadow_reject_candidates": [f"{s.get('subcluster_id')}:{s.get('subcluster_label')}" for s in reject],
        "8_shadow_bonus_candidates": [f"{s.get('subcluster_id')}:{s.get('subcluster_label')}" for s in bonus],
        "9_runtime_adopt": False,
        "10_next_phase": "phase546_entry_cluster_shadow_replay",
        "subcluster_summary": {int(s["subcluster_id"]): s for s in summary},
    }


@dataclass
class Phase545BJob:
    repo_root: Path
    cluster_dataset: Optional[Path] = None
    phase544_dataset: Optional[Path] = None

    def run(self) -> dict[str, Any]:
        reports = resolve_reports_dir(self.repo_root)
        c_path = self.cluster_dataset or (reports / "phase545_cluster_dataset.csv")
        p_path = self.phase544_dataset or (reports / "phase544_entry_feature_dataset.csv")
        all_rows = _load_csv(c_path)
        p544 = _load_csv(p_path)
        c3 = [r for r in all_rows if int(r.get("cluster_id") or -1) == PARENT_CLUSTER_ID]
        rows = _merge_phase544(c3, p544)

        x, valid_idx, medians = _feature_matrix(rows)
        xs = StandardScaler().fit_transform(x)
        method, best_k, labels, method_compare = _search_methods(xs)

        label_map: dict[int, str] = {}
        clustered: list[dict[str, Any]] = []
        for i, row_i in enumerate(valid_idx):
            r = dict(rows[row_i])
            sid = int(labels[i])
            r["subcluster_id"] = sid
            r["subcluster_method"] = method
            clustered.append(r)
        by: dict[int, list[dict[str, Any]]] = {}
        for r in clustered:
            by.setdefault(int(r["subcluster_id"]), []).append(r)
        global_med = _centroid(clustered)
        for sid, items in by.items():
            label_map[sid] = _subcluster_label(_centroid(items), global_med)
        summary = _subcluster_summary(clustered, label_map)
        cls_map = {int(s["subcluster_id"]): str(s["classification"]) for s in summary}
        for r in clustered:
            r["subcluster_label"] = label_map.get(int(r["subcluster_id"]), "未分類")
            r["classification"] = cls_map.get(int(r["subcluster_id"]), "research_continue")
        importance = _importance_rows(clustered, label_map, global_med)
        shadow = _shadow_rows(summary)
        answers = _mandatory_answers(best_k=best_k, summary=summary, shadow=shadow)

        return {
            "verdict": PHASE545B_VERDICT,
            "generated_at": _now_iso(),
            "parent_cluster_id": PARENT_CLUSTER_ID,
            "input_trade_count": len(c3),
            "clustered_trade_count": len(clustered),
            "best_method": method,
            "optimal_recluster_count": best_k,
            "method_compare": method_compare,
            "cluster_dataset": clustered,
            "cluster_summary": summary,
            "cluster_importance": importance,
            "shadow_candidates": shadow,
            "mandatory_answers": answers,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "dataset": reports / "phase545b_cluster3_dataset.csv",
            "summary": reports / "phase545b_cluster3_summary.csv",
            "importance": reports / "phase545b_cluster3_importance.csv",
            "shadow": reports / "phase545b_cluster3_shadow_candidates.csv",
            "method_compare": reports / "phase545b_cluster3_method_compare.csv",
            "report": reports / "phase545b_report.json",
            "docs": kabu / "docs" / "operations" / "phase545b_recursive_cluster_refinement.md",
        }
        _write_csv(paths["dataset"], DATASET_FIELDS, list(result.get("cluster_dataset") or []))
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("cluster_summary") or []))
        _write_csv(paths["importance"], IMPORTANCE_FIELDS, list(result.get("cluster_importance") or []))
        _write_csv(paths["shadow"], SHADOW_FIELDS, list(result.get("shadow_candidates") or []))
        _write_csv(paths["method_compare"], METHOD_COMPARE_FIELDS, list(result.get("method_compare") or []))
        public = {k: v for k, v in result.items() if k != "cluster_dataset"}
        paths["report"].write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    summary = list(result.get("cluster_summary") or [])
    lines = [
        "# Phase545B — Recursive Cluster Refinement (Cluster3)",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Parent:** Cluster {result.get('parent_cluster_id')} ({result.get('input_trade_count')} trades)",
        f"**Method:** {result.get('best_method')} k={result.get('optimal_recluster_count')}",
        "",
        "## Subcluster summary",
        "",
    ]
    for s in summary:
        lines.append(
            f"- Sub{s.get('subcluster_id')} **{s.get('subcluster_label')}** [{s.get('classification')}]: "
            f"n={s.get('trade_count')} PF={s.get('profit_factor')} PnL={s.get('total_pnl_yen_100')} "
            f"MFE0={s.get('mfe0_rate')} loss_pct={s.get('loss_contribution_pct')}"
        )
    lines.extend(["", "## Mandatory answers", ""])
    for k, v in ma.items():
        if k != "subcluster_summary":
            lines.append(f"- **{k}:** {v}")
    return "\n".join(lines) + "\n"
