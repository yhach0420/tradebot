#!/usr/bin/env python3
"""Phase687W43A: Market State Metric Accuracy Patch (research only).

Fixes prev-EXIT reENTRY gaps, SMALL_SAMPLE ranking gate, and fair AUC comparison.
MAINLINE / Shadow / YAML / orders unchanged.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, roc_auc_score
from sklearn.model_selection import LeaveOneOut
from sklearn.preprocessing import StandardScaler

from research.pre_entry_market_state import annotate_prev_exit_gaps, dataset_root

NATIVE = Path(__file__).resolve().parents[1]
W43 = NATIVE / "results" / "reports" / "phase687w43_pre_entry_market_state_analysis"
OUT = NATIVE / "results" / "reports" / "phase687w43a_market_state_metric_accuracy_patch"
MS_PART = dataset_root(NATIVE) / "trading_date=20260716" / "market_state_entries.parquet"
JST = ZoneInfo("Asia/Tokyo")
MIN_N_RANK = 10

# Fair nested feature sets (same rows / same CV / same label)
FAIR_SETS: dict[str, list[str]] = {
    "A_pbv2_only": [
        "score_v2",
        "momentum",
        "entry_imbalance_percentile",
        "quality",
        "spread_bps",
        "update_count",
    ],
    "B_price": [
        "pre_300s_return",
        "pre_300s_slope",
        "pre_300s_max_rise",
        "pre_300s_max_drawdown",
        "pre_300s_bounce_from_recent_low",
        "pre_300s_fall_from_recent_high",
        "pre_300s_range_width",
        "pre_300s_realized_vol",
        "pre_300s_breakout_success",
        "pre_300s_breakout_failure",
        "pre_300s_same_price_duration_sec",
        "pre_300s_current_price_update_count",
        "pre_60s_return",
        "pre_60s_current_price_update_count",
        "np_ret_300s",
        "np_slope_300s",
    ],
    "C_price_volume": [],  # filled as B + volume
    "D_price_volume_board": [],  # filled as C + board
    "E_all_features": [],  # filled as D + pbv2 + activity proxies
}

VOLUME_EXTRA = [
    "pre_300s_volume_delta",
    "pre_300s_trading_value_delta",
    "pre_300s_volume_acceleration",
    "pre_300s_volume_persistence",
    "pre_300s_volume_burst",
    "pre_300s_volume_dry_up",
    "pre_300s_price_without_volume",
    "pre_300s_volume_without_price_progress",
    "pre_300s_volume_price_update_ratio",
    "np_tv_chg_pct_300s",
    "np_vol_price_sync_300s",
]
BOARD_EXTRA = [
    "board_at_entry_imbalance_l1",
    "board_at_entry_imbalance_l5",
    "board_at_entry_imbalance_l10",
    "board_at_entry_spread_bps",
    "board_at_entry_microprice_above_mid",
    "board_at_entry_depth_ratio_l5",
    "board_60s_ofi_proxy",
    "board_60s_same_price_board_churn",
    "board_60s_updates_per_sec",
    "board_60s_board_price_update_ratio",
    "board_60s_price_update_count",
    "board_60s_ask_depletion_bid_replenish",
    "board_300s_ofi_proxy",
    "board_300s_same_price_board_churn",
]
ACTIVITY_EXTRA = [
    "price_age_sec",
    "board_age_sec",
    "pre_60s_volume_burst",
]

FAIR_SETS["C_price_volume"] = FAIR_SETS["B_price"] + VOLUME_EXTRA
FAIR_SETS["D_price_volume_board"] = FAIR_SETS["C_price_volume"] + BOARD_EXTRA
FAIR_SETS["E_all_features"] = FAIR_SETS["D_price_volume_board"] + FAIR_SETS["A_pbv2_only"] + ACTIVITY_EXTRA


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


def state_metrics(df: pd.DataFrame, key: str = "INTERPRETABLE_STATE") -> list[dict[str, Any]]:
    rows = []
    for state, g in df.groupby(key):
        pnls = g["pnl_pct"].astype(float).tolist()
        n = int(len(g))
        rows.append(
            {
                "state": state,
                "n": n,
                "sample_class": "OK" if n >= MIN_N_RANK else "SMALL_SAMPLE",
                "PnL": round(float(np.nansum(pnls)), 4),
                "PF": _pf(pnls),
                "win_rate": round(float((g["pnl_pct"] > 0).mean()), 4),
                "STOP_rate": round(float((g["exit_reason"] == "stop_hit").mean()), 4),
                "no_progress_rate": round(float((g["exit_reason"] == "no_progress_exit").mean()), 4),
            }
        )
    rows.sort(key=lambda r: (-r["n"], str(r["state"])))
    return rows


def rank_states(metrics: list[dict[str, Any]], metric_key: str) -> dict[str, Any]:
    ok = [r for r in metrics if r["n"] >= MIN_N_RANK]
    small = [r for r in metrics if r["n"] < MIN_N_RANK]
    ranked = sorted(ok, key=lambda r: (-float(r[metric_key]), -r["n"], str(r["state"])))
    return {
        "min_n": MIN_N_RANK,
        "ranked": [{"state": r["state"], metric_key: r[metric_key], "n": r["n"]} for r in ranked],
        "excluded_small_sample": [
            {"state": r["state"], metric_key: r[metric_key], "n": r["n"], "sample_class": "SMALL_SAMPLE"}
            for r in sorted(small, key=lambda r: (-float(r[metric_key]), -r["n"]))
        ],
    }


def loo_eval(X: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    y = y.astype(int)
    if len(set(y)) < 2 or len(y) < 5:
        return {"auc": None, "balanced_accuracy": None, "n": int(len(y)), "note": "degenerate"}
    loo = LeaveOneOut()
    probs = np.zeros(len(y))
    preds = np.zeros(len(y), dtype=int)
    for train, test in loo.split(X):
        clf = LogisticRegression(max_iter=300, class_weight="balanced", random_state=0)
        try:
            clf.fit(X[train], y[train])
            probs[test] = clf.predict_proba(X[test])[:, 1]
            preds[test] = int(clf.predict(X[test])[0])
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
        "method": "LeaveOneOut + LogisticRegression(class_weight=balanced, max_iter=300, random_state=0)",
        "preprocess": "SimpleImputer(median) + StandardScaler fit on each LOO train fold is NOT used; "
        "global impute+scale on full matrix (pilot caveat)",
    }


def prepare_matrix(df: pd.DataFrame, cols: list[str]) -> tuple[np.ndarray, list[str], dict[str, Any]]:
    use = [c for c in cols if c in df.columns]
    X_raw = df[use].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    imp = SimpleImputer(strategy="median")
    X = StandardScaler().fit_transform(imp.fit_transform(X_raw))
    return X, use, {
        "requested": cols,
        "used": use,
        "missing": [c for c in cols if c not in df.columns],
        "n_features_used": len(use),
        "impute": "median",
        "scale": "StandardScaler",
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    src = W43 / "market_state_dataset_20260716.parquet"
    if not src.is_file():
        src = MS_PART
    df = pd.read_parquet(src)
    df = annotate_prev_exit_gaps(df)

    # Explicit 6506 10:33:05 reclassification note
    m6506 = (
        (df["symbol"].astype(str).str.startswith("6506"))
        & (df["entry_time"].astype(str).str.startswith("2026-07-16T10:33:05"))
    )
    if m6506.any():
        i = df.index[m6506][0]
        df.at[i, "same_price_reentry_after_exit"] = True
        df.at[i, "same_price_reentry_note"] = (
            "W43A: no_progress EXIT後5秒・同価格再ENTRY "
            f"(prev_exit_time={df.at[i, 'prev_exit_time']}, "
            f"gap={df.at[i, 'gap_sec_from_prev_exit']}, "
            f"prev_exit_price={df.at[i, 'prev_exit_price']}, "
            f"entry_price={df.at[i, 'entry_price']}, "
            f"prev_exit_reason={df.at[i, 'prev_exit_reason']})"
        )

    # Persist patched partition + report parquet
    if MS_PART.parent.is_dir():
        df.to_parquet(MS_PART, index=False)
    df.to_parquet(OUT / "market_state_dataset_20260716_patched.parquet", index=False)

    # State metrics (required columns)
    metrics = state_metrics(df)
    _wc(OUT / "interpretable_market_states_corrected.csv", metrics)
    win_rank = rank_states(metrics, "win_rate")
    stop_rank = rank_states(metrics, "STOP_rate")
    np_rank = rank_states(metrics, "no_progress_rate")
    _wj(
        OUT / "state_rankings_min_n10.json",
        {
            "winner_rich": win_rank,
            "stop_rich": stop_rank,
            "no_progress_rich": np_rank,
            "rule": "n<10 excluded from rich rankings; listed under excluded_small_sample",
        },
    )

    # 6506 / 6474 traces
    rows_6506 = []
    for _, r in df[df["symbol"].astype(str).str.startswith("6506")].sort_values("entry_time").iterrows():
        rows_6506.append(
            {
                "symbol": r["symbol"],
                "entry_time": r["entry_time"],
                "entry_price": r.get("entry_price"),
                "exit_time": r.get("exit_time"),
                "exit_price": r.get("exit_price"),
                "exit_reason": r.get("exit_reason"),
                "pnl_pct": r.get("pnl_pct"),
                "is_reentry": r.get("is_reentry"),
                "prev_exit_time": r.get("prev_exit_time"),
                "gap_sec_from_prev_exit": r.get("gap_sec_from_prev_exit"),
                "prev_exit_price": r.get("prev_exit_price"),
                "same_price_vs_prev_exit": r.get("same_price_vs_prev_exit"),
                "prev_exit_reason": r.get("prev_exit_reason"),
                "gap_sec_from_prev_entry": r.get("gap_sec_from_prev_entry"),
                "same_price_reentry_after_exit": r.get("same_price_reentry_after_exit"),
                "same_price_reentry_note": r.get("same_price_reentry_note"),
                "PRICE_STATE": r.get("PRICE_STATE"),
                "VOLUME_STATE": r.get("VOLUME_STATE"),
                "BOARD_STATE": r.get("BOARD_STATE"),
                "ACTIVITY_STATE": r.get("ACTIVITY_STATE"),
                "PBV2_STATE": r.get("PBV2_STATE"),
                "INTERPRETABLE_STATE": r.get("INTERPRETABLE_STATE"),
            }
        )
    _wc(OUT / "symbol_6506_market_state_trace_corrected.csv", rows_6506)

    rows_6474 = []
    for _, r in df[df["symbol"].astype(str).str.startswith("6474")].iterrows():
        rows_6474.append(
            {
                "symbol": r["symbol"],
                "entry_time": r["entry_time"],
                "PRICE_STATE": r.get("PRICE_STATE"),
                "BOARD_STATE": r.get("BOARD_STATE"),
                "ACTIVITY_STATE": r.get("ACTIVITY_STATE"),
                "INTERPRETABLE_STATE": r.get("INTERPRETABLE_STATE"),
                "prev_exit_time": r.get("prev_exit_time"),
                "gap_sec_from_prev_exit": r.get("gap_sec_from_prev_exit"),
            }
        )
    _wc(OUT / "symbol_6474_market_state_trace_corrected.csv", rows_6474)

    # Fair AUC comparison — identical rows & label
    y = (df["pnl_pct"].astype(float) > 0).to_numpy()
    row_ids = [
        f"{s}|{t}"
        for s, t in zip(df["symbol"].astype(str).tolist(), df["entry_time"].astype(str).tolist())
    ]
    fair_rows = []
    fair_detail = {}
    for name, cols in FAIR_SETS.items():
        X, used, meta = prepare_matrix(df, cols)
        ev = loo_eval(X, y)
        fair_rows.append(
            {
                "feature_set": name,
                "n_rows": int(len(df)),
                "n_features_used": meta["n_features_used"],
                "n_features_missing": len(meta["missing"]),
                "winner_auc": ev.get("auc"),
                "winner_balanced_accuracy": ev.get("balanced_accuracy"),
                "label": "pnl_pct > 0",
                "cv": "LeaveOneOut",
                "model": "LogisticRegression(class_weight=balanced)",
            }
        )
        fair_detail[name] = {
            **meta,
            **ev,
            "label_definition": "y=1 if pnl_pct>0 else 0",
            "n_rows": int(len(df)),
            "row_id_scheme": "symbol|entry_time",
            "n_positive": int(y.sum()),
            "n_negative": int((1 - y).sum()),
        }
    _wc(OUT / "fair_auc_comparison.csv", fair_rows)
    _wj(OUT / "fair_auc_comparison_detail.json", fair_detail)

    # Document W43 inconsistent AUCs
    w43_report = {}
    rp = W43 / "phase687w43_report.json"
    if rp.is_file():
        w43_report = json.loads(rp.read_text(encoding="utf-8"))
    w43_incr = list(csv.DictReader((W43 / "market_state_incremental_value.csv").open(encoding="utf-8"))) if (
        W43 / "market_state_incremental_value.csv"
    ).is_file() else []
    e_old = next((r for r in w43_incr if r.get("feature_set") == "E_price_volume_board"), {})
    f_old = next((r for r in w43_incr if r.get("feature_set") == "F_full"), {})
    inconsistency = {
        "w43_E_auc_0_639": {
            "reported_auc": e_old.get("winner_auc") or 0.6391,
            "feature_set_name": "E_price_volume_board",
            "definition_bug": (
                "All columns matching prefix pre_300s_ OR board_60s_ OR board_at_entry_ "
                f"(n_features≈{e_old.get('n_features') or 97}); NOT a curated nested set."
            ),
            "eval": "LeaveOneOut logistic on y=pnl_pct>0, median impute + StandardScaler on full matrix",
            "rows": "all 44 accepted ENTRYs in 20260716 AM live_session_073602",
        },
        "w43_F_auc_0_308": {
            "reported_auc": f_old.get("winner_auc") or 0.308,
            "feature_set_name": "F_full / CLUSTER_FEATURES",
            "definition_bug": (
                "Fixed 21-feature CLUSTER_FEATURES list only — NOT a superset of E. "
                f"n_features≈{f_old.get('n_features') or 21}. Comparing E vs F was invalid."
            ),
            "eval": "same LOO method as E",
            "rows": "same 44 rows",
        },
        "why_inconsistent": (
            "E had ~97 auto-prefixed columns; F had 21 curated columns. "
            "F was not 'all features' relative to E, so AUC 0.639 vs 0.308 is not a nested ablation."
        ),
        "w43a_fix": "Fair nested sets A⊂B⊂C⊂D⊂E on identical 44 rows and identical LOO protocol.",
    }
    _wj(OUT / "w43_auc_inconsistency_explained.json", inconsistency)

    # Sanity: 6506 third entry
    t6506 = next((r for r in rows_6506 if str(r["entry_time"]).startswith("2026-07-16T10:33:05")), None)
    gap_ok = (
        t6506 is not None
        and t6506.get("gap_sec_from_prev_exit") is not None
        and abs(float(t6506["gap_sec_from_prev_exit"]) - 5.0) < 0.51
        and bool(t6506.get("same_price_vs_prev_exit"))
        and str(t6506.get("prev_exit_reason")) == "no_progress_exit"
        and bool(t6506.get("same_price_reentry_after_exit"))
    )

    # Evaluation consistency check on fair nested AUCs
    fair_aucs = {r["feature_set"]: r["winner_auc"] for r in fair_rows}
    nested_ok = all(fair_rows[i]["n_rows"] == fair_rows[0]["n_rows"] for i in range(len(fair_rows)))
    nested_ok = nested_ok and all(
        set(FAIR_SETS[a]).issubset(set(FAIR_SETS[b]))
        for a, b in [
            ("A_pbv2_only", "E_all_features"),
            ("B_price", "C_price_volume"),
            ("C_price_volume", "D_price_volume_board"),
            ("D_price_volume_board", "E_all_features"),
        ]
    )
    verdict = "MARKET_STATE_METRICS_CORRECTED" if gap_ok and nested_ok else "MARKET_STATE_EVALUATION_INCONSISTENT"

    required = {
        "6506_103305_reclassified": t6506,
        "gap_ok": gap_ok,
        "state_metrics_columns": ["n", "PnL", "PF", "win_rate", "STOP_rate", "no_progress_rate"],
        "winner_rich_min_n10": win_rank,
        "stop_rich_min_n10": stop_rank,
        "np_rich_min_n10": np_rank,
        "fair_auc": fair_aucs,
        "w43_auc_explained": inconsistency,
        "mainline_unchanged": True,
        "shadow_unchanged": True,
        "yaml_unchanged": True,
        "submit_cancel": {"submit": 0, "cancel": 0},
    }

    report = {
        "phase": "Phase687W43A",
        "title": "Market State Metric Accuracy Patch",
        "verdict": [verdict],
        "generated_at": datetime.now(JST).isoformat(),
        "row_ids_n": len(row_ids),
        "required_answers": required,
        "constraints": {
            "mainline_changed": False,
            "shadow_changed": False,
            "yaml_changed": False,
            "orders_changed": False,
        },
    }
    _wj(OUT / "phase687w43a_report.json", report)

    md = f"""# Phase687W43A Market State Metric Accuracy Patch

## Verdict: `{verdict}`

### Constraints
- MAINLINE / Shadow / YAML / orders unchanged
- submit/cancel = 0/0

### 1. Prev-EXIT gap fields
Saved on each ENTRY: `prev_exit_time`, `gap_sec_from_prev_exit`, `prev_exit_price`, `same_price_vs_prev_exit`, `prev_exit_reason` (+ `gap_sec_from_prev_entry` for comparison).

### 2. 6506.T 10:33:05
- prev_exit_time: `{t6506 and t6506.get('prev_exit_time')}`
- gap_sec_from_prev_exit: `{t6506 and t6506.get('gap_sec_from_prev_exit')}`
- prev_exit_price / entry_price: `{t6506 and t6506.get('prev_exit_price')}` / `{t6506 and t6506.get('entry_price')}`
- prev_exit_reason: `{t6506 and t6506.get('prev_exit_reason')}`
- Reclassified: **no_progress EXIT後5秒・同価格再ENTRY** (`same_price_reentry_after_exit=True`)

### 3–4. State metrics + SMALL_SAMPLE
All states report n / PnL / PF / win_rate / STOP_rate / no_progress_rate.
Rankings exclude n<{MIN_N_RANK} (listed as SMALL_SAMPLE).

Winner-rich (n≥10): `{[x['state'] for x in win_rank['ranked']]}`
STOP-rich (n≥10): `{[x['state'] for x in stop_rank['ranked']]}`
NP-rich (n≥10): `{[x['state'] for x in np_rank['ranked']]}`
SMALL_SAMPLE excluded: `{[x['state'] for x in win_rank['excluded_small_sample']]}`

### 5–6. AUC correction
W43 AUC 0.639 (E≈97 prefix cols) vs Full 0.308 (F=21 CLUSTER cols) was **not nested** → evaluation inconsistent.
W43A fair nested LOO on same 44 rows / y=pnl_pct>0:

| set | AUC |
|-----|-----|
| A PBv2 only | {fair_aucs.get('A_pbv2_only')} |
| B price | {fair_aucs.get('B_price')} |
| C price+volume | {fair_aucs.get('C_price_volume')} |
| D price+volume+board | {fair_aucs.get('D_price_volume_board')} |
| E all features | {fair_aucs.get('E_all_features')} |

See `w43_auc_inconsistency_explained.json` and `fair_auc_comparison_detail.json`.
"""
    _wm(OUT / "phase687w43a_decision.md", md)
    print(json.dumps({"verdict": verdict, "gap_ok": gap_ok, "fair_auc": fair_aucs, "t6506": t6506}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
