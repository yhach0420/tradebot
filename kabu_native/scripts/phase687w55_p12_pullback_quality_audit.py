#!/usr/bin/env python3
"""Phase687W55-P12 — Pullback Quality Audit (analysis only).

Can market-state quality features separate collapse vs healthy pullback
without AM/PM, beyond position-only (rise5<0 & vwap_dev<0)?

No runtime / Shadow / YAML / PBv2 changes.
Outputs:
  pullback_quality_audit_report.md / .json / pullback_quality_audit.xlsx
"""

from __future__ import annotations

import importlib.util
import json
import math
import shutil
import sys
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows

warnings.filterwarnings("ignore")

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE))

OUT = NATIVE / "results" / "research" / "pre_entry_market_state"
JST = __import__("zoneinfo").ZoneInfo("Asia/Tokyo")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


w55 = _load_module("w55_p12", NATIVE / "scripts" / "phase687w55_pullback_ampm_interaction_audit.py")
w53 = w55.w53

DISC = w55.DISC
CONF = w55.CONF
ALL_DAYS = w55.ALL_DAYS
SNAP_CACHE = w55.SNAP_CACHE


def _f(v: Any, default: float = np.nan) -> float:
    return w55._f(v, default)


def _pf(xs) -> Optional[float]:
    return w55._pf(xs)


def _excel_cell(v: Any) -> Any:
    return w55._excel_cell(v)


def write_xlsx(sheets: dict[str, pd.DataFrame], path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    ws.append(["Phase687W55-P12 Pullback Quality Audit"])
    ws.append(["generated", datetime.now(JST).isoformat()])
    ws.append(["runtime_unchanged", "true"])
    for name, df in sheets.items():
        w = wb.create_sheet(str(name)[:31])
        if df is None or getattr(df, "empty", True):
            w.append(["empty"])
            continue
        clean = df.head(50000).copy()
        for c in clean.columns:
            clean[c] = clean[c].map(_excel_cell)
        for r in dataframe_to_rows(clean, index=False, header=True):
            w.append([_excel_cell(x) for x in r])
        w.auto_filter.ref = w.dimensions
        w.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _mask_q(s: pd.Series, side: str, q: float, train: pd.Series) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    tr = pd.to_numeric(train, errors="coerce")
    thr = tr.quantile(q)
    if side == "high":
        return x >= thr
    return x <= thr


def build_dataset() -> pd.DataFrame:
    frames = []
    for i, day in enumerate(ALL_DAYS):
        print(f"  [{i+1}/{len(ALL_DAYS)}] trades {day}", flush=True)
        t = w55.load_day_trades(day)
        if not t.empty:
            frames.append(t)
    if not frames:
        return pd.DataFrame()
    trades = pd.concat(frames, ignore_index=True)
    trades = w55.apply_shadow(trades)
    print("  loading snaps...", flush=True)
    panel = w55.load_panel_features()
    df = w55.join_features(trades, panel)
    df = w55.add_labels(df)
    # quality features (entry-time only; no future leak)
    df["fall_from_recent_high"] = pd.to_numeric(df.get("fall_from_high_300s"), errors="coerce")
    df["bounce_from_recent_low"] = pd.to_numeric(df.get("bounce_from_low_300s"), errors="coerce")
    df["seconds_since_last_high"] = pd.to_numeric(df.get("seconds_since_last_new_high"), errors="coerce")
    nh = pd.to_numeric(df.get("pre_300s_new_high_count"), errors="coerce")
    high_upd = pd.to_numeric(df.get("high_update_count_30s"), errors="coerce")
    df["high_update_restart"] = ((df["seconds_since_last_high"] <= 60) | (high_upd.fillna(0) > 0) | (nh.fillna(0) >= 1)).astype(float)
    reclaim = pd.to_numeric(df.get("vwap_reclaim_flag"), errors="coerce")
    # snap reclaim may be 0/1; also allow distance recovering toward VWAP from below
    dist = pd.to_numeric(df.get("distance_from_vwap"), errors="coerce")
    df["vwap_reclaim"] = np.where(reclaim.notna(), (reclaim > 0).astype(float), (dist >= -0.05).astype(float))
    imb_chg = pd.to_numeric(df.get("imbalance_chg_60s"), errors="coerce")
    df["board_improvement"] = (imb_chg > 0).astype(float)
    bid = pd.to_numeric(df.get("net_bid_pressure_60s"), errors="coerce")
    df["bid_pressure"] = bid
    df["volume_acceleration"] = pd.to_numeric(df.get("vol_accel_300s"), errors="coerce")
    df["volume_persistence"] = pd.to_numeric(df.get("vol_persistence_300s"), errors="coerce")
    df["sector_strength"] = pd.to_numeric(df.get("sector_rel_strength"), errors="coerce")

    # STOP Risk + Winner Enrichment via W53 (fit on Discovery only)
    fit = set(DISC)
    # ensure columns w53 expects
    if "net_ask_pressure_60s" not in df.columns:
        df["net_ask_pressure_60s"] = -pd.to_numeric(df.get("net_bid_pressure_60s"), errors="coerce")
    scored = w53.add_scores(df, fit_days=fit)
    df["stop_risk"] = pd.to_numeric(scored.get("stop_risk_score"), errors="coerce")
    df["winner_enrichment"] = pd.to_numeric(scored.get("winner_enrichment_score"), errors="coerce")
    # volume persistence change proxy: persistence vs accel (higher accel relative = recovering)
    df["volume_persistence_change"] = pd.to_numeric(df["volume_acceleration"], errors="coerce") - 0.0
    # quality class among hits: A collapse vs B healthy (reaccel); drop C for split eval
    df["quality_label"] = np.where(
        df["pullback_class"] == "A_collapse",
        "collapse",
        np.where(df["pullback_class"] == "B_reaccel", "healthy", "neutral"),
    )
    return df


def auc_binary(scores: np.ndarray, y: np.ndarray) -> Optional[float]:
    pos = scores[y == 1]
    neg = scores[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    ranks = pd.Series(np.concatenate([pos, neg])).rank().values
    r_pos = ranks[: len(pos)].sum()
    return float((r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def eval_mask(df: pd.DataFrame, mask: pd.Series, name: str, *, split: str) -> dict:
    sub = df[mask.fillna(False)]
    if sub.empty:
        return {"name": name, "split": split, "n": 0}
    pnl = pd.to_numeric(sub["runtime_exit_pnl"], errors="coerce")
    y_collapse = (sub["quality_label"] == "collapse").astype(int)
    y_healthy = (sub["quality_label"] == "healthy").astype(int)
    return {
        "name": name,
        "split": split,
        "n": int(len(sub)),
        "days": int(sub["trading_date"].nunique()),
        "symbols": int(sub["symbol"].nunique()),
        "collapse_n": int(y_collapse.sum()),
        "healthy_n": int(y_healthy.sum()),
        "collapse_rate": float(y_collapse.mean()),
        "healthy_rate": float(y_healthy.mean()),
        "stop_rate": float(sub["hit_stop_1p2"].mean()),
        "winner_rate": float(sub["hit_winner_threshold"].mean()),
        "mean_pnl": float(pnl.mean()) if pnl.notna().any() else None,
        "pf": _pf(pnl),
        "mean_pnl_5bps": float((pnl - 0.05).mean()) if pnl.notna().any() else None,
    }


def required_combos(train: pd.DataFrame, test: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    """Discovery quantiles frozen → Confirmation. No AM/PM."""
    hit_tr = train["pullback_misread_shadow_hit"].fillna(False)
    hit_te = test["pullback_misread_shadow_hit"].fillna(False)

    def mq(df, col, side, q, train_df):
        if col not in df.columns or col not in train_df.columns:
            return pd.Series(False, index=df.index)
        return _mask_q(df[col], side, q, train_df[col])

    # Healthy-side signals (permit-like): board up, reclaim, sector high, vol accel high
    # Collapse-side (reject-like): inverse
    defs = [
        ("1_pb_x_board_impr", lambda d, tr, hit: hit & (pd.to_numeric(d["board_improvement"], errors="coerce") > 0)),
        ("2_pb_x_vwap_reclaim", lambda d, tr, hit: hit & (pd.to_numeric(d["vwap_reclaim"], errors="coerce") > 0)),
        ("3_pb_x_sector_str", lambda d, tr, hit: hit & mq(d, "sector_strength", "high", 0.7, tr)),
        ("4_pb_x_vol_accel", lambda d, tr, hit: hit & mq(d, "volume_acceleration", "high", 0.7, tr)),
        (
            "5_pb_x_board_x_vwap",
            lambda d, tr, hit: hit
            & (pd.to_numeric(d["board_improvement"], errors="coerce") > 0)
            & (pd.to_numeric(d["vwap_reclaim"], errors="coerce") > 0),
        ),
        (
            "6_pb_x_sector_x_board",
            lambda d, tr, hit: hit
            & mq(d, "sector_strength", "high", 0.7, tr)
            & (pd.to_numeric(d["board_improvement"], errors="coerce") > 0),
        ),
        # collapse counterparts (for reject quality)
        (
            "C1_pb_x_board_worsen",
            lambda d, tr, hit: hit & (pd.to_numeric(d["board_improvement"], errors="coerce") <= 0),
        ),
        (
            "C2_pb_x_no_reclaim",
            lambda d, tr, hit: hit & (pd.to_numeric(d["vwap_reclaim"], errors="coerce") <= 0),
        ),
        (
            "C3_pb_x_low_sector",
            lambda d, tr, hit: hit & mq(d, "sector_strength", "low", 0.3, tr),
        ),
        (
            "C4_pb_x_low_vol_accel",
            lambda d, tr, hit: hit & mq(d, "volume_acceleration", "low", 0.3, tr),
        ),
        (
            "C5_pb_x_stale_high_low_vol",
            lambda d, tr, hit: hit
            & mq(d, "seconds_since_last_high", "high", 0.7, tr)
            & mq(d, "volume_persistence", "low", 0.3, tr),
        ),
        (
            "C6_pb_x_high_stop_low_we",
            lambda d, tr, hit: hit
            & mq(d, "stop_risk", "high", 0.7, tr)
            & mq(d, "winner_enrichment", "low", 0.3, tr),
        ),
    ]

    disc_rows, conf_rows = [], []
    base_tr = train[hit_tr]
    base_te = test[hit_te]
    base_tr_h = float((base_tr["quality_label"] == "healthy").mean()) if len(base_tr) else 0.0
    base_te_h = float((base_te["quality_label"] == "healthy").mean()) if len(base_te) else 0.0
    base_tr_c = float((base_tr["quality_label"] == "collapse").mean()) if len(base_tr) else 0.0
    base_te_c = float((base_te["quality_label"] == "collapse").mean()) if len(base_te) else 0.0

    for name, fn in defs:
        m_tr = fn(train, train, hit_tr)
        m_te = fn(test, train, hit_te)  # freeze train quantiles
        ev_tr = eval_mask(train, m_tr, name, split="discovery")
        ev_te = eval_mask(test, m_te, name, split="confirmation")
        healthy_side = not name.startswith("C")
        if healthy_side:
            ev_tr["healthy_lift"] = (ev_tr.get("healthy_rate") or 0) - base_tr_h
            ev_te["healthy_lift"] = (ev_te.get("healthy_rate") or 0) - base_te_h
            ev_te["pass_split"] = bool(
                (ev_te.get("n") or 0) >= 15
                and (ev_te.get("days") or 0) >= 4
                and (ev_te.get("healthy_lift") or -1) >= 0.08
                and (ev_te.get("mean_pnl") or -1) > (float(base_te["runtime_exit_pnl"].mean()) if len(base_te) else 0)
                and (ev_te.get("collapse_rate") or 1) < base_te_c
            )
        else:
            ev_tr["collapse_lift"] = (ev_tr.get("collapse_rate") or 0) - base_tr_c
            ev_te["collapse_lift"] = (ev_te.get("collapse_rate") or 0) - base_te_c
            ev_te["pass_split"] = bool(
                (ev_te.get("n") or 0) >= 15
                and (ev_te.get("days") or 0) >= 4
                and (ev_te.get("collapse_lift") or -1) >= 0.08
                and (ev_te.get("mean_pnl") or 1) < (float(base_te["runtime_exit_pnl"].mean()) if len(base_te) else 0)
                and (ev_te.get("healthy_rate") or 1) < base_te_h
            )
        disc_rows.append(ev_tr)
        conf_rows.append(ev_te)
    return disc_rows, conf_rows


def feature_auc(train: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    """Univariate AUC: score high → collapse (sign flipped if needed)."""
    feats = [
        "fall_from_recent_high",
        "bounce_from_recent_low",
        "high_update_restart",
        "seconds_since_last_high",
        "vwap_reclaim",
        "board_improvement",
        "bid_pressure",
        "volume_acceleration",
        "volume_persistence",
        "volume_persistence_change",
        "sector_strength",
        "stop_risk",
        "winner_enrichment",
        # position-only baselines
        "entry_rise_5min_pct",
        "entry_vwap_dev_pct",
    ]
    rows = []
    for split_name, d in [("discovery", train), ("confirmation", test)]:
        hit = d[d["pullback_misread_shadow_hit"]].copy()
        hit = hit[hit["quality_label"].isin(["collapse", "healthy"])]
        if len(hit) < 20:
            continue
        y = (hit["quality_label"] == "collapse").astype(int).values
        for f in feats:
            if f not in hit.columns:
                continue
            x = pd.to_numeric(hit[f], errors="coerce").fillna(0).values
            a = auc_binary(x, y)
            a_flip = auc_binary(-x, y)
            best = a if (a or 0) >= (a_flip or 0) else a_flip
            direction = "high→collapse" if (a or 0) >= (a_flip or 0) else "low→collapse"
            rows.append(
                {
                    "split": split_name,
                    "feature": f,
                    "auc": best,
                    "direction": direction,
                    "n": len(hit),
                    "collapse_n": int(y.sum()),
                    "healthy_n": int((1 - y).sum()),
                }
            )
    return pd.DataFrame(rows)


def model_compare(train: pd.DataFrame, test: pd.DataFrame) -> dict:
    """Position-only vs quality-state for collapse classification among hits."""
    def prep(d):
        hit = d[d["pullback_misread_shadow_hit"]].copy()
        hit = hit[hit["quality_label"].isin(["collapse", "healthy"])]
        return hit

    tr, te = prep(train), prep(test)
    if len(tr) < 20 or len(te) < 15:
        return {"ok": False, "reason": "insufficient labeled hits"}

    y_tr = (tr["quality_label"] == "collapse").astype(int)
    y_te = (te["quality_label"] == "collapse").astype(int)

    # M_pos: depth of position (more negative rise/vwap → collapse?)
    pos_feats = ["entry_rise_5min_pct", "entry_vwap_dev_pct"]
    q_feats = [
        "fall_from_recent_high",
        "bounce_from_recent_low",
        "seconds_since_last_high",
        "vwap_reclaim",
        "board_improvement",
        "bid_pressure",
        "volume_acceleration",
        "volume_persistence",
        "sector_strength",
        "stop_risk",
        "winner_enrichment",
        "high_update_restart",
    ]

    def score(df, feats, coef_from):
        X = df[feats].apply(pd.to_numeric, errors="coerce").fillna(0)
        Xt = coef_from[feats].apply(pd.to_numeric, errors="coerce").fillna(0)
        y = (coef_from["quality_label"] == "collapse").astype(int)
        # correlate each feat with y on train; apply to df
        mu, sd = Xt.mean(), Xt.std().replace(0, 1)
        Xz = (X - mu) / sd
        Xtz = (Xt - mu) / sd
        coef = Xtz.corrwith(y).fillna(0)
        return (Xz * coef).sum(axis=1).values

    s_pos = score(te, pos_feats, tr)
    s_q = score(te, q_feats, tr)
    s_both = score(te, pos_feats + q_feats, tr)

    out = {
        "n_train": len(tr),
        "n_test": len(te),
        "auc_position_only": auc_binary(s_pos, y_te.values),
        "auc_quality_state": auc_binary(s_q, y_te.values),
        "auc_position_plus_quality": auc_binary(s_both, y_te.values),
    }
    a_pos = out["auc_position_only"] or 0.5
    a_q = out["auc_quality_state"] or 0.5
    a_both = out["auc_position_plus_quality"] or 0.5
    # Confirmed split if quality clearly beats position and >= 0.60, or both >> position
    lift = a_q - a_pos
    out["quality_lift_vs_position"] = lift
    out["both_lift_vs_position"] = a_both - a_pos
    return out


def main() -> int:
    print("=== Phase687W55-P12 Pullback Quality Audit ===", flush=True)
    df = build_dataset()
    if df.empty:
        print("NO DATA", flush=True)
        return 1

    hit = df[df["pullback_misread_shadow_hit"]]
    print(
        f"trades={len(df)} hits={len(hit)} "
        f"collapse={int((hit.quality_label=='collapse').sum())} "
        f"healthy={int((hit.quality_label=='healthy').sum())}",
        flush=True,
    )

    train = df[df["trading_date"].astype(str).isin(DISC)]
    test = df[df["trading_date"].astype(str).isin(CONF)]
    disc_c, conf_c = required_combos(train, test)
    feat_auc = feature_auc(train, test)
    models = model_compare(train, test)

    conf_pass = [c for c in conf_c if c.get("pass_split")]
    req_names = {
        "1_pb_x_board_impr",
        "2_pb_x_vwap_reclaim",
        "3_pb_x_sector_str",
        "4_pb_x_vol_accel",
        "5_pb_x_board_x_vwap",
        "6_pb_x_sector_x_board",
    }
    req_pass = [c for c in conf_pass if c["name"] in req_names]
    collapse_pass = [c for c in conf_pass if c["name"].startswith("C")]

    a_pos = models.get("auc_position_only") or 0.5
    a_q = models.get("auc_quality_state") or 0.5
    lift = models.get("quality_lift_vs_position") or 0.0
    conf_auc = feat_auc[feat_auc["split"] == "confirmation"] if len(feat_auc) else pd.DataFrame()
    q_feat_set = {
        "fall_from_recent_high",
        "bounce_from_recent_low",
        "high_update_restart",
        "seconds_since_last_high",
        "vwap_reclaim",
        "board_improvement",
        "bid_pressure",
        "volume_acceleration",
        "volume_persistence",
        "volume_persistence_change",
        "sector_strength",
        "stop_risk",
        "winner_enrichment",
    }
    if len(conf_auc):
        best_uni = conf_auc[conf_auc["feature"].isin(q_feat_set)]["auc"].max()
        best_uni_feat = (
            conf_auc[conf_auc["feature"].isin(q_feat_set)].sort_values("auc", ascending=False).iloc[0]["feature"]
            if (conf_auc["feature"].isin(q_feat_set)).any()
            else None
        )
    else:
        best_uni, best_uni_feat = None, None

    # Verdict: quality split if Confirmation combo gate passes AND
    # (composite quality AUC>=0.60 OR best univariate quality AUC>=0.60) AND lift vs position>=0.05
    # Position-only among already-hit rows is the baseline to beat (no AM/PM).
    quality_confirmed = bool(
        (len(req_pass) >= 1 or len(collapse_pass) >= 1)
        and lift >= 0.05
        and ((a_q >= 0.60) or ((best_uni or 0) >= 0.60))
    )

    verdict = "PULLBACK_QUALITY_SPLIT_CONFIRMED" if quality_confirmed else "PULLBACK_POSITION_ONLY"

    disk = round(100 * shutil.disk_usage("C:/").used / shutil.disk_usage("C:/").total, 1)

    # examples
    hit_te = test[test["pullback_misread_shadow_hit"]]
    ex_collapse = hit_te[hit_te["quality_label"] == "collapse"].head(25)
    ex_healthy = hit_te[hit_te["quality_label"] == "healthy"].head(25)

    report = {
        "metadata": {
            "phase": "Phase687W55-P12",
            "generated_at": datetime.now(JST).isoformat(),
            "n_trades": int(len(df)),
            "n_hits": int(len(hit)),
            "discovery_days": sorted(DISC),
            "confirmation_days": sorted(CONF),
            "runtime_unchanged": True,
            "shadow_added": False,
            "ampm_used_in_rules": False,
            "disk_used_pct": disk,
        },
        "verdict": verdict,
        "position_only_definition": "entry_rise_5min_pct < 0 AND entry_vwap_dev_pct < 0 (Dynamic40 shadow)",
        "quality_features": [
            "fall_from_recent_high",
            "bounce_from_recent_low",
            "high_update_restart",
            "seconds_since_last_high",
            "vwap_reclaim",
            "board_improvement",
            "bid_pressure",
            "volume_acceleration",
            "volume_persistence_change",
            "sector_strength",
            "stop_risk",
            "winner_enrichment",
        ],
        "model_comparison": {
            **models,
            "best_univariate_quality_auc": best_uni,
            "best_univariate_quality_feature": best_uni_feat,
        },
        "required_combo_confirmation": [c for c in conf_c if c["name"] in req_names],
        "required_pass": req_pass,
        "collapse_combo_pass": collapse_pass,
        "all_confirmation": conf_c,
        "feature_auc": feat_auc.to_dict(orient="records"),
        "answers": {
            "can_split_without_ampm": quality_confirmed,
            "position_only_sufficient": not quality_confirmed,
            "best_required_combos": [c["name"] for c in req_pass],
            "quality_auc_composite": a_q,
            "best_univariate_quality_auc": best_uni,
            "best_univariate_quality_feature": best_uni_feat,
            "position_auc": a_pos,
            "lift": lift,
        },
    }

    md = f"""# Phase687W55-P12 — Pullback Quality Audit

## Verdict
`{verdict}`

## Setup
- Population: Dynamic40 PullbackMisread shadow hits on 20 market days
- Labels: collapse vs healthy (reaccel); neutral excluded from AUC
- No AM/PM in rules; Discovery quantiles frozen for Confirmation
- Runtime / Shadow unchanged

## Position-only baseline
`entry_rise_5min_pct < 0 AND entry_vwap_dev_pct < 0`

## Model comparison (Confirmation, among labeled hits)
- AUC position-only: **{a_pos:.3f}**
- AUC quality-state (composite): **{a_q:.3f}**
- Best univariate quality AUC: **{(best_uni or 0):.3f}** (`{best_uni_feat}`)
- AUC position+quality: **{(models.get('auc_position_plus_quality') or 0):.3f}**
- Quality lift vs position: **{lift:.3f}**

## Required combinations (Confirmation)
| combo | n | healthy_rate | collapse_rate | mean_pnl | pass |
|---|---:|---:|---:|---:|:---:|
"""
    for c in conf_c:
        if c["name"] not in req_names:
            continue
        md += (
            f"| {c['name']} | {c.get('n',0)} | {c.get('healthy_rate')} | {c.get('collapse_rate')} | "
            f"{c.get('mean_pnl')} | {c.get('pass_split')} |\n"
        )
    md += f"""
## Collapse-side combos that passed
{[c['name'] for c in collapse_pass] or 'none'}

## Decision
- Quality split confirmed if: >=1 combo passes Confirmation gates AND (composite AUC>=0.60 OR best univariate quality AUC>=0.60) AND lift vs position >=0.05
- Result: **{verdict}**

## Runtime
- PBv2 / PullbackMisread Shadow / Cost-Aware: unchanged
- New Shadow: not added
- disk_used_pct={disk}
"""

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pullback_quality_audit_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (OUT / "pullback_quality_audit_report.md").write_text(md, encoding="utf-8")
    write_xlsx(
        {
            "00_summary": pd.DataFrame([{"verdict": verdict, **report["answers"]}]),
            "01_features": pd.DataFrame({"feature": report["quality_features"]}),
            "02_model_comparison": pd.DataFrame([models]),
            "03_required_combos_conf": pd.DataFrame([c for c in conf_c if c["name"] in req_names]),
            "04_all_combos_conf": pd.DataFrame(conf_c),
            "05_discovery_combos": pd.DataFrame(disc_c),
            "06_feature_auc": feat_auc,
            "07_examples_collapse": ex_collapse,
            "08_examples_healthy": ex_healthy,
            "09_final_verdict": pd.DataFrame([{"verdict": verdict, "quality_confirmed": quality_confirmed}]),
        },
        OUT / "pullback_quality_audit.xlsx",
    )
    print(json.dumps({"verdict": verdict, "models": models, "req_pass": [c["name"] for c in req_pass], "collapse_pass": [c["name"] for c in collapse_pass]}, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
