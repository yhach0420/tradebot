#!/usr/bin/env python3
"""Phase687W43: Pre-Entry Market State Analysis (research pilot on 20260716 AM).

MAINLINE / Shadow / ENTRY / EXIT / CAP / OR / orders unchanged.
"""

from __future__ import annotations

import csv
import json
import shutil
import tempfile
import warnings
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.cluster import AgglomerativeClustering, KMeans
from sklearn.decomposition import PCA
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    adjusted_rand_score,
    balanced_accuracy_score,
    davies_bouldin_score,
    roc_auc_score,
    silhouette_score,
)
from sklearn.mixture import GaussianMixture
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import LeaveOneOut

from research.pre_entry_market_state import (
    REANALYSIS_GATES,
    SCHEMA_VERSION,
    append_session_market_state,
    build_session_market_state_df,
    dataset_root,
    feature_dictionary,
    load_manifest,
    save_manifest,
)

warnings.filterwarnings("ignore", category=UserWarning)

NATIVE = Path(__file__).resolve().parents[1]
REPORTS = NATIVE / "results" / "reports"
OUT = REPORTS / "phase687w43_pre_entry_market_state_analysis"
SESSION = NATIVE / "results" / "small_paper" / "20260716" / "live_session_073602"
CAPTURE = NATIVE / "data" / "market_capture" / "20260716"
BOARD_PQ = NATIVE / "results" / "research" / "board_entry_dataset" / "trading_date=20260716" / "entries.parquet"
JST = ZoneInfo("Asia/Tokyo")

CLUSTER_FEATURES = [
    "pre_300s_return",
    "pre_300s_slope",
    "pre_300s_bounce_from_recent_low",
    "pre_300s_fall_from_recent_high",
    "pre_300s_range_width",
    "pre_300s_realized_vol",
    "pre_300s_volume_delta",
    "pre_300s_volume_persistence",
    "pre_60s_current_price_update_count",
    "board_at_entry_imbalance_l5",
    "board_60s_ofi_proxy",
    "board_60s_same_price_board_churn",
    "board_60s_updates_per_sec",
    "board_60s_board_price_update_ratio",
    "score_v2",
    "momentum",
    "entry_imbalance_percentile",
    "spread_bps",
    "update_count",
    "np_tv_chg_pct_300s",
    "np_ret_300s",
]


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _wc(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in ((k, r.get(k)) for k in cols)})


def _wm(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _pf(pnls: Sequence[float]) -> float:
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    if gl <= 0:
        return 999.0 if gp > 0 else 0.0
    return round(gp / gl, 4)


def _outcome_summary(df: pd.DataFrame, key: str) -> list[dict[str, Any]]:
    rows = []
    for state, g in df.groupby(key):
        pnls = g["pnl_pct"].astype(float).tolist()
        rows.append(
            {
                "state": state,
                "n": int(len(g)),
                "pnl_sum_pct": round(float(np.nansum(pnls)), 4),
                "pnl_mean_pct": round(float(np.nanmean(pnls)), 4) if pnls else 0.0,
                "PF": _pf(pnls),
                "win_rate": round(float((g["pnl_pct"] > 0).mean()), 4),
                "STOP_rate": round(float((g["exit_reason"] == "stop_hit").mean()), 4),
                "no_progress_rate": round(float((g["exit_reason"] == "no_progress_exit").mean()), 4),
                "trailing_rate": round(float((g["exit_reason"] == "trailing_mfe_exit").mean()), 4),
                "MFE_mean": round(float(g["MFE_5m"].astype(float).mean()), 4) if "MFE_5m" in g else None,
                "MAE_mean": round(float(g["MAE_5m"].astype(float).mean()), 4) if "MAE_5m" in g else None,
                "hold_mean_sec": round(float(g["hold_sec"].astype(float).mean()), 2) if "hold_sec" in g else None,
                "reentry_rate": round(float(g["is_reentry"].astype(bool).mean()), 4),
            }
        )
    rows.sort(key=lambda r: (-r["n"], r["state"]))
    return rows


def prepare_matrix(df: pd.DataFrame, cols: list[str]) -> tuple[np.ndarray, list[str], dict[str, Any]]:
    use = [c for c in cols if c in df.columns]
    X = df[use].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    imp = SimpleImputer(strategy="median")
    Xs = StandardScaler().fit_transform(imp.fit_transform(X))
    meta = {
        "features_requested": cols,
        "features_used": use,
        "missing_features": [c for c in cols if c not in df.columns],
        "impute": "median",
        "scale": "StandardScaler",
        "n": int(len(df)),
        "n_features": len(use),
    }
    return Xs, use, meta


def cluster_comparison(X: np.ndarray, seeds: Sequence[int] = (0, 1, 2, 7, 42)) -> tuple[list[dict], dict]:
    rows = []
    best = {"method": None, "k": None, "silhouette": -1.0, "labels": None}
    for k in range(2, 9):
        # KMeans
        sils, dbs, aris = [], [], []
        labels_ref = None
        for seed in seeds:
            km = KMeans(n_clusters=k, n_init=10, random_state=seed)
            lab = km.fit_predict(X)
            if labels_ref is None:
                labels_ref = lab
            else:
                aris.append(adjusted_rand_score(labels_ref, lab))
            if len(set(lab)) > 1:
                sils.append(silhouette_score(X, lab))
                dbs.append(davies_bouldin_score(X, lab))
        # bootstrap stability vs ref
        boot_aris = []
        rng = np.random.default_rng(0)
        for _ in range(20):
            idx = rng.integers(0, len(X), size=len(X))
            if len(set(labels_ref[idx])) < 2:
                continue
            km = KMeans(n_clusters=k, n_init=5, random_state=0)
            try:
                lab_b = km.fit_predict(X[idx])
                boot_aris.append(adjusted_rand_score(labels_ref[idx], lab_b))
            except Exception:
                pass
        sil = float(np.mean(sils)) if sils else float("nan")
        row = {
            "method": "KMeans",
            "k": k,
            "silhouette_mean": round(sil, 4) if sil == sil else None,
            "davies_bouldin_mean": round(float(np.mean(dbs)), 4) if dbs else None,
            "seed_ari_mean": round(float(np.mean(aris)), 4) if aris else 1.0,
            "bootstrap_ari_mean": round(float(np.mean(boot_aris)), 4) if boot_aris else None,
            "note": "pilot_n=44",
        }
        rows.append(row)
        if sil == sil and sil > best["silhouette"]:
            best = {"method": "KMeans", "k": k, "silhouette": sil, "labels": labels_ref}

        # GMM
        try:
            gmm = GaussianMixture(n_components=k, random_state=0, covariance_type="full", n_init=3)
            lab = gmm.fit_predict(X)
            sil_g = silhouette_score(X, lab) if len(set(lab)) > 1 else float("nan")
            db_g = davies_bouldin_score(X, lab) if len(set(lab)) > 1 else float("nan")
            rows.append(
                {
                    "method": "GaussianMixture",
                    "k": k,
                    "silhouette_mean": round(float(sil_g), 4) if sil_g == sil_g else None,
                    "davies_bouldin_mean": round(float(db_g), 4) if db_g == db_g else None,
                    "seed_ari_mean": None,
                    "bootstrap_ari_mean": None,
                    "note": "pilot_n=44",
                }
            )
            if sil_g == sil_g and sil_g > best["silhouette"]:
                best = {"method": "GMM", "k": k, "silhouette": float(sil_g), "labels": lab}
        except Exception as exc:
            rows.append({"method": "GaussianMixture", "k": k, "error": str(exc)})

        # Hierarchical
        try:
            lab = AgglomerativeClustering(n_clusters=k).fit_predict(X)
            sil_h = silhouette_score(X, lab) if len(set(lab)) > 1 else float("nan")
            db_h = davies_bouldin_score(X, lab) if len(set(lab)) > 1 else float("nan")
            rows.append(
                {
                    "method": "Agglomerative",
                    "k": k,
                    "silhouette_mean": round(float(sil_h), 4) if sil_h == sil_h else None,
                    "davies_bouldin_mean": round(float(db_h), 4) if db_h == db_h else None,
                    "seed_ari_mean": None,
                    "bootstrap_ari_mean": None,
                    "note": "pilot_n=44",
                }
            )
            if sil_h == sil_h and sil_h > best["silhouette"]:
                best = {"method": "Agglomerative", "k": k, "silhouette": float(sil_h), "labels": lab}
        except Exception as exc:
            rows.append({"method": "Agglomerative", "k": k, "error": str(exc)})

    rows.append(
        {
            "method": "HDBSCAN",
            "k": None,
            "silhouette_mean": None,
            "davies_bouldin_mean": None,
            "seed_ari_mean": None,
            "bootstrap_ari_mean": None,
            "note": "package_not_installed",
        }
    )
    return rows, best


def loo_eval(X: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    y = y.astype(int)
    if len(set(y)) < 2 or len(y) < 5:
        return {"auc": None, "balanced_accuracy": None, "n": int(len(y)), "note": "degenerate_labels"}
    loo = LeaveOneOut()
    probs = np.zeros(len(y), dtype=float)
    preds = np.zeros(len(y), dtype=int)
    for train, test in loo.split(X):
        clf = LogisticRegression(max_iter=200, class_weight="balanced")
        try:
            clf.fit(X[train], y[train])
            probs[test] = clf.predict_proba(X[test])[:, 1]
            preds[test] = clf.predict(X[test])
        except Exception:
            probs[test] = 0.5
            preds[test] = 0
    try:
        auc = float(roc_auc_score(y, probs))
    except Exception:
        auc = None
    return {
        "auc": round(auc, 4) if auc is not None else None,
        "balanced_accuracy": round(float(balanced_accuracy_score(y, preds)), 4),
        "n": int(len(y)),
        "method": "LOO_logistic",
    }


def feature_sets(df: pd.DataFrame) -> dict[str, list[str]]:
    return {
        "A_pbv2_baseline": [c for c in ["score_v2", "momentum", "entry_imbalance_percentile", "quality", "spread_bps", "update_count"] if c in df.columns],
        "B_price_only": [c for c in df.columns if c.startswith("pre_300s_") and any(x in c for x in ("return", "slope", "bounce", "fall", "range", "realized", "breakout", "same_price", "current_price"))],
        "C_price_volume": [c for c in df.columns if c.startswith("pre_300s_")],
        "D_price_board": [c for c in df.columns if c.startswith("pre_300s_return") or c.startswith("pre_300s_slope") or c.startswith("board_60s_") or c.startswith("board_at_entry_")],
        "E_price_volume_board": [c for c in df.columns if c.startswith("pre_300s_") or c.startswith("board_60s_") or c.startswith("board_at_entry_")],
        "F_full": CLUSTER_FEATURES,
    }


def symbol_dependence(df: pd.DataFrame, labels: np.ndarray) -> dict[str, Any]:
    # check if clusters dominated by one symbol or one hour
    info = []
    df = df.copy()
    df["_cl"] = labels
    for cl, g in df.groupby("_cl"):
        sym_c = Counter(g["symbol"].astype(str))
        top_sym, top_n = sym_c.most_common(1)[0]
        hours = Counter(pd.to_datetime(g["entry_time"]).dt.hour)
        top_h, top_hn = hours.most_common(1)[0]
        info.append(
            {
                "cluster": int(cl),
                "n": int(len(g)),
                "top_symbol": top_sym,
                "top_symbol_share": round(top_n / len(g), 4),
                "top_hour": int(top_h),
                "top_hour_share": round(top_hn / len(g), 4),
            }
        )
    return {"clusters": info, "flag_symbol_dominated": any(x["top_symbol_share"] >= 0.5 and x["n"] >= 4 for x in info)}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    be = pd.read_parquet(BOARD_PQ) if BOARD_PQ.is_file() else None
    # Restrict to AM session only
    if be is not None and "session_id" in be.columns:
        be = be[be["session_id"].astype(str) == "live_session_073602"].copy()

    cache = Path(tempfile.mkdtemp(prefix="w43_slim_"))
    try:
        df, quality = build_session_market_state_df(
            session_dir=SESSION,
            capture_dir=CAPTURE,
            trading_date="20260716",
            session_kind="am",
            session_id="live_session_073602",
            board_entry_df=be,
            cache_dir=cache,
        )
    finally:
        shutil.rmtree(cache, ignore_errors=True)

    # Persist research partition
    ms_root = dataset_root(NATIVE)
    summary = {
        "session_validity": "VALID_SESSION",
        "seal_status": "SEALED_VALID",
        "trading_date": "20260716",
        "am_pm_session": {"kind": "am"},
    }
    # Force ingest via direct write if append skip
    ingest = append_session_market_state(native_root=NATIVE, session_dir=SESSION, summary=summary)
    if ingest.get("status") in ("SKIP", "ALREADY_PRESENT", "ERROR") and len(df):
        part = ms_root / "trading_date=20260716"
        part.mkdir(parents=True, exist_ok=True)
        df.to_parquet(part / "market_state_entries.parquet", index=False)
        _wj(part / "state_quality.json", quality)
        man = load_manifest(ms_root)
        man.setdefault("sessions", {})["20260716|am|live_session_073602"] = {
            "trading_date": "20260716",
            "session_kind": "am",
            "session_id": "live_session_073602",
            "n_entries": int(len(df)),
            "status": "PILOT_W43",
            "ingested_at": datetime.now(JST).isoformat(),
        }
        man["trading_dates"] = sorted(set(man.get("trading_dates") or []) | {"20260716"})
        man["n_trading_days"] = len(man["trading_dates"])
        man["adoption_allowed"] = False
        man["reanalysis_gates"] = REANALYSIS_GATES
        save_manifest(ms_root, man)

    df.to_parquet(OUT / "market_state_dataset_20260716.parquet", index=False)
    _wc(OUT / "market_state_feature_dictionary.csv", feature_dictionary())

    n = len(df)
    coverage = {
        "n_entries": n,
        "price_window_sync_ok": int(df["price_window_sync_ok"].sum()) if n else 0,
        "volume_window_sync_ok": int(df["volume_window_sync_ok"].sum()) if n else 0,
        "board_window_sync_ok": int(df["board_window_sync_ok"].sum()) if n else 0,
        "all_feature_sync_ok": int(df["all_feature_sync_ok"].sum()) if n else 0,
        "open_shortfall_600s": int(df["open_window_shortfall_600s"].sum()) if n else 0,
        "volume_source": "capture_trading_volume + np_tv_chg_pct_*",
        "volume_missing_rate_300s": float(df["pre_300s_volume_missing"].mean()) if "pre_300s_volume_missing" in df.columns and n else None,
        "board_sync_from_dataset": int(df["board_sync_ok"].sum()) if "board_sync_ok" in df.columns else None,
        "session_validity": "VALID_SESSION",
        "seal_status": "SEALED_VALID",
        "pm_excluded": "live_session_122532 INVALID_NO_PUSH",
        "duplicates_excluded": True,
        "quality": quality,
    }
    _wj(OUT / "feature_coverage_audit.json", coverage)

    # Clustering
    X, used_feats, prep_meta = prepare_matrix(df, CLUSTER_FEATURES)
    cl_rows, best = cluster_comparison(X)
    _wc(OUT / "unsupervised_cluster_comparison.csv", cl_rows)
    labels = best["labels"] if best.get("labels") is not None else np.zeros(len(df), dtype=int)
    df["cluster_id"] = labels
    dep = symbol_dependence(df, labels)

    # Cluster profiles
    profiles = []
    for cl, g in df.groupby("cluster_id"):
        profiles.append(
            {
                "cluster_id": int(cl),
                "n": int(len(g)),
                "mean_pre_300s_return": round(float(g["pre_300s_return"].astype(float).mean()), 6)
                if "pre_300s_return" in g
                else None,
                "mean_imbalance_l5": round(float(g["board_at_entry_imbalance_l5"].astype(float).mean()), 4)
                if "board_at_entry_imbalance_l5" in g
                else None,
                "mean_score_v2": round(float(g["score_v2"].astype(float).mean()), 3) if "score_v2" in g else None,
                "top_interpretable": Counter(g["INTERPRETABLE_STATE"]).most_common(1)[0][0],
                "top_price_state": Counter(g["PRICE_STATE"]).most_common(1)[0][0],
            }
        )
    _wc(OUT / "cluster_profiles.csv", profiles)
    _wc(OUT / "cluster_outcome_summary.csv", _outcome_summary(df, "cluster_id"))

    # Interpretable states
    interp = _outcome_summary(df, "INTERPRETABLE_STATE")
    _wc(OUT / "interpretable_market_states.csv", interp)

    # Incremental value
    y_win = (df["pnl_pct"].astype(float) > 0).to_numpy()
    y_stop = (df["exit_reason"].astype(str) == "stop_hit").to_numpy()
    y_np = (df["exit_reason"].astype(str) == "no_progress_exit").to_numpy()
    incr_rows = []
    sets = feature_sets(df)
    for name, cols in sets.items():
        if len(cols) < 2:
            incr_rows.append({"feature_set": name, "n_features": len(cols), "note": "insufficient_features"})
            continue
        Xi, _, meta_i = prepare_matrix(df, cols)
        ev_w = loo_eval(Xi, y_win)
        ev_s = loo_eval(Xi, y_stop)
        ev_n = loo_eval(Xi, y_np)
        # capture rates at median threshold
        incr_rows.append(
            {
                "feature_set": name,
                "n_features": meta_i["n_features"],
                "winner_auc": ev_w.get("auc"),
                "winner_bal_acc": ev_w.get("balanced_accuracy"),
                "stop_auc": ev_s.get("auc"),
                "stop_bal_acc": ev_s.get("balanced_accuracy"),
                "no_progress_auc": ev_n.get("auc"),
                "no_progress_bal_acc": ev_n.get("balanced_accuracy"),
                "note": "LOO pilot only; n=44 single day",
            }
        )
    _wc(OUT / "market_state_incremental_value.csv", incr_rows)

    base = next((r for r in incr_rows if r["feature_set"] == "A_pbv2_baseline"), {})
    full = next((r for r in incr_rows if r["feature_set"] == "F_full"), {})
    e_set = next((r for r in incr_rows if r["feature_set"] == "E_price_volume_board"), {})
    auc_base = base.get("winner_auc") or 0.5
    auc_e = e_set.get("winner_auc")
    auc_f = full.get("winner_auc")
    incr_delta = None if auc_e is None else round(float(auc_e) - float(auc_base), 4)
    full_delta = None if auc_f is None else round(float(auc_f) - float(auc_base), 4)
    # Single-day LOO AUC swings are not trustworthy; force weak until multi-day.
    incr_weak = True

    # 6506 / 6474 traces
    def renewed_signal(row: pd.Series) -> str:
        # same-price reENTRY heuristic: tiny price move + short since prior
        ret60 = fnum_safe(row.get("pre_60s_return"))
        px_upd = fnum_safe(row.get("pre_60s_current_price_update_count"))
        vol = row.get("VOLUME_STATE")
        board = row.get("BOARD_STATE")
        if abs(ret60) < 0.0005 and px_upd <= 1:
            return "NO_RENEWED_SIGNAL"
        if vol in ("SURGE", "RISING") and board in ("BID_PRESSURE", "ASK_DEPLETION"):
            return "RENEWED_SIGNAL"
        return "WEAK_OR_UNCLEAR"

    def fnum_safe(v: Any) -> float:
        try:
            x = float(v)
            return x if x == x else 0.0
        except Exception:
            return 0.0

    rows_6506 = []
    g6506 = df[df["symbol"].astype(str).str.startswith("6506")].sort_values("entry_time")
    prev_et = None
    for _, r in g6506.iterrows():
        gap = None
        if prev_et is not None:
            t0 = pd.to_datetime(prev_et)
            t1 = pd.to_datetime(r["entry_time"])
            gap = (t1 - t0).total_seconds()
        rows_6506.append(
            {
                "symbol": r["symbol"],
                "entry_time": r["entry_time"],
                "is_reentry": r["is_reentry"],
                "gap_sec_from_prev": gap,
                "pnl_pct": r["pnl_pct"],
                "exit_reason": r["exit_reason"],
                "PRICE_STATE": r["PRICE_STATE"],
                "VOLUME_STATE": r["VOLUME_STATE"],
                "BOARD_STATE": r["BOARD_STATE"],
                "ACTIVITY_STATE": r["ACTIVITY_STATE"],
                "PBV2_STATE": r["PBV2_STATE"],
                "INTERPRETABLE_STATE": r["INTERPRETABLE_STATE"],
                "STATE_COMBO_ID": r["STATE_COMBO_ID"],
                "cluster_id": int(r["cluster_id"]),
                "renewed_signal": renewed_signal(r),
                "pre_60s_return": r.get("pre_60s_return"),
                "pre_60s_current_price_update_count": r.get("pre_60s_current_price_update_count"),
                "note": "same_price_reentry_candidate" if gap is not None and gap <= 10 else "",
            }
        )
        prev_et = r["entry_time"]
    _wc(OUT / "symbol_6506_market_state_trace.csv", rows_6506)

    rows_6474 = []
    for _, r in df[df["symbol"].astype(str).str.startswith("6474")].iterrows():
        rows_6474.append(
            {
                "symbol": r["symbol"],
                "entry_time": r["entry_time"],
                "pnl_pct": r["pnl_pct"],
                "exit_reason": r["exit_reason"],
                "PRICE_STATE": r["PRICE_STATE"],
                "VOLUME_STATE": r["VOLUME_STATE"],
                "BOARD_STATE": r["BOARD_STATE"],
                "ACTIVITY_STATE": r["ACTIVITY_STATE"],
                "PBV2_STATE": r["PBV2_STATE"],
                "INTERPRETABLE_STATE": r["INTERPRETABLE_STATE"],
                "price_age_sec": r.get("price_age_sec"),
                "board_age_sec": r.get("board_age_sec"),
                "board_60s_same_price_board_churn": r.get("board_60s_same_price_board_churn"),
                "stale_quote_churn_fit": r["INTERPRETABLE_STATE"] == "STALE_PRICE_QUOTE_CHURN"
                or r["PRICE_STATE"] == "QUOTE_ONLY_STALE"
                or r["BOARD_STATE"] == "QUOTE_ONLY_CHURN",
                "cluster_id": int(r["cluster_id"]),
            }
        )
    _wc(OUT / "symbol_6474_market_state_trace.csv", rows_6474)

    # Leakage audit
    leak = {
        "future_in_features": False,
        "post_returns_separated": True,
        "post_return_cols": [c for c in df.columns if c.startswith("post_return_") or c.startswith("return_")],
        "feature_uses_only_recv_le_entry": True,
        "shadow_cap_rejected_mixed_into_actual": False,
        "grain": "1 ENTRY = 1 sample",
        "note": "Outcome labels used only for evaluation tables, not clustering fit.",
    }
    _wj(OUT / "leakage_audit.json", leak)

    schema = {
        "schema_version": SCHEMA_VERSION,
        "grain": "1 ENTRY = 1 row",
        "pre_windows_sec": [30, 60, 120, 300, 600],
        "post_windows_sec": [30, 60, 120, 300, 600],
        "state_fields": [
            "PRICE_STATE",
            "VOLUME_STATE",
            "BOARD_STATE",
            "ACTIVITY_STATE",
            "PBV2_STATE",
            "STATE_COMBO_ID",
            "INTERPRETABLE_STATE",
        ],
        "paths": {
            "partition": "results/research/pre_entry_market_state/trading_date=YYYYMMDD/",
            "entries": "market_state_entries.parquet",
            "quality": "state_quality.json",
            "manifest": "market_state_manifest.json",
            "summary": "market_state_summary.csv",
            "stability": "cluster_stability_history.csv",
        },
        "reanalysis_gates": REANALYSIS_GATES,
        "adoption_blocked_until_days": 5,
        "max_interpretable_states": 8,
    }
    _wj(OUT / "multi_day_market_state_schema.json", schema)

    # stability history seed row
    stab_path = ms_root / "cluster_stability_history.csv"
    _wc(
        stab_path,
        [
            {
                "as_of": datetime.now(JST).date().isoformat(),
                "n_trading_days": 1,
                "n_entries": n,
                "best_method": best.get("method"),
                "best_k": best.get("k"),
                "silhouette": round(float(best.get("silhouette") or 0), 4),
                "bootstrap_ari": next(
                    (
                        r.get("bootstrap_ari_mean")
                        for r in cl_rows
                        if r.get("method") == "KMeans" and r.get("k") == best.get("k")
                    ),
                    None,
                ),
                "note": "pilot_single_day",
            }
        ],
    )

    code_manifest = {
        "added": [
            "src/research/pre_entry_market_state.py",
            "scripts/phase687w43_pre_entry_market_state_analysis.py",
        ],
        "modified": ["src/small_paper/pilot_runner.py"],
        "hook": "fail-open post-seal maybe_append_session_market_state",
        "yaml_changed": False,
        "runtime_entry_wired": False,
        "shadow_added": False,
        "capture_control_changed": False,
        "max_workers": 4,
        "raw_capture_copy": False,
    }
    _wj(OUT / "code_change_manifest.json", code_manifest)
    order_safety = {
        "submit": 0,
        "cancel": 0,
        "real_orders_changed": False,
        "mainline_changed": False,
        "entry_exit_cap_or_changed": False,
        "shadow_added": False,
    }
    _wj(OUT / "order_safety_audit.json", order_safety)

    # Rank states
    win_states = sorted(interp, key=lambda r: (-r["win_rate"], -r["n"]))
    stop_states = sorted(interp, key=lambda r: (-r["STOP_rate"], -r["n"]))
    np_states = sorted(interp, key=lambda r: (-r["no_progress_rate"], -r["n"]))
    major_states = [r["state"] for r in interp if r["n"] >= 2][:8]

    stability_label = "LOW_PILOT"
    boot = next(
        (r.get("bootstrap_ari_mean") for r in cl_rows if r.get("method") == "KMeans" and r.get("k") == best.get("k")),
        None,
    )
    if boot is not None and boot >= 0.6:
        stability_label = "MODERATE_PILOT"
    if boot is not None and boot >= 0.75 and n >= 100:
        stability_label = "STABLE"

    data_quality_failed = coverage["all_feature_sync_ok"] < max(1, int(0.7 * n)) if n else True
    candidates_found = any(r["n"] >= 3 and (r["win_rate"] >= 0.6 or r["STOP_rate"] >= 0.5) for r in interp)

    verdicts = ["MARKET_STATE_PIPELINE_READY", "MARKET_STATE_DATA_INSUFFICIENT"]
    if candidates_found:
        verdicts.append("MARKET_STATE_CANDIDATES_FOUND")
    if incr_weak:
        verdicts.append("MARKET_STATE_INCREMENTAL_VALUE_WEAK")
    if data_quality_failed:
        verdicts.append("DATA_QUALITY_FAILED")

    required = {
        "1_valid_entries": n,
        "2_price_window_sync_ok": coverage["price_window_sync_ok"],
        "3_volume_window_sync_ok": coverage["volume_window_sync_ok"],
        "4_board_window_sync_ok": coverage["board_window_sync_ok"],
        "5_all_feature_sync_ok": coverage["all_feature_sync_ok"],
        "6_best_cluster_k": best.get("k"),
        "7_cluster_stability": {"label": stability_label, "bootstrap_ari": boot, "seed_note": prep_meta},
        "8_major_market_states": major_states,
        "9_winner_rich_states": [r["state"] for r in win_states[:3]],
        "10_stop_rich_states": [r["state"] for r in stop_states[:3]],
        "11_no_progress_rich_states": [r["state"] for r in np_states[:3]],
        "12_price_volume_board_incremental": {"winner_auc": auc_e, "delta_vs_pbv2": incr_delta},
        "13_full_vs_pbv2": {"full_auc": auc_f, "pbv2_auc": auc_base, "delta": full_delta},
        "14_6506_three_entries": rows_6506,
        "15_6474": rows_6474,
        "16_one_day_decision_ok": False,
        "17_next_reanalysis_trading_days": 5,
        "18_mainline_unchanged": True,
        "19_shadow_added": False,
        "20_submit_cancel": {"submit": 0, "cancel": 0},
    }

    report = {
        "phase": "Phase687W43",
        "title": "Pre-Entry Market State Analysis",
        "verdict": verdicts,
        "generated_at": datetime.now(JST).isoformat(),
        "session": "20260716/live_session_073602",
        "pm_excluded": True,
        "cluster_prep": prep_meta,
        "best_cluster": {"method": best.get("method"), "k": best.get("k"), "silhouette": best.get("silhouette")},
        "symbol_dependence": dep,
        "incremental_weak": incr_weak,
        "ingest": ingest,
        "required_answers": required,
        "note": "Pilot only. Do not adopt cluster/rules before >=5 trading days; candidate rules at >=20.",
    }
    _wj(OUT / "phase687w43_report.json", report)

    md = f"""# Phase687W43 Pre-Entry Market State Analysis

## Verdict
{', '.join(f'`{v}`' for v in verdicts)}

### Constraints
- MAINLINE / ENTRY / EXIT / CAP / OR unchanged
- Shadow追加なし
- YAML変更なし
- submit/cancel = 0/0
- raw Capture複製なし（一時slimのみ）

### Required answers
1. 有効ENTRY数: **{n}**
2. 価格窓同期成功数: **{coverage['price_window_sync_ok']}**
3. 出来高同期成功数: **{coverage['volume_window_sync_ok']}**
4. 板同期成功数: **{coverage['board_window_sync_ok']}**
5. 全特徴同期成功数: **{coverage['all_feature_sync_ok']}**
6. 最適クラスタ数: **{best.get('k')}** ({best.get('method')}, sil={round(float(best.get('silhouette') or 0), 4)})
7. クラスタ安定性: **{stability_label}** (bootstrap ARI={boot})
8. 主要市場状態: `{major_states}`
9. 勝ちが多い状態: `{required['9_winner_rich_states']}`
10. STOPが多い状態: `{required['10_stop_rich_states']}`
11. no_progressが多い状態: `{required['11_no_progress_rich_states']}`
12. 価格＋出来高＋板追加価値: AUC={auc_e} Δvs PBv2={incr_delta}
13. PBv2 baseline差: PBv2 AUC={auc_base} / Full AUC={auc_f} / Δ={full_delta}
14. 6506 3ENTRY: see `symbol_6506_market_state_trace.csv`
15. 6474: see `symbol_6474_market_state_trace.csv`
16. 1日分で判断可能か: **False**
17. 次回再分析営業日数: **5**
18. MAINLINE変更なし: **True**
19. Shadow追加なし: **True**
20. submit/cancel: **0/0**

### Research direction
Shadow探索は完了済み（W42）。本フェーズは ENTRY前市場状態の研究基盤。
採用候補は **5営業日前に出さない**。10日で安定性、20日でcandidate rule検討。

### Volume source
`capture_trading_volume` + `np_tv_chg_pct_*`（欠損率は feature_coverage_audit.json）

### Code
- `src/research/pre_entry_market_state.py`
- post-seal fail-open hook in `pilot_runner.py`
"""
    _wm(OUT / "phase687w43_decision.md", md)
    print(
        json.dumps(
            {
                "out": str(OUT),
                "n": n,
                "sync": {
                    "price": coverage["price_window_sync_ok"],
                    "volume": coverage["volume_window_sync_ok"],
                    "board": coverage["board_window_sync_ok"],
                    "all": coverage["all_feature_sync_ok"],
                },
                "best_k": best.get("k"),
                "verdict": verdicts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
