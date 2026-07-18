#!/usr/bin/env python3
"""W47: Winner ENTRY trigger search (research-only).

Reuses W43C/W43D snapshot + independent-move machinery. Does NOT rebuild Capture
and does NOT modify Runtime / YAML / trading conditions.
"""

from __future__ import annotations

import json
import math
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import combinations
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier, _tree

warnings.filterwarnings("ignore", category=UserWarning)

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE / "scripts"))

import phase687w43c_watch50_future30m_opportunity as w43c  # noqa: E402
import phase687w43d_5day_winner_state_validation as w43d  # noqa: E402

OUT = NATIVE / "results" / "research" / "pre_entry_market_state"
TMP = OUT / "_w47_tmp"
SNAP_CACHE = TMP / "day_snaps"
PUSH_ROOT = NATIVE / "data" / "push_jsonl"
REPORTS = NATIVE / "results" / "reports"
MAX_WORKERS = 4
TARGET_DAYS = 20
MIN_SYMBOLS = 40
Q_BINS = 5
TOP_FEATURES_STAGE1 = 40
TOP_FEATURES_STAGE2 = 30
TOP_RULES_PROMOTE = 20
MIN_RULE_N = 25
MIN_RULE_N_CONF = 15

FEATURE_COLS = list(w43d.FEATURE_COLS)

META_COLS = {
    "trading_date",
    "symbol",
    "session",
    "universe_segment",
    "refresh_flag",
    "t0_epoch",
    "t0_time",
    "anchor_time",
    "anchor_epoch",
    "independent_move_id",
    "primary_label",
    "exclude_reason",
    "capture_class",
    "funnel_class",
    "candidate_seen",
    "funnel_detail",
    "secs_to_entry",
    "raw_episode_count",
    "label_horizon",
    "source",
    "row_id",
    "split",
    "winner_a",
    "stop_proxy",
    "no_progress_proxy",
    "future_30m_return",
    "future_30m_mfe",
    "future_30m_mae",
    "max_future_mfe",
    "max_future_return",
    "proxy_pnl",
    "score_proxy",
    "pbv2_baseline",
}


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def day_compact(d: str) -> str:
    return d.replace("-", "")


def classify_market_data_days() -> list[dict[str, Any]]:
    days = sorted(
        [p.name for p in PUSH_ROOT.iterdir() if p.is_dir() and p.name.startswith("20")],
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    for d in days:
        n = len(list((PUSH_ROOT / d).glob("*.jsonl")))
        if n < MIN_SYMBOLS:
            continue
        day = day_compact(d)
        uni = {
            "am": REPORTS / f"universe_core10_dynamic40_price_risk_am_{day}.csv",
            "am_refresh": REPORTS / f"universe_core10_dynamic40_price_risk_am_refresh1000_{day}.csv",
            "pm": REPORTS / f"universe_core10_dynamic40_price_risk_pm_{day}.csv",
            "pm_refresh": REPORTS / f"universe_core10_dynamic40_price_risk_pm_refresh1430_{day}.csv",
        }
        uni_ok = all(p.is_file() for p in uni.values())
        # fallback: am/pm only if refresh missing
        if not uni_ok:
            uni2 = {
                "am": uni["am"],
                "am_refresh": uni["am"] if uni["am"].is_file() else uni["am_refresh"],
                "pm": uni["pm"],
                "pm_refresh": uni["pm"] if uni["pm"].is_file() else uni["pm_refresh"],
            }
            if all(p.is_file() for p in uni2.values()):
                uni = uni2
                uni_ok = True
        out.append(
            {
                "date": d,
                "day": day,
                "push_files": n,
                "universe_ok": uni_ok,
                "universe": uni,
                "push_dir": PUSH_ROOT / d,
                "sessions": w43d.pick_sessions(day),
                "class": "MARKET_DATA_DAY",
            }
        )
        if len(out) >= TARGET_DAYS:
            break
    return out


def load_or_build_day_snap(meta: dict[str, Any]) -> pd.DataFrame:
    day = meta["day"]
    cache = SNAP_CACHE / f"{day}_watch50_snapshot.parquet"
    if cache.is_file():
        print(f"  cache hit {cache.name}", flush=True)
        return w43d.enrich_features(pd.read_parquet(cache))
    if day == "20260717":
        pq = OUT / "w43c_20260717_watch50_snapshot.parquet"
        if pq.is_file():
            print(f"  reusing W43C {pq.name}", flush=True)
            df = w43d.enrich_features(pd.read_parquet(pq))
            SNAP_CACHE.mkdir(parents=True, exist_ok=True)
            df.to_parquet(cache, index=False)
            return df
    if not meta.get("universe_ok"):
        print(f"  skip {day}: universe incomplete", flush=True)
        return pd.DataFrame()
    print(f"  building snapshots {meta['date']}...", flush=True)
    df = w43d.run_day_snapshots(meta)
    SNAP_CACHE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache, index=False)
    return df


def _collapse_move_with_mae(rows: list[pd.Series]) -> dict[str, Any]:
    r0 = rows[0]
    mfes = [float(r["future_30m_mfe"]) for r in rows if pd.notna(r.get("future_30m_mfe"))]
    rets = [float(r["future_30m_return"]) for r in rows if pd.notna(r.get("future_30m_return"))]
    maes = [float(r["future_30m_mae"]) for r in rows if pd.notna(r.get("future_30m_mae"))]
    feat = {c: r0.get(c) for c in FEATURE_COLS if c in r0.index}
    return {
        "trading_date": r0["trading_date"],
        "symbol": r0["symbol"],
        "session": r0.get("session"),
        "universe_segment": r0.get("universe_segment"),
        "refresh_flag": r0.get("refresh_flag"),
        "label_horizon": "15m" if r0.get("universe_segment") == "pm_refresh1430" else "30m",
        **feat,
        "independent_move_id": f"{r0['trading_date']}_{r0['symbol']}_{int(float(r0['t0_epoch']))}",
        "anchor_time": r0.get("t0_time"),
        "anchor_epoch": float(r0["t0_epoch"]),
        "raw_episode_count": len(rows),
        "future_30m_mfe": max(mfes) if mfes else None,
        "future_30m_return": max(rets) if rets else None,
        "future_30m_mae": min(maes) if maes else None,
        "max_future_mfe": max(mfes) if mfes else None,
        "max_future_return": max(rets) if rets else None,
        "primary_label": r0.get("primary_label"),
        "source": "independent_move",
    }


def build_independent_moves_from_snaps(snaps: pd.DataFrame) -> pd.DataFrame:
    """Collapse contiguous LARGE_RISE snapshots into independent moves (with MAE)."""
    lr = snaps[snaps["primary_label"] == "LARGE_RISE"].sort_values(
        ["trading_date", "symbol", "t0_epoch"]
    )
    if lr.empty:
        return pd.DataFrame()
    # first: raw episodes (gap > 90s)
    eps: list[list[pd.Series]] = []
    for (_, _), g in lr.groupby(["trading_date", "symbol"]):
        g = g.sort_values("t0_epoch")
        cur: list[pd.Series] = []
        prev = None
        for _, row in g.iterrows():
            t = float(row["t0_epoch"])
            if prev is None or t - prev <= 90:
                cur.append(row)
            else:
                if cur:
                    eps.append(cur)
                cur = [row]
            prev = t
        if cur:
            eps.append(cur)
    # collapse overlapping 30m windows into independent moves
    ep_rows = []
    for cur in eps:
        r0 = cur[0]
        mfes = [float(r["future_30m_mfe"]) for r in cur if pd.notna(r.get("future_30m_mfe"))]
        rets = [float(r["future_30m_return"]) for r in cur if pd.notna(r.get("future_30m_return"))]
        maes = [float(r["future_30m_mae"]) for r in cur if pd.notna(r.get("future_30m_mae"))]
        feat = {c: r0.get(c) for c in FEATURE_COLS if c in r0.index}
        ep_rows.append(
            {
                "trading_date": r0["trading_date"],
                "symbol": r0["symbol"],
                "session": r0.get("session"),
                "universe_segment": r0.get("universe_segment"),
                "refresh_flag": r0.get("refresh_flag"),
                "t0_epoch": float(r0["t0_epoch"]),
                "t0_time": r0.get("t0_time"),
                "future_30m_mfe": max(mfes) if mfes else None,
                "future_30m_return": max(rets) if rets else None,
                "future_30m_mae": min(maes) if maes else None,
                "primary_label": r0.get("primary_label"),
                **feat,
            }
        )
    ep = pd.DataFrame(ep_rows)
    if ep.empty:
        return ep
    moves: list[dict[str, Any]] = []
    for (_, _), g in ep.sort_values("t0_epoch").groupby(["trading_date", "symbol"]):
        g = g.sort_values("t0_epoch")
        cur: list[pd.Series] = []
        cur_end = None
        for _, r in g.iterrows():
            a = float(r["t0_epoch"])
            if cur and a <= cur_end:
                cur.append(r)
                cur_end = max(cur_end, a + 1800.0)
            else:
                if cur:
                    moves.append(_collapse_move_with_mae(cur))
                cur = [r]
                cur_end = a + 1800.0
        if cur:
            moves.append(_collapse_move_with_mae(cur))
    return pd.DataFrame(moves)


def attach_candidate_seen(moves: pd.DataFrame) -> pd.DataFrame:
    """Join candidate_seen from existing W43D moves when available."""
    path = OUT / "w43d_5d_independent_moves.csv"
    if not path.is_file() or moves.empty:
        moves = moves.copy()
        moves["candidate_seen"] = np.nan
        return moves
    old = pd.read_csv(path)
    if "independent_move_id" not in old.columns:
        moves = moves.copy()
        moves["candidate_seen"] = np.nan
        return moves
    keep = old[["independent_move_id", "candidate_seen"]].drop_duplicates("independent_move_id")
    out = moves.merge(keep, on="independent_move_id", how="left")
    return out


def labeled_snapshot_rows(snaps: pd.DataFrame) -> pd.DataFrame:
    """All valid future-labeled snapshots (mixed winners/stops — not LARGE_RISE-only)."""
    if snaps.empty:
        return pd.DataFrame()
    df = snaps[
        snaps["future_30m_mfe"].notna()
        & snaps["future_30m_return"].notna()
        & snaps["future_30m_mae"].notna()
        & (snaps["primary_label"].astype(str) != "UNAVAILABLE")
    ].copy()
    df["independent_move_id"] = (
        df["trading_date"].astype(str)
        + "_"
        + df["symbol"].astype(str)
        + "_"
        + df["t0_epoch"].astype(float).astype(int).astype(str)
    )
    df["anchor_epoch"] = df["t0_epoch"].astype(float)
    df["anchor_time"] = df["t0_time"]
    df["source"] = "snapshot"
    return df


def tag_move_membership(snaps_labeled: pd.DataFrame, moves: pd.DataFrame) -> pd.DataFrame:
    """Flag snapshot rows that fall inside an independent-move 30m window."""
    out = snaps_labeled.copy()
    out["in_independent_move"] = False
    out["move_id_overlap"] = ""
    if moves.empty or out.empty:
        return out
    for _, m in moves.iterrows():
        day = str(m["trading_date"])
        sym = str(m["symbol"])
        a0 = float(m["anchor_epoch"])
        a1 = a0 + 1800.0
        sel = (
            (out["trading_date"].astype(str) == day)
            & (out["symbol"].astype(str) == sym)
            & (out["anchor_epoch"] >= a0)
            & (out["anchor_epoch"] <= a1)
        )
        out.loc[sel, "in_independent_move"] = True
        out.loc[sel, "move_id_overlap"] = str(m["independent_move_id"])
    return out


def dedupe_analysis_frame(snaps_labeled: pd.DataFrame, moves: pd.DataFrame) -> pd.DataFrame:
    """
    Primary universe = labeled snapshots (needed for WINNER/STOP contrast).
    Independent-move anchors are merged in and deduped by symbol-time
    (moves alone are LARGE_RISE-only ⇒ WINNER_A tautology).
    """
    parts = []
    if not snaps_labeled.empty:
        parts.append(snaps_labeled)
    if not moves.empty:
        m = moves.copy()
        m["source"] = "independent_move"
        # moves are already WINNER_A by construction; keep for coverage tagging only
        # but still include for dedupe completeness when snap missing
        parts.append(m)
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True, sort=False)
    # prefer snapshot rows (mixed labels) over move-only duplicates
    df["_src_rank"] = np.where(df["source"] == "snapshot", 0, 1)
    df = df.sort_values(["_src_rank", "anchor_epoch"], kind="mergesort")
    df["_sym_time"] = (
        df["symbol"].astype(str)
        + "|"
        + df["trading_date"].astype(str)
        + "|"
        + (df["anchor_epoch"].astype(float) // 30 * 30).astype(int).astype(str)
    )
    df = df.drop_duplicates(subset=["_sym_time"], keep="first")
    return df.drop(columns=["_src_rank", "_sym_time"], errors="ignore")


def label_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    mfe = pd.to_numeric(out.get("future_30m_mfe"), errors="coerce")
    ret = pd.to_numeric(out.get("future_30m_return"), errors="coerce")
    mae = pd.to_numeric(out.get("future_30m_mae"), errors="coerce")
    # fallback columns from older moves
    if mfe.isna().all() and "max_future_mfe" in out.columns:
        mfe = pd.to_numeric(out["max_future_mfe"], errors="coerce")
        out["future_30m_mfe"] = mfe
    if ret.isna().all() and "max_future_return" in out.columns:
        ret = pd.to_numeric(out["max_future_return"], errors="coerce")
        out["future_30m_return"] = ret

    out["winner_a"] = (mfe >= 1.0) & (ret >= 0.5)
    out["stop_proxy"] = mae <= -1.2
    # NO_PROGRESS_PROXY from futures when MAE available; else skip (NaN)
    np_mask = (
        (~out["winner_a"])
        & (~out["stop_proxy"].fillna(False))
        & (mfe < 0.5)
        & (ret.abs() < 0.3)
        & mae.notna()
    )
    out["no_progress_proxy"] = np.where(mae.notna(), np_mask, np.nan)
    # proxy pnl: stop hits locked to MAE, else 30m return
    out["proxy_pnl"] = np.where(out["stop_proxy"].fillna(False), mae, ret)
    return out


def numeric_feature_cols(df: pd.DataFrame) -> list[str]:
    cols = []
    for c in FEATURE_COLS:
        if c not in df.columns:
            continue
        s = pd.to_numeric(df[c], errors="coerce")
        if s.notna().sum() < 50:
            continue
        if s.nunique(dropna=True) < 5:
            continue
        cols.append(c)
    return cols


def score_proxy_series(df: pd.DataFrame) -> pd.Series:
    parts = []
    for c, w in (
        ("accel_60s", 1.0),
        ("ret_30s", 1.0),
        ("vol_ratio_60_300", 0.5),
        ("pre_300s_new_high_count", 0.3),
        ("spread_bps", -0.3),
    ):
        if c not in df.columns:
            continue
        s = pd.to_numeric(df[c], errors="coerce")
        mu = s.mean(skipna=True)
        sd = s.std(skipna=True)
        if sd is None or not np.isfinite(sd) or sd < 1e-12:
            z = s * 0.0
        else:
            z = (s - mu) / sd
        parts.append(w * z)
    if not parts:
        return pd.Series(np.nan, index=df.index)
    return sum(parts)


def assign_pbv2_baseline(df: pd.DataFrame) -> pd.DataFrame:
    """PBv2 baseline: candidate_seen when covering both splits; else top-quartile score proxy."""
    out = df.copy()
    out["score_proxy"] = score_proxy_series(out)
    cs = out.get("candidate_seen")
    cs_bool = None
    if cs is not None:
        cs_bool = cs.map(lambda x: bool(x) if pd.notna(x) else False)
    # candidate_seen only exists on a subset of W43D days — require coverage on enough days
    if cs_bool is not None and int(cs_bool.sum()) >= 50:
        days_with = out.loc[cs_bool, "trading_date"].astype(str).nunique()
        if days_with >= 4:
            out["pbv2_baseline"] = cs_bool
            out["pbv2_baseline_mode"] = "candidate_seen"
            return out
    q = out["score_proxy"].quantile(0.75)
    out["pbv2_baseline"] = out["score_proxy"] >= q
    out["pbv2_baseline_mode"] = "top_quartile_score_proxy"
    return out


def eval_mask(df: pd.DataFrame, mask: pd.Series) -> dict[str, Any]:
    m = mask.fillna(False).astype(bool)
    sub = df.loc[m]
    n = int(len(sub))
    if n == 0:
        return {
            "n": 0,
            "winner_rate": None,
            "winner_precision": None,
            "mean_ret": None,
            "stop_rate": None,
            "net_proxy_pnl": None,
            "mean_proxy_pnl": None,
        }
    w = sub["winner_a"].astype(bool)
    s = sub["stop_proxy"].fillna(False).astype(bool)
    return {
        "n": n,
        "winner_rate": float(w.mean()),
        "winner_precision": float(w.mean()),
        "mean_ret": float(pd.to_numeric(sub["future_30m_return"], errors="coerce").mean()),
        "stop_rate": float(s.mean()),
        "net_proxy_pnl": float(pd.to_numeric(sub["proxy_pnl"], errors="coerce").sum()),
        "mean_proxy_pnl": float(pd.to_numeric(sub["proxy_pnl"], errors="coerce").mean()),
    }


def stage1_quantile_bins(df: pd.DataFrame, features: list[str]) -> tuple[list[dict], list[str]]:
    rows: list[dict[str, Any]] = []
    mono_hits: list[tuple[float, str, str, int]] = []
    for feat in features[:TOP_FEATURES_STAGE1]:
        s = pd.to_numeric(df[feat], errors="coerce")
        try:
            bins = pd.qcut(s, q=Q_BINS, duplicates="drop")
        except ValueError:
            continue
        tmp = df.loc[s.notna()].copy()
        tmp["_bin"] = bins.loc[s.notna()].astype(str)
        stats = []
        for b, g in tmp.groupby("_bin", observed=True):
            ev = eval_mask(g, pd.Series(True, index=g.index))
            stats.append({"bin": str(b), **ev})
            rows.append({"feature": feat, "bin": str(b), **ev})
        if len(stats) < 3:
            continue
        rates = [st["winner_rate"] if st["winner_rate"] is not None else 0.0 for st in stats]
        # monotonic increasing / decreasing
        inc = all(rates[i] <= rates[i + 1] + 1e-12 for i in range(len(rates) - 1))
        dec = all(rates[i] >= rates[i + 1] - 1e-12 for i in range(len(rates) - 1))
        if inc or dec:
            side = "high" if inc else "low"
            best_i = int(np.argmax(rates)) if inc else int(np.argmin(rates) * 0 + (0 if dec else 0))
            # extreme bin
            extreme = stats[-1] if side == "high" else stats[0]
            lift = (extreme["winner_rate"] or 0) - float(df["winner_a"].mean())
            mono_hits.append((lift, feat, side, extreme["n"] or 0))
    mono_hits.sort(reverse=True)
    ranked = [f for _, f, _, _ in mono_hits]
    # fill with AUC-like ranking if needed
    if len(ranked) < TOP_FEATURES_STAGE2:
        base = float(df["winner_a"].mean()) if len(df) else 0.0
        scored = []
        for feat in features:
            if feat in ranked:
                continue
            s = pd.to_numeric(df[feat], errors="coerce")
            m = s.notna()
            if m.sum() < 50:
                continue
            # point-biserial proxy
            y = df.loc[m, "winner_a"].astype(float).to_numpy()
            x = s.loc[m].to_numpy()
            if np.std(x) < 1e-12 or len(np.unique(y)) < 2:
                continue
            corr = float(np.corrcoef(x, y)[0, 1])
            scored.append((abs(corr), feat))
        scored.sort(reverse=True)
        for _, f in scored:
            if f not in ranked:
                ranked.append(f)
            if len(ranked) >= TOP_FEATURES_STAGE2:
                break
    return rows, ranked[:TOP_FEATURES_STAGE2]


def _bin_extreme_specs(df: pd.DataFrame, feat: str) -> list[dict[str, Any]]:
    """Discovery-only quantile extremes with frozen numeric edges for Confirmation."""
    s = pd.to_numeric(df[feat], errors="coerce")
    try:
        cats = pd.qcut(s, q=Q_BINS, duplicates="drop")
    except ValueError:
        return []
    levels = list(cats.cat.categories) if hasattr(cats, "cat") else []
    if len(levels) < 2:
        return []
    out: list[dict[str, Any]] = []
    for side, iv in (("low", levels[0]), ("high", levels[-1])):
        closed = getattr(iv, "closed", "right")
        left_closed = closed in ("left", "both")
        right_closed = closed in ("right", "both")
        left_b = "[" if left_closed else "("
        right_b = "]" if right_closed else ")"
        out.append(
            {
                "feature": feat,
                "side": side,
                "name": f"{feat}:q_{side}",
                "lo": float(iv.left),
                "hi": float(iv.right),
                "left_closed": left_closed,
                "right_closed": right_closed,
                "mask": cats == iv,
                "description": f"{feat} in {left_b}{float(iv.left):.6g}, {float(iv.right):.6g}{right_b}",
            }
        )
    return out


def _apply_interval_spec(df: pd.DataFrame, spec: dict[str, Any]) -> pd.Series:
    s = pd.to_numeric(df[spec["feature"]], errors="coerce")
    ok = s.notna()
    if spec["left_closed"]:
        ok &= s >= spec["lo"]
    else:
        ok &= s > spec["lo"]
    if spec["right_closed"]:
        ok &= s <= spec["hi"]
    else:
        ok &= s < spec["hi"]
    return ok.fillna(False)


def stage2_two_feature_rules(
    df: pd.DataFrame, features: list[str]
) -> list[dict[str, Any]]:
    extremes: dict[str, list[dict[str, Any]]] = {}
    for f in features:
        extremes[f] = _bin_extreme_specs(df, f)
    rules: list[dict[str, Any]] = []
    for f1, f2 in combinations(features, 2):
        for s1 in extremes.get(f1, []):
            for s2 in extremes.get(f2, []):
                mask = s1["mask"].reindex(df.index, fill_value=False) & s2["mask"].reindex(
                    df.index, fill_value=False
                )
                ev = eval_mask(df, mask)
                if (ev["n"] or 0) < MIN_RULE_N:
                    continue
                base_w = float(df["winner_a"].mean())
                lift = (ev["winner_precision"] or 0) - base_w
                desc = f"{s1['description']} AND {s2['description']}"
                rules.append(
                    {
                        "rule_id": f"AND::{s1['name']}::{s2['name']}",
                        "features": [f1, f2],
                        "description": desc,
                        "specs": [
                            {k: s1[k] for k in ("feature", "side", "lo", "hi", "left_closed", "right_closed", "description")},
                            {k: s2[k] for k in ("feature", "side", "lo", "hi", "left_closed", "right_closed", "description")},
                        ],
                        "kind": "two_feature_and",
                        "discovery": ev,
                        "lift_winner": lift,
                        "score": lift * math.sqrt(ev["n"]) - 0.5 * (ev["stop_rate"] or 0) * math.sqrt(ev["n"]),
                    }
                )
    rules.sort(key=lambda r: r["score"], reverse=True)
    seen = set()
    uniq = []
    for r in rules:
        key = tuple(sorted(r["features"])) + (r["description"],)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
        if len(uniq) >= TOP_RULES_PROMOTE * 3:
            break
    return uniq[:TOP_RULES_PROMOTE]


def extract_tree_rules(clf: DecisionTreeClassifier, feature_names: list[str]) -> list[str]:
    tree = clf.tree_
    feature_name = [
        feature_names[i] if i != _tree.TREE_UNDEFINED else "undefined"
        for i in tree.feature
    ]
    rules: list[str] = []

    def recurse(node: int, path: list[str]) -> None:
        if tree.feature[node] != _tree.TREE_UNDEFINED:
            name = feature_name[node]
            thr = tree.threshold[node]
            recurse(tree.children_left[node], path + [f"{name} <= {thr:.6g}"])
            recurse(tree.children_right[node], path + [f"{name} > {thr:.6g}"])
        else:
            vals = tree.value[node][0]
            # class1 = winner
            if len(vals) > 1 and vals[1] > vals[0] and sum(vals) >= 5:
                rules.append(" AND ".join(path) if path else "TRUE")

    recurse(0, [])
    return rules[:10]


def apply_rule_description(df: pd.DataFrame, description: str) -> pd.Series:
    """Evaluate AND of 'feat in [lo, hi]' / tree inequalities using frozen edges."""
    if description == "TRUE":
        return pd.Series(True, index=df.index)
    parts = [p.strip() for p in description.split(" AND ") if p.strip()]
    mask = pd.Series(True, index=df.index)
    for p in parts:
        if " in " in p and ("[" in p or "(" in p):
            feat, interval = p.split(" in ", 1)
            feat = feat.strip()
            lo, hi, left, right = _parse_interval(interval.strip())
            s = pd.to_numeric(df[feat], errors="coerce")
            ok = s.notna()
            ok &= s >= lo if left == "closed" else s > lo
            ok &= s <= hi if right == "closed" else s < hi
            mask &= ok
        elif "<=" in p:
            feat, thr = p.split("<=", 1)
            mask &= pd.to_numeric(df[feat.strip()], errors="coerce") <= float(thr)
        elif ">" in p:
            feat, thr = p.split(">", 1)
            mask &= pd.to_numeric(df[feat.strip()], errors="coerce") > float(thr)
        else:
            mask &= False
    return mask.fillna(False)


def _parse_interval(text: str) -> tuple[float, float, str, str]:
    text = text.strip()
    left = "closed" if text[0] == "[" else "open"
    right = "closed" if text[-1] == "]" else "open"
    body = text[1:-1]
    a, b = body.split(",")
    return float(a), float(b), left, right


def rule_mask_from_specs(df: pd.DataFrame, rule: dict[str, Any]) -> pd.Series:
    specs = rule.get("specs") or []
    if not specs:
        return apply_rule_description(df, rule["description"])
    mask = pd.Series(True, index=df.index)
    for sp in specs:
        mask &= _apply_interval_spec(df, sp)
    return mask.fillna(False)


def confirm_rules(
    disc: pd.DataFrame,
    conf: pd.DataFrame,
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    base_disc = eval_mask(disc, disc["pbv2_baseline"].astype(bool))
    base_conf = eval_mask(conf, conf["pbv2_baseline"].astype(bool))
    confirmed = []
    for rule in rules:
        # NO retuning: frozen Discovery edges on Confirmation
        m_disc = rule_mask_from_specs(disc, rule)
        m_conf = rule_mask_from_specs(conf, rule)
        ev_d = eval_mask(disc, m_disc)
        ev_c = eval_mask(conf, m_conf)
        ok = False
        reasons = []
        if (ev_c["n"] or 0) < MIN_RULE_N_CONF:
            reasons.append("conf_n_low")
        else:
            pnl_ok = (ev_c["net_proxy_pnl"] or -1e18) > (base_conf["net_proxy_pnl"] or 0)
            prec_ok = (ev_c["winner_precision"] or -1) > (base_conf["winner_precision"] or 0)
            stop_ok = (ev_c["stop_rate"] or 1) <= (base_conf["stop_rate"] or 0) + 1e-12
            ok = bool(pnl_ok and prec_ok and stop_ok)
            if not pnl_ok:
                reasons.append("pnl_not_improved")
            if not prec_ok:
                reasons.append("precision_not_improved")
            if not stop_ok:
                reasons.append("stop_rate_worse")
        row = {
            **{k: rule[k] for k in ("rule_id", "features", "description", "kind", "score", "lift_winner")},
            "discovery": ev_d,
            "confirmation": ev_c,
            "baseline_discovery": base_disc,
            "baseline_confirmation": base_conf,
            "confirmed": ok,
            "reject_reasons": reasons,
        }
        confirmed.append(row)
    return confirmed


def run_ml_rules(disc: pd.DataFrame, conf: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    use = [f for f in features if f in disc.columns][:25]
    Xd = disc[use].apply(pd.to_numeric, errors="coerce")
    yd = disc["winner_a"].astype(int)
    med = Xd.median()
    Xd = Xd.fillna(med)
    Xc = conf[use].apply(pd.to_numeric, errors="coerce").fillna(med)
    yc = conf["winner_a"].astype(int)
    out: dict[str, Any] = {"features": use, "logistic": {}, "tree": {}}
    base_conf = eval_mask(conf, conf["pbv2_baseline"].astype(bool))

    # LogisticRegression
    try:
        lr = LogisticRegression(max_iter=500, class_weight="balanced", solver="lbfgs")
        lr.fit(Xd, yd)
        coef = sorted(
            [{"feature": f, "coef": float(c)} for f, c in zip(use, lr.coef_[0])],
            key=lambda x: abs(x["coef"]),
            reverse=True,
        )
        # rule: top-3 positive coef features above discovery median
        pos = [c["feature"] for c in coef if c["coef"] > 0][:3]
        if pos:
            m_d = pd.Series(True, index=disc.index)
            m_c = pd.Series(True, index=conf.index)
            desc_parts = []
            for f in pos:
                thr = float(pd.to_numeric(disc[f], errors="coerce").median())
                m_d &= pd.to_numeric(disc[f], errors="coerce") >= thr
                m_c &= pd.to_numeric(conf[f], errors="coerce") >= thr
                desc_parts.append(f"{f} >= {thr:.6g}")
            desc = " AND ".join(desc_parts)
            ev_d = eval_mask(disc, m_d)
            ev_c = eval_mask(conf, m_c)
            ok = (
                (ev_c["n"] or 0) >= MIN_RULE_N_CONF
                and (ev_c["net_proxy_pnl"] or -1e18) > (base_conf["net_proxy_pnl"] or 0)
                and (ev_c["winner_precision"] or -1) > (base_conf["winner_precision"] or 0)
                and (ev_c["stop_rate"] or 1) <= (base_conf["stop_rate"] or 0) + 1e-12
            )
            out["logistic"] = {
                "coefficients": coef[:10],
                "rule_description": desc,
                "discovery": ev_d,
                "confirmation": ev_c,
                "confirmed": ok,
            }
        else:
            out["logistic"] = {"coefficients": coef[:10], "confirmed": False, "note": "no_positive_coefs"}
    except Exception as e:
        out["logistic"] = {"error": str(e), "confirmed": False}

    # DecisionTree depth<=3
    try:
        dt = DecisionTreeClassifier(max_depth=3, min_samples_leaf=15, class_weight="balanced", random_state=42)
        dt.fit(Xd, yd)
        tree_rules = extract_tree_rules(dt, use)
        best = None
        for desc in tree_rules:
            m_d = apply_rule_description(disc, desc)
            m_c = apply_rule_description(conf, desc)
            # tree inequalities use raw thresholds; apply_rule_description handles <=/>
            ev_d = eval_mask(disc, m_d)
            ev_c = eval_mask(conf, m_c)
            ok = (
                (ev_c["n"] or 0) >= MIN_RULE_N_CONF
                and (ev_c["net_proxy_pnl"] or -1e18) > (base_conf["net_proxy_pnl"] or 0)
                and (ev_c["winner_precision"] or -1) > (base_conf["winner_precision"] or 0)
                and (ev_c["stop_rate"] or 1) <= (base_conf["stop_rate"] or 0) + 1e-12
            )
            cand = {
                "description": desc,
                "discovery": ev_d,
                "confirmation": ev_c,
                "confirmed": ok,
            }
            if best is None or (ev_c.get("winner_precision") or 0) > (best["confirmation"].get("winner_precision") or 0):
                best = cand
        out["tree"] = {"rules_extracted": tree_rules, "best": best, "confirmed": bool(best and best["confirmed"])}
    except Exception as e:
        out["tree"] = {"error": str(e), "confirmed": False}

    return out


def main() -> int:
    TMP.mkdir(parents=True, exist_ok=True)
    SNAP_CACHE.mkdir(parents=True, exist_ok=True)
    print("W47 classify MARKET_DATA_DAY...", flush=True)
    market_days = classify_market_data_days()
    note_days = None
    if len(market_days) < TARGET_DAYS:
        note_days = f"Only {len(market_days)} days with >={MIN_SYMBOLS} symbols; using all available."
        print(note_days, flush=True)
    else:
        print(f"MARKET_DATA_DAY n={len(market_days)}", flush=True)

    # expandable: only days with universe
    buildable = [m for m in market_days if m["universe_ok"]]
    print(f"buildable with universe: {len(buildable)} / {len(market_days)}", flush=True)

    all_snaps = []
    for meta in buildable:
        df = load_or_build_day_snap(meta)
        if not df.empty:
            all_snaps.append(df)
            print(f"  {meta['day']} snaps={len(df)}", flush=True)

    if not all_snaps:
        result = {
            "error": "no_snapshots",
            "market_data_days": [m["date"] for m in market_days],
            "confirmed_rules_count": 0,
            "note": note_days,
        }
        _wj(TMP / "winner_trigger_results.json", result)
        print("CONFIRMED_RULES_COUNT=0", flush=True)
        return 1

    snaps = pd.concat(all_snaps, ignore_index=True)
    print(f"total snaps={len(snaps)} - building independent moves...", flush=True)
    moves = build_independent_moves_from_snaps(snaps)
    moves = attach_candidate_seen(moves)
    print(f"independent moves={len(moves)}", flush=True)

    labeled = labeled_snapshot_rows(snaps)
    print(f"labeled snapshots={len(labeled)} (mixed outcome universe)", flush=True)
    frame = dedupe_analysis_frame(labeled, moves)
    frame = label_outcomes(frame)
    if "candidate_seen" not in frame.columns:
        frame["candidate_seen"] = np.nan
    frame = assign_pbv2_baseline(frame)
    print(
        f"analysis frame n={len(frame)} winners={int(frame['winner_a'].sum())} "
        f"stops={int(frame['stop_proxy'].fillna(False).sum())} "
        f"winner_rate={float(frame['winner_a'].mean()):.4f} "
        f"pbv2_mode={frame['pbv2_baseline_mode'].iloc[0] if len(frame) else None}",
        flush=True,
    )

    # Discovery / Confirmation split by day (older half / newer half)
    day_list = sorted(frame["trading_date"].astype(str).unique())
    mid = len(day_list) // 2
    disc_days = set(day_list[:mid] if mid else day_list[:1])
    conf_days = set(day_list[mid:] if mid else day_list[-1:])
    if not conf_days:
        conf_days = set(day_list[-1:])
    if disc_days & conf_days and len(day_list) >= 2:
        # ensure disjoint when possible
        conf_days = set(day_list[mid:])
        disc_days = set(day_list[:mid])
    disc = frame[frame["trading_date"].astype(str).isin(disc_days)].copy()
    conf = frame[frame["trading_date"].astype(str).isin(conf_days)].copy()
    print(f"Discovery days={sorted(disc_days)} n={len(disc)}", flush=True)
    print(f"Confirmation days={sorted(conf_days)} n={len(conf)}", flush=True)

    features = numeric_feature_cols(disc)
    print(f"numeric features={len(features)} - Stage1...", flush=True)
    stage1_rows, top_feats = stage1_quantile_bins(disc, features)
    print(f"Stage2 top features={top_feats[:10]}...", flush=True)
    stage2_rules = stage2_two_feature_rules(disc, top_feats)
    print(f"Stage2 candidate rules={len(stage2_rules)} - promote top {TOP_RULES_PROMOTE}", flush=True)
    promoted = stage2_rules[:TOP_RULES_PROMOTE]
    confirmed_rows = confirm_rules(disc, conf, promoted)
    n_confirmed = sum(1 for r in confirmed_rows if r["confirmed"])

    print("ML Logistic / Tree...", flush=True)
    ml = run_ml_rules(disc, conf, top_feats)
    if ml.get("logistic", {}).get("confirmed"):
        n_confirmed += 1
    if ml.get("tree", {}).get("confirmed"):
        n_confirmed += 1

    base_disc = eval_mask(disc, disc["pbv2_baseline"].astype(bool))
    base_conf = eval_mask(conf, conf["pbv2_baseline"].astype(bool))
    overall = eval_mask(frame, pd.Series(True, index=frame.index))

    # optionally fold W43B outcome dataset summary (actual trades)
    w43b_note = None
    w43b_path = OUT / "w43b_20260717_entry_outcome_dataset.csv"
    if w43b_path.is_file():
        b = pd.read_csv(w43b_path)
        w43b_note = {
            "n_rows": int(len(b)),
            "outcome_counts": b["outcome"].value_counts().to_dict() if "outcome" in b.columns else {},
            "used_as": "reference_only_actual_trades_not_merged_into_trigger_search",
        }

    result = {
        "phase": "W47_winner_trigger_search",
        "runtime_trading_conditions_modified": False,
        "market_data_days": [
            {"date": m["date"], "push_files": m["push_files"], "universe_ok": m["universe_ok"]}
            for m in market_days
        ],
        "market_data_days_count": len(market_days),
        "target_days": TARGET_DAYS,
        "insufficient_20_days_note": note_days,
        "buildable_days": [m["date"] for m in buildable],
        "days_in_frame": day_list,
        "discovery_days": sorted(disc_days),
        "confirmation_days": sorted(conf_days),
        "n_snaps": int(len(snaps)),
        "n_independent_moves": int(len(moves)),
        "n_labeled_snapshots": int(len(labeled)),
        "n_analysis_frame": int(len(frame)),
        "universe_note": (
            "Analysis uses valid-labeled 30s snapshots (mixed outcomes). "
            "Independent moves (LARGE_RISE collapses) are expandable/deduped but "
            "not used alone because LARGE_RISE == WINNER_A by definition."
        ),
        "label_defs": {
            "WINNER_A": "future_30m_mfe>=1.0 AND future_30m_return>=0.5",
            "STOP_PROXY": "future_30m_mae<=-1.2",
            "NO_PROGRESS_PROXY": "not winner, not stop, mfe<0.5, |ret|<0.3 (skipped when mae missing)",
        },
        "label_counts": {
            "winner_a": int(frame["winner_a"].sum()),
            "stop_proxy": int(frame["stop_proxy"].fillna(False).sum()),
            "no_progress_proxy": int(pd.Series(frame["no_progress_proxy"]).fillna(False).astype(bool).sum()),
        },
        "overall": overall,
        "pbv2_baseline_mode": str(frame["pbv2_baseline_mode"].iloc[0]) if len(frame) else None,
        "baseline_discovery": base_disc,
        "baseline_confirmation": base_conf,
        "stage1_top_features": top_feats,
        "stage1_bin_rows_n": len(stage1_rows),
        "stage1_bin_sample": stage1_rows[:40],
        "stage2_promoted_rules": confirmed_rows,
        "ml": ml,
        "confirmed_rules": [r for r in confirmed_rows if r["confirmed"]]
        + (
            [
                {
                    "rule_id": "ML::logistic",
                    "description": ml["logistic"].get("rule_description"),
                    "confirmation": ml["logistic"].get("confirmation"),
                    "confirmed": True,
                    "kind": "logistic",
                }
            ]
            if ml.get("logistic", {}).get("confirmed")
            else []
        )
        + (
            [
                {
                    "rule_id": "ML::tree",
                    "description": (ml.get("tree") or {}).get("best", {}).get("description"),
                    "confirmation": (ml.get("tree") or {}).get("best", {}).get("confirmation"),
                    "confirmed": True,
                    "kind": "decision_tree",
                }
            ]
            if ml.get("tree", {}).get("confirmed")
            else []
        ),
        "confirmed_rules_count": n_confirmed,
        "w43b_reference": w43b_note,
        "max_workers": MAX_WORKERS,
    }
    out_path = TMP / "winner_trigger_results.json"
    _wj(out_path, result)
    # also save stage1 full + frame summary
    pd.DataFrame(stage1_rows).to_csv(TMP / "stage1_quantile_bins.csv", index=False)
    frame[
        [
            c
            for c in [
                "trading_date",
                "symbol",
                "independent_move_id",
                "source",
                "winner_a",
                "stop_proxy",
                "no_progress_proxy",
                "future_30m_return",
                "future_30m_mfe",
                "future_30m_mae",
                "proxy_pnl",
                "pbv2_baseline",
                "score_proxy",
            ]
            if c in frame.columns
        ]
    ].to_csv(TMP / "analysis_frame_labels.csv", index=False)

    print("=" * 60, flush=True)
    print(f"RESULT_PATH={out_path}", flush=True)
    print(f"CONFIRMED_RULES_COUNT={n_confirmed}", flush=True)
    print(
        f"frame={len(frame)} disc={len(disc)} conf={len(conf)} "
        f"winner_rate={overall['winner_rate']:.4f} stop_rate={overall['stop_rate']:.4f}",
        flush=True,
    )
    print(
        f"baseline_conf n={base_conf['n']} prec={base_conf['winner_precision']} "
        f"pnl={base_conf['net_proxy_pnl']} stop={base_conf['stop_rate']}",
        flush=True,
    )
    for r in result["confirmed_rules"]:
        print(f"  CONFIRMED: {r.get('description')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

